#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Command-line interface for the VP2 voice tool."""

from __future__ import annotations

import argparse
import sys

from tools.voice_patcher import audio, movie
from tools.voice_patcher.capacity import (
    load_capacity_csv, load_lezard_capacity_csv,
    write_capacity_csv, write_lezard_capacity_csv,
)
from tools.voice_patcher.build import (
    default_japanese_audio_output, default_patch_output, default_voice_root,
    extract_voices, import_japanese_audio, import_japanese_cutscene, patch_iso,
)
from tools.voice_patcher.layout import load_bank_map, load_unmapped_map
from tools.voice_patcher.mapping import (
    load_battle_groups, load_cutscene_map, map_existing_extraction,
)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check", action="store_true",
        help="verify this build carries everything it needs, then exit",
    )
    commands = parser.add_subparsers(dest="command")
    extract = commands.add_parser(
        "extract", help="extract every voice line from a USA or Japan ISO"
    )
    extract.add_argument("source", help="USA or Japan Valkyrie Profile 2 ISO")
    extract.add_argument(
        "-o", "--output", help="voice root (default: voices; en/jp is added)"
    )
    map_command = commands.add_parser(
        "map", help="write review CSVs for an existing en or jp extraction"
    )
    map_command.add_argument(
        "folder", help="extracted language folder containing manifest.csv"
    )
    capacity = commands.add_parser(
        "build-capacity",
        help="read USA/Japanese voice slots into the data lookup CSV",
    )
    capacity.add_argument("usa", help="USA Valkyrie Profile 2 ISO")
    capacity.add_argument("japan", help="Japanese Valkyrie Profile 2 ISO")
    capacity.add_argument(
        "-o", "--output",
        help="output CSV (default: opensource/data/voice-capacities.csv)",
    )
    lezard_capacity = commands.add_parser(
        "build-lezard-capacity",
        help="read USA/Japanese Lezard slots into the data lookup CSV",
    )
    lezard_capacity.add_argument("usa", help="USA Valkyrie Profile 2 ISO")
    lezard_capacity.add_argument("japan", help="Japanese Valkyrie Profile 2 ISO")
    lezard_capacity.add_argument(
        "-o", "--output",
        help="output CSV (default: opensource/data/lezard-capacities.csv)",
    )
    patch = commands.add_parser(
        "patch", help="patch identified WAV/TAC files into a new ISO"
    )
    patch.add_argument("source", help="USA or Japan Valkyrie Profile 2 ISO")
    patch.add_argument(
        "voices", help="folder containing replacement WAV or .laac TAC files"
    )
    patch.add_argument("-o", "--output", help="new ISO path")
    patch.add_argument(
        "--scope", choices=("all", "v1"), default="all",
        help="v1 selects cutscenes 0010-1389 plus alicia and lezard",
    )
    patch.add_argument(
        "--allow-overlong", action="store_true",
        help="deliberately trim WAVs that exceed their available game slots",
    )
    for scene in ("0010", "1323", "1337"):
        patch.add_argument(
            "--sync-" + scene, type=float, default=0.0, metavar="SECONDS",
            help=("shift scene %s movie WAV audio; negative starts earlier, "
                  "positive starts later" % scene),
        )
    import_jp = commands.add_parser(
        "import-japanese",
        help="create a Japanese-audio edition of a supported USA or PAL ISO",
    )
    import_jp.add_argument(
        "base", help="USA or PAL Valkyrie Profile 2 target ISO"
    )
    import_jp.add_argument("japan", help="Japanese Valkyrie Profile 2 ISO")
    import_jp.add_argument("-o", "--output", help="new ISO path")
    import_scene = commands.add_parser(
        "import-japanese-cutscene",
        help="copy one cutscene's complete Japanese voice banks into USA/PAL",
    )
    import_scene.add_argument("base", help="USA or PAL target ISO")
    import_scene.add_argument("japan", help="Japanese Valkyrie Profile 2 ISO")
    import_scene.add_argument("voice_scene", type=int, help="voice scene ID")
    import_scene.add_argument("-o", "--output", help="new ISO path")
    return parser


def self_check(stream=None):
    output = stream or sys.stdout
    notes, problems = [], []
    notes.append("frozen            : %s" % bool(getattr(sys, "frozen", False)))
    try:
        owners = load_bank_map()
        alternates = sum(owner.category == "alternate"
                         for owner in owners.values())
        notes.append(
            "known bank map    : %d bank(s), %d alternate/unmapped"
            % (len(owners), alternates)
        )
    except Exception as exc:
        problems.append("voice-bank map does not load: %r" % exc)
    try:
        capacities = load_capacity_csv()
        notes.append("voice capacities  : %d USA/Japanese slot(s)" %
                     len(capacities))
    except Exception as exc:
        problems.append("voice-capacity lookup does not load: %r" % exc)
    try:
        capacities = load_lezard_capacity_csv()
        notes.append("Lezard capacities: %d USA/Japanese slot(s)" %
                     len(capacities))
    except Exception as exc:
        problems.append("Lezard-capacity lookup does not load: %r" % exc)
    try:
        voices = load_unmapped_map()
        notes.append("unmapped voice map: %d sample(s)" % len(voices))
    except Exception as exc:
        problems.append("unmapped-voice map does not load: %r" % exc)
    try:
        cutscenes = load_cutscene_map()
        notes.append("cutscene voice map: %d slot(s)" % len(cutscenes))
    except Exception as exc:
        problems.append("cutscene voice map does not load: %r" % exc)
    try:
        battles, slots = load_battle_groups()
        notes.append(
            "battle voice map  : %d group(s), %d slot(s)"
            % (len(battles), len(slots))
        )
    except Exception as exc:
        problems.append("battle voice map does not load: %r" % exc)
    try:
        encoded = audio.encode_adpcm(b"\0\0" * 28)
        if len(encoded) != audio.FRAME:
            raise ValueError("unexpected encoded frame length")
        notes.append("audio codec       : %d Hz PCM/PS-ADPCM" % audio.SAMPLE_RATE)
    except Exception as exc:
        problems.append("audio codec self-test failed: %r" % exc)
    try:
        header = movie.decode_protected_movie(bytes.fromhex("77522267"))
        if header != b"\x00\x00\x01\xba":
            raise ValueError("protected movie header does not decode")
        notes.append(
            "movie audio codec : %d Hz PCM/TAC, reversible EE transform"
            % movie.SAMPLE_RATE
        )
        decoder = movie.find_vgmstream_cli()
        if decoder is not None:
            notes.append("TAC decoder       : %s" % decoder)
        elif getattr(sys, "frozen", False):
            problems.append("packaged TAC decoder is missing")
        else:
            notes.append("TAC decoder       : unavailable (.laac fallback)")
        encoder = movie.find_tac_encoder()
        matrices = [
            movie._runtime_root() / "data" / "tac" / name
            for name in ("analysis-pair.f32.zlib", "overlap.f32.zlib")
        ]
        if encoder is not None and all(path.is_file() for path in matrices):
            notes.append("TAC encoder       : %s" % encoder)
        elif getattr(sys, "frozen", False):
            problems.append("packaged TAC encoder or its analysis data is missing")
        else:
            notes.append("TAC encoder       : unavailable (WAV-to-TAC disabled)")
    except Exception as exc:
        problems.append("movie audio self-test failed: %r" % exc)
    for note in notes:
        print(note, file=output)
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
    try:
        if args.command == "extract":
            result = extract_voices(
                args.source, args.output or default_voice_root(), progress=print
            )
            print(
                "Extracted %d audio files from %d voice banks, including "
                "%d unmapped samples, %d battle samples, and %d movie "
                "track(s), to %s"
                % (result.clips, result.banks, result.unmapped_clips,
                   result.battle_clips, result.movie_tracks, result.output)
            )
        elif args.command == "map":
            cutscenes, battles = map_existing_extraction(args.folder)
            print(
                "Mapped %d cutscene clips and %d deduplicated battle groups "
                "under %s" % (cutscenes, battles, args.folder)
            )
        elif args.command == "build-capacity":
            output, rows = write_capacity_csv(
                args.usa, args.japan, args.output
            )
            print("Wrote %d USA/Japanese voice capacities to %s" %
                  (rows, output))
        elif args.command == "build-lezard-capacity":
            output, rows = write_lezard_capacity_csv(
                args.usa, args.japan, args.output
            )
            print("Wrote %d USA/Japanese Lezard capacities to %s" %
                  (rows, output))
        elif args.command == "patch":
            result = patch_iso(
                args.source, args.voices,
                args.output or default_patch_output(args.source),
                progress=print,
                allow_overlong=args.allow_overlong,
                scope=args.scope,
                movie_sync={
                    11: args.sync_0010,
                    20: args.sync_1323,
                    14: args.sync_1337,
                    15: args.sync_1337,
                    16: args.sync_1337,
                    18: args.sync_1337,
                },
            )
            print(
                "Patched %d voice clips; verified output: %s"
                % (len(result.replacements), result.output)
            )
        elif args.command == "import-japanese-cutscene":
            result = import_japanese_cutscene(
                args.base, args.japan, args.voice_scene,
                args.output or default_japanese_audio_output(args.base),
                progress=print,
            )
            print(
                "Imported cutscene %d using %d Japanese voice bank(s); "
                "verified output: %s"
                % (args.voice_scene, len(result.resources), result.output)
            )
        else:
            result = import_japanese_audio(
                args.base, args.japan,
                args.output or default_japanese_audio_output(args.base),
                progress=print,
            )
            print(
                "Imported %d complete Japanese audio resources; verified "
                "output: %s"
                % (len(result.resources), result.output)
            )
    except (OSError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
