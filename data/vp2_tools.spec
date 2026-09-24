# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
# -*- mode: python ; coding: utf-8 -*-

"""One-file GUI build of all three VP2 tools.

What goes in is the runtime plus the source-free tables a build reads: the
build profile, the structural tables, and every language pack. What stays out
is anything cut from a game image -- there is no workspace here, no glyph
pool, no compression cache. The user's first run makes those from the disc
they already own, which is what keeps this file publishable.

The result is a few megabytes. If it ever is not, something game-derived has
been added to the payload and should be taken back out.

    pyinstaller data/vp2_tools.spec \
        --workpath workspace/internal/build \
        --clean --noconfirm
    ./dist/ValkyrieProfile2-Tools --self-check
"""
import os
import sys
from pathlib import Path

# The spec lives with tracked structural data, one directory below the public
# project root. Keep payload and launcher resolution rooted at the project,
# not at the spec's storage directory.
ROOT = Path(SPECPATH).parent

# As the package, not as a loose module: everything under tools/scripts
# imports its siblings relatively, so a bare import of one of them fails
# before it reaches the first line that matters.
sys.path.insert(0, os.fspath(ROOT))
from tools.scripts.public_release import payload_members          # noqa: E402

APP_NAME = "ValkyrieProfile2-Tools"
ICON = ROOT / "images" / "vp2_release.ico"
ARTWORK = (
    "images/vp2_release.ico",
    "images/vp2_release.png",
    "images/vp2_release_bg.png",
)
UNUSED_TCL_TREES = ("_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/msgs")

members = payload_members(ROOT)
datas = [(os.fspath(source), os.path.dirname(name) or ".")
         for source, name in members]
datas += [(os.fspath(ROOT / name), os.path.dirname(name)) for name in ARTWORK
          if (ROOT / name).is_file()]
VGMSTREAM = ROOT / "vendor" / "vgmstream" / (
    "vgmstream-cli.exe" if sys.platform == "win32" else "vgmstream-cli"
)
VGMSTREAM_LICENSE = ROOT / "vendor" / "vgmstream" / "COPYING"
binaries = ([(os.fspath(VGMSTREAM), "vendor/vgmstream")]
            if VGMSTREAM.is_file() else [])
if VGMSTREAM_LICENSE.is_file():
    datas.append((os.fspath(VGMSTREAM_LICENSE), "vendor/vgmstream"))
TAC_ENCODER = ROOT / "vendor" / "tac_encoder" / (
    "vp2-tac-encode.exe" if sys.platform == "win32" else "vp2-tac-encode"
)
if TAC_ENCODER.is_file():
    binaries.append((os.fspath(TAC_ENCODER), "vendor/tac_encoder"))
print("payload: %d file(s), %.1f MB"
      % (len(members), sum(s.stat().st_size for s, _ in members) / 1e6))

a = Analysis(
    [os.fspath(ROOT / "vp2_tools.py")],
    pathex=[os.fspath(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Tk is the end-user front end. The numerical and scientific stacks are
    # still unreachable and each costs megabytes if a transitive import ever
    # makes PyInstaller discover it.
    excludes=[
        "turtle",
        "numpy", "scipy", "pandas", "matplotlib", "PIL",
        "pytest", "setuptools", "pip", "distutils",
        "test", "unittest.test",
        "curses", "readline",
        "http.server", "xmlrpc", "pydoc_data",
    ],
    noarchive=False,
    optimize=0,
)

# The window uses no Tcl time zones or translated Tk message catalogues.
# In one-file mode every bundled file is extracted and scanned on each start,
# so omitting these hundreds of unreachable files materially improves launch.
_before = len(a.datas)
a.datas = [entry for entry in a.datas
           if not entry[0].replace("\\", "/").startswith(UNUSED_TCL_TREES)]
print("dropped %d unused Tcl/Tk data file(s)" % (_before - len(a.datas)))

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=APP_NAME,
    icon=os.fspath(ICON) if ICON.is_file() else None,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX shrinks the file and gets it flagged: a compressed entry point is
    # what a good deal of malware looks like to a heuristic scanner, and an
    # unsigned binary from an unknown publisher has little goodwill to
    # spend. A few megabytes is the cheaper side of that trade.
    upx=False,
    # Double-click opens only the branded window. Headless commands attach to
    # an inherited pipe or the caller's console inside the launcher.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
