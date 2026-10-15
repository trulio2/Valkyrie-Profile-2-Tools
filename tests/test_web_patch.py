# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import gzip
import hashlib
import json
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import web_patch  # noqa: E402
from tools.web_patch import SECTOR  # noqa: E402


def _image(sectors, seed=1):
    return random.Random(seed).randbytes(sectors * SECTOR)


class DiffTests(unittest.TestCase):

    def _roundtrip(self, source, target):
        ops, data = web_patch.diff(source, target)
        self.assertEqual(web_patch.apply(source, data, ops), target)
        return ops, data

    def test_identical_image_is_one_copy(self):
        source = _image(700)
        ops, data = self._roundtrip(source, source)
        self.assertEqual(ops, [["s", 0, len(source)]])
        self.assertEqual(data, b"")

    def test_an_edit_inside_a_sector_stores_only_the_edit(self):
        source = _image(40)
        target = bytearray(source)
        target[5 * SECTOR + 300:5 * SECTOR + 310] = b"translated"
        ops, data = self._roundtrip(source, bytes(target))
        self.assertLessEqual(len(data), web_patch.CHUNK * 2)

    def test_a_moved_resource_is_copied_from_where_it_was(self):
        source = _image(64)
        moved = source[10 * SECTOR:20 * SECTOR]
        target = source[:10 * SECTOR] + bytes(10 * SECTOR) + source[20 * SECTOR:] + moved
        ops, data = self._roundtrip(source, target)
        self.assertIn(["s", 10 * SECTOR, 10 * SECTOR], ops)
        self.assertLessEqual(len(data), SECTOR)

    def test_bytes_behind_a_small_insertion_are_copied_shifted(self):
        source = _image(64)
        target = source[:9 * SECTOR + 100] + _image(1, seed=4)[:300] + source[9 * SECTOR + 100:]
        ops, data = self._roundtrip(source, target)
        self.assertLess(len(data), 300 + 4 * web_patch.CHUNK)

    def test_new_bytes_and_a_ragged_end_survive(self):
        source = _image(32)
        fresh = _image(3, seed=2)
        target = source[:8 * SECTOR] + fresh + source[8 * SECTOR:] + b"tail"
        ops, data = self._roundtrip(source, target)
        self.assertGreaterEqual(len(data), len(fresh))

    def test_a_repeated_new_sector_is_stored_once(self):
        source = _image(16)
        fresh = _image(1, seed=3)
        target = source + fresh + source[:SECTOR] + fresh
        _, data = self._roundtrip(source, target)
        self.assertEqual(len(data), SECTOR)

    def test_a_target_shorter_than_the_source(self):
        source = _image(20)
        self._roundtrip(source, source[:7 * SECTOR + 11])


class MakePatchTests(unittest.TestCase):

    def test_option_sets_cover_every_combination(self):
        self.assertEqual(web_patch.option_sets(["corrupt-save", "glyph-draw"]), [
            {"glyph-draw": True, "corrupt-save": True},
            {"glyph-draw": True, "corrupt-save": False},
            {"glyph-draw": False, "corrupt-save": True},
            {"glyph-draw": False, "corrupt-save": False},
        ])
        self.assertEqual(web_patch.option_sets([]), [{}])

    def test_each_source_carries_a_variant_per_option_set(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clean = _image(48)
            undub = clean[:20 * SECTOR] + _image(6, seed=5) + clean[20 * SECTOR:]
            new = _image(4, seed=9)
            glyphs = _image(1, seed=11)
            (root / "clean.iso").write_bytes(clean)
            (root / "undub.iso").write_bytes(undub)
            built = {}

            def build(path, options):
                source = Path(path).read_bytes()
                target = source[:SECTOR] + new + source[SECTOR:]
                if options["glyph-draw"]:
                    target += glyphs
                out = root / "built.iso"
                out.write_bytes(target)
                built[(Path(path).name, options["glyph-draw"])] = target
                return out

            patches = root / "web"
            manifest = web_patch.make_patch(
                [("Clean", root / "clean.iso"), ("Undub", root / "undub.iso")],
                patches / "xx-YY", "xx-YY", build=build, offered=["glyph-draw"],
                label="Test", version="1.0", log=lambda *_: None)
            web_patch.write_index(patches)

            self.assertEqual(manifest["options"],
                             [{"id": "glyph-draw", "default": True}])
            written = json.loads((patches / "xx-YY" / "manifest.json").read_text())
            self.assertEqual(written, manifest)
            for number, (source, image) in enumerate(
                    zip(manifest["sources"], (clean, undub))):
                self.assertEqual(source["sha256"], hashlib.sha256(image).hexdigest())
                data = b"".join(
                    gzip.decompress((patches / "xx-YY" / part["file"]).read_bytes())
                    for part in source["parts"])
                self.assertTrue(all(part["file"].startswith(f"s{number}-")
                                    for part in source["parts"]))
                self.assertEqual(len(data), source["dataSize"])
                # the new bytes both variants share are stored once
                self.assertLess(len(data), len(new) + len(glyphs) + SECTOR)
                for variant in source["variants"]:
                    target = built[(("clean.iso", "undub.iso")[number],
                                    variant["options"]["glyph-draw"])]
                    self.assertEqual(variant["target"]["sha256"],
                                     hashlib.sha256(target).hexdigest())
                    self.assertEqual(web_patch.apply(image, data, variant["ops"]),
                                     target)

            index = json.loads((patches / "index.json").read_text())
            self.assertEqual(index["patches"][0]["path"], "xx-YY/manifest.json")
            self.assertEqual(index["patches"][0]["tool"], web_patch.TRANSLATION)
            self.assertEqual(index["patches"][0]["outputSize"],
                             len(undub) + len(new) + len(glyphs))
            self.assertTrue(web_patch.write_release_zip(patches, "xx-YY").is_file())


class ToolTests(unittest.TestCase):

    def test_a_tool_patch_is_listed_under_its_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            clean = _image(8)
            (root / "clean.iso").write_bytes(clean)

            def build(path, options):
                (root / "built.iso").write_bytes(clean + bytes(SECTOR))
                return root / "built.iso"

            manifest = web_patch.make_patch(
                [("USA", root / "clean.iso")], root / "web" / web_patch.GLYPHS,
                "", build=build, offered=["corrupt-save"], tool=web_patch.GLYPHS,
                log=lambda *_: None)
            self.assertEqual(manifest["tool"], web_patch.GLYPHS)
            self.assertEqual([v["options"] for v in manifest["sources"][0]["variants"]],
                             [{"corrupt-save": True}, {"corrupt-save": False}])
            entry, = web_patch.write_index(root / "web")
            self.assertEqual((entry["tool"], entry["path"]),
                             (web_patch.GLYPHS, "glyphs/manifest.json"))
            self.assertEqual(web_patch.write_release_zip(root / "web", web_patch.GLYPHS).name,
                             "web-patch-glyphs.zip")


if __name__ == "__main__":
    unittest.main()
