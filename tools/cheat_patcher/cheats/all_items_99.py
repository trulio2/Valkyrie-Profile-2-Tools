# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Set every ordinary item stack to 99 when the Items menu opens."""

from dataclasses import dataclass

from . import _items_99
from .. import injected_code


LABEL = "All Items 99"
RESOURCE = _items_99.RESOURCE
OWNER = _items_99.OWNER
MODE = _items_99.MODE
PATCHES = _items_99.PATCHES
GUARDS = _items_99.GUARDS
INJECT_ADDRESS = _items_99.INJECT_ADDRESS
INJECT_WRITES = _items_99.inject_writes(_items_99.ALL_ITEMS_GUARD)


@dataclass(frozen=True)
class PatchSet:
    resources: tuple
    files: tuple


def patch_resource(resource):
    return _items_99.patch_resource(resource, LABEL)


def patch_executable(data):
    return injected_code.patch_executable(
        data, "All Items 99 routines", INJECT_WRITES
    )


RESOURCE_PATCHERS = ((RESOURCE, patch_resource),)
ISO_FILE_PATCHERS = ((injected_code.EXECUTABLE_PATH, patch_executable),)


def combine_details(resources, files):
    return PatchSet(tuple(resources), tuple(files))
