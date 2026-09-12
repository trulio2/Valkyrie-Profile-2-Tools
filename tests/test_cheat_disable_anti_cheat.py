# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import struct
import tempfile
import unittest

from tools.cheat_patcher import (
    battle_overlay, direct_overlay, elf, iso9660, menu_overlay, sle, slz3,
    slz12, triace
)
from tools.cheat_patcher.cheats.all_items_99 import (
    GUARDS as ALL_ITEMS_GUARDS,
    MODE as ITEM_MODE,
    OWNER as ITEM_OWNER,
    PATCHES as ALL_ITEMS_PATCHES,
)
from tools.cheat_patcher.cheats.angel_slayer import (
    ORIGINAL_WORD as ANGEL_ORIGINAL,
    PATCHED_WORD as ANGEL_PATCHED,
    TARGET_ADDRESS as ANGEL_ADDRESS,
)
from tools.cheat_patcher.cheats.battle_anti_freeze import (
    ORIGINAL_INSTRUCTION as ANTI_FREEZE_ORIGINAL,
    PATCHED_INSTRUCTION as ANTI_FREEZE_PATCHED,
    TARGET_ADDRESS as ANTI_FREEZE_ADDRESS,
)
from tools.cheat_patcher.cheats.battle_menu_always import (
    GUARD_ADDRESS as BATTLE_MENU_GUARD,
    GUARD_INSTRUCTION as BATTLE_MENU_GUARD_WORD,
    PATCHES as BATTLE_MENU_PATCHES,
)
from tools.cheat_patcher.cheats.character_limit_36 import (
    PATCHES as CHARACTER_LIMIT_PATCHES,
)
from tools.cheat_patcher.cheats.drop_rate_100 import (
    PATCHES as DROP_RATE_PATCHES,
)
from tools.cheat_patcher.cheats.dupe_attacks import (
    MODE as ATTACK_MODE,
    OWNER as ATTACK_OWNER,
    PATCHES as DUPE_ATTACK_PATCHES,
    RESOURCE as ATTACK_RESOURCE,
)
from tools.cheat_patcher.cheats.ether_set_effects import (
    BYTE_PATCHES as ETHER_BYTE_PATCHES,
)
from tools.cheat_patcher.cheats.infinite_ap_attacks import (
    GUARDS as INFINITE_AP_GUARDS,
    PATCHES as INFINITE_AP_PATCHES,
)
from tools.cheat_patcher.cheats.heavenly_punishment_15_ap import (
    BATTLE_PATCHES as HEAVENLY_BATTLE_PATCHES,
    RESOURCE3_PATCHES as HEAVENLY_RESOURCE3_PATCHES,
)
from tools.cheat_patcher.cheats.hold_circle_float import (
    GUARDS as FLOAT_GUARDS,
    HOOK_PATCHES as FLOAT_HOOK_PATCHES,
    INJECT_ADDRESS as FLOAT_INJECT_ADDRESS,
    INJECT_WORDS as FLOAT_INJECT_WORDS,
)
from tools.cheat_patcher.cheats.negate_encounters import (
    PATCHES as NEGATE_ENCOUNTER_PATCHES,
)
from tools.cheat_patcher.cheats.no_limit_sealstone_withdrawals import (
    PATCHES as WITHDRAWAL_PATCHES,
)
from tools.cheat_patcher.cheats.restore_all_sealstones import (
    PATCHES as RESTORE_SEALSTONE_PATCHES,
)
from tools.cheat_patcher.build import build_iso
from tools.cheat_patcher.cheats.disable_anti_cheat import (
    ALL_BATTLE_PATCHES,
    CRC_COMPENSATION_OFFSET,
    EXECUTABLE_ADDRESS,
    EXECUTABLE_ORIGINAL,
    EXECUTABLE_PATCHED,
    MAIN_PATCHES,
    MEMORY_CARD_PATCHES,
    SAVE_PATCHES,
    patch_battle_resource,
    patch_executable,
    patch_main_resource,
    patch_memory_card_resource,
    patch_save_resource,
)
from tools.cheat_patcher.cheats.stop_removing_characters import (
    HOOK_ADDRESS,
    HOOK_ORIGINALS,
    HOOK_PATCHED,
    INJECT_ADDRESS,
    INJECTED_CODE,
)
from tools.cheat_patcher.cheats.skill_points_99 import (
    LABEL as SKILL_LABEL,
    MODE as SKILL_MODE,
    PATCHES as SKILL_PATCHES,
    TARGET_OFFSETS as SKILL_TARGET_OFFSETS,
)
from tools.cheat_patcher.cheats.join_all_unlocked import (
    PATCHES as ALL_UNLOCKED_PATCHES,
)
from tools.cheat_patcher.cheats.join_level_1 import (
    PATCHES as LEVEL_1_PATCHES,
)
from tools.cheat_patcher.cheats import add_characters
from tools.cheat_patcher.cheats.add_characters import (
    HOOK_ADDRESS as ADD_CHARACTERS_HOOK_ADDRESS,
    HOOK_ORIGINAL as ADD_CHARACTERS_HOOK_ORIGINAL,
)
from tests.test_cheat_angel_slayer import write_synthetic_iso
from tests.test_cheat_equip_everything import make_equip_resource
from tests.test_cheat_skill_points_99 import make_skill_resource


def _plant(output, base, patches):
    for patch in patches:
        struct.pack_into("<I", output, patch.address - base, patch.original)


def make_resource_3():
    first_output = bytearray(0x19A00)
    struct.pack_into("<I", first_output, 8, 0x0035EC80)
    _plant(first_output, 0x0035EC80, MEMORY_CARD_PATCHES)
    first_unlinked = sle.conceal(slz12.compress(first_output, 1))
    first = sle.conceal(
        slz12.compress(first_output, 1, next_offset=len(first_unlinked))
    )

    base = 0x001E7E00
    highest = max(ANGEL_ADDRESS,
                  max(address for address, _, _ in HEAVENLY_RESOURCE3_PATCHES),
                  max(address for address, _, _ in ETHER_BYTE_PATCHES))
    second_output = bytearray(highest - base + 0x100)
    struct.pack_into("<I", second_output, 8, base)
    struct.pack_into("<I", second_output, ANGEL_ADDRESS - base, ANGEL_ORIGINAL)
    for address, original, _ in ALL_UNLOCKED_PATCHES + LEVEL_1_PATCHES:
        struct.pack_into("<I", second_output, address - base, original)
    for address, original, _ in HEAVENLY_RESOURCE3_PATCHES:
        struct.pack_into("<I", second_output, address - base, original)
    # This Ether record has unrelated flags in the other three bytes.
    struct.pack_into("<I", second_output, 0x003170F4 - base, 0x00001400)
    second = sle.conceal(slz3.compress(bytes(second_output)))
    return first + second + b"\0" * 0x800


def make_main_resource():
    output = bytearray(0xC4D00)
    struct.pack_into("<I", output, 8, 0x0035EC80)
    _plant(output, 0x0035EC80, MAIN_PATCHES)
    struct.pack_into(
        "<2I", output, HOOK_ADDRESS - 0x0035EC80, *HOOK_ORIGINALS
    )
    for address, original, _ in (CHARACTER_LIMIT_PATCHES +
                                 NEGATE_ENCOUNTER_PATCHES +
                                 FLOAT_HOOK_PATCHES):
        struct.pack_into("<I", output, address - 0x0035EC80, original)
    for address, expected in FLOAT_GUARDS:
        struct.pack_into("<I", output, address - 0x0035EC80, expected)
    return sle.conceal(slz12.compress(output, 2)) + b"\0" * 0x800


def make_save_resource():
    output = bytearray(0x6D00)
    struct.pack_into("<I", output, 8, 0x00495500)
    _plant(output, 0x00495500, SAVE_PATCHES)
    encoded = sle.conceal(slz12.compress(output, 1))
    inner_size = (len(encoded) + 3) & ~3
    span = (0x10 + inner_size + 0x7F) & ~0x7F
    suffix = b"SAVE-SUFFIX" + b"\xA5" * 32
    resource = bytearray(span + len(suffix) + 0x100)
    struct.pack_into("<4sIII", resource, 0, b"ZLS\0", inner_size, 0, span)
    resource[0x10:0x10 + len(encoded)] = encoded
    resource[span:span + len(suffix)] = suffix
    return bytes(resource), suffix, span


def make_attack_resource():
    base = 0x00495500
    highest = max(
        max(address for address, _, _ in DUPE_ATTACK_PATCHES),
        0x00499AF4,
    )
    output = bytearray(highest - base + 0x100)
    struct.pack_into("<I", output, 8, base)
    for address, original, _ in DUPE_ATTACK_PATCHES:
        struct.pack_into("<I", output, address - base, original)
    struct.pack_into("<I", output, 0x00499AF4 - base, 0x1460FFD8)
    encoded = sle.conceal(slz12.compress(output, ATTACK_MODE))
    inner_size = (len(encoded) + 3) & ~3
    span = (0x10 + inner_size + 0x7F) & ~0x7F
    resource = bytearray(span + 0x100)
    struct.pack_into("<4sIII", resource, 0, b"ZLS\0", inner_size, 0, span)
    resource[0x10:0x10 + len(encoded)] = encoded
    return bytes(resource)


def make_item_resource():
    base = 0x00495500
    highest = max(
        max(address for address, _, _ in ALL_ITEMS_PATCHES),
        max(address for address, _ in ALL_ITEMS_GUARDS),
    )
    output = bytearray(highest - base + 0x100)
    struct.pack_into("<I", output, 8, base)
    for address, original, _ in ALL_ITEMS_PATCHES:
        struct.pack_into("<I", output, address - base, original)
    encoded = sle.conceal(slz3.compress(bytes(output)))
    inner_size = (len(encoded) + 3) & ~3
    span = (0x10 + inner_size + 0x7F) & ~0x7F
    suffix = b"ITEM-SUFFIX" + b"\xA5" * 32
    resource = bytearray(span + len(suffix) + 0x100)
    struct.pack_into("<4sIII", resource, 0, b"ZLS\0", inner_size, 0, span)
    resource[0x10:0x10 + len(encoded)] = encoded
    resource[span:span + len(suffix)] = suffix
    return bytes(resource), suffix, span


def make_sealstone_resource(resource_number):
    base = 0x00495500
    fixed_span = 0x8910
    output = bytearray(0xFC00)
    struct.pack_into("<I", output, 8, base)
    for address, original, _ in (RESTORE_SEALSTONE_PATCHES +
                                 WITHDRAWAL_PATCHES):
        struct.pack_into("<I", output, address - base, original)
    encoded = slz3.compress(
        bytes(output), next_offset=(fixed_span if resource_number == 866 else 0)
    )
    if len(encoded) > fixed_span:
        raise AssertionError("synthetic sealstone overlay exceeds its span")
    suffix = (b"SEALSTONE-INLINE-BANK" + b"\xA5" * 32
              if resource_number == 866 else b"")
    resource = bytearray(fixed_span + len(suffix) + 0x100)
    resource[:len(encoded)] = encoded
    resource[fixed_span:fixed_span + len(suffix)] = suffix
    return bytes(resource), suffix, fixed_span


def make_battle_resource():
    base = 0x0036D900
    highest = max(
        max(patch.address for patch in ALL_BATTLE_PATCHES),
        max(address for address, _, _ in BATTLE_MENU_PATCHES),
        max(address for address, _, _ in INFINITE_AP_PATCHES),
        max(address for address, _, _ in DROP_RATE_PATCHES),
        max(address for address, _, _ in HEAVENLY_BATTLE_PATCHES),
    )
    output = bytearray((highest - base + 0x104) & ~1)
    struct.pack_into("<I", output, 8, base)
    struct.pack_into(
        "<I", output, ANTI_FREEZE_ADDRESS - base, ANTI_FREEZE_ORIGINAL
    )
    struct.pack_into("<I", output, 0x00397CAC - base, 0x0262102A)
    _plant(output, base, ALL_BATTLE_PATCHES)
    struct.pack_into(
        "<I", output, BATTLE_MENU_GUARD - base, BATTLE_MENU_GUARD_WORD
    )
    for address, original, _ in BATTLE_MENU_PATCHES:
        struct.pack_into("<I", output, address - base, original)
    for address, expected in INFINITE_AP_GUARDS:
        struct.pack_into("<I", output, address - base, expected)
    for address, original, _ in INFINITE_AP_PATCHES + DROP_RATE_PATCHES:
        struct.pack_into("<I", output, address - base, original)
    for address, original, _ in HEAVENLY_BATTLE_PATCHES:
        struct.pack_into("<I", output, address - base, original)
    stream = slz3.compress(bytes(output))

    count = 2
    first = 8 + (count + 1) * 8
    second = (first + len(stream) + 0x3F) & ~0x3F
    marker = b"NEXT-PACKAGE-ITEM" + b"\xA5" * 32
    end = (second + len(marker) + 0x3F) & ~0x3F
    resource = bytearray(end + 0x200)
    struct.pack_into("<4sHH", resource, 0, b"p@Ck", 1, count)
    struct.pack_into("<II", resource, 8, first, 0x6800)
    struct.pack_into("<II", resource, 16, second, 0x6A00)
    struct.pack_into("<II", resource, 24, end, 0)
    resource[first:first + len(stream)] = stream
    resource[second:second + len(marker)] = marker
    for index, value in enumerate(battle_overlay.HEADER_XOR):
        resource[index] ^= value
    return bytes(resource), marker, second


def make_executable():
    data = bytearray(0x24608)
    data[:6] = b"\x7fELF\x01\x01"
    struct.pack_into("<I", data, 28, 52)
    struct.pack_into("<I", data, 32, 0x20800)
    struct.pack_into("<HH", data, 42, 32, 2)
    struct.pack_into("<HHH", data, 46, 40, 397, 1)
    file_offset = 0x1000
    virtual_address = 0x00100000
    file_size = 0x1F628
    struct.pack_into(
        "<8I", data, 52, 1, file_offset, virtual_address, 0,
        file_size, file_size, 5, 0x1000
    )
    struct.pack_into(
        "<8I", data, 84, 1, 0x20800, 0x00200000, 0x00200000,
        0, 0, 6, 0x10
    )
    target = file_offset + EXECUTABLE_ADDRESS - virtual_address
    struct.pack_into("<I", data, target, EXECUTABLE_ORIGINAL)
    add_hook_target = file_offset + ADD_CHARACTERS_HOOK_ADDRESS - virtual_address
    struct.pack_into(
        "<I", data, add_hook_target, ADD_CHARACTERS_HOOK_ORIGINAL
    )
    return bytes(data), target


def install_iso_file(path, name, data):
    image = bytearray(path.read_bytes())
    root_sector = 0x100
    file_sector = 0x120
    root_size = 0x800
    allocation = (len(data) + 0x7FF) & ~0x7FF
    end = file_sector * 0x800 + allocation
    if len(image) < end:
        image.extend(b"\0" * (end - len(image)))

    descriptor = bytearray(0x800)
    descriptor[0:7] = b"\x01CD001\x01"
    root = bytearray(34)
    root[0] = 34
    struct.pack_into("<I", root, 2, root_sector)
    struct.pack_into(">I", root, 6, root_sector)
    struct.pack_into("<I", root, 10, root_size)
    struct.pack_into(">I", root, 14, root_size)
    root[25] = 2
    root[32:34] = b"\x01\0"
    descriptor[156:190] = root
    image[16 * 0x800:17 * 0x800] = descriptor

    encoded_name = name.encode("ascii") + b";1"
    record_length = 33 + len(encoded_name) + (len(encoded_name) % 2 == 0)
    record = bytearray(record_length)
    record[0] = record_length
    struct.pack_into("<I", record, 2, file_sector)
    struct.pack_into(">I", record, 6, file_sector)
    struct.pack_into("<I", record, 10, len(data))
    struct.pack_into(">I", record, 14, len(data))
    record[32] = len(encoded_name)
    record[33:33 + len(encoded_name)] = encoded_name
    root_at = root_sector * 0x800
    image[root_at:root_at + len(record)] = record
    file_at = file_sector * 0x800
    image[file_at:file_at + len(data)] = data
    path.write_bytes(image)


def _assert_patches(test, before, after, base, patches):
    expected = bytearray(before)
    for patch in patches:
        struct.pack_into("<I", expected, patch.address - base, patch.patched)
    test.assertEqual(bytes(expected), after)


class DisableAntiCheatTests(unittest.TestCase):
    def test_each_owner_changes_only_declared_instructions(self):
        resource = make_resource_3()
        old = list(sle.iter_streams(resource))[0].output
        new = list(sle.iter_streams(
            patch_memory_card_resource(resource).data
        ))[0].output
        _assert_patches(self, old, new, 0x0035EC80, MEMORY_CARD_PATCHES)

        resource = make_main_resource()
        old = next(sle.iter_streams(resource)).output
        new = next(sle.iter_streams(patch_main_resource(resource).data)).output
        _assert_patches(self, old, new, 0x0035EC80, MAIN_PATCHES)

        resource, suffix, span = make_save_resource()
        old = next(sle.iter_streams(resource[:span])).output
        patched = patch_save_resource(resource).data
        new = next(sle.iter_streams(patched[:span])).output
        _assert_patches(self, old, new, 0x00495500, SAVE_PATCHES)
        self.assertEqual(suffix, patched[span:span + len(suffix)])

        resource, marker, second = make_battle_resource()
        old = battle_overlay.read(resource).output
        patched = patch_battle_resource(resource).data
        new = battle_overlay.read(patched).output
        _assert_patches(self, old, new, 0x0036D900, ALL_BATTLE_PATCHES)
        self.assertEqual(marker, patched[second:second + len(marker)])

        executable, target = make_executable()
        details = patch_executable(executable)
        patched = details.data
        expected = bytearray(executable)
        struct.pack_into("<I", expected, target, EXECUTABLE_PATCHED)
        compensation = elf.pcsx2_crc(expected) ^ elf.pcsx2_crc(executable)
        struct.pack_into(
            "<I", expected, CRC_COMPENSATION_OFFSET, compensation
        )
        self.assertEqual(bytes(expected), patched)
        self.assertEqual(elf.pcsx2_crc(executable), elf.pcsx2_crc(patched))
        self.assertEqual(CRC_COMPENSATION_OFFSET,
                         details.crc_compensation_offset)
        self.assertEqual(compensation, details.crc_compensation_value)

    def test_rejects_a_memory_card_instruction_with_only_matching_low_half(self):
        original = make_resource_3()
        stream = list(sle.iter_streams(original))[0]
        output = bytearray(stream.output)
        offset = MEMORY_CARD_PATCHES[0].address - 0x0035EC80
        struct.pack_into("<I", output, offset, 0xDEAD00FF)
        unlinked = sle.conceal(slz12.compress(output, 1))
        encoded = sle.conceal(
            slz12.compress(output, 1, next_offset=len(unlinked))
        )
        resource = encoded + original[stream.next_offset:]
        with self.assertRaisesRegex(ValueError, "expected 0x306B00FF"):
            patch_memory_card_resource(resource)


class CompleteBuildTests(unittest.TestCase):
    def test_all_twenty_one_patches_compose_in_one_iso(self):
        save_resource, _, _ = make_save_resource()
        battle_resource, _, _ = make_battle_resource()
        executable, executable_target = make_executable()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "clean.iso"
            output = root / "patched.iso"
            equip_resource, _, _ = make_equip_resource()
            skill_resource, _, _ = make_skill_resource()
            item_resource, _, _ = make_item_resource()
            sealstone_866, _, _ = make_sealstone_resource(866)
            sealstone_867, _, _ = make_sealstone_resource(867)
            write_synthetic_iso(source, {
                3: make_resource_3(),
                22: make_main_resource(),
                644: item_resource,
                645: equip_resource,
                646: skill_resource,
                647: make_attack_resource(),
                652: save_resource,
                866: sealstone_866,
                867: sealstone_867,
                1781: battle_resource,
            })
            install_iso_file(source, "SLUS_214.52", executable)
            source_before = source.read_bytes()

            result = build_iso(source, output)

            self.assertEqual(
                ("add-characters", "angel-slayer", "equip-everything",
                 "99-skill-points", "battle-anti-freeze",
                 "battle-menu-always", "36-character-limit",
                 "infinite-ap-attacks", "dupe-attacks",
                 "100-percent-drop-rate", "negate-encounters",
                 "hold-circle-float", "disable-anti-cheat",
                 "stop-removing-characters", "join-all-unlocked",
                 "join-level-1", "ether-set-effects",
                 "heavenly-punishment-15-ap",
                 "restore-all-sealstones",
                 "no-limit-sealstone-withdrawals", "all-items-99"),
                tuple(item.name for item in result.patches)
            )
            self.assertEqual(source_before, source.read_bytes())
            with output.open("rb") as handle:
                index = triace.read_index(handle)
                resource_3 = triace.read_resource(handle, index, 3)
                main = triace.read_resource(handle, index, 22)
                skill = triace.read_resource(handle, index, 646)
                battle = triace.read_resource(handle, index, 1781)
                extent = iso9660.locate_file(handle, "/SLUS_214.52")
                handle.seek(extent.offset)
                patched_executable = handle.read(extent.size)

            streams = list(sle.iter_streams(resource_3))
            for patch in MEMORY_CARD_PATCHES:
                self.assertEqual(patch.patched, struct.unpack_from(
                    "<I", streams[0].output,
                    patch.address - 0x0035EC80
                )[0])
            self.assertEqual(ANGEL_PATCHED, struct.unpack_from(
                "<I", streams[1].output, ANGEL_ADDRESS - 0x001E7E00
            )[0])

            main_output = next(sle.iter_streams(main)).output
            self.assertEqual(HOOK_PATCHED, struct.unpack_from(
                "<2I", main_output, HOOK_ADDRESS - 0x0035EC80
            ))
            self.assertEqual(
                tuple(replacement for _, _, replacement in FLOAT_HOOK_PATCHES),
                tuple(struct.unpack_from(
                    "<I", main_output, address - 0x0035EC80
                )[0] for address, _, _ in FLOAT_HOOK_PATCHES),
            )

            skill_output = menu_overlay.read(
                skill, 646, SKILL_LABEL, SKILL_MODE
            ).output
            self.assertEqual(
                tuple(replacement for _, _, replacement in SKILL_PATCHES),
                tuple(struct.unpack_from("<I", skill_output, offset)[0]
                      for offset in SKILL_TARGET_OFFSETS)
            )

            battle_output = battle_overlay.read(battle).output
            self.assertEqual(ANTI_FREEZE_PATCHED, struct.unpack_from(
                "<I", battle_output, ANTI_FREEZE_ADDRESS - 0x0036D900
            )[0])
            for patch in ALL_BATTLE_PATCHES:
                self.assertEqual(patch.patched, struct.unpack_from(
                    "<I", battle_output, patch.address - 0x0036D900
                )[0])
            for address, _, replacement in BATTLE_MENU_PATCHES:
                self.assertEqual(replacement, struct.unpack_from(
                    "<I", battle_output, address - 0x0036D900
                )[0])
            self.assertEqual(EXECUTABLE_PATCHED, struct.unpack_from(
                "<I", patched_executable, executable_target
            )[0])
            self.assertEqual(add_characters.INJECT_ADDRESS,
                             (struct.unpack_from(
                                 "<I", patched_executable,
                                 elf.file_offset_for_address(
                                     patched_executable,
                                     ADD_CHARACTERS_HOOK_ADDRESS, 4,
                                 ),
                             )[0] & 0x03FFFFFF) << 2)
            self.assertEqual(
                elf.pcsx2_crc(executable),
                elf.pcsx2_crc(patched_executable)
            )
            injected_at = elf.file_offset_for_address(
                patched_executable, INJECT_ADDRESS, len(INJECTED_CODE)
            )
            self.assertEqual(
                INJECTED_CODE,
                patched_executable[injected_at:injected_at + len(INJECTED_CODE)]
            )
            float_at = elf.file_offset_for_address(
                patched_executable, FLOAT_INJECT_ADDRESS,
                len(FLOAT_INJECT_WORDS) * 4,
            )
            self.assertEqual(
                FLOAT_INJECT_WORDS,
                struct.unpack_from(
                    "<%dI" % len(FLOAT_INJECT_WORDS),
                    patched_executable, float_at,
                ),
            )
            self.assertEqual(len(executable), len(patched_executable))


if __name__ == "__main__":
    unittest.main()
