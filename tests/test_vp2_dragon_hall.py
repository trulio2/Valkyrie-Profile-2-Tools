import csv
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.scripts import build_patchers
from tools.scripts import scene_fonts
from tools.scripts import scene_layout
from tools.scripts import scene_sheet_export
from tools.scripts import scene_text
from tools.scripts import sle
from tools.scripts import slz_compress
from tools.scripts import vp2_container_text as container_text
from tools.scripts import vp2_dragon_hall as dragon


BANK_SIZE = 0x480
TABLE_START = 0x80
TEXT_START = 0x200


def fixture_resource():
    expanded = bytearray((index * 37) & 0xFF for index in range(4480))
    bank = bytearray(BANK_SIZE)
    bank[:13] = b"mcps2lib 1.50"
    struct.pack_into("<5I", bank, 0x20, BANK_SIZE, TABLE_START, TEXT_START, 0, 0)
    for index, (message_id, english, _japanese) in enumerate(dragon.TEXT_FIELDS):
        relative = message_id - dragon.MESSAGE_ID
        struct.pack_into("<II", bank, TABLE_START + index * 8,
                         dragon.TABLE_ID[message_id], relative)
        encoded = container_text.encode_codepage(english)
        bank[TEXT_START + relative:TEXT_START + relative + len(encoded)] = encoded
    expanded[dragon.BANK_OFFSET:dragon.BANK_OFFSET + BANK_SIZE] = bank
    stream = dragon.protect(
        slz_compress.compress(
            expanded, mode=2, target_size=2544, cache_dir=""))
    return b"prefix" + stream + b"suffix"


def expanded_of(raw, details):
    return sle.decompress(raw[details["stream_offset"]:])


class DragonHallPromptTests(unittest.TestCase):
    def test_patch_preserves_resource_and_stream_size(self):
        raw = fixture_resource()
        rebuilt, details = dragon.patch_raw(raw, "Choose a stone?")
        self.assertEqual(len(raw), len(rebuilt))
        self.assertEqual(dragon.extract_english(rebuilt), "Choose a stone?")
        self.assertEqual(details["wrapper"], "SLE")
        before, after = expanded_of(raw, details), expanded_of(rebuilt, details)
        bank_end = dragon.BANK_OFFSET + BANK_SIZE
        self.assertEqual(before[:dragon.BANK_OFFSET], after[:dragon.BANK_OFFSET])
        self.assertEqual(before[bank_end:], after[bank_end:])

    def test_a_name_may_outgrow_its_english_slot(self):
        halo, painted = 0xEA3, 0xEAE
        rebuilt, details = dragon.patch_raw(
            fixture_resource(), {halo: "Pedra da Aureola"})
        self.assertEqual(details["fields"], {halo: "Pedra da Aureola"})
        self.assertEqual(dragon.extract_english(rebuilt, painted),
                         "Painted Cloud Stone")
        self.assertEqual(dragon.extract_english(rebuilt, 0xEE6),
                         "Ring of Mylinn")

    def test_unused_names_give_way_only_when_the_bank_is_full(self):
        def grown(extra):
            return {message_id: english + " " + "x" * extra
                    for message_id, english, _japanese in dragon.TEXT_FIELDS
                    if message_id not in dragon.UNUSED_FIELDS}

        rebuilt, _details = dragon.patch_raw(fixture_resource(), grown(40))
        self.assertEqual(dragon.extract_english(rebuilt, 0xEF5), "Dragon Orb")
        stones = grown(55)
        rebuilt, _details = dragon.patch_raw(fixture_resource(), stones)
        for message_id in dragon.UNUSED_FIELDS:
            self.assertEqual(dragon.extract_english(rebuilt, message_id), "")
        for message_id, text in stones.items():
            self.assertEqual(dragon.extract_english(rebuilt, message_id), text)

    def test_rejects_names_larger_than_the_bank(self):
        with self.assertRaisesRegex(ValueError, "do not fit"):
            dragon.patch_raw(fixture_resource(), "x" * BANK_SIZE)

    def test_container_manifest_dispatches_the_custom_record(self):
        class MemoryIso:
            def __init__(self, raw):
                self.raw = raw

            def read_entry(self, _resource):
                return self.raw

            def write_entry(self, _resource, raw):
                self.raw = bytes(raw)

        with tempfile.TemporaryDirectory() as temporary:
            sheet = Path(temporary) / "container-0328.csv"
            with sheet.open("w", encoding="utf-8", newline="") as target:
                fields = [
                    "kind", "resource", "message_id", "message_index",
                    "record_kind", "original_en", "translated", "notes",
                ]
                writer = csv.DictWriter(target, fieldnames=fields)
                writer.writeheader()
                writer.writerow({
                    "kind": "container", "resource": "328",
                    "message_id": str(dragon.MESSAGE_ID), "message_index": "",
                    "record_kind": "dragon_hall_prompt",
                    "original_en": dragon.ORIGINAL_EN,
                    "translated": "Choose a stone?", "notes": "",
                })
            iso = MemoryIso(fixture_resource())
            result = build_patchers.patch_container_resource_in_memory(
                iso, {
                    "resource": "328", "sheet": str(sheet),
                    "flags": "shared-font-glyphs",
                })
        self.assertEqual(result["written"], 1)
        self.assertEqual(dragon.extract_english(iso.raw), "Choose a stone?")


class SharedHeaderTests(unittest.TestCase):
    def test_reference_export_keeps_an_interior_blank_row(self):
        self.assertEqual(
            "preferred patrons.\n\nAmong these",
            scene_sheet_export.export_run_text(
                "preferred patrons.\n \nAmong these"))

    def test_full_font_uses_the_lowest_duplicate_space_metric(self):
        glyph_bytes = 4
        text_end = 0x80
        font_start = text_end + 49 * 2
        expanded = bytearray(font_start + 49 * glyph_bytes)
        expanded[text_end + 4 * 2:text_end + 4 * 2 + 2] = bytes((5, 0))
        expanded[text_end + 48 * 2:text_end + 48 * 2 + 2] = bytes((18, 0))
        layout = {
            "text_end": text_end, "font_start": font_start,
            "glyph_count": 49, "glyph_bytes": glyph_bytes,
        }
        alphabet = {4: " ", 48: " "}
        scene_fonts.apply_full_font(
            expanded, layout, alphabet, {" ": 4}, set(), object())
        self.assertEqual(
            bytes((5, 0)), scene_fonts.glyph_metric(expanded, layout, 4))

    def test_same_line_word_runs_keep_the_authored_part_space(self):
        self.assertEqual(
            " em algum lugar",
            scene_layout.preserve_translated_run_spacing(
                "Anotações do Comerciante", "\nsomewhere",
                " em algum lugar", "em algum lugar"))
        self.assertEqual(
            ".",
            scene_layout.preserve_translated_run_spacing(
                "Comerciante", "\n.", " .", "."))

    def test_standalone_angle_heading_uses_shared_ui_face(self):
        for heading in (
                "<Shopkeeper's Notes>",
                "<Making Valued Customer Items>",
                "<Theology>"):
            self.assertTrue(scene_text.row_uses_shared_header({
                "original_en": heading,
            }))

    def test_angle_text_inside_dialogue_does_not_force_the_ui_face(self):
        self.assertFalse(scene_text.row_uses_shared_header({
            "original_en": "Read <Theology> before continuing.",
        }))

    def test_structured_header_chevrons_do_not_make_it_local_dialogue(self):
        metadata = {"glyph_base": 0x65, "glyph_count": 2}
        tokens = [0x65, 0x22, 0x66]  # local <, shared A, local >
        self.assertTrue(scene_text.run_uses_shared_header(
            "<A>\n", tokens, metadata, {0: "<", 1: ">"}))
        self.assertFalse(scene_text.run_uses_shared_header(
            "Read <A>", tokens, metadata, {0: "<", 1: ">"}))

    def test_structured_ui_page_is_not_limited_to_three_dialogue_lines(self):
        alphabet = {0: "<", 1: ">"}
        record = (bytes([0x65, 0x22, 0x66])
                  + struct.pack("<H", 0x8089) + bytes([0x23])
                  + struct.pack("<H", 0x8089) + bytes([0x24]) + b"\0")
        metadata = {
            "text_start": 0, "text_end": 0,
            "glyph_base": 0x65, "glyph_count": len(alphabet),
        }
        row = {
            "message_id": "37", "audio_id": "r0043-m0037",
            "original_en": "<A> <PART> B <PART> C",
            "translated": "<D>\n <PART> Q\n <PART> one\ntwo\nthree",
        }
        with mock.patch.object(
                scene_text, "message_pointers",
                return_value=([(0, 37, 0)], {0: len(record)})), \
                mock.patch.object(
                    scene_text, "glyph_advances",
                    return_value={character: 8 for character in
                                  "<>ABCDQonetwhr."}):
            replacements, _rendered = scene_text.run_replacements(
                bytearray(record), metadata, alphabet, 0x65, [row])
        self.assertIn(0, replacements)

    def test_forced_header_replacement_uses_no_scene_font_token(self):
        alphabet = {0: "<", 1: "A", 2: ">"}
        record = bytes((0x65, 0x66, 0x67, 0))
        metadata = {
            "text_start": 0, "text_end": 0,
            "glyph_base": 0x65, "glyph_count": len(alphabet),
        }
        row = {
            "message_id": "19", "audio_id": "header",
            "original_en": "<A>", "translated": "<B>",
        }
        with mock.patch.object(
                scene_text, "message_pointers",
                return_value=([(0, 19, 0)], {0: len(record)})), \
                mock.patch.object(
                    scene_text, "glyph_advances",
                    return_value={"<": 8, "A": 8, "B": 8, ">": 8}):
            replacements, _rendered = scene_text.run_replacements(
                bytearray(record), metadata, alphabet, 0x65, [row])
        tokens = scene_text.byte_tokens(replacements[0][0][2])
        self.assertTrue(tokens)
        self.assertEqual(tokens[0], 0x65)
        self.assertEqual(tokens[-1], 0x67)
        self.assertTrue(all(
            scene_text.token_slot(token, 0x65, len(alphabet)) is None
            for token in tokens[1:-1] if token < 0x8000))


if __name__ == "__main__":
    unittest.main()
