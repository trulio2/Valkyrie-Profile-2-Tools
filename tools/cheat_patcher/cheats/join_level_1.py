# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Make the special recruitments handled by the PNACH routine join at level 1."""

from dataclasses import dataclass

from .. import injected_code, resource3_overlay


RESOURCE = 3
LABEL = "Characters Join At Level 1"
PATCHES = ((0x002F8E54, 0x00628024, 0x087FAD80),)
INJECT_ADDRESS = 0x01FEB600
INJECT_WORDS = (
    0x3C080054, 0x35089090, 0x14880015, 0x00000000, 0x2008000C, 0x1268002F,
    0x00000000, 0x20080008, 0x12680032, 0x00000000, 0x20080007, 0x12680035,
    0x00000000, 0x20080000, 0x12680038, 0x00000000, 0x20080009, 0x1268003B,
    0x00000000, 0x20080001, 0x1268003E, 0x00000000, 0x00000000, 0x00000000,
    0x00628024, 0x080BE397, 0x20080000, 0x00000000, 0x00000000, 0x20A80000,
    0x3C17A328, 0x36F77637, 0xAD170000, 0x20A80004, 0x3C17E0E1, 0x36F7C095,
    0xAD170000, 0x20A8001C, 0x3C17E057, 0x36F7AAB7, 0xAD170000, 0x20A80020,
    0x3C17E2CD, 0x36F76BBF, 0xAD170000, 0x20A80024, 0x3C17E0E0, 0x36F7E9BF,
    0xAD170000, 0x00000000, 0x1000FFE5, 0x20170000, 0x00000000, 0x20A80018,
    0x3C1770F8, 0x36F7F751, 0xAD170000, 0x1000FFE3, 0x00000000, 0x20A80018,
    0x3C1768F8, 0x36F7F751, 0xAD170000, 0x1000FFDD, 0x00000000, 0x20A80018,
    0x3C1768F8, 0x36F7F751, 0xAD170000, 0x1000FFD7, 0x00000000, 0x20A80018,
    0x3C1768F8, 0x36F7F751, 0xAD170000, 0x1000FFD1, 0x00000000, 0x20A80018,
    0x3C1768F8, 0x36F7F751, 0xAD170000, 0x1000FFCB, 0x00000000, 0x20A80018,
    0x3C176CF8, 0x36F7F751, 0xAD170000, 0x1000FFC5, 0x00000000, 0x00000000,
)
ADD_CHARACTERS_LEVEL_OVERRIDES = (
    (0x01FEAC6C, 0x24060032, 0x24060001),
    (0x01FEAC78, 0x24060032, 0x24060001),
    (0x01FEAC84, 0x24060032, 0x24060001),
    (0x01FEAC90, 0x24060032, 0x24060001),
    (0x01FEAC9C, 0x24060032, 0x24060001),
    (0x01FEACA8, 0x2406002D, 0x24060001),
    (0x01FEACB4, 0x2406002F, 0x24060001),
    (0x01FEACC0, 0x24060037, 0x24060001),
    (0x01FEACCC, 0x24060030, 0x24060001),
)


@dataclass(frozen=True)
class ResourcePatch:
    data: bytes
    allocation_size: int
    label: str
    change_count: int
    overlay_offsets: tuple
    old_stored_size: int
    new_stored_size: int


@dataclass(frozen=True)
class PatchSet:
    resources: tuple
    files: tuple


def patch_resource(resource):
    patched = resource3_overlay.patch_words(resource, LABEL, PATCHES)
    return ResourcePatch(
        patched.data, len(resource), "resource 3 level-one recruitment hook",
        len(PATCHES), patched.offsets,
        patched.old_stored_size, patched.new_stored_size,
    )


def patch_executable(data):
    writes = [injected_code.Write(INJECT_ADDRESS, INJECT_WORDS)]
    writes.extend(
        injected_code.Write(address, (replacement,), ((0,), (original,)))
        for address, original, replacement in ADD_CHARACTERS_LEVEL_OVERRIDES
    )
    return injected_code.patch_executable(
        data, "main executable level-one recruitment routine", tuple(writes)
    )


RESOURCE_PATCHERS = ((RESOURCE, patch_resource),)
ISO_FILE_PATCHERS = ((injected_code.EXECUTABLE_PATH, patch_executable),)


def combine_details(resources, files):
    return PatchSet(tuple(resources), tuple(files))
