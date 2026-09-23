# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Entry 8's second code range, and the glyph routine change it needs."""
import struct

from . import sle, slz
from ..cheat_patcher import slz3


RESOURCE = 3
LOAD_ADDRESS = 0x001E7E00

#: The first code of the range, and the entry-8 glyph it draws.
BASE = 0x400
FIRST_GLYPH = 100
#: Entry 8's metric table holds 128 entries before its glyph data.
LAST_GLYPH = 127

START = 0x002E90B0
ORIGINAL = (
    0x8C83006C, 0x9066003C, 0x10C00009, 0x24A7FFFF, 0x28A10065, 0x10200004,
    0x3C010036, 0x8C23E170, 0x10000003, 0x24630014, 0x24E7FF9C, 0x00000000,
    0x8C8600C0, 0xACC30018, 0x8C8600C0, 0xA4C7002E, 0x8C8600C0, 0xA4C5002C,
    0x8C860094, 0x8C8500C0, 0xACA60020, 0x8C8600A0, 0x8C8500C0, 0xACA60024,
    0x8C8600A4, 0x8C8500C0, 0xACA60028, 0x8C8700C0,
)


def token_for_glyph(glyph):
    """The token word a record spends to draw entry-8 ``glyph``."""
    if glyph < FIRST_GLYPH:
        token = glyph + 1
        if token > 0x64:
            raise ValueError("glyph %d is past the one-byte range" % glyph)
        return token
    if glyph > LAST_GLYPH:
        raise ValueError("entry 8 cannot hold glyph %d" % glyph)
    code = BASE + glyph - FIRST_GLYPH
    return (0x80 | (code & 0x7F)) | ((code >> 7) << 8)


def is_range_token(token):
    return (token >= 0x100 and bool(token & 0x80) and not token & 0x8000
            and code_of(token) >= BASE)


def code_of(token):
    """The text code a one- or two-byte token word carries."""
    if token < 0x80:
        return token
    return (token & 0x7F) | ((token >> 8) << 7)


def glyph_for_token(token):
    """The entry-8 glyph a slot-table token draws."""
    if 1 <= token <= 0x64:
        return token - 1
    if token >= 0x100 and token & 0x80 and not token & 0x8000:
        glyph = code_of(token) - BASE + FIRST_GLYPH
        if FIRST_GLYPH <= glyph <= LAST_GLYPH:
            return glyph
    raise ValueError("shared-font token 0x%X draws no entry-8 glyph" % token)


def _pack(words):
    return b"".join(struct.pack("<I", word) for word in words)


def _i(op, rs, rt, imm):
    return (op << 26) | (rs << 21) | (rt << 16) | (imm & 0xFFFF)


def _branch(op, rs, rt, index, target):
    return _i(op, rs, rt, target - index - 1)


def _selection():
    at, v1, a0, a1, a2, a3 = 1, 3, 4, 5, 6, 7
    SLOT0, STORE = 11, 13
    return [
        _i(0x23, a0, v1, 108),                  # lw    v1, 108(a0)
        _i(0x24, v1, a2, 60),                   # lbu   a2, 60(v1)
        _branch(0x04, a2, 0, 2, STORE),         # beq   a2, zero, STORE
        _i(0x09, a1, a3, -1),                   # addiu a3, a1, -1
        _i(0x0A, a1, at, 101),                  # slti  at, a1, 101
        _branch(0x05, at, 0, 5, SLOT0),         # bne   at, zero, SLOT0
        _i(0x0F, 0, a2, 0x0036),                # lui   a2, 0x0036
        _i(0x0A, a1, at, BASE),                 # slti  at, a1, BASE
        _branch(0x05, at, 0, 8, STORE),         # bne   at, zero, STORE
        _i(0x09, a3, a3, -100),                 # addiu a3, a3, -100
        _i(0x09, a3, a3, 201 - BASE),           # addiu a3, a3, 201 - BASE
        _i(0x23, a2, v1, -7824),                # SLOT0: lw v1, -7824(a2)
        _i(0x09, v1, v1, 20),                   # addiu v1, v1, 20
        _i(0x23, a0, a2, 192),                  # STORE: lw a2, 192(a0)
        _i(0x2B, a2, v1, 24),                   # sw    v1, 24(a2)
        _i(0x29, a2, a3, 46),                   # sh    a3, 46(a2)
        _i(0x29, a2, a1, 44),                   # sh    a1, 44(a2)
        _i(0x23, a0, a1, 148),                  # lw    a1, 148(a0)
        _i(0x2B, a2, a1, 32),                   # sw    a1, 32(a2)
        _i(0x23, a0, a1, 160),                  # lw    a1, 160(a0)
        _i(0x2B, a2, a1, 36),                   # sw    a1, 36(a2)
        _i(0x23, a0, a1, 164),                  # lw    a1, 164(a0)
        _i(0x2B, a2, a1, 40),                   # sw    a1, 40(a2)
        (a2 << 21) | (a3 << 11) | 0x25,         # or    a3, a2, zero
        0, 0, 0, 0,                             # nop
    ]


def renderer_block():
    """The rewritten routine."""
    return tuple(_selection())


def _resident_stream(resource):
    """``(offset, end, expanded)`` of resource 3's resident overlay."""
    offset = 0x10 if resource[:4] == b"ZLS\0" else 0
    found = None
    while offset + sle.HEADER_SIZE <= len(resource):
        if resource[offset:offset + 3] != b"SLE":
            raise ValueError("resource 3 has no SLE stream at 0x%X" % offset)
        stored, expanded_size, next_offset = struct.unpack_from(
            "<III", resource, offset + 4)
        end = offset + sle.HEADER_SIZE + stored
        output = slz.decompress(sle.reveal(resource[offset:end]))
        if len(output) != expanded_size:
            raise ValueError("resource 3 stream at 0x%X expands wrongly" % offset)
        if (len(output) >= 12
                and struct.unpack_from("<I", output, 8)[0] == LOAD_ADDRESS):
            found = (offset, end, output)
        if not next_offset:
            break
        offset += next_offset
    if found is None or found[1] != end:
        raise ValueError("resource 3's resident overlay is not its last stream")
    if any(resource[end:]):
        raise ValueError("resource 3 has data after its resident overlay")
    return found


def resident_overlay(resource):
    """``(offset, expanded)`` of resource 3's resident overlay."""
    offset, _end, output = _resident_stream(bytes(resource))
    return offset, output


def replace_resident(resource, offset, patched):
    """``resource`` with the overlay at ``offset`` re-encoded from ``patched``."""
    resource = bytes(resource)
    encoded = sle.conceal(slz3.compress(bytes(patched)))
    if offset + len(encoded) > len(resource):
        raise ValueError("resource 3's resident overlay no longer fits")
    rebuilt = bytearray(resource)
    rebuilt[offset:] = bytes(len(resource) - offset)
    rebuilt[offset:offset + len(encoded)] = encoded
    if _resident_stream(bytes(rebuilt))[2] != bytes(patched):
        raise ValueError("resource 3's resident overlay did not read back")
    return bytes(rebuilt)


def patch_renderer(resource):
    """``resource`` with the renderer rewrite in; unchanged if already there."""
    resource = bytes(resource)
    offset, output = resident_overlay(resource)
    at = START - LOAD_ADDRESS
    block = renderer_block()
    present = struct.unpack_from("<%dI" % len(ORIGINAL), output, at)
    if present == block:
        return resource
    if present != ORIGINAL:
        raise ValueError(
            "resource 3's glyph routine at 0x%08X is not the retail one"
            % START)
    patched = bytearray(output)
    patched[at:at + 4 * len(block)] = _pack(block)
    return replace_resident(resource, offset, patched)
