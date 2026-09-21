# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tools.scripts import row_cache


class FakeIso:
    """Enough of an IsoFile for a row to read from and write to."""

    def __init__(self, entries):
        self.entries = dict(entries)
        self.journal = None

    def read_entry(self, resource):
        data = self.entries[resource]
        if self.journal is not None:
            self.journal.read(resource, data)
        return data

    def write_entry(self, resource, new_bytes):
        self.entries[resource] = bytes(new_bytes)
        if self.journal is not None:
            self.journal.wrote(resource, new_bytes)
        return new_bytes


class JournalTests(unittest.TestCase):
    def test_a_read_is_recorded_once_at_its_first_value(self):
        iso = FakeIso({8: b"font", 49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.read_entry(49)
        iso.write_entry(49, b"patched")
        iso.read_entry(49)
        self.assertEqual([49], list(iso.journal.reads))
        self.assertEqual(
            row_cache.hashlib.sha256(b"scene").hexdigest(),
            iso.journal.reads[49])

    def test_every_write_is_recorded_in_order(self):
        iso = FakeIso({8: b"font", 49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.write_entry(49, b"one")
        iso.write_entry(8, b"two")
        self.assertEqual([(49, b"one"), (8, b"two")], iso.journal.writes)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="vp2-rows-")
        self.addCleanup(shutil.rmtree, self.store, ignore_errors=True)

    def _record(self, name="a" * 64, details=None, verified=False):
        iso = FakeIso({8: b"font", 49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.read_entry(49)
        iso.read_entry(8)
        iso.write_entry(49, b"patched scene")
        row_cache.save(self.store, name, iso.journal,
                       details or {"written": 3}, verified=verified)
        return name

    def test_a_stored_row_replays_its_writes(self):
        name = self._record()
        fresh = FakeIso({8: b"font", 49: b"scene"})
        entry = row_cache.load(self.store, name)
        self.assertTrue(entry.matches(fresh))
        details = entry.replay(fresh)
        self.assertEqual(b"patched scene", fresh.entries[49])
        self.assertEqual(3, details["written"])

    def test_a_changed_entry_refuses_the_row(self):
        name = self._record()
        moved = FakeIso({8: b"a different font", 49: b"scene"})
        self.assertFalse(row_cache.load(self.store, name).matches(moved))

    def test_a_missing_entry_refuses_the_row(self):
        name = self._record()
        entry = row_cache.load(self.store, name)
        self.assertFalse(entry.matches(FakeIso({49: b"scene"})))

    def test_the_verify_gate_is_remembered(self):
        self.assertFalse(
            row_cache.load(self.store, self._record("b" * 64)).verified)
        self.assertTrue(
            row_cache.load(self.store,
                           self._record("c" * 64, verified=True)).verified)

    def test_grown_bytes_survive_the_round_trip(self):
        name = self._record(details={"written": 1, "grown_sectors": 2,
                                     "patched": b"a much longer entry"})
        details = row_cache.load(self.store, name).replay(
            FakeIso({8: b"font", 49: b"scene"}))
        self.assertEqual(2, details["grown_sectors"])
        self.assertEqual(b"a much longer entry", details["patched"])

    def test_patched_bytes_equal_to_a_write_are_stored_once(self):
        iso = FakeIso({49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.read_entry(49)
        payload = b"x" * 4096
        iso.write_entry(49, payload)
        row_cache.save(self.store, "d" * 64, iso.journal,
                       {"written": 1, "patched": payload})
        entry = row_cache.load(self.store, "d" * 64)
        self.assertEqual(len(payload), len(entry.blob))
        self.assertEqual(payload, entry.replay(FakeIso({49: b"scene"}))
                         ["patched"])

    def test_a_row_from_another_format_is_ignored(self):
        name = self._record("e" * 64)
        with mock.patch.object(row_cache, "FORMAT", row_cache.FORMAT + 1):
            self.assertIsNone(row_cache.load(self.store, name))

    def test_a_truncated_row_is_ignored_rather_than_raised(self):
        name = self._record("f" * 64)
        path = row_cache.path(self.store, name)
        with open(path, "r+b") as handle:
            handle.truncate(4)
        self.assertIsNone(row_cache.load(self.store, name))

    def test_prune_drops_the_least_recently_used_first(self):
        for index, name in enumerate(("1", "2", "3")):
            full = name * 64
            self._record(full)
            os.utime(row_cache.path(self.store, full),
                     ns=(index * 10 ** 9, index * 10 ** 9))
        sizes = [os.path.getsize(row_cache.path(self.store, n * 64))
                 for n in ("1", "2", "3")]
        removed, held = row_cache.prune(self.store, limit=sum(sizes) - 1)
        self.assertEqual(1, removed)
        self.assertIsNone(row_cache.load(self.store, "1" * 64))
        self.assertIsNotNone(row_cache.load(self.store, "3" * 64))
        self.assertLessEqual(held, sum(sizes) - 1)


class KeyTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vp2-rowkey-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.sheet = os.path.join(self.root, "resource-0049-scenes.csv")
        with open(self.sheet, "w", encoding="utf-8", newline="") as handle:
            handle.write("resource,message_id,original_en,translated\r\n"
                         "49,1,Hello,Ola\r\n")

    def _row(self, **changed):
        row = {"kind": "scene", "resource": "49", "sheet": self.sheet,
               "flags": "", "verify": "yes", "subresource": "",
               "chapter_title": "", "chapter_title_message": ""}
        row.update(changed)
        return row

    def test_the_same_sheet_gives_the_same_digest(self):
        self.assertEqual(row_cache.inputs_digest(self._row()),
                         row_cache.inputs_digest(self._row()))

    def test_editing_the_sheet_changes_the_digest(self):
        before = row_cache.inputs_digest(self._row())
        with open(self.sheet, "w", encoding="utf-8", newline="") as handle:
            handle.write("resource,message_id,original_en,translated\r\n"
                         "49,1,Hello,Alo\r\n")
        self.assertNotEqual(before, row_cache.inputs_digest(self._row()))

    def test_a_flag_changes_the_digest(self):
        self.assertNotEqual(row_cache.inputs_digest(self._row()),
                            row_cache.inputs_digest(
                                self._row(flags="keep-undrawn")))

    def test_a_sheet_that_is_not_there_has_no_digest(self):
        self.assertIsNone(
            row_cache.inputs_digest(self._row(sheet=self.sheet + ".gone")))

    def test_editing_the_image_layout_changes_the_digest(self):
        images = os.path.join(self.root, "pack", "images")
        os.makedirs(images)
        with open(os.path.join(images, "fis-1781-a.png"), "wb") as handle:
            handle.write(b"png")
        layout = os.path.join(self.root, "pack", "fis-image-layouts.json")
        row = self._row(kind="image", resource="1781", sheet=images)
        with open(layout, "w", encoding="utf-8") as handle:
            handle.write('{"version": 1, "images": {}}')
        before = row_cache.inputs_digest(row)
        with open(layout, "w", encoding="utf-8") as handle:
            handle.write('{"version": 1, "images": {"a": {}}}')
        self.assertNotEqual(before, row_cache.inputs_digest(row))

    def test_the_store_can_be_turned_off(self):
        with mock.patch.dict(os.environ, {"VP2_ROW_CACHE": "0"}):
            self.assertEqual("", row_cache.resolve_store())
        with mock.patch.dict(os.environ, {"VP2_ROW_CACHE_BYTES": "0"}):
            self.assertEqual("", row_cache.resolve_store())
        with mock.patch.dict(os.environ, {"VP2_ROW_CACHE": self.root}):
            self.assertEqual(self.root, row_cache.resolve_store())


class FingerprintTests(unittest.TestCase):
    """What retires every stored row, and what leaves them alone."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vp2-fingerprint-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.tools = os.path.join(self.root, "tools", "scripts")
        self.data = os.path.join(self.root, "data")
        self.cache = os.path.join(self.root, ".cache")
        for folder in (self.tools, self.data,
                       os.path.join(self.cache, "font-layout")):
            os.makedirs(folder)
        self.writer = os.path.join(self.tools, "scene_text.py")
        self._write(self.writer, "def wrap():\n    return 1\n")
        self.table = os.path.join(self.data, "record-limits.csv")
        self._write(self.table, "resource,extent\n")
        self.iso = os.path.join(self.root, "pristine.iso")
        with open(self.iso, "wb") as handle:
            handle.write(b"\0" * 4096)

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def _mark(self):
        with mock.patch.object(row_cache, "PROJECT_ROOT", self.root), \
                mock.patch.object(row_cache, "DATA_DIR", self.data), \
                mock.patch.object(row_cache, "CACHE_ROOT", self.cache):
            return row_cache.fingerprint(self.iso)

    def test_the_same_tree_gives_the_same_mark(self):
        self.assertEqual(self._mark(), self._mark())

    def test_editing_a_writer_retires_every_row(self):
        before = self._mark()
        self._write(self.writer, "def wrap():\n    return 2\n")
        self.assertNotEqual(before, self._mark())

    def test_editing_a_structural_table_retires_every_row(self):
        before = self._mark()
        self._write(self.table, "resource,extent\n1197,2048\n")
        self.assertNotEqual(before, self._mark())

    def test_a_new_font_layout_retires_every_row(self):
        before = self._mark()
        self._write(os.path.join(self.cache, "font-layout", "aa.json"), "{}")
        self.assertNotEqual(before, self._mark())

    def test_a_different_image_retires_every_row(self):
        before = self._mark()
        os.utime(self.iso, ns=(1, 1))
        self.assertNotEqual(before, self._mark())

    def test_a_file_that_is_not_source_leaves_them_alone(self):
        before = self._mark()
        self._write(os.path.join(self.tools, "notes.txt"), "not source")
        self.assertEqual(before, self._mark())

    def test_the_glyph_pool_is_part_of_the_mark(self):
        pool = os.path.join(self.root, "glyph-pool.csv")
        self._write(pool, "character,digest\n")
        with mock.patch.object(row_cache, "PROJECT_ROOT", self.root), \
                mock.patch.object(row_cache, "DATA_DIR", self.data), \
                mock.patch.object(row_cache, "CACHE_ROOT", self.cache):
            before = row_cache.fingerprint(self.iso, pool)
            self._write(pool, "character,digest\na,00\n")
            self.assertNotEqual(before,
                                row_cache.fingerprint(self.iso, pool))

class RetiredRowTests(unittest.TestCase):
    """A row made under a mark the build no longer uses is dead weight."""

    def setUp(self):
        self.store = tempfile.mkdtemp(prefix="vp2-retired-")
        self.addCleanup(shutil.rmtree, self.store, ignore_errors=True)

    def _record(self, name, mark):
        iso = FakeIso({49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.read_entry(49)
        iso.write_entry(49, b"patched" * 100)
        row_cache.save(self.store, name, iso.journal, {"written": 1},
                       mark=mark)

    def test_a_retired_row_goes_before_a_live_one(self):
        self._record("a" * 64, "old-mark")
        self._record("b" * 64, "new-mark")
        removed, held = row_cache.prune(self.store, limit=1 << 30,
                                        mark="new-mark")
        self.assertEqual(1, removed)
        self.assertIsNone(row_cache.load(self.store, "a" * 64))
        self.assertIsNotNone(row_cache.load(self.store, "b" * 64))
        self.assertLess(held, 1 << 30)

    def test_a_row_with_no_mark_at_all_is_dropped_too(self):
        iso = FakeIso({49: b"scene"})
        iso.journal = row_cache.Journal()
        iso.read_entry(49)
        iso.write_entry(49, b"patched")
        row_cache.save(self.store, "f" * 64, iso.journal, {"written": 1})
        removed, _held = row_cache.prune(self.store, limit=1 << 30,
                                         mark="new-mark")
        self.assertEqual(1, removed)
        self.assertIsNone(row_cache.load(self.store, "f" * 64))

    def test_no_mark_given_leaves_every_row_to_its_age(self):
        self._record("c" * 64, "old-mark")
        self._record("d" * 64, "new-mark")
        removed, _held = row_cache.prune(self.store, limit=1 << 30)
        self.assertEqual(0, removed)

    def test_the_mark_is_kept_with_the_row(self):
        self._record("e" * 64, "a-mark")
        self.assertEqual(
            "a-mark",
            row_cache.load(self.store, "e" * 64).header.get("mark"))


class AuditKeyTests(unittest.TestCase):
    """The pre-flight key asks about coverage, not about the file."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="vp2-audit-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.sheet = os.path.join(self.root, "resource-0049-scenes.csv")
        self._write("resource,message_id,original_en,translated\r\n"
                    "49,1,Hello,Ola\r\n49,2,Bye,Tchau\r\n")

    def _write(self, text):
        with open(self.sheet, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)

    def _row(self):
        return {"kind": "scene", "resource": "49", "sheet": self.sheet}

    def test_rewriting_the_same_sheet_keeps_the_key(self):
        before = row_cache.audit_key("mark", self._row())
        self._write("resource,message_id,original_en,translated\r\n"
                    "49,1,Hello,Ola\r\n49,2,Bye,Tchau\r\n")
        self.assertEqual(before, row_cache.audit_key("mark", self._row()))

    def test_retranslating_a_line_keeps_the_key(self):
        before = row_cache.audit_key("mark", self._row())
        self._write("resource,message_id,original_en,translated\r\n"
                    "49,1,Hello,Alo\r\n49,2,Bye,Ate mais\r\n")
        self.assertEqual(before, row_cache.audit_key("mark", self._row()))

    def test_a_message_the_sheet_stops_covering_changes_the_key(self):
        before = row_cache.audit_key("mark", self._row())
        self._write("resource,message_id,original_en,translated\r\n"
                    "49,1,Hello,Ola\r\n")
        self.assertNotEqual(before, row_cache.audit_key("mark", self._row()))

    def test_a_different_image_changes_the_key(self):
        self.assertNotEqual(row_cache.audit_key("one", self._row()),
                            row_cache.audit_key("two", self._row()))

    def test_a_sheet_that_is_not_there_has_no_key(self):
        row = self._row()
        row["sheet"] += ".gone"
        self.assertIsNone(row_cache.audit_key("mark", row))

    def test_a_mark_is_left_and_found(self):
        name = row_cache.audit_key("mark", self._row())
        self.assertFalse(row_cache.remembered(self.root, name))
        row_cache.remember(self.root, name)
        self.assertTrue(row_cache.remembered(self.root, name))

if __name__ == "__main__":
    unittest.main()
