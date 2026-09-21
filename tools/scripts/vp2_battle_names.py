# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Translate the compact character names drawn by the battle HUD."""

import struct

from .overlay_edits import Edit


KEY_PREFIX = "battle_name_"
TABLE_OFFSET = 0x1A1974
RECORD_SIZE = 24
NAME_CAPACITY = 20
LENGTH_PREFIX = 4
TERMINATOR = 0xFF
HYPHEN = 0xFE

ORIGINAL_NAMES = (
    "EMPTY",
    "SILMERIA", "BRAHMS", "RUFUS", "LEZARD", "ARNGRIM", "DYLAN",
    "LEONE", "HRIST", "LENNETH", "VALKYRIE", "ALICIA", "ALICIA",
    "FREYA", "ULL", "HEIMDALL", "LWYN", "JESSICA", "RICHELLE",
    "FRAUDIR", "CELES", "CRESCENT", "SYLPHIDE", "TYRITH", "CIRCE",
    "RASHEEKA", "FALX", "GERALD", "GUILM", "ROLAND", "EHLEN",
    "ADONIS", "AARON", "DYN", "KRAAD", "ZUNDE", "ATRASIA",
    "PHYRESS", "CHRYSTIE", "SHA-KON", "LYLIA", "LYDIA", "SOPHALLA",
    "MILLIDIA", "ARCANA", "EHRDE", "MITHRA", "AEGIS", "PSORON",
    "KHANON", "FARANT", "XEHNON", "ALM", "SELUVIA", "MASATO",
    "WOLTAR",
)


def key(index):
    if not 0 <= index < len(ORIGINAL_NAMES):
        raise ValueError("battle name index must be 0..%d" %
                         (len(ORIGINAL_NAMES) - 1))
    return "%s%02X" % (KEY_PREFIX, index)


def encode_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValueError("battle character name must not be blank")
    name = name.strip().upper()
    encoded = bytearray()
    for character in name:
        if "A" <= character <= "Z":
            encoded.append(ord(character) - ord("A"))
        elif character == "-":
            encoded.append(HYPHEN)
        else:
            raise ValueError(
                "battle character name cannot encode %r; use A-Z or hyphen"
                % character)
    if len(encoded) >= NAME_CAPACITY:
        raise ValueError("battle character name holds at most %d characters"
                         % (NAME_CAPACITY - 1))
    encoded.append(TERMINATOR)
    return bytes(encoded).ljust(NAME_CAPACITY, b"\0")


def translations(values):
    known = {key(index): index for index in range(len(ORIGINAL_NAMES))}
    result = {}
    for misc_key, value in values.items():
        if not misc_key.startswith(KEY_PREFIX):
            continue
        if misc_key not in known:
            raise ValueError("unknown battle character name key %r" % misc_key)
        if isinstance(value, dict):
            value = value.get("translated") or ""
        if value:
            index = known[misc_key]
            encode_name(value)
            result[index] = value.strip().upper()
    return result


def edits(values):
    changes = []
    for index, translated in translations(values).items():
        changes.append(Edit(
            TABLE_OFFSET + index * RECORD_SIZE - LENGTH_PREFIX,
            struct.pack("<I", len(ORIGINAL_NAMES[index])),
            struct.pack("<I", len(translated)),
            "battle character name length %s" % key(index),
        ))
        changes.append(Edit(
            TABLE_OFFSET + index * RECORD_SIZE,
            encode_name(ORIGINAL_NAMES[index]),
            encode_name(translated),
            "battle character name %s (%s)" %
            (key(index), ORIGINAL_NAMES[index]),
        ))
    return changes
