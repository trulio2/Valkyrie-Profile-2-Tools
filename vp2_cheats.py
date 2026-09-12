#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Command-line interface for the VP2 cheat patcher."""

import argparse
import sys

from tools.cheat_patcher.cheats import (
    add_characters,
    all_items_99,
    angel_slayer,
    battle_anti_freeze,
    battle_menu_always,
    character_limit_36,
    disable_anti_cheat,
    drop_rate_100,
    dupe_attacks,
    equip_everything,
    ether_set_effects,
    infinite_ap_attacks,
    heavenly_punishment_15_ap,
    join_all_unlocked,
    join_level_1,
    negate_encounters,
    no_limit_sealstone_withdrawals,
    restore_all_sealstones,
    skill_points_99,
    stop_removing_characters,
)
from tools.cheat_patcher.build import PATCHERS, build_iso, default_output_path
from tools.cheat_patcher.catalog import required_with


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?",
                        help="path to a clean Valkyrie Profile 2 USA ISO")
    parser.add_argument(
        "-o", "--output",
        help="output path (default: beside the tool, named after the source)",
    )
    parser.add_argument(
        "--self-check", action="store_true",
        help="verify this build carries everything it needs, then exit")
    parser.add_argument(
        "--patch",
        action="append",
        choices=tuple(PATCHERS),
        help="patch to apply; repeat to select multiple (default: all)",
    )
    return parser.parse_args(argv)


def self_check(stream=None):
    """Load what a patch run loads and report it.  Non-zero means do not ship."""
    out = stream or sys.stdout
    notes, problems = [], []
    frozen = getattr(sys, "frozen", False)
    notes.append("frozen            : %s" % bool(frozen))

    notes.append("iso written to    : %s" % default_output_path("Example.iso").parent)

    try:
        from tools.cheat_patcher import catalog
        notes.append("patches           : %d (%s)"
                     % (len(catalog.CHEATS),
                        ", ".join(sorted(catalog.BY_NAME))))
        missing = sorted(set(PATCHERS) - set(catalog.BY_NAME))
        if missing:
            problems.append("patches with no description: %s" % missing)
    except Exception as exc:                     # pragma: no cover - packaging
        problems.append("the catalog does not import: %r" % exc)

    for line in notes:
        print(line, file=out)
    for line in problems:
        print("FAIL  %s" % line, file=out)
    if problems:
        print("\n%d problem(s); this build should not ship" % len(problems),
              file=out)
        return 1
    print("\nself-check ok", file=out)
    return 0


def main(argv=None):
    args = parse_args(argv)
    if args.self_check:
        return self_check()
    if args.source is None:
        print("error: give a source ISO (use --help for usage)", file=sys.stderr)
        return 2
    output = args.output or default_output_path(args.source)
    selected = (required_with(args.patch) if args.patch else tuple(PATCHERS))
    print("Validating and recompressing: %s..." % ", ".join(selected))
    try:
        result = build_iso(args.source, output, selected=selected,
                           progress=print)
    except (OSError, ValueError) as error:
        print("error: %s" % error, file=sys.stderr)
        return 1
    for applied in result.patches:
        patch = applied.details
        if applied.name == "angel-slayer":
            print(
                "Angel Slayer: EE 0x%08X 0x%08X -> 0x%08X "
                "(resource %d, stream %d, module +0x%X)"
                % (angel_slayer.TARGET_ADDRESS, angel_slayer.ORIGINAL_WORD,
                   angel_slayer.PATCHED_WORD, applied.resource,
                   patch.stream_number, patch.module_offset)
            )
        elif applied.name == "equip-everything":
            print(
                "Equip Everything: EE 0x%08X 0x%08X -> NOP "
                "(resource %d, CampEquip.ovl +0x%X)"
                % (equip_everything.TARGET_ADDRESS,
                   equip_everything.ORIGINAL_INSTRUCTION,
                   applied.resource, patch.overlay_offset)
            )
        elif applied.name == "99-skill-points":
            print(
                "99 Skill Points: EE 0x%08X and 0x%08X "
                "(resource %d, CampSkill.ovl +0x%X/+0x%X)"
                % (skill_points_99.PATCHES[0][0],
                   skill_points_99.PATCHES[1][0], applied.resource,
                   patch.overlay_offsets[0], patch.overlay_offsets[1])
            )
        elif applied.name == "battle-anti-freeze":
            print(
                "Battle Anti-Freeze: EE 0x%08X 0x%08X -> 0x%08X "
                "(resource %d, battle overlay +0x%X)"
                % (battle_anti_freeze.TARGET_ADDRESS,
                   battle_anti_freeze.ORIGINAL_INSTRUCTION,
                   battle_anti_freeze.PATCHED_INSTRUCTION,
                   applied.resource, patch.module_offset)
            )
        elif applied.name == "battle-menu-always":
            print(
                "Battle Menu Always Available: EE 0x%08X and 0x%08X "
                "(resource %d, battle overlay +0x%X/+0x%X)"
                % (battle_menu_always.PATCHES[0][0],
                   battle_menu_always.PATCHES[1][0], applied.resource,
                   patch.overlay_offsets[0], patch.overlay_offsets[1])
            )
        elif applied.name == "36-character-limit":
            print(
                "36 Characters Limit: EE 0x%08X 0x%08X -> 0x%08X "
                "(resource %d, main overlay +0x%X)"
                % (*character_limit_36.PATCHES[0], applied.resource,
                   patch.overlay_offsets[0])
            )
        elif applied.name == "infinite-ap-attacks":
            print(
                "Infinite AP And Attacks: %d battle instructions "
                "(resource %d)"
                % (len(infinite_ap_attacks.PATCHES), applied.resource)
            )
        elif applied.name == "dupe-attacks":
            print(
                "Dupe Attacks: EE 0x%08X 0x%08X -> 0x%08X "
                "(resource %d, CampAttack.ovl +0x%X)"
                % (*dupe_attacks.PATCHES[0], applied.resource,
                   patch.overlay_offsets[0])
            )
        elif applied.name == "100-percent-drop-rate":
            print(
                "100%% Drop Rate: %d battle instructions (resource %d)"
                % (len(drop_rate_100.PATCHES), applied.resource)
            )
        elif applied.name == "negate-encounters":
            print(
                "Negate Encounters: EE 0x%08X 0x%08X -> NOP "
                "(resource %d, main overlay +0x%X)"
                % (negate_encounters.PATCHES[0][0],
                   negate_encounters.PATCHES[0][1], applied.resource,
                   patch.overlay_offsets[0])
            )
        elif applied.name == "disable-anti-cheat":
            resources = ", ".join(
                "%s: %d" % (item.label, item.change_count)
                for item in patch.resources
            )
            file_changes = sum(item.change_count for item in patch.files)
            total = (sum(item.change_count for item in patch.resources) +
                     file_changes)
            print(
                "Disable Anti-Cheat: %d instructions (%s; executable: %d; "
                "PCSX2 CRC preserved: %08X)"
                % (total, resources, file_changes,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "stop-removing-characters":
            print(
                "Stop Removing Characters: %d hook instructions + %d-word "
                "routine at EE 0x%08X (PCSX2 CRC preserved: %08X)"
                % (patch.resources[0].change_count,
                   patch.files[0].change_count,
                   stop_removing_characters.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "join-all-unlocked":
            print(
                "Join All Unlocked: %d hook instructions + %d-word routine "
                "at EE 0x%08X (PCSX2 CRC preserved: %08X)"
                % (patch.resources[0].change_count,
                   patch.files[0].change_count,
                   join_all_unlocked.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "add-characters":
            print(
                "Add Characters: %d hook word + %d-word routine at EE "
                "0x%08X (PCSX2 CRC preserved: %08X)"
                % (patch.files[0].change_count - len(add_characters.INJECT_WORDS),
                   len(add_characters.INJECT_WORDS),
                   add_characters.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "join-level-1":
            print(
                "Join At Level 1: %d hook instruction + %d exact code words "
                "from EE 0x%08X (PCSX2 CRC preserved: %08X)"
                % (patch.resources[0].change_count,
                   patch.files[0].change_count,
                   join_level_1.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "ether-set-effects":
            print(
                "Ether Set Effects: %d item bytes (resource %d)"
                % (len(ether_set_effects.BYTE_PATCHES), applied.resource)
            )
        elif applied.name == "heavenly-punishment-15-ap":
            print(
                "Heavenly Punishment 15 AP: 4 hook instructions + %d-word "
                "routine at EE 0x%08X (PCSX2 CRC preserved: %08X)"
                % (len(heavenly_punishment_15_ap.INJECT_WORDS),
                   heavenly_punishment_15_ap.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
        elif applied.name == "restore-all-sealstones":
            print(
                "Restore All Sealstones: %d words in each of resources "
                "866/867 + %d-word routine at EE 0x%08X"
                % (len(restore_all_sealstones.PATCHES),
                   len(restore_all_sealstones.INJECT_WORDS),
                   restore_all_sealstones.INJECT_ADDRESS)
            )
        elif applied.name == "no-limit-sealstone-withdrawals":
            print(
                "No Limit For Sealstone Withdrawals: %d words in each of "
                "resources 866/867"
                % len(no_limit_sealstone_withdrawals.PATCHES)
            )
        elif applied.name == "all-items-99":
            print(
                "All Items 99: CampItem hook + %d exact routine words from "
                "EE 0x%08X (PCSX2 CRC preserved: %08X)"
                % (sum(len(write.words) for write in all_items_99.INJECT_WRITES),
                   all_items_99.INJECT_ADDRESS,
                   patch.files[0].patched_crc)
            )
    print("Verified output: %s" % result.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
