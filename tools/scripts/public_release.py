# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""What a packaged release carries, and how it proves it carries it."""
from __future__ import annotations

import os
from pathlib import Path

from .paths import (
    BUILD_DIR, DATA_DIR, PROJECT_ROOT, FROZEN, WORKSPACE_DIR, output_root,
)
from .translation_pack import is_language_pack

PAYLOAD_TREES = (
    ("data", "**/*.csv"),
    ("data", "**/*.md"),
    ("translations", "**/*.csv"),
    ("translations", "**/*.toml"),
    ("tools/scripts", "*.csv"),
)

PAYLOAD_EXCLUDED: frozenset[str] = frozenset()
RELEASE_ARTWORK = (
    "images/vp2_release.ico",
    "images/vp2_release.png",
    "images/vp2_release_bg.png",
)


def payload_members(root: str | os.PathLike[str] | None = None):
    """``[(source path, name inside the bundle)]`` for the whole payload."""
    base = Path(root or PROJECT_ROOT)
    members = []
    seen = set()
    for name, pattern in PAYLOAD_TREES:
        for path in sorted((base / name).glob(pattern)):
            if not path.is_file() or path.name in PAYLOAD_EXCLUDED:
                continue
            inside = path.relative_to(base / name).parts
            if (name == "translations" and len(inside) > 1
                    and not is_language_pack(base / name / inside[0])):
                continue
            relative = path.relative_to(base).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            members.append((path, relative))
    return members


def _packs(root: Path):
    directory = root / "translations"
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir()
                  if is_language_pack(p))


def self_check(stream=None) -> int:
    """Load what a build loads and report it.  Non-zero means do not ship."""
    import sys

    out = stream or sys.stdout
    notes: list[str] = []
    problems: list[str] = []

    notes.append(f"frozen            : {FROZEN}")
    notes.append(f"payload root      : {PROJECT_ROOT}")
    notes.append(f"workspace         : {WORKSPACE_DIR}")
    notes.append(f"working state     : {BUILD_DIR}")
    notes.append(f"iso written to    : {output_root()}")

    tables = sorted(p.name for p in DATA_DIR.glob("*.csv")) if DATA_DIR.is_dir() else []
    notes.append(f"structural tables : {len(tables)}")
    for required in ("menu-layout.csv", "record-limits.csv",
                     "authored-marks.csv", "authored-glyphs.csv",
                     "einherjar-rosters.csv"):
        if required not in tables:
            problems.append(f"missing structural table: {required}")

    packs = _packs(PROJECT_ROOT)
    if not packs:
        problems.append("no language pack is bundled")
    try:
        from .public_build import check_pack_profile
    except Exception as exc:                     # pragma: no cover - packaging
        check_pack_profile = None
        problems.append(f"the pack loader does not import: {exc!r}")
    for name in packs:
        if check_pack_profile is None:
            break
        try:
            resources = check_pack_profile(PROJECT_ROOT / "translations" / name)
        except Exception as exc:
            problems.append(f"language pack {name}: {exc}")
        else:
            notes.append(
                f"language pack     : {name}, {resources} resource(s)")

    artwork = [name for name in RELEASE_ARTWORK
               if (PROJECT_ROOT / name).is_file()]
    notes.append("release artwork   : " + (", ".join(artwork) or "none"))
    missing_artwork = sorted(set(RELEASE_ARTWORK) - set(artwork))
    if missing_artwork:
        problems.append("missing release artwork: " +
                        ", ".join(missing_artwork))

    try:
        import tkinter
        interpreter = tkinter.Tcl()
        notes.append(
            f"window runtime    : Tcl {interpreter.eval('info patchlevel')}")
        if FROZEN:
            for relative in ("_tcl_data/init.tcl", "_tk_data/tk.tcl"):
                if not (PROJECT_ROOT / relative).is_file():
                    problems.append(f"missing window runtime file: {relative}")
    except Exception as exc:                     # pragma: no cover - packaging
        problems.append(f"the Tk window runtime does not initialize: {exc!r}")

    try:
        from . import vp2_cutscene_subtitles as subtitles
        marks = len(subtitles.ACCENT_MARKS)
        notes.append(f"accent marks      : {marks}")
        if not marks:
            problems.append("no accent marks loaded")
    except Exception as exc:                     # pragma: no cover - packaging
        problems.append(f"the runtime does not import: {exc!r}")

    try:
        from . import vp2_build  # noqa: F401
        from . import public_build  # noqa: F401
        notes.append("runtime           : imports")
    except Exception as exc:                     # pragma: no cover - packaging
        import traceback
        problems.append("the build driver does not import: %r\n%s"
                        % (exc, traceback.format_exc().rstrip()))

    for line in notes:
        print(line, file=out)
    for line in problems:
        print(f"FAIL  {line}", file=out)
    if problems:
        print(f"\n{len(problems)} problem(s); this build should not ship",
              file=out)
        return 1
    print("\nself-check ok", file=out)
    return 0
