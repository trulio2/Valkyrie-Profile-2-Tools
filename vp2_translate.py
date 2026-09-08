#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Command-line interface for the VP2 translation tools."""

from __future__ import annotations

import argparse
import sys

from tools.scripts.paths import WORKSPACE_DIR
from tools.scripts.public_build import (
    build_iso, check_pack_profile, resolve_pack,
)
from tools.scripts.translation_pack import PackError, load_pack
from tools.scripts.workspace_extract import generate_workspace


DEFAULT_WORKSPACE = str(WORKSPACE_DIR)
DEFAULT_LANGUAGE = "pt-BR"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check", action="store_true",
        help="verify the translation runtime and data, then exit",
    )
    commands = parser.add_subparsers(dest="command")

    check = commands.add_parser("check-pack", help="validate one language pack")
    check.add_argument("language", metavar="LANGUAGE",
                       help="a locale under translations/, or a pack path")

    generate = commands.add_parser(
        "generate", help="generate the local reference and internal build state")
    generate.add_argument(
        "images", nargs="*", metavar="IMAGE",
        help="disc image(s): the USA release, and optionally the Japanese one "
             "for its original script",
    )
    generate.add_argument("--jp-image", help=argparse.SUPPRESS)
    generate.add_argument(
        "--workspace", default=DEFAULT_WORKSPACE,
        help="where to write it (default: %(default)s)",
    )

    build = commands.add_parser(
        "build", help="build a translated ISO from a generated workspace")
    build.add_argument("usa_image")
    build.add_argument(
        "language", nargs="?", default=DEFAULT_LANGUAGE, metavar="LANGUAGE",
        help="a locale under translations/, or a pack path "
             "(default: %(default)s)",
    )
    build.add_argument(
        "--workspace", default=DEFAULT_WORKSPACE,
        help="the workspace `generate` made (default: %(default)s)",
    )
    build.add_argument("--output")
    build.add_argument(
        "--no-verify", action="store_true",
        help="skip read-back verification (faster, less safe)",
    )
    build.add_argument(
        "--strict-extents", action="store_true",
        help="refuse a resource that runs past the furthest extent anyone "
             "has played, instead of recording it as a candidate and "
             "carrying on",
    )
    return parser


def run_internal_build(arguments: list[str]) -> int:
    """Run the private build driver used by the frozen unified application."""
    from tools.scripts import vp2_build

    saved = sys.argv
    sys.argv = ["vp2_build.py", *arguments]
    try:
        vp2_build.main()
    except SystemExit as exc:
        return int(exc.code or 0)
    finally:
        sys.argv = saved
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.self_check:
        from tools.scripts.public_release import self_check
        return self_check()
    if not args.command:
        parser.print_help(sys.stderr)
        return 2
    try:
        if args.command == "generate":
            images = list(args.images)
            if args.jp_image:
                images.append(args.jp_image)
            if not images:
                raise PackError("give at least one disc image")
            details = generate_workspace(images, args.workspace)
            print(
                "generated "
                f"{details['scene_sheets'] + details['container_sheets'] + details['chapter_sheets']} "
                "source sheet(s), "
                f"{details['scene_lines'] + details['container_lines'] + details['chapter_lines']} row(s)"
            )
        elif args.command == "build":
            output = build_iso(
                args.usa_image, args.language, workspace=args.workspace,
                output=args.output, no_verify=args.no_verify,
                strict_extents=args.strict_extents,
            )
            print(f"built {output}")
        else:
            pack = resolve_pack(args.language)
            rows = load_pack(pack)
            profile = check_pack_profile(pack)
            print(
                f"pack ok: {len(rows)} translated record(s), "
                f"{profile} resource(s) in the build profile"
            )
    except PackError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
