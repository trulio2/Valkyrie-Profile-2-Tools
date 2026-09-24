# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Precomputed USA/Japanese voice-slot capacity data."""

from __future__ import annotations

import csv
from pathlib import Path

from ..scripts import disc_identity, vp2_dcms, vp2_iso_space
from ..scripts.paths import DATA_DIR
from .layout import (
    JAPAN_BOOT, USA_BOOT, VOICE_BANKS, SECTOR, entry_span, parse_bank,
    parse_standalone,
)


OUTPUT_NAME = "voice-capacities.csv"
LEZARD_OUTPUT_NAME = "lezard-capacities.csv"
ALICIA_ALIAS_NAME = "alicia-field-aliases.csv"
LEZARD_ENTRIES = (1011, 1013)
FIELDS = (
    "bank", "sub", "clip_id",
    "usa_payload_bytes", "jp_payload_bytes",
    "usa_frames", "jp_frames",
    "usa_seconds", "jp_seconds",
    "max_payload_bytes", "max_seconds", "max_subfile_bytes",
    "max_region", "max_header_patch", "max_flag_patch",
)
SAMPLES_PER_FRAME = 28
SAMPLE_RATE = 24000
FRAME_BYTES = 16

LEZARD_FIELDS = (
    "entry", "group", "sample", "clip_id", "zone",
    "usa_payload_bytes", "jp_payload_bytes",
    "usa_seconds", "jp_seconds",
    "usa_group_bytes", "jp_group_bytes",
    "jp_header_patch", "jp_controls", "jp_flag_patch", "jp_terminal_patch",
    "jp_trailer_patch",
    "max_payload_bytes", "max_seconds", "max_group_bytes",
    "max_region", "max_header_patch", "max_flag_patch",
)


def _read_banks(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("ISO does not exist: %s" % path)
    try:
        boot, _region = disc_identity.identify(path)
    except disc_identity.DiscError as exc:
        raise ValueError(str(exc)) from exc
    if boot not in (USA_BOOT, JAPAN_BOOT):
        raise ValueError(
            "capacity source must be USA (%s) or Japanese (%s), got %s"
            % (USA_BOOT, JAPAN_BOOT, boot)
        )
    with path.open("rb") as handle:
        _seed, _offset, total, table = vp2_iso_space.read_index(handle)
        result = {}
        for bank in VOICE_BANKS:
            offset, length = entry_span(table, total, bank)
            handle.seek(offset)
            data = handle.read(length)
            if len(data) != length:
                raise ValueError("voice bank %d extends past the ISO" % bank)
            for clip in parse_bank(data):
                subfile = data[
                    clip.sub_offset:clip.sub_offset + clip.sub_length
                ]
                payload = subfile[
                    clip.payload_offset:
                    clip.payload_offset + clip.payload_length
                ]
                result[(bank, clip.sub_index)] = {
                    "clip_id": clip.clip_id,
                    "payload_bytes": clip.payload_length,
                    "subfile_bytes": clip.sub_length,
                    "stream_bytes": int.from_bytes(subfile[0x14:0x18], "little"),
                    "header": subfile[:clip.payload_offset],
                    "flags": tuple(
                        (offset + 1, payload[offset + 1])
                        for offset in range(0, len(payload), FRAME_BYTES)
                        if payload[offset + 1]
                    ),
                }
    return boot, result


def _seconds(payload_bytes):
    frames = payload_bytes // FRAME_BYTES
    return frames * SAMPLES_PER_FRAME / SAMPLE_RATE


def _streamed_groups(data):
    entries = vp2_dcms.parse_pk1(data)
    if not entries:
        return None
    content_end = max(offset + length for _tag, offset, length in entries)
    tail_start = (content_end + SECTOR - 1) // SECTOR * SECTOR
    groups = []
    position = tail_start
    while position < len(data):
        position = data.find(b"SEQW", position)
        if position < 0:
            break
        if position % 16 == 0:
            try:
                clips = parse_standalone(data[position:])
            except ValueError:
                pass
            else:
                groups.append((position, clips))
        position += 4
    return (tail_start, tuple(groups)) if groups else None


def _read_lezard(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ValueError("ISO does not exist: %s" % path)
    try:
        boot, _region = disc_identity.identify(path)
    except disc_identity.DiscError as exc:
        raise ValueError(str(exc)) from exc
    if boot not in (USA_BOOT, JAPAN_BOOT):
        raise ValueError("Lezard capacity source has an unsupported release")
    result = {}
    with path.open("rb") as handle:
        _seed, _offset, total, table = vp2_iso_space.read_index(handle)
        for entry in LEZARD_ENTRIES:
            offset, length = entry_span(table, total, entry)
            handle.seek(offset)
            data = handle.read(length)
            found = _streamed_groups(data)
            if found is None:
                raise ValueError("Lezard entry %d has no streamed audio" % entry)
            _tail_start, groups = found
            for group, (position, clips) in enumerate(groups):
                end = (groups[group + 1][0]
                       if group + 1 < len(groups) else len(data))
                if len(clips) != 1:
                    raise ValueError(
                        "Lezard entry %d group %d has %d samples"
                        % (entry, group, len(clips))
                    )
                clip = clips[0]
                payload = data[
                    position + clip.payload_offset:
                    position + clip.payload_offset + clip.payload_length
                ]
                terminal = next(
                    (
                        frame_offset
                        for frame_offset in range(
                            0, len(payload), FRAME_BYTES
                        )
                        if payload[frame_offset + 1] == 7
                    ),
                    len(payload),
                )
                group_data = data[position:end]
                result[(entry, group, clip.sample_index)] = {
                    "clip_id": clip.clip_id,
                    "zone": clip.zone,
                    "payload_bytes": clip.payload_length,
                    "group_bytes": len(group_data),
                    "stream_bytes": int.from_bytes(
                        group_data[0x14:0x18], "little"
                    ),
                    "header": group_data[:clip.payload_offset],
                    "trailer": group_data[
                        clip.payload_offset + clip.payload_length:
                    ],
                    "flags": tuple(
                        (flag_offset + 1, payload[flag_offset + 1])
                        for flag_offset in range(0, len(payload), FRAME_BYTES)
                        if payload[flag_offset + 1]
                    ),
                    "controls": bytes(
                        payload[frame_offset]
                        for frame_offset in range(0, terminal, FRAME_BYTES)
                    ),
                    "terminal_patch": tuple(
                        (frame_offset + byte_offset,
                         payload[frame_offset + byte_offset])
                        for frame_offset in range(
                            0, len(payload), FRAME_BYTES
                        )
                        if payload[frame_offset + 1] == 7
                        for byte_offset in range(FRAME_BYTES)
                        if payload[frame_offset + byte_offset]
                    ),
                }
    return boot, result


def build_lezard_capacity_rows(usa, japan):
    """Read both releases and return matched Lezard streamed-slot rows."""
    usa_boot, usa_rows = _read_lezard(usa)
    jp_boot, jp_rows = _read_lezard(japan)
    if usa_boot != USA_BOOT or jp_boot != JAPAN_BOOT:
        raise ValueError("capacity sources must be supplied as USA then Japanese")
    if set(usa_rows) != set(jp_rows):
        raise ValueError("USA/Japanese Lezard streamed slots do not match")
    rows = []
    for key in sorted(usa_rows):
        usa_row, jp_row = usa_rows[key], jp_rows[key]
        if ((usa_row["clip_id"], usa_row["zone"]) !=
                (jp_row["clip_id"], jp_row["zone"])):
            raise ValueError(
                "Lezard identity mismatch at entry %d group %d sample %d"
                % key
            )
        maximum = max(
            (usa_row, jp_row),
            key=lambda row: (row["payload_bytes"], row["stream_bytes"]),
        )
        if len(usa_row["header"]) != len(maximum["header"]):
            raise ValueError("regional Lezard voice headers differ in size")
        max_header_patch = ";".join(
            "%x:%02x" % (offset, value)
            for offset, (original, value) in enumerate(zip(
                usa_row["header"], maximum["header"]
            ))
            if original != value
        )
        jp_header_patch = ";".join(
            "%x:%02x" % (offset, value)
            for offset, (original, value) in enumerate(zip(
                usa_row["header"], jp_row["header"]
            ))
            if original != value
        )
        rows.append({
            "entry": key[0], "group": key[1], "sample": key[2],
            "clip_id": "%04x" % usa_row["clip_id"],
            "zone": usa_row["zone"],
            "usa_payload_bytes": usa_row["payload_bytes"],
            "jp_payload_bytes": jp_row["payload_bytes"],
            "usa_seconds": "%.6f" % _seconds(usa_row["payload_bytes"]),
            "jp_seconds": "%.6f" % _seconds(jp_row["payload_bytes"]),
            "usa_group_bytes": usa_row["group_bytes"],
            "jp_group_bytes": jp_row["group_bytes"],
            "jp_header_patch": jp_header_patch,
            "jp_controls": jp_row["controls"].hex(),
            "jp_flag_patch": ";".join(
                "%x:%02x" % item for item in jp_row["flags"]
            ),
            "jp_terminal_patch": ";".join(
                "%x:%02x" % item for item in jp_row["terminal_patch"]
            ),
            "jp_trailer_patch": ";".join(
                "%x:%02x" % (offset, value)
                for offset, value in enumerate(jp_row["trailer"])
                if value
            ),
            "max_payload_bytes": maximum["payload_bytes"],
            "max_seconds": "%.6f" % _seconds(maximum["payload_bytes"]),
            "max_group_bytes": maximum["group_bytes"],
            "max_region": "jp" if maximum is jp_row else "usa",
            "max_header_patch": max_header_patch,
            "max_flag_patch": ";".join(
                "%x:%02x" % item for item in maximum["flags"]
            ),
        })
    return rows


def build_capacity_rows(usa, japan):
    """Read both ISOs and return matched capacity rows."""
    usa_boot, usa_rows = _read_banks(usa)
    jp_boot, jp_rows = _read_banks(japan)
    if usa_boot != USA_BOOT or jp_boot != JAPAN_BOOT:
        raise ValueError("capacity sources must be supplied as USA then Japanese")
    if set(usa_rows) != set(jp_rows):
        missing_usa = sorted(set(jp_rows) - set(usa_rows))
        missing_jp = sorted(set(usa_rows) - set(jp_rows))
        raise ValueError(
            "USA/Japanese voice banks do not match (missing USA: %s; "
            "missing Japanese: %s)" % (missing_usa[:5], missing_jp[:5])
        )
    rows = []
    for key in sorted(usa_rows):
        usa_row, jp_row = usa_rows[key], jp_rows[key]
        if usa_row["clip_id"] != jp_row["clip_id"]:
            raise ValueError(
                "clip identity mismatch at bank %d subfile %d: %04x != %04x"
                % (key[0], key[1], usa_row["clip_id"], jp_row["clip_id"])
            )
        usa_bytes = usa_row["payload_bytes"]
        jp_bytes = jp_row["payload_bytes"]
        maximum = max(
            (usa_row, jp_row),
            key=lambda row: (row["payload_bytes"], row["stream_bytes"]),
        )
        max_region = "jp" if maximum is jp_row else "usa"
        if len(usa_row["header"]) != len(maximum["header"]):
            raise ValueError(
                "regional voice headers differ in size at bank %d subfile %d"
                % key
            )
        header_patch = ";".join(
            "%x:%02x" % (offset, value)
            for offset, (original, value) in enumerate(zip(
                usa_row["header"], maximum["header"]
            ))
            if original != value
        )
        flag_patch = ";".join(
            "%x:%02x" % item for item in maximum["flags"]
        )
        rows.append({
            "bank": key[0],
            "sub": key[1],
            "clip_id": "%04x" % usa_row["clip_id"],
            "usa_payload_bytes": usa_bytes,
            "jp_payload_bytes": jp_bytes,
            "usa_frames": usa_bytes // FRAME_BYTES,
            "jp_frames": jp_bytes // FRAME_BYTES,
            "usa_seconds": "%.6f" % _seconds(usa_bytes),
            "jp_seconds": "%.6f" % _seconds(jp_bytes),
            "max_payload_bytes": max(usa_bytes, jp_bytes),
            "max_seconds": "%.6f" % _seconds(max(usa_bytes, jp_bytes)),
            "max_subfile_bytes": maximum["subfile_bytes"],
            "max_region": max_region,
            "max_header_patch": header_patch,
            "max_flag_patch": flag_patch,
        })
    return rows


def write_capacity_csv(usa, japan, output=None):
    """Write the one-time USA/Japanese capacity lookup CSV."""
    output = Path(output or DATA_DIR / OUTPUT_NAME).expanduser().resolve()
    rows = build_capacity_rows(usa, japan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return output, len(rows)


def load_capacity_csv(path=None):
    """Load the precomputed lookup keyed by ``(bank, sub)``."""
    path = Path(path or DATA_DIR / OUTPUT_NAME)
    with path.open(encoding="utf-8", newline="") as source:
        return {
            (int(row["bank"]), int(row["sub"])): row
            for row in csv.DictReader(source)
        }


def write_lezard_capacity_csv(usa, japan, output=None):
    """Write the one-time USA/Japanese Lezard capacity lookup CSV."""
    output = Path(
        output or DATA_DIR / LEZARD_OUTPUT_NAME
    ).expanduser().resolve()
    rows = build_lezard_capacity_rows(usa, japan)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(
            target, fieldnames=LEZARD_FIELDS, lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)
    return output, len(rows)


def load_lezard_capacity_csv(path=None):
    """Load cached Lezard capacities keyed by ``(entry, group, sample)``."""
    path = Path(path or DATA_DIR / LEZARD_OUTPUT_NAME)
    with path.open(encoding="utf-8", newline="") as source:
        return {
            (int(row["entry"]), int(row["group"]), int(row["sample"])): row
            for row in csv.DictReader(source)
        }


def load_alicia_field_aliases(path=None):
    """Load canonical Alicia field WAV identities and every area copy."""
    path = Path(path or DATA_DIR / ALICIA_ALIAS_NAME)
    aliases = {}
    targets = set()
    with path.open(encoding="utf-8-sig", newline="") as source:
        for row in csv.DictReader(source):
            canonical = (
                int(row["canonical_entry"]), int(row["canonical_group"]),
                int(row["canonical_sample"]),
                int(row["canonical_clip_id"], 16),
                int(row["canonical_zone"]),
            )
            target = (
                int(row["target_entry"]), int(row["target_group"]),
                int(row["target_sample"]), int(row["target_clip_id"], 16),
                int(row["target_zone"]),
            )
            if target in targets:
                raise ValueError("duplicate Alicia field alias: %r" % (target,))
            targets.add(target)
            aliases.setdefault(canonical, {})[target] = int(
                row["max_payload_bytes"]
            )
    if not aliases:
        raise ValueError("Alicia field alias map is empty")
    return aliases
