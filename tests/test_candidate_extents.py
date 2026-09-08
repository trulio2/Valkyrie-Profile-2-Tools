# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""A build may record an untested ceiling; it may never call it verified."""

import codecs
import csv
import io
import os
import tempfile
import unittest

from tools.scripts import vp2_container_text as container_text

FIELDS = ["resource", "scope", "max_extent", "kind", "evidence"]


def write(path, rows, bom=b""):
    with io.open(path, "wb") as handle:
        handle.write(bom)
    with io.open(path, "a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS,
                                lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)


def read(path):
    with io.open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


class CandidateExtentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "record-limits.csv")

    def test_a_resource_with_no_row_gains_a_candidate(self):
        write(self.path, [])
        self.assertTrue(container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path))
        rows = read(self.path)
        self.assertEqual(1, len(rows))
        self.assertEqual("4096", rows[0]["max_extent"])
        self.assertEqual("candidate", rows[0]["kind"])

    def test_a_verified_row_is_never_overwritten(self):
        """Only a person writes that word, so a build may not argue with it."""
        write(self.path, [{"resource": "53", "scope": "scene-content",
                           "max_extent": "1000", "kind": "verified",
                           "evidence": "played"}])
        self.assertFalse(container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path))
        rows = read(self.path)
        self.assertEqual("1000", rows[0]["max_extent"])
        self.assertEqual("verified", rows[0]["kind"])

    def test_an_existing_candidate_is_raised_not_duplicated(self):
        write(self.path, [{"resource": "53", "scope": "scene-content",
                           "max_extent": "2000", "kind": "candidate",
                           "evidence": "an earlier build"}])
        self.assertTrue(container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path))
        rows = read(self.path)
        self.assertEqual(1, len(rows))
        self.assertEqual("4096", rows[0]["max_extent"])

    def test_a_lower_extent_changes_nothing(self):
        write(self.path, [{"resource": "53", "scope": "scene-content",
                           "max_extent": "4096", "kind": "candidate",
                           "evidence": "an earlier build"}])
        self.assertFalse(container_text.record_candidate_extent(
            53, "scene-content", 100, path=self.path))
        self.assertEqual("4096", read(self.path)[0]["max_extent"])

    def test_scope_separates_two_measurements_of_one_resource(self):
        write(self.path, [{"resource": "53", "scope": "container",
                           "max_extent": "900", "kind": "verified",
                           "evidence": "played"}])
        self.assertTrue(container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path))
        rows = {row["scope"]: row for row in read(self.path)}
        self.assertEqual("900", rows["container"]["max_extent"])
        self.assertEqual("4096", rows["scene-content"]["max_extent"])

    def test_the_word_verified_is_never_written(self):
        write(self.path, [])
        for resource in (53, 61, 97):
            container_text.record_candidate_extent(
                resource, "scene-content", 4096, path=self.path)
        for row in read(self.path):
            self.assertEqual("candidate", row["kind"])

    def test_a_recorded_candidate_then_builds_rather_than_refusing(self):
        write(self.path, [])
        container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path)
        limits = container_text.load_record_limits(self.path,
                                                   scope="scene-content")
        container_text.check_scene_content_extent(
            53, 4096, pristine_allocation=1000, limits=limits)


class TableFidelityTests(unittest.TestCase):
    """The whole table is rewritten, so it comes back as it went in."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "record-limits.csv")
        self.existing = [{"resource": "61", "scope": "scene-content",
                          "max_extent": "526360", "kind": "verified",
                          "evidence": "played"}]

    def test_a_table_with_a_byte_order_mark_keeps_it(self):
        write(self.path, self.existing, bom=codecs.BOM_UTF8)
        container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path)
        with io.open(self.path, "rb") as handle:
            self.assertEqual(codecs.BOM_UTF8, handle.read(3))

    def test_a_table_without_one_does_not_gain_one(self):
        write(self.path, self.existing)
        container_text.record_candidate_extent(
            53, "scene-content", 4096, path=self.path)
        with io.open(self.path, "rb") as handle:
            self.assertNotEqual(codecs.BOM_UTF8, handle.read(3))

    def test_the_rows_already_there_survive_either_way(self):
        for bom in (codecs.BOM_UTF8, b""):
            with self.subTest(bom=bool(bom)):
                write(self.path, self.existing, bom=bom)
                container_text.record_candidate_extent(
                    53, "scene-content", 4096, path=self.path)
                rows = {row["resource"]: row for row in read(self.path)}
                self.assertEqual("526360", rows["61"]["max_extent"])
                self.assertEqual("verified", rows["61"]["kind"])
                self.assertEqual("4096", rows["53"]["max_extent"])


if __name__ == "__main__":
    unittest.main()
