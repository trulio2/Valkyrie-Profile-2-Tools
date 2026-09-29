#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Check translated text without building a whole ISO.

    python test.py pt-BR 51        one scene
    python test.py pt-BR 10        one container
    python test.py pt-BR --all     everything this language translates

Says whether a build would accept the text, and how much room a scene has
left. The disc image is opened read-only. Whether a scene plays correctly
is only answered by playing it.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import io
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

from tools.scripts.paths import PROJECT_ROOT, WORKSPACE_DIR
from tools.scripts.public_build import (
    PACK_PROFILE, PackError, compile_build_workspace, ensure_glyph_pool,
    resolve_pack,
)
from tools.scripts.workspace_extract import _remembered_sources

KIND_FOR_CLASSIFICATION = {
    "local_font_dcms": "scene",
    "fontless_dcms_compatible": "fontless",
    "container_slz": "container",
    "container_zls": "container",
    "container_sle": "container",
}

FLAGS_FOR_KIND = {
    "container": {"shared-font-glyphs"},
    "fontless": {""},
    "scene": {"", "full-font"},
}


def _source_image(workspace):
    remembered = _remembered_sources(workspace)
    for region in ("usa", "japan"):
        path = remembered.get(region)
        if path and Path(path).is_file():
            return Path(path)
    raise PackError(
        "no disc image is recorded yet. Run a build once, or "
        "`python vp2_translate.py generate <usa-image.iso>`, and this will "
        "remember where it is.")


def _row_for(path, resource):
    """One row of a build profile or a compiled manifest, by resource."""
    with io.open(path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if (row.get("kind") or "").strip() in ("chapter-label", "misc",
                                                   "option"):
                continue
            if int(row["resource"], 0) == resource:
                return row
    return None


def _classification(iso, resource):
    from tools.scripts import resource_classify
    try:
        raw = iso.read_entry(resource)
    except Exception:
        return None
    try:
        return resource_classify.classify_entry(raw)[0]
    except Exception:
        return None


def check_profile_row(row, classification, resource, say=print):
    """Say whether the row describes the resource the disc actually holds."""
    problems = []
    expected = KIND_FOR_CLASSIFICATION.get(classification)
    if row is None:
        say("  not in this language's build-profile.csv, so a build skips it.")
        if expected:
            flags = sorted(FLAGS_FOR_KIND[expected] - {""}) or ["(none)"]
            say("  the disc says it is a %s resource, so its row would read "
                "kind=%s with flags %s."
                % (classification, expected, ", ".join(flags)))
        return ["absent from the build profile"]
    kind = (row.get("kind") or "").strip()
    flags = (row.get("flags") or "").strip()
    if expected and kind != expected:
        problems.append(
            "the row says kind=%s, but the disc holds a %s resource, which "
            "is built as kind=%s" % (kind or "(empty)", classification, expected))
    allowed = FLAGS_FOR_KIND.get(kind)
    if allowed is not None and flags not in allowed:
        wanted = sorted(allowed - {""}) or ["(none)"]
        problems.append(
            "the row has flags=%s; a %s row uses %s"
            % (flags or "(none)", kind, ", ".join(wanted)))
    return problems


class _Overlay:
    """A disc that accepts writes without touching the file behind it."""

    def __init__(self, iso, pristine=None):
        self._iso = iso
        self.pristine = iso if pristine is None else pristine
        self._written = {}
        self.is_in_memory = False

    def read_entry(self, resource):
        if resource in self._written:
            return self._written[resource]
        return self._iso.read_entry(resource)

    def write_entry(self, resource, data):
        self._written[resource] = bytes(data)

    def __getattr__(self, name):
        return getattr(self._iso, name)


def _manifest_rows(manifest_path, sheets):
    """Every row of the compiled manifest, pointed at the compiled sheets."""
    rows = []
    with io.open(manifest_path, encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row = dict(row)
            row["sheet"] = os.fspath(Path(sheets) / Path(row["sheet"]).name)
            rows.append(row)
    return rows


def _repack_wall(raw, packed, alignment=4):
    """Largest replacement this archive takes in place, less what it holds."""
    from tools.scripts.pk1_archive import repack_pk1_subresource

    def fits(size):
        try:
            repack_pk1_subresource(raw, "DCMS", b"\xff" * size, alignment)
        except (ValueError, KeyError, IndexError, struct.error):
            return False
        return True

    low, high = len(packed), len(raw)
    if not fits(low):
        return None
    while low < high:
        middle = (low + high + 1) // 2
        if fits(middle):
            low = middle
        else:
            high = middle - 1
    return low - len(packed)


def _headroom(raw, info):
    """Bytes of growth left, and whether that is exact or a floor."""
    spare = info.get("spare")
    if spare is not None:
        return spare, True
    packed = info.get("recompressed")
    slot = info.get("dcms_length")
    if not packed or not slot:
        return None, False
    if len(packed) <= slot:
        return slot - len(packed), False
    room = _repack_wall(raw, packed)
    return (room, True) if room is not None else (None, False)


def _shown(character):
    """A letter the terminal can print, however it is configured."""
    try:
        character.encode(sys.stdout.encoding or "ascii")
    except (UnicodeEncodeError, LookupError):
        from tools.scripts.vp2_cutscene_subtitles import ACCENTS
        base, mark = ACCENTS[character]
        return "%s with %s" % (base, mark.replace("_", " "))
    return "'%s'" % character


def _least_used_accent(rendered):
    """The accented letter this text leans on least, and how often."""
    from tools.scripts.vp2_cutscene_subtitles import ACCENTS

    text = "".join(part[-1] for part in rendered or ()
                   if part and isinstance(part[-1], str))
    counts = {}
    for character in text:
        if character in ACCENTS:
            counts[character] = counts.get(character, 0) + 1
    for character in sorted(counts, key=lambda value: (counts[value], value)):
        base = ACCENTS[character][0]
        if base in text:
            return character, base, counts[character]
    return None


def _sheet_without(character, base, sheet, directory):
    """A copy of *sheet* with one accented letter written plainly.

    The name is kept; a sheet is identified by it.
    """
    with io.open(sheet, encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        fields = reader.fieldnames or []
        rows = [dict(row) for row in reader]
    for row in rows:
        if row.get("translated"):
            row["translated"] = row["translated"].replace(character, base)
    target = Path(directory) / Path(sheet).name
    with io.open(target, "w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return target


def _lookup_without(character, base, lookup):
    """The dedupe lookup with one accented letter written plainly.

    A lookup also holds claims, whose value is ``True`` rather than text.
    """
    return {key: value.replace(character, base) if isinstance(value, str)
            else value
            for key, value in (lookup or {}).items()}


def _accent_saving(session, disc, row, info):
    """Measure what dropping one accented letter would buy this resource."""
    from tools.scripts import build_patchers

    choice = _least_used_accent(info.get("rendered"))
    packed = len(info.get("recompressed") or b"")
    if choice is None or not packed:
        return None
    character, base, used = choice
    with tempfile.TemporaryDirectory() as directory:
        plain_row = dict(row)
        plain_row["sheet"] = os.fspath(
            _sheet_without(character, base, row["sheet"], directory))
        lookup = _lookup_without(character, base, session.lookup)
        iso = _Overlay(disc, pristine=disc.pristine)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                plain = build_patchers.patch_scene_resource_in_memory(
                    iso, plain_row, primary_lookup=lookup,
                    reference=iso.pristine)
        except (ValueError, KeyError, IndexError):
            return None
    saved = packed - len(plain.get("recompressed") or b"")
    return (character, used, saved) if saved > 0 else None


class _Session:
    """What a build assembles once, so several checks can share it."""

    def __init__(self, language, workspace=None):
        from tools.scripts.vp2_build import _load_dedupe_lookup

        self.workspace = Path(workspace or WORKSPACE_DIR)
        self.pack = Path(resolve_pack(language))
        self.source = _source_image(self.workspace)
        with contextlib.redirect_stdout(io.StringIO()):
            compiled = compile_build_workspace(self.workspace, self.pack)
            pool = ensure_glyph_pool(self.source, self.workspace)
        os.environ["VP2_GLYPH_POOL"] = os.fspath(pool)
        self.sheets = Path(compiled["sheets"])
        self.manifest = Path(compiled["manifest"])
        self.rows = _manifest_rows(self.manifest, self.sheets)
        self.lookup = _load_dedupe_lookup(os.fspath(self.sheets))

    def resources(self):
        return [int(row["resource"], 0) for row in self.rows]

    @contextlib.contextmanager
    def disc(self):
        """The disc as a build has it once the shared font is installed."""
        from tools.scripts import build_patchers
        from tools.scripts import vp2_iso_buffer as iso_buffer

        with iso_buffer.IsoFile(os.fspath(self.source), "rb") as handle:
            base = _Overlay(handle)
            # A build installs the shared font before anything else.
            with contextlib.redirect_stdout(io.StringIO()):
                build_patchers.install_shared_font_in_memory(
                    base, self.rows, primary_lookup=self.lookup)
            yield base


def _refuse_past_the_ceiling(iso, resource, info):
    from tools.scripts import vp2_container_text as limits
    from tools.scripts import vp2_iso_space as iso_space

    patched = info.get("patched") if info else None
    if not patched:
        return
    _t, _e, content_end, _start, tail = iso_space._parse_archive(patched)
    if tail:
        return
    limits.check_scene_content_extent(
        resource, content_end, iso.pristine.entry_outer_allocation(resource))


def _read_back(iso, row, info, lookup):
    """Read the patched scene back the way a build's verify step does."""
    from tools.scripts import build_patchers

    grown = info and (info.get("grown_sectors")
                      or info.get("relocated_offset") is not None)
    if grown:
        iso.write_entry(int(row["resource"], 0), info["patched"])
    report = io.StringIO()
    try:
        with contextlib.redirect_stdout(report):
            build_patchers.verify_scene_in_memory(
                iso, row, iso.pristine, primary_lookup=lookup)
    except ValueError as exc:
        raise ValueError("\n".join([str(exc)] + report.getvalue().splitlines()))


def _check_one(session, disc, resource, say=print, brief=False):
    """Patch one resource against *disc* and say what a build would do."""
    from tools.scripts import build_patchers, vp2_container_text as limits

    started = time.perf_counter()
    row = _row_for(session.pack / PACK_PROFILE, resource)
    built = _row_for(session.manifest, resource) or row

    iso = _Overlay(disc, pristine=disc.pristine)
    classification = _classification(iso, resource)
    if not brief:
        problems = check_profile_row(row, classification, resource, say=say)
        for problem in problems:
            say("  ! %s" % problem)
    if row is None:
        if brief:
            say("  %-6d %-9s not in this language's build profile"
                % (resource, "?"))
        return 1

    sheet = session.sheets / Path(built["sheet"]).name
    if not sheet.is_file():
        if brief:
            say("  %-6d %-9s no translated records yet"
                % (resource, (built.get("kind") or "?").strip()))
        else:
            say("  no translated records reach this resource yet.")
        return 1

    patched = dict(built)
    patched["sheet"] = os.fspath(sheet)
    kind = (built.get("kind") or "").strip()
    info = None
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            if kind == "container":
                build_patchers.patch_container_resource_in_memory(
                    iso, patched, primary_lookup=session.lookup)
            elif kind in ("fontless", "worldmap"):
                build_patchers.patch_fontless_resource_in_memory(
                    iso, patched, primary_lookup=session.lookup)
            else:
                info = build_patchers.patch_scene_resource_in_memory(
                    iso, patched, primary_lookup=session.lookup,
                    reference=iso.pristine)
                _refuse_past_the_ceiling(iso, resource, info)
                _read_back(iso, patched, info, session.lookup)
        verdict, detail = "fits", ""
    except limits.StreamedNeighbourReclaimed as exc:
        verdict, detail = "reclaims", str(exc)
    except ValueError as exc:
        message = str(exc)
        if "open it 'r+b'" in message:
            verdict, detail = "fits", ""
        else:
            verdict, detail = "refused", message
    except Exception as exc:                     # pragma: no cover - defensive
        verdict, detail = "error", "%s: %s" % (type(exc).__name__, exc)

    seconds = time.perf_counter() - started
    spare, exact = None, False
    if verdict == "fits" and info:
        spare, exact = _headroom(iso.pristine.read_entry(resource), info)

    if brief:
        if verdict == "fits":
            room = ("" if spare is None else
                    "  %s%d byte(s) spare" % ("" if exact else ">=", spare))
            say("  %-6d %-9s ok%s" % (resource, kind or "?", room))
            return 0
        say("  %-6d %-9s NOT ACCEPTED (%s)" % (resource, kind or "?", verdict))
        for line in detail.splitlines()[:3]:
            say("           %s" % line.strip())
        return 2

    if verdict == "fits":
        say("  OK -- the text fits and a build would accept it.  (%.1fs)"
            % seconds)
        if spare is not None:
            say("  Room left: %s%d byte(s) before this scene needs more space."
                % ("" if exact else "at least ", spare))
        if info:
            saving = _accent_saving(session, disc, patched, info)
            if saving:
                character, used, saved = saving
                say("  Writing %s plainly, used %d time(s) here, would free "
                    "about %d more." % (_shown(character), used, saved))
        say("  Whether the scene plays correctly is still only answered by "
            "playing it.")
        return 0
    if verdict == "reclaims":
        say("  NOT ACCEPTED -- it only fits by rewriting part of the disc "
            "next to it.  (%.1fs)" % seconds)
        say("  Shorten the text, or use fewer different accented letters, "
            "and try again.")
        return 2
    say("  NOT ACCEPTED  (%.1fs)" % seconds)
    for line in detail.splitlines()[:4]:
        say("    %s" % line.strip())
    return 2


def check_resource(language, resource, workspace=None, say=print):
    """Patch one resource in memory and report what a build would do."""
    session = _Session(language, workspace)
    say("resource %d, %s" % (resource, session.pack.name))
    with session.disc() as disc:
        return _check_one(session, disc, resource, say=say)


def check_all(language, workspace=None, say=print):
    """Check everything this language translates, in one pass."""
    session = _Session(language, workspace)
    resources = session.resources()
    say("%s: %d resource(s)" % (session.pack.name, len(resources)))
    started = time.perf_counter()
    refused = []
    with session.disc() as disc:
        for resource in resources:
            if _check_one(session, disc, resource, say=say, brief=True):
                refused.append(resource)
    say("")
    say("%d of %d accepted in %.0fs"
        % (len(resources) - len(refused), len(resources),
           time.perf_counter() - started))
    if refused:
        say("not accepted: %s"
            % ", ".join(str(resource) for resource in refused))
        return 2
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Check translated text without a full build.")
    parser.add_argument("language", help="a locale under translations/")
    parser.add_argument("resource", nargs="?", type=lambda v: int(v, 0),
                        help="the scene or container number, e.g. 51 or 1197")
    parser.add_argument("--all", action="store_true",
                        help="check every resource this language translates")
    parser.add_argument("--workspace", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.all == (args.resource is not None):
        parser.error("give a resource number, or --all")
    try:
        if args.all:
            return check_all(args.language, args.workspace)
        return check_resource(args.language, args.resource, args.workspace)
    except PackError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
