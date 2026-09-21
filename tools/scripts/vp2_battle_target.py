# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import string
import struct

from .overlay_edits import Edit, word


KEY = "battle_target"
LABEL_OFFSET = 0x1A2490
LABEL_CAPACITY = 8
ORIGINAL_LABEL = bytes.fromhex("3f 0a 19 0c 0e 1f 00 00")
COPY_LENGTH_OFFSET = 0x19FC0
ORIGINAL_COPY_INSTRUCTION = 0x24060006  # addiu a2, zero, 6
COPY_INSTRUCTION = 0x24060000           # addiu a2, zero, <length>
TARGET_CALL_OFFSET = 0x1A0AC
ORIGINAL_TARGET_CALL = 0x0C132954       # jal 0x004CA550
LABEL_X_OFFSET = 0x19134
ORIGINAL_LABEL_X = 0x3C084210           # lui t0, 0x4210 (36.0)
LABEL_X_MARGIN = 36.0
LETTER_WIDTHS = dict(zip(
    string.ascii_uppercase + string.ascii_lowercase,
    bytes.fromhex("0d0e0c100f0c0c0f09080f0c120f0d0d0d0d0a0d100e140c0e0d"
                  "090908090807090a060508050e0a0809090707070a0a0f090909")))
GLYPH_SCALE = 1.1
GLYPH_GAP = 0.1


def encode_label(label):
    if not isinstance(label, str) or not label:
        raise ValueError("battle target label must be a non-empty string")
    if len(label) > LABEL_CAPACITY:
        raise ValueError(
            "battle target label holds at most %d characters" % LABEL_CAPACITY
        )
    encoded = bytearray()
    for character in label:
        if not character.isascii() or not character.isalpha():
            raise ValueError(
                "battle target label cannot encode character %r" % character
            )
        encoded.append(ord(character) ^ 0x6B)
    return bytes(encoded)


def validate_label(label):
    encode_label(label)
    return label


def label_advance(label):
    encode_label(label)
    return sum(GLYPH_SCALE * LETTER_WIDTHS[character] + GLYPH_GAP
               for character in label)


def centred_x(label):
    offset = (label_advance("Target") - label_advance(label)) / 2
    return round(offset * 4) / 4


def parse_x(value):
    if value is None or not str(value).strip():
        return None
    try:
        offset = float(str(value).strip())
    except ValueError:
        raise ValueError("battle target x must be a number, got %r"
                         % value) from None
    bits = struct.unpack("<I", struct.pack("<f", LABEL_X_MARGIN - offset))[0]
    if bits & 0xFFFF or struct.unpack(
            "<f", struct.pack("<I", bits))[0] != LABEL_X_MARGIN - offset:
        raise ValueError("battle target x %r cannot be placed exactly; use a "
                         "whole number or a quarter" % value)
    return offset


def label_x(label=None, x=None):
    offset = parse_x(x)
    if offset is None:
        offset = centred_x(label) if label else 0.0
    return offset


def edits(label=None, x=None):
    changes = []
    if label:
        encoded = encode_label(label)
        changes += [
            Edit(LABEL_OFFSET, ORIGINAL_LABEL,
                 encoded.ljust(LABEL_CAPACITY, b"\0"), "battle Target label"),
            Edit(COPY_LENGTH_OFFSET, word(ORIGINAL_COPY_INSTRUCTION),
                 word(COPY_INSTRUCTION | len(encoded)),
                 "battle Target label copy length"),
            Edit(TARGET_CALL_OFFSET, word(ORIGINAL_TARGET_CALL),
                 word(ORIGINAL_TARGET_CALL), "battle Target renderer call"),
        ]
    offset = label_x(label, x)
    if offset:
        bits = struct.unpack(
            "<I", struct.pack("<f", LABEL_X_MARGIN - offset))[0]
        changes.append(Edit(LABEL_X_OFFSET, word(ORIGINAL_LABEL_X),
                            word(0x3C080000 | (bits >> 16)),
                            "battle Target label x"))
    return changes
