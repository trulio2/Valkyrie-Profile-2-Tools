"""Translation-sheet loading, deduplication, and workspace merging."""

import csv
import os
from pathlib import Path

from .paths import PROJECT_ROOT
from . import normalize_sheet_newlines
from .flag_duplicates import (AUTHORITATIVE, AUTHORITATIVE_RECORD,
                              WORKSPACE_CLAIM)
from . import einherjar_roster

HERE = PROJECT_ROOT / "tools"

SCENES_DIR = Path(HERE).parent / "data" / "vp2" / "scenes"

WORKSPACE_DIR = Path(HERE).parent / "data" / "vp2" / "translate"

MAX_CONFLICTS_SHOWN = 10

def sheet_kind(path):
    """Which half of the corpus a SHEET belongs to."""
    name = os.path.basename(str(path))
    if name.startswith("container-"):
        return "container"
    if name.startswith("resource-"):
        return "resource"
    return name

def workspace_kind(name):
    """Which records a workspace file's translations may fill."""
    base = os.path.basename(str(name))
    if base.startswith("container-"):
        return "container"
    if base.startswith("scene-"):
        return "resource"
    if base.startswith("menu"):
        return "container"
    return "resource"

def _load_workspace_translations(lookup, source, workspace_dir, en_only):
    """Seed the lookup from the language pack."""
    from .flag_duplicates import _normalize_text
    base = Path(workspace_dir) if workspace_dir else WORKSPACE_DIR
    if not base or not base.is_dir():
        return
    files = []
    for folder in ("dialogue", "menu"):
        directory = base / folder
        if not directory.is_dir():
            continue
        for name in sorted(os.listdir(directory)):
            if name.endswith(".csv"):
                files.append((directory / name, workspace_kind(name)))
    # The single-file layout this replaced, still read if it is there.
    if (base / "menus.csv").is_file():
        files.append((base / "menus.csv", "container"))
    for path, kind in files:
        if not path.is_file():
            continue
        try:
            rows, _, _ = normalize_sheet_newlines.read_rows(path)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        for row in rows:
            translated = (row.get("translated") or "").strip()
            en = _normalize_text(row.get("original_en"))
            if not en:
                continue
            jp = "" if en_only else _normalize_text(row.get("original_jp"))
            key = (kind, en, jp)
            resource = (row.get("resource") or "").strip()
            if resource:
                lookup.setdefault(
                    (WORKSPACE_CLAIM, kind, resource, en, jp), True)
                if translated:
                    lookup.setdefault(
                        (AUTHORITATIVE_RECORD, kind, resource, en, jp),
                        translated)
            if not translated:
                continue
            if key not in lookup:
                lookup[key] = translated
                source[key] = (path.name, row.get("message_id") or "")
                lookup[(AUTHORITATIVE,) + key] = translated

def _build_dedupe_lookup(scenes_dir=None, *, en_only=False, conflicts=None,
                         workspace_dir=None):
    """Walk every SHEET in ``scenes_dir`` and return ``{key: translated}``."""
    from .flag_duplicates import _normalize_text
    lookup = {}
    source = {}
    rosters = einherjar_roster.load_rosters()
    _load_workspace_translations(lookup, source, workspace_dir, en_only)
    base = Path(scenes_dir) if scenes_dir else SCENES_DIR
    if not base or not base.is_dir():
        return lookup
    for fname in sorted(os.listdir(base)):
        if not fname.endswith(".csv"):
            continue
        path = base / fname
        try:
            rows, _, _ = normalize_sheet_newlines.read_rows(path)
        except (OSError, csv.Error, UnicodeDecodeError):
            continue
        kind = sheet_kind(fname)
        for claimed in einherjar_roster.suppressed_rows(rows, rosters):
            resource = (claimed.get("resource") or "").strip()
            claimed_en = _normalize_text(claimed.get("original_en"))
            claimed_jp = ("" if en_only
                          else _normalize_text(claimed.get("original_jp")))
            if resource and claimed_en:
                lookup.setdefault(
                    (WORKSPACE_CLAIM, kind, resource, claimed_en, claimed_jp),
                    True)
        for row in rows:
            translated = (row.get("translated") or "").strip()
            if not translated:
                continue
            en = _normalize_text(row.get("original_en"))
            if not en:
                continue
            jp = "" if en_only else _normalize_text(row.get("original_jp"))
            key = (kind, en, jp)
            if key not in lookup:
                lookup[key] = translated
                source[key] = (fname, row.get("message_id"))
                continue
            if conflicts is not None and _normalize_text(
                    lookup[key]) != _normalize_text(translated):
                conflicts.setdefault(key, [source[key]]).append(
                    (fname, row.get("message_id")))
    return lookup

CHAPTERS_CSV = WORKSPACE_DIR / "chapters.csv"

def apply_chapter_titles(rows, path=None):
    """Take chapter titles from the workspace instead of the manifest."""
    source = Path(path) if path else CHAPTERS_CSV
    if not source.is_file():
        return 0
    try:
        titles, _, _ = normalize_sheet_newlines.read_rows(source)
    except (OSError, csv.Error, UnicodeDecodeError):
        return 0
    by_resource = {}
    for title in titles:
        translated = (title.get("translated") or "").strip()
        resource = (title.get("resource") or "").strip()
        message = (title.get("message_id") or "").strip()
        if translated and resource and message:
            by_resource[resource] = (translated, message)
    applied = 0
    for row in rows:
        found = by_resource.get((row.get("resource") or "").strip())
        if not found:
            continue
        row["chapter_title"], row["chapter_title_message"] = found
        applied += 1
    return applied

def repair_manifest_sheets(rows):
    """Put every manifest sheet back into the canonical line-ending form."""
    repaired = 0
    for row in rows:
        sheet = row.get('sheet')
        if not sheet or not os.path.exists(sheet):
            continue
        if os.path.isdir(sheet):
            continue
        fields, records = normalize_sheet_newlines.repair_in_place(sheet)
        if not (fields or records):
            continue
        repaired += 1
        parts = []
        if fields:
            parts.append(f"{fields} CRLF inside quoted field(s)")
        if records:
            parts.append(f"{records} record terminator(s) without CR")
        print(f"sheets: repaired {sheet} ({'; '.join(parts)})")
    return repaired

def _load_dedupe_lookup(scenes_dir):
    """``_build_dedupe_lookup`` plus the report a build should print."""
    conflicts = {}
    lookup = _build_dedupe_lookup(scenes_dir, conflicts=conflicts)
    if lookup:
        print(f"dedupe: {len(lookup)} translated line(s) loaded "
              f"from {scenes_dir}")
    return lookup

def _read_sheet_with_dedupe(scene_sheet, primary_lookup=None):
    """Read a SHEET and apply ``resolve_duplicates`` so empty translated"""
    from .flag_duplicates import resolve_duplicates
    sheet_rows, _, _ = normalize_sheet_newlines.read_rows(scene_sheet)
    replaced = []
    sheet_rows, _ = resolve_duplicates(
        sheet_rows, primary_lookup=primary_lookup,
        kind=sheet_kind(scene_sheet), replaced=replaced)
    for row, was, now in replaced:
        print(f"  workspace: {os.path.basename(str(scene_sheet))} "
              f"msg {row.get('message_id')} {was[:28]!r} -> {now[:28]!r}")
    return sheet_rows
