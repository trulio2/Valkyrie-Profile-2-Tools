# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Run the cheat patcher on an image the project page can only read in pieces."""

import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from . import build, catalog

BUNDLE = "python.zip"
_LIST_MODULES = (
    "import sys, tools.cheat_patcher.web\n"
    "for name, module in sorted(sys.modules.items()):\n"
    "    if name.split('.')[0] == 'tools' and getattr(module, '__file__', None):\n"
    "        print(module.__file__)\n")
CATALOG = "cheats.json"


class RandomAccess(io.RawIOBase):
    """A read-only file over ``read_at(offset, size)``."""

    def __init__(self, read_at, size):
        self._read_at = read_at
        self._size = size
        self._position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._position

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._position,
                io.SEEK_END: self._size}[whence]
        self._position = max(0, base + offset)
        return self._position

    def readinto(self, buffer):
        size = min(len(buffer), self._size - self._position)
        if size <= 0:
            return 0
        chunk = self._read_at(self._position, size)
        if hasattr(chunk, "to_bytes"):
            chunk = chunk.to_bytes()
        buffer[:len(chunk)] = chunk
        self._position += len(chunk)
        return len(chunk)


def plan(read_at, size, selected, corrupt_save, progress=None):
    """``(writes, patch names)`` for *selected* applied to the image."""
    handle = io.BufferedReader(RandomAccess(read_at, size), 1 << 20)
    result = build.plan_patches(handle, catalog.required_with(selected),
                                progress, corrupt_save=corrupt_save)
    return list(result.writes), [patch.name for patch in result.patches]


def catalog_entries():
    return [{"name": cheat.name, "title": cheat.title, "summary": cheat.summary,
             "requiresAntiCheat": cheat.requires_anti_cheat}
            for cheat in catalog.CHEATS]


def write_bundle(folder):
    """Write the Python the page runs, and the cheat list it shows."""
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    listing = subprocess.run(
        [sys.executable, "-c", _LIST_MODULES], cwd=root, check=True,
        capture_output=True, text=True).stdout
    files = sorted({Path(line).resolve() for line in listing.splitlines()
                    if line})
    with zipfile.ZipFile(out / BUNDLE, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    with open(out / CATALOG, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"antiCheat": catalog.ANTI_CHEAT,
                   "cheats": catalog_entries()}, handle, indent=2)
    return out / BUNDLE, len(files)
