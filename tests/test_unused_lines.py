import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.scripts import undrawn_records  # noqa: E402

HEADER = "resource,first_id,last_id,reclaim,reason\n"


def _write(directory, text):
    path = os.path.join(directory, "unused-lines.csv")
    with io.open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


def _row(resource, message_id):
    return {"resource": str(resource), "message_id": str(message_id),
            "original_en": "Line %s" % message_id, "speaker": ""}


class LoaderTests(unittest.TestCase):
    def test_a_range_names_every_id_in_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write(directory, HEADER + "5,1,3,yes,run\n")
            self.assertEqual(undrawn_records.load_unused_lines(path),
                             {"5": frozenset({"1", "2", "3"})})
            self.assertEqual(undrawn_records.load_reclaimable_unused(path),
                             {"5": frozenset({"1", "2", "3"})})

    def test_reclaim_no_is_hidden_but_never_spent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = _write(directory,
                          HEADER + "5,1,3,no,dead\n6,1,2,yes,dead\n")
            self.assertIn("2", undrawn_records.load_unused_lines(path)["5"])
            self.assertEqual(undrawn_records.load_reclaimable_unused(path),
                             {"6": frozenset({"1", "2"})})


class HiddenTests(unittest.TestCase):
    def test_a_never_loaded_resource_is_hidden_but_kept(self):
        rows = [_row(1311, 1184)]
        self.assertEqual(undrawn_records.hidden_message_ids(rows), {"1184"})
        self.assertEqual(undrawn_records.reclaimable_message_ids(rows), set())

    def test_a_built_scene_spends_its_unused_run(self):
        rows = [_row(1149, 150)]
        self.assertIn("150", undrawn_records.hidden_message_ids(rows))
        self.assertIn("150", undrawn_records.reclaimable_message_ids(rows))

    def test_the_helpers_take_the_table_they_are_given(self):
        table = {"189": frozenset({"2581", "2787"})}
        rows = [_row(189, 2), _row(189, 2581), _row(189, 2787)]
        self.assertEqual(undrawn_records._unused_hidden(rows, table),
                         {"2581", "2787"})
        self.assertEqual(undrawn_records._unused_reclaimable(rows, table),
                         {"2581", "2787"})


if __name__ == "__main__":
    unittest.main()
