# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Transactional extraction and fixed-slot ISO voice replacement."""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import shutil
import struct

from . import audio, layout, mapping, movie
from .capacity import (
    load_alicia_field_aliases, load_capacity_csv, load_lezard_capacity_csv,
)
from .layout import (
    JAPAN_BOOT, JAPANESE_AUDIO_TARGET_BOOTS, SUPPORTED_BOOTS,
    VOICE_BANKS, VOICE_SOURCE_BOOTS, entry_span,
    battle_filename, battle_signature, decode_battle_entry,
    encode_battle_entry, exported_filename, field_filename,
    lezard_filename, load_bank_map, load_unmapped_map, parse_bank,
    parse_battle_filename, parse_exported_filename, parse_field_filename,
    parse_lezard_filename, parse_standalone, parse_unmapped_filename,
    read_index, unmapped_filename,
)
from ..cheat_patcher import battle_overlay
from ..scripts import (
    disc_identity, package_archive, pk1_archive, protected_package, slz,
    slz_compress, vp2_dcms, vp2_iso_space,
)
from ..scripts.paths import PROJECT_ROOT, output_root


COPY_CHUNK = 8 * 1024 * 1024
PROTECTED_STREAM_MAGIC = bytes.fromhex("77522267")
GLOBAL_BATTLE_RESOURCE = 1781
BATTLE_RESULT_ASSET_FLAG = 0x5400
FIELD_AUDIO_MARKERS = (0x0A80, 0x0A83)
LEZARD_AUDIO_MARKERS = (0x0A89, 0x0A9C)
MANIFEST_FIELDS = (
    "kind", "region", "resource", "voice_scene", "bank", "sub", "entry",
    "sample", "zone", "clip_id", "relative_path", "slot_bytes",
    "max_seconds", "seconds", "target_rms", "peak", "voiced_pct",
    "silent", "sha256",
)


@dataclass(frozen=True)
class ExtractionResult:
    output: Path
    region: str
    banks: int
    clips: int
    mapped_banks: int
    unmapped_clips: int
    battle_clips: int
    movie_tracks: int


@dataclass(frozen=True)
class Replacement:
    path: Path
    kind: str
    clip_id: int
    slot_bytes: int
    bank: int | None = None
    sub: int | None = None
    entry: int | None = None
    sample: int | None = None
    zone: int | None = None
    truncated: bool = False


@dataclass(frozen=True)
class PatchResult:
    output: Path
    region: str
    replacements: tuple[Replacement, ...]


@dataclass(frozen=True)
class ImportResult:
    output: Path
    resources: tuple[int, ...]
    appended_sectors: int


def default_voice_root():
    """Repository-local output in source, current-directory output frozen."""
    if getattr(__import__("sys"), "frozen", False):
        return Path.cwd() / "voices"
    return PROJECT_ROOT / "voices"


def default_patch_output(source):
    source = Path(source)
    return output_root() / (source.stem + "-voice-patched.iso")


def default_japanese_audio_output(source):
    source = Path(source)
    return output_root() / (source.stem + "-japanese-audio.iso")


def describe_disc(path):
    """Return ``(release code, boot)`` for every supported source or target."""
    try:
        boot, _region = disc_identity.identify(path)
    except disc_identity.DiscError as exc:
        raise ValueError(str(exc)) from exc
    if boot not in SUPPORTED_BOOTS:
        raise ValueError(
            "unsupported disc %s; select a supported Valkyrie Profile 2 "
            "USA, PAL, or Japanese release" % boot
        )
    return SUPPORTED_BOOTS[boot], boot


def _validated_source(path, allowed_boots=None, purpose="this operation"):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("source ISO does not exist: %s" % path)
    region, boot = describe_disc(path)
    if allowed_boots is not None and boot not in allowed_boots:
        raise ValueError(
            "%s does not support %s (%s)" % (purpose, path.name, boot)
        )
    with path.open("rb") as handle:
        read_index(handle)
    return path, region, boot


def _read_bank(handle, table, total, bank):
    offset, length = entry_span(table, total, bank)
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise ValueError("voice bank %d extends past the ISO" % bank)
    return offset, data


def _read_entry(handle, table, total, entry):
    offset, length = entry_span(table, total, entry)
    handle.seek(offset)
    data = handle.read(length)
    if len(data) != length:
        raise ValueError("voice entry %d extends past the ISO" % entry)
    return offset, data


def _battle_entries(handle, table, total):
    """Discover encrypted SEQW entries by the loader's signature table."""
    entries = []
    for entry in range(total):
        if not table[total + entry]:
            continue
        offset, length = entry_span(table, total, entry)
        handle.seek(offset)
        if battle_signature(handle.read(4)) is not None:
            entries.append(entry)
    return entries


def extract_voices(source, output=None, progress=None, bank_map=None,
                   unmapped_map=None):
    """Decode mapped cutscenes and language-dependent unmapped samples."""
    say = progress or (lambda _message: None)
    source, region, boot = _validated_source(
        source, VOICE_SOURCE_BOOTS, "voice extraction"
    )
    root = Path(output).expanduser().resolve() if output else default_voice_root()
    destination = root / region
    partial = root / (region + ".partial")
    if destination.exists():
        raise ValueError(
            "voice output already exists; move or remove it first: %s"
            % destination
        )
    if partial.exists():
        raise ValueError(
            "partial voice output already exists; remove it first: %s" % partial
        )
    owners = load_bank_map(bank_map)
    unmapped_voices = load_unmapped_map(unmapped_map)
    alicia_aliases = load_alicia_field_aliases()
    alicia_canonical = frozenset(alicia_aliases)
    alicia_targets = frozenset(
        target for targets in alicia_aliases.values() for target in targets
    )
    by_entry = {}
    for voice in unmapped_voices.values():
        by_entry.setdefault(voice.entry, {})[voice.sample] = voice
    rows = []
    mapped = 0
    root.mkdir(parents=True, exist_ok=True)
    partial.mkdir()

    def emit(kind, folder, filename, clip_id, zone, pcm, slot_bytes,
             entry="", sample="", bank="", sub=""):
        folder.mkdir(exist_ok=True)
        target = folder / filename
        audio.write_wav(target, pcm)
        peak, rms, voiced = audio.statistics(pcm)
        rows.append({
            "kind": kind,
            "region": region,
            "resource": "",
            "voice_scene": "",
            "bank": bank,
            "sub": sub,
            "entry": entry,
            "sample": sample,
            "zone": zone,
            "clip_id": "%04x" % clip_id,
            "relative_path": target.relative_to(partial).as_posix(),
            "slot_bytes": slot_bytes,
            "max_seconds": "%.4f" % (
                slot_bytes // audio.FRAME
                * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
            ),
            "seconds": "%.4f" % (len(pcm) // 2 / audio.SAMPLE_RATE),
            "target_rms": int(rms),
            "peak": peak,
            "voiced_pct": "%.1f" % (100 * voiced),
            "silent": "yes" if rms == 0 else "",
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        })

    try:
        with source.open("rb") as handle:
            total, table = read_index(handle)
            battle_entries = _battle_entries(handle, table, total)
            protected_entries = _protected_stream_entries(
                handle, table, total
            )
            extraction_steps = (
                len(VOICE_BANKS) + len(by_entry) + len(battle_entries) +
                len(protected_entries)
            )
            for position, bank in enumerate(VOICE_BANKS, 1):
                _offset, bank_data = _read_bank(handle, table, total, bank)
                clips = parse_bank(bank_data)
                owner = owners.get(bank)
                if owner:
                    if len(clips) != owner.slot_count:
                        raise ValueError(
                            "voice bank %d has %d clips, but its scene map "
                            "expects %d" % (bank, len(clips), owner.slot_count)
                        )
                    if owner.category == "cutscene":
                        folder = partial / str(owner.resource)
                        if owner.resource == 1337:
                            folder /= "unused"
                        mapped += 1
                    elif owner.resource is not None:
                        folder = partial / str(owner.resource) / "alternate-takes"
                    else:
                        folder = partial / "unmapped" / "alternate-takes"
                else:
                    folder = partial / "unmapped"
                folder.mkdir(parents=True, exist_ok=True)
                for clip in clips:
                    payload_start = clip.sub_offset + clip.payload_offset
                    payload = bank_data[
                        payload_start:payload_start + clip.payload_length
                    ]
                    pcm = audio.decode_adpcm(payload)
                    name = exported_filename(bank, clip)
                    target = folder / name
                    audio.write_wav(target, pcm)
                    peak, rms, voiced = audio.statistics(pcm)
                    relative = target.relative_to(partial).as_posix()
                    rows.append({
                        "kind": owner.category if owner else "unmapped-bank",
                        "region": region,
                        "resource": (
                            owner.resource
                            if owner and owner.resource is not None
                            else ""
                        ),
                        "voice_scene": (
                            owner.voice_scene
                            if owner and owner.voice_scene is not None
                            else ""
                        ),
                        "bank": bank,
                        "sub": clip.sub_index,
                        "entry": "",
                        "sample": "",
                        "zone": "",
                        "clip_id": "%04x" % clip.clip_id,
                        "relative_path": relative,
                        "slot_bytes": clip.payload_length,
                        "max_seconds": "%.4f" % (
                            clip.payload_length // audio.FRAME
                            * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                        ),
                        "seconds": "%.4f" % (
                            len(pcm) // 2 / audio.SAMPLE_RATE
                        ),
                        "target_rms": int(rms),
                        "peak": peak,
                        "voiced_pct": "%.1f" % (100 * voiced),
                        "silent": "yes" if rms == 0 else "",
                        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                    })
                say(
                    "extract: bank %d (%d/%d), %d clip(s)"
                    % (bank, position, extraction_steps, len(clips))
                )
            unmapped_folder = partial / "unmapped"
            unmapped_folder.mkdir(exist_ok=True)
            unmapped_count = 0
            for position, (entry, expected) in enumerate(
                    sorted(by_entry.items()), 1):
                _offset, entry_data = _read_entry(handle, table, total, entry)
                clips = parse_standalone(entry_data)
                indexed = {clip.sample_index: clip for clip in clips}
                for sample_index, voice in sorted(expected.items()):
                    clip = indexed.get(sample_index)
                    if clip is None:
                        raise ValueError(
                            "unmapped voice entry %d has no sample %d"
                            % (entry, sample_index)
                        )
                    if (clip.clip_id, clip.zone) != (
                            voice.clip_id, voice.zone):
                        raise ValueError(
                            "unmapped voice entry %d sample %d is %04x/%d, "
                            "but its map expects %04x/%d"
                            % (entry, sample_index, clip.clip_id, clip.zone,
                               voice.clip_id, voice.zone)
                        )
                    payload = entry_data[
                        clip.payload_offset:
                        clip.payload_offset + clip.payload_length
                    ]
                    pcm = audio.decode_adpcm(payload)
                    if voice.resource is not None:
                        folder = partial / str(voice.resource)
                        if voice.resource == 1337:
                            folder /= "unused"
                        folder.mkdir(parents=True, exist_ok=True)
                    else:
                        folder = unmapped_folder
                    target = folder / unmapped_filename(entry, clip)
                    audio.write_wav(target, pcm)
                    peak, rms, voiced = audio.statistics(pcm)
                    relative = target.relative_to(partial).as_posix()
                    rows.append({
                        "kind": "unmapped",
                        "region": region,
                        "resource": (
                            voice.resource
                            if voice.resource is not None
                            else ""
                        ),
                        "voice_scene": "",
                        "bank": "",
                        "sub": "",
                        "entry": entry,
                        "sample": sample_index,
                        "zone": clip.zone,
                        "clip_id": "%04x" % clip.clip_id,
                        "relative_path": relative,
                        "slot_bytes": clip.payload_length,
                        "max_seconds": "%.4f" % (
                            clip.payload_length // audio.FRAME
                            * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                        ),
                        "seconds": "%.4f" % (
                            len(pcm) // 2 / audio.SAMPLE_RATE
                        ),
                        "target_rms": int(rms),
                        "peak": peak,
                        "voiced_pct": "%.1f" % (100 * voiced),
                        "silent": "yes" if rms == 0 else "",
                        "sha256": hashlib.sha256(
                            target.read_bytes()
                        ).hexdigest(),
                    })
                    unmapped_count += 1
                say(
                    "extract: unmapped entry %d (%d/%d), %d sample(s)"
                    % (entry, len(VOICE_BANKS) + position,
                       extraction_steps, len(expected))
                )
            battle_folder = partial / "battle"
            battle_folder.mkdir(exist_ok=True)
            battle_count = 0
            for position, entry in enumerate(battle_entries, 1):
                _offset, stored = _read_entry(handle, table, total, entry)
                clear, _signature = decode_battle_entry(stored)
                clips = parse_standalone(clear)
                for clip in clips:
                    payload = clear[
                        clip.payload_offset:
                        clip.payload_offset + clip.payload_length
                    ]
                    pcm = audio.decode_adpcm(payload)
                    target = battle_folder / battle_filename(entry, clip)
                    audio.write_wav(target, pcm)
                    peak, rms, voiced = audio.statistics(pcm)
                    relative = target.relative_to(partial).as_posix()
                    rows.append({
                        "kind": "battle",
                        "region": region,
                        "resource": "",
                        "voice_scene": "",
                        "bank": "",
                        "sub": "",
                        "entry": entry,
                        "sample": clip.sample_index,
                        "zone": clip.zone,
                        "clip_id": "%04x" % clip.clip_id,
                        "relative_path": relative,
                        "slot_bytes": clip.payload_length,
                        "max_seconds": "%.4f" % (
                            clip.payload_length // audio.FRAME
                            * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                        ),
                        "seconds": "%.4f" % (
                            len(pcm) // 2 / audio.SAMPLE_RATE
                        ),
                        "target_rms": int(rms),
                        "peak": peak,
                        "voiced_pct": "%.1f" % (100 * voiced),
                        "silent": "yes" if rms == 0 else "",
                        "sha256": hashlib.sha256(
                            target.read_bytes()
                        ).hexdigest(),
                    })
                    battle_count += 1
                say(
                    "extract: battle entry %d (%d/%d), %d sample(s)"
                    % (entry, len(VOICE_BANKS) + len(by_entry) + position,
                       extraction_steps, len(clips))
                )
            field_folder = partial / "field"
            alicia_folder = partial / "alicia"
            lezard_folder = partial / "lezard"
            for entry in _scene_audio_entries(handle, table, total):
                _offset, entry_data = _read_entry(handle, table, total, entry)
                groups = _indexed_audio_groups(entry_data)
                if _has_markers(
                        {clip for group in groups
                         for clip, _zone in group[3]},
                        FIELD_AUDIO_MARKERS):
                    for group_index, group in enumerate(groups):
                        for clip in parse_standalone(group[4]):
                            payload = group[4][
                                clip.payload_offset:
                                clip.payload_offset + clip.payload_length
                            ]
                            identity = (
                                entry, group_index, clip.sample_index,
                                clip.clip_id, clip.zone,
                            )
                            if identity in alicia_targets:
                                if identity not in alicia_canonical:
                                    continue
                                folder = alicia_folder
                            else:
                                folder = field_folder
                            emit(
                                "field", folder,
                                field_filename(entry, group_index, clip),
                                clip.clip_id, clip.zone,
                                audio.decode_adpcm(payload),
                                clip.payload_length, entry=entry,
                                sample=clip.sample_index, sub=group_index,
                            )
                found = _streamed_audio_groups(entry_data)
                if found is not None:
                    _tail_start, tail_groups = found
                    if _has_markers(
                            {clip.clip_id for _position, _payload, clips
                             in tail_groups for clip in clips},
                            LEZARD_AUDIO_MARKERS):
                        for group_index, (position, payload, clips) in enumerate(
                                tail_groups):
                            for clip in clips:
                                sample = payload[
                                    clip.payload_offset:
                                    clip.payload_offset + clip.payload_length
                                ]
                                emit(
                                    "lezard", lezard_folder,
                                    lezard_filename(entry, group_index, clip),
                                    clip.clip_id, clip.zone,
                                    audio.decode_adpcm(sample),
                                    clip.payload_length, entry=entry,
                                    sample=clip.sample_index, sub=group_index,
                                )
            movie_count = 0
            tac_decoder = movie.find_vgmstream_cli()
            for position, entry in enumerate(protected_entries, 1):
                _offset, stored = _read_entry(handle, table, total, entry)
                clear = movie.decode_protected_movie(stored)
                tracks = movie.parse_audio_tracks(clear)
                scene_resource = movie.SCENE_RESOURCES.get(entry)
                folder = (
                    partial / ("%04d" % scene_resource)
                    if scene_resource is not None else
                    partial / "fmv" / ("entry-%04d" % entry)
                )
                folder.mkdir(parents=True, exist_ok=True)
                for track in tracks:
                    if track.codec == movie.PCM_CODEC:
                        target = folder / movie.exported_filename(
                            entry, track.movie
                        )
                        movie.write_pcm_wav(
                            target, movie.decode_pcm_payload(track.data)
                        )
                        peak, rms, voiced = audio.statistics(track.data)
                        seconds = (
                            len(track.data) // movie.PCM_FRAME_BYTES /
                            movie.SAMPLE_RATE
                        )
                        maximum = (
                            track.capacity // movie.PCM_FRAME_BYTES /
                            movie.SAMPLE_RATE
                        )
                        kind = "fmv-pcm"
                        silent = "yes" if rms == 0 else ""
                    else:
                        laac_source = folder / movie.exported_filename(
                            entry, track.movie, "laac"
                        )
                        info = movie.tac_info(track.data)
                        if info is None:
                            raise ValueError(
                                "FMV entry %d track %d does not form a "
                                "complete TAC stream" % (entry, track.movie)
                            )
                        laac_source.write_bytes(track.data)
                        seconds = info.samples / movie.SAMPLE_RATE
                        maximum = seconds
                        wav_target = folder / movie.exported_filename(
                            entry, track.movie
                        )
                        try:
                            decoded = movie.decode_tac_to_wav(
                                laac_source, wav_target, tac_decoder
                            )
                        finally:
                            laac_source.unlink(missing_ok=True)
                        if not decoded:
                            raise ValueError(
                                "FMV entry %d track %d requires vgmstream "
                                "for WAV extraction" % (entry, track.movie)
                            )
                        target = wav_target
                        pcm = movie.read_pcm_wav(target)
                        peak, rms, voiced = audio.statistics(pcm)
                        kind = "fmv-tac"
                        silent = "yes" if rms == 0 else ""
                    rows.append({
                        "kind": kind,
                        "region": region,
                        "resource": (
                            scene_resource
                            if scene_resource is not None else entry
                        ),
                        "voice_scene": "",
                        "bank": "",
                        "sub": "",
                        "entry": entry,
                        "sample": track.movie,
                        "zone": "",
                        "clip_id": "",
                        "relative_path": target.relative_to(
                            partial
                        ).as_posix(),
                        "slot_bytes": track.capacity,
                        "max_seconds": "%.4f" % maximum,
                        "seconds": "%.4f" % seconds,
                        "target_rms": int(rms),
                        "peak": peak,
                        "voiced_pct": "%.1f" % (100 * voiced),
                        "silent": silent,
                        "sha256": hashlib.sha256(
                            target.read_bytes()
                        ).hexdigest(),
                    })
                    movie_count += 1
                say(
                    "extract: FMV entry %d (%d/%d), %d audio track(s)"
                    % (
                        entry,
                        len(VOICE_BANKS) + len(by_entry) +
                        len(battle_entries) + position,
                        extraction_steps, len(tracks),
                    )
                )
        with (partial / "manifest.csv").open(
                "w", encoding="utf-8", newline="") as manifest:
            writer = csv.DictWriter(manifest, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        mapped_cutscenes, mapped_battles = mapping.write_voice_maps(
            partial, rows
        )
        (partial / "README.txt").write_text(
            "Extracted from %s (%s).\n"
            "Cutscene files are named <bank>-<subfile>-<clip-id>.wav.\n"
            "Unmapped files are named unmapped-<entry>-<sample>-<clip-id>-"
            "<zone>.wav.\n"
            "Battle files are named battle-<entry>-<sample>-<clip-id>-"
            "<zone>.wav.\n"
            "Field files are named field-<entry>-<group>-<sample>-<clip-id>-"
            "<zone>.wav. Alicia's six canonical resource-0024 field calls "
            "are grouped under alicia/; duplicate area copies are omitted. "
            "Patching a canonical Alicia WAV updates every mapped area copy. "
            "Other field calls remain under field/. Lezard files use the "
            "same identity shape.\n"
            "FMV packet candidates are named fmv-<entry>-<movie> under the "
            "owning scene folder. Entry 11 is PCM; entries 14, 15, 16, 18, "
            "and 20 are TAC. "
            "TAC is exported as WAV through the bundled vgmstream decoder; "
            "intermediate .laac files are not retained.\n"
            "Folders named by number are cutscene resources; unmapped files "
            "with a known scene owner are placed there too. Banks holding an "
            "unverified alternate performance go under that scene's "
            "alternate-takes/ when its owner is known, otherwise under "
            "unmapped/alternate-takes/. Ordinary 1337 bank lines are retained "
            "for reference under 1337/unused/; its four movie WAVs remain in "
            "the 1337 root.\n"
            "Each cutscene folder and battle/ contains a generated "
            "voice-map.csv. These maps add speakers, scene coordinates, "
            "deduplicated battle identities, confidence, and evidence without "
            "changing the patch identity in any WAV filename.\n"
            % (source.name, boot),
            encoding="utf-8",
        )
        partial.replace(destination)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    say(
        "wrote %d clips, %d cutscene map rows, and %d battle groups to %s"
        % (len(rows), mapped_cutscenes, mapped_battles, destination)
    )
    return ExtractionResult(
        output=destination,
        region=region,
        banks=len(VOICE_BANKS),
        clips=len(rows),
        mapped_banks=mapped,
        unmapped_clips=unmapped_count,
        battle_clips=battle_count,
        movie_tracks=movie_count,
    )


def _find_manifest(folder):
    folder = Path(folder).resolve()
    current = folder
    for _ in range(4):
        candidate = current / "manifest.csv"
        if candidate.is_file():
            return candidate
        if current.parent == current:
            break
        current = current.parent
    return None


def _old_manifest_identities(manifest):
    identities = {}
    if manifest is None:
        return identities
    with manifest.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            if not all(row.get(field) not in (None, "")
                       for field in ("id", "bank", "sub")):
                continue
            stem = row["id"].lower().removeprefix("0x")
            identity = (int(row["bank"]), int(row["sub"]), int(stem, 16))
            previous = identities.setdefault(stem, identity)
            if previous != identity:
                raise ValueError("manifest clip id %s is ambiguous" % stem)
    return identities


def _in_replacement_scope(path, folder, scope):
    if scope in (None, "all"):
        return True
    if scope != "v1":
        raise ValueError("unknown replacement scope: %s" % scope)
    relative = path.relative_to(folder)
    top = relative.parts[0].lower()
    if top in ("alicia", "lezard"):
        return True
    return top.isdigit() and 10 <= int(top) <= 1389


def discover_replacements(folder, scope="all"):
    """Identify exported and legacy dub-kit WAVs below *folder*."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("voice folder does not exist: %s" % folder)
    wavs = sorted(
        path for path in folder.rglob("*")
        if (path.is_file() and path.suffix.lower() in (".wav", ".laac")
            and _in_replacement_scope(path, folder, scope))
    )
    if not wavs:
        raise ValueError("voice folder contains no WAV or TAC files: %s" % folder)
    legacy = _old_manifest_identities(_find_manifest(folder))
    found = {}
    unknown = []
    for path in wavs:
        if (path.suffix.lower() == ".laac" and
                path.with_suffix(".wav").is_file()):
            # Prefer the editable decoded WAV over its adjacent archival TAC.
            continue
        identity = parse_exported_filename(path)
        if identity is None:
            identity = parse_unmapped_filename(path)
        if identity is None:
            battle = parse_battle_filename(path)
            if battle:
                identity = ("battle",) + battle
        if identity is None:
            field = parse_field_filename(path)
            if field:
                identity = ("field",) + field
        if identity is None:
            lezard = parse_lezard_filename(path)
            if lezard:
                identity = ("lezard",) + lezard
        if identity is None:
            fmv = movie.parse_exported_filename(path)
            if fmv:
                identity = ("fmv",) + fmv
        if identity is None:
            stem = path.stem.lower().removeprefix("id_").removeprefix("0x")
            identity = legacy.get(stem)
        if identity is None:
            unknown.append(path)
            continue
        if identity in found:
            raise ValueError("two WAV files target the same voice slot: %s and %s"
                             % (found[identity], path))
        found[identity] = path
    if unknown:
        preview = ", ".join(str(path.relative_to(folder)) for path in unknown[:5])
        raise ValueError(
            "%d WAV file(s) have no recognized voice/FMV identity: %s%s"
            % (len(unknown), preview, " ..." if len(unknown) > 5 else "")
        )
    return found


def _copy_with_progress(source, target, say):
    total = source.stat().st_size
    done = last = 0
    with source.open("rb") as reader, target.open("wb") as writer:
        while True:
            chunk = reader.read(COPY_CHUNK)
            if not chunk:
                break
            writer.write(chunk)
            done += len(chunk)
            percent = done * 100 // total if total else 100
            if percent != last:
                say("copy: %d%%" % percent)
                last = percent


def _protected_stream_entries(handle, values, total):
    """Return every protected movie-stream component on a VP2 disc."""
    entries = []
    for entry in range(total):
        if not values[total + entry]:
            continue
        handle.seek(values[entry] * layout.SECTOR)
        if handle.read(4) == PROTECTED_STREAM_MAGIC:
            entries.append(entry)
    return tuple(entries)


def _indexed_audio_groups(data):
    """Return structurally valid standalone audio in indexed PK1 rows."""
    groups = []
    for number, (tag, offset, length) in enumerate(
            vp2_dcms.parse_pk1(data)):
        payload = data[offset:offset + length]
        try:
            clips = parse_standalone(payload)
        except ValueError:
            continue
        groups.append((
            number, tag, offset,
            tuple((clip.clip_id, clip.zone) for clip in clips),
            payload,
        ))
    return tuple(groups)


def _merge_indexed_audio_groups(base, donor):
    """Keep target PK1 rows except for complete matching donor audio rows."""
    base_groups = _indexed_audio_groups(base)
    donor_groups = _indexed_audio_groups(donor)
    base_shape = tuple(
        (tag, identities)
        for _number, tag, _offset, identities, _payload in base_groups
    )
    donor_shape = tuple(
        (tag, identities)
        for _number, tag, _offset, identities, _payload in donor_groups
    )
    if not base_groups or base_shape != donor_shape:
        raise ValueError("regional indexed-audio PK1 structure does not match")
    replacements = [
        (number, tag, identities, donor_payload)
        for ((number, tag, _base_offset, identities, base_payload),
             (_donor_number, _donor_tag, _donor_offset,
              _donor_identities, donor_payload))
        in zip(base_groups, donor_groups)
        if base_payload != donor_payload
    ]
    if not replacements:
        return base, 0

    base_by_number = {group[0]: group[4] for group in base_groups}
    growth = sum(max(len(payload) - len(base_by_number[number]), 0)
                 for number, _tag, _identities, payload in replacements)
    rebuilt = base + bytes(growth + layout.SECTOR)
    for number, tag, identities, payload in replacements:
        current = _indexed_audio_groups(rebuilt)
        matches = [
            group for group in current
            if group[0] == number and group[1] == tag and
            group[3] == identities
        ]
        if len(matches) != 1:
            raise ValueError("indexed-audio PK1 row moved unexpectedly")
        rebuilt = pk1_archive.repack_pk1_subresource(
            rebuilt, tag, payload, target_offset=matches[0][2]
        )

    entries = vp2_dcms.parse_pk1(rebuilt)
    content_end = max(offset + length for _tag, offset, length in entries)
    final_size = (
        (content_end + layout.SECTOR - 1) // layout.SECTOR * layout.SECTOR
    )
    rebuilt = rebuilt[:final_size]
    checked = _indexed_audio_groups(rebuilt)
    donor_by_number = {
        number: payload
        for number, _tag, _identities, payload in replacements
    }
    base_entries = vp2_dcms.parse_pk1(base)
    checked_entries = vp2_dcms.parse_pk1(rebuilt)
    if len(checked_entries) != len(base_entries):
        raise ValueError("indexed-audio merge changed the PK1 row count")
    replacement_numbers = {item[0] for item in replacements}
    for number, ((base_tag, base_offset, base_length),
                 (checked_tag, checked_offset, checked_length)) in enumerate(
                     zip(base_entries, checked_entries)):
        expected = (donor_by_number[number]
                    if number in replacement_numbers
                    else base[base_offset:base_offset + base_length])
        actual = rebuilt[checked_offset:checked_offset + checked_length]
        if checked_tag != base_tag or actual != expected:
            raise ValueError("indexed-audio merge changed an unrelated PK1 row")
    if tuple((item[1], item[3]) for item in checked) != donor_shape:
        raise ValueError("indexed-audio merge failed structural read-back")
    return rebuilt, len(replacements)


def _indexed_audio_hybrids(base_handle, base_values, donor_handle,
                           donor_values, total):
    """Discover regional audio stored as indexed PK1 subresources."""
    hybrids = {}
    group_count = 0
    for entry in range(1, total):
        if not (base_values[total + entry] and
                donor_values[total + entry]):
            continue
        _offset, donor = _read_entry(
            donor_handle, donor_values, total, entry
        )
        if not _indexed_audio_groups(donor):
            continue
        _offset, base = _read_entry(base_handle, base_values, total, entry)
        base_shape = tuple(
            (item[1], item[3])
            for item in _indexed_audio_groups(base)
        )
        donor_shape = tuple(
            (item[1], item[3])
            for item in _indexed_audio_groups(donor)
        )
        if not base_shape or base_shape != donor_shape:
            continue
        rebuilt, groups = _merge_indexed_audio_groups(base, donor)
        if rebuilt == base:
            continue
        hybrids[entry] = rebuilt
        group_count += groups
    return hybrids, group_count


def _streamed_audio_groups(data):
    """Return ``(tail offset, groups)`` for SEQW streams after PK1 content."""
    entries = vp2_dcms.parse_pk1(data)
    if not entries:
        return None
    content_end = max(offset + length for _tag, offset, length in entries)
    tail_start = (
        (content_end + layout.SECTOR - 1) // layout.SECTOR * layout.SECTOR
    )
    if tail_start >= len(data):
        return None
    groups = []
    position = tail_start
    while True:
        position = data.find(b"SEQW", position)
        if position < 0:
            break
        if position % 16 == 0:
            try:
                clips = parse_standalone(data[position:])
            except ValueError:
                pass
            else:
                groups.append((position, data[position:], clips))
        position += 4
    if not groups:
        return None
    return tail_start, tuple(groups)


def _streamed_audio_tail(data):
    """Return ``(tail offset, clip identities)`` for a PK1 audio tail."""
    found = _streamed_audio_groups(data)
    if found is None:
        return None
    tail_start, groups = found
    return tail_start, tuple(
        tuple((clip.clip_id, clip.zone) for clip in clips)
        for _position, _payload, clips in groups
    )


def _has_markers(identities, markers):
    return all(marker in identities for marker in markers)


def _scene_audio_entries(handle, table, total):
    """Return every entry whose first words form a PK1 table header."""
    entries = []
    for entry in range(1, total):
        if not table[total + entry]:
            continue
        handle.seek(table[entry] * layout.SECTOR)
        header = handle.read(12)
        if len(header) < 12:
            continue
        if struct.unpack_from("<I", header, 0)[0] != 0:
            continue
        count = struct.unpack_from("<I", header, 4)[0] + 1
        if not 1 <= count <= 4096:
            continue
        if struct.unpack_from("<I", header, 8)[0] != count * 16:
            continue
        entries.append(entry)
    return entries


def _merge_streamed_audio_tail(base, donor):
    """Keep target PK1 content/offsets and replace its complete audio tail."""
    base_audio = _streamed_audio_tail(base)
    donor_audio = _streamed_audio_tail(donor)
    if base_audio is None or donor_audio is None:
        raise ValueError("regional streamed-audio PK1 structure does not match")
    base_start, base_identities = base_audio
    donor_start, donor_identities = donor_audio
    if base_identities != donor_identities:
        raise ValueError("regional streamed-audio clip identities do not match")
    rebuilt = base[:base_start] + donor[donor_start:]
    if len(rebuilt) % layout.SECTOR:
        raise ValueError("rebuilt streamed-audio resource is not sector-aligned")
    if rebuilt[:base_start] != base[:base_start]:
        raise ValueError("streamed-audio merge changed indexed target content")
    if rebuilt[base_start:] != donor[donor_start:]:
        raise ValueError("streamed-audio merge did not preserve the donor tail")
    checked = _streamed_audio_tail(rebuilt)
    if checked != (base_start, donor_identities):
        raise ValueError("streamed-audio merge failed structural read-back")
    return rebuilt, sum(len(group) for group in donor_identities)


def _streamed_audio_hybrids(base_handle, base_values, donor_handle,
                            donor_values, total):
    """Discover localized audio after PK1 indexed content on both discs."""
    hybrids = {}
    clip_count = 0
    for entry in range(1, total):
        if not (base_values[total + entry] and
                donor_values[total + entry]):
            continue
        _offset, donor = _read_entry(
            donor_handle, donor_values, total, entry
        )
        if _streamed_audio_tail(donor) is None:
            continue
        _offset, base = _read_entry(base_handle, base_values, total, entry)
        rebuilt, clips = _merge_streamed_audio_tail(base, donor)
        if rebuilt == base:
            continue
        hybrids[entry] = rebuilt
        clip_count += clips
    return hybrids, clip_count


def _canonical_archive_layout(base_values, donor_values, total,
                              donor_resources, sector_overrides=None):
    base_active = [
        entry for entry in range(1, total)
        if base_values[total + entry]
    ]
    donor_active = [
        entry for entry in range(1, total)
        if donor_values[total + entry]
    ]
    if not base_active or base_active != donor_active:
        raise ValueError(
            "target and Japanese archives have different active entries"
        )
    for name, values in (("target", base_values), ("Japanese", donor_values)):
        for previous, entry in zip(base_active, base_active[1:]):
            if (values[previous] + values[total + previous] !=
                    values[entry]):
                raise ValueError(
                    "%s archive is not physically contiguous at resources "
                    "%d/%d" % (name, previous, entry)
                )

    rebuilt = list(base_values)
    cursor = base_values[base_active[0]]
    donor_resources = set(donor_resources)
    sector_overrides = dict(sector_overrides or {})
    for entry in base_active:
        rebuilt[entry] = cursor
        if entry in sector_overrides:
            if sector_overrides[entry] <= 0:
                raise ValueError("resource sector override must be positive")
            rebuilt[total + entry] = sector_overrides[entry]
        elif entry in donor_resources:
            rebuilt[total + entry] = donor_values[total + entry]
        cursor += rebuilt[total + entry]
    return rebuilt, tuple(base_active), cursor


def _replace_flagged_package_items(base, donor, flag):
    """Copy a structural family of fixed-span package items from *donor*."""
    base_layout = package_archive.layout(base)
    donor_layout = package_archive.layout(donor)
    if base_layout.count != donor_layout.count:
        raise ValueError("regional battle packages have different item counts")
    selected = tuple(
        index for index, item_flag in enumerate(base_layout.flags)
        if item_flag == flag
    )
    if not selected or selected != tuple(
            index for index, item_flag in enumerate(donor_layout.flags)
            if item_flag == flag):
        raise ValueError("regional battle asset groups do not match")
    rebuilt = bytearray(base)
    for index in selected:
        base_start, base_end = base_layout.offsets[index:index + 2]
        donor_start, donor_end = donor_layout.offsets[index:index + 2]
        if base_end - base_start != donor_end - donor_start:
            raise ValueError(
                "regional battle asset %d has incompatible geometry" % index
            )
        rebuilt[base_start:base_end] = donor[donor_start:donor_end]
    if package_archive.layout(bytes(rebuilt)) != base_layout:
        raise ValueError("regional battle asset merge changed package geometry")
    return bytes(rebuilt), selected


def _decoded_package_stream(clear):
    """Find the package that owns the global text bank and decode its parent."""
    _bank, path = package_archive.locate_container(clear)
    if not path.steps or path.steps[0].compression is None:
        raise ValueError("global battle bank has no compressed parent package")
    root = clear[path.root_offset:path.root_offset + path.root_size]
    root_layout = package_archive.layout(root)
    item = path.steps[0].item
    start, end = root_layout.offsets[item:item + 2]
    stream = root[start:end]
    if len(stream) < 0x10 or stream[:3] != b"SLZ":
        raise ValueError("global battle parent item is not SLZ")
    stored = struct.unpack_from("<I", stream, 4)[0]
    if 0x10 + stored > len(stream):
        raise ValueError("global battle parent SLZ exceeds its package item")
    return path, root, root_layout, item, slz.decompress(
        stream[:0x10 + stored]
    )


def _pack_fixed_slz(decoded, old_item):
    """Recompress one SLZ output without changing its package allocation."""
    mode = old_item[3]
    packed = bytearray(slz_compress.compress(
        decoded, mode=mode, optimal=False, cache_dir=""
    ))
    if len(packed) > len(old_item):
        packed = bytearray(slz_compress.compress(
            decoded, mode=mode, optimal=True, cache_dir=""
        ))
    if len(packed) > len(old_item):
        raise ValueError(
            "Japanese battle assets need %d bytes but the target item holds %d"
            % (len(packed), len(old_item))
        )
    encoded_stored = len(packed) - 0x10
    old_stored = struct.unpack_from("<I", old_item, 4)[0]
    if encoded_stored <= old_stored and 0x10 + old_stored <= len(old_item):
        struct.pack_into("<I", packed, 4, old_stored)
        packed.extend(bytes(old_stored - encoded_stored))
    packed.extend(bytes(len(old_item) - len(packed)))
    if slz.decompress(bytes(packed)) != decoded:
        raise ValueError("Japanese battle asset SLZ failed read-back")
    return bytes(packed)


def _merge_battle_result_assets(base, donor):
    """Keep target battle code/text and carry Japanese result-screen assets."""
    base_clear, base_protected = protected_package.decode_entry(base)
    donor_clear, _donor_protected = protected_package.decode_entry(donor)
    base_parts = _decoded_package_stream(base_clear)
    donor_parts = _decoded_package_stream(donor_clear)
    base_path, base_root, base_root_layout, item, base_inner = base_parts
    _donor_path, _donor_root, _donor_layout, _donor_item, donor_inner = (
        donor_parts
    )
    merged_inner, selected = _replace_flagged_package_items(
        base_inner, donor_inner, BATTLE_RESULT_ASSET_FLAG
    )
    start, end = base_root_layout.offsets[item:item + 2]
    rebuilt_root = bytearray(base_root)
    rebuilt_root[start:end] = _pack_fixed_slz(
        merged_inner, base_root[start:end]
    )
    rebuilt_clear = bytearray(base_clear)
    rebuilt_clear[
        base_path.root_offset:base_path.root_offset + base_path.root_size
    ] = rebuilt_root
    rebuilt = protected_package.encode_entry(
        base, bytes(rebuilt_clear), base_protected
    )
    if battle_overlay.read(rebuilt).output != battle_overlay.read(base).output:
        raise ValueError("battle asset merge changed the target executable overlay")
    checked_clear, _checked = protected_package.decode_entry(rebuilt)
    if (package_archive.unpack_container(checked_clear) !=
            package_archive.unpack_container(base_clear)):
        raise ValueError("battle asset merge changed the target text bank")
    checked_inner = _decoded_package_stream(checked_clear)[-1]
    donor_layout = package_archive.layout(donor_inner)
    checked_layout = package_archive.layout(checked_inner)
    for index in selected:
        checked_start, checked_end = checked_layout.offsets[index:index + 2]
        donor_start, donor_end = donor_layout.offsets[index:index + 2]
        if (checked_inner[checked_start:checked_end] !=
                donor_inner[donor_start:donor_end]):
            raise ValueError("Japanese battle asset %d failed read-back" % index)
    return rebuilt, selected


def _rewrite_logical_positions(original, rebuilt, total):
    """Carry changed sector counts through the index's active logical runs."""
    active = [
        entry for entry in range(total)
        if original[total + entry] and original[2 * total + entry]
    ]
    for previous, entry in zip(active, active[1:]):
        if (original[2 * total + entry] ==
                original[2 * total + previous] +
                original[total + previous]):
            rebuilt[2 * total + entry] = (
                rebuilt[2 * total + previous] +
                rebuilt[total + previous]
            )


def _copy_resource(source, target, source_lba, target_lba, sectors):
    remaining = sectors * layout.SECTOR
    source.seek(source_lba * layout.SECTOR)
    target.seek(target_lba * layout.SECTOR)
    digest = hashlib.sha256()
    while remaining:
        chunk = source.read(min(COPY_CHUNK, remaining))
        if not chunk:
            raise ValueError("Japanese audio resource extends past its ISO")
        target.write(chunk)
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.digest()


def _write_resource(target, target_lba, sectors, data):
    data = bytes(data)
    expected = sectors * layout.SECTOR
    if len(data) != expected:
        raise ValueError(
            "rebuilt resource has %d bytes; its allocation is %d"
            % (len(data), expected)
        )
    target.seek(target_lba * layout.SECTOR)
    target.write(data)
    return hashlib.sha256(data).digest()


def import_japanese_audio(base, japan, output=None, progress=None,
                          resource_filter=None):
    """Build a Japanese-audio variant, optionally limited to resources."""
    say = progress or (lambda _message: None)
    base, _base_region, _base_boot = _validated_source(
        base, JAPANESE_AUDIO_TARGET_BOOTS, "Japanese-audio import"
    )
    japan, donor_region, _donor_boot = _validated_source(
        japan, {JAPAN_BOOT}, "Japanese-audio donor selection"
    )
    if donor_region != "jp":
        raise ValueError("Japanese audio import requires a Japanese donor ISO")
    output = (Path(output).expanduser().resolve()
              if output else default_japanese_audio_output(base))
    if output in {base, japan}:
        raise ValueError("output must be different from both source ISOs")
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite: %s" % output)
    partial = output.with_name(output.name + ".partial")
    if partial.exists():
        raise ValueError("partial output already exists; remove it first: %s" % partial)

    with base.open("rb") as base_handle, japan.open("rb") as donor_handle:
        base_seed, base_offset, total, base_values = (
            vp2_iso_space.read_index(base_handle)
        )
        donor_seed, donor_offset, donor_total, donor_values = (
            vp2_iso_space.read_index(donor_handle)
        )
        if ((base_seed, base_offset, total) !=
                (donor_seed, donor_offset, donor_total)):
            raise ValueError("target and Japanese discs use incompatible indexes")
        battle = _battle_entries(donor_handle, donor_values, total)
        standalone = {
            voice.entry for voice in load_unmapped_map().values()
        }
        protected = _protected_stream_entries(
            donor_handle, donor_values, total
        )
        indexed_hybrids, indexed_groups = _indexed_audio_hybrids(
            base_handle, base_values, donor_handle, donor_values, total
        )
        if indexed_hybrids:
            say(
                "prepare: %d indexed-scene audio group(s) in %d resource(s)"
                % (indexed_groups, len(indexed_hybrids))
            )
        streamed_hybrids, streamed_clips = _streamed_audio_hybrids(
            base_handle, base_values, donor_handle, donor_values, total
        )
        if streamed_hybrids:
            say(
                "prepare: %d streamed-scene audio clip(s) in %d resource(s)"
                % (streamed_clips, len(streamed_hybrids))
            )
        overlap = set(indexed_hybrids) & set(streamed_hybrids)
        if overlap:
            raise ValueError(
                "audio occurs in indexed rows and the streamed tail of "
                "resource(s): %s" % ", ".join(map(str, sorted(overlap)))
            )
        hybrids = dict(indexed_hybrids)
        hybrids.update(streamed_hybrids)
        if resource_filter is None:
            resources = tuple(sorted(
                set(VOICE_BANKS) | standalone | set(battle) | set(protected) |
                set(hybrids)
            ))
        else:
            resources = tuple(sorted(set(resource_filter)))
            invalid = set(resources) - (set(VOICE_BANKS) | set(protected))
            if invalid:
                raise ValueError(
                    "selected Japanese audio resources are not voice banks "
                    "or protected movies: %s"
                    % ", ".join(map(str, sorted(invalid)))
                )
            hybrids = {}
        for entry in resources:
            if not (base_values[total + entry] and
                    donor_values[total + entry]):
                raise ValueError("audio resource %d is absent from one disc" % entry)
        image_bytes = base.stat().st_size
        if image_bytes % layout.SECTOR:
            raise ValueError("target ISO size is not a whole number of sectors")
        image_lba = image_bytes // layout.SECTOR
        hybrid_sectors = {
            entry: len(data) // layout.SECTOR
            for entry, data in hybrids.items()
            if entry in resources
        }
        rebuilt, archive_entries, end_lba = _canonical_archive_layout(
            base_values, donor_values, total, resources, hybrid_sectors
        )
        _rewrite_logical_positions(base_values, rebuilt, total)
        if (resource_filter is None and
                base_values[total + GLOBAL_BATTLE_RESOURCE] and
                donor_values[total + GLOBAL_BATTLE_RESOURCE]):
            _offset, base_battle = _read_entry(
                base_handle, base_values, total, GLOBAL_BATTLE_RESOURCE
            )
            _offset, donor_battle = _read_entry(
                donor_handle, donor_values, total, GLOBAL_BATTLE_RESOURCE
            )
            hybrids[GLOBAL_BATTLE_RESOURCE], battle_assets = (
                _merge_battle_result_assets(base_battle, donor_battle)
            )
            say("prepare: %d Japanese battle-result asset(s)" %
                len(battle_assets))
        output_lba = max(image_lba, end_lba)
        appended = max(output_lba - image_lba, 0)

        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            say("copy: creating a safe target-disc image")
            _copy_with_progress(base, partial, say)
            expected = {}
            with partial.open("r+b") as target:
                count = len(archive_entries)
                resource_set = set(resources)
                for position, entry in enumerate(archive_entries, 1):
                    if entry in hybrids:
                        expected[entry] = _write_resource(
                            target, rebuilt[entry], rebuilt[total + entry],
                            hybrids[entry]
                        )
                    elif entry in resource_set:
                        expected[entry] = _copy_resource(
                            donor_handle, target, donor_values[entry],
                            rebuilt[entry], donor_values[total + entry],
                        )
                    else:
                        expected[entry] = _copy_resource(
                            base_handle, target, base_values[entry],
                            rebuilt[entry], base_values[total + entry],
                        )
                    if (position == 1 or position == count or
                            position % 100 == 0):
                        say("repack: resource %d (%d/%d)" %
                            (entry, position, count))
                target.truncate(output_lba * layout.SECTOR)
                if vp2_iso_space.read_volume_sectors(target) is not None:
                    vp2_iso_space.write_volume_sectors(target, output_lba)
                if output_lba > image_lba:
                    vp2_iso_space.extend_last_file(target, output_lba)
                vp2_iso_space.write_index(
                    target, base_seed, base_offset, total, rebuilt
                )

            say("verify: reading imported resources back")
            with partial.open("rb") as target:
                _seed, _offset, check_total, check = (
                    vp2_iso_space.read_index(target)
                )
                if check_total != total or check != rebuilt:
                    raise ValueError("Japanese-audio index failed read-back")
                for previous, entry in zip(
                        archive_entries, archive_entries[1:]):
                    if (check[previous] + check[total + previous] !=
                            check[entry]):
                        raise ValueError(
                            "Japanese-audio archive is not contiguous at "
                            "resources %d/%d" % (previous, entry)
                        )
                for entry in archive_entries:
                    target.seek(check[entry] * layout.SECTOR)
                    remaining = check[total + entry] * layout.SECTOR
                    digest = hashlib.sha256()
                    while remaining:
                        chunk = target.read(min(COPY_CHUNK, remaining))
                        if not chunk:
                            raise ValueError(
                                "imported resource %d extends past output" % entry
                            )
                        digest.update(chunk)
                        remaining -= len(chunk)
                    if digest.digest() != expected[entry]:
                        raise ValueError(
                            "imported resource %d failed read-back" % entry
                        )
            partial.replace(output)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
    say("wrote Japanese audio to %s" % output)
    return ImportResult(output, resources, appended)


def import_japanese_cutscene(base, japan, voice_scene, output=None,
                             progress=None):
    """Import one mapped cutscene's complete Japanese voice banks."""
    scene = int(voice_scene)
    banks = tuple(sorted(
        owner.bank for owner in load_bank_map().values()
        if owner.resource == scene and owner.category == "cutscene"
    ))
    if not banks:
        raise ValueError("no mapped voice banks found for cutscene %d" % scene)
    say = progress or (lambda _message: None)
    say("select: cutscene %d uses voice bank(s) %s" %
        (scene, ", ".join(map(str, banks))))
    return import_japanese_audio(
        base, japan, output=output, progress=progress,
        resource_filter=banks,
    )


def _metadata_patch(value):
    if not value:
        return ()
    return tuple(
        (int(item.split(":", 1)[0], 16),
         int(item.split(":", 1)[1], 16))
        for item in value.split(";")
    )


def _rebuild_voice_bank(data, replacements, capacities):
    """Rebuild one USA bank using cached max-region slot geometry."""
    clips = {clip.sub_index: clip for clip in parse_bank(data)}
    count = struct.unpack_from("<I", data, 0)[0]
    first = min(clip.sub_offset for clip in clips.values())
    if first % layout.SECTOR:
        raise ValueError("voice-bank first subfile is not sector-aligned")
    header = bytearray(data[:first])
    subfiles = []
    cursor = first
    expected = {}
    for sub_index in range(count):
        position = 4 + sub_index * 4
        start_sector, sector_count = struct.unpack_from("<HH", data, position)
        if not sector_count:
            struct.pack_into("<HH", header, position, 0, 0)
            continue
        clip = clips[sub_index]
        original = data[
            clip.sub_offset:clip.sub_offset + clip.sub_length
        ]
        replacement = replacements.get(sub_index)
        if replacement is None:
            rebuilt = original
        else:
            encoded, path, clip_id, bank = replacement
            row = capacities.get((bank, sub_index))
            if row is None:
                raise ValueError(
                    "no cached max capacity for bank %d subfile %d"
                    % (bank, sub_index)
                )
            if int(row["clip_id"], 16) != clip_id:
                raise ValueError(
                    "cached clip identity mismatch for %s" % path.name
                )
            target = int(row["max_payload_bytes"])
            if len(encoded) > target:
                raise ValueError(
                    "%s needs %d encoded bytes but the USA/Japanese maximum "
                    "is %d" % (path.name, len(encoded), target)
                )
            prefix = bytearray(original[:clip.payload_offset])
            for offset, value in _metadata_patch(row["max_header_patch"]):
                if offset >= len(prefix):
                    raise ValueError("cached voice-header patch is out of range")
                prefix[offset] = value
            fitted = bytearray(audio.fit_payload(encoded, target, 0))
            for offset, value in _metadata_patch(row["max_flag_patch"]):
                if offset >= len(fitted):
                    raise ValueError("cached voice-flag patch is out of range")
                fitted[offset] = value
            subfile_bytes = int(row["max_subfile_bytes"])
            rebuilt = bytes(prefix) + bytes(fitted)
            if len(rebuilt) > subfile_bytes:
                raise ValueError(
                    "%s rebuilt subfile exceeds its cached allocation"
                    % path.name
                )
            rebuilt += bytes(subfile_bytes - len(rebuilt))
            expected[sub_index] = target
        if len(rebuilt) % layout.SECTOR:
            raise ValueError("rebuilt voice subfile is not sector-aligned")
        struct.pack_into(
            "<HH", header, position,
            cursor // layout.SECTOR, len(rebuilt) // layout.SECTOR,
        )
        subfiles.append(rebuilt)
        cursor += len(rebuilt)
    rebuilt = bytes(header) + b"".join(subfiles)
    checked = {clip.sub_index: clip for clip in parse_bank(rebuilt)}
    for sub_index, target in expected.items():
        if checked[sub_index].payload_length != target:
            raise ValueError(
                "rebuilt bank subfile %d failed capacity read-back" % sub_index
            )
    return rebuilt


def _rebuild_standalone_group(data, replacements):
    """Reflow selected samples in one indexed standalone SEQW."""
    clips = parse_standalone(data)
    wav_offset = struct.unpack_from("<I", data, 4)[0]
    header_length, table_length = struct.unpack_from(
        "<II", data, wav_offset + 8
    )
    payload_offset = clips[0].payload_offset
    old_payload_length = struct.unpack_from("<I", data, wav_offset + 0x10)[0]
    start_fields = []
    position = wav_offset + 0x20
    table_end = position + table_length
    while position < table_end:
        record_length = struct.unpack_from("<H", data, position)[0]
        cursor = position + 4
        while (cursor + 0x14 <= position + record_length and
               struct.unpack_from("<I", data, cursor)[0] == 0x14):
            start_fields.append(cursor + 0x10)
            cursor += 0x14
        position += record_length
    if len(start_fields) != len(clips):
        raise ValueError("standalone sample table changed unexpectedly")

    prefix = bytearray(data[:payload_offset])
    payloads = []
    starts = []
    cursor = 0
    expected = {}
    for clip in clips:
        starts.append(cursor)
        replacement = replacements.get(clip.sample_index)
        if replacement is None:
            payload = data[
                clip.payload_offset:clip.payload_offset + clip.payload_length
            ]
        else:
            encoded, target, path = replacement
            if len(encoded) > target:
                raise ValueError(
                    "%s needs %d encoded bytes but the USA/Japanese maximum "
                    "is %d" % (path.name, len(encoded), target)
                )
            payload = audio.fit_payload(encoded, target, clip.tail_flag)
            expected[clip.sample_index] = target
        payloads.append(payload)
        cursor += len(payload)
    for field, start in zip(start_fields, starts):
        struct.pack_into("<I", prefix, field, start)
    payload_length = sum(map(len, payloads))
    if payload_length < 0x40:
        raise ValueError("rebuilt standalone payload is too short")
    struct.pack_into("<I", prefix, 0x14, payload_length - 0x40)
    struct.pack_into("<I", prefix, wav_offset + 4,
                     header_length + payload_length)
    struct.pack_into("<I", prefix, wav_offset + 0x10, payload_length)
    struct.pack_into("<I", prefix, wav_offset + 0x14, payload_length - 0x40)
    suffix = data[payload_offset + old_payload_length:]
    rebuilt = bytes(prefix) + b"".join(payloads) + suffix
    checked = {clip.sample_index: clip for clip in parse_standalone(rebuilt)}
    if tuple((clip.clip_id, clip.zone) for clip in checked.values()) != tuple(
            (clip.clip_id, clip.zone) for clip in clips):
        raise ValueError("rebuilt standalone identities changed")
    for sample, target in expected.items():
        if checked[sample].payload_length != target:
            raise ValueError("rebuilt standalone sample failed read-back")
    return rebuilt


def _rebuild_alicia_field_entry(data, replacements):
    """Rebuild indexed rows holding replicated Alicia field calls."""
    groups = _indexed_audio_groups(data)
    payloads = {}
    for group_index in sorted({key[0] for key in replacements}):
        group_replacements = {
            sample: value
            for (group, sample), value in replacements.items()
            if group == group_index
        }
        payloads[group_index] = _rebuild_standalone_group(
            groups[group_index][4], group_replacements
        )
    growth = sum(
        max(len(payload) - len(groups[index][4]), 0)
        for index, payload in payloads.items()
    )
    rebuilt = data + bytes(growth + layout.SECTOR)
    for group_index, payload in payloads.items():
        current = _indexed_audio_groups(rebuilt)
        group = current[group_index]
        rebuilt = pk1_archive.repack_pk1_subresource(
            rebuilt, group[1], payload, target_offset=group[2]
        )
    entries = vp2_dcms.parse_pk1(rebuilt)
    content_end = max(offset + length for _tag, offset, length in entries)
    rebuilt = rebuilt[:(
        (content_end + layout.SECTOR - 1) // layout.SECTOR * layout.SECTOR
    )]
    checked = _indexed_audio_groups(rebuilt)
    for group_index, payload in payloads.items():
        if checked[group_index][4] != payload:
            raise ValueError("rebuilt Alicia field row failed read-back")
    return rebuilt


ESP_BASE = 0x20


def _rewrite_stream_directory(data, tail_start, old_starts, new_starts, end):
    """Point a streamed tail's ESP directory at its reflowed groups."""
    if data[tail_start + 0x10:tail_start + 0x14] != b"ESP\0":
        raise ValueError("streamed tail has no ESP directory")
    base = tail_start + ESP_BASE
    count = struct.unpack_from("<I", data, base)[0]
    if count != len(old_starts) or count != len(new_starts):
        raise ValueError("ESP directory does not list every streamed group")
    for index, (old, new) in enumerate(zip(old_starts, new_starts)):
        field = base + 8 + index * 8 + 4
        if struct.unpack_from("<I", data, field)[0] != old - base:
            raise ValueError(
                "ESP directory entry %d does not point at its group" % index
            )
        struct.pack_into("<I", data, field, new - base)
    struct.pack_into("<I", data, tail_start + 0x18, end - base)


def _rebuild_lezard_entry(data, entry, replacements, capacities):
    """Reflow a complete Lezard tail using coherent Japanese geometry."""
    found = _streamed_audio_groups(data)
    if found is None:
        raise ValueError("Lezard entry %d has no streamed audio" % entry)
    _tail_start, groups = found
    required = set(range(len(groups)))
    supplied = set(replacements)
    if supplied != required:
        missing = sorted(required - supplied)
        raise ValueError(
            "Lezard entry %d needs all %d WAVs to use Japanese geometry "
            "(missing group(s): %s)"
            % (entry, len(groups), ", ".join(map(str, missing)))
        )
    pieces = [data[:groups[0][0]]]
    expected = {}
    for group_index, (position, _payload, clips) in enumerate(groups):
        end = (groups[group_index + 1][0]
               if group_index + 1 < len(groups) else len(data))
        original = data[position:end]
        replacement = replacements[group_index]
        encoded, path, replacement_info = replacement
        clip = {item.sample_index: item for item in clips}.get(
            replacement_info.sample
        )
        if clip is None:
            raise ValueError("%s targets a missing Lezard sample" % path.name)
        row = capacities.get((entry, group_index, clip.sample_index))
        if row is None:
            raise ValueError(
                "no cached Japanese geometry for Lezard entry %d group %d"
                % (entry, group_index)
            )
        if ((int(row["clip_id"], 16), int(row["zone"])) !=
                (clip.clip_id, clip.zone)):
            raise ValueError(
                "cached Lezard identity mismatch for %s" % path.name
            )
        target = int(row["jp_payload_bytes"])
        if len(encoded) > target:
            raise ValueError(
                "%s needs %d encoded bytes but the coherent Japanese slot is %d"
                % (path.name, len(encoded), target)
            )
        prefix = bytearray(original[:clip.payload_offset])
        for offset, value in _metadata_patch(row["jp_header_patch"]):
            if offset >= len(prefix):
                raise ValueError("cached Lezard header patch is out of range")
            prefix[offset] = value
        if len(encoded) % audio.FRAME:
            raise ValueError("encoded Lezard audio is not frame-aligned")
        fitted = bytearray(encoded + bytes(target - len(encoded)))
        if fitted:
            fitted[:audio.FRAME] = bytes(audio.FRAME)
        for offset, value in _metadata_patch(row["jp_flag_patch"]):
            if offset >= len(fitted):
                raise ValueError("cached Lezard flag patch is out of range")
            fitted[offset] = value
        for offset, value in _metadata_patch(row["jp_terminal_patch"]):
            if offset >= len(fitted):
                raise ValueError("cached Lezard terminal patch is out of range")
            fitted[offset] = value
        group_bytes = int(row["jp_group_bytes"])
        trailer_bytes = group_bytes - len(prefix) - len(fitted)
        if trailer_bytes < 0:
            raise ValueError("%s exceeds its cached Lezard group" % path.name)
        trailer = bytearray(trailer_bytes)
        for offset, value in _metadata_patch(row["jp_trailer_patch"]):
            if offset >= len(trailer):
                raise ValueError("cached Lezard trailer patch is out of range")
            trailer[offset] = value
        rebuilt = bytes(prefix) + bytes(fitted) + bytes(trailer)
        if len(rebuilt) > group_bytes:
            raise ValueError("%s exceeds its cached Lezard group" % path.name)
        pieces.append(rebuilt + bytes(group_bytes - len(rebuilt)))
        expected[group_index] = target
    starts = []
    cursor = 0
    for piece in pieces:
        starts.append(cursor)
        cursor += len(piece)
    rebuilt = bytearray(b"".join(pieces))
    last_clip = groups[-1][2][0]
    lip = rebuilt.find(
        b"LIP ",
        starts[-1] + last_clip.payload_offset + expected[len(groups) - 1],
    )
    if lip < 0 or lip % 16:
        raise ValueError("rebuilt Lezard tail has no closing LIP block")
    _rewrite_stream_directory(
        rebuilt, _tail_start, [position for position, _p, _c in groups],
        starts[1:], lip + struct.unpack_from("<I", rebuilt, lip + 4)[0]
    )
    rebuilt = bytes(rebuilt) + bytes(-len(rebuilt) % layout.SECTOR)
    checked = _streamed_audio_groups(rebuilt)
    if checked is None or len(checked[1]) != len(groups):
        raise ValueError("rebuilt Lezard resource failed structural read-back")
    for group_index, target in expected.items():
        clips = checked[1][group_index][2]
        clip = {item.sample_index: item for item in clips}.get(
            replacements[group_index][2].sample
        )
        if clip is None or clip.payload_length != target:
            raise ValueError(
                "rebuilt Lezard group %d failed capacity read-back"
                % group_index
            )
    return rebuilt


def _repack_resource_overrides(base, output, overrides, writes=(),
                               progress=None):
    """Repack the target archive with selected complete resource payloads."""
    say = progress or (lambda _message: None)
    base = Path(base).expanduser().resolve()
    output = Path(output).expanduser().resolve()
    partial = output.with_name(output.name + ".partial")
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite: %s" % output)
    if partial.exists():
        raise ValueError("partial output already exists; remove it first: %s" % partial)
    overrides = {int(entry): bytes(data) for entry, data in overrides.items()}
    writes = tuple(writes)
    write_entries = {entry for entry, _relative, _data, _items in writes}
    for entry, data in overrides.items():
        if not data or len(data) % layout.SECTOR:
            raise ValueError(
                "rebuilt resource %d is not sector-aligned" % entry
            )
    with base.open("rb") as base_handle:
        seed, index_offset, total, values = vp2_iso_space.read_index(base_handle)
        sector_overrides = {
            entry: len(data) // layout.SECTOR
            for entry, data in overrides.items()
        }
        rebuilt, archive_entries, end_lba = _canonical_archive_layout(
            values, values, total, (), sector_overrides
        )
        _rewrite_logical_positions(values, rebuilt, total)
        image_lba = base.stat().st_size // layout.SECTOR
        output_lba = max(image_lba, end_lba)
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            say("copy: creating a safe target-disc image")
            _copy_with_progress(base, partial, say)
            expected = {}
            with partial.open("r+b") as target:
                count = len(archive_entries)
                for position, entry in enumerate(archive_entries, 1):
                    if entry in overrides:
                        digest = _write_resource(
                            target, rebuilt[entry], rebuilt[total + entry],
                            overrides[entry],
                        )
                    else:
                        digest = _copy_resource(
                            base_handle, target, values[entry], rebuilt[entry],
                            values[total + entry],
                        )
                    if entry not in write_entries:
                        expected[entry] = digest
                    if position in (1, count) or position % 250 == 0:
                        say("repack: resource %d (%d/%d)" %
                            (entry, position, count))
                target.truncate(output_lba * layout.SECTOR)
                if vp2_iso_space.read_volume_sectors(target) is not None:
                    vp2_iso_space.write_volume_sectors(target, output_lba)
                if output_lba > image_lba:
                    vp2_iso_space.extend_last_file(target, output_lba)
                vp2_iso_space.write_index(
                    target, seed, index_offset, total, rebuilt
                )
                for entry, relative, data, _items in writes:
                    allocation = rebuilt[total + entry] * layout.SECTOR
                    if relative < 0 or relative + len(data) > allocation:
                        raise ValueError(
                            "voice write exceeds rebuilt resource %d" % entry
                        )
                    target.seek(rebuilt[entry] * layout.SECTOR + relative)
                    target.write(data)
            say("verify: reading rebuilt resources back")
            with partial.open("rb") as target:
                _seed, _offset, check_total, check = vp2_iso_space.read_index(target)
                if check_total != total or check != rebuilt:
                    raise ValueError("rebuilt voice archive index failed read-back")
                for entry, digest in expected.items():
                    target.seek(check[entry] * layout.SECTOR)
                    data = target.read(check[total + entry] * layout.SECTOR)
                    if hashlib.sha256(data).digest() != digest:
                        raise ValueError(
                            "rebuilt resource %d failed read-back" % entry
                        )
                for entry, relative, data, items in writes:
                    target.seek(check[entry] * layout.SECTOR + relative)
                    if target.read(len(data)) != data:
                        raise ValueError(
                            "%s voice %04x failed rebuilt-resource read-back"
                            % (items[0].kind, items[0].clip_id)
                        )
            partial.replace(output)
        except Exception:
            partial.unlink(missing_ok=True)
            raise


def patch_iso(source, voices, output=None, progress=None,
              allow_overlong=False, movie_sync=None, scope="all"):
    """Copy an ISO, replace selected lines in place, and read them back."""
    say = progress or (lambda _message: None)
    source, region, _boot = _validated_source(
        source, VOICE_SOURCE_BOOTS, "fixed-slot voice patching"
    )
    output = (Path(output).expanduser().resolve()
              if output else default_patch_output(source))
    if output == source:
        raise ValueError("output must be different from the source ISO")
    if output.exists():
        raise ValueError("output already exists; refusing to overwrite: %s" % output)
    partial = output.with_name(output.name + ".partial")
    if partial.exists():
        raise ValueError("partial output already exists; remove it first: %s" % partial)
    selected = discover_replacements(voices, scope=scope)
    alicia_aliases = load_alicia_field_aliases()
    alicia_target_capacities = {}
    for canonical, targets in alicia_aliases.items():
        canonical_identity = ("field",) + canonical
        path = selected.get(canonical_identity)
        if path is None:
            continue
        for target, maximum in targets.items():
            target_identity = ("field",) + target
            selected[target_identity] = path
            alicia_target_capacities[target_identity] = maximum
    capacities = load_capacity_csv() if region == "en" else {}
    lezard_capacities = load_lezard_capacity_csv() if region == "en" else {}
    normalized_sync = {}
    for entry, seconds in (movie_sync or {}).items():
        value = float(seconds)
        movie.tac_sync_frames(value)
        if value != 0.0:
            normalized_sync[int(entry)] = value
    movie_sync = normalized_sync
    pending = []
    with source.open("rb") as handle:
        total, table = read_index(handle)
        banks = {}
        entries = {}
        battle_entries = {}
        scene_entries = {}
        movie_entries = {}
        allowed_unmapped = {
            (voice.entry, voice.sample, voice.clip_id, voice.zone)
            for voice in load_unmapped_map().values()
        }
        prefix_rank = {"battle": 2, "field": 3, "lezard": 4, "fmv": 5}

        def identity_order(item):
            identity = item[0]
            if identity[0] in prefix_rank:
                return (prefix_rank[identity[0]], *identity[1:])
            return (0 if len(identity) == 3 else 1, *identity)

        for identity, path in sorted(selected.items(), key=identity_order):
            if identity[0] == "fmv":
                _kind, entry, movie_index = identity
                if entry not in movie_entries:
                    entry_offset, stored = _read_entry(
                        handle, table, total, entry
                    )
                    clear = movie.decode_protected_movie(stored)
                    movie_entries[entry] = {
                        "offset": entry_offset,
                        "stored_length": len(stored),
                        "clear": clear,
                        "tracks": {
                            track.movie: track
                            for track in movie.parse_audio_tracks(clear)
                        },
                        "replacements": [],
                    }
                current = movie_entries[entry]
                track = current["tracks"].get(movie_index)
                if track is None:
                    raise ValueError(
                        "%s targets missing movie track %d in entry %d"
                        % (path.name, movie_index, entry)
                    )
                replacement = Replacement(
                    path=path, kind="fmv", entry=entry,
                    sample=movie_index, clip_id=movie_index,
                    slot_bytes=track.capacity,
                )
                if track.codec == movie.PCM_CODEC:
                    if path.suffix.lower() != ".wav":
                        raise ValueError(
                            "%s targets PCM movie audio and must be WAV"
                            % path.name
                        )
                    pcm = movie.read_pcm_wav(path)
                    if entry in movie_sync:
                        pcm = movie.shift_pcm(pcm, movie_sync[entry])
                    pcm = movie.encode_pcm_payload(pcm)
                    rebuilt = movie.replace_pcm_track(
                        current["clear"], track, pcm
                    )
                else:
                    if path.suffix.lower() == ".wav":
                        frames = movie.tac_sync_frames(movie_sync.get(entry, 0))
                        say(
                            "encode: movie entry %d track %d sync %+.4fs (%+d frame(s))"
                            % (entry, movie_index,
                               movie.tac_sync_seconds(frames), frames)
                        )
                        template = track.data
                        target_samples = movie.tac_target_samples(template)
                        encoded = movie.encode_tac_wav(
                            path, template, track.capacity,
                            movie_sync.get(entry, 0),
                            target_samples=target_samples,
                            progress=say,
                        )
                    elif path.suffix.lower() == ".laac":
                        if entry in movie_sync:
                            raise ValueError(
                                "%s is already encoded; movie sync applies "
                                "only when patching its WAV" % path.name
                            )
                        encoded = path.read_bytes()
                    else:
                        raise ValueError(
                            "%s targets TAC movie audio; provide WAV or .laac"
                            % path.name
                        )
                    rebuilt = movie.replace_tac_track(
                        current["clear"], track, encoded
                    )
                if rebuilt == current["clear"]:
                    say(
                        "skip unchanged: movie entry %d track %d"
                        % (entry, movie_index)
                    )
                    continue
                current["clear"] = rebuilt
                current["replacements"].append(replacement)
                say(
                    "prepare: movie entry %d track %d <- %s"
                    % (entry, movie_index, path.name)
                )
                continue
            if identity[0] == "battle":
                _kind, entry, sample_index, clip_id, zone = identity
                if entry not in battle_entries:
                    entry_offset, stored = _read_entry(
                        handle, table, total, entry
                    )
                    clear, signature = decode_battle_entry(stored)
                    battle_entries[entry] = {
                        "offset": entry_offset,
                        "clear": bytearray(clear),
                        "signature": signature,
                        "clips": {
                            item.sample_index: item
                            for item in parse_standalone(clear)
                        },
                        "replacements": [],
                    }
                current = battle_entries[entry]
                clip = current["clips"].get(sample_index)
                if clip is None:
                    raise ValueError(
                        "%s targets missing sample %d in battle entry %d"
                        % (path.name, sample_index, entry)
                    )
                if (clip.clip_id, clip.zone) != (clip_id, zone):
                    raise ValueError(
                        "%s says clip %04x/%d, but battle entry %d sample "
                        "%d is %04x/%d"
                        % (path.name, clip_id, zone, entry, sample_index,
                           clip.clip_id, clip.zone)
                    )
                original_payload = bytes(current["clear"])[
                    clip.payload_offset:
                    clip.payload_offset + clip.payload_length
                ]
                replacement = Replacement(
                    path=path, kind="battle", entry=entry,
                    sample=sample_index, zone=zone, clip_id=clip_id,
                    slot_bytes=clip.payload_length,
                )
                label = "battle entry %d sample %d" % (entry, sample_index)
            elif identity[0] in ("field", "lezard"):
                kind, entry, group_index, sample_index, clip_id, zone = (
                    identity
                )
                if entry not in scene_entries:
                    entry_offset, entry_data = _read_entry(
                        handle, table, total, entry
                    )
                    found = _streamed_audio_groups(entry_data)
                    scene_entries[entry] = {
                        "offset": entry_offset,
                        "data": entry_data,
                        "indexed": _indexed_audio_groups(entry_data),
                        "streamed": found[1] if found else (),
                        "field_encoded": {},
                        "field_replacements": [],
                        "lezard_encoded": {},
                        "lezard_replacements": [],
                    }
                current = scene_entries[entry]
                if kind == "field":
                    if group_index >= len(current["indexed"]):
                        raise ValueError(
                            "%s targets missing indexed group %d in "
                            "entry %d" % (path.name, group_index, entry)
                        )
                    relative = current["indexed"][group_index][2]
                    clips = parse_standalone(
                        current["indexed"][group_index][4]
                    )
                else:
                    if group_index >= len(current["streamed"]):
                        raise ValueError(
                            "%s targets missing lezard group %d in entry %d"
                            % (path.name, group_index, entry)
                        )
                    relative = current["streamed"][group_index][0]
                    clips = current["streamed"][group_index][2]
                clip = {item.sample_index: item for item in clips}.get(
                    sample_index
                )
                if clip is None:
                    raise ValueError(
                        "%s targets missing sample %d in %s entry %d group %d"
                        % (path.name, sample_index, kind, entry, group_index)
                    )
                if (clip.clip_id, clip.zone) != (clip_id, zone):
                    raise ValueError(
                        "%s says clip %04x/%d, but %s entry %d group %d "
                        "sample %d is %04x/%d"
                        % (path.name, clip_id, zone, kind, entry, group_index,
                           sample_index, clip.clip_id, clip.zone)
                    )
                relative += clip.payload_offset
                original_payload = current["data"][
                    relative:relative + clip.payload_length
                ]
                replacement = Replacement(
                    path=path, kind=kind, entry=entry, sub=group_index,
                    sample=sample_index, zone=zone, clip_id=clip_id,
                    slot_bytes=clip.payload_length,
                )
                label = "%s entry %d group %d sample %d" % (
                    kind, entry, group_index, sample_index
                )
                if kind == "field" and identity in alicia_target_capacities:
                    maximum_bytes = alicia_target_capacities[identity]
                    replacement = replace(
                        replacement, slot_bytes=maximum_bytes
                    )
                    pcm = audio.read_wav(path)
                    encoded = audio.encode_adpcm(pcm)
                    if len(encoded) > maximum_bytes:
                        if not allow_overlong:
                            duration = len(pcm) // 2 / audio.SAMPLE_RATE
                            seconds = (
                                maximum_bytes // audio.FRAME
                                * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                            )
                            raise ValueError(
                                "%s is %.3fs but its available game slot "
                                "maximum is %.3fs: encoded audio needs %d "
                                "bytes but the slot holds %d"
                                % (path.name, duration, seconds, len(encoded),
                                   maximum_bytes)
                            )
                        encoded = encoded[:maximum_bytes]
                        replacement = replace(replacement, truncated=True)
                        say(
                            "warning: %s exceeds the USA/Japanese maximum "
                            "and will be trimmed" % path.name
                        )
                    current["field_encoded"][(group_index, sample_index)] = (
                        encoded, maximum_bytes, path, replacement
                    )
                    current["field_replacements"].append(replacement)
                    say("prepare: %s <- %s" % (label, path.name))
                    continue
                if kind == "lezard":
                    row = lezard_capacities.get(
                        (entry, group_index, sample_index)
                    )
                    if (row is not None and
                            int(row["usa_payload_bytes"]) !=
                            clip.payload_length):
                        row = None
                    maximum_bytes = (
                        int(row["max_payload_bytes"])
                        if row is not None else clip.payload_length
                    )
                    replacement = replace(
                        replacement, slot_bytes=maximum_bytes
                    )
                    pcm = audio.read_wav(path)
                    if row is None or not row.get("jp_controls"):
                        raise ValueError(
                            "no cached Japanese control template for %s"
                            % path.name
                        )
                    encoded = audio.encode_adpcm_with_controls(
                        pcm, bytes.fromhex(row["jp_controls"])
                    )
                    if len(encoded) > maximum_bytes:
                        if not allow_overlong:
                            duration = len(pcm) // 2 / audio.SAMPLE_RATE
                            seconds = (
                                maximum_bytes // audio.FRAME
                                * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                            )
                            raise ValueError(
                                "%s is %.3fs but its available game slot "
                                "maximum is %.3fs: encoded audio needs %d "
                                "bytes but the slot holds %d"
                                % (path.name, duration, seconds, len(encoded),
                                   maximum_bytes)
                            )
                        encoded = encoded[:maximum_bytes]
                        replacement = replace(replacement, truncated=True)
                        say(
                            "warning: %s exceeds the USA/Japanese maximum "
                            "and will be trimmed" % path.name
                        )
                    current["lezard_encoded"][group_index] = (
                        encoded, path, replacement
                    )
                    current["lezard_replacements"].append(replacement)
                    say("prepare: %s <- %s" % (label, path.name))
                    continue
            elif len(identity) == 3:
                bank, sub_index, clip_id = identity
                if bank not in VOICE_BANKS:
                    raise ValueError(
                        "replacement targets non-voice bank %d" % bank
                    )
                if bank not in banks:
                    bank_offset, bank_data = _read_bank(
                        handle, table, total, bank
                    )
                    banks[bank] = {
                        "offset": bank_offset,
                        "data": bank_data,
                        "clips": {
                            item.sub_index: item
                            for item in parse_bank(bank_data)
                        },
                        "encoded": {},
                        "replacements": [],
                    }
                current_bank = banks[bank]
                bank_offset = current_bank["offset"]
                clips = current_bank["clips"]
                clip = clips.get(sub_index)
                if clip is None:
                    raise ValueError(
                        "%s targets missing subfile %d in bank %d"
                        % (path.name, sub_index, bank)
                    )
                if clip.clip_id != clip_id:
                    raise ValueError(
                        "%s says clip %04x, but bank %d subfile %d is %04x"
                        % (path.name, clip_id, bank, sub_index, clip.clip_id)
                    )
                absolute = (
                    bank_offset + clip.sub_offset + clip.payload_offset
                )
                capacity = capacities.get((bank, sub_index))
                if (capacity is not None and
                        int(capacity["usa_payload_bytes"]) !=
                        clip.payload_length):
                    capacity = None
                replacement = Replacement(
                    path=path, kind="cutscene", bank=bank, sub=sub_index,
                    clip_id=clip_id,
                    slot_bytes=(int(capacity["max_payload_bytes"])
                                if capacity is not None
                                else clip.payload_length),
                )
                label = "bank %d subfile %d" % (bank, sub_index)
                pcm = audio.read_wav(path)
                encoded = audio.encode_adpcm(pcm)
                maximum = replacement.slot_bytes
                if len(encoded) > maximum:
                    if not allow_overlong:
                        duration = len(pcm) // 2 / audio.SAMPLE_RATE
                        seconds = (
                            maximum // audio.FRAME
                            * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
                        )
                        raise ValueError(
                            "%s is %.3fs but its available game slot maximum "
                            "is %.3fs: encoded audio needs %d bytes but the "
                            "slot holds %d"
                            % (path.name, duration, seconds,
                               len(encoded), maximum)
                        )
                    encoded = encoded[:maximum]
                    replacement = replace(replacement, truncated=True)
                    say(
                        "warning: %s exceeds the USA/Japanese maximum and "
                        "will be trimmed to %.3fs"
                        % (path.name, maximum // audio.FRAME
                           * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE)
                    )
                current_bank["encoded"][sub_index] = (
                    encoded, path, clip_id, bank
                )
                current_bank["replacements"].append(replacement)
                say("prepare: %s <- %s" % (label, path.name))
                continue
            elif len(identity) == 4:
                entry, sample_index, clip_id, zone = identity
                if identity not in allowed_unmapped:
                    raise ValueError(
                        "%s is not a tracked unmapped voice slot" % path.name
                    )
                if entry not in entries:
                    entry_offset, entry_data = _read_entry(
                        handle, table, total, entry
                    )
                    entries[entry] = (entry_offset, entry_data, {
                        item.sample_index: item
                        for item in parse_standalone(entry_data)
                    })
                entry_offset, entry_data, clips = entries[entry]
                clip = clips.get(sample_index)
                if clip is None:
                    raise ValueError(
                        "%s targets missing sample %d in entry %d"
                        % (path.name, sample_index, entry)
                    )
                if (clip.clip_id, clip.zone) != (clip_id, zone):
                    raise ValueError(
                        "%s says clip %04x/%d, but entry %d sample %d is "
                        "%04x/%d"
                        % (path.name, clip_id, zone, entry, sample_index,
                           clip.clip_id, clip.zone)
                    )
                relative = clip.payload_offset
                original_payload = entry_data[
                    clip.payload_offset:
                    clip.payload_offset + clip.payload_length
                ]
                replacement = Replacement(
                    path=path, kind="unmapped", entry=entry,
                    sample=sample_index, zone=zone, clip_id=clip_id,
                    slot_bytes=clip.payload_length,
                )
                label = "unmapped entry %d sample %d" % (
                    entry, sample_index
                )
            else:
                raise ValueError("unknown voice identity for %s" % path.name)
            pcm = audio.read_wav(path)
            encoded = audio.encode_adpcm(pcm)
            truncated = len(encoded) > clip.payload_length
            maximum = (
                clip.payload_length // audio.FRAME
                * audio.SAMPLES_PER_FRAME / audio.SAMPLE_RATE
            )
            try:
                fitted = audio.fit_payload(
                    encoded, clip.payload_length, clip.tail_flag,
                    allow_truncate=allow_overlong,
                )
            except ValueError as exc:
                duration = len(pcm) // 2 / audio.SAMPLE_RATE
                raise ValueError(
                    "%s is %.3fs but its game slot is %.3fs: %s"
                    % (path.name, duration, maximum, exc)
                ) from exc
            if replacement.kind in ("unmapped", "battle", "field", "lezard"):
                fitted = bytearray(fitted)
                for offset in range(0, len(fitted), audio.FRAME):
                    fitted[offset + 1] = original_payload[offset + 1]
                fitted = bytes(fitted)
            replacement = replace(replacement, truncated=truncated)
            if replacement.kind == "battle":
                start = clip.payload_offset
                current["clear"][start:start + clip.payload_length] = fitted
                current["replacements"].append(replacement)
            else:
                pending.append((
                    replacement.entry, relative, fitted, (replacement,)
                ))
            say("prepare: %s <- %s" % (label, path.name))
            if truncated:
                say("warning: %s is overlong and will be trimmed to %.3fs" % (
                    path.name, maximum
                ))
        bank_expanded = any(
            len(encoded[0]) > current["clips"][sub].payload_length
            for current in banks.values()
            for sub, encoded in current["encoded"].items()
        )
        lezard_expanded_entries = {
            entry for entry, current in scene_entries.items()
            if any(
                len(encoded[0]) > current["streamed"][group][2][0].payload_length
                for group, encoded in current["lezard_encoded"].items()
            )
        }
        for entry in sorted(lezard_expanded_entries):
            current = scene_entries[entry]
            required = set(range(len(current["streamed"])))
            supplied = set(current["lezard_encoded"])
            if supplied != required:
                missing = sorted(required - supplied)
                raise ValueError(
                    "Lezard entry %d has a WAV longer than its USA slot; "
                    "all %d Lezard WAVs are required to switch to coherent "
                    "Japanese geometry (missing group(s): %s)"
                    % (entry, len(required), ", ".join(map(str, missing)))
                )
            updated_replacements = {}
            for group_index in sorted(required):
                encoded, path, replacement = current["lezard_encoded"][
                    group_index
                ]
                clip = current["streamed"][group_index][2][0]
                row = lezard_capacities.get(
                    (entry, group_index, clip.sample_index)
                )
                if row is None:
                    raise ValueError(
                        "no cached Japanese geometry for Lezard entry %d "
                        "group %d" % (entry, group_index)
                    )
                target = int(row["jp_payload_bytes"])
                if len(encoded) > target:
                    if not allow_overlong:
                        raise ValueError(
                            "%s needs %d encoded bytes but the coherent "
                            "Japanese slot holds %d"
                            % (path.name, len(encoded), target)
                        )
                    encoded = encoded[:target]
                    replacement = replace(replacement, truncated=True)
                    say(
                        "warning: %s exceeds the coherent Japanese slot "
                        "and will be trimmed" % path.name
                    )
                replacement = replace(replacement, slot_bytes=target)
                current["lezard_encoded"][group_index] = (
                    encoded, path, replacement
                )
                updated_replacements[path] = replacement
            current["lezard_replacements"] = [
                updated_replacements.get(item.path, item)
                for item in current["lezard_replacements"]
            ]
        field_expanded_entries = {
            entry for entry, current in scene_entries.items()
            if any(
                len(encoded[0]) > {
                    clip.sample_index: clip
                    for clip in parse_standalone(
                        current["indexed"][group][4]
                    )
                }[sample].payload_length
                for (group, sample), encoded
                in current["field_encoded"].items()
            )
        }
        expanded = (
            bank_expanded or bool(lezard_expanded_entries) or
            bool(field_expanded_entries)
        )
        overrides = {}
        if bank_expanded:
            overrides.update({
                bank: _rebuild_voice_bank(
                    current["data"], current["encoded"], capacities
                )
                for bank, current in banks.items()
            })
        else:
            for bank, current in banks.items():
                for sub_index, encoded_item in current["encoded"].items():
                    encoded, path, _clip_id, _bank = encoded_item
                    clip = current["clips"][sub_index]
                    fitted = audio.fit_payload(
                        encoded, clip.payload_length, clip.tail_flag,
                        allow_truncate=allow_overlong,
                    )
                    replacement = next(
                        item for item in current["replacements"]
                        if item.sub == sub_index
                    )
                    pending.append((
                        bank, clip.sub_offset + clip.payload_offset,
                        fitted, (replacement,),
                    ))
        for entry, current in scene_entries.items():
            if entry in field_expanded_entries:
                overrides[entry] = _rebuild_alicia_field_entry(
                    current["data"], {
                        key: (value[0], value[1], value[2])
                        for key, value in current["field_encoded"].items()
                    },
                )
            else:
                for (group_index, sample_index), encoded_item in (
                        current["field_encoded"].items()):
                    encoded, _maximum, _path, replacement = encoded_item
                    group = current["indexed"][group_index]
                    clip = {item.sample_index: item for item in parse_standalone(
                        group[4]
                    )}[sample_index]
                    fitted = bytearray(audio.fit_payload(
                        encoded, clip.payload_length, clip.tail_flag
                    ))
                    relative = group[2] + clip.payload_offset
                    original = current["data"]
                    for offset in range(0, len(fitted), audio.FRAME):
                        fitted[offset + 1] = original[relative + offset + 1]
                    pending.append((
                        entry, relative, bytes(fitted), (replacement,)
                    ))
            if entry in lezard_expanded_entries:
                overrides[entry] = _rebuild_lezard_entry(
                    current["data"], entry,
                    current["lezard_encoded"], lezard_capacities,
                )
                continue
            for group_index, encoded_item in current["lezard_encoded"].items():
                encoded, _path, replacement = encoded_item
                _position, _payload, clips = current["streamed"][group_index]
                clip = {item.sample_index: item for item in clips}[
                    replacement.sample
                ]
                fitted = bytearray(audio.fit_payload(
                    encoded, clip.payload_length, clip.tail_flag
                ))
                original = current["data"]
                relative = current["streamed"][group_index][0] + clip.payload_offset
                for offset in range(0, len(fitted), audio.FRAME):
                    fitted[offset + 1] = original[relative + offset + 1]
                pending.append((
                    entry, relative, bytes(fitted), (replacement,)
                ))
        for current in battle_entries.values():
            if not current["replacements"]:
                continue
            stored = encode_battle_entry(
                bytes(current["clear"]), current["signature"]
            )
            pending.append((
                current["replacements"][0].entry, 0, stored,
                tuple(current["replacements"]),
            ))
        for current in movie_entries.values():
            if not current["replacements"]:
                continue
            stored = movie.encode_protected_movie(current["clear"])
            if len(stored) != current["stored_length"]:
                raise ValueError("movie replacement changed its entry size")
            pending.append((
                current["replacements"][0].entry, 0, stored,
                tuple(current["replacements"]),
            ))
        if expanded:
            replacements = tuple(
                replacement
                for current in banks.values()
                for replacement in current["replacements"]
            ) + tuple(
                replacement
                for entry, current in scene_entries.items()
                if entry in field_expanded_entries
                for replacement in current["field_replacements"]
            ) + tuple(
                replacement
                for entry, current in scene_entries.items()
                if entry in lezard_expanded_entries
                for replacement in current["lezard_replacements"]
            ) + tuple(
                replacement
                for _entry, _relative, _payload, items in pending
                for replacement in items
            )
            _repack_resource_overrides(
                source, output, overrides, writes=pending, progress=say
            )
            say("wrote %s" % output)
            return PatchResult(
                output=output, region=region, replacements=replacements
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        say("copy: 0%")
        _copy_with_progress(source, partial, say)
        replacement_count = sum(len(item[3]) for item in pending)
        say("write: applying %d voice replacement(s)" % replacement_count)
        with partial.open("r+b") as candidate:
            for entry, relative, payload, _replacements in pending:
                offset, allocation = entry_span(table, total, entry)
                if relative + len(payload) > allocation:
                    raise ValueError("voice write exceeds resource %d" % entry)
                candidate.seek(offset + relative)
                candidate.write(payload)
            candidate.flush()
            os.fsync(candidate.fileno())
        say("verify: reading every replaced slot back from disk")
        with partial.open("rb") as candidate:
            total, table = read_index(candidate)
            for entry, relative, payload, replacements in pending:
                offset, _allocation = entry_span(table, total, entry)
                candidate.seek(offset + relative)
                if candidate.read(len(payload)) != payload:
                    raise ValueError(
                        "%s voice %04x did not read back byte-for-byte"
                        % (replacements[0].kind, replacements[0].clip_id)
                    )
        if partial.stat().st_size != source.stat().st_size:
            raise ValueError("output ISO size differs from source")
        partial.replace(output)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    say("wrote %s" % output)
    return PatchResult(
        output=output, region=region,
        replacements=tuple(
            replacement
            for _entry, _relative, _payload, replacements in pending
            for replacement in replacements
        ),
    )
