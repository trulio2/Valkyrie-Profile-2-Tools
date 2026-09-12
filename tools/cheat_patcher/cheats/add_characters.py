# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Install the executable hook that recruits the PNACH special roster."""

from dataclasses import dataclass
import struct
from typing import Optional

from .. import elf, injected_code


HOOK_ADDRESS = 0x0011F624
HOOK_ORIGINAL = 0x00000000
HOOK_PATCHED = 0x087FAB00
INJECT_ADDRESS = 0x01FEAC00
RETURN_ADDRESS = 0x0011F62C
INJECT_WORDS = (
    0x00000000, 0x00000000,
    0x3C06001C, 0x34C6A1DC, 0x94C60000, 0x34A5F0FF,
    0x14A60032, 0x00000000,
    0x3C06003B, 0x34C65DD4, 0x8CC60000, 0x3C051645,
    0x34A50004, 0x14A6002B, 0x00000000,
    0x3C060804, 0x34C67D8B, 0x3C0501FE, 0x34A5AC00,
    0xACA60000, 0x00000000,
    0x3C06003B, 0x34C65DDC, 0xACC00000, 0x00000000,
    0x2405000D, 0x0C0ED754, 0x24060032,
    0x2405000D, 0x0C0ED754, 0x24060032,
    0x2405000D, 0x0C0ED754, 0x24060032,
    0x2405000D, 0x0C0ED754, 0x24060032,
    0x24050009, 0x0C0ED754, 0x24060032,
    0x24050008, 0x0C0ED754, 0x2406002D,
    0x24050001, 0x0C0ED754, 0x2406002F,
    0x2405000A, 0x0C0ED754, 0x24060037,
    0x24050002, 0x0C0ED754, 0x24060030,
    0x00000000,
    0x3C050011, 0x34A53D64, 0x24BF0000,
    0x00000000,
    0x24050000, 0x24060000, 0x08047D8B,
)


@dataclass(frozen=True)
class ComponentPatch:
    data: bytes
    allocation_size: int
    label: str
    change_count: int
    file_offset: Optional[int] = None
    original_size: Optional[int] = None
    new_size: Optional[int] = None
    original_crc: Optional[int] = None
    patched_crc: Optional[int] = None
    crc_compensation_offset: Optional[int] = None
    crc_compensation_value: Optional[int] = None
    program_header_index: Optional[int] = None


@dataclass(frozen=True)
class PatchSet:
    resources: tuple
    files: tuple


def _word(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def patch_executable(data):
    """Install the Add Characters hook and recruitment routine in SLUS_214.52."""
    data = bytes(data)
    hook_offset = elf.file_offset_for_address(data, HOOK_ADDRESS, 4)
    observed = _word(data, hook_offset)
    if observed == HOOK_PATCHED:
        raise ValueError("Add Characters is already patched in the executable")
    if observed != HOOK_ORIGINAL:
        raise ValueError(
            "Add Characters hook validation failed at EE 0x%08X; "
            "expected 0x%08X, found 0x%08X"
            % (HOOK_ADDRESS, HOOK_ORIGINAL, observed)
        )
    inject_details = injected_code.patch_executable(
        data, "main executable Add Characters routine",
        (injected_code.Write(INJECT_ADDRESS, INJECT_WORDS),),
    )
    intermediate = bytearray(inject_details.data)
    new_hook_offset = elf.file_offset_for_address(
        intermediate, HOOK_ADDRESS, 4
    )
    struct.pack_into("<I", intermediate, new_hook_offset, HOOK_PATCHED)
    original_crc = elf.pcsx2_crc(data)
    rebuilt, compensation_value, patched_crc = elf.preserve_pcsx2_crc(
        data, bytes(intermediate)
    )
    expected = bytearray(intermediate)
    struct.pack_into("<I", expected, new_hook_offset, HOOK_PATCHED)
    struct.pack_into(
        "<I", expected, elf.CRC_COMPENSATION_OFFSET, compensation_value
    )
    if rebuilt != bytes(expected) or len(rebuilt) != len(intermediate):
        raise ValueError(
            "executable changed outside its Add Characters hook and routine"
        )
    return ComponentPatch(
        rebuilt, len(rebuilt),
        "main executable Add Characters hook and routine",
        1 + len(INJECT_WORDS),
        file_offset=inject_details.file_offset,
        original_size=len(data), new_size=len(rebuilt),
        original_crc=original_crc, patched_crc=patched_crc,
        crc_compensation_offset=elf.CRC_COMPENSATION_OFFSET,
        crc_compensation_value=compensation_value,
        program_header_index=inject_details.program_header_index,
    )


RESOURCE_PATCHERS = ()
ISO_FILE_PATCHERS = ((injected_code.EXECUTABLE_PATH, patch_executable),)


def combine_details(resources, files):
    return PatchSet(tuple(resources), tuple(files))
