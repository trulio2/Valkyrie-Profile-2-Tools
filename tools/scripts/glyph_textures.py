# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import math
import os
import struct
import zlib

from . import glyph_slots
from . import slz
from . import vp2_shared_font as shared_font
from .vp2_cutscene_subtitles import find_dcms, font_layout, glyph_bitmap

SCALE = 4
EDGE = 1.2


def scene_cells(iso, resource):
    raw = bytes(iso.read_entry(resource))
    _offset, _length, packed = find_dcms(raw, resource)
    expanded = slz.decompress(packed)
    try:
        layout = font_layout(expanded)
    except ValueError:
        return []
    if layout["glyph_bytes"] != glyph_slots.CELL_BYTES:
        return []
    return [glyph_bitmap(expanded, layout, slot)
            for slot in range(layout["glyph_count"])]


def shared_cells(iso):
    archive = bytes(iso.read_entry(shared_font.SHARED_FONT_ENTRY))
    _at, _size, _span, _inner, font, layout = \
        shared_font.shared_font_stream(archive)
    start, size = layout["font_start"], layout["glyph_bytes"]
    return [font[start + k * size:start + (k + 1) * size]
            for k in range(layout["glyph_count"])]


def battle_cells(iso):
    from . import vp2_battle_label_font as battle_font
    from . import vp2_container_text as container_text
    blob = container_text.unpack_container_entry(
        bytes(iso.read_entry(battle_font.RESOURCE)), battle_font.RESOURCE)
    layout = container_text.layout(blob)
    start, size = layout["font_start"], glyph_slots.CELL_BYTES
    return [blob[start + k * size:start + (k + 1) * size]
            for k in range(layout["glyph_count"])]


def collect(iso, scene_resources):
    seen = {}
    for cell in shared_cells(iso):
        seen.setdefault(cell, None)
    for cell in battle_cells(iso):
        seen.setdefault(cell, None)
    for resource in scene_resources:
        for cell in scene_cells(iso, resource):
            seen.setdefault(cell, None)
    return [cell for cell in seen if any(cell)]


def _taps(scale):
    out = []
    for x in range(glyph_slots.SIZE * scale):
        u = (x + 0.5) / scale - 0.5
        i = math.floor(u)
        t = u - i
        weights = (((-0.5 * t + 1.0) * t - 0.5) * t,
                   (1.5 * t - 2.5) * t * t + 1.0,
                   ((-1.5 * t + 2.0) * t + 0.5) * t,
                   (0.5 * t - 0.5) * t * t)
        out.append(tuple((min(max(i - 1 + k, 0), glyph_slots.SIZE - 1), w)
                         for k, w in enumerate(weights)))
    return out


def _upscale(field, taps):
    n = glyph_slots.SIZE
    rows = [[sum(field[y * n + i] * w for i, w in tap) for tap in taps]
            for y in range(n)]
    width = len(taps)
    return [[sum(rows[i][x] * w for i, w in tap) for x in range(width)]
            for tap in taps]


def _edge(value, width):
    t = (value - 0.5) / width + 0.5
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    return t * t * (3.0 - 2.0 * t)


def level(body, outline):
    blend = min(max(body * 7.0, 0.0), 1.0)
    return (1.0 - blend) * 7.0 * outline + blend * (8.0 + 7.0 * body)


def levels(cell, scale=SCALE):
    plane = glyph_slots.indices(cell)
    body = [max(0, i - 8) / 7.0 for i in plane]
    outline = [1.0 if i >= 8 else i / 7.0 for i in plane]
    taps = _taps(scale)
    width = EDGE / scale
    body = _upscale(body, taps)
    outline = _upscale(outline, taps)
    out = []
    for body_row, outline_row in zip(body, outline):
        for b, o in zip(body_row, outline_row):
            out.append(level(_edge(b, width), _edge(o, width)))
    return out


def colour(level_values, palette):
    entries = []
    for word in palette:
        alpha = word >> 24
        entries.append(tuple((word >> (8 * c) & 255) * alpha
                             for c in range(3)) + (alpha,))
    out = bytearray(4 * len(level_values))
    for at, v in enumerate(level_values):
        lo = int(v)
        hi = min(lo + 1, 15)
        t = v - lo
        p, q = entries[lo], entries[hi]
        alpha = p[3] + (q[3] - p[3]) * t
        if alpha <= 0.0:
            continue
        for c in range(3):
            out[4 * at + c] = min(255, int((p[c] + (q[c] - p[c]) * t)
                                           / alpha + 0.5))
        out[4 * at + 3] = int(alpha + 0.5)
    return bytes(out)


def png(width, height, rgba):
    def chunk(kind, data):
        body = kind + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))
    stride = 4 * width
    raw = b"".join(b"\0" + rgba[y * stride:(y + 1) * stride]
                   for y in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6,
                                         0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


def write_pack(iso, scene_resources, out_dir, scale=SCALE, progress=None):
    cells = collect(iso, scene_resources)
    os.makedirs(out_dir, exist_ok=True)
    size = glyph_slots.SIZE * scale
    written = 0
    for n, cell in enumerate(cells):
        values = levels(cell, scale)
        for palette in glyph_slots.PALETTES.values():
            name = glyph_slots.replacement_name(cell, palette) + ".png"
            with open(os.path.join(out_dir, name), "wb") as handle:
                handle.write(png(size, size, colour(values, palette)))
            written += 1
        if progress:
            progress(n + 1, len(cells))
    return written
