# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import string
import struct

from .overlay_edits import Edit, LOAD_ADDRESS
from .vp2_battle_label_font import STORED_GLYPHS


KEY_PREFIX = "battle_status_"
TABLE_OFFSET = 0x19EFC0
BLOCK_OFFSET = 0x1A3828
BLOCK_END = 0x1A3930
LENGTH_BASE = 0x4B
GLYPH_XOR = 0x6B
RECORD_ALIGN = 4

RECORDS = (
    ("Silence", 0x75, 0x2C),
    ("Stone", 0x69, 0x2C),
    ("Curse", 0x67, 0x2C),
    ("Faint", 0x65, 0x2C),
    ("Frozen", 0x71, 0x2C),
    ("Paralysis", 0x85, 0x2C),
    ("Poison", 0x75, 0x2C),
    ("Confusion", 0x8F, 0x2C),
    ("Frailty", 0x7B, 0x2C),
    ("Transfer", 0x85, 0x2C),
    ("WeaponBroken", 0xAD, 0x34),
    ("Survived", 0x85, 0x3A),
    ("Revive", 0x75, 0x34),
    ("StatusUp", 0x85, 0x34),
    ("StatusDown", 0x95, 0x34),
    ("ResistanceUp", 0x9F, 0x34),
    ("RecoverTransfer", 0xB3, 0x34),
)

ORIGINAL_OFFSETS = (
    0x1A3828, 0x1A3838, 0x1A3840, 0x1A3848, 0x1A3850, 0x1A3860, 0x1A3870,
    0x1A3880, 0x1A3890, 0x1A38A0, 0x1A38B0, 0x1A38C0, 0x1A38D0, 0x1A38E0,
    0x1A38F0, 0x1A3900, 0x1A3910,
)
ORIGINAL_POINTERS = tuple(LOAD_ADDRESS + offset for offset in ORIGINAL_OFFSETS)

OTHER_GLYPHS = {
    " ": 0xB4,
    **STORED_GLYPHS,
}

_KEYS = {("%02X" % index): index for index in range(len(RECORDS))}


def key(index):
    if not 0 <= index < len(RECORDS):
        raise ValueError("battle status index must be 0..%d"
                         % (len(RECORDS) - 1))
    return "%s%02X" % (KEY_PREFIX, index)


def _stored(character):
    if "A" <= character <= "Z" or "a" <= character <= "z":
        return ord(character) ^ GLYPH_XOR
    try:
        return OTHER_GLYPHS[character]
    except KeyError:
        raise ValueError("battle status label cannot encode %r"
                         % character) from None


def encode(text):
    if not isinstance(text, str) or not text:
        raise ValueError("battle status label must be a non-empty string")
    return bytes(_stored(character) for character in text)


def decode(data):
    glyphs = {ord(character) ^ GLYPH_XOR: character
              for character in string.ascii_letters}
    glyphs.update({value: character for character, value in OTHER_GLYPHS.items()})
    return "".join(glyphs[byte] for byte in data)


def translations(values):
    result = {}
    for misc_key, row in values.items():
        if not misc_key.startswith(KEY_PREFIX):
            continue
        suffix = misc_key[len(KEY_PREFIX):]
        if suffix not in _KEYS:
            raise ValueError("unknown battle status key %r" % misc_key)
        translated = (row.get("translated") if isinstance(row, dict)
                      else row) or ""
        offset = (row.get("offset_x") if isinstance(row, dict) else "") or ""
        if str(offset).strip():
            raise ValueError("battle status label %s: offset_x cannot be "
                             "placed yet" % misc_key)
        if translated:
            encode(translated)
            result[_KEYS[suffix]] = translated
    return result


def _retail_block():
    buffer = bytearray(BLOCK_END - BLOCK_OFFSET)
    for index, (original, field_a, field_b) in enumerate(RECORDS):
        encoded = encode(original)
        record = bytes((LENGTH_BASE + len(encoded), field_a, field_b)) + encoded
        start = ORIGINAL_OFFSETS[index] - BLOCK_OFFSET
        buffer[start:start + len(record)] = record
    return bytes(buffer)


def edits(values):
    requested = translations(values)
    if not requested:
        return []
    block = bytearray()
    pointers = []
    for index, (original, field_a, field_b) in enumerate(RECORDS):
        encoded = encode(requested.get(index, original))
        pointers.append(BLOCK_OFFSET + len(block))
        record = bytes((LENGTH_BASE + len(encoded), field_a, field_b)) + encoded
        block += record.ljust(
            (len(record) + RECORD_ALIGN - 1) // RECORD_ALIGN * RECORD_ALIGN,
            b"\0")
    capacity = BLOCK_END - BLOCK_OFFSET
    if len(block) > capacity:
        raise ValueError("battle status labels need %d bytes but the block "
                         "holds %d; the records would run into the colour table"
                         % (len(block), capacity))
    laid_out = bytes(block).ljust(capacity, b"\0")
    changes = []
    for index, pointer in enumerate(pointers):
        changes.append(Edit(
            TABLE_OFFSET + 4 * index,
            struct.pack("<I", ORIGINAL_POINTERS[index]),
            struct.pack("<I", LOAD_ADDRESS + pointer),
            "battle status pointer %s" % key(index),
        ))
    changes.append(Edit(BLOCK_OFFSET, _retail_block(), laid_out,
                        "battle status labels"))
    return changes
