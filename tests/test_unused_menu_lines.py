import csv
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.scripts import translation_layout, undrawn_records  # noqa: E402


HEADER = "resource,first_id,last_id,reason\n"


def _row(resource, message_id, english, japanese="j"):
    return {"resource": str(resource), "message_id": str(message_id),
            "message_index": "", "original_en": english, "original_jp": japanese}


def _key(resource, message_id):
    return ("container", str(resource), str(message_id), "")


def _write_table(directory, text):
    path = os.path.join(directory, "unused-menu-lines.csv")
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


class LoaderTests(unittest.TestCase):
    def test_a_range_names_every_id_in_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_table(directory, HEADER + "7,1,3,debug\n")
            self.assertEqual(undrawn_records.load_unused_menu_lines(path),
                             {"7": frozenset({"1", "2", "3"})})

    def test_it_is_keyed_by_resource_and_message_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write_table(directory, HEADER + "7,1,3,debug\n")
            table = undrawn_records.load_unused_menu_lines(path)
            self.assertTrue(undrawn_records.menu_hidden("7", "2", table))
            self.assertFalse(undrawn_records.menu_hidden("8", "2", table))
            self.assertFalse(undrawn_records.menu_hidden("7", "4", table))


class MenuReferenceTests(unittest.TestCase):
    """A unit is dropped whole, or not at all."""

    def _reference(self):
        rows = {
            _key(642, 1): _row(642, 1, "Debug name"),
            _key(642, 2): _row(642, 2, "Debug name"),
            _key(642, 3): _row(642, 3, "Shared name"),
            _key(648, 9000): _row(648, 9000, "Shared name"),
            _key(648, 9001): _row(648, 9001, "Real name"),
        }
        layout = {
            (1, 1): [_key(642, 1), _key(642, 2)],
            (1, 2): [_key(642, 3), _key(648, 9000)],
            (1, 3): [_key(648, 9001)],
        }
        with tempfile.TemporaryDirectory() as directory:
            layout_path = os.path.join(directory, "unused-menu-lines.csv")
            with io.open(layout_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(HEADER + "642,1,2,debug\n")
            table = undrawn_records.load_unused_menu_lines(layout_path)
            original = undrawn_records.load_unused_menu_lines
            undrawn_records.load_unused_menu_lines = lambda: table
            try:
                translation_layout._menu_reference(rows, layout, Path(directory))
                path = os.path.join(directory, "menu", "menu-1.csv")
                with open(path, encoding="utf-8", newline="") as handle:
                    return [(row["resource"], row["message_id"])
                            for row in csv.DictReader(handle)]
            finally:
                undrawn_records.load_unused_menu_lines = original

    def test_an_all_unused_unit_is_dropped(self):
        listed = self._reference()
        self.assertNotIn(("642", "1"), listed)
        self.assertNotIn(("642", "2"), listed)

    def test_a_unit_with_one_used_record_keeps_its_representative(self):
        listed = self._reference()
        self.assertIn(("642", "3"), listed)   # representative stays 642
        self.assertNotIn(("648", "9000"), listed)

    def test_a_used_unit_is_kept(self):
        self.assertIn(("648", "9001"), self._reference())


if __name__ == "__main__":
    unittest.main()
