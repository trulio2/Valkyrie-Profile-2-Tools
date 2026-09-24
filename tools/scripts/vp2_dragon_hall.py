# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Extract and patch the Dragon Hall stone-selection prompt."""

from __future__ import annotations

import contextlib
import io
import struct

from . import sle
from . import slz_compress
from . import vp2_container_text as container_text


RESOURCES = (328, 330, 338, 340, 342, 346, 348, 354, 372, 382, 388)
MESSAGE_ID = 0xE80
PROMPT_OFFSET = MESSAGE_ID
ORIGINAL_EN = "Insert which stone?"
ORIGINAL_JP = "どの石をはめますか？"
JP_PROMPT = bytes(range(0x65, 0x6F)) + b"\0"
STREAM_SIZE = 4480
BANK_OFFSET = 0xC80
TEXT_FIELDS = (
    (0xE80, "Insert which stone?", "どの石をはめますか？"),
    (0xE94, "Sunlight Stone", "陽光の石"),
    (0xEA3, "Halo Stone", "輪光の石"),
    (0xEAE, "Painted Cloud Stone", "彩雲の石"),
    (0xEC2, "Dark Moon Stone", "裏月の石"),
    (0xED2, "Crimson Flame Stone", "紅炎の石"),
    (0xEE6, "Ring of Mylinn", "ミュリンの指輪"),
    (0xEF5, "Dragon Orb", "ドラゴンオーブ"),
    (0xF00, "Ghoul Powder", "グールパウダー"),
    (0xF0D, "Sun and Moon Stone", "陽月の石"),
    (0xF20, "Jade Sealpouch", "翠の封陣器"),
    (0xF2F, "Rose Sealpouch", "緋の封陣器"),
    (0xF3E, "Azure Sealpouch", "藍の封陣器"),
    (0xF4E, "Eclipse Stone", "日食の石"),
)
UNUSED_FIELDS = frozenset((0xEE6, 0xEF5, 0xF00, 0xF20, 0xF2F, 0xF3E))
TABLE_ID = {message_id: index + 1
            for index, (message_id, _english, _japanese)
            in enumerate(TEXT_FIELDS)}
FIELD_BY_ID = {
    message_id: (english, japanese,
                 (TEXT_FIELDS[index + 1][0] - message_id
                  if index + 1 < len(TEXT_FIELDS)
                  else len(container_text.encode_codepage(english))))
    for index, (message_id, english, japanese) in enumerate(TEXT_FIELDS)
}


def protect(slz_blob: bytes) -> bytes:
    if len(slz_blob) < sle.HEADER_SIZE or slz_blob[:3] != b"SLZ":
        raise ValueError("not an SLZ stream")
    stored_size = struct.unpack_from("<I", slz_blob, 4)[0]
    end = sle.HEADER_SIZE + stored_size
    if end != len(slz_blob):
        raise ValueError("SLZ stream length does not match its header")
    protected = bytearray(slz_blob)
    for position in range(stored_size):
        plain = protected[sle.HEADER_SIZE + position]
        addend = (3 + 3 * position) & 0xFF
        protected[sle.HEADER_SIZE + position] = (
            (plain ^ sle.KEY[position & 0x0F]) + addend
        ) & 0xFF
    protected[2] = ord("E")
    return bytes(protected)


def _sle_candidates(raw: bytes):
    start = 0
    while True:
        offset = raw.find(b"SLE", start)
        if offset < 0:
            return
        start = offset + 3
        if offset + sle.HEADER_SIZE > len(raw):
            continue
        stored_size = struct.unpack_from("<I", raw, offset + 4)[0]
        end = offset + sle.HEADER_SIZE + stored_size
        if end > len(raw):
            continue
        try:
            expanded = sle.decompress(raw[offset:end])
        except (ValueError, IndexError, struct.error):
            continue
        yield offset, end, stored_size, raw[offset:end], expanded


def _bank(expanded):
    if expanded[BANK_OFFSET:BANK_OFFSET + 8] != b"mcps2lib":
        raise ValueError("SPDDragonHall.bin text bank was not found")
    size = struct.unpack_from("<I", expanded, BANK_OFFSET + 0x20)[0]
    return bytes(expanded[BANK_OFFSET:BANK_OFFSET + size])


def _english_stream(raw: bytes):
    for candidate in _sle_candidates(raw):
        expanded = candidate[4]
        if (len(expanded) == STREAM_SIZE
                and expanded[BANK_OFFSET:BANK_OFFSET + 8] == b"mcps2lib"):
            return candidate
    raise ValueError("SPDDragonHall.bin prompt stream was not found")


def _read_field(bank, message_id, accent_tokens=None):
    try:
        table_id = TABLE_ID[message_id]
    except KeyError as exc:
        raise ValueError(f"unknown Dragon Hall text field {message_id}") from exc
    meta = container_text.layout(bank)
    offset = dict(container_text.entries(bank, meta)).get(table_id)
    if offset is None:
        raise ValueError(f"Dragon Hall text field {message_id} is not indexed")
    return container_text.render_codepage(
        bank, meta, offset, accent_tokens=accent_tokens)[0]


def extract_english(raw: bytes, message_id=MESSAGE_ID, *, accent_tokens=None) -> str:
    return _read_field(_bank(_english_stream(raw)[4]), message_id,
                       accent_tokens=accent_tokens)


def extract_japanese(raw: bytes) -> str:
    for candidate in _sle_candidates(raw):
        expanded = candidate[4]
        if expanded[PROMPT_OFFSET:PROMPT_OFFSET + len(JP_PROMPT)] == JP_PROMPT:
            return ORIGINAL_JP
    raise ValueError("Japanese SPDDragonHall.bin prompt stream was not found")


def source_rows(resource: int, raw: bytes, japanese_raw: bytes | None = None):
    if resource not in RESOURCES:
        raise ValueError(f"resource {resource} is not a Dragon Hall prompt owner")
    if japanese_raw is not None:
        extract_japanese(japanese_raw)
    rows = []
    for message_id, expected, japanese in TEXT_FIELDS:
        if message_id in UNUSED_FIELDS:
            continue
        english = extract_english(raw, message_id)
        if english != expected:
            raise ValueError(
                f"resource {resource}: expected {expected!r}, found {english!r}")
        size = FIELD_BY_ID[message_id][2]
        rows.append({
            "kind": "container",
            "resource": str(resource),
            "message_id": str(message_id),
            "message_index": "",
            "record_kind": "dragon_hall_prompt",
            "offset": str(message_id),
            "byte_length": str(size),
            "original_en": english,
            "original_jp": japanese if japanese_raw is not None else "",
            "translated": "",
            "notes": "",
        })
    return rows


def source_row(resource: int, raw: bytes, japanese_raw: bytes | None = None):
    return source_rows(resource, raw, japanese_raw)[0]


def _repack(bank, resource, translations, accent_tokens):
    rows = {str(TABLE_ID[message_id]): {"translated": text}
            for message_id, text in translations.items()}
    with contextlib.redirect_stdout(io.StringIO()):
        rebuilt, written = container_text.rebuild_codepage_records(
            bank, resource, rows, accent_tokens=accent_tokens,
            keep_region=True)
    if written != len(rows):
        return None
    return bytes(rebuilt)


def patch_raw(raw: bytes, translations, *, accent_tokens=None, resource=0):
    if isinstance(translations, str):
        translations = {MESSAGE_ID: translations}
    else:
        translations = {int(key): value for key, value in translations.items()}
    offset, end, stored_size, stream, expanded = _english_stream(raw)
    bank = _bank(expanded)
    for message_id in translations:
        original = FIELD_BY_ID.get(message_id, (None,))[0]
        if original is None:
            raise ValueError(f"unknown Dragon Hall text field {message_id}")
        before = _read_field(bank, message_id, accent_tokens=accent_tokens)
        if before != original:
            raise ValueError(f"expected {original!r}, found {before!r}")
    rebuilt_bank = _repack(bank, resource, translations, accent_tokens)
    if rebuilt_bank is None:
        spare = {message_id: "" for message_id in UNUSED_FIELDS}
        spare.update(translations)
        rebuilt_bank = _repack(bank, resource, spare, accent_tokens)
    if rebuilt_bank is None:
        raise ValueError(
            "Dragon Hall names do not fit the %d-byte text bank"
            % (len(bank) - container_text.layout(bank)["text_start"]))
    rebuilt = bytearray(expanded)
    rebuilt[BANK_OFFSET:BANK_OFFSET + len(bank)] = rebuilt_bank
    compressed = slz_compress.compress(
        rebuilt, mode=stream[3], target_size=stored_size, cache_dir="")
    protected = protect(compressed)
    if len(protected) != len(stream):
        raise AssertionError("exact-size Dragon Hall recompression changed the stream")
    output = raw[:offset] + protected + raw[end:]
    expected = {
        message_id: container_text.codepage_semantic_text(
            translated, accent_tokens=accent_tokens)
        for message_id, translated in translations.items()
    }
    readback = {
        message_id: extract_english(
            output, message_id, accent_tokens=accent_tokens)
        for message_id in translations
    }
    if readback != expected:
        raise AssertionError(f"Dragon Hall text readback mismatch: {readback!r}")
    return output, {
        "wrapper": "SLE",
        "stream_offset": offset,
        "stored_size": stored_size,
        "prompt": readback.get(MESSAGE_ID, extract_english(output)),
        "fields": readback,
    }


def patch_resource_in_memory(iso, resource: int, supplied, *, accent_tokens=None):
    known = {str(message_id) for message_id in FIELD_BY_ID}
    extra = sorted(set(supplied) - known)
    if extra:
        raise ValueError(f"resource {resource}: unexpected Dragon Hall rows {extra}")
    supplied = {key: row for key, row in supplied.items()
                if int(key) not in UNUSED_FIELDS}
    original = bytes(iso.read_entry(resource))
    rebuilt, details = patch_raw(
        original,
        {int(key): row["translated"] for key, row in supplied.items()},
        accent_tokens=accent_tokens, resource=resource)
    iso.write_entry(resource, rebuilt)
    return {"written": len(supplied), "details": details, "font_patch": None}
