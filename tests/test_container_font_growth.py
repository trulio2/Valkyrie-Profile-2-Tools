# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import unittest

from tools.scripts import vp2_container_text as container_text


def mixed_container(record):
    """A container with one record and a two-slot font naming ``a`` and ``b``."""
    table_start = 0x80
    text_start = table_start + 8
    text_end = text_start + 0x20
    font_start = text_end + 0x20
    blob = bytearray(font_start + 2 * 448)
    blob[:13] = b"mcps2lib 1.50"
    struct.pack_into("<6I", blob, 0x20, len(blob), table_start, text_start,
                     text_end, font_start, 2)
    struct.pack_into("<I", blob, 0x50, 0x65)
    struct.pack_into("<II", blob, table_start, 0, 0)
    blob[text_start:text_start + len(record)] = record
    for slot in range(2):
        origin = font_start + slot * 448
        blob[origin + 10 * 16:origin + 448] = bytes([0xFF] * (448 - 10 * 16))
        blob[text_end + slot * 2] = 12
    return blob


MIXED_ALPHABET = {0: "a", 1: "b"}


class CodepageLocalNamingTests(unittest.TestCase):
    """Naming a slot is safe only where the record draws nothing shared."""

    def test_a_wholly_local_record_is_named(self):
        record = bytes([0x65, 0x66, 0x00])
        blob = mixed_container(record)
        meta = container_text.layout(blob)
        self.assertTrue(container_text.codepage_record_is_local(blob, meta, 0))
        text, _length = container_text.render_codepage(
            blob, meta, 0, alphabet=MIXED_ALPHABET)
        self.assertEqual(text, "ab")

    def test_a_record_that_also_spells_shared_text_is_not(self):
        shared = container_text.encode_codepage("b")[:-1]
        record = bytes([0x65]) + shared + bytes(1)
        blob = mixed_container(record)
        meta = container_text.layout(blob)
        self.assertFalse(container_text.codepage_record_is_local(blob, meta, 0))
        text, _length = container_text.render_codepage(
            blob, meta, 0, alphabet=MIXED_ALPHABET)
        self.assertEqual(text, "<0065>b")

        runs = container_text.codepage_run_tokens(blob, meta, 0, MIXED_ALPHABET)
        self.assertEqual(runs, [{}])
        self.assertEqual(
            container_text.encode_codepage(text, local_tokens=runs), record)

    def test_a_record_the_shared_codepage_spells_needs_no_face(self):
        record = container_text.encode_codepage("Yes")
        blob = mixed_container(record)
        meta = container_text.layout(blob)
        self.assertEqual(container_text.codepage_record_runs(blob, meta, 0), [[]])

        _grown, new_meta, _alphabet, runs, recut = (
            container_text.grow_codepage_font(
                blob, meta, MIXED_ALPHABET, {0: "Sim"}, 10))
        self.assertEqual(recut, [])
        self.assertEqual(new_meta["glyph_count"], meta["glyph_count"])
        self.assertEqual(runs, {0: [{}]})

        patched, written = container_text.rebuild_codepage_records(
            blob, 10, {"0": {"translated": "Sim"}}, alphabet=MIXED_ALPHABET)
        self.assertEqual(written, 1)
        self.assertEqual(patched[meta["text_start"]:
                                  meta["text_start"] + len(record)],
                         container_text.encode_codepage("Sim")
                         + bytes(len(record) - len(container_text.encode_codepage("Sim"))))

    def test_a_mixed_record_keeps_the_slots_its_tags_still_name(self):
        """Its local glyphs travel back as <XXXX>, so they are still drawn."""
        record = (bytes([0x65]) + container_text.encode_codepage("b")[:-1]
                  + bytes(1))
        blob = mixed_container(record)
        meta = container_text.layout(blob)
        text, _length = container_text.render_codepage(
            blob, meta, 0, alphabet=MIXED_ALPHABET)
        self.assertEqual(text, "<0065>b")
        _grown, _meta, _alphabet, runs, recut = (
            container_text.grow_codepage_font(
                blob, meta, MIXED_ALPHABET, {0: "<0065>a"}, 10))
        self.assertEqual(recut, [])
        self.assertEqual(runs, {0: [{}]})

    def test_a_named_record_still_round_trips(self):
        record = bytes([0x65, 0x66, 0x00])
        blob = mixed_container(record)
        meta = container_text.layout(blob)
        text, _length = container_text.render_codepage(
            blob, meta, 0, alphabet=MIXED_ALPHABET)
        runs = container_text.codepage_run_tokens(blob, meta, 0, MIXED_ALPHABET)
        self.assertEqual(
            container_text.encode_codepage(text, local_tokens=runs), record)


def two_cut_container(glyph_count=4, base=0x65):
    """A container whose font is two cuts of the same two letters."""
    table_start = 0x80
    text_start = table_start + 2 * 8
    text_end = text_start + 0x20
    font_start = text_end + 0x20
    blob = bytearray(font_start + glyph_count * 448)
    blob[:13] = b"mcps2lib 1.50"
    struct.pack_into("<6I", blob, 0x20, len(blob), table_start, text_start,
                     text_end, font_start, glyph_count)
    struct.pack_into("<I", blob, 0x50, base)

    def code(slot):
        """The bytes a record spends to draw *slot*, paged as the game pages."""
        value = base + slot
        return bytes([value]) if value < 0x80 else bytes([value, 0x01])

    boundary = bytes([0x8E, 0x80, 0x00, 0x00, 0x80, 0xBF])
    first = code(0) + code(1) + boundary + code(2) + code(3) + bytes(1)
    second = code(1) + code(0) + bytes(1)
    struct.pack_into("<II", blob, table_start, 0, 0)
    struct.pack_into("<II", blob, table_start + 8, 1, len(first))
    blob[text_start:text_start + len(first)] = first
    blob[text_start + len(first):
         text_start + len(first) + len(second)] = second
    for slot in range(glyph_count):
        top = 10 + (slot // 2) * 2
        origin = font_start + slot * 448
        blob[origin + top * 16:origin + 448] = bytes(
            [0xFF] * (448 - top * 16))
        blob[text_end + slot * 2] = 12
    return blob


TWO_CUT_ALPHABET = {0: "a", 1: "b", 2: "a", 3: "b"}


class CodepageFontGrowthTests(unittest.TestCase):
    """Growing a container's own font, per cut."""

    def test_the_font_partitions_into_the_cuts_its_runs_draw_from(self):
        blob = two_cut_container()
        meta = container_text.layout(blob)
        cut_of, cuts = container_text.codepage_font_cuts(
            blob, meta, TWO_CUT_ALPHABET)
        self.assertEqual(len(cuts), 2)
        self.assertEqual(sorted(sorted(table.items()) for table in cuts.values()),
                         [[("a", 0), ("b", 1)], [("a", 2), ("b", 3)]])
        self.assertNotEqual(cut_of[0], cut_of[2])

    def test_a_new_glyph_is_cut_from_the_face_that_will_draw_it(self):
        blob = two_cut_container()
        meta = container_text.layout(blob)
        grown, new_meta, alphabet, runs, recut = (
            container_text.grow_codepage_font(
                blob, meta, TWO_CUT_ALPHABET,
                {0: "áb<808E:000080BF>ab"}, 31))
        self.assertEqual([row["character"] for row in recut], ["á"])
        slot = recut[0]["slot"]
        self.assertIsNone(recut[0]["released"])
        self.assertEqual(new_meta["glyph_count"], 5)
        self.assertEqual(slot, 4)

        def cell(source, number):
            origin = meta["font_start"] + number * 448
            return bytes(source[origin:origin + 448])

        self.assertEqual(cell(grown, slot)[10 * 16:], cell(blob, 0)[10 * 16:])
        self.assertNotEqual(cell(grown, slot)[10 * 16:], cell(blob, 2)[10 * 16:])
        self.assertNotEqual(cell(grown, slot), cell(blob, 0))
        self.assertEqual(grown[meta["text_end"] + slot * 2], 12)
        self.assertEqual(alphabet[slot], "á")
        self.assertEqual(runs[0][0]["á"], 0x65 + slot)

    def test_a_slot_no_record_still_draws_is_reclaimed_before_appending(self):
        blob = two_cut_container()
        meta = container_text.layout(blob)
        _grown, new_meta, _alphabet, _runs, recut = (
            container_text.grow_codepage_font(
                blob, meta, TWO_CUT_ALPHABET,
                {0: "áb<808E:000080BF>ab", 11: "b"}, 31))
        self.assertEqual([row["character"] for row in recut], ["á"])
        self.assertEqual(recut[0]["slot"], 0)
        self.assertEqual(recut[0]["released"], "a")
        self.assertEqual(new_meta["glyph_count"], 4)

    def test_a_letter_the_face_does_not_hold_is_refused(self):
        """There is no source for it: the other cut is the wrong weight."""
        blob = two_cut_container()
        meta = container_text.layout(blob)
        with self.assertRaises(ValueError) as caught:
            container_text.grow_codepage_font(
                blob, meta, TWO_CUT_ALPHABET,
                {0: "Zb<808E:000080BF>ab"}, 31)
        self.assertIn("not a mark over a letter", str(caught.exception))

    def test_losing_a_run_boundary_is_refused(self):
        blob = two_cut_container()
        meta = container_text.layout(blob)
        with self.assertRaises(ValueError) as caught:
            container_text.grow_codepage_font(
                blob, meta, TWO_CUT_ALPHABET, {0: "abab"}, 31)
        self.assertIn("run-boundary tag was added or lost",
                      str(caught.exception))

    def test_the_face_stops_at_the_last_code_a_record_can_address(self):
        blob = two_cut_container(base=0xFC)
        meta = container_text.layout(blob)
        self.assertEqual(
            container_text.codepage_record_runs(blob, meta, 0),
            [[0, 1], [2, 3]])
        with self.assertRaises(ValueError) as caught:
            container_text.grow_codepage_font(
                blob, meta, TWO_CUT_ALPHABET,
                {0: "áb<808E:000080BF>ab"}, 31)
        self.assertIn("out of glyph codes", str(caught.exception))

    def test_a_translation_reads_back_through_the_grown_face(self):
        blob = two_cut_container()
        patched, written = container_text.rebuild_codepage_records(
            blob, 31, {"0": {"translated": "áb<808E:000080BF>ab"}},
            alphabet=TWO_CUT_ALPHABET)
        self.assertEqual(written, 1)
        meta = container_text.layout(patched)
        self.assertEqual(meta["glyph_count"], 5)
        alphabet = dict(TWO_CUT_ALPHABET)
        alphabet[4] = "á"
        _meta, messages = container_text.read_messages(
            bytes(patched), 31, alphabet=alphabet)
        by_id = {message["message_id"]: message for message in messages}
        self.assertEqual(by_id[0]["original_en"],
                         "áb<808E:000080BF>ab")
        self.assertEqual(by_id[1]["original_en"], "ba")


if __name__ == "__main__":
    unittest.main()
