# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Turn translated ISOs into a patch the project page applies in a browser.

A patch supports one or more source images. For each, it holds one variant
per combination of build options: a list of copy operations plus the bytes
the source does not already have. ``["s", at, length]`` copies from the
player's image, ``["d", at, length]`` from the source's patch data. The
data is stored as gzip parts so the page can fetch and unpack it with what
a browser already provides. They are named ``.dat`` so a server does not
send them as gzip-encoded.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import mmap
import os
import shutil
import sys
import tomllib
from pathlib import Path

FORMAT = "vp2-web-patch-2"
OPTIONS = {"glyph-draw": True, "corrupt-save": False}
SECTOR = 2048
BLOCK = 1024 * 1024
CHUNK = 64
MIN_COPY = 2 * CHUNK
SHIFT_WINDOW = 256 * 1024
PROBE = 32
PROBES = (0, 680, 1360, 2016)
TRANSLATION = "translation"
GLYPHS = "glyphs"
PART_BYTES = 32 * 1024 * 1024
MANIFEST = "manifest.json"
INDEX = "index.json"


class PatchError(ValueError):
    """The images cannot be turned into a patch."""


def _sha256(view, step: int = 64 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    for at in range(0, len(view), step):
        digest.update(view[at:at + step])
    return digest.hexdigest()


class _Ops:
    """Operations in order, merging each into the last where it continues it."""

    def __init__(self) -> None:
        self.items: list[list] = []

    def add(self, kind: str, at: int, length: int) -> None:
        if length <= 0:
            return
        if self.items:
            last = self.items[-1]
            if last[0] == kind and last[1] + last[2] == at:
                last[2] += length
                return
        self.items.append([kind, at, length])

    def resume(self) -> int | None:
        """Where in the clean image the next byte would continue a copy."""
        if self.items and self.items[-1][0] == "s":
            return self.items[-1][1] + self.items[-1][2]
        return None


class _Data:
    """The new bytes, each repeated run stored once across all variants."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.stored: dict[bytes, int] = {}

    def add(self, chunk: bytes) -> int:
        key = hashlib.blake2b(chunk, digest_size=16).digest()
        at = self.stored.get(key)
        if at is None:
            at = self.stored[key] = len(self.buffer)
            self.buffer += chunk
        return at


def _index_sectors(source) -> dict[int, int]:
    index: dict[int, int] = {}
    for at in range(0, len(source) - SECTOR + 1, SECTOR):
        index.setdefault(hash(source[at:at + SECTOR]), at)
    return index


def _shifted(source, chunk: bytes, anchors: list[int | None]) -> list[int]:
    found: dict[int, None] = {}
    for anchor in dict.fromkeys(a for a in anchors if a is not None):
        low = max(0, anchor - SHIFT_WINDOW)
        high = min(len(source), anchor + SHIFT_WINDOW + len(chunk))
        for offset in PROBES:
            probe = chunk[offset:offset + PROBE]
            if len(probe) < PROBE or len(set(probe)) < 4:
                continue
            at = source.find(probe, low, high)
            if at >= offset:
                found[at - offset] = None
    return list(found)


def _copy_or_store(ops: _Ops, data: _Data, source, target_chunk: bytes,
                   references: list[int]) -> None:
    size = len(target_chunk)
    references = [r for r in references if 0 <= r and r + size <= len(source)]
    originals = [source[r:r + size] for r in references]
    runs: list[list] = []
    for at in range(0, size, CHUNK):
        piece = target_chunk[at:at + CHUNK]
        end = min(at + CHUNK, size)
        owner = next((r for r, original in zip(references, originals)
                      if original[at:end] == piece), None)
        if runs and runs[-1][0] == owner:
            runs[-1][2] = end
        else:
            runs.append([owner, at, end])
    for owner, start, end in runs:
        if owner is not None and end - start >= MIN_COPY:
            ops.add("s", owner + start, end - start)
        else:
            ops.add("d", data.add(target_chunk[start:end]), end - start)


def diff(source, target, data: _Data | None = None,
         index: dict[int, int] | None = None) -> tuple[list[list], bytes]:
    index = _index_sectors(source) if index is None else index
    ops = _Ops()
    data = _Data() if data is None else data
    position = 0
    size = len(target)
    while position < size:
        resume = ops.resume()
        if resume is not None:
            length = min(BLOCK, size - position, len(source) - resume)
            if length >= SECTOR and (source[resume:resume + length]
                                     == target[position:position + length]):
                ops.add("s", resume, length)
                position += length
                continue
        chunk = target[position:position + SECTOR]
        found = None
        candidates = [resume, position]
        for candidate in candidates:
            if (candidate is not None
                    and source[candidate:candidate + len(chunk)] == chunk):
                found = candidate
                break
        if found is None and len(chunk) == SECTOR:
            candidate = index.get(hash(chunk))
            if candidate is not None and source[candidate:candidate + SECTOR] == chunk:
                found = candidate
        if found is not None:
            ops.add("s", found, len(chunk))
        else:
            nearby = [resume, position]
            references = [r for r in nearby if r is not None]
            references += _shifted(source, chunk, nearby)
            _copy_or_store(ops, data, source, chunk, list(dict.fromkeys(references)))
        position += len(chunk)
    return ops.items, bytes(data.buffer)


def apply(source, data: bytes, ops: list[list]) -> bytes:
    out = bytearray()
    for kind, at, length in ops:
        out += (source if kind == "s" else data)[at:at + length]
    return bytes(out)


def _rebuilt_sha256(source, data: bytes, ops: list[list]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    view = memoryview(source)
    for kind, at, length in ops:
        piece = view[at:at + length] if kind == "s" else data[at:at + length]
        digest.update(piece)
        size += length
    return digest.hexdigest(), size


def pack_identity(pack: Path) -> tuple[str, str]:
    try:
        with open(pack / "pack.toml", "rb") as handle:
            meta = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PatchError(f"cannot read {pack / 'pack.toml'}: {exc}") from exc
    locale = str(meta.get("locale") or pack.name)
    return locale, str(meta.get("name") or locale)


def write_release_zip(root: Path, folder: str) -> Path:
    """Zip one patch under its own folder, for a release asset."""
    archive = shutil.make_archive(
        str(root / f"web-patch-{folder}"), "zip", root_dir=root,
        base_dir=folder)
    return Path(archive)


def parse_sources(values: list[str]) -> list[tuple[str, str]]:
    """``(name, path)`` for each ``[NAME=]IMAGE``; each must be the USA disc."""
    from tools.scripts.disc_identity import DiscError, identify

    sources = []
    for value in values:
        name, separator, path = value.partition("=")
        if not separator or not Path(path).is_file():
            name, path = Path(value).stem, value
        try:
            if identify(path)[1] != "usa":
                raise PatchError(f"{path} is not the USA release")
        except DiscError as exc:
            raise PatchError(str(exc)) from exc
        sources.append((name, path))
    return sources


def option_sets(offered) -> list[dict[str, bool]]:
    """Every combination of the *offered* options."""
    names = [name for name in OPTIONS if name in offered]
    return [dict(zip(names, values))
            for values in itertools.product((True, False), repeat=len(names))]


def _describe(options: dict[str, bool]) -> str:
    return ", ".join(f"{name} {'on' if value else 'off'}"
                     for name, value in options.items()) or "the build"


def _write_parts(out: Path, prefix: str, data: bytes) -> list[dict]:
    parts = []
    for number, at in enumerate(range(0, max(len(data), 1), PART_BYTES)):
        raw = data[at:at + PART_BYTES]
        packed = gzip.compress(raw, compresslevel=9, mtime=0)
        name = f"{prefix}data-{number:02d}.dat"
        (out / name).write_bytes(packed)
        parts.append({
            "file": name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "packedSize": len(packed),
            "packedSha256": hashlib.sha256(packed).hexdigest(),
        })
    return parts


def _source_variants(source_path, variants, build, log) -> tuple[dict, bytes]:
    with open(source_path, "rb") as source_file:
        if os.fstat(source_file.fileno()).st_size == 0:
            raise PatchError(f"{source_path} is empty")
        source = mmap.mmap(source_file.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            log(f"hashing {Path(source_path).name}")
            entry = {"size": len(source),
                     "sha256": _sha256(memoryview(source)), "variants": []}
            index = _index_sectors(source)
            data = _Data()
            for options in variants:
                target_path = build(source_path, options)
                with open(target_path, "rb") as target_file:
                    target = mmap.mmap(target_file.fileno(), 0,
                                       access=mmap.ACCESS_READ)
                    try:
                        log(f"comparing {_describe(options)}")
                        target_sha = _sha256(memoryview(target))
                        ops, _ = diff(source, target, data, index)
                        rebuilt, size = _rebuilt_sha256(
                            source, bytes(data.buffer), ops)
                        if rebuilt != target_sha or size != len(target):
                            raise PatchError(
                                f"the patch does not rebuild {target_path}")
                        entry["variants"].append({
                            "options": options,
                            "target": {"size": len(target),
                                       "sha256": target_sha},
                            "ops": ops,
                        })
                    finally:
                        target.close()
        finally:
            source.close()
    return entry, bytes(data.buffer)


def make_patch(sources, output_dir: str | os.PathLike[str], language: str, *,
               build, offered=tuple(OPTIONS), label: str | None = None,
               version: str = "", output_name: str | None = None,
               tool: str = TRANSLATION, log=print) -> dict:
    out = Path(output_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    manifest = {
        "format": FORMAT,
        "tool": tool,
        "language": language,
        "label": label or language,
        "version": version,
        "outputName": (output_name
                       or f"Valkyrie Profile 2 - Silmeria ({language}).iso"),
        "options": [{"id": name, "default": OPTIONS[name]}
                    for name in OPTIONS if name in offered],
        "sources": [],
    }
    for number, (name, path) in enumerate(sources):
        entry, data = _source_variants(path, option_sets(offered), build, log)
        manifest["sources"].append({
            "name": name, **entry, "dataSize": len(data),
            "parts": _write_parts(out, f"s{number}-", data)})
    with open(out / MANIFEST, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, separators=(",", ":"))
    return manifest


def write_index(root: str | os.PathLike[str]) -> list[dict]:
    """List every patch under *root* in ``index.json``."""
    base = Path(root)
    entries = []
    for path in sorted(base.glob(f"*/{MANIFEST}")):
        with open(path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        if manifest.get("format") != FORMAT:
            continue
        sources = manifest["sources"]
        entries.append({
            "tool": manifest.get("tool", TRANSLATION),
            "language": manifest["language"],
            "label": manifest["label"],
            "version": manifest["version"],
            "path": f"{path.parent.name}/{MANIFEST}",
            "download": max(sum(part["packedSize"] for part in source["parts"])
                            for source in sources),
            "outputSize": max(variant["target"]["size"] for source in sources
                              for variant in source["variants"]),
        })
    with open(base / INDEX, "w", encoding="utf-8", newline="\n") as handle:
        json.dump({"format": FORMAT, "patches": entries}, handle, indent=2)
    return entries


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="list the web patches in a folder")
    parser.add_argument("command", choices=["index"])
    parser.add_argument("root")
    args = parser.parse_args(argv)
    entries = write_index(args.root)
    print(f"{len(entries)} patch(es) listed in {Path(args.root) / INDEX}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
