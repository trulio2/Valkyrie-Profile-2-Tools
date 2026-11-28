# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Disable VP2's main, save, and battle checksum systems on disc.

A disc that already has them disabled, such as a translated one, is left as
it is.
"""

from ...scripts.anti_cheat import (  # noqa: F401
    ALL_BATTLE_PATCHES,
    BATTLE_GUARD_ADDRESS,
    BATTLE_GUARD_INSTRUCTION,
    BATTLE_PATCHES,
    BATTLE_RESOURCE,
    CORRUPTED_SAVE_PATCHES,
    CRC_COMPENSATION_OFFSET,
    EXECUTABLE_ADDRESS,
    EXECUTABLE_ORIGINAL,
    EXECUTABLE_PATCHED,
    EXECUTABLE_PATH,
    ISO_FILE_PATCHERS,
    MAIN_PATCHES,
    MAIN_RESOURCE,
    MEMORY_CARD_PATCHES,
    MEMORY_CARD_RESOURCE,
    RESIDENT_PATCHES,
    RESIDENT_RESOURCE,
    RESOURCE_PATCHERS,
    SAVE_PATCHES,
    SAVE_RESOURCE,
    ComponentPatch,
    InstructionPatch,
    PatchSet,
    combine_details,
    patch_battle_resource,
    patch_executable,
    patch_main_resource,
    patch_memory_card_resource,
    patch_resident_resource,
    patch_save_resource,
)
