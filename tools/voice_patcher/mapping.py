# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from ..scripts.paths import DATA_DIR


CUTSCENE_FIELDS = (
    "bank", "sub", "resource", "voice_scene", "message_id", "scene",
    "scene_line", "speaker", "context", "confidence", "evidence", "notes",
)
CUTSCENE_OUTPUT_FIELDS = (
    "file", "bank", "sub", "clip_id", "voice_scene", "message_id", "scene",
    "scene_line", "speaker", "context", "confidence", "evidence", "notes",
)
BATTLE_FIELDS = (
    "map_id", "slots", "speaker", "event", "confidence", "evidence", "notes",
)
BATTLE_OUTPUT_FIELDS = (
    "map_id", "example_file", "occurrences", "clip_ids", "slots", "speaker",
    "event", "confidence", "evidence", "notes",
)


@dataclass(frozen=True)
class BattleGroup:
    map_id: str
    slots: tuple[tuple[int, int, int], ...]
    speaker: str
    event: str
    confidence: str
    evidence: str
    notes: str


def _read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def load_cutscene_map(path=None):
    """Return the reviewed cutscene metadata keyed by ``(bank, sub)``."""
    path = Path(path or DATA_DIR / "cutscene-voice-map.csv")
    mappings = {}
    for row in _read_rows(path):
        missing = [field for field in CUTSCENE_FIELDS if field not in row]
        if missing:
            raise ValueError(
                "cutscene voice map is missing column(s): %s"
                % ", ".join(missing)
            )
        key = (int(row["bank"]), int(row["sub"]))
        if key in mappings:
            raise ValueError(
                "duplicate cutscene voice map row: %d sub %d" % key
            )
        mappings[key] = {field: row.get(field, "") for field in CUTSCENE_FIELDS}
    return mappings


def _parse_slot(value):
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError("invalid battle voice slot: %s" % value)
    entry, sample, zone = (int(part) for part in parts)
    if min(entry, sample, zone) < 0:
        raise ValueError("battle voice slot values must be non-negative")
    return entry, sample, zone


def load_battle_groups(path=None):
    """Return reviewed battle groups and a structural-slot reverse index."""
    path = Path(path or DATA_DIR / "battle-voice-groups.csv")
    groups = {}
    by_slot = {}
    for row in _read_rows(path):
        missing = [field for field in BATTLE_FIELDS if field not in row]
        if missing:
            raise ValueError(
                "battle voice groups are missing column(s): %s"
                % ", ".join(missing)
            )
        map_id = row["map_id"].strip()
        if not map_id or map_id in groups:
            raise ValueError("missing or duplicate battle map id: %s" % map_id)
        slots = tuple(_parse_slot(value) for value in row["slots"].split())
        if not slots:
            raise ValueError("battle map %s has no structural slots" % map_id)
        group = BattleGroup(
            map_id=map_id,
            slots=slots,
            speaker=row["speaker"].strip(),
            event=row["event"].strip(),
            confidence=row["confidence"].strip(),
            evidence=row["evidence"].strip(),
            notes=row["notes"].strip(),
        )
        groups[map_id] = group
        for slot in slots:
            if slot in by_slot:
                raise ValueError(
                    "battle slot %d:%d:%d belongs to more than one map"
                    % slot
                )
            by_slot[slot] = group
    return groups, by_slot


def _write(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_voice_maps(root, manifest_rows, cutscene_map=None,
                     battle_groups=None):
    """Write review CSVs beside an extraction without moving any WAV files."""
    root = Path(root)
    cutscene_map = cutscene_map if cutscene_map is not None else load_cutscene_map()
    if battle_groups is None:
        battle_groups, battle_by_slot = load_battle_groups()
    else:
        battle_by_slot = {
            slot: group for group in battle_groups.values() for slot in group.slots
        }

    by_folder = {}
    for row in manifest_rows:
        if row["kind"] not in {"cutscene", "alternate"}:
            continue
        relative = Path(row["relative_path"])
        folder = root / relative.parent
        mapped = (
            cutscene_map.get((int(row["bank"]), int(row["sub"])), {})
            if row["kind"] == "cutscene" else {}
        )
        output = {
            "file": relative.name,
            "bank": row["bank"],
            "sub": row["sub"],
            "clip_id": row["clip_id"],
        }
        for field in CUTSCENE_OUTPUT_FIELDS[4:]:
            output[field] = mapped.get(field, "")
        if not output["confidence"]:
            output["confidence"] = "unmapped"
        by_folder.setdefault(folder, []).append(output)
    for folder, rows in by_folder.items():
        rows.sort(key=lambda row: (int(row["bank"]), int(row["sub"])))
        _write(folder / "voice-map.csv", CUTSCENE_OUTPUT_FIELDS, rows)

    present = {}
    for row in manifest_rows:
        if row["kind"] != "battle":
            continue
        slot = (int(row["entry"]), int(row["sample"]), int(row["zone"]))
        group = battle_by_slot.get(slot)
        if group is None:
            raise ValueError(
                "battle extraction slot %d:%d:%d has no voice-map group" % slot
            )
        present.setdefault(group.map_id, []).append(row)

    battle_rows = []
    for map_id, rows in sorted(present.items()):
        group = battle_groups[map_id]
        rows.sort(key=lambda row: (
            int(row["entry"]), int(row["sample"]), int(row["zone"])
        ))
        battle_rows.append({
            "map_id": map_id,
            "example_file": rows[0]["relative_path"],
            "occurrences": len(rows),
            "clip_ids": " ".join(sorted({row["clip_id"] for row in rows})),
            "slots": " ".join(
                "%d:%d:%d" % slot for slot in group.slots
            ),
            "speaker": group.speaker,
            "event": group.event,
            "confidence": group.confidence or "unmapped",
            "evidence": group.evidence,
            "notes": group.notes,
        })
    _write(root / "battle" / "voice-map.csv", BATTLE_OUTPUT_FIELDS, battle_rows)
    return sum(len(rows) for rows in by_folder.values()), len(battle_rows)


def map_existing_extraction(folder):
    """Regenerate review CSVs for an already extracted language folder."""
    folder = Path(folder).expanduser().resolve()
    manifest = folder / "manifest.csv"
    if not manifest.is_file():
        raise ValueError("voice extraction has no manifest.csv: %s" % folder)
    rows = _read_rows(manifest)
    return write_voice_maps(folder, rows)
