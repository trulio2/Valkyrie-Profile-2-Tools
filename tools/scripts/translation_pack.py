# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Read, validate, and synchronize source-free translation packs."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import tempfile
import tomllib
import uuid
from pathlib import Path


PACK_FIELDS = ("resource", "message_id", "translated", "notes")
REFERENCE_FIELDS = frozenset({"original_en", "original_jp", "speaker"})
LEGACY_PACK_FIELDS = (
    "kind", "resource", "message_id", "message_index", "source_hash",
    "translated", "notes",
)
KEY_FIELDS = ("kind", "resource", "message_id", "message_index")
SOURCE_FIELDS = frozenset({
    "original_en", "original_jp", "english", "japanese", "source_en",
    "source_jp",
})
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
MESSAGE_KEY_RE = re.compile(r"^(?:0[xX][0-9A-Fa-f]+|[0-9]+)(?:\+[0-9]+)?$")
SCENE_PATH_RE = re.compile(r"^dialogue/scene-([0-9]+)\.csv$")
CONTAINER_PATH_RE = re.compile(r"^dialogue/container-([0-9]+)\.csv$")
MENU_PATH_RE = re.compile(r"^menu/menu-([1-5])\.csv$")
PACK_FORMAT = 2
PACK_PROFILE = "build-profile.csv"
PACK_SLOTS = "shared-font-slots.csv"


class PackError(ValueError):
    """A language pack or local workspace violates the public contract."""


def is_language_pack(path: str | os.PathLike[str]) -> bool:
    """Whether a folder is a language to offer; a leading `_` marks one that is not."""
    directory = Path(path)
    return (not directory.name.startswith("_")
            and (directory / "pack.toml").is_file())


def canonical_source(text: str | None) -> str:
    """Return the canonical decoded source representation."""
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def source_hash(text: str | None) -> str:
    """Fingerprint decoded source text for legacy-format migration checks."""
    return hashlib.sha256(canonical_source(text).encode("utf-8")).hexdigest()


def stable_key(row: dict[str, str], *, where: str = "row") -> tuple[str, ...]:
    """Validate and return a generated row's exact record identity."""
    values = tuple((row.get(field) or "").strip() for field in KEY_FIELDS)
    kind, resource, message_id, message_index = values
    if not kind:
        raise PackError(f"{where}: missing kind")
    if not resource:
        raise PackError(f"{where}: missing resource")
    try:
        resource_number = int(resource, 0)
    except ValueError as exc:
        raise PackError(f"{where}: invalid resource {resource!r}") from exc
    if resource_number < 0:
        raise PackError(f"{where}: resource must not be negative")
    if not message_id:
        raise PackError(f"{where}: missing message_id")
    if not MESSAGE_KEY_RE.fullmatch(message_id):
        raise PackError(f"{where}: invalid message_id {message_id!r}")
    if message_index:
        try:
            index = int(message_index, 0)
        except ValueError as exc:
            raise PackError(
                f"{where}: invalid message_index {message_index!r}") from exc
        if index < 0:
            raise PackError(f"{where}: message_index must not be negative")
    return kind, str(resource_number), message_id, message_index


def _csv_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.rglob("*.csv") if path.is_file())


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or ())
            return fields, [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PackError(f"cannot read {path}: {exc}") from exc


def _write_csv_atomic(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as target:
            writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\r\n")
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _path_kind(path: Path, base: Path) -> tuple[str, int | None]:
    relative = path.relative_to(base).as_posix()
    if relative == "chapter.csv":
        return "chapter", None
    match = SCENE_PATH_RE.fullmatch(relative)
    if match:
        return "scene", int(match.group(1))
    match = CONTAINER_PATH_RE.fullmatch(relative)
    if match:
        return "container", int(match.group(1))
    if MENU_PATH_RE.fullmatch(relative):
        return "menu", None
    raise PackError(f"{path}: CSV is outside chapter.csv, dialogue/, or menu/")


def _structured_key(
    path: Path,
    base: Path,
    row: dict[str, str],
    *,
    where: str,
) -> tuple[str, ...]:
    kind, path_resource = _path_kind(path, base)
    key = stable_key({
        "kind": kind,
        "resource": row.get("resource") or "",
        "message_id": row.get("message_id") or "",
        "message_index": "",
    }, where=where)
    if path_resource is not None and int(key[1]) != path_resource:
        raise PackError(
            f"{where}: resource {key[1]} does not match {path.name}")
    return key


def _load_legacy_pack(path: Path) -> dict[tuple[str, ...], dict[str, str]]:
    fields, records = _read_csv(path)
    if tuple(fields) != LEGACY_PACK_FIELDS:
        raise PackError(
            f"{path}: expected legacy columns {', '.join(LEGACY_PACK_FIELDS)}")
    rows: dict[tuple[str, ...], dict[str, str]] = {}
    for line, row in enumerate(records, 2):
        where = f"{path}:{line}"
        if not (row.get("translated") or "").strip():
            raise PackError(f"{where}: legacy packs must be sparse")
        digest = (row.get("source_hash") or "").strip()
        if not HASH_RE.fullmatch(digest):
            raise PackError(f"{where}: source_hash must be lowercase SHA-256")
        key = stable_key(row, where=where)
        if key in rows:
            raise PackError(f"{where}: duplicate identity {key!r}")
        rows[key] = {field: row.get(field) or "" for field in LEGACY_PACK_FIELDS}
    return rows


def _manifest_format(base: Path) -> int:
    path = base / "pack.toml"
    try:
        with path.open("rb") as source:
            manifest = tomllib.load(source)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"cannot read language pack manifest {path}: {exc}") from exc
    value = manifest.get("format")
    if not isinstance(value, int):
        raise PackError(f"{path}: format must be an integer")
    for field in ("locale", "name"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise PackError(f"{path}: missing {field}")
    return value


def load_pack(
    directory: str | os.PathLike[str],
    *,
    ignore_reference_columns: bool = False,
) -> dict[tuple[str, ...], dict[str, str]]:
    """Validate a pack and return only its authored translations."""
    base = Path(directory)
    if not base.is_dir():
        raise PackError(f"language pack directory does not exist: {base}")
    files = [path for path in _csv_files(base)
             if path.relative_to(base).as_posix() not in (PACK_PROFILE,
                                                          PACK_SLOTS)]
    legacy = base / "translations.csv"
    if legacy in files:
        if files != [legacy]:
            raise PackError(f"{base}: legacy and structured pack CSVs are mixed")
        if _manifest_format(base) != 1:
            raise PackError(f"{base / 'pack.toml'}: legacy pack format must be 1")
        return _load_legacy_pack(legacy)
    if _manifest_format(base) != PACK_FORMAT:
        raise PackError(
            f"{base / 'pack.toml'}: structured pack format must be {PACK_FORMAT}")

    rows: dict[tuple[str, ...], dict[str, str]] = {}
    for path in files:
        fields, records = _read_csv(path)
        validated_fields = [
            field for field in fields
            if not (ignore_reference_columns and field in REFERENCE_FIELDS)
        ]
        forbidden = SOURCE_FIELDS.intersection(
            field.lower() for field in validated_fields)
        if forbidden:
            names = ", ".join(sorted(forbidden))
            raise PackError(f"{path}: forbidden source column(s): {names}")
        if tuple(validated_fields) != PACK_FIELDS:
            raise PackError(f"{path}: expected columns {', '.join(PACK_FIELDS)}")
        for line, row in enumerate(records, 2):
            where = f"{path}:{line}"
            key = _structured_key(path, base, row, where=where)
            if key in rows:
                raise PackError(f"{where}: duplicate identity {key!r}")
            if not (row.get("translated") or "").strip():
                continue
            rows[key] = {
                "kind": key[0],
                "resource": key[1],
                "message_id": key[2],
                "message_index": key[3],
                "translated": row.get("translated") or "",
                "notes": row.get("notes") or "",
            }
    return rows


def _reference_files(reference: Path) -> list[Path]:
    files = _csv_files(reference)
    for path in files:
        _path_kind(path, reference)
    return files


def _menu_units(menu_layout: str | os.PathLike[str]) -> dict[tuple[str, ...], list[tuple[str, ...]]]:
    from .translation_layout import load_menu_layout

    units = {}
    for (_menu, _unit), exact_keys in load_menu_layout(menu_layout).items():
        representative = exact_keys[0]
        key = ("menu", representative[1], representative[2], representative[3])
        if key in units:
            raise PackError(f"menu layout repeats representative identity {key!r}")
        units[key] = exact_keys
    return units


def _expanded_targets(
    translations: dict[tuple[str, ...], dict[str, str]],
    menu_units: dict[tuple[str, ...], list[tuple[str, ...]]],
) -> dict[tuple[str, ...], str]:
    expanded = {}
    for key, row in translations.items():
        targets = menu_units.get(key, (key,)) if key[0] == "menu" else (key,)
        for target in targets:
            value = row["translated"]
            previous = expanded.get(target)
            if previous is not None and previous != value:
                raise PackError(f"conflicting translations for {target!r}")
            expanded[target] = value
    return expanded


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _replace_pack(target: Path, generated: Path) -> None:
    backup = target.with_name("." + target.name + "-previous")
    if backup.exists() and not target.exists():
        backup.replace(target)
    elif backup.exists():
        _remove_path(backup)
    if target.exists():
        target.replace(backup)
    try:
        generated.replace(target)
    except BaseException:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    if backup.exists():
        _remove_path(backup)
