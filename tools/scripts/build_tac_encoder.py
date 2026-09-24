# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Build the native VP2 TAC encoder against the pinned vgmstream source."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "tools" / "native" / "tac_encoder"
VGMSTREAM = ROOT / "workspace" / "internal" / "vgmstream" / "source"
BUILD = ROOT / "workspace" / "internal" / "tac_encoder"
DESTINATION = ROOT / "vendor" / "tac_encoder"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    if not (VGMSTREAM / "src" / "coding" / "libs" / "tac_lib.c").is_file():
        raise RuntimeError(
            "pinned vgmstream source is missing; run build_vgmstream.py first"
        )
    run(
        "cmake", "-S", SOURCE, "-B", BUILD,
        "-DCMAKE_BUILD_TYPE=Release", "-DVGMSTREAM_SOURCE=%s" % VGMSTREAM,
    )
    run("cmake", "--build", BUILD, "--config", "Release")
    name = "vp2-tac-encode.exe" if sys.platform == "win32" else "vp2-tac-encode"
    matches = list(BUILD.rglob(name))
    if len(matches) != 1:
        raise RuntimeError("expected one %s build, found %d" % (name, len(matches)))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    shutil.copy2(matches[0], DESTINATION / name)
    print("built TAC encoder at %s" % (DESTINATION / name))


if __name__ == "__main__":
    main()

