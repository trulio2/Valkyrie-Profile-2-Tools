# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Per-slot glyph textures and their PCSX2 replacement names."""
import struct

from . import glyph_range, overlay_edits
from .xxh3 import xxh3_64

RESOURCE = glyph_range.RESOURCE

WORDS = {
    0x002E7FD4: (0x00125900, 0x00121042),
    0x002E7FDC: (0x02494821, 0x00135100),
    0x002E7FE0: (0x00135100, 0x004A1021),
    0x002E7FE4: (0x256B0008, 0x240B0008),
    0x002E7FE8: (0x254A0008, 0x240A0008),
    0x002E801C: (0x02684021, 0x00000000),
    0x002E8464: (0x3407AA44, 0x3C075644),
    0x002E846C: (0x00073C38, 0x00E23825),
}

BATTLE_WORDS = {
    0x004C93F8: (0x000C5100, 0x3C0A0008),
    0x004C93FC: (0x254A0008, 0x354A0008),
    0x004C9400: (0x000B6900, 0x000C6940),
    0x004C9404: (0x25AD0008, 0x01AB6821),
    0x004C9408: (0x000A503C, 0x000D6842),
    0x004C940C: (0x000A503F, 0x3C015644),
    0x004C9410: (0x000D683C, 0x01A16825),
    0x004C9414: (0x000D683F, 0xAFAD0180),
    0x004C9418: (0x000A5438, 0x00000000),
    0x004C941C: (0x01AA5025, 0x00000000),
    0x004C9420: (0x01684021, 0x00000000),
    0x004C9428: (0x01894821, 0x00000000),
    0x004C9AB8: (0x3406AA44, 0x8FA70180),
    0x004C9ABC: (0x00063C38, 0x00000000),
    0x004C9C00: (0x3404AA44, 0x8FA60180),
    0x004C9C04: (0x00043438, 0x00000000),
    0x004C9D94: (0x340CAA44, 0x8FAF0180),
    0x004C9D9C: (0x000C7C38, 0x00000000),
}

CELL_BYTES = 448
SIZE = 32

NAME_BITS = 0x24 | (5 << 6) | (5 << 10)

_A = (0x12, 0x25, 0x37, 0x49, 0x5B, 0x6E, 0x80)

PALETTES = {
    0: (0x00000000,) + tuple(a << 24 for a in _A) + (0x80000000,)
       + tuple(0x80000000 | v * 0x010101
               for v in (0x24, 0x49, 0x6D, 0x92, 0xB6, 0xDB, 0xFF)),
    1: (0,) * 9 + tuple(a << 24 | 0xFFFFFF for a in _A),
    2: (0,) + tuple(a << 24 | 0xFFFFFF for a in _A) + (0x80FFFFFF,) * 8,
}


def patch_overlay(output):
    """``output`` with the rewrite in; unchanged if already there."""
    patched = bytearray(output)
    state = set()
    for address, (retail, new) in WORDS.items():
        at = address - glyph_range.LOAD_ADDRESS
        word = struct.unpack_from("<I", output, at)[0]
        if word == new:
            state.add("rewritten")
        elif word == retail:
            state.add("retail")
            struct.pack_into("<I", patched, at, new)
        else:
            raise ValueError("resource 3 holds 0x%08X at 0x%08X, neither the "
                             "retail word nor the rewrite" % (word, address))
    if len(state) > 1:
        raise ValueError("resource 3's glyph draw is partly rewritten")
    return bytes(patched)


def is_rewritten(resource):
    _offset, output = glyph_range.resident_overlay(resource)
    return patch_overlay(output) == output


def patch_resource(resource):
    """``resource`` with the rewrite in; unchanged if already there."""
    offset, output = glyph_range.resident_overlay(resource)
    patched = patch_overlay(output)
    if patched == output:
        return bytes(resource)
    return glyph_range.replace_resident(resource, offset, patched)


def battle_edits():
    """The battle labels' copy of the rewrite, as battle overlay edits."""
    return [overlay_edits.Edit(overlay_edits.offset_of(address),
                               overlay_edits.word(retail),
                               overlay_edits.word(new),
                               "glyph slot at 0x%08X" % address)
            for address, (retail, new) in BATTLE_WORDS.items()]


def indices(cell):
    """A cell as the 32x32 index plane of its slot."""
    if len(cell) != CELL_BYTES:
        raise ValueError("a glyph cell is %d bytes, not %d"
                         % (len(cell), CELL_BYTES))
    out = bytearray(SIZE * SIZE)
    for at, byte in enumerate(cell):
        out[2 * at] = byte & 15
        out[2 * at + 1] = byte >> 4
    return bytes(out)


def texture_hash(cell):
    """PCSX2's texture hash of a glyph slot."""
    return xxh3_64(indices(cell))


def palette_hash(palette):
    return xxh3_64(struct.pack("<16I", *palette))


def replacement_name(cell, palette):
    """The file stem PCSX2 looks up."""
    return "%x-%x-%08x" % (texture_hash(cell), palette_hash(palette),
                           NAME_BITS)
