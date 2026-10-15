# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_cheat_disable_anti_cheat import (  # noqa: E402
    install_iso_file, make_battle_resource, make_executable, make_main_resource,
    make_resource_3, make_save_resource, write_synthetic_iso,
)
from tools.cheat_patcher import build, catalog, web  # noqa: E402


class WebPlanTests(unittest.TestCase):

    def test_a_plan_read_in_pieces_matches_one_read_from_the_file(self):
        save_resource, _, _ = make_save_resource()
        battle_resource, _, _ = make_battle_resource()
        executable, _ = make_executable()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "clean.iso"
            write_synthetic_iso(source, {
                3: make_resource_3(), 22: make_main_resource(),
                652: save_resource, 1781: battle_resource,
            })
            install_iso_file(source, "SLUS_214.52", executable)
            image = source.read_bytes()
            reads = []

            def read_at(offset, size):
                reads.append(size)
                return image[offset:offset + size]

            writes, names = web.plan(read_at, len(image),
                                     ["stop-removing-characters"], False)
            expected = build.plan_patches(
                source, catalog.required_with(["stop-removing-characters"]),
                corrupt_save=False)

            self.assertEqual(list(expected.writes), writes)
            self.assertEqual(["disable-anti-cheat", "stop-removing-characters"],
                             names)
            self.assertLess(sum(reads), len(image))

    def test_random_access_reads_seeks_and_stops_at_the_end(self):
        data = bytes(range(256)) * 4
        handle = web.RandomAccess(lambda at, n: data[at:at + n], len(data))
        handle.seek(1000)
        self.assertEqual(data[1000:], handle.read())
        self.assertEqual(b"", handle.read(5))
        handle.seek(-4, 2)
        self.assertEqual(data[-4:], handle.read(10))


class BundleTests(unittest.TestCase):

    def test_the_bundle_carries_every_module_and_the_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle, count = web.write_bundle(temporary)
            names = zipfile.ZipFile(bundle).namelist()
            self.assertEqual(count, len(names))
            for module in ("tools/cheat_patcher/web.py",
                           "tools/cheat_patcher/build.py",
                           "tools/scripts/anti_cheat.py"):
                self.assertIn(module, names)
            self.assertFalse(any("gui" in name for name in names))
            listed = json.loads((Path(temporary) / web.CATALOG).read_text(
                encoding="utf-8"))
            self.assertEqual([cheat.name for cheat in catalog.CHEATS],
                             [entry["name"] for entry in listed["cheats"]])


if __name__ == "__main__":
    unittest.main()
