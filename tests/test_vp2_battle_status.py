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
from tools.scripts import vp2_battle_label_font as label_font
from tools.scripts import vp2_battle_status as status
from tools.scripts import vp2_build


class BattleStatusEncodingTests(unittest.TestCase):
    def test_letters_are_ascii_xor_6b(self):
        self.assertEqual(bytes(ord(character) ^ 0x6B for character in "Dungeon"),
                         status.encode("Dungeon"))

    def test_the_four_community_labels_encode(self):
        self.assertEqual(bytes.fromhex("39 0e 1d 02 1d 0e"),
                         status.encode("Revive"))
        self.assertEqual(bytes.fromhex("3f 19 0a 05 18 0d 0e 19"),
                         status.encode("Transfer"))
        self.assertEqual(bytes.fromhex("38 1f 0a 1f 1e 18 3e 1b"),
                         status.encode("StatusUp"))
        self.assertEqual(bytes.fromhex("38 1f 0a 1f 1e 18 2f 04 1c 05"),
                         status.encode("StatusDown"))

    def test_letters_decode_back(self):
        for text in ("Silence", "WeaponBroken", "Reanimar"):
            with self.subTest(text=text):
                self.assertEqual(text, status.decode(status.encode(text)))

    def test_a_space_takes_its_own_byte(self):
        self.assertEqual(bytes.fromhex("38 1f 0a 1f 1e 18 b4 3e 1b"),
                         status.encode("Status Up"))

    def test_supported_accents_use_the_appended_font_routes(self):
        self.assertEqual(bytes.fromhex("10 17 16"), status.encode("ãçê"))
        self.assertEqual("ãçê", status.decode(bytes.fromhex("10 17 16")))

    def test_an_unsupported_accent_or_arrow_is_refused(self):
        for glyph in ("é", "↑", "↓"):
            with self.subTest(glyph=glyph):
                with self.assertRaisesRegex(ValueError, "cannot encode"):
                    status.encode("Status " + glyph)

    def test_accented_portuguese_labels_round_trip(self):
        for text in ("Silêncio", "Petrificação", "Maldição", "Confusão",
                     "ResistênciaAlta", "RecuperarTransferência"):
            with self.subTest(text=text):
                self.assertEqual(text, status.decode(status.encode(text)))

    def test_misc_keys_use_uppercase_hex_indices(self):
        self.assertEqual("battle_status_00", status.key(0))
        self.assertEqual("battle_status_0A", status.key(10))
        self.assertEqual("battle_status_10", status.key(16))

    def test_a_misspelled_key_is_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError, "unknown battle status key"):
            status.translations({"battle_status_99": "Nobody"})

    def test_an_offset_is_refused_until_placement_is_understood(self):
        with self.assertRaisesRegex(ValueError, "offset_x"):
            status.translations({"battle_status_0C": {
                "translated": "Reanimar", "offset_x": "4"}})


def usa_overlay():
    output = bytearray(status.BLOCK_END)
    struct.pack_into("<I", output, 8, overlay_edits.LOAD_ADDRESS)
    for index, pointer in enumerate(status.ORIGINAL_POINTERS):
        struct.pack_into("<I", output, status.TABLE_OFFSET + 4 * index, pointer)
    output[status.BLOCK_OFFSET:status.BLOCK_END] = status._retail_block()
    return bytes(output)


class BattleStatusEditTests(unittest.TestCase):
    def test_a_translation_rewrites_the_block_in_place(self):
        values = {"battle_status_0C": {"translated": "Reanimar",
                                       "offset_x": ""},
                  "battle_status_0D": {"translated": "Status Alto",
                                       "offset_x": ""}}
        patched, changed = overlay_edits.edit_output(
            usa_overlay(), status.edits(values))
        self.assertTrue(changed)
        cursor = status.BLOCK_OFFSET
        expected = [record[0] for record in status.RECORDS]
        expected[0x0C] = "Reanimar"
        expected[0x0D] = "Status Alto"
        for index, text in enumerate(expected):
            pointer = struct.unpack_from(
                "<I", patched, status.TABLE_OFFSET + 4 * index)[0]
            self.assertEqual(overlay_edits.LOAD_ADDRESS + cursor, pointer)
            self.assertTrue(status.BLOCK_OFFSET <= cursor < status.BLOCK_END)
            length = patched[cursor] - status.LENGTH_BASE
            self.assertEqual(text, status.decode(
                patched[cursor + 3:cursor + 3 + length]))
            size = 3 + length
            cursor += -(-size // status.RECORD_ALIGN) * status.RECORD_ALIGN
        self.assertLessEqual(cursor, status.BLOCK_END)
        self.assertEqual(len(patched), status.BLOCK_END)
        self.assertEqual(len(status._retail_block()),
                         status.BLOCK_END - status.BLOCK_OFFSET)

    def test_applying_the_same_labels_twice_is_harmless(self):
        values = {"battle_status_0C": {"translated": "Reanimar",
                                       "offset_x": ""}}
        edits = status.edits(values)
        patched, _ = overlay_edits.edit_output(usa_overlay(), edits)
        again, changed = overlay_edits.edit_output(patched, edits)
        self.assertEqual(patched, again)
        self.assertEqual(0, changed)

    def test_a_blank_value_writes_nothing(self):
        self.assertEqual([], status.edits({"battle_status_0C": {
            "translated": "", "offset_x": ""}}))

    def test_the_build_reads_battle_status_from_misc(self):
        with tempfile.TemporaryDirectory() as folder:
            misc = Path(folder, "misc.csv")
            misc.write_text(
                "key,translated,offset_x,notes\n"
                "battle_status_0C,Reanimar,,Revive\n",
                encoding="utf-8")
            row = {"kind": "misc", "resource": "1781", "sheet": str(misc)}
            edits = vp2_build.battle_overlay_edits([row])
            self.assertTrue(any("battle status" in edit.what
                                for edit in edits))

    def test_the_shipped_ptbr_probe_encodes_and_enables_the_font_patch(self):
        sheet = ROOT / "translations" / "pt-BR" / "misc.csv"
        values = vp2_build.read_misc(sheet)
        translated = status.translations(values)
        self.assertEqual("StãtusAlto", translated[13])
        for text in translated.values():
            self.assertEqual(text, status.decode(status.encode(text)))
        self.assertTrue(status.edits(values))
        edits = vp2_build.battle_overlay_edits([
            {"kind": "misc", "resource": "1781", "sheet": str(sheet)}])
        self.assertTrue(any(
            edit.offset == label_font.RENDERER_CLASS_MASK_OFFSET
            for edit in edits))


if __name__ == "__main__":
    unittest.main()
