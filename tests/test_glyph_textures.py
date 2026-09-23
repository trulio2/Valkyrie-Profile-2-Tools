# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.scripts import (glyph_range, glyph_slots, glyph_textures,
                           overlay_edits)
from tools.scripts.xxh3 import xxh3_64


class Xxh3Test(unittest.TestCase):
    def test_reference_vectors(self):
        self.assertEqual(xxh3_64(b""), 0x2D06800538D394C2)
        self.assertEqual(xxh3_64(b"a"), 0xE6C632B61E964E1F)


class NameTest(unittest.TestCase):
    def test_palettes(self):
        self.assertEqual(
            {k: "%x" % glyph_slots.palette_hash(p)
             for k, p in glyph_slots.PALETTES.items()},
            {0: "6f63a5f713213b43", 1: "51fbe9bf9f52832e",
             2: "24ce994b075a81a2"})

    def test_empty_slot(self):
        cell = bytes(glyph_slots.CELL_BYTES)
        self.assertEqual(
            glyph_slots.replacement_name(cell, glyph_slots.PALETTES[1]),
            "de5f15ab6daf7941-51fbe9bf9f52832e-00001564")

    def test_no_leading_zeros(self):
        for seed in range(200):
            cell = bytes((seed * 7 + i * 13) & 255 for i in range(448))
            name = glyph_slots.replacement_name(cell, glyph_slots.PALETTES[2])
            self.assertFalse(name.startswith("0"))

    def test_indices_low_nibble_first(self):
        cell = bytes([0x21]) + bytes(447)
        plane = glyph_slots.indices(cell)
        self.assertEqual(plane[:2], bytes([1, 2]))
        self.assertEqual(len(plane), 1024)
        self.assertFalse(any(plane[28 * 32:]))


class RewriteTest(unittest.TestCase):
    def overlay(self, which):
        top = max(glyph_slots.WORDS) + 4 - glyph_range.LOAD_ADDRESS
        data = bytearray(top)
        for address, words in glyph_slots.WORDS.items():
            struct.pack_into("<I", data, address - glyph_range.LOAD_ADDRESS,
                             words[which])
        return bytes(data)

    def test_rewrite_and_again(self):
        rewritten = glyph_slots.patch_overlay(self.overlay(0))
        self.assertEqual(rewritten, self.overlay(1))
        self.assertEqual(glyph_slots.patch_overlay(rewritten), rewritten)

    def test_refuses_mixed_and_unknown(self):
        data = bytearray(self.overlay(0))
        first = min(glyph_slots.WORDS)
        at = first - glyph_range.LOAD_ADDRESS
        struct.pack_into("<I", data, at, glyph_slots.WORDS[first][1])
        with self.assertRaises(ValueError):
            glyph_slots.patch_overlay(bytes(data))
        struct.pack_into("<I", data, at, 1)
        with self.assertRaises(ValueError):
            glyph_slots.patch_overlay(bytes(data))


class BattleRewriteTest(unittest.TestCase):
    def overlay(self, which):
        load = overlay_edits.LOAD_ADDRESS
        data = bytearray(max(glyph_slots.BATTLE_WORDS) + 4 - load)
        struct.pack_into("<I", data, 8, load)
        for address, words in glyph_slots.BATTLE_WORDS.items():
            struct.pack_into("<I", data, address - load, words[which])
        return bytes(data)

    def test_rewrite_and_again(self):
        edits = glyph_slots.battle_edits()
        rewritten, changed = overlay_edits.edit_output(self.overlay(0), edits)
        self.assertEqual(self.overlay(1), rewritten)
        self.assertTrue(changed)
        self.assertEqual(0, overlay_edits.edit_output(rewritten, edits)[1])

    def test_tex0_loads_the_stored_word(self):
        new = {address: words[1]
               for address, words in glyph_slots.BATTLE_WORDS.items()}
        stores = [w for w in new.values() if w >> 16 == 0xAFAD]
        loads = [w for w in new.values() if w >> 26 == 0x23]
        self.assertEqual(1, len(stores))
        self.assertEqual(3, len(loads))
        self.assertTrue(all(w & 0xFFFF == stores[0] & 0xFFFF for w in loads))

    def test_refuses_unknown(self):
        data = bytearray(self.overlay(0))
        first = min(glyph_slots.BATTLE_WORDS)
        struct.pack_into("<I", data, first - overlay_edits.LOAD_ADDRESS, 1)
        with self.assertRaises(ValueError):
            overlay_edits.edit_output(bytes(data), glyph_slots.battle_edits())


class RenderTest(unittest.TestCase):
    def test_empty_cell_is_clear(self):
        values = glyph_textures.levels(bytes(448), 2)
        self.assertEqual(len(values), 64 * 64)
        rgba = glyph_textures.colour(values, glyph_slots.PALETTES[2])
        self.assertFalse(any(rgba))

    def test_solid_body(self):
        cell = bytes([0xFF] * 16 * 20) + bytes(448 - 16 * 20)
        values = glyph_textures.levels(cell, 2)
        rgba = glyph_textures.colour(values, glyph_slots.PALETTES[1])
        middle = (20 * 64 + 32) * 4
        self.assertEqual(rgba[middle:middle + 4], bytes([255, 255, 255, 0x80]))
        corner = (63 * 64 + 63) * 4
        self.assertEqual(rgba[corner + 3], 0)

    def test_png(self):
        pixels = bytes([1, 2, 3, 4, 5, 6, 7, 8])
        data = glyph_textures.png(2, 1, pixels)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", data[16:24]), (2, 1))
        at = data.index(b"IDAT")
        size = struct.unpack(">I", data[at - 4:at])[0]
        self.assertEqual(zlib.decompress(data[at + 4:at + 4 + size]),
                         b"\x00" + pixels)


if __name__ == "__main__":
    unittest.main()
