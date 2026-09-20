# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Structural identities inside VP2 voice banks."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import struct
import sys

from ..scripts.paths import DATA_DIR
from ..scripts.triace_ps2_unpack import SECTOR, load_table


FIRST_VOICE_BANK = 1482
LAST_VOICE_BANK = 1566
VOICE_BANKS = tuple(range(FIRST_VOICE_BANK, LAST_VOICE_BANK + 1))
USA_BOOT = "SLUS_214.52"
JAPAN_BOOT = "SLPM_664.19"
PAL_BOOTS = {
    "SLES_546.44": "pal-en",
    "SLES_546.45": "fr",
    "SLES_546.46": "de",
    "SLES_546.47": "it",
    "SLES_546.48": "es",
}
SUPPORTED_BOOTS = {USA_BOOT: "en", JAPAN_BOOT: "jp", **PAL_BOOTS}
VOICE_SOURCE_BOOTS = frozenset((USA_BOOT, JAPAN_BOOT))
JAPANESE_AUDIO_TARGET_BOOTS = frozenset((USA_BOOT, *PAL_BOOTS))
EXPORTED_NAME = re.compile(
    r"^(?P<bank>\d{4})-(?P<sub>\d{3})-(?P<clip>[0-9a-fA-F]{4})\.wav$",
    re.IGNORECASE,
)
UNMAPPED_NAME = re.compile(
    r"^unmapped-(?P<entry>\d{4})-(?P<sample>\d{3})-"
    r"(?P<clip>[0-9a-fA-F]{4})-(?P<zone>\d+)\.wav$",
    re.IGNORECASE,
)
BATTLE_NAME = re.compile(
    r"^battle-(?P<entry>\d{4})-(?P<sample>\d{3})-"
    r"(?P<clip>[0-9a-fA-F]{4})-(?P<zone>\d+)\.wav$",
    re.IGNORECASE,
)
FIELD_NAME = re.compile(
    r"^field-(?P<entry>\d{4})-(?P<group>\d{2})-(?P<sample>\d{3})-"
    r"(?P<clip>[0-9a-fA-F]{4})-(?P<zone>\d+)\.wav$",
    re.IGNORECASE,
)
LEZARD_NAME = re.compile(
    r"^lezard-(?P<entry>\d{4})-(?P<group>\d{2})-(?P<sample>\d{3})-"
    r"(?P<clip>[0-9a-fA-F]{4})-(?P<zone>\d+)\.wav$",
    re.IGNORECASE,
)

BATTLE_SEEDS = {
    0x9E636CDE: 0x00E6373A,
    0xAFA9E715: 0x0056F1E7,
    0xF43962BD: 0x000D94AF,
    0x5D63FC57: 0x0006107D,
    0x31BD3633: 0x006C6C09,
    0xA8493A7E: 0x003E6D5A,
    0xB70803AD: 0x000098FF,
    0x81C37347: 0x0177CACD,
    0x6C4B2898: 0x00105150,
    0x05C59B54: 0x0034A83C,
}
BATTLE_MULTIPLIER = 0x000323BD
BATTLE_INCREMENT = 0x000075BB
MASK32 = 0xFFFFFFFF


@dataclass(frozen=True)
class BankOwner:
    bank: int
    resource: int | None
    voice_scene: int | None
    slot_count: int
    category: str = "cutscene"


@dataclass(frozen=True)
class Clip:
    sub_index: int
    sub_offset: int
    sub_length: int
    clip_id: int
    payload_offset: int
    payload_length: int
    tail_flag: int


@dataclass(frozen=True)
class UnmappedVoice:
    entry: int
    sample: int
    clip_id: int
    zone: int
    resource: int | None = None


@dataclass(frozen=True)
class StandaloneClip:
    sample_index: int
    clip_id: int
    zone: int
    payload_offset: int
    payload_length: int
    tail_flag: int


def load_bank_map(path=None):
    path = Path(path or DATA_DIR / "voice-bank-map.csv")
    owners = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            owner = BankOwner(
                bank=int(row["bank"]),
                resource=(
                    int(row["resource"])
                    if row["resource"].strip()
                    else None
                ),
                voice_scene=(
                    int(row["voice_scene"])
                    if row["voice_scene"].strip()
                    else None
                ),
                slot_count=int(row["slot_count"]),
                category=(row.get("category") or "cutscene").strip(),
            )
            if owner.bank in owners:
                raise ValueError("duplicate voice-bank map row: %d" % owner.bank)
            if owner.bank not in VOICE_BANKS:
                raise ValueError("voice-bank map row is outside 1482..1566")
            if owner.slot_count <= 0:
                raise ValueError("voice-bank map slot count must be positive")
            if owner.category not in {"cutscene", "alternate"}:
                raise ValueError("unknown voice-bank category: %s"
                                 % owner.category)
            if owner.category == "cutscene" and owner.resource is None:
                raise ValueError("cutscene banks need a resource")
            if owner.category == "alternate" and owner.voice_scene is not None:
                raise ValueError("alternate banks cannot claim a voice scene")
            owners[owner.bank] = owner
    return owners


def load_unmapped_map(path=None):
    """Load language-dependent samples that have no proven scene owner."""
    path = Path(path or DATA_DIR / "unmapped-voice-map.csv")
    voices = {}
    with path.open(encoding="utf-8", newline="") as source:
        for row in csv.DictReader(source):
            resource = (row.get("resource") or "").strip()
            voice = UnmappedVoice(
                entry=int(row["entry"]),
                sample=int(row["sample"]),
                clip_id=int(row["clip_id"], 16),
                zone=int(row["zone"]),
                resource=int(resource) if resource else None,
            )
            identity = (voice.entry, voice.sample)
            if identity in voices:
                raise ValueError(
                    "duplicate unmapped-voice row: %d sample %d" % identity
                )
            if voice.entry < 0 or voice.sample < 0 or voice.zone < 0:
                raise ValueError("unmapped-voice values must be non-negative")
            if voice.resource is not None and voice.resource <= 0:
                raise ValueError("unmapped-voice resource must be positive")
            voices[identity] = voice
    return voices


def entry_span(table, total, index):
    if not 0 <= index < total:
        raise ValueError("voice bank %d is outside the disc index" % index)
    offset = table[index] * SECTOR
    length = table[total + index] * SECTOR
    if not length:
        raise ValueError("voice bank %d has no allocation" % index)
    return offset, length


def read_index(handle):
    name, total, table = load_table(handle)
    if name != "VP2":
        raise ValueError("the disc index belongs to %s, not VP2" % name)
    return total, table


def parse_bank(data: bytes) -> tuple[Clip, ...]:
    """Parse the PK2-like voice-bank table, including its five slack banks."""
    if len(data) < 8:
        raise ValueError("voice bank is too short for its subfile table")
    count = struct.unpack_from("<I", data, 0)[0]
    if not 0 < count <= 1024 or 4 + count * 4 > len(data):
        raise ValueError("invalid voice-bank subfile count: %d" % count)
    clips = []
    for sub_index in range(count):
        start_sector, sector_count = struct.unpack_from(
            "<HH", data, 4 + sub_index * 4
        )
        if not sector_count:
            continue
        sub_offset = start_sector * SECTOR
        sub_length = sector_count * SECTOR
        if sub_offset < 4 + count * 4 or sub_offset + sub_length > len(data):
            raise ValueError(
                "subfile %d extends outside its voice bank" % sub_index
            )
        subfile = data[sub_offset:sub_offset + sub_length]
        if subfile[:4] != b"SEQW":
            raise ValueError("subfile %d is not SEQW" % sub_index)
        if len(subfile) < 0x1C:
            raise ValueError("subfile %d has a truncated SEQW header" % sub_index)
        wav_offset = struct.unpack_from("<I", subfile, 4)[0]
        if wav_offset + 0x14 > len(subfile) or subfile[
                wav_offset:wav_offset + 4] != b"WAV ":
            raise ValueError("subfile %d has no valid WAV chunk" % sub_index)
        header_length = struct.unpack_from("<I", subfile, wav_offset + 8)[0]
        payload_length = struct.unpack_from("<I", subfile, wav_offset + 0x10)[0]
        payload_offset = (wav_offset + header_length + 0x0F) & ~0x0F
        if (payload_length <= 0 or payload_length % 16 or
                payload_offset + payload_length > len(subfile)):
            raise ValueError("subfile %d has an invalid ADPCM payload" % sub_index)
        clip_id = struct.unpack_from("<H", subfile, 0x1A)[0]
        tail_flag = subfile[payload_offset + payload_length - 15]
        clips.append(Clip(
            sub_index=sub_index,
            sub_offset=sub_offset,
            sub_length=sub_length,
            clip_id=clip_id,
            payload_offset=payload_offset,
            payload_length=payload_length,
            tail_flag=tail_flag,
        ))
    if not clips:
        raise ValueError("voice bank contains no SEQW clips")
    return tuple(clips)


def parse_standalone(data: bytes) -> tuple[StandaloneClip, ...]:
    """Parse independently addressed samples from one standalone SEQW."""
    if len(data) < 0x40 or data[:4] != b"SEQW":
        raise ValueError("standalone voice entry is not SEQW")
    wav_offset = struct.unpack_from("<I", data, 4)[0]
    if (wav_offset + 0x20 > len(data) or
            data[wav_offset:wav_offset + 4] != b"WAV "):
        raise ValueError("standalone SEQW has no valid WAV chunk")
    header_length, table_length = struct.unpack_from(
        "<II", data, wav_offset + 8
    )
    payload_length = struct.unpack_from("<I", data, wav_offset + 0x10)[0]
    payload_offset = (wav_offset + header_length + 0x0F) & ~0x0F
    table_start = wav_offset + 0x20
    table_end = table_start + table_length
    if (header_length < 0x20 or table_end > wav_offset + header_length or
            payload_length <= 0 or payload_length % 16 or
            payload_offset + payload_length > len(data)):
        raise ValueError("standalone SEQW has invalid WAV geometry")

    samples = []
    position = table_start
    while position < table_end:
        if position + 4 > table_end:
            raise ValueError("standalone SEQW has a truncated sample record")
        record_length, clip_id = struct.unpack_from("<HH", data, position)
        if record_length < 0x18 or position + record_length > table_end:
            raise ValueError("standalone SEQW has an invalid sample record")
        cursor = position + 4
        zone = 0
        while (cursor + 0x14 <= position + record_length and
               struct.unpack_from("<I", data, cursor)[0] == 0x14):
            start = struct.unpack_from("<I", data, cursor + 0x10)[0]
            samples.append((clip_id, zone, start))
            cursor += 0x14
            zone += 1
        if not zone or any(data[cursor:position + record_length]):
            raise ValueError("standalone SEQW has an unknown sample record")
        position += record_length
    if position != table_end or not samples:
        raise ValueError("standalone SEQW has no complete sample table")

    starts = [sample[2] for sample in samples]
    if (starts != sorted(starts) or len(starts) != len(set(starts)) or
            any(start % 16 or start >= payload_length for start in starts)):
        raise ValueError("standalone SEQW has invalid sample offsets")
    clips = []
    for sample_index, (clip_id, zone, start) in enumerate(samples):
        end = (starts[sample_index + 1]
               if sample_index + 1 < len(starts) else payload_length)
        slot = data[payload_offset + start:payload_offset + end]
        flags = [slot[offset + 1] for offset in range(0, len(slot), 16)
                 if slot[offset + 1]]
        clips.append(StandaloneClip(
            sample_index=sample_index,
            clip_id=clip_id,
            zone=zone,
            payload_offset=payload_offset + start,
            payload_length=end - start,
            tail_flag=flags[-1] if flags else 0,
        ))
    return tuple(clips)


def battle_signature(data: bytes) -> int | None:
    """Return the loader-recognized signature for an encrypted sound entry."""
    if len(data) < 4:
        return None
    signature = struct.unpack_from("<I", data)[0]
    return signature if signature in BATTLE_SEEDS else None


def transform_battle_entry(data: bytes, signature: int) -> bytes:
    """Apply the symmetric battle-sound transform with *signature*'s seed."""
    if signature not in BATTLE_SEEDS:
        raise ValueError("unknown battle-voice signature %08x" % signature)
    if len(data) % 4:
        raise ValueError("battle-voice entry length is not a multiple of four")
    state = BATTLE_SEEDS[signature]
    output = bytearray(data)
    if sys.byteorder == "little":
        words = memoryview(output).cast("I")
        for index, word in enumerate(words):
            state = (state * BATTLE_MULTIPLIER + BATTLE_INCREMENT) & MASK32
            words[index] = word ^ state
    else:
        for offset in range(0, len(output), 4):
            state = (state * BATTLE_MULTIPLIER + BATTLE_INCREMENT) & MASK32
            word = struct.unpack_from("<I", output, offset)[0]
            struct.pack_into("<I", output, offset, word ^ state)
    return bytes(output)


def decode_battle_entry(data: bytes) -> tuple[bytes, int]:
    """Decode one loader-recognized encrypted SEQW entry."""
    signature = battle_signature(data)
    if signature is None:
        raise ValueError("entry has no recognized battle-voice signature")
    clear = transform_battle_entry(data, signature)
    if clear[:4] != b"SEQW":
        raise ValueError("battle-voice transform did not produce SEQW")
    return clear, signature


def encode_battle_entry(clear: bytes, signature: int) -> bytes:
    """Restore one decoded SEQW entry to its original encrypted form."""
    if clear[:4] != b"SEQW":
        raise ValueError("decoded battle-voice entry is not SEQW")
    stored = transform_battle_entry(clear, signature)
    if battle_signature(stored) != signature:
        raise ValueError("battle-voice entry did not restore its signature")
    return stored


def exported_filename(bank: int, clip: Clip) -> str:
    return "%04d-%03d-%04x.wav" % (bank, clip.sub_index, clip.clip_id)


def parse_exported_filename(path):
    match = EXPORTED_NAME.match(Path(path).name)
    if not match:
        return None
    return tuple(int(match.group(name), 16 if name == "clip" else 10)
                 for name in ("bank", "sub", "clip"))


def unmapped_filename(entry: int, clip: StandaloneClip) -> str:
    return "unmapped-%04d-%03d-%04x-%d.wav" % (
        entry, clip.sample_index, clip.clip_id, clip.zone
    )


def parse_unmapped_filename(path):
    match = UNMAPPED_NAME.match(Path(path).name)
    if not match:
        return None
    return tuple(int(match.group(name), 16 if name == "clip" else 10)
                 for name in ("entry", "sample", "clip", "zone"))


def battle_filename(entry: int, clip: StandaloneClip) -> str:
    return "battle-%04d-%03d-%04x-%d.wav" % (
        entry, clip.sample_index, clip.clip_id, clip.zone
    )


def parse_battle_filename(path):
    match = BATTLE_NAME.match(Path(path).name)
    if not match:
        return None
    return tuple(int(match.group(name), 16 if name == "clip" else 10)
                 for name in ("entry", "sample", "clip", "zone"))


def _grouped_filename(prefix, entry, group, clip):
    return "%s-%04d-%02d-%03d-%04x-%d.wav" % (
        prefix, entry, group, clip.sample_index, clip.clip_id, clip.zone
    )


def _parse_grouped_filename(pattern, path):
    match = pattern.match(Path(path).name)
    if not match:
        return None
    return tuple(int(match.group(name), 16 if name == "clip" else 10)
                 for name in ("entry", "group", "sample", "clip", "zone"))


def field_filename(entry, group, clip):
    return _grouped_filename("field", entry, group, clip)


def parse_field_filename(path):
    return _parse_grouped_filename(FIELD_NAME, path)


def lezard_filename(entry, group, clip):
    return _grouped_filename("lezard", entry, group, clip)


def parse_lezard_filename(path):
    return _parse_grouped_filename(LEZARD_NAME, path)
