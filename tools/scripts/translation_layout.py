# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Generate the mirrored, local-only translator reference tree."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from collections import defaultdict
from pathlib import Path

from .translation_pack import (
    PackError,
    _csv_files,
    _read_csv,
    _write_csv_atomic,
    canonical_source,
    stable_key,
)

DIALOGUE_CONTAINERS = frozenset({"10", "31"})

MENU_LAYOUT_FIELDS = (
    "menu",
    "unit",
    "resource",
    "message_id",
    "message_index",
)

SCENE_REFERENCE_FIELDS = (
    "resource",
    "message_id",
    "original_en",
    "original_jp",
    "speaker",
    "scene",
    "scene_line",
    "details",
)
CONTAINER_REFERENCE_FIELDS = (
    "resource",
    "message_id",
    "original_en",
    "original_jp",
    "record_kind",
)
CHAPTER_REFERENCE_FIELDS = (
    "resource",
    "message_id",
    "original_en",
    "original_jp",
    "chapter",
)
MENU_REFERENCE_FIELDS = (
    "resource",
    "message_id",
    "original_en",
    "original_jp",
    "occurrences",
    "resources",
)

SCENE_SOURCE_NAME = re.compile(r"^resource-([0-9]+)-scenes\.csv$")


def menu_source_key(text: str | None) -> str:
    """Ignore inherited display wrapping when matching one menu string."""
    return re.sub(r"\s+", " ", canonical_source(text)).strip()


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def rename_tree(source: Path, target: Path, timeout: float = 30.0) -> None:
    """Rename a directory, waiting out a transient hold on a fresh tree."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def _replace_tree(target: Path, generated: Path) -> None:
    """Replace one generated directory, restoring the old tree on error."""
    backup = target.with_name("." + target.name + "-previous")
    if backup.exists() and not target.exists():
        rename_tree(backup, target)
    elif backup.exists():
        _remove_path(backup)
    if target.exists():
        rename_tree(target, backup)
    try:
        rename_tree(generated, target)
    except BaseException:
        if backup.exists() and not target.exists():
            rename_tree(backup, target)
        raise
    if backup.exists():
        _remove_path(backup)


def _source_rows(original: Path) -> dict[tuple[str, ...], dict[str, str]]:
    rows: dict[tuple[str, ...], dict[str, str]] = {}
    for path in _csv_files(original):
        fields, records = _read_csv(path)
        if "original_en" not in fields:
            raise PackError(f"{path}: generated source has no original_en")
        for line, row in enumerate(records, 2):
            key = stable_key(row, where=f"{path}:{line}")
            if key in rows:
                raise PackError(f"{path}:{line}: duplicate source identity {key!r}")
            rows[key] = row
    return rows


def load_menu_layout(
    path: str | os.PathLike[str],
) -> dict[tuple[int, int], list[tuple[str, ...]]]:
    """Read the source-free mapping from menu units to exact containers."""
    source = Path(path)
    fields, rows = _read_csv(source)
    if tuple(fields) != MENU_LAYOUT_FIELDS:
        raise PackError(
            f"{source}: expected columns {', '.join(MENU_LAYOUT_FIELDS)}")
    units: dict[tuple[int, int], list[tuple[str, ...]]] = defaultdict(list)
    seen: set[tuple[str, ...]] = set()
    for line, row in enumerate(rows, 2):
        where = f"{source}:{line}"
        try:
            menu = int((row.get("menu") or "").strip())
            unit = int((row.get("unit") or "").strip())
        except ValueError as exc:
            raise PackError(f"{where}: invalid menu or unit number") from exc
        if menu not in range(1, 6) or unit < 1:
            raise PackError(f"{where}: menu must be 1-5 and unit must be positive")
        identity = {
            "kind": "container",
            "resource": row.get("resource") or "",
            "message_id": row.get("message_id") or "",
            "message_index": row.get("message_index") or "",
        }
        key = stable_key(identity, where=where)
        if key in seen:
            raise PackError(f"{where}: container identity appears more than once")
        seen.add(key)
        units[(menu, unit)].append(key)
    for menu in range(1, 6):
        numbers = sorted(unit for current, unit in units if current == menu)
        if numbers and numbers != list(range(1, numbers[-1] + 1)):
            raise PackError(f"{source}: menu-{menu} unit numbers are not contiguous")
    return dict(units)


def _copy_reference_rows(
    source: Path,
    target: Path,
    fields: tuple[str, ...],
) -> int:
    _source_fields, rows = _read_csv(source)
    visible = [
        {field: row.get(field) or "" for field in fields}
        for row in rows
        if row.get("original_en") or row.get("original_jp")
    ]
    _write_csv_atomic(target, list(fields), visible)
    return len(visible)


def _menu_reference(
    rows: dict[tuple[str, ...], dict[str, str]],
    layout: dict[tuple[int, int], list[tuple[str, ...]]],
    output: Path,
) -> tuple[int, int]:
    expected = {
        key for key, row in rows.items()
        if key[0] == "container" and key[1] not in DIALOGUE_CONTAINERS
        and (row.get("original_en") or row.get("original_jp"))
    }
    mapped = {key for keys in layout.values() for key in keys}
    missing = sorted(expected - mapped)
    extra = sorted(mapped - expected)
    if missing or extra:
        def _summarise(keys):
            seen = {}
            for key in keys:
                seen[key[1]] = seen.get(key[1], 0) + 1
            return ", ".join(
                f"#{resource} ({count})"
                for resource, count in sorted(
                    seen.items(), key=lambda item: -item[1])[:8])

        detail = []
        if missing:
            detail.append(
                f"{len(missing)} extracted record(s) the layout does not "
                f"list, in resource(s) {_summarise(missing)}")
        if extra:
            detail.append(
                f"{len(extra)} listed record(s) the extraction did not "
                f"produce, in resource(s) {_summarise(extra)}")
        raise PackError(
            "menu layout does not match the extracted container catalogue: "
            + "; ".join(detail)
            + ". data/menu-layout.csv describes every container record this "
              "build can translate, so it has to be regenerated whenever the "
              "extractor learns to read a bank it could not read before")

    row_count = occurrence_count = 0
    for menu in range(1, 6):
        reference_rows = []
        units = sorted(
            ((unit, keys) for (current, unit), keys in layout.items()
             if current == menu),
            key=lambda item: item[0],
        )
        for _unit, keys in units:
            records = [rows[key] for key in keys]
            english = {menu_source_key(row.get("original_en")) for row in records}
            if len(english) != 1:
                raise PackError(
                    f"menu-{menu} unit {_unit} joins different USA source text")
            japanese = []
            for record in records:
                text = canonical_source(record.get("original_jp"))
                if text and text not in japanese:
                    japanese.append(text)
            representative = records[0]
            resources = sorted(
                {int(record["resource"]) for record in records})
            reference_rows.append({
                "resource": str(int(representative["resource"])),
                "message_id": representative["message_id"],
                "original_jp": "\n---\n".join(japanese),
                "original_en": representative.get("original_en") or "",
                "occurrences": str(len(records)),
                "resources": " ".join(
                    f"container-{resource:04d}.csv" for resource in resources),
            })
            occurrence_count += len(records)
        _write_csv_atomic(
            output / "menu" / f"menu-{menu}.csv",
            list(MENU_REFERENCE_FIELDS), reference_rows)
        row_count += len(reference_rows)
    return row_count, occurrence_count


def write_reference_tree(
    original: str | os.PathLike[str],
    menu_layout: str | os.PathLike[str],
    output: str | os.PathLike[str],
) -> dict[str, int]:
    """Generate an atomic, translator-facing reference tree."""
    original_dir = Path(original)
    output_dir = Path(output)
    if not original_dir.is_dir():
        raise PackError(f"generated source directory does not exist: {original_dir}")
    layout_path = Path(menu_layout)
    layout = load_menu_layout(layout_path)
    source_rows = _source_rows(original_dir)

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = output_dir.parent / (".reference-" + uuid.uuid4().hex)
    try:
        dialogue_rows = 0
        for source in sorted((original_dir / "scenes").glob("*.csv")):
            match = SCENE_SOURCE_NAME.fullmatch(source.name)
            if not match:
                raise PackError(f"unexpected scene source filename: {source}")
            resource = int(match.group(1))
            dialogue_rows += _copy_reference_rows(
                source, staging / "dialogue" / f"scene-{resource:04d}.csv",
                SCENE_REFERENCE_FIELDS)

        for resource in sorted(int(item) for item in DIALOGUE_CONTAINERS):
            source = (original_dir / "containers"
                      / f"container-{resource:04d}.csv")
            if source.is_file():
                dialogue_rows += _copy_reference_rows(
                    source, staging / "dialogue" / f"container-{resource:04d}.csv",
                    CONTAINER_REFERENCE_FIELDS)

        chapter_rows = _copy_reference_rows(
            original_dir / "chapters.csv", staging / "chapter.csv",
            CHAPTER_REFERENCE_FIELDS)
        menu_rows, menu_occurrences = _menu_reference(
            source_rows, layout, staging)
        metadata = {
            "format": 1,
            "chapter_rows": chapter_rows,
            "dialogue_rows": dialogue_rows,
            "menu_rows": menu_rows,
            "menu_occurrences": menu_occurrences,
            "menu_layout_sha256": hashlib.sha256(
                layout_path.read_bytes()).hexdigest(),
        }
        (staging / "generation.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        _replace_tree(output_dir, staging)
        return metadata
    finally:
        if staging.exists():
            shutil.rmtree(staging)
