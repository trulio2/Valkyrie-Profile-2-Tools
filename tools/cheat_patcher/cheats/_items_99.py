# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The CampItem hook and injected routine the item-count cheats share."""

from . import _overlay_patch
from .. import injected_code


RESOURCE = 644
OWNER = "CampItem"
MODE = 3
PATCHES = ((0x0049EBB4, 0x19200178, 0x087FAF40),)
GUARDS = ((0x0049EBB4, 0x19200178),)
INJECT_ADDRESS = 0x01FEBC9C
MIDDLE_ADDRESS = 0x01FEBD00
TAIL_ADDRESS = 0x01FEBD50
HEAD_WORDS = (
    0x341B02A2, 0x136D0035, 0x00000000, 0x341B02A3, 0x136D0032,
    0x00000000, 0x341B02A4, 0x136D002F, 0x00000000, 0x341B02A5,
    0x136D002C, 0x00000000, 0x341B02A6, 0x136D0029, 0x00000000,
    0x341B02A7, 0x136D0026, 0x00000000, 0x341B02B1, 0x136D0023,
    0x00000000, 0x10000017, 0x00000000,
)
MIDDLE_WORDS = (
    0x341B02EE, 0x01BBD82A, 0x1360001B, 0x00000000, 0x1000FFE2,
    0x00000000, 0x00000000,
)
TAIL_WORDS = (
    0x34090063, 0xA1C90002, 0x341B0000, 0x08127AEF, 0x00000000,
    0x00000000, 0x11200003, 0x00000000, 0x1000FFF9, 0x00000000,
    0x341B0000, 0x08127C66, 0x00000000,
)
ALL_ITEMS_GUARD = (0x00000000, 0x00000000, 0x00000000, 0x00000000)
UNLOCKED_ITEMS_GUARD = (0x81DB0002, 0x00000000, 0x13600007, 0x00000000)


def inject_writes(guard_words):
    return (
        injected_code.Write(INJECT_ADDRESS, HEAD_WORDS),
        injected_code.Write(MIDDLE_ADDRESS, MIDDLE_WORDS),
        injected_code.Write(TAIL_ADDRESS, tuple(guard_words) + TAIL_WORDS),
    )


def patch_resource(resource, label):
    return _overlay_patch.patch_menu(
        resource, RESOURCE, OWNER, MODE, label, PATCHES, GUARDS
    )
