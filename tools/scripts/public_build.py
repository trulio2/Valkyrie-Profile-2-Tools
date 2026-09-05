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
from pathlib import Path

from .paths import BUILD_DIR, PROJECT_ROOT, WORKSPACE_DIR, output_root
from .workspace_extract import generate_workspace
from .translation_layout import rename_tree
from .translation_pack import (
    PACK_PROFILE,
    PACK_SLOTS,
    PackError,
    _expanded_targets,
    _menu_units,
    load_pack,
)


_ROUTINE = re.compile(r"^(\[\d+/\d+\]|copy: |writing to |workspace: "
                      r"|dedupe: |shared-font: |chapters: |sheets: |== |"
                      r"accents: |streamed archive |tracked SLZ )")

SHEET_NAME_RE = re.compile(
    r"^(?:resource-[0-9]+-scenes|container-[0-9]+)\.csv$")
MENU_LAYOUT = PROJECT_ROOT / "data" / "menu-layout.csv"
WORKSPACE = WORKSPACE_DIR
TRANSLATIONS = PROJECT_ROOT / "translations"


def installed_locales() -> list[str]:
    if not TRANSLATIONS.is_dir():
        return []
    return sorted(entry.name for entry in TRANSLATIONS.iterdir()
                  if (entry / "pack.toml").is_file())


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


def check_pack_profile(pack: str | os.PathLike[str]) -> int:
    """Validate one pack's build profile on its own, and count its rows."""
    path = resolve_pack(pack) / PACK_PROFILE
    rows = _profile_rows(path)
    seen: set[tuple[str, str]] = set()
    for line, row in enumerate(rows, 2):
        where = f"{path}:{line}"
        kind = (row.get("kind") or "").strip()
        if kind not in ("scene", "container", "fontless"):
            raise PackError(f"{where}: unknown kind {kind!r}")
        try:
            resource = str(int((row.get("resource") or "").strip(), 0))
        except ValueError as exc:
            raise PackError(f"{where}: invalid resource "
                            f"{row.get('resource')!r}") from exc
        # Only the shape of the name, because a sheet is a source of text
        # rather than the resource's own: the menu layout points 25, 868,
        # 869, 1480, and 1481 at container-0024.csv.
        name = Path(row.get("sheet") or "").name
        if not SHEET_NAME_RE.fullmatch(name):
            raise PackError(f"{where}: {name!r} is not a generated sheet name")
        if (kind, resource) in seen:
            raise PackError(f"{where}: duplicate {kind} resource {resource}")
        seen.add((kind, resource))
    return len(rows)


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
) -> dict[str, object]:
    """Join one pack to extracted records and emit patcher-ready sheets."""
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
    translations = load_pack(pack_path, ignore_reference_columns=True)
    expanded = _expanded_targets(
        translations, _menu_units(os.fspath(menu_layout)))
    chapters = {
        key: value for key, value in expanded.items() if key[0] == "chapter"
    }
    exact = {
        key: value for key, value in expanded.items() if key[0] != "chapter"
    }

    build_root = internal / "build" / locale
    build_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{locale}-", dir=build_root.parent))
    sheets = staging / "sheets"
    manifest_rows: list[dict[str, str]] = []
    matched: set[tuple[str, str, str, str]] = set()
    matched_chapters: set[tuple[str, str, str, str]] = set()
    try:
        profile_rows = _profile_rows(profile_path)
        profile_sheets = {Path(row["sheet"]).name for row in profile_rows}

        for source in sorted((records / "scenes").glob("*.csv")) + sorted(
                (records / "containers").glob("*.csv")):
            fields, rows = _read_csv(source)
            translated_count = 0
            for record in rows:
                key = _record_key(record)
                value = exact.get(key)
                if value is None:
                    continue
                record["translated"] = value
                matched.add(key)
                translated_count += 1
            if translated_count or source.name in profile_sheets:
                _write_csv(sheets / source.name, fields, rows)

        unmatched = sorted(set(exact) - matched)
        if unmatched:
            raise PackError(
                f"pack has {len(unmatched)} identity row(s) absent from the "
                f"generated records: {unmatched[:5]!r}")

        for profile_row in profile_rows:
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
                manifest["chapter_title"] = value
                manifest["chapter_title_message"] = key[2]
                matched_chapters.add(key)
            manifest_rows.append(manifest)

        profile_pairs = {
            (_record_key({
                "kind": "container" if Path(row["sheet"]).name.startswith(
                    "container-") else "scene",
                "resource": Path(row["sheet"]).name.split("-")[1].split(".")[0],
                "message_id": "0",
                "message_index": "",
            })[:2])
            for row in profile_rows
        }
        ignored_chapters = set(chapters) - matched_chapters
        ignored = (sum(key[:2] not in profile_pairs for key in exact)
                   + len(ignored_chapters))
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
            "resources": len(manifest_rows),
            "exact_translations": len(matched),
            "outside_profile": ignored,
        }
        (staging / "build.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
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


def _echo(text):
    """Print one line of child output whatever the console can encode."""
    stream = sys.stdout
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "ascii"
        stream.write(text.encode(encoding, "replace").decode(encoding, "replace"))
    stream.flush()


def build_iso(
    source_iso: str | os.PathLike[str],
    pack: str | os.PathLike[str],
    *,
    workspace: str | os.PathLike[str] = WORKSPACE,
    output: str | os.PathLike[str] | None = None,
    no_verify: bool = False,
    images: list[str | os.PathLike[str]] | None = None,
) -> Path:
    """Compile the pack and run the patcher in a clean subprocess.

    Reads the workspace out of the disc first when it is not there yet.
    """
    source = Path(source_iso).expanduser().resolve()
    if not source.is_file():
        raise PackError(f"USA image does not exist: {source}")
    if not workspace_is_ready(workspace):
        print("workspace: not prepared yet; reading the disc first",
              flush=True)
        generate_workspace(list(images) if images else [source], workspace)
        print("workspace: prepared", flush=True)
    compiled = compile_build_workspace(workspace, resolve_pack(pack))
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
    command = runtime_command(runtime_args)
    environment = os.environ.copy()
    environment["VP2_STATE_ROOT"] = os.fspath(BUILD_DIR)
    environment["VP2_GLYPH_POOL"] = os.fspath(glyph_pool)
    environment["PYTHONUNBUFFERED"] = "1"
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
