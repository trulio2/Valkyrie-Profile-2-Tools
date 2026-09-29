#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import argparse
import csv
import sys

from tools.scripts import glyph_range
from tools.scripts import normalize_sheet_newlines
from tools.scripts import overlay_edits
from tools.scripts import vp2_item_sort
from tools.scripts import vp2_sealstone_sort
from tools.scripts import vp2_shared_font as shared_font
from tools.scripts.build_translations import _build_dedupe_lookup, sheet_kind
from tools.scripts.flag_duplicates import resolve_duplicates
from tools.scripts.paths import WORKSPACE_DIR
from tools.scripts.public_build import (
    BUILD_OPTIONS, compile_build_workspace, resolve_pack, workspace_is_ready,
)


CHECKED = {
    3: "the resident overlay (main checksum)",
    22: "the main overlay (main checksum)",
    1781: "the battle overlay (battle checksum)",
}

FONT_KINDS = ("scene", "fontless", "worldmap")


def range_characters(tokens):
    return {character for character, token in tokens.items()
            if glyph_range.is_range_token(token)}


def row_findings(row, text_characters, range_characters, enabled):
    findings = []
    kind = (row.get("kind") or "").strip()
    flags = (row.get("flags") or "").split()
    try:
        resource = int((row.get("resource") or "0").strip(), 0)
    except ValueError:
        resource = None
    contributes = (kind in FONT_KINDS
                   or (kind == "container" and "shared-font-glyphs" in flags))
    if contributes and text_characters:
        missing = sorted(text_characters & range_characters)
        if missing:
            findings.append(
                "text needs the shared-font second range (%s); its install "
                "rewrites %s"
                % (" ".join("U+%04X" % ord(c) for c in missing), CHECKED[3]))
    if kind == "misc":
        findings.append("edits %s" % CHECKED[overlay_edits.RESOURCE])
    elif kind == "image" and resource == overlay_edits.RESOURCE:
        findings.append("the image layout edits %s"
                        % CHECKED[overlay_edits.RESOURCE])
    elif resource in (3, 22) and kind not in ("image",):
        findings.append("edits %s" % CHECKED[resource])
    if "item-order" in enabled and resource == vp2_item_sort.NAME_RESOURCE:
        findings.append("its names make the item sort rewrite %s"
                        % CHECKED[vp2_item_sort.RESOURCE])
    if ("sealstone-order" in enabled
            and resource == vp2_sealstone_sort.NAME_RESOURCE):
        findings.append("its names make the Sealstone sort rewrite %s"
                        % CHECKED[vp2_sealstone_sort.RESOURCE])
    return findings


def _read_rows(path, primary_lookup):
    rows, _fields, _bad = normalize_sheet_newlines.read_rows(path)
    rows, _ = resolve_duplicates(
        rows, primary_lookup=primary_lookup, kind=sheet_kind(path),
        replaced=[])
    return rows


def _text_characters(path, primary_lookup):
    characters = set()
    for row in _read_rows(path, primary_lookup):
        characters.update(row.get("translated") or "")
        characters.update(row.get("speaker_name") or "")
    return characters


def _manifest_rows(compiled):
    with open(compiled["manifest"], encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _slot_tokens(compiled):
    if compiled.get("slots"):
        return shared_font.load_slot_assignments(compiled["slots"])
    return shared_font.SHARED_EXTENSION_TOKENS


def scan(compiled):
    enabled = set(BUILD_OPTIONS) - set(compiled.get("disabled_options", ()))
    ranges = range_characters(_slot_tokens(compiled))
    lookup = _build_dedupe_lookup(compiled["sheets"], conflicts={})
    findings = []
    for row in _manifest_rows(compiled):
        kind = (row.get("kind") or "").strip()
        text = set()
        if kind in FONT_KINDS or kind == "container":
            text = _text_characters(row["sheet"], lookup)
        for reason in row_findings(row, text, ranges, enabled):
            findings.append((row, reason))
    return findings, enabled


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "language", nargs="?", default="pt-BR",
        help="a locale under translations/, or a pack path")
    parser.add_argument(
        "--workspace", default=str(WORKSPACE_DIR),
        help="the workspace `vp2_translate.py generate` made "
             "(default: %(default)s)")
    parser.add_argument(
        "--only", help="comma-separated resource ids, as the build window "
                       "selects them")
    args = parser.parse_args(argv)

    pack = resolve_pack(args.language)
    if not workspace_is_ready(args.workspace):
        print("no generated workspace: run a build or "
              "`vp2_translate.py generate <usa-iso>` first", file=sys.stderr)
        return 2
    only = None
    if args.only:
        only = {item.strip() for item in args.only.split(",") if item.strip()}
    compiled = compile_build_workspace(args.workspace, pack, only=only)

    findings, enabled = scan(compiled)
    options = sorted(name for name in enabled
                     if name in ("item-order", "sealstone-order", "glyph-draw"))
    print("pack: %s   rows: %d   options on: %s"
          % (compiled.get("locale"), compiled["resources"],
             ", ".join(sorted(enabled)) or "(none)"))
    if options:
        print("options that themselves change a checked module: %s"
              % ", ".join(options))
    if not findings:
        print("no selected row changes a checked module; anti-cheat can be "
              "left off.")
        return 0
    print("anti-cheat is required by %d row(s):" % len(findings))
    for row, reason in findings:
        print("  %-9s %-6s  %s"
              % (row.get("kind"), row.get("resource"), reason))
    print("keep the anti-cheat option on for this selection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
