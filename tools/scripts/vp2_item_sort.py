# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Keep item/equipment alphabetical order aligned with translated names.

Sealstones use a separate table handled by ``vp2_sealstone_sort``.
"""

from dataclasses import dataclass
import struct
import unicodedata

from . import sle
from ..cheat_patcher import slz3


RESOURCE = 3
LOAD_ADDRESS = 0x001E7E00
TABLE_OFFSET = 0x12CE00
ITEM_COUNT = 750
RECORD_SIZE = 56
SORT_KEY_OFFSET = 6
NAME_RESOURCE = 644
NAME_MESSAGE_BASE = 48
TYPE_MASK = 0xF000
RANK_MASK = 0x0FFF
RANK_SCALE = 4


@dataclass(frozen=True)
class Result:
    data: bytes
    changed: int
    stored_size: int
    room: int


def names_from_rows(rows):
    """Return every item id's translated name, with English as fallback."""
    names = {}
    for row in rows:
        raw_id = (row.get("message_id") or "").strip()
        try:
            message_id = int(raw_id, 0)
        except ValueError:
            continue
        item_id = message_id - NAME_MESSAGE_BASE
        if not 0 <= item_id < ITEM_COUNT:
            continue
        if item_id in names:
            raise ValueError("item-name sheet repeats message %d" % message_id)
        translated = (row.get("translated") or "").strip()
        original = (row.get("original_en") or "").strip()
        name = translated or original
        if not name:
            raise ValueError("item %d has no translated or English name"
                             % item_id)
        names[item_id] = name
    missing = sorted(set(range(ITEM_COUNT)) - set(names))
    if missing:
        raise ValueError("item-name sheet is missing %d item(s): %s"
                         % (len(missing), ", ".join(map(str, missing[:8]))))
    return names


def has_translated_names(rows):
    """Whether the item-name range contains at least one real translation."""
    for row in rows:
        try:
            message_id = int((row.get("message_id") or "").strip(), 0)
        except ValueError:
            continue
        translated = (row.get("translated") or "").strip()
        original = (row.get("original_en") or "").strip()
        if (NAME_MESSAGE_BASE <= message_id < NAME_MESSAGE_BASE + ITEM_COUNT
                and translated and translated != original):
            return True
    return False


def collation_key(text):
    """A deterministic, case/accent/punctuation-insensitive name key."""
    folded = "".join(
        character for character in unicodedata.normalize("NFKD", text).casefold()
        if not unicodedata.combining(character)
    )
    return "".join(character for character in folded
                   if character.isalnum()), folded


def _table_keys(output):
    end = TABLE_OFFSET + ITEM_COUNT * RECORD_SIZE
    if end > len(output):
        raise ValueError("resident overlay is too small for the item table")
    keys = [
        struct.unpack_from(
            "<H", output, TABLE_OFFSET + item_id * RECORD_SIZE + SORT_KEY_OFFSET
        )[0]
        for item_id in range(ITEM_COUNT)
    ]
    if any(key & (RANK_SCALE - 1) for key in keys):
        raise ValueError("item table contains an unaligned alphabetical rank")
    ranks = [(key & RANK_MASK) // RANK_SCALE for key in keys]
    if sorted(ranks) != list(range(ITEM_COUNT)):
        raise ValueError("item table ranks are not a permutation of 0..%d"
                         % (ITEM_COUNT - 1))
    return keys, ranks


def rewrite_output(output, names):
    """Return ``(expanded overlay, changed records)`` for localized names."""
    if set(names) != set(range(ITEM_COUNT)):
        raise ValueError("localized item names must cover ids 0..%d"
                         % (ITEM_COUNT - 1))
    keys, old_ranks = _table_keys(output)
    order = sorted(
        range(ITEM_COUNT),
        key=lambda item_id: (
            collation_key(names[item_id]), old_ranks[item_id], item_id
        ),
    )
    new_ranks = {item_id: rank for rank, item_id in enumerate(order)}
    rebuilt = bytearray(output)
    changed = 0
    for item_id, old_key in enumerate(keys):
        new_key = (old_key & TYPE_MASK) | new_ranks[item_id] * RANK_SCALE
        if new_key != old_key:
            changed += 1
            struct.pack_into(
                "<H", rebuilt,
                TABLE_OFFSET + item_id * RECORD_SIZE + SORT_KEY_OFFSET,
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
        raise ValueError("resource 3 has %d resident item overlays" % len(matches))
    stream = matches[0]
    if stream.mode != 3 or stream.next_offset:
        raise ValueError("resident item overlay is not the final mode-3 stream")
    end = stream.offset + len(stream.encoded)
    if any(resource[end:]):
        raise ValueError("resource 3 has unknown data after its resident overlay")
    return stream


def patch_resource(resource, names):
    """Rewrite resource 3's ranks and preserve its fixed outer allocation."""
    resource = bytes(resource)
    stream = _resident_stream(resource)
    output, changed = rewrite_output(stream.output, names)
    if not changed:
        return Result(resource, 0, stream.stored_size,
                      len(resource) - stream.offset - len(stream.encoded))
    encoded = sle.conceal(slz3.compress(output))
    capacity = len(resource) - stream.offset
    if len(encoded) > capacity:
        raise ValueError(
            "localized item ranks exceed resource 3 by %d byte(s)"
            % (len(encoded) - capacity)
        )
    rebuilt = bytearray(resource)
    rebuilt[stream.offset:] = bytes(capacity)
    rebuilt[stream.offset:stream.offset + len(encoded)] = encoded
    rebuilt = bytes(rebuilt)
    readback = _resident_stream(rebuilt)
    if readback.output != output:
        raise ValueError("localized item ranks did not read back")
    if rebuilt[:stream.offset] != resource[:stream.offset]:
        raise ValueError("localized item ranks changed an earlier overlay")
    return Result(rebuilt, changed, readback.stored_size,
                  capacity - len(readback.encoded))


def apply_to_iso(iso, names):
    """Patch item ranks in an open image."""
    original = iso.read_entry(RESOURCE)
    result = patch_resource(original, names)
    if result.changed:
        iso.write_entry(RESOURCE, result.data)
    return result
