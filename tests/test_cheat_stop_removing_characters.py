# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import struct
import unittest

from tools.cheat_patcher import elf, main_overlay
from tools.cheat_patcher.cheats.stop_removing_characters import (
    ENTRY_ADDRESS,
    HOOK_ADDRESS,
    HOOK_PATCHED,
    INJECT_ADDRESS,
    INJECTED_CODE,
    patch_executable,
    patch_main_resource,
)
from tests.test_cheat_disable_anti_cheat import (
    make_executable,
    make_main_resource,
)


class StopRemovingCharactersTests(unittest.TestCase):
    def test_hook_changes_only_its_two_instructions(self):
        resource = make_main_resource()
        before = main_overlay.read(resource).output
        details = patch_main_resource(resource)
        after = main_overlay.read(details.data).output
        expected = bytearray(before)
        struct.pack_into(
            "<2I", expected,
            HOOK_ADDRESS - main_overlay.LOAD_ADDRESS,
            *HOOK_PATCHED
        )
        self.assertEqual(bytes(expected), after)
        self.assertEqual(2, details.change_count)
        self.assertEqual(
            ENTRY_ADDRESS,
            (HOOK_PATCHED[0] & 0x03FFFFFF) << 2
        )

    def test_installs_exact_routine_and_preserves_crc(self):
        executable, _ = make_executable()
        details = patch_executable(executable)
        patched = details.data
        mapped = elf.file_offset_for_address(
            patched, INJECT_ADDRESS, len(INJECTED_CODE)
        )
        self.assertEqual(details.file_offset, mapped)
        self.assertEqual(
            INJECTED_CODE, patched[mapped:mapped + len(INJECTED_CODE)]
        )
        self.assertEqual(37, details.change_count)
        self.assertEqual(8, INJECT_ADDRESS % 0x10)
        program_offset = struct.unpack_from("<I", patched, 28)[0]
        entry_size, count = struct.unpack_from("<HH", patched, 42)
        segment = struct.unpack_from(
            "<8I", patched, program_offset + (count - 1) * entry_size
        )
        terminal_file_offset = struct.unpack_from(
            "<8I", executable, 84
        )[1]
        self.assertEqual(terminal_file_offset, segment[1])
        self.assertEqual(elf.CODE_ARENA_ADDRESS, segment[2])
        self.assertEqual(elf.CODE_ARENA_SIZE, segment[4])
        self.assertEqual(elf.CODE_ARENA_SIZE, segment[5])
        self.assertEqual(
            b"\0" * (INJECT_ADDRESS - elf.CODE_ARENA_ADDRESS),
            patched[segment[1]:details.file_offset]
        )
        self.assertEqual(
            0x24010009,
            struct.unpack_from(
                "<I", patched, mapped + ENTRY_ADDRESS - INJECT_ADDRESS
            )[0]
        )
        self.assertEqual(len(executable), len(patched))
        self.assertEqual(0, struct.unpack_from("<I", patched, 32)[0])
        self.assertEqual((0, 0, 0), struct.unpack_from("<HHH", patched, 46))
        self.assertEqual(elf.pcsx2_crc(executable), elf.pcsx2_crc(patched))

    def test_rejects_an_executable_without_the_empty_terminal_segment(self):
        executable, _ = make_executable()
        malformed = bytearray(executable)
        struct.pack_into("<HH", malformed, 42, 32, 1)
        with self.assertRaisesRegex(ValueError, "empty terminal load segments"):
            patch_executable(malformed)

    def test_maps_an_unaligned_payload_at_its_exact_requested_address(self):
        executable, _ = make_executable()
        payload = b"\x11\x22\x33\x44"
        injection = elf.inject_load_segment(
            executable, 0x01FEAFE8, payload
        )
        mapped = elf.file_offset_for_address(
            injection.data, 0x01FEAFE8, len(payload)
        )
        self.assertEqual(injection.file_offset, mapped)
        self.assertEqual(payload, injection.data[mapped:mapped + len(payload)])


if __name__ == "__main__":
    unittest.main()
