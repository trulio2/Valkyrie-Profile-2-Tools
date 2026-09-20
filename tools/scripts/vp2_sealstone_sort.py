# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

from dataclasses import dataclass
import struct

from . import sle
from ..cheat_patcher import slz3
from .vp2_item_sort import collation_key


RESOURCE = 3
LOAD_ADDRESS = 0x001E7E00
TABLE_OFFSET = 0x137532
SEALSTONE_COUNT = 68
EMPTY_IDS = frozenset((24, 64))
NAMED_IDS = frozenset(set(range(SEALSTONE_COUNT)) - EMPTY_IDS)
RECORD_SIZE = 12
SORT_KEY_OFFSET = 6
NAME_RESOURCE = 648
NAME_MESSAGE_BASE = 714
RANK_BASE = 0x22
RANK_MASK = 0x00FF


@dataclass(frozen=True)
class Result:
    data: bytes
    changed: int
    stored_size: int
    room: int


def names_from_rows(rows):
    """Return every named Sealstone id's translation, with English fallback."""
    names = {}
    for row in rows:
        raw_id = (row.get("message_id") or "").strip()
        try:
            message_id = int(raw_id, 0)
        except ValueError:
            continue
        sealstone_id = message_id - NAME_MESSAGE_BASE
        if sealstone_id not in NAMED_IDS:
            continue
        if sealstone_id in names:
            raise ValueError("Sealstone-name sheet repeats message %d"
                             % message_id)
        translated = (row.get("translated") or "").strip()
        original = (row.get("original_en") or "").strip()
        name = translated or original
        if not name:
            raise ValueError("Sealstone %d has no translated or English name"
                             % sealstone_id)
        names[sealstone_id] = name
    missing = sorted(NAMED_IDS - set(names))
    if missing:
        raise ValueError("Sealstone-name sheet is missing %d name(s): %s"
                         % (len(missing), ", ".join(map(str, missing[:8]))))
    return names


def has_translated_names(rows):
    """Whether the Sealstone-name range contains a real translation."""
    for row in rows:
        try:
            message_id = int((row.get("message_id") or "").strip(), 0)
        except ValueError:
            continue
        translated = (row.get("translated") or "").strip()
        original = (row.get("original_en") or "").strip()
        sealstone_id = message_id - NAME_MESSAGE_BASE
        if (sealstone_id in NAMED_IDS and translated
                and translated != original):
            return True
    return False


def _table_keys(output):
    end = TABLE_OFFSET + SEALSTONE_COUNT * RECORD_SIZE
    if end > len(output):
        raise ValueError("resident overlay is too small for the Sealstone table")
    keys = [
        struct.unpack_from(
            "<H", output,
            TABLE_OFFSET + sealstone_id * RECORD_SIZE + SORT_KEY_OFFSET,
        )[0]
        for sealstone_id in range(SEALSTONE_COUNT)
    ]
    if keys[24] != 0 or keys[64] != 1:
        raise ValueError("Sealstone table does not contain its two empty records")
    named = [keys[sealstone_id] for sealstone_id in sorted(NAMED_IDS)]
    expected = list(range(RANK_BASE, RANK_BASE + len(NAMED_IDS)))
    if sorted(key & RANK_MASK for key in named) != expected:
        raise ValueError("Sealstone alphabetical ranks have an unknown layout")
    return keys


def rewrite_output(output, names):
    """Return ``(expanded overlay, changed records)`` for localized names."""
    if set(names) != NAMED_IDS:
        raise ValueError("localized Sealstone names must cover the 66 named ids")
    keys = _table_keys(output)
    order = sorted(NAMED_IDS,
                   key=lambda sealstone_id:
                   (collation_key(names[sealstone_id]), sealstone_id))
    new_ranks = {sealstone_id: rank
                 for rank, sealstone_id in enumerate(order)}
    rebuilt = bytearray(output)
    changed = 0
    for sealstone_id in sorted(NAMED_IDS):
        old_key = keys[sealstone_id]
        new_key = ((old_key & ~RANK_MASK)
                   | (RANK_BASE + new_ranks[sealstone_id]))
        if new_key != old_key:
            changed += 1
            struct.pack_into(
                "<H", rebuilt,
                TABLE_OFFSET + sealstone_id * RECORD_SIZE + SORT_KEY_OFFSET,
                new_key,
            )
    return bytes(rebuilt), changed


def _resident_stream(resource):
    matches = [
        stream for stream in sle.iter_streams(resource)
        if (len(stream.output) >= 12
            and struct.unpack_from("<I", stream.output, 8)[0] == LOAD_ADDRESS)
    ]
    if len(matches) != 1:
        raise ValueError("resource 3 has %d resident Sealstone overlays"
                         % len(matches))
    stream = matches[0]
    if stream.mode != 3 or stream.next_offset:
        raise ValueError("resident Sealstone overlay is not the final mode-3 stream")
    end = stream.offset + len(stream.encoded)
    if any(resource[end:]):
        raise ValueError("resource 3 has unknown data after its resident overlay")
    return stream


def patch_resource(resource, names):
    """Rewrite resource 3's Sealstone ranks in its fixed allocation."""
    resource = bytes(resource)
    stream = _resident_stream(resource)
    output, changed = rewrite_output(stream.output, names)
    if not changed:
        return Result(resource, 0, stream.stored_size,
                      len(resource) - stream.offset - len(stream.encoded))
    encoded = sle.conceal(slz3.compress(output))
    capacity = len(resource) - stream.offset
    if len(encoded) > capacity:
        raise ValueError("localized Sealstone ranks exceed resource 3 by %d byte(s)"
                         % (len(encoded) - capacity))
    rebuilt = bytearray(resource)
    rebuilt[stream.offset:] = bytes(capacity)
    rebuilt[stream.offset:stream.offset + len(encoded)] = encoded
    rebuilt = bytes(rebuilt)
    readback = _resident_stream(rebuilt)
    if readback.output != output:
        raise ValueError("localized Sealstone ranks did not read back")
    if rebuilt[:stream.offset] != resource[:stream.offset]:
        raise ValueError("localized Sealstone ranks changed an earlier overlay")
    return Result(rebuilt, changed, readback.stored_size,
                  capacity - len(readback.encoded))


def apply_to_iso(iso, names):
    """Patch Sealstone ranks in an open image."""
    original = iso.read_entry(RESOURCE)
    result = patch_resource(original, names)
    if result.changed:
        iso.write_entry(RESOURCE, result.data)
    return result
