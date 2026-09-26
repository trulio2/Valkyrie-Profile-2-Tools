# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Compile a source-free language pack and run the ISO patching engine."""

from __future__ import annotations

import base64
import collections
import contextlib
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from collections.abc import Iterable
from pathlib import Path

from .paths import (
    BUILD_DIR, FROZEN, PROJECT_ROOT, WORKSPACE_DIR, output_root,
)
from . import row_cache
from .workspace_extract import generate_workspace
from .translation_layout import rename_tree
from . import chapter_label
from . import overlay_edits
from . import vp2_battle_target
from . import vp2_battle_names
from . import vp2_battle_status
from .translation_pack import (
    PACK_CHAPTERS,
    PACK_MISC,
    PACK_PROFILE,
    PACK_SLOTS,
    PackError,
    _expanded_targets,
    _menu_units,
    is_language_pack,
    load_misc,
    load_pack,
)


_ROUTINE = re.compile(r"^(\[\d+/\d+\]|copy: |writing to |workspace: "
                      r"|dedupe: |shared-font: |chapters: |sheets: |== |"
                      r"accents: |streamed archive |tracked SLZ )")

IMAGE_DIRECTORY = "images"
SHEET_NAME_RE = re.compile(
    r"^(?:resource-[0-9]+-scenes|container-[0-9]+)\.csv$")
MENU_LAYOUT = PROJECT_ROOT / "data" / "menu-layout.csv"
WORKSPACE = WORKSPACE_DIR
TRANSLATIONS = PROJECT_ROOT / "translations"

#: Profile rows that name a pack file instead of a generated sheet: the
#: file, and the resources the row may name.
PACK_FILE_ROWS = {
    "chapter-label": (PACK_CHAPTERS, frozenset(chapter_label.CARRIERS)),
    "misc": (PACK_MISC, frozenset({overlay_edits.RESOURCE})),
}
PROFILE_KINDS = ("scene", "container", "fontless", "image",
                 *PACK_FILE_ROWS)

COMPILE_FORMAT = 1
COMPILE_STAMP = "compiled.json"


def _compile_stamp(records, pack_path, profile_path, menu_layout, only):
    """What the compiled workspace was made from, cheaply."""
    digest = hashlib.sha256()
    digest.update(("compile-v%d\0" % COMPILE_FORMAT).encode("ascii"))
    digest.update(os.fspath(profile_path).encode("utf-8"))
    digest.update(b"\0")
    digest.update(",".join(sorted(only or ())).encode("utf-8"))
    row_cache.stamp_path(digest, os.fspath(profile_path))
    row_cache.stamp_path(digest, os.fspath(menu_layout))
    row_cache.stamp_path(digest, os.fspath(records.parent / "generation.json"))
    row_cache.stamp_tree(digest, os.fspath(records))
    row_cache.stamp_tree(digest, os.fspath(pack_path))
    return digest.hexdigest()


def _compiled_already(build_root, stamp):
    """The previous compilation, when nothing it was made from has moved."""
    try:
        if (build_root / COMPILE_STAMP).read_text(encoding="utf-8").strip() \
                != stamp:
            return None
        metadata = json.loads(
            (build_root / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    manifest = build_root / "manifest.csv"
    sheets = build_root / "sheets"
    if not manifest.is_file() or not sheets.is_dir():
        return None
    slots = build_root / PACK_SLOTS
    return {**metadata, "root": build_root, "manifest": manifest,
            "sheets": sheets, "slots": slots if slots.is_file() else None}


def installed_locales() -> list[str]:
    if not TRANSLATIONS.is_dir():
        return []
    return sorted(entry.name for entry in TRANSLATIONS.iterdir()
                  if is_language_pack(entry))


def workspace_is_ready(workspace: str | os.PathLike[str]) -> bool:
    """Whether a build can read this workspace instead of making one."""
    internal = Path(workspace).expanduser().resolve() / "internal"
    return (internal / "generation.json").is_file() and (
        internal / "records").is_dir()


def resolve_pack(language: str | os.PathLike[str]) -> Path:
    """A locale name, or a path to a pack anywhere on disk."""
    value = os.fspath(language)
    bare = not any(separator in value for separator in "/\\")
    if bare and (TRANSLATIONS / value).is_dir():
        return TRANSLATIONS / value
    path = Path(value).expanduser()
    if path.is_dir():
        return path.resolve()
    installed = ", ".join(installed_locales())
    raise PackError(f"no language pack {value!r}"
                    + (f"; installed: {installed}" if installed else ""))


_RUNNING_LOCK = threading.Lock()
_RUNNING: set[subprocess.Popen] = set()


@contextlib.contextmanager
def _tracked(process: subprocess.Popen):
    """Make one patcher child stoppable from another thread."""
    with _RUNNING_LOCK:
        _RUNNING.add(process)
    try:
        yield process
    finally:
        with _RUNNING_LOCK:
            _RUNNING.discard(process)


def terminate_active_builds(timeout: float = 5.0) -> int:
    """Stop every patcher this process started, and say how many there were."""
    with _RUNNING_LOCK:
        running = [process for process in _RUNNING if process.poll() is None]
    for process in running:
        try:
            process.terminate()
        except OSError:                          # pragma: no cover - raced exit
            pass
    for process in running:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
        except OSError:                          # pragma: no cover - raced exit
            pass
    return len(running)


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = list(reader.fieldnames or ())
            return fields, [dict(row) for row in reader]
    except (OSError, UnicodeDecodeError, csv.Error) as exc:
        raise PackError(f"cannot read {path}: {exc}") from exc


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\r\n")
        writer.writeheader()
        writer.writerows(rows)


def _pack_locale(pack: Path) -> str:
    try:
        with (pack / "pack.toml").open("rb") as source:
            value = tomllib.load(source).get("locale")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"cannot read {pack / 'pack.toml'}: {exc}") from exc
    if not isinstance(value, str) or not value.strip():
        raise PackError(f"{pack / 'pack.toml'}: missing locale")
    return value.strip()


def _validated_misc(pack: Path) -> dict[str, dict[str, str]]:
    misc = load_misc(pack)
    row = misc.get("battle_target")
    if row is not None:
        try:
            vp2_battle_target.parse_x(row["offset_x"])
            if row["translated"]:
                vp2_battle_target.encode_label(row["translated"])
        except ValueError as exc:
            raise PackError(f"{pack / PACK_MISC}: {exc}") from exc
    try:
        vp2_battle_names.translations(
            {key: value["translated"] for key, value in misc.items()})
        vp2_battle_status.translations(misc)
    except ValueError as exc:
        raise PackError(f"{pack / PACK_MISC}: {exc}") from exc
    return misc


def _pack_battle_target(pack: Path) -> str | None:
    row = _validated_misc(pack).get("battle_target")
    return row["translated"] if row is not None else None


def _record_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    resource = (row.get("resource") or "0").strip()
    return (
        (row.get("kind") or "").strip(),
        str(int(resource, 16 if resource.lower().startswith("0x") else 10)),
        (row.get("message_id") or "").strip(),
        (row.get("message_index") or "").strip(),
    )


def _profile_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise PackError(
            f"language pack has no {PACK_PROFILE}: {path}. It lists the "
            f"resources this language's build writes, and how to write them.")
    fields, rows = _read_csv(path)
    required = {"kind", "resource", "sheet", "flags", "verify"}
    if not required.issubset(fields):
        raise PackError(f"{path}: expected columns {', '.join(sorted(required))}")
    return rows


def _checked_profile(path: Path) -> list[dict[str, str]]:
    rows = _profile_rows(path)
    seen: set[tuple[str, str]] = set()
    for line, row in enumerate(rows, 2):
        where = f"{path}:{line}"
        kind = (row.get("kind") or "").strip()
        if kind not in PROFILE_KINDS:
            raise PackError(f"{where}: unknown kind {kind!r}")
        try:
            resource = str(int((row.get("resource") or "").strip(), 0))
        except ValueError as exc:
            raise PackError(f"{where}: invalid resource "
                            f"{row.get('resource')!r}") from exc
        name = Path(row.get("sheet") or "").name
        if kind in PACK_FILE_ROWS:
            sheet, resources = PACK_FILE_ROWS[kind]
            if name != sheet:
                raise PackError(f"{where}: a {kind} row names {sheet}")
            if int(resource) not in resources:
                raise PackError(
                    f"{where}: resource {resource} has no {kind} to write "
                    f"(it may name {', '.join(map(str, sorted(resources)))})")
        elif kind != "image" and not SHEET_NAME_RE.fullmatch(name):
            raise PackError(f"{where}: {name!r} is not a generated sheet name")
        if (kind, resource) in seen:
            raise PackError(f"{where}: duplicate {kind} resource {resource}")
        seen.add((kind, resource))
        row["kind"] = kind
    return rows


def check_pack_profile(pack: str | os.PathLike[str]) -> int:
    """Validate one pack's build profile and its named assets; count rows."""
    pack_path = resolve_pack(pack)
    profile_path = pack_path / PACK_PROFILE
    rows = _checked_profile(profile_path)
    for line, row in enumerate(rows, 2):
        where = f"{profile_path}:{line}"
        kind = row["kind"]
        if kind == "image":
            folder = pack_path / (row.get("sheet") or IMAGE_DIRECTORY)
            if not folder.is_dir():
                raise PackError(
                    f"{where}: image directory {folder} is not there")
            resource = int(row["resource"], 0)
            prefix = f"fis-{resource:04d}-"
            if not any(path.is_file() and path.name.startswith(prefix)
                       and path.suffix.lower() == ".png"
                       for path in folder.iterdir()):
                raise PackError(
                    f"{where}: image directory {folder} has no {prefix}*.png")
        elif kind in PACK_FILE_ROWS:
            filename = PACK_FILE_ROWS[kind][0]
            path = pack_path / filename
            if not path.is_file():
                raise PackError(
                    f"{where}: required pack file {path} is not there")
    return len(rows)


def entry_id(row: dict[str, str]) -> str:
    kind = (row.get("kind") or "").strip()
    return f"{kind}-{int((row.get('resource') or '').strip(), 0)}"


def entry_label(row: dict[str, str]) -> str:
    return "misc" if (row.get("kind") or "").strip() == "misc" \
        else entry_id(row)


def profile_entries(
    pack: str | os.PathLike[str],
    *,
    profile: str | os.PathLike[str] | None = None,
) -> list[dict[str, str]]:
    pack_path = resolve_pack(pack)
    path = (Path(profile).expanduser().resolve() if profile is not None
            else pack_path / PACK_PROFILE)
    rows = _checked_profile(path)
    entries = [{"id": entry_id(row), "label": entry_label(row),
                "kind": (row.get("kind") or "").strip(),
                "resource": str(int((row.get("resource") or "").strip(), 0))}
               for row in rows]
    entries.sort(key=lambda entry: (entry["kind"], int(entry["resource"])))
    return entries


def _select_profile_rows(
    rows: list[dict[str, str]],
    only: Iterable[str] | None,
    profile_path: Path,
) -> list[dict[str, str]]:
    if only is None:
        return rows
    wanted = {str(item).strip() for item in only if str(item).strip()}
    known = {entry_id(row) for row in rows}
    unknown = sorted(wanted - known)
    if unknown:
        raise PackError(
            f"{profile_path}: no profile row is called "
            f"{', '.join(unknown[:6])}")
    return [row for row in rows if entry_id(row) in wanted]


def _input_sheet(records: Path, row: dict[str, str]) -> Path:
    name = Path(row["sheet"]).name
    folder = "containers" if name.startswith("container-") else "scenes"
    return records / folder / name


def _install_build_root(staging, build_root):
    retired = None
    if build_root.exists():
        retired = build_root.with_name(
            "%s.old.%d" % (build_root.name, os.getpid()))
        _retry_filesystem(lambda: build_root.replace(retired))
    try:
        _retry_filesystem(lambda: staging.replace(build_root))
    except BaseException:
        if retired is not None:
            _retry_filesystem(lambda: retired.replace(build_root), raising=False)
        raise
    if retired is not None:
        shutil.rmtree(retired, ignore_errors=True)


def _retry_filesystem(action, attempts=5, raising=True):
    for attempt in range(attempts):
        try:
            return action()
        except PermissionError:
            if attempt == attempts - 1:
                if raising:
                    raise
                return None
            time.sleep(0.1 * (attempt + 1))


def compile_build_workspace(
    workspace: str | os.PathLike[str],
    pack: str | os.PathLike[str],
    *,
    profile: str | os.PathLike[str] | None = None,
    menu_layout: str | os.PathLike[str] = MENU_LAYOUT,
    only: Iterable[str] | None = None,
) -> dict[str, object]:
    workspace_path = Path(workspace).expanduser().resolve()
    pack_path = resolve_pack(pack)
    profile_path = (Path(profile).expanduser().resolve() if profile is not None
                    else pack_path / PACK_PROFILE)
    internal = workspace_path / "internal"
    records = internal / "records"
    generation = internal / "generation.json"
    if not generation.is_file() or not records.is_dir():
        raise PackError(
            f"generated workspace is missing: run `python vp2_translate.py "
            f"generate <USA.iso>` first")

    locale = _pack_locale(pack_path)
    build_root = internal / "build" / locale
    stamp = _compile_stamp(records, pack_path, profile_path, menu_layout, only)
    reusable = _compiled_already(build_root, stamp)
    if reusable is not None:
        return reusable

    misc = _validated_misc(pack_path)
    target_row = misc.get("battle_target")
    battle_target = target_row["translated"] if target_row is not None else None
    translations = load_pack(pack_path, ignore_reference_columns=True)
    expanded = _expanded_targets(
        translations, _menu_units(os.fspath(menu_layout)))
    chapters = {
        key: value for key, value in expanded.items() if key[0] == "chapter"
    }
    exact = {
        key: value for key, value in expanded.items() if key[0] != "chapter"
    }

    build_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{locale}-", dir=build_root.parent))
    sheets = staging / "sheets"
    manifest_rows: list[dict[str, str]] = []
    matched: set[tuple[str, str, str, str]] = set()
    matched_chapters: set[tuple[str, str, str, str]] = set()
    try:
        profile_rows = _select_profile_rows(
            _checked_profile(profile_path), only, profile_path)
        text_rows = [row for row in profile_rows
                     if row["kind"] not in ("image", *PACK_FILE_ROWS)]
        listed = {kind: [row for row in profile_rows if row["kind"] == kind]
                  for kind in PACK_FILE_ROWS}
        profile_sheets = {Path(row["sheet"]).name for row in text_rows}
        missing = [row for row in text_rows
                   if not _input_sheet(records, row).is_file()]
        if missing:
            named = ", ".join(
                "#%s (%s)" % (row["resource"], Path(row["sheet"]).name)
                for row in missing[:6])
            raise PackError(
                f"{profile_path}: {len(missing)} resource(s) the profile "
                f"builds were not extracted: {named}. The workspace was "
                f"generated from a disc the extractor could not read them "
                f"from, or it skipped them as having no readable text")

        for source in sorted((records / "scenes").glob("*.csv")) + sorted(
                (records / "containers").glob("*.csv")):
            fields, rows = _read_csv(source)
            translated_count = 0
            used_speaker_names = False
            for record in rows:
                key = _record_key(record)
                value = exact.get(key)
                if value is None:
                    continue
                record["translated"] = value["translated"]
                if value["speaker_name"]:
                    record["speaker_name"] = value["speaker_name"]
                    used_speaker_names = True
                matched.add(key)
                translated_count += 1
            if used_speaker_names and "speaker_name" not in fields:
                fields.append("speaker_name")
            if translated_count or source.name in profile_sheets:
                _write_csv(sheets / source.name, fields, rows)

        unmatched = sorted(set(exact) - matched)
        if unmatched:
            resources = sorted({key[1] for key in unmatched}, key=int)
            raise PackError(
                f"pack has {len(unmatched)} identity row(s) absent from the "
                f"generated records, in resource(s) "
                f"{', '.join('#' + item for item in resources[:8])}: "
                f"{unmatched[:5]!r}")

        for profile_row in profile_rows:
            if profile_row["kind"] in PACK_FILE_ROWS:
                continue
            if profile_row["kind"] == "image":
                folder = pack_path / (profile_row.get("sheet")
                                      or IMAGE_DIRECTORY)
                if not folder.is_dir():
                    raise PackError(
                        f"{profile_path}: resource "
                        f"{profile_row['resource']} names image directory "
                        f"{folder}, which is not there")
                manifest_rows.append({
                    "kind": "image",
                    "resource": str(int(profile_row["resource"], 0)),
                    "sheet": os.fspath(folder),
                    "flags": "", "verify": "", "subresource": "",
                    "chapter_title": "", "chapter_title_message": "",
                })
                continue
            source = _input_sheet(records, profile_row)
            if not source.is_file():
                raise PackError(
                    f"{profile_path}: resource {profile_row['resource']} "
                    f"has no extracted sheet {source}")
            resource = str(int(profile_row["resource"], 0))
            chapter_matches = [
                (key, value) for key, value in chapters.items()
                if key[1] == resource
            ]
            manifest = {
                "kind": profile_row["kind"],
                "resource": resource,
                "sheet": os.fspath(build_root / "sheets" / source.name),
                "flags": profile_row.get("flags") or "",
                "verify": profile_row.get("verify") or "",
                "subresource": profile_row.get("subresource") or "",
                "chapter_title": "",
                "chapter_title_message": "",
            }
            if chapter_matches:
                if len(chapter_matches) != 1:
                    raise PackError(
                        f"resource {resource}: multiple chapter translations")
                key, value = chapter_matches[0]
                manifest["chapter_title"] = value["translated"]
                manifest["chapter_title_message"] = key[2]
                matched_chapters.add(key)
            manifest_rows.append(manifest)

        label_key = next(
            (key for key in chapters
             if key[1] == str(chapter_label.CARRIERS[0])
             and key[2] == str(chapter_label.FIRST_MESSAGE)), None)
        wanted = {"chapter-label": label_key is not None,
                  "misc": bool(misc)}
        for kind, (name, _resources) in PACK_FILE_ROWS.items():
            if not (wanted[kind] and listed[kind]):
                continue
            shutil.copyfile(pack_path / name, staging / name)
            for profile_row in listed[kind]:
                manifest_rows.append({
                    "kind": kind,
                    "resource": str(int(profile_row["resource"], 0)),
                    "sheet": os.fspath(build_root / name),
                    "flags": "", "verify": "", "subresource": "",
                    "chapter_title": "", "chapter_title_message": "",
                })
        if label_key is not None and listed["chapter-label"]:
            matched_chapters.add(label_key)
        if not listed["misc"]:
            battle_target = None

        profile_pairs = {
            (_record_key({
                "kind": "container" if Path(row["sheet"]).name.startswith(
                    "container-") else "scene",
                "resource": Path(row["sheet"]).name.split("-")[1].split(".")[0],
                "message_id": "0",
                "message_index": "",
            })[:2])
            for row in text_rows
        }
        ignored_chapters = set(chapters) - matched_chapters
        ignored = (sum(key[:2] not in profile_pairs for key in exact)
                   + len(ignored_chapters)
                   + (wanted["misc"] and not listed["misc"]))
        if not manifest_rows:
            raise PackError(f"{profile_path} lists no resources")

        manifest_path = staging / "manifest.csv"
        manifest_fields = [
            "kind", "resource", "sheet", "flags", "verify", "subresource",
            "chapter_title", "chapter_title_message",
        ]
        _write_csv(manifest_path, manifest_fields, manifest_rows)
        pack_slots = pack_path / PACK_SLOTS
        if pack_slots.is_file():
            shutil.copyfile(pack_slots, staging / PACK_SLOTS)
        metadata = {
            "format": 1,
            "locale": locale,
            "battle_target": battle_target,
            "resources": len(manifest_rows),
            "exact_translations": len(matched),
            "outside_profile": ignored,
        }
        (staging / "build.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        (staging / COMPILE_STAMP).write_text(stamp + "\n", encoding="utf-8")
        if build_root.exists():
            shutil.rmtree(build_root)
        rename_tree(staging, build_root)
        compiled_slots = build_root / PACK_SLOTS
        return {
            **metadata,
            "root": build_root,
            "manifest": build_root / "manifest.csv",
            "sheets": build_root / "sheets",
            "slots": compiled_slots if compiled_slots.is_file() else None,
        }
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _glyph_names(path: Path) -> dict[str, str]:
    _fields, rows = _read_csv(path)
    return {
        (row.get("digest") or "").strip().lower(): row.get("character") or ""
        for row in rows if (row.get("digest") or "").strip()
    }


def ensure_glyph_pool(
    source_iso: str | os.PathLike[str],
    workspace: str | os.PathLike[str],
) -> Path:
    """Harvest the USA subtitle face into the ignored local cache."""
    source = Path(source_iso).expanduser().resolve()
    internal = Path(workspace).expanduser().resolve() / "internal"
    inventory = internal / "inventory" / "usa.csv"
    cache = internal / "cache"
    pool = cache / "glyph-pool.csv"
    stamp = cache / "glyph-pool.json"
    identity = {
        "format": 1,
        "source_bytes": source.stat().st_size,
        "source_mtime_ns": source.stat().st_mtime_ns,
    }
    try:
        if pool.is_file() and json.loads(stamp.read_text(encoding="utf-8")) == identity:
            return pool
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    if not inventory.is_file():
        raise PackError(f"workspace inventory is missing: {inventory}")

    from . import vp2_cutscene_subtitles as subtitles
    from . import vp2_iso_buffer

    names = _glyph_names(PROJECT_ROOT / "data" / "glyph-names" / "en.csv")
    rejected = set(_glyph_names(
        PROJECT_ROOT / "data" / "glyph-names" / "title.csv"))
    _fields, inventory_rows = _read_csv(inventory)
    resources = [
        int(row["index"]) for row in inventory_rows
        if row.get("classification") == "local_font_dcms"
    ]
    iso = vp2_iso_buffer.IsoFile(os.fspath(source), mode="rb")
    candidates: dict[str, dict[str, dict[str, object]]] = {}
    try:
        anchored = {}
        for character, (resource, slot) in subtitles.BASIC_DONORS.items():
            block, _metric, _size = subtitles.donor_glyph(iso, resource, slot)
            anchored[hashlib.sha1(bytes(block)).hexdigest()] = character
        for position, resource in enumerate(resources, 1):
            try:
                found = subtitles.iso_alphabet(iso, resource, reference=iso)
            except (ValueError, IndexError, KeyError):
                continue
            expanded, layout, alphabet = found[3], found[4], found[5]
            for slot in range(layout["glyph_count"]):
                block = bytes(subtitles.glyph_bitmap(expanded, layout, slot))
                digest = hashlib.sha1(block).hexdigest()
                if digest in rejected:
                    continue
                character = alphabet.get(slot) or anchored.get(digest) or names.get(digest)
                if not character:
                    continue
                by_digest = candidates.setdefault(character, {})
                entry = by_digest.get(digest)
                if entry is None:
                    by_digest[digest] = {
                        "count": 1,
                        "resource": resource,
                        "slot": slot,
                        "metric": bytes(subtitles.glyph_metric(expanded, layout, slot)),
                        "pixels": block,
                    }
                else:
                    entry["count"] = int(entry["count"]) + 1
            if position % 50 == 0:
                print(f"glyphs: scanned {position}/{len(resources)} local fonts", flush=True)
    finally:
        iso.close()

    rows = []
    for character, options in sorted(candidates.items()):
        digest, entry = max(
            options.items(), key=lambda item: (int(item[1]["count"]), item[0]))
        rows.append({
            "character": character,
            "digest": digest,
            "source_resource": entry["resource"],
            "source_slot": entry["slot"],
            "scenes": entry["count"],
            "metric": base64.b64encode(entry["metric"]).decode("ascii"),
            "pixels": base64.b64encode(entry["pixels"]).decode("ascii"),
        })
    required = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0125")
    missing = sorted(required - set(candidates))
    if missing:
        raise PackError(f"USA glyph harvest is incomplete: {''.join(missing)}")
    cache.mkdir(parents=True, exist_ok=True)
    _write_csv(pool, [
        "character", "digest", "source_resource", "source_slot", "scenes",
        "metric", "pixels",
    ], rows)
    stamp.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"glyphs: cached {len(rows)} USA subtitle glyph(s)", flush=True)
    return pool


def runtime_environment(glyph_pool) -> dict:
    """The environment the runtime subprocess is given."""
    environment = os.environ.copy()
    environment["VP2_STATE_ROOT"] = os.fspath(BUILD_DIR)
    environment["VP2_GLYPH_POOL"] = os.fspath(glyph_pool)
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return environment


def _echo(text):
    """Print one line of child output whatever the console can encode."""
    stream = sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        if getattr(stream, "reconfigure", None) is not None:
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
                stream.write(text)
                stream.flush()
                return
            except (ValueError, OSError):
                pass
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "backslashreplace")
                     .decode(encoding, "replace"))
    stream.flush()


def build_iso(
    source_iso: str | os.PathLike[str],
    pack: str | os.PathLike[str],
    *,
    workspace: str | os.PathLike[str] = WORKSPACE,
    output: str | os.PathLike[str] | None = None,
    no_verify: bool = False,
    images: list[str | os.PathLike[str]] | None = None,
    strict_extents: bool = False,
    only: Iterable[str] | None = None,
    glyph_textures: str | os.PathLike[str] | None = None,
) -> Path:
    source = Path(source_iso).expanduser().resolve()
    if not source.is_file():
        raise PackError(f"USA image does not exist: {source}")
    if not workspace_is_ready(workspace):
        print("workspace: not prepared yet; reading the disc first",
              flush=True)
        generate_workspace(list(images) if images else [source], workspace,
                           reference=not FROZEN)
        print("workspace: prepared", flush=True)
    compiled = compile_build_workspace(workspace, resolve_pack(pack),
                                       only=only)
    glyph_pool = ensure_glyph_pool(source, workspace)
    locale = str(compiled["locale"])
    destination = (Path(output).expanduser().resolve() if output else
                   output_root() / f"{source.stem}.{locale}.iso")
    destination.parent.mkdir(parents=True, exist_ok=True)
    print(f"writing to {destination}", flush=True)

    runtime_args = [
        os.fspath(source),
        "--manifest", os.fspath(compiled["manifest"]),
        "--scenes-dir", os.fspath(compiled["sheets"]),
        "--output", os.fspath(destination),
        "--reference-iso", os.fspath(source),
    ]
    if compiled.get("slots"):
        runtime_args += ["--shared-font-slots",
                         os.fspath(compiled["slots"])]
    if no_verify:
        runtime_args.append("--no-verify")
    if not strict_extents:
        runtime_args.append("--record-candidate-extents")
    if glyph_textures:
        runtime_args += ["--glyph-textures",
                         os.fspath(Path(glyph_textures).expanduser().resolve())]
    command = runtime_command(runtime_args)
    environment = runtime_environment(glyph_pool)
    process = subprocess.Popen(
        command, cwd=PROJECT_ROOT, env=environment,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1)
    assert process.stdout is not None
    recent: collections.deque[str] = collections.deque(maxlen=6)
    with _tracked(process):
        for line in process.stdout:
            _echo(line)
            if line.strip():
                recent.append(line.rstrip())
        returncode = process.wait()
    if returncode:
        reason = [line for line in recent if not _ROUTINE.match(line)]
        said = "\n".join(reason or recent)
        raise PackError(
            (f"{said}\n\n" if said else "")
            + f"(ISO build failed with exit code {returncode})")
    return destination


def runtime_command(arguments: list[str]) -> list[str]:
    """Command that runs the low-level builder in source and frozen apps"""
    if getattr(sys, "frozen", False):
        return [sys.executable, "_runtime-build", *arguments]
    return [sys.executable, "-m", "tools.scripts.vp2_build", *arguments]
