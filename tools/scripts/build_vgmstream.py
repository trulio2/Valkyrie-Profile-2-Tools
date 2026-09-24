# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Build the pinned minimal vgmstream CLI used for TAC movie extraction."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


COMMIT = "764c84c5048932054356f2ea67a71ea7673abc83"
REPOSITORY = "https://github.com/vgmstream/vgmstream.git"
ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "workspace" / "internal" / "vgmstream"
SOURCE = WORK / "source"
BUILD = WORK / "build"
DESTINATION = ROOT / "vendor" / "vgmstream"


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def git(*args):
    """Run Git against our generated checkout without changing user config."""
    run("git", "-c", "safe.directory=%s" % SOURCE, "-C", SOURCE, *args)


def main():
    if not SOURCE.is_dir():
        SOURCE.parent.mkdir(parents=True, exist_ok=True)
        run("git", "init", SOURCE)
        git("remote", "add", "origin", REPOSITORY)
    git("fetch", "--depth", "1", "origin", COMMIT)
    git("checkout", "--detach", "--force", "FETCH_HEAD")
    options = (
        "BUILD_CLI", "BUILD_WINAMP", "BUILD_XMPLAY", "BUILD_FB2K",
        "BUILD_V123", "BUILD_AUDACIOUS", "USE_FFMPEG", "USE_MPEG",
        "USE_VORBIS", "USE_G719", "USE_G7221", "USE_ATRAC9",
        "USE_CELT", "USE_SPEEX",
    )
    configure = [
        "cmake", "-S", SOURCE, "-B", BUILD,
        "-DCMAKE_BUILD_TYPE=Release", "-DBUILD_CLI=ON",
    ] + ["-D%s=OFF" % option for option in options if option != "BUILD_CLI"]
    run(*configure)
    run("cmake", "--build", BUILD, "--config", "Release", "--target", "vgmstream_cli")
    name = "vgmstream-cli.exe" if sys.platform == "win32" else "vgmstream-cli"
    matches = list(BUILD.rglob(name))
    if len(matches) != 1:
        raise RuntimeError("expected one %s build, found %d" % (name, len(matches)))
    DESTINATION.mkdir(parents=True, exist_ok=True)
    shutil.copy2(matches[0], DESTINATION / name)
    shutil.copy2(SOURCE / "COPYING", DESTINATION / "COPYING")
    print("built %s at %s" % (COMMIT[:12], DESTINATION / name))


if __name__ == "__main__":
    main()
