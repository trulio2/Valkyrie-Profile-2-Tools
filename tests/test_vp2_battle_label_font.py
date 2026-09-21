# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.scripts import vp2_battle_label_font as font  # noqa: E402
from tools.scripts import vp2_container_text as container_text  # noqa: E402
from tools.scripts import vp2_cutscene_subtitles as subtitles  # noqa: E402
from tools.scripts import vp2_glyph_compose as compose  # noqa: E402
from tools.scripts import overlay_edits  # noqa: E402


TEXT_END = 0x11D80
FONT_START = 0x11E80


def synthetic_font(template_offset=0x1500, free_offset=0xBDBC):
    blob = bytearray(0x19900)
    magic = b"mcps2lib 1.50\0"
    blob[:len(magic)] = magic
    struct.pack_into("<I", blob, 0x20, len(blob))
    struct.pack_into("<5I", blob, 0x24, 0x80, 0x5F80, TEXT_END,
                     FONT_START, 70)
    struct.pack_into("<I", blob, 0x50, 101)
    struct.pack_into("<I", blob, 0x54, 3034)
    rows = [(index + 1, 0) for index in range(3032)]
    rows += [(0x2750, template_offset), (0x2776, free_offset - 1)]
    for index, row in enumerate(sorted(rows)):
        struct.pack_into("<II", blob, 0x80 + index * 8, *row)
    blob[0x5F80 + template_offset:0x5F80 + template_offset + 15] = bytes.fromhex(
        "8a80 cdcc8c3f cdcc8c3f 9d01 8b80 00")
    for character, slot in (("a", 31), ("c", 33), ("e", 35)):
        grid = [[0] * compose.WIDTH for _ in range(compose.HEIGHT)]
        left = 5 + (ord(character) % 3)
        for y in range(12, 24):
            for x in range(left, left + 9):
                grid[y][x] = 15
        start = FONT_START + slot * font.GLYPH_BYTES
        blob[start:start + font.GLYPH_BYTES] = compose.pack(grid)
        blob[TEXT_END + slot * 2:TEXT_END + slot * 2 + 2] = bytes(
            (8 + slot % 3, 10))
    return bytes(blob), dict(font.GLYPHS)


class BattleLabelFontTests(unittest.TestCase):
    def test_accented_labels_enable_the_renderer_punctuation_class(self):
        output = bytearray(font.RENDERER_CLASS_MASK_OFFSET + 4)
        struct.pack_into("<I", output, 8, overlay_edits.LOAD_ADDRESS)
        struct.pack_into("<I", output, font.RENDERER_CLASS_MASK_OFFSET,
                         font.RENDERER_CLASS_MASK_ORIGINAL)

        edits = font.renderer_edits("ãçê")
        patched, changed = overlay_edits.edit_output(output, edits)

        self.assertEqual(1, changed)
        self.assertEqual(font.RENDERER_CLASS_MASK_PATCHED, struct.unpack_from(
            "<I", patched, font.RENDERER_CLASS_MASK_OFFSET)[0])
        again, changed = overlay_edits.edit_output(patched, edits)
        self.assertEqual(patched, again)
        self.assertEqual(0, changed)
        self.assertEqual([], font.renderer_edits("abc"))

    def test_appends_three_cells_without_changing_the_retail_font(self):
        original, glyphs = synthetic_font()
        patched, info = font.patch_font(original, "ãçê")
        self.assertEqual(len(original) + 3 * font.GLYPH_BYTES, len(patched))
        self.assertEqual(("ã", "ç", "ê"), info["characters"])

        meta = container_text.layout(original)
        patched_meta = container_text.layout(patched)
        self.assertEqual(73, patched_meta["glyph_count"])
        self.assertEqual(len(patched), struct.unpack_from("<I", patched, 0x20)[0])
        retail_font = slice(meta["font_start"],
                            meta["font_start"] + 70 * font.GLYPH_BYTES)
        self.assertEqual(original[retail_font], patched[retail_font])

        allowed = set(range(meta["table_start"], meta["text_start"]))
        allowed.update(range(0x20, 0x24))
        allowed.update(range(0x34, 0x38))
        allowed.update(range(font.MESSAGE_COUNT_AT,
                             font.MESSAGE_COUNT_AT + 4))
        for offset in (0xBDBC, 0xBDCB, 0xBDDA):
            at = meta["text_start"] + offset
            allowed.update(range(at, at + len(font.SCRIPT_TEMPLATE)))
        for character, (_stored, slot, base) in glyphs.items():
            target = meta["font_start"] + slot * font.GLYPH_BYTES
            metric = meta["text_end"] + slot * 2
            allowed.update(range(metric, metric + 2))

            base_slot = font._letter_slot(base)
            base_at = meta["font_start"] + base_slot * font.GLYPH_BYTES
            base_block = original[base_at:base_at + font.GLYPH_BYTES]
            mark_character = compose.COMPOSITES[character][1]
            mark = subtitles.ACCENT_MARKS[mark_character]
            expected = compose.compose_character(
                base_block, character, compose.unpack(mark["pixels"]),
                mark["rows"], donor_bottom=mark.get("donor_bottom"),
                horizontal_shift=font.ACCENT_X_SHIFTS.get(character, 0))
            self.assertEqual(expected,
                             patched[target:target + font.GLYPH_BYTES])
            self.assertEqual(
                original[meta["text_end"] + base_slot * 2:
                         meta["text_end"] + base_slot * 2 + 2],
                patched[metric:metric + 2])

        changed = {index for index, pair in enumerate(zip(original, patched))
                   if pair[0] != pair[1]}
        self.assertTrue(changed)
        self.assertEqual(set(), changed - allowed)

    def test_installs_indexed_single_glyph_scripts_for_appended_slots(self):
        original, glyphs = synthetic_font()
        patched, _info = font.patch_font(original, "ãçê")

        meta = container_text.layout(patched)
        indexed = dict(container_text.entries(patched, meta))
        expected = {"ã": (0x2751, 0xBDBC),
                    "ç": (0x2752, 0xBDCB),
                    "ê": (0x2753, 0xBDDA)}
        for character, (message_id, offset) in expected.items():
            with self.subTest(character=character):
                self.assertEqual(offset, indexed[message_id])
                slot = glyphs[character][1]
                script = bytearray(bytes.fromhex(
                    "8a80 cdcc8c3f cdcc8c3f 9d01 8b80 00"))
                script[10:12] = bytes((101 + slot, 1))
                at = meta["text_start"] + offset
                self.assertEqual(bytes(script), patched[at:at + len(script)])
        self.assertEqual(3037, struct.unpack_from("<I", patched, 0x54)[0])

    def test_allocates_after_records_moved_by_the_container_pass(self):
        original, glyphs = synthetic_font(template_offset=0x1612,
                                          free_offset=0xBCEE)
        patched, _info = font.patch_font(original, "ã")
        meta = container_text.layout(patched)
        indexed = dict(container_text.entries(patched, meta))
        self.assertEqual(0xBCEE, indexed[0x2751])

    def test_only_the_upper_marks_receive_the_face_specific_nudge(self):
        self.assertEqual({"ã": 1, "ê": 1}, font.ACCENT_X_SHIFTS)
        self.assertNotIn("ç", font.ACCENT_X_SHIFTS)

    def test_second_application_is_a_no_op(self):
        original, glyphs = synthetic_font()
        patched, _ = font.patch_font(original, "ãçê")
        again, info = font.patch_font(patched, "ãçê")
        self.assertEqual(patched, again)
        self.assertTrue(info["no_op"])

    def test_an_unknown_target_cell_is_refused(self):
        original, _glyphs = synthetic_font()
        patched, _info = font.patch_font(original, "ã")
        damaged = bytearray(patched)
        damaged[FONT_START + 70 * font.GLYPH_BYTES] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "unexpected appended data"):
            font.patch_font(damaged, "ã")


if __name__ == "__main__":
    unittest.main()
