#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Command-line interface for the VP2 glyph texture tool."""

from __future__ import annotations

import argparse
import sys

from tools.glyph_patcher import build
from tools.scripts import glyph_slots
from tools.scripts.paths import BUILD_DIR, output_root


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check", action="store_true",
        help="verify this build carries everything it needs, then exit",
    )
    commands = parser.add_subparsers(dest="command")
    patch = commands.add_parser(
        "patch", help="copy an ISO so it draws one texture per glyph, with "
                      "the anti-cheat off")
    patch.add_argument("source", help="USA ISO or a translated build of it")
    patch.add_argument("-o", "--output", help="output folder")
    extract = commands.add_parser(
        "extract", help="write every glyph a patched ISO draws as a PNG")
    extract.add_argument("source", help="an ISO made by the patch command")
    extract.add_argument("-o", "--output",
                         help="folder to create the glyph folder in")
    extract.add_argument("--replace", action="store_true",
                         help="replace the glyphs of an earlier run")
    dds = commands.add_parser(
        "dds", help="turn a folder of glyph-<hash>.png files into PCSX2 "
                    "textures")
    dds.add_argument("masters", help="folder of glyph-<hash>.png files")
    dds.add_argument("-o", "--output",
                     help="folder to create the texture folder in")
    dds.add_argument("--replace", action="store_true",
                     help="replace the textures of an earlier run")
    web = commands.add_parser(
        "web-patch", help="make the glyph patch the project page applies")
    web.add_argument(
        "--source", action="append", required=True, metavar="[NAME=]IMAGE",
        help="a USA image players may start from; repeat for each one")
    web.add_argument(
        "--output", default=str(BUILD_DIR / "web-patch"),
        help="folder that holds the web patches (default: %(default)s)")
    return parser


def web_patch(args):
    import shutil
    from pathlib import Path

    from tools import web_patch as patch
    from tools.app_meta import VERSION

    scratch = Path(BUILD_DIR) / "web-patch-glyphs"

    def make(source, options):
        target = build.patched_iso_path(source, scratch)
        target.unlink(missing_ok=True)
        return build.patch_iso(source, scratch, progress=print,
                               corrupt_save=options["corrupt-save"]).output

    root = Path(args.output).expanduser()
    try:
        manifest = patch.make_patch(
            patch.parse_sources(args.source), root / patch.GLYPHS, "",
            build=make, offered=["corrupt-save"], label="Glyph textures",
            version=VERSION, tool=patch.GLYPHS,
            output_name="Valkyrie Profile 2 - Silmeria (glyph textures).iso")
        patch.write_index(root)
        archive = patch.write_release_zip(root, patch.GLYPHS)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    for source in manifest["sources"]:
        download = sum(part["packedSize"] for part in source["parts"])
        print("%s: %d variant(s), %.1f MB to download"
              % (source["name"], len(source["variants"]), download / 1e6))
    return archive


def self_check(stream=None):
    output = stream or sys.stdout
    problems = []
    try:
        scenes = build.scene_resources()
        if not scenes:
            raise ValueError("no language pack lists a scene")
        print("scene fonts       : %d scene(s)" % len(scenes), file=output)
    except Exception as exc:
        problems.append("scene list does not load: %r" % exc)
    try:
        cell = bytes(glyph_slots.CELL_BYTES)
        png = build.glyph_textures.png(2, 1, b"\1\2\3\4\5\6\7\x08")
        if build.read_png_bytes(png) != (2, 1, b"\1\2\3\4\5\6\7\x08"):
            raise ValueError("PNG does not read back")
        if len(build.dds(4, 4, bytes(64))) != 128 + 64:
            raise ValueError("unexpected DDS size")
        if len(build.preview(cell)) != 4 * glyph_slots.SIZE ** 2:
            raise ValueError("unexpected preview size")
        print("image codecs      : PNG, uncompressed DDS", file=output)
    except Exception as exc:
        problems.append("image self-test failed: %r" % exc)
    for problem in problems:
        print("FAIL  %s" % problem, file=output)
    if problems:
        print("\n%d problem(s); this build should not ship" % len(problems),
              file=output)
        return 1
    print("\nself-check ok", file=output)
    return 0


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.self_check:
        return self_check()
    if args.command is None:
        _parser().print_help(sys.stderr)
        return 2
    folder = args.output or output_root()
    try:
        if args.command == "web-patch":
            print("release asset: %s" % web_patch(args))
            return 0
        if args.command == "patch":
            result = build.patch_iso(args.source, folder, progress=print)
        elif args.command == "extract":
            result = build.export_glyphs(args.source, folder, progress=print,
                                         replace=args.replace)
        else:
            result = build.write_dds(args.masters, folder, progress=print,
                                     replace=args.replace)
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    print("output: %s" % result.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
