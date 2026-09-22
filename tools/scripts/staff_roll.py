from __future__ import annotations

import csv
import os
import struct

from . import sle
from .paths import DATA_DIR
from . import vp2_container_text as container_text
from .container_archive import unpack_container_entry
from .vp2_dcms import parse_pk1

OVERLAY_RESOURCE = 30
ROLL_RESOURCE = 31
ROLL_BANK = "SYS"
NAME = b"SPStaffRoll.bin"
HEADING = 1
ENTRY = 8
END = struct.pack("<BxxxI", 0xFF, 0xFFFFFFFF)
CELLS_PER_LINE = 4
SECTIONS_PATH = DATA_DIR / "roll-sections.csv"
PARTS = ("heading", "role", "name", "follow")
MARGIN_LINES = 2


class StaffRollError(ValueError):
    """The overlay or the roll is not in the shape this module rewrites."""


def overlay_row(raw):
    for index, (_tag, offset, length) in enumerate(parse_pk1(raw)):
        payload = raw[offset:offset + length]
        if payload[:3] != b"SLE":
            continue
        try:
            blob = sle.decompress(payload)
        except Exception:                                        # noqa: BLE001
            continue
        if blob[:4] == b"MWo3" and NAME in blob[:0x60]:
            return index, offset, length, blob
    return None


def heading_table(blob):
    terminator = blob.find(END)
    if terminator < 0 or blob.find(END, terminator + 1) >= 0:
        raise StaffRollError("the roll overlay has no single heading table")
    start = terminator
    while start >= ENTRY:
        kind, pad, message_id = struct.unpack_from(
            "<B3sI", blob, start - ENTRY)
        if kind not in (0, HEADING) or any(pad) or not 0 < message_id < 0x10000:
            break
        start -= ENTRY
    entries = [struct.unpack_from("<BxxxI", blob, at)
               for at in range(start, terminator, ENTRY)]
    after = terminator + ENTRY
    room = after
    while room < len(blob) and blob[room] == 0:
        room += 1
    return start, terminator, entries, room - after


def heading_lines(bank):
    meta = container_text.layout(bank)
    _meta, messages = container_text.read_messages(
        bytes(bank), ROLL_RESOURCE,
        alphabet=container_text.resource_alphabet(bank, ROLL_RESOURCE))
    by_id = {message["message_id"]: message for message in messages}

    def kind(message_id):
        message = by_id.get(message_id)
        if message is None:
            return None
        if container_text.CODEPAGE_TAG.sub("", message["original_en"]).strip():
            return "text"
        runs = container_text.codepage_record_runs(
            bank, meta, message["offset"])
        return "blank" if any(runs) else "empty"

    ids = sorted(by_id)
    headings, first = [], None
    for position, message_id in enumerate(ids):
        if position == 0 or message_id != ids[position - 1] + 1:
            first = message_id
        if (message_id - first) % CELLS_PER_LINE:
            continue
        if kind(message_id) == "text" and kind(message_id + 1) == "blank":
            headings.append(message_id)
    return headings


def add_headings(blob, wanted):
    _start, terminator, entries, room = heading_table(blob)
    have = {message_id for _kind, message_id in entries}
    new = [message_id for message_id in wanted if message_id not in have]
    if not new:
        return bytes(blob), []
    if len(new) * ENTRY > room:
        raise StaffRollError(
            "the roll has %d new heading(s) and its overlay has room for %d"
            % (len(new), room // ENTRY))
    out = bytearray(blob)
    at = terminator
    for message_id in new:
        struct.pack_into("<BxxxI", out, at, HEADING, message_id)
        at += ENTRY
    out[at:at + ENTRY] = END
    return bytes(out), new


def apply_to_iso(iso):
    from .chapter_label import ChapterLabelError, rewrite_row

    bank = unpack_container_entry(
        bytes(iso.read_entry(ROLL_RESOURCE)), ROLL_RESOURCE, ROLL_BANK)
    raw = bytes(iso.read_entry(OVERLAY_RESOURCE))
    found = overlay_row(raw)
    if found is None:
        raise StaffRollError(
            "resource %d carries no %s" % (OVERLAY_RESOURCE, NAME.decode()))
    index, offset, length, blob = found
    patched, new = add_headings(blob, heading_lines(bytearray(bank)))
    if not new:
        return []
    try:
        rebuilt = rewrite_row(raw, index, offset, length, patched)
    except ChapterLabelError as exc:
        raise StaffRollError(str(exc)) from exc
    back = overlay_row(rebuilt)
    if back is None or back[3] != patched:
        raise StaffRollError("the roll overlay did not read back")
    iso.write_entry(OVERLAY_RESOURCE, rebuilt)
    return new


def load_sections(path=None):
    path = os.fspath(path or SECTIONS_PATH)
    sections = {}
    if not os.path.exists(path):
        return sections
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            part = (row.get("part") or "").strip()
            if part not in PARTS:
                raise StaffRollError("%s: unknown part %r" % (path, part))
            sections.setdefault(int(row["resource"]), []).append(
                (int(row["message_id"]), part, row.get("text") or ""))
    return sections


def section_defaults(resource, sections=None):
    rows = (load_sections() if sections is None else sections).get(
        resource, [])
    return {str(message_id): text for message_id, _part, text in rows
            if text.strip()}


def _visible(message):
    text = container_text.CODEPAGE_TAG.sub("", message["original_en"])
    return bool(text.strip())


def section_region(bank, resource, sections=None):
    rows = (load_sections() if sections is None else sections).get(resource)
    if not rows:
        return None
    _meta, messages = container_text.read_messages(
        bytes(bank), resource,
        alphabet=container_text.resource_alphabet(bank, resource))
    declared = {message_id for message_id, _part, _text in rows}
    if not declared <= {message["message_id"] for message in messages}:
        return None
    start = min(message_id for message_id, part, _text in rows
                if part != "follow")
    later = sorted(message["message_id"] for message in messages
                   if message["message_id"] > start
                   and message["message_id"] not in declared
                   and _visible(message))
    end = later[0] if later else max(
        message["message_id"] for message in messages) + 1
    earlier = [message["message_id"] for message in messages
               if message["message_id"] < start and _visible(message)]
    first = max(earlier) + 1 if earlier else min(
        message["message_id"] for message in messages)
    return first, end


def lay_out_section(bank, resource, translations, sections=None):
    rows = (load_sections() if sections is None else sections).get(resource)
    if not rows:
        return translations
    region = section_region(bank, resource, sections=sections)
    if region is None:
        return translations
    first, end = region
    start = min(message_id for message_id, part, _text in rows
                if part != "follow")
    declared = {str(message_id) for message_id, _part, _text in rows}
    stray = sorted((key for key in translations
                    if first <= int(key) < end and key not in declared),
                   key=int)
    if stray:
        raise StaffRollError(
            "resource #%d message(s) %s sit inside the section the build lays "
            "out; write them into the section's own rows"
            % (resource, ", ".join(stray[:8])))

    def text(message_id):
        row = translations.get(str(message_id))
        return (row["translated"] if row else "").strip()

    lines, heading, groups, follow = [], None, [], []
    for message_id, part, _default in rows:
        if part == "heading":
            heading = text(message_id)
        elif part == "role":
            groups.append([text(message_id), []])
        elif part == "name":
            if not groups:
                raise StaffRollError(
                    "resource #%d section lists a name before any role"
                    % resource)
            groups[-1][1].append(text(message_id))
        else:
            follow.append(message_id)
    for role, names in groups:
        names = [name for name in names if name]
        if not names:
            continue
        if heading:
            lines.append((heading, ""))
            heading = None
        elif lines:
            lines.append(("", ""))
        lines.append((role, names[0]))
        lines.extend(("", name) for name in names[1:])

    step = CELLS_PER_LINE
    margin = (MARGIN_LINES + 1) * step
    last = start + step * (len(lines) - 1) if lines else start - step
    placed = last
    if follow:
        home = min(follow)
        placed = max(home, last + margin)
    if placed + margin > end:
        raise StaffRollError(
            "resource #%d section needs %d line(s) and the roll has room for "
            "%d before its next text" % (
                resource, len(lines) + (1 if follow else 0),
                (end - margin - start) // step + (0 if follow else 1)))

    result = {key: row for key, row in translations.items()
              if key not in declared}
    for index, (left, right) in enumerate(lines):
        line = start + step * index
        for offset, value in ((0, left), (1, right)):
            if value:
                result[str(line + offset)] = {"translated": value}
    for message_id in follow:
        position = placed + (message_id - home)
        if position == message_id:
            if text(message_id):
                result[str(message_id)] = {"translated": text(message_id)}
        else:
            result[str(position)] = {
                "translated": "<FROM:%d>%s" % (message_id, text(message_id))}
    return result
