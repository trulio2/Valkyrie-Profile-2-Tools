"""Decode and restore tri-Ace's protected ``p@Ck`` entry wrapper."""

from __future__ import annotations

import collections
import dataclasses
import struct


MASK32 = 0xFFFFFFFF
OFFSET_MASK = 0x7FFFFFFF
PROTECTED_OFFSET = 0x80000000

HEADER_XOR = bytes.fromhex("EB A8 52 A6 FD FD 6F 6F 3D 3D F3 F3 F3 F3")


class ProtectedPackageError(ValueError):
    """The entry is not an unambiguous protected package."""


@dataclasses.dataclass(frozen=True)
class ProtectedLayout:
    seed: int
    payload_start: int
    payload_end: int
    offsets: tuple[int, ...]
    flags: tuple[int, ...]
    previous_clear: int
    counter: int
    lcg: int


def _next_table_key(value):
    return (value * 2053 + 0x3619) & MASK32


def _table_key(seed):
    base = (seed ^ 0x5E) * 29
    return (base * 2053 + 0x3619) & MASK32


def _state(seed):
    counter = ((seed ^ 0xF3) * 0x2AB7D + 0x33E) & MASK32
    previous = (counter * 129 + 0x1CBC) & MASK32
    lcg = (seed ^ 0xC7) * 3
    return previous, counter, lcg


def _header_candidates(raw):
    if len(raw) < 0x18:
        return []
    prepared = bytearray(raw)
    for index, value in enumerate(HEADER_XOR):
        prepared[index] ^= value

    candidates = []
    for seed in range(256):
        key = _table_key(seed)
        first = struct.unpack_from("<I", prepared, 8)[0] ^ key
        first_offset = first & OFFSET_MASK
        if not first & PROTECTED_OFFSET or first_offset < 0x18 \
                or first_offset % 8:
            continue
        count = first_offset // 8 - 2
        if not 1 <= count < 0x8000 or first_offset > len(raw):
            continue

        rows = []
        for row in range(count + 1):
            at = 8 + row * 8
            offset = struct.unpack_from("<I", prepared, at)[0] ^ key
            key = _next_table_key(key)
            flags = struct.unpack_from("<I", prepared, at + 4)[0] ^ key
            key = _next_table_key(key)
            rows.append((offset, flags))

        offsets = tuple(offset & OFFSET_MASK for offset, _flags in rows)
        flags = tuple(item_flags for _offset, item_flags in rows)
        if offsets[0] != first_offset or offsets[-1] > len(raw):
            continue
        if any(left > right for left, right in zip(offsets, offsets[1:])):
            continue
        if flags[-1] or any(value & 0xFFFF0000 for value in flags[:-1]):
            continue
        if not any(offset & PROTECTED_OFFSET for offset, _flags in rows[:-1]):
            continue

        previous, counter, lcg = _state(seed)
        candidates.append(ProtectedLayout(
            seed=seed,
            payload_start=offsets[0],
            payload_end=offsets[-1],
            offsets=offsets,
            flags=flags,
            previous_clear=previous,
            counter=counter,
            lcg=lcg,
        ))
    return candidates


def layout(raw):
    """Return the sole structurally valid protected-wrapper layout."""
    candidates = _header_candidates(bytes(raw))
    if len(candidates) != 1:
        raise ProtectedPackageError(
            "entry has %d protected-package header candidates" %
            len(candidates))
    return candidates[0]


def extend_payload_end(clear, new_end):
    clear = bytes(clear)
    parsed = layout(clear)
    if new_end <= parsed.payload_end:
        return clear, parsed
    if new_end > len(clear) or new_end % 4:
        raise ProtectedPackageError(
            "protected payload end %d is outside a %d-byte entry or unaligned"
            % (new_end, len(clear)))

    prepared = bytearray(clear)
    for index, value in enumerate(HEADER_XOR):
        prepared[index] ^= value
    key = _table_key(parsed.seed)
    last = len(parsed.offsets) - 1
    for row in range(last + 1):
        offset_key = key
        key = _next_table_key(key)
        key = _next_table_key(key)  # flags use this key; leave them unchanged
        if row == last:
            at = 8 + row * 8
            stored = struct.unpack_from("<I", prepared, at)[0]
            old = stored ^ offset_key
            value = (old & PROTECTED_OFFSET) | new_end
            struct.pack_into("<I", prepared, at, value ^ offset_key)
    for index, value in enumerate(HEADER_XOR):
        prepared[index] ^= value
    prepared[parsed.payload_end:new_end] = bytes(new_end - parsed.payload_end)

    expanded = bytes(prepared)
    current = layout(expanded)
    if (current.offsets[:-1] != parsed.offsets[:-1] or
            current.offsets[-1] != new_end or
            current.flags != parsed.flags or current.seed != parsed.seed):
        raise ProtectedPackageError(
            "protected payload expansion changed its layout")
    return expanded, current


def _arithmetic_half(value):
    signed = value if value < 0x80000000 else value - 0x100000000
    return (signed >> 1) & MASK32


def _transform(data, current, *, encode):
    if len(data) % 4:
        raise ProtectedPackageError(
            "protected payload length is not a multiple of four")
    previous = current.previous_clear
    counter = current.counter
    lcg = current.lcg
    output = bytearray(len(data))
    for offset in range(0, len(data), 4):
        word = struct.unpack_from("<I", data, offset)[0]
        shifted = (counter << ((lcg >> 16) & 31)) & MASK32
        half = _arithmetic_half(lcg)
        if encode:
            clear = word
            transformed = ((clear + half) & MASK32) ^ previous ^ shifted
        else:
            transformed = ((word ^ previous ^ shifted) - half) & MASK32
            clear = transformed
        struct.pack_into("<I", output, offset, transformed)
        previous = clear
        counter = (counter + 0x00718331) & MASK32
        lcg = (lcg * 0x00102009 + 0x00040001) & MASK32
    return bytes(output)


_DECODED = collections.OrderedDict()
_DECODED_ENTRIES = 4


def decode_entry(raw):
    """Return ``(clear_entry, layout)`` for one protected entry."""
    raw = bytes(raw)
    found = _DECODED.get(raw)
    if found is not None:
        _DECODED.move_to_end(raw)
        return found
    parsed = layout(raw)
    clear = bytearray(raw)
    start, end = parsed.payload_start, parsed.payload_end
    clear[start:end] = _transform(raw[start:end], parsed, encode=False)
    _DECODED[raw] = found = (bytes(clear), parsed)
    while len(_DECODED) > _DECODED_ENTRIES:
        _DECODED.popitem(last=False)
    return found


def encode_entry(original, clear, parsed):
    """Restore a decoded entry while preserving its header and allocation."""
    original = bytes(original)
    clear = bytes(clear)
    if len(clear) != len(original):
        raise ProtectedPackageError(
            "protected entry geometry changed from %d to %d bytes" %
            (len(original), len(clear)))
    current = layout(clear)
    expanded = current != parsed
    if expanded and not (
            current.seed == parsed.seed and
            current.payload_start == parsed.payload_start and
            current.payload_end > parsed.payload_end and
            current.offsets[:-1] == parsed.offsets[:-1] and
            current.flags == parsed.flags):
        raise ProtectedPackageError(
            "protected entry changed more than its final item allocation")
    if expanded:
        expected, _layout = extend_payload_end(original, current.payload_end)
        if clear[:current.payload_start] != expected[:current.payload_start]:
            raise ProtectedPackageError(
                "protected entry header changed unexpectedly")
        parsed = current
    start, end = parsed.payload_start, parsed.payload_end
    rebuilt = bytearray(original)
    if expanded:
        rebuilt[:start] = clear[:start]
    rebuilt[start:end] = _transform(clear[start:end], parsed, encode=True)
    checked, checked_layout = decode_entry(bytes(rebuilt))
    if checked != clear or checked_layout != parsed:
        raise ProtectedPackageError(
            "protected entry did not decode back to the requested bytes")
    return bytes(rebuilt)
