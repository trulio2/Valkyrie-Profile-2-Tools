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

from . import audio, layout
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
            extraction_steps = (
                len(VOICE_BANKS) + len(by_entry) + len(battle_entries)
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
                            emit(
                                "field", field_folder,
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
        with (partial / "manifest.csv").open(
                "w", encoding="utf-8", newline="") as manifest:
            writer = csv.DictWriter(manifest, fieldnames=MANIFEST_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        (partial / "README.txt").write_text(
            "Extracted from %s (%s).\n"
            "Cutscene files are named <bank>-<subfile>-<clip-id>.wav.\n"
            "Unmapped files are named unmapped-<entry>-<sample>-<clip-id>-"
            "<zone>.wav.\n"
            "Battle files are named battle-<entry>-<sample>-<clip-id>-"
            "<zone>.wav.\n"
            "Field files are named field-<entry>-<group>-<sample>-<clip-id>-"
            "<zone>.wav, and lezard/ files use the same shape.\n"
            "Folders named by number are cutscene resources; unmapped files "
            "with a known scene owner are placed there too. Banks holding an "
            "unverified alternate performance go under that scene's "
            "alternate-takes/ when its owner is known, otherwise under "
            "unmapped/alternate-takes/.\n"
            % (source.name, boot),
            encoding="utf-8",
        )
        partial.replace(destination)
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        raise
    say("wrote %d clips to %s" % (len(rows), destination))
    return ExtractionResult(
        output=destination,
        region=region,
        banks=len(VOICE_BANKS),
        clips=len(rows),
        mapped_banks=mapped,
        unmapped_clips=unmapped_count,
        battle_clips=battle_count,
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


def discover_replacements(folder):
    """Identify exported and legacy dub-kit WAVs below *folder*."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("voice folder does not exist: %s" % folder)
    wavs = sorted(path for path in folder.rglob("*")
                  if path.is_file() and path.suffix.lower() == ".wav")
    if not wavs:
        raise ValueError("voice folder contains no WAV files: %s" % folder)
    legacy = _old_manifest_identities(_find_manifest(folder))
    found = {}
    unknown = []
    for path in wavs:
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
            "%d WAV file(s) have no cutscene/unmapped/battle identity: %s%s"
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


def import_japanese_audio(base, japan, output=None, progress=None):
    """Build a Japanese-audio variant of a supported USA or PAL ISO."""
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
        resources = tuple(sorted(
            set(VOICE_BANKS) | standalone | set(battle) | set(protected) |
            set(hybrids)
        ))
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
        if (base_values[total + GLOBAL_BATTLE_RESOURCE] and
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


def patch_iso(source, voices, output=None, progress=None,
              allow_overlong=False):
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
    selected = discover_replacements(voices)
    pending = []
    with source.open("rb") as handle:
        total, table = read_index(handle)
        banks = {}
        entries = {}
        battle_entries = {}
        scene_entries = {}
        allowed_unmapped = {
            (voice.entry, voice.sample, voice.clip_id, voice.zone)
            for voice in load_unmapped_map().values()
        }
        prefix_rank = {"battle": 2, "field": 3, "lezard": 4}

        def identity_order(item):
            identity = item[0]
            if identity[0] in prefix_rank:
                return (prefix_rank[identity[0]], *identity[1:])
            return (0 if len(identity) == 3 else 1, *identity)

        for identity, path in sorted(selected.items(), key=identity_order):
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
                    }
                current = scene_entries[entry]
                if kind == "field":
                    if group_index >= len(current["indexed"]):
                        raise ValueError(
                            "%s targets missing indexed group %d in "
                            "entry %d" % (path.name, group_index, entry)
                        )
                    absolute = current["offset"] + current["indexed"][
                        group_index
                    ][2]
                    clips = parse_standalone(
                        current["indexed"][group_index][4]
                    )
                else:
                    if group_index >= len(current["streamed"]):
                        raise ValueError(
                            "%s targets missing lezard group %d in entry %d"
                            % (path.name, group_index, entry)
                        )
                    absolute = current["offset"] + current["streamed"][
                        group_index
                    ][0]
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
                absolute += clip.payload_offset
                original_payload = current["data"][
                    absolute - current["offset"]:
                    absolute - current["offset"] + clip.payload_length
                ]
                replacement = Replacement(
                    path=path, kind=kind, entry=entry, sub=group_index,
                    sample=sample_index, zone=zone, clip_id=clip_id,
                    slot_bytes=clip.payload_length,
                )
                label = "%s entry %d group %d sample %d" % (
                    kind, entry, group_index, sample_index
                )
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
                    banks[bank] = (bank_offset, {
                        item.sub_index: item for item in parse_bank(bank_data)
                    })
                bank_offset, clips = banks[bank]
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
                replacement = Replacement(
                    path=path, kind="cutscene", bank=bank, sub=sub_index,
                    clip_id=clip_id, slot_bytes=clip.payload_length,
                )
                label = "bank %d subfile %d" % (bank, sub_index)
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
                absolute = entry_offset + clip.payload_offset
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
                pending.append((absolute, fitted, (replacement,)))
            say("prepare: %s <- %s" % (label, path.name))
            if truncated:
                say("warning: %s is overlong and will be trimmed to %.3fs" % (
                    path.name, maximum
                ))
        for current in battle_entries.values():
            stored = encode_battle_entry(
                bytes(current["clear"]), current["signature"]
            )
            pending.append((
                current["offset"], stored,
                tuple(current["replacements"]),
            ))
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        say("copy: 0%")
        _copy_with_progress(source, partial, say)
        replacement_count = sum(len(item[2]) for item in pending)
        say("write: applying %d voice replacement(s)" % replacement_count)
        with partial.open("r+b") as candidate:
            for offset, payload, _replacements in pending:
                candidate.seek(offset)
                candidate.write(payload)
            candidate.flush()
            os.fsync(candidate.fileno())
        say("verify: reading every replaced slot back from disk")
        with partial.open("rb") as candidate:
            total, table = read_index(candidate)
            for offset, payload, replacements in pending:
                candidate.seek(offset)
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
            for _offset, _payload, replacements in pending
            for replacement in replacements
        ),
    )
