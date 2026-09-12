# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Apply the supported ISO patches in one copy-and-verify transaction."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Optional

from . import iso9660, triace
from .cheats import (
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
    hold_circle_float,
    join_all_unlocked,
    join_level_1,
    negate_encounters,
    no_limit_sealstone_withdrawals,
    restore_all_sealstones,
    skill_points_99,
    stop_removing_characters,
)


PATCHERS = {
    "add-characters": add_characters,
    "angel-slayer": angel_slayer,
    "equip-everything": equip_everything,
    "99-skill-points": skill_points_99,
    "battle-anti-freeze": battle_anti_freeze,
    "battle-menu-always": battle_menu_always,
    "36-character-limit": character_limit_36,
    "infinite-ap-attacks": infinite_ap_attacks,
    "dupe-attacks": dupe_attacks,
    "100-percent-drop-rate": drop_rate_100,
    "negate-encounters": negate_encounters,
    "hold-circle-float": hold_circle_float,
    "disable-anti-cheat": disable_anti_cheat,
    "stop-removing-characters": stop_removing_characters,
    "join-all-unlocked": join_all_unlocked,
    "join-level-1": join_level_1,
    "ether-set-effects": ether_set_effects,
    "heavenly-punishment-15-ap": heavenly_punishment_15_ap,
    "restore-all-sealstones": restore_all_sealstones,
    "no-limit-sealstone-withdrawals": no_limit_sealstone_withdrawals,
    "all-items-99": all_items_99,
}


@dataclass(frozen=True)
class AppliedPatch:
    name: str
    resource: Optional[int]
    iso_offset: Optional[int]
    details: object


@dataclass(frozen=True)
class BuildResult:
    output: Path
    patches: tuple


def default_output_path(source):
    """Where a patched image goes when the caller does not say."""
    from ..scripts.paths import output_root

    source = Path(source)
    return output_root() / (source.stem + "-cheat-patched.iso")


def _selected_patchers(selected):
    names = tuple(selected) if selected else tuple(PATCHERS)
    if len(set(names)) != len(names):
        raise ValueError("the same patch was selected more than once")
    unknown = [name for name in names if name not in PATCHERS]
    if unknown:
        raise ValueError("unknown patch: %s" % ", ".join(unknown))
    if not names:
        raise ValueError("select at least one patch")
    return [(name, PATCHERS[name]) for name in names]


def _resource_patchers(patcher):
    if hasattr(patcher, "RESOURCE_PATCHERS"):
        return patcher.RESOURCE_PATCHERS
    return ((patcher.RESOURCE, patcher.patch_resource),)


def _read_iso_file(handle, path):
    extent = iso9660.locate_file(handle, path)
    handle.seek(extent.offset)
    data = handle.read(extent.size)
    if len(data) != extent.size:
        raise ValueError("ISO9660 file extends past the end of the image: %s" % path)
    return extent, data


COPY_CHUNK = 8 * 1024 * 1024


def _copy_with_progress(source, target, say):
    """`shutil.copyfile` that reports progress."""
    total = source.stat().st_size
    done = last = 0
    with source.open("rb") as reader, target.open("wb") as writer:
        while True:
            chunk = reader.read(COPY_CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            done += len(chunk)
            percent = done * 100 // total if total else 100
            if percent != last:
                say("copy: %d%%" % percent)
                last = percent


def build_iso(source, output=None, selected=None, progress=None):
    """Copy an ISO once, apply selected patches, and verify it from disk.

    *progress* is called with single-line status messages if given.
    """
    say = progress if progress is not None else (lambda message: None)
    source = Path(source).expanduser().resolve()
    output = (Path(output).expanduser().resolve()
              if output else default_output_path(source))
    if not source.is_file():
        raise ValueError("source ISO does not exist: %s" % source)
    if source == output:
        raise ValueError("output must be different from the source ISO")
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite: %s" % output)
    partial = output.with_name(output.name + ".partial")
    if partial.exists():
        raise ValueError("partial output already exists; remove it first: %s" % partial)

    resource_states = {}
    file_states = {}
    pending = []
    with source.open("rb") as source_handle:
        source_index = triace.read_index(source_handle)
        for name, patcher in _selected_patchers(selected):
            say("patch: reading and rebuilding %s" % name)
            resource_details = []
            file_details = []
            touched_resources = []
            for resource, patch_resource in _resource_patchers(patcher):
                if resource not in resource_states:
                    iso_offset, allocation = source_index.extent(resource)
                    original = triace.read_resource(
                        source_handle, source_index, resource
                    )
                    resource_states[resource] = {
                        "offset": iso_offset,
                        "allocation": allocation,
                        "original": original,
                        "current": original,
                    }
                state = resource_states[resource]
                details = patch_resource(state["current"])
                if (details.allocation_size != state["allocation"] or
                        len(details.data) != state["allocation"]):
                    raise ValueError(
                        "resource %d allocation changed unexpectedly" % resource
                    )
                state["current"] = details.data
                resource_details.append(details)
                touched_resources.append(resource)

            for path, patch_file in getattr(patcher, "ISO_FILE_PATCHERS", ()):
                key = path.upper()
                if key not in file_states:
                    extent, original = _read_iso_file(source_handle, path)
                    source_handle.seek(extent.offset + extent.size)
                    slack = source_handle.read(
                        extent.allocation_size - extent.size
                    )
                    if (len(slack) != extent.allocation_size - extent.size or
                            any(slack)):
                        raise ValueError(
                            "ISO9660 file has nonzero or truncated allocation "
                            "slack: %s" % path
                        )
                    file_states[key] = {
                        "path": path,
                        "extent": extent,
                        "original": original,
                        "current": original,
                    }
                state = file_states[key]
                details = patch_file(state["current"])
                if (details.allocation_size != len(details.data) or
                        not len(state["current"]) <= len(details.data)
                        <= state["extent"].allocation_size):
                    raise ValueError(
                        "ISO9660 file exceeded its fixed allocation: %s" % path
                    )
                state["current"] = details.data
                file_details.append(details)

            if hasattr(patcher, "combine_details"):
                details = patcher.combine_details(
                    resource_details, file_details
                )
            else:
                details = resource_details[0]
            resource = (touched_resources[0]
                        if len(touched_resources) == 1 and not file_details
                        else None)
            iso_offset = (resource_states[resource]["offset"]
                          if resource is not None else None)
            pending.append((name, resource, iso_offset, details))

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        say("copy: 0%")
        _copy_with_progress(source, partial, say)
        say("write: applying %d patch(es)" % len(pending))
        with partial.open("r+b") as candidate_handle:
            for state in resource_states.values():
                candidate_handle.seek(state["offset"])
                candidate_handle.write(state["current"])
            for state in file_states.values():
                candidate_handle.seek(state["extent"].offset)
                candidate_handle.write(state["current"])
                if len(state["current"]) != state["extent"].size:
                    iso9660.write_file_size(
                        candidate_handle, state["extent"],
                        len(state["current"])
                    )
            candidate_handle.flush()
            os.fsync(candidate_handle.fileno())

        say("verify: reading every patched region back from disk")
        with partial.open("rb") as candidate_handle:
            candidate_index = triace.read_index(candidate_handle)
            if candidate_index != source_index:
                raise ValueError("ISO resource index changed during patching")
            for resource, state in resource_states.items():
                candidate = triace.read_resource(
                    candidate_handle, candidate_index, resource
                )
                if candidate != state["current"]:
                    raise ValueError(
                        "resource %d did not read back byte-for-byte"
                        % resource
                    )
            for state in file_states.values():
                extent, candidate = _read_iso_file(
                    candidate_handle, state["path"]
                )
                original_extent = state["extent"]
                if (extent.path != original_extent.path or
                        extent.offset != original_extent.offset or
                        extent.allocation_size != original_extent.allocation_size or
                        extent.record_offset != original_extent.record_offset or
                        extent.size != len(state["current"])):
                    raise ValueError(
                        "ISO9660 file extent changed: %s" % state["path"]
                    )
                if candidate != state["current"]:
                    raise ValueError(
                        "ISO9660 file did not read back byte-for-byte: %s"
                        % state["path"]
                    )
        if partial.stat().st_size != source.stat().st_size:
            raise ValueError("output ISO size differs from source")
        partial.replace(output)
        say("wrote %s" % output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise

    applied = tuple(
        AppliedPatch(name, resource, iso_offset, details)
        for name, resource, iso_offset, details in pending
    )
    return BuildResult(output=output, patches=applied)
