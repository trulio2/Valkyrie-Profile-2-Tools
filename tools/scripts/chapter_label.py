from __future__ import annotations

import struct

from . import sle
from .vp2_dcms import parse_pk1

CARRIERS = (60, 1196, 1200, 1202, 1212, 1282, 1298, 1302, 1304, 1312, 1356)
CHAPTERS = 8
NAME = b"SPChapterTitle.bin"
CONTAINER_AT = 0x1800
GLYPH_BYTES = 448
METRIC_BYTES = 2
TABLE_ENTRY = 8
ALIGN = 0x80
ROW_TABLE = 0x10
ROW_SIZE = 16
FIRST_MESSAGE = 51


class ChapterLabelError(ValueError):
    """The entry does not carry a chapter label this module can rebuild."""


def label_row(raw):
    """``(row index, offset, length, blob)`` for the label row, or ``None``."""
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


def read_container(blob):
    """``(header fields, [(glyph bitmap, metric)], [(id, [slot, ...])])``."""
    body = blob[CONTAINER_AT:]
    if body[:9] != b"mcps2lib ":
        raise ChapterLabelError("no container at 0x%X" % CONTAINER_AT)
    size, table_start, text_start, text_end, font_start, glyph_count = \
        struct.unpack_from("<6I", body, 0x20)
    glyph_base = struct.unpack_from("<I", body, 0x50)[0]
    metric_at = text_end
    glyphs = []
    for slot in range(glyph_count):
        at = font_start + slot * GLYPH_BYTES
        glyphs.append((bytes(body[at:at + GLYPH_BYTES]),
                       bytes(body[metric_at + slot * METRIC_BYTES:
                                  metric_at + (slot + 1) * METRIC_BYTES])))
    records = []
    for position in range(table_start, text_start, TABLE_ENTRY):
        message_id, offset = struct.unpack_from("<II", body, position)
        if not message_id:
            continue
        at = text_start + offset
        slots = []
        while at < text_end:
            token = body[at]
            at += 1
            if token == 0:
                break
            slots.append(token - glyph_base)
        records.append((message_id, slots))
    fields = {"size": size, "table_start": table_start, "text_start": text_start,
              "text_end": text_end, "font_start": font_start,
              "glyph_count": glyph_count, "glyph_base": glyph_base}
    return fields, glyphs, records


def _aligned(value, align=ALIGN):
    return (value + align - 1) // align * align


def build_container(template, word_art, digit_art, space_art, chapters,
                    spelling, order):
    header = bytearray(template[:0x80])
    base = struct.unpack_from("<I", header, 0x50)[0]
    glyphs = list(word_art) + [space_art] + list(digit_art)
    if base + len(glyphs) > 0x7F:
        raise ChapterLabelError(
            "%d glyphs do not fit the one-byte token range" % len(glyphs))
    space_slot = len(word_art)

    records, text = [], bytearray()
    for number in range(1, min(chapters, len(digit_art)) + 1):
        tokens = bytearray(base + order[character] for character in spelling)
        tokens.append(base + space_slot)
        tokens.append(base + space_slot + number)
        tokens.append(0)
        records.append((FIRST_MESSAGE + number - 1, len(text)))
        text += tokens

    table_start = 0x80
    text_start = _aligned(table_start + (len(records) + 1) * TABLE_ENTRY)
    text_end = text_start + _aligned(len(text))
    font_start = text_end + ALIGN
    size = font_start + len(glyphs) * GLYPH_BYTES

    out = bytearray(size)
    out[:0x80] = header
    struct.pack_into("<6I", out, 0x20, size, table_start, text_start,
                     text_end, font_start, len(glyphs))
    for position, (message_id, offset) in enumerate(records):
        struct.pack_into("<II", out, table_start + position * TABLE_ENTRY,
                         message_id, offset)
    out[text_start:text_start + len(text)] = text
    for slot, (_character, _bitmap, metric) in enumerate(glyphs):
        at = text_end + slot * METRIC_BYTES
        out[at:at + len(metric)] = metric[:METRIC_BYTES]
    for slot, (_character, bitmap, _metric) in enumerate(glyphs):
        at = font_start + slot * GLYPH_BYTES
        out[at:at + GLYPH_BYTES] = bitmap[:GLYPH_BYTES]
    return bytes(out)

SOURCE_WORDS = ("CHAPTER", "CAPITULO", "Chapter")

def _unique(word, fold):
    seen, out = set(), []
    for character in word:
        folded = fold(character)
        if folded in seen:
            continue
        seen.add(folded)
        out.append(folded)
    return out


def source_font(glyphs, records, fold, word=None):
    letters = len(records[0][1]) - 2 if records else 0
    for candidate in ([word] if word else SOURCE_WORDS):
        spelled = _unique(candidate, fold)
        if len(spelled) != letters or len(spelled) + 1 > len(glyphs):
            continue
        art = {character: glyphs[slot] for slot, character in enumerate(spelled)}
        art[" "] = glyphs[len(spelled)]
        for number, glyph in enumerate(glyphs[len(spelled) + 1:], start=1):
            art[str(number)] = glyph
        return art
    raise ChapterLabelError(
        "cannot tell which letters this label's %d glyphs are" % len(glyphs))


def cut_for_word(reader, blob, word, chapters=CHAPTERS, source_word=None,
                 donors=None):
    from . import vp2_title_face as title_face
    fields, glyphs, records = read_container(blob)
    art = source_font(glyphs, records, title_face.fold, source_word)
    index, provenance, word_art = {}, [], []
    for character in _unique(word, title_face.fold):
        if character in art:
            bitmap, metric = art[character]
            provenance.append((character, "the label's own font"))
        else:
            if donors is None:
                _face, found = title_face.donor_index(reader)
                harvested = title_face.read_harvested_donors()
                donors = (found, harvested,
                          title_face._composition_art(
                              reader, found, harvested,
                              {"glyph_bytes": GLYPH_BYTES}))
            bitmap, metric, where, slot = title_face.glyph_art(
                reader, character, donors[0], donors[1], donors[2],
                {"glyph_bytes": GLYPH_BYTES})
            provenance.append((character, "%s:%s" % (where, slot)))
        index[character] = len(word_art)
        word_art.append((character, bitmap, metric))
    spelling = [title_face.fold(character) for character in word]
    digits = [(str(number), art[str(number)][0], art[str(number)][1])
              for number in range(1, chapters + 1) if str(number) in art]
    space = (" ", art[" "][0], art[" "][1])
    container = build_container(blob[CONTAINER_AT:], word_art, digits, space,
                                len(digits), spelling, index)
    return replace_container(blob, container), provenance


def replace_container(blob, container):
    """*blob* with its container replaced and its own header fields fixed."""
    out = bytearray(blob[:CONTAINER_AT])
    out += container
    struct.pack_into("<I", out, 0x10, len(container) + ALIGN * 2)
    loaded = struct.unpack_from("<I", out, 0x08)[0]
    end = loaded + CONTAINER_AT + len(container)
    struct.pack_into("<I", out, 0x18, end)
    struct.pack_into("<I", out, 0x1C, end)
    return bytes(out)


def rewrite_row(raw, index, offset, length, blob, mode=2):
    """*raw* with the label row replaced by *blob*, at the entry's own length."""
    payload = sle.compress(blob, mode=mode)
    room = len(raw) - offset
    if len(payload) > room:
        raise ChapterLabelError(
            "the rebuilt label stores %d bytes and the entry holds %d from "
            "its row onward" % (len(payload), room))
    out = bytearray(raw)
    out[offset:offset + len(payload)] = payload
    spare = offset + length - (offset + len(payload))
    if spare > 0:
        out[offset + len(payload):offset + length] = bytes(spare)
    struct.pack_into("<I", out, ROW_TABLE + index * ROW_SIZE + 8, len(payload))
    return bytes(out[:len(raw)])
