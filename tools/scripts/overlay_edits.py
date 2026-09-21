# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Guarded byte edits to the battle overlay, applied in one recompression."""

from dataclasses import dataclass
import struct

from ..cheat_patcher import battle_overlay


RESOURCE = 1781
LOAD_ADDRESS = 0x0036D900


@dataclass(frozen=True)
class Edit:
    """Bytes at ``offset`` in the expanded overlay: what they are, what they become."""
    offset: int
    original: bytes
    replacement: bytes
    what: str


@dataclass(frozen=True)
class Result:
    data: bytes
    changed: int
    stored_size: int
    room: int


def word(value):
    return struct.pack("<I", value)


def offset_of(address):
    """The overlay offset loaded at ``address``."""
    return address - LOAD_ADDRESS


def edit_output(output, edits):
    """``(patched, changed bytes)``; every edit must find its original or its replacement."""
    output = bytes(output)
    if len(output) < 12:
        raise ValueError("battle overlay is too small to carry its load address")
    base = struct.unpack_from("<I", output, 8)[0]
    if base != LOAD_ADDRESS:
        raise ValueError("battle overlay loads at 0x%08X, expected 0x%08X"
                         % (base, LOAD_ADDRESS))
    patched = bytearray(output)
    claimed = {}
    for edit in edits:
        size = len(edit.original)
        if size == 0:
            if not edit.replacement:
                raise ValueError("%s: an edit must change something"
                                 % edit.what)
            if edit.offset == len(patched):
                patched[edit.offset:edit.offset] = edit.replacement
                continue
            end = edit.offset + len(edit.replacement)
            if (edit.offset <= len(patched) and end <= len(patched)
                    and bytes(patched[edit.offset:end]) == edit.replacement):
                continue
            raise ValueError("%s: an append edit must start at the overlay "
                             "end" % edit.what)
        if len(edit.replacement) != size:
            raise ValueError("%s: original and replacement must be the same "
                             "non-zero length" % edit.what)
        if edit.offset < 0 or edit.offset + size > len(output):
            raise ValueError("%s: offset 0x%X is outside the overlay"
                             % (edit.what, edit.offset))
        for index in range(size):
            position = edit.offset + index
            byte = edit.replacement[index]
            earlier = claimed.setdefault(position, (edit.what, byte))
            if earlier[1] != byte:
                raise ValueError("%s and %s both change overlay byte 0x%X"
                                 % (earlier[0], edit.what, position))
        found = output[edit.offset:edit.offset + size]
        if found not in (edit.original, edit.replacement):
            raise ValueError("%s: expected %s at overlay 0x%X, found %s"
                             % (edit.what, edit.original.hex(" "), edit.offset,
                                found.hex(" ")))
        patched[edit.offset:edit.offset + size] = edit.replacement
    changed = sum(before != after for before, after in zip(output, patched))
    changed += abs(len(patched) - len(output))
    return bytes(patched), changed


def _room(overlay, stored_size):
    return overlay.item_span - battle_overlay.SLZ_HEADER_SIZE - stored_size


def apply(resource, edits, more=None):
    """Apply ``edits`` to a battle resource, recompressing its overlay once.

    ``more``, if given, returns further edits for the expanded overlay.
    """
    resource = bytes(resource)
    overlay = battle_overlay.read(resource)
    if more is not None:
        edits = list(edits) + list(more(overlay.output))
    patched, changed = edit_output(overlay.output, edits)
    if not changed:
        return Result(resource, 0, overlay.stored_size,
                      _room(overlay, overlay.stored_size))
    rebuilt, stored_size = battle_overlay.replace(resource, patched)
    if len(rebuilt) != len(resource):
        raise ValueError("battle overlay edits changed resource %d's size"
                         % RESOURCE)
    if battle_overlay.read(rebuilt).output != patched:
        raise ValueError("battle overlay edits did not read back")
    return Result(rebuilt, changed, stored_size, _room(overlay, stored_size))


def apply_to_iso(iso, edits, more=None):
    """Apply ``edits`` to the battle resource in an open image."""
    result = apply(iso.read_entry(RESOURCE), edits, more)
    if result.changed:
        iso.write_entry(RESOURCE, result.data)
    return result
