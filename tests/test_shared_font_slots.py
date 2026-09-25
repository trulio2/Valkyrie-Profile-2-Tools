# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import csv
import tempfile
import unittest
from pathlib import Path

from tools.scripts import glyph_range
from tools.scripts import vp2_cutscene_subtitles as subtitles
from tools.scripts import vp2_glyph_compose as glyph_compose
from tools.scripts import vp2_shared_font as shared_font
from tools.scripts import vp2_text_patch as text_patch
from tools.scripts.paths import PROJECT_ROOT
from tools.scripts.translation_pack import (
    PACK_FIELDS, PACK_SLOTS, is_language_pack, load_pack,
)

PACKS = PROJECT_ROOT / "translations"



def pack_tables():
    for pack in sorted(PACKS.iterdir()):
        table = pack / PACK_SLOTS
        if is_language_pack(pack) and table.is_file():
            yield pack.name, table


def write_manifest(path, locale="x-test"):
    path.mkdir(parents=True, exist_ok=True)
    (path / "pack.toml").write_text(
        f'format = 2\nlocale = "{locale}"\nname = "Test"\n', encoding="utf-8")


class PackSlotTableTests(unittest.TestCase):
    """Each pack brings its own map, and every pack ships one that works."""

    def test_a_slot_table_is_not_read_as_a_translation_sheet(self):
        with tempfile.TemporaryDirectory() as temporary:
            pack = Path(temporary)
            write_manifest(pack)
            sheet = pack / "dialogue" / "scene-0089.csv"
            sheet.parent.mkdir(parents=True, exist_ok=True)
            with sheet.open("w", encoding="utf-8", newline="") as target:
                csv.DictWriter(target, fieldnames=PACK_FIELDS).writeheader()
            (pack / PACK_SLOTS).write_text(
                "character,token\n\u00e5,0x3C\n", encoding="utf-8")
            self.assertEqual({}, load_pack(pack))

    def test_every_pack_table_loads(self):
        for name, table in pack_tables():
            with self.subTest(pack=name):
                self.assertIsInstance(
                    shared_font.load_slot_assignments(table), dict)

    def test_every_token_has_a_slot_in_the_font(self):
        for name, table in pack_tables():
            for character, token in sorted(
                    shared_font.load_slot_assignments(table).items()):
                with self.subTest(pack=name, character=character):
                    glyph_range.glyph_for_token(token)

    def test_every_assigned_character_can_be_drawn(self):
        """A token with no way to draw its letter fails late, mid-build."""
        for name, table in pack_tables():
            for character in sorted(shared_font.load_slot_assignments(table)):
                with self.subTest(pack=name, character=character):
                    recipe = glyph_compose.COMPOSITES.get(character)
                    drawable = (
                        (recipe is not None
                         and recipe[1] in subtitles.ACCENT_MARKS)
                        or character in subtitles.ACCENTS
                        or character in subtitles.POOL)
                    self.assertTrue(drawable)


class ActiveSlotMapTests(unittest.TestCase):
    """Selecting a map has to reach the modules that already imported it."""

    def setUp(self):
        self.restore = dict(shared_font.SHARED_EXTENSION_TOKENS)
        self.addCleanup(self._restore)

    def _restore(self):
        shared_font.SHARED_EXTENSION_TOKENS.clear()
        shared_font.SHARED_EXTENSION_TOKENS.update(self.restore)

    def test_selecting_a_map_reaches_a_module_that_imported_it_by_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            table = Path(temporary) / PACK_SLOTS
            table.write_text("character,token\n\u00e5,0x3C\n",
                             encoding="utf-8")
            shared_font.use_slot_assignments(table)
        self.assertEqual({"\u00e5": 0x3C}, shared_font.SHARED_EXTENSION_TOKENS)
        # Imported at module load, long before the selection above.
        self.assertEqual({"\u00e5": 0x3C}, text_patch.SHARED_EXTENSION_TOKENS)

    def test_the_default_table_is_used_when_nothing_selects_one(self):
        self.assertEqual(
            shared_font.load_slot_assignments(shared_font.DEFAULT_SLOT_TABLE),
            self.restore)


class DisplacedCharacterTests(unittest.TestCase):
    """A slot a pack gives to a letter no longer draws the character it held."""

    MAP = {"Ú": 0x0C, "ç": 0x5F}

    def test_the_displaced_characters_are_named(self):
        self.assertEqual({"+": "Ú", "~": "ç"},
                         shared_font.displaced_characters(self.MAP))

    def test_a_container_record_refuses_a_displaced_character(self):
        from tools.scripts import vp2_container_text as container_text
        with self.assertRaisesRegex(ValueError, "draws 'Ú'"):
            container_text.encode_codepage("Ataque+", accent_tokens=self.MAP)

    def test_a_fontless_record_refuses_a_displaced_character(self):
        with self.assertRaisesRegex(ValueError, "draws 'Ú'"):
            text_patch.encode_english_text("Ataque+", self.MAP)

    def test_the_letter_itself_still_encodes(self):
        from tools.scripts import vp2_container_text as container_text
        self.assertEqual(
            b"\x0C\x00",
            container_text.encode_codepage("Ú", accent_tokens=self.MAP))


class GlyphRangeTests(unittest.TestCase):
    """Codes from 0x400 draw entry-8 glyphs 100 and up."""

    def test_tokens_and_glyphs_round_trip(self):
        from tools.scripts import glyph_range
        for glyph in (0, 94, 99, 100, 101, 127):
            token = glyph_range.token_for_glyph(glyph)
            self.assertEqual(glyph, glyph_range.glyph_for_token(token))
        self.assertEqual(0x0880, glyph_range.token_for_glyph(100))

    def test_a_local_font_token_is_not_in_the_range(self):
        from tools.scripts import glyph_range
        for token in (0x0165, 0x01FF, 0x8080, 0x8099):
            self.assertFalse(glyph_range.is_range_token(token))
            with self.assertRaises(ValueError):
                glyph_range.glyph_for_token(token)

    def test_the_rewrite_fills_the_routine_and_ends_in_nops(self):
        from tools.scripts import glyph_range
        block = glyph_range.renderer_block()
        self.assertEqual(len(glyph_range.ORIGINAL), len(block))
        self.assertEqual((0, 0, 0, 0), block[-4:])


if __name__ == "__main__":
    unittest.main()
