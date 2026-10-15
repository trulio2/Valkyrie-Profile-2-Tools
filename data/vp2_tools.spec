# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
# -*- mode: python ; coding: utf-8 -*-

"""GUI build of all VP2 tools.

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
    ./dist/ValkyrieProfile2-Tools/ValkyrieProfile2-Tools.exe --self-check

Windows uses a one-folder bundle so launching does not extract and scan the
whole payload every time. Linux keeps the single executable distributed by
the release container.
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
from tools.scripts.release_version import write_windows_version   # noqa: E402

APP_NAME = "ValkyrieProfile2-Tools"
WINDOWS_ONE_FOLDER = sys.platform == "win32"
WINDOWS_VERSION = None
if WINDOWS_ONE_FOLDER:
    WINDOWS_VERSION = Path(workpath) / "windows-version.txt"
    write_windows_version(WINDOWS_VERSION)
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
    # These tools are loaded when their page, self-check, or frozen build
    # handoff is first used instead of delaying the initial window.
    hiddenimports=[
        "vp2_cheats", "vp2_glyphs", "vp2_translate", "vp2_voices",
        "tools.cheat_patcher.gui", "tools.glyph_patcher.gui",
        "tools.voice_patcher.gui",
    ],
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

# The window uses no Tcl time zones or translated Tk message catalogues. They
# are unnecessary in either layout and especially expensive in one-file mode.
_before = len(a.datas)
a.datas = [entry for entry in a.datas
           if not entry[0].replace("\\", "/").startswith(UNUSED_TCL_TREES)]
print("dropped %d unused Tcl/Tk data file(s)" % (_before - len(a.datas)))

pyz = PYZ(a.pure)
exe_contents = (([],) if WINDOWS_ONE_FOLDER
                else (a.binaries, a.datas, []))

exe = EXE(
    pyz,
    a.scripts,
    *exe_contents,
    name=APP_NAME,
    exclude_binaries=WINDOWS_ONE_FOLDER,
    icon=os.fspath(ICON) if ICON.is_file() else None,
    version=os.fspath(WINDOWS_VERSION) if WINDOWS_VERSION else None,
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

if WINDOWS_ONE_FOLDER:
    bundle = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        name=APP_NAME,
    )
