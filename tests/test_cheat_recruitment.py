# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import struct
import unittest
from pathlib import Path

from tools.cheat_patcher import elf, resource3_overlay
from tools.cheat_patcher.cheats import (
    add_characters,
    join_all_unlocked,
    join_level_1,
    stop_removing_characters,
)
from tests.test_cheat_disable_anti_cheat import (
    make_executable, make_main_resource, make_resource_3,
)


def _words(data, address, count):
    offset = elf.file_offset_for_address(data, address, count * 4)
    return struct.unpack_from("<%dI" % count, data, offset)


class AddCharactersTests(unittest.TestCase):
    def test_rejects_a_pristine_hook_with_anything_but_a_zero_word(self):
        executable, _ = make_executable()
        offset = elf.file_offset_for_address(
            executable, add_characters.HOOK_ADDRESS, 4
        )
        bad = bytearray(executable)
        struct.pack_into("<I", bad, offset, 0xDEADBEEF)
        with self.assertRaisesRegex(
            ValueError,
            "Add Characters hook validation failed at EE 0x%08X; "
            "expected 0x00000000, found 0xDEADBEEF" % add_characters.HOOK_ADDRESS,
        ):
            add_characters.patch_executable(bytes(bad))

    def test_rejects_a_hook_that_is_already_patched(self):
        executable, _ = make_executable()
        patched = add_characters.patch_executable(executable).data
        with self.assertRaisesRegex(
            ValueError, "Add Characters is already patched in the executable"
        ):
            add_characters.patch_executable(patched)

    def test_installs_the_exact_routine_and_hook_word(self):
        executable, _ = make_executable()
        details = add_characters.patch_executable(executable)
        self.assertEqual(
            add_characters.INJECT_WORDS,
            _words(
                details.data, add_characters.INJECT_ADDRESS,
                len(add_characters.INJECT_WORDS),
            ),
        )
        hook_offset = elf.file_offset_for_address(
            details.data, add_characters.HOOK_ADDRESS, 4
        )
        self.assertEqual(
            add_characters.HOOK_PATCHED,
            struct.unpack_from("<I", details.data, hook_offset)[0],
        )
        self.assertEqual(elf.pcsx2_crc(executable), details.patched_crc)
        self.assertEqual(
            details.change_count, 1 + len(add_characters.INJECT_WORDS)
        )

    def test_hook_jump_resolves_to_the_inject_address(self):
        hook_jump = add_characters.HOOK_PATCHED
        self.assertEqual(
            add_characters.INJECT_ADDRESS,
            (hook_jump & 0x03FFFFFF) << 2,
        )

    def test_return_jump_resolves_past_the_hook(self):
        return_jump = add_characters.INJECT_WORDS[-1]
        self.assertEqual(
            add_characters.RETURN_ADDRESS,
            (return_jump & 0x03FFFFFF) << 2
        )

    def test_inject_words_match_the_pnach_reference_byte_for_byte(self):
        reference = (
            Path(__file__).resolve().parents[1]
            / "tools" / "cheat_patcher" / "cheats" / "references"
            / "SLUS-21452_CC96CE93.pnach"
        )
        words = {}
        in_block = False
        for line in reference.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("["):
                in_block = stripped == "[(!) Add Characters]"
                continue
            if not in_block or not stripped.startswith("patch="):
                continue
            parts = stripped.split(",")
            if parts[0] != "patch=1" or parts[1] != "EE":
                continue
            address = int(parts[2], 16)
            value = int(parts[4], 16)
            words[address] = value
        start = add_characters.INJECT_ADDRESS
        end = start + len(add_characters.INJECT_WORDS) * 4
        for offset in range(start, end, 4):
            self.assertEqual(
                add_characters.INJECT_WORDS[(offset - start) // 4],
                words.get(offset, 0),
                "PNACH byte at EE 0x%08X does not match add_characters "
                "INJECT_WORDS" % offset,
            )


class RecruitmentPatchTests(unittest.TestCase):
    def test_all_unlocked_changes_only_three_hooks_and_installs_exact_code(self):
        resource = make_resource_3()
        before = resource3_overlay.read(resource).output
        details = join_all_unlocked.patch_resource(resource)
        after = resource3_overlay.read(details.data).output
        expected = bytearray(before)
        for address, _, replacement in join_all_unlocked.PATCHES:
            struct.pack_into(
                "<I", expected,
                address - resource3_overlay.LOAD_ADDRESS, replacement
            )
        self.assertEqual(bytes(expected), after)

        executable, _ = make_executable()
        patched = join_all_unlocked.patch_executable(executable).data
        self.assertEqual(
            join_all_unlocked.INJECT_WORDS,
            _words(patched, join_all_unlocked.INJECT_ADDRESS,
                   len(join_all_unlocked.INJECT_WORDS))
        )
        self.assertEqual(elf.pcsx2_crc(executable), elf.pcsx2_crc(patched))

    def test_level_one_is_independent_and_writes_its_add_characters_overrides(self):
        resource = make_resource_3()
        before = resource3_overlay.read(resource).output
        details = join_level_1.patch_resource(resource)
        after = resource3_overlay.read(details.data).output
        expected = bytearray(before)
        address, _, replacement = join_level_1.PATCHES[0]
        struct.pack_into(
            "<I", expected,
            address - resource3_overlay.LOAD_ADDRESS, replacement
        )
        self.assertEqual(bytes(expected), after)

        executable, _ = make_executable()
        add_patched = add_characters.patch_executable(executable).data
        patched = join_level_1.patch_executable(add_patched).data
        self.assertEqual(
            join_level_1.INJECT_WORDS,
            _words(patched, join_level_1.INJECT_ADDRESS,
                   len(join_level_1.INJECT_WORDS))
        )
        for address, _, replacement in join_level_1.ADD_CHARACTERS_LEVEL_OVERRIDES:
            self.assertEqual((replacement,), _words(patched, address, 1))

    def test_all_injected_routines_share_one_exact_address_arena(self):
        executable, _ = make_executable()
        modules = (
            stop_removing_characters,
            join_all_unlocked,
            add_characters,
            join_level_1,
        )
        forward = executable
        for module in modules:
            forward = module.patch_executable(forward).data
        self.assertEqual(len(executable), len(forward))
        self.assertEqual(elf.pcsx2_crc(executable), elf.pcsx2_crc(forward))
        self.assertEqual(
            join_all_unlocked.INJECT_WORDS,
            _words(forward, join_all_unlocked.INJECT_ADDRESS,
                   len(join_all_unlocked.INJECT_WORDS))
        )
        self.assertEqual(
            stop_removing_characters.INJECT_WORDS,
            _words(forward, stop_removing_characters.INJECT_ADDRESS,
                   len(stop_removing_characters.INJECT_WORDS))
        )
        override_addresses = {
            address
            for address, _, _ in join_level_1.ADD_CHARACTERS_LEVEL_OVERRIDES
        }
        patched_words = list(add_characters.INJECT_WORDS)
        for address, _, replacement in (
            join_level_1.ADD_CHARACTERS_LEVEL_OVERRIDES
        ):
            patched_words[(address - add_characters.INJECT_ADDRESS) // 4] = (
                replacement
            )
        self.assertEqual(
            tuple(patched_words),
            _words(forward, add_characters.INJECT_ADDRESS,
                   len(add_characters.INJECT_WORDS))
        )
        self.assertEqual(
            join_level_1.INJECT_WORDS,
            _words(forward, join_level_1.INJECT_ADDRESS,
                   len(join_level_1.INJECT_WORDS))
        )


if __name__ == "__main__":
    unittest.main()
