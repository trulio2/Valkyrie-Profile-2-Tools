# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.test_cheat_disable_anti_cheat import (  # noqa: E402
    install_iso_file, make_attack_resource, make_battle_resource,
    make_equip_resource, make_executable, make_item_resource,
    make_main_resource, make_resource_3, make_save_resource,
    make_sealstone_resource, make_skill_resource, patch_battle_resource,
    write_synthetic_iso,
)
from tools.cheat_patcher import build, deferral, slz3  # noqa: E402


def _image(folder, battle=None):
    source = Path(folder) / "clean.iso"
    save_resource, _, _ = make_save_resource()
    equip, _, _ = make_equip_resource()
    skill, _, _ = make_skill_resource()
    item, _, _ = make_item_resource()
    write_synthetic_iso(source, {
        3: make_resource_3(), 22: make_main_resource(), 644: item,
        645: equip, 646: skill, 647: make_attack_resource(),
        652: save_resource, 866: make_sealstone_resource(866)[0],
        867: make_sealstone_resource(867)[0],
        1781: battle if battle is not None else make_battle_resource()[0],
    })
    install_iso_file(source, "SLUS_214.52", make_executable()[0])
    return source


def _unbatched(source, selected):
    with mock.patch.object(deferral.Session, "begin",
                           lambda self, resource, defer: setattr(
                               self, "resource", None)):
        return build.plan_patches(source, selected)


class DeferralTests(unittest.TestCase):

    def test_batching_writes_what_one_compression_per_cheat_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = _image(folder)
            calls = []
            compress = slz3.compress

            def counted(*args, **kwargs):
                stream = compress(*args, **kwargs)
                if deferral.MARK not in stream:
                    calls.append(len(args[0]))
                return stream

            with mock.patch.object(slz3, "compress", counted):
                batched = build.plan_patches(source, list(build.PATCHERS))
                batched_calls = len(calls)
                calls.clear()
                plain = _unbatched(source, list(build.PATCHERS))
            self.assertEqual(plain.writes, batched.writes)
            self.assertLess(batched_calls, len(calls))

    def test_a_last_cheat_that_changes_nothing_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            battle = patch_battle_resource(make_battle_resource()[0]).data
            source = _image(folder, battle)
            selected = ["battle-menu-always", "disable-anti-cheat"]
            batched = build.plan_patches(source, selected)
            plain = _unbatched(source, selected)
            self.assertEqual(plain.writes, batched.writes)
            self.assertFalse(any(deferral.MARK in data
                                 for _, data in batched.writes))

    def test_a_placeholder_reads_back_and_is_reported_until_replaced(self):
        original = bytes(range(256)) * 64
        edited = bytearray(original)
        edited[100:104] = b"\0\0\0\0"
        session = deferral.Session()
        with session:
            session.begin(1781, True)
            stream = slz3.compress(original)
            session.end()
            self.assertEqual(original, session.output_for(stream + b"\0" * 8))
            self.assertEqual({1781}, session.unfinished())
            session.begin(1781, False)
            slz3.compress(bytes(edited))
            session.end()
            self.assertEqual(set(), session.unfinished())

    def test_two_streams_mistaken_for_one_are_reported(self):
        with deferral.Session() as session:
            session.begin(3, True)
            slz3.compress(bytes(4096 * 8))
            session.end()
            session.begin(3, False)
            slz3.compress(bytes(range(256)) * 128)
            session.end()
            self.assertEqual({3}, session.unfinished())


if __name__ == "__main__":
    unittest.main()
