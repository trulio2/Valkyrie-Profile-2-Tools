# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import csv
import tempfile
import unittest
from pathlib import Path

from tools.scripts import vp2_cutscene_subtitles as subtitles
from tools.scripts import vp2_glyph_compose as glyph_compose
from tools.scripts import vp2_shared_font as shared_font
from tools.scripts import vp2_text_patch as text_patch
from tools.scripts.paths import PROJECT_ROOT
from tools.scripts.translation_pack import (
    PACK_FIELDS, PACK_SLOTS, is_language_pack, load_pack,
)

PACKS = PROJECT_ROOT / "translations"
#: Entry 8's font holds this many glyphs, so a token outside it has no slot.
SHARED_FONT_SLOTS = 95


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
                self.assertTrue(shared_font.load_slot_assignments(table))

    def test_every_token_has_a_slot_in_the_font(self):
        for name, table in pack_tables():
            for character, token in sorted(
                    shared_font.load_slot_assignments(table).items()):
                with self.subTest(pack=name, character=character):
                    self.assertTrue(1 <= token <= SHARED_FONT_SLOTS)

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


if __name__ == "__main__":
    unittest.main()
