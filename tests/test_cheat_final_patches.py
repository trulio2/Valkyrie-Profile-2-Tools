# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Exact-address tests for the final four PNACH ports."""

import struct
import unittest

from tools.cheat_patcher import (
    battle_overlay, direct_overlay, elf, menu_overlay, resource3_overlay
)
from tools.cheat_patcher.cheats import (
    all_items_99,
    heavenly_punishment_15_ap,
    no_limit_sealstone_withdrawals,
    restore_all_sealstones,
)
from tests.test_cheat_disable_anti_cheat import (
    make_battle_resource,
    make_executable,
    make_item_resource,
    make_resource_3,
    make_sealstone_resource,
)


def assert_word_changes(test, before, after, base, patches):
    expected = bytearray(before)
    for address, _original, replacement in patches:
        struct.pack_into("<I", expected, address - base, replacement)
    test.assertEqual(bytes(expected), after)


def words_at(data, address, count):
    offset = elf.file_offset_for_address(data, address, count * 4)
    return struct.unpack_from("<%dI" % count, data, offset)


class HeavenlyPunishmentTests(unittest.TestCase):
    def test_both_overlay_hooks_change_only_their_two_words(self):
        resource = make_resource_3()
        old = resource3_overlay.read(resource).output
        details = heavenly_punishment_15_ap.patch_resource3(resource)
        new = resource3_overlay.read(details.data).output
        assert_word_changes(
            self, old, new, resource3_overlay.LOAD_ADDRESS,
            heavenly_punishment_15_ap.RESOURCE3_PATCHES,
        )

        resource, marker, marker_at = make_battle_resource()
        old = battle_overlay.read(resource).output
        details = heavenly_punishment_15_ap.patch_battle(resource)
        new = battle_overlay.read(details.data).output
        assert_word_changes(
            self, old, new, 0x0036D900,
            heavenly_punishment_15_ap.BATTLE_PATCHES,
        )
        self.assertEqual(
            marker, details.data[marker_at:marker_at + len(marker)]
        )

    def test_installs_the_exact_pnach_routine(self):
        executable, _ = make_executable()
        details = heavenly_punishment_15_ap.patch_executable(executable)
        self.assertEqual(
            heavenly_punishment_15_ap.INJECT_WORDS,
            words_at(details.data, heavenly_punishment_15_ap.INJECT_ADDRESS,
                     len(heavenly_punishment_15_ap.INJECT_WORDS)),
        )
        self.assertEqual(elf.pcsx2_crc(executable), details.patched_crc)


class SealstonePatchTests(unittest.TestCase):
    def test_both_identical_owners_compose_and_preserve_resource_866_suffix(self):
        for number, restore, withdrawal in (
            (866, restore_all_sealstones.patch_resource_866,
             no_limit_sealstone_withdrawals.patch_resource_866),
            (867, restore_all_sealstones.patch_resource_867,
             no_limit_sealstone_withdrawals.patch_resource_867),
        ):
            with self.subTest(resource=number):
                resource, suffix, span = make_sealstone_resource(number)
                old = direct_overlay.read(
                    resource, number, "Sealstone overlay", 3, span
                ).output
                restored = restore(resource).data
                final = withdrawal(restored).data
                new = direct_overlay.read(
                    final, number, "Sealstone overlay", 3, span
                ).output
                expected = bytearray(old)
                for patch in (restore_all_sealstones.PATCHES +
                              no_limit_sealstone_withdrawals.PATCHES):
                    address, _original, replacement = patch
                    struct.pack_into(
                        "<I", expected, address - 0x00495500, replacement
                    )
                self.assertEqual(bytes(expected), new)
                self.assertEqual(suffix, final[span:span + len(suffix)])
                self.assertEqual(
                    span if number == 866 else 0,
                    struct.unpack_from("<I", final, 0x0C)[0],
                )

    def test_restore_installs_exact_highest_address_routine(self):
        executable, _ = make_executable()
        details = restore_all_sealstones.patch_executable(executable)
        self.assertEqual(
            restore_all_sealstones.INJECT_WORDS,
            words_at(details.data, restore_all_sealstones.INJECT_ADDRESS,
                     len(restore_all_sealstones.INJECT_WORDS)),
        )
        self.assertEqual(0x01FEDE00,
                         elf.CODE_ARENA_ADDRESS + elf.CODE_ARENA_SIZE)
        self.assertEqual(len(executable), len(details.data))
        self.assertEqual(elf.pcsx2_crc(executable), details.patched_crc)

    def test_withdrawal_guard_rejects_only_a_matching_low_half(self):
        resource, _, span = make_sealstone_resource(866)
        overlay = direct_overlay.read(
            resource, 866, "Sealstone overlay", 3, span
        )
        output = bytearray(overlay.output)
        struct.pack_into("<I", output, 0x0049E094 - 0x00495500, 0xDEAD0002)
        changed, _ = direct_overlay.replace(
            resource, output, 866, "Sealstone overlay", 3, span
        )
        with self.assertRaisesRegex(ValueError, "expected 0x92030002"):
            no_limit_sealstone_withdrawals.patch_resource_866(changed)


class AllItemsTests(unittest.TestCase):
    def test_changes_only_the_camp_item_hook(self):
        resource, suffix, span = make_item_resource()
        old = menu_overlay.read(
            resource, all_items_99.RESOURCE, all_items_99.OWNER,
            all_items_99.MODE
        ).output
        details = all_items_99.patch_resource(resource)
        new = menu_overlay.read(
            details.data, all_items_99.RESOURCE, all_items_99.OWNER,
            all_items_99.MODE
        ).output
        assert_word_changes(
            self, old, new, 0x00495500, all_items_99.PATCHES
        )
        self.assertEqual(suffix, details.data[span:span + len(suffix)])

    def test_installs_each_pnach_block_without_filling_its_gaps(self):
        executable, _ = make_executable()
        details = all_items_99.patch_executable(executable)
        for write in all_items_99.INJECT_WRITES:
            self.assertEqual(
                write.words,
                words_at(details.data, write.address, len(write.words)),
            )
        self.assertEqual((0, 0), words_at(details.data, 0x01FEBCF8, 2))
        self.assertEqual(
            (0,) * 13, words_at(details.data, 0x01FEBD1C, 13)
        )
        self.assertEqual(elf.pcsx2_crc(executable), details.patched_crc)


if __name__ == "__main__":
    unittest.main()
