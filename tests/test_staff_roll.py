# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import unittest

from tools.scripts import vp2_container_text  # noqa: F401
from tools.scripts import staff_roll


def overlay(ids, padding=0x18):
    """A blob with a heading table for *ids*, then zero padding, then data."""
    body = bytearray(b"MWo3" + bytes(12))
    for message_id in ids:
        body += struct.pack("<BxxxI", staff_roll.HEADING, message_id)
    body += staff_roll.END + bytes(padding) + b"\x01\x02\x03\x04"
    return bytes(body)


class HeadingTableTests(unittest.TestCase):
    def test_the_table_is_read_back_to_its_first_entry(self):
        blob = overlay([2000, 2856])
        start, terminator, entries, room = staff_roll.heading_table(blob)
        self.assertEqual(start, 16)
        self.assertEqual(terminator, 32)
        self.assertEqual(entries, [(1, 2000), (1, 2856)])
        self.assertEqual(room, 0x18)

    def test_a_new_heading_is_appended_before_the_terminator(self):
        blob = overlay([2000])
        patched, new = staff_roll.add_headings(blob, [2000, 3332])
        self.assertEqual(new, [3332])
        self.assertEqual(len(patched), len(blob))
        _start, _end, entries, room = staff_roll.heading_table(patched)
        self.assertEqual(entries, [(1, 2000), (1, 3332)])
        self.assertEqual(room, 0x10)
        self.assertTrue(patched.endswith(b"\x01\x02\x03\x04"))

    def test_nothing_changes_when_every_heading_is_listed(self):
        blob = overlay([2000, 2856])
        patched, new = staff_roll.add_headings(blob, [2856])
        self.assertEqual((patched, new), (blob, []))

    def test_more_headings_than_padding_are_refused(self):
        blob = overlay([2000], padding=8)
        with self.assertRaises(staff_roll.StaffRollError):
            staff_roll.add_headings(blob, [3332, 3336])


def section_bank():
    """A roll of blank cells, ids 0-63, with text at 20/21 and at 60."""
    blank = bytes([0x66, 0x80, 0x80, 0x00])
    records = {message_id: blank for message_id in range(64)}
    records[20] = staff_roll.container_text.encode_codepage("Dev")
    records[21] = staff_roll.container_text.encode_codepage("Co")
    records[60] = staff_roll.container_text.encode_codepage("Next")
    table_start = 0x80
    text_start = table_start + len(records) * 8
    body = b"".join(records[message_id] for message_id in sorted(records))
    text_end = text_start + len(body) + 0x10
    font_start = text_end + 0x10
    blob = bytearray(font_start + 2 * 448)
    blob[:13] = b"mcps2lib 1.50"
    struct.pack_into("<6I", blob, 0x20, len(blob), table_start, text_start,
                     text_end, font_start, 2)
    struct.pack_into("<I", blob, 0x50, 0x65)
    offset = 0
    for message_id in sorted(records):
        struct.pack_into("<II", blob, table_start + message_id * 8,
                         message_id, offset)
        record = records[message_id]
        blob[text_start + offset:text_start + offset + len(record)] = record
        offset += len(records[message_id])
    return bytes(blob)


SECTION = {31: [(4, "heading", "Team"), (8, "role", "Code"), (9, "name", "Ann"),
                (13, "name", ""), (16, "role", "Text"), (17, "name", ""),
                (20, "follow", ""), (21, "follow", "")]}


class SectionLayoutTests(unittest.TestCase):
    def lay_out(self, translations):
        rows = {key: {"translated": text} for key, text in translations.items()}
        for key, text in staff_roll.section_defaults(31, SECTION).items():
            rows.setdefault(key, {"translated": text})
        laid = staff_roll.lay_out_section(section_bank(), 31, rows, SECTION)
        return {int(key): row["translated"] for key, row in laid.items()}

    def test_the_section_owns_the_ids_between_texts(self):
        self.assertEqual(
            staff_roll.section_region(section_bank(), 31, SECTION), (0, 60))

    def test_filled_lines_are_packed_and_empty_roles_left_out(self):
        self.assertEqual(self.lay_out({}),
                         {4: "Team", 8: "Code", 9: "Ann"})

    def test_a_name_slot_takes_a_line_only_when_filled(self):
        laid = self.lay_out({"13": "Bo", "17": "Cy"})
        self.assertEqual(laid[13], "Bo")
        self.assertEqual((laid[20], laid[21]), ("Text", "Cy"))

    def test_the_following_line_moves_down_when_the_section_reaches_it(self):
        laid = self.lay_out({"13": "Bo", "17": "Cy"})
        self.assertEqual(laid[32], "<FROM:20>")
        self.assertEqual(laid[33], "<FROM:21>")

    def test_a_section_longer_than_the_room_is_refused(self):
        section = {31: SECTION[31][:5] + [(21 + 4 * n, "name", "")
                                          for n in range(1, 8)]
                   + SECTION[31][5:]}
        rows = {str(message_id): {"translated": "X"}
                for message_id, part, _text in section[31] if part != "follow"}
        with self.assertRaises(staff_roll.StaffRollError):
            staff_roll.lay_out_section(section_bank(), 31, rows, section)

    def test_text_in_a_cell_the_section_lays_out_over_is_refused(self):
        with self.assertRaises(staff_roll.StaffRollError):
            staff_roll.lay_out_section(
                section_bank(), 31, {"24": {"translated": "X"}}, SECTION)


if __name__ == "__main__":
    unittest.main()
