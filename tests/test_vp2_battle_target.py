# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.scripts import overlay_edits
from tools.scripts import vp2_battle_target as target
from tools.scripts import vp2_build


class BattleTargetEncodingTests(unittest.TestCase):
    def test_known_regional_labels_document_the_encoding(self):
        labels = {
            "Target": "3f 0a 19 0c 0e 1f",
            "Objetivo": "24 09 01 0e 1f 02 1d 04",
            "Cible": "28 02 09 07 0e",
            "Ziel": "31 02 0e 07",
            "Alvo": "2a 07 1d 04",
        }
        for label, expected in labels.items():
            with self.subTest(label=label):
                self.assertEqual(bytes.fromhex(expected),
                                 target.encode_label(label))

    def test_the_usa_buffer_cannot_hold_nine_characters(self):
        with self.assertRaisesRegex(ValueError, "at most 8"):
            target.encode_label("Obiettivo")

    def test_unknown_font_characters_are_refused(self):
        with self.assertRaisesRegex(ValueError, "cannot encode"):
            target.encode_label("Alvo!")

    def test_the_compact_encoding_is_ascii_xor_6b(self):
        self.assertEqual(bytes(ord(character) ^ 0x6B
                               for character in "Dungeon"),
                         target.encode_label("Dungeon"))


def usa_overlay():
    output = bytearray(target.LABEL_OFFSET + target.LABEL_CAPACITY)
    struct.pack_into("<I", output, 8, overlay_edits.LOAD_ADDRESS)
    struct.pack_into("<I", output, target.COPY_LENGTH_OFFSET,
                     target.ORIGINAL_COPY_INSTRUCTION)
    struct.pack_into("<I", output, target.TARGET_CALL_OFFSET,
                     target.ORIGINAL_TARGET_CALL)
    struct.pack_into("<I", output, target.LABEL_X_OFFSET,
                     target.ORIGINAL_LABEL_X)
    output[target.LABEL_OFFSET:target.LABEL_OFFSET + 8] = target.ORIGINAL_LABEL
    return bytes(output)


class BattleTargetEditTests(unittest.TestCase):
    def patch(self, output, label, x=0):
        return overlay_edits.edit_output(output, target.edits(label, x))

    def test_alvo_changes_the_label_and_copy_count_only(self):
        original = usa_overlay()
        patched, _changed = self.patch(original, "Alvo")
        expected = bytearray(original)
        expected[target.LABEL_OFFSET:target.LABEL_OFFSET + 8] = (
            bytes.fromhex("2a 07 1d 04 00 00 00 00"))
        struct.pack_into("<I", expected, target.COPY_LENGTH_OFFSET, 0x24060004)
        self.assertEqual(bytes(expected), patched)

    def test_an_x_offset_rewrites_the_label_margin(self):
        for x, instruction in ((8, 0x3C0841E0), (-6, 0x3C084228),
                               ("2.5", 0x3C084206)):
            with self.subTest(x=x):
                patched, _ = self.patch(usa_overlay(), "Alvo", x)
                self.assertEqual(
                    instruction,
                    struct.unpack_from("<I", patched, target.LABEL_X_OFFSET)[0])

    def test_a_zero_offset_leaves_the_margin(self):
        original = usa_overlay()
        patched, _ = self.patch(original, "Alvo", "0")
        at = target.LABEL_X_OFFSET
        self.assertEqual(original[at:at + 4], patched[at:at + 4])

    def test_a_blank_offset_centres_the_label(self):
        for x in ("", None):
            with self.subTest(x=x):
                patched, _ = self.patch(usa_overlay(), "Alvo", x)
                self.assertEqual(  # 36 - 9.5
                    0x3C0841D4,
                    struct.unpack_from("<I", patched, target.LABEL_X_OFFSET)[0])

    def test_centring_measures_each_glyph_as_the_game_does(self):
        # Advances read from glyph boxes in PCSX2 savestates.
        measured = {"T": 14.4, "a": 10.0, "r": 7.8, "g": 10.0, "e": 8.9,
                    "t": 7.8, "A": 14.4, "l": 5.6, "v": 11.1, "o": 8.9}
        for letter, advance in measured.items():
            with self.subTest(letter=letter):
                self.assertAlmostEqual(advance, target.label_advance(letter))
        self.assertEqual(0.0, target.centred_x("Target"))
        self.assertEqual(9.5, target.centred_x("Alvo"))
        self.assertEqual(-7.25, target.centred_x("Objetivo"))

    def test_target_itself_needs_no_offset_edit(self):
        original = usa_overlay()
        patched, _ = self.patch(original, "Target")
        at = target.LABEL_X_OFFSET
        self.assertEqual(original[at:at + 4], patched[at:at + 4])

    def test_an_offset_without_a_label_moves_only_the_label(self):
        original = usa_overlay()
        patched, changed = self.patch(original, None, 4)
        at = target.LABEL_X_OFFSET
        self.assertEqual(original[:at], patched[:at])
        self.assertEqual(original[at + 4:], patched[at + 4:])
        self.assertTrue(changed)

    def test_an_offset_that_cannot_be_placed_is_refused(self):
        for x in ("0.3", "left", "1e9"):
            with self.subTest(x=x):
                with self.assertRaises(ValueError):
                    target.parse_x(x)

    def test_the_build_reads_the_offset_from_the_misc_sheet(self):
        with tempfile.TemporaryDirectory() as folder:
            misc = Path(folder, "misc.csv")
            misc.write_text("key,translated,offset_x,notes\n"
                            "battle_target,Alvo,-3,\n", encoding="utf-8")
            row = {"kind": "misc", "resource": "1781", "sheet": str(misc)}
            self.assertEqual(-3.0, vp2_build.battle_target_x([row]))
            self.assertEqual("Alvo", vp2_build.battle_target_label([row]))

    def test_a_label_of_the_original_length_keeps_the_copy_instruction(self):
        original = usa_overlay()
        patched, _ = self.patch(original, "Target")
        at = target.COPY_LENGTH_OFFSET
        self.assertEqual(original[at:at + 4], patched[at:at + 4])

    def test_a_different_source_label_is_refused(self):
        original = bytearray(usa_overlay())
        original[target.LABEL_OFFSET] ^= 1
        with self.assertRaisesRegex(ValueError, "Target label: expected"):
            self.patch(bytes(original), "Alvo")

    def test_a_different_renderer_call_is_refused(self):
        original = bytearray(usa_overlay())
        struct.pack_into("<I", original, target.TARGET_CALL_OFFSET, 0)
        with self.assertRaisesRegex(ValueError, "renderer call: expected"):
            self.patch(bytes(original), "Alvo")

    def test_patching_the_same_label_twice_is_harmless(self):
        patched, _ = self.patch(usa_overlay(), "Alvo")
        again, changed = self.patch(patched, "Alvo")
        self.assertEqual(patched, again)
        self.assertEqual(0, changed)

    def test_a_second_different_label_is_refused(self):
        patched, _ = self.patch(usa_overlay(), "Alvo")
        with self.assertRaises(ValueError):
            self.patch(patched, "Ziel")


if __name__ == "__main__":
    unittest.main()
