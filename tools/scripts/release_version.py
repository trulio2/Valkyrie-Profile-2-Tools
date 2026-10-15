# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Release identity checks and generated Windows metadata."""

from __future__ import annotations

import sys
from pathlib import Path

from tools.app_meta import PROJECT_NAME, PROJECT_URL, VERSION


def numeric_version(version: str) -> tuple[int, int, int, int]:
    pieces = version.split(".")
    if not 1 <= len(pieces) <= 4 or any(
            not piece.isdecimal() for piece in pieces):
        raise ValueError(f"invalid application version {version!r}")
    numbers = tuple(int(piece) for piece in pieces)
    if any(number > 65535 for number in numbers):
        raise ValueError(f"application version component is too large: {version}")
    return numbers + (0,) * (4 - len(numbers))


def windows_version_text() -> str:
    numbers = numeric_version(VERSION)
    file_version = ".".join(str(number) for number in numbers)
    return f"""# Generated from tools/app_meta.py. Do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numbers!r},
    prodvers={numbers!r},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'Valkyrie Profile 2 Tools contributors'),
          StringStruct('FileDescription', '{PROJECT_NAME}'),
          StringStruct('FileVersion', '{file_version}'),
          StringStruct('InternalName', 'ValkyrieProfile2-Tools'),
          StringStruct('LegalCopyright', 'Copyright 2026 Valkyrie Profile 2 Tools contributors'),
          StringStruct('OriginalFilename', 'ValkyrieProfile2-Tools.exe'),
          StringStruct('ProductName', '{PROJECT_NAME}'),
          StringStruct('ProductVersion', '{VERSION}'),
          StringStruct('Comments', '{PROJECT_URL}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_windows_version(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(windows_version_text(), encoding="utf-8", newline="\n")
    return target


def verify_release_tag(tag: str) -> None:
    expected = f"v{VERSION}"
    if tag != expected:
        raise ValueError(
            f"release tag {tag!r} does not match application version "
            f"{VERSION!r}; expected {expected!r}")


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m tools.scripts.release_version TAG",
              file=sys.stderr)
        return 2
    try:
        verify_release_tag(arguments[0])
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"release version {VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
