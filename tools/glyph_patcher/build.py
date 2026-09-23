# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Patch an ISO to draw one texture per glyph, export its glyphs, write DDS."""

from __future__ import annotations

import csv
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from ..cheat_patcher import build as cheat_build
from ..cheat_patcher import battle_overlay, iso9660, triace
from ..cheat_patcher.catalog import ANTI_CHEAT
from ..cheat_patcher.cheats import disable_anti_cheat
from ..scripts import (disc_identity, glyph_slots, glyph_textures,
                       overlay_edits)
from ..scripts.paths import PROJECT_ROOT
from ..scripts.translation_pack import PACK_PROFILE, is_language_pack
from ..scripts.vp2_iso_buffer import IsoFile


SUPPORTED_BOOT = "SLUS_214.52"
GLYPH_NAME = re.compile(r"^glyph-([0-9a-f]{1,16})\.png$", re.IGNORECASE)
SHEET_NAME = "glyphs-sheet.png"
SHEET_COLUMNS = 16
SHEET_CELL = 40
SHEET_BACKGROUND = (96, 96, 112)


@dataclass(frozen=True)
class Result:
    output: Path
    count: int


@dataclass(frozen=True)
class DiscState:
    glyph_textures: bool
    anti_cheat_off: bool


@dataclass(frozen=True)
class GlyphDrawPatch:
    data: bytes
    allocation_size: int
    change_count: int


def _patch_glyph_draw(resource):
    resource = bytes(resource)
    patched = glyph_slots.patch_resource(resource)
    return GlyphDrawPatch(patched, len(resource), int(patched != resource))


def _patch_battle_draw(resource):
    resource = bytes(resource)
    result = overlay_edits.apply(resource, glyph_slots.battle_edits())
    return GlyphDrawPatch(result.data, len(resource), result.changed)


GLYPH_DRAW = SimpleNamespace(
    RESOURCE_PATCHERS=((glyph_slots.RESOURCE, _patch_glyph_draw),
                       (overlay_edits.RESOURCE, _patch_battle_draw)),
    combine_details=lambda resources, files: tuple(resources))


def check_disc(path):
    try:
        boot, region = disc_identity.identify(path)
    except disc_identity.DiscError as exc:
        raise ValueError(str(exc)) from exc
    if boot != SUPPORTED_BOOT:
        raise ValueError("%s is %s (%s), not the USA release (%s)"
                         % (Path(path).name, boot, region, SUPPORTED_BOOT))


def disc_state(path):
    check_disc(path)
    with open(path, "rb") as handle:
        index = triace.read_index(handle)
        resources = {}

        def resource(number):
            if number not in resources:
                resources[number] = triace.read_resource(handle, index, number)
            return resources[number]

        draw = resource(glyph_slots.RESOURCE)
        overlay = battle_overlay.read(resource(overlay_edits.RESOURCE))
        glyphs = (glyph_slots.patch_resource(draw) == draw
                  and not overlay_edits.edit_output(
                      overlay.output, glyph_slots.battle_edits())[1])
        anti_cheat = all(
            not patch(resource(number)).change_count
            for number, patch in disable_anti_cheat.RESOURCE_PATCHERS)
        for file_path, patch in disable_anti_cheat.ISO_FILE_PATCHERS:
            extent = iso9660.locate_file(handle, file_path)
            handle.seek(extent.offset)
            anti_cheat = anti_cheat and not patch(
                handle.read(extent.size)).change_count
    return DiscState(glyphs, anti_cheat)


def patched_iso_path(source, output_dir):
    return Path(output_dir) / (Path(source).stem + "-glyphs.iso")


def patch_iso(source, output_dir, progress=print):
    source = Path(source)
    state = disc_state(source)
    if state.glyph_textures and state.anti_cheat_off:
        raise ValueError("%s already draws one texture per glyph and has "
                         "anti-cheat off; use it as it is" % source.name)
    progress("glyph textures: %s" % ("already on" if state.glyph_textures
                                     else "turning on"))
    progress("anti-cheat: %s" % ("already off" if state.anti_cheat_off
                                 else "turning off"))
    output = patched_iso_path(source, output_dir)
    result = cheat_build.build_iso(
        source, output, [ANTI_CHEAT], progress=progress,
        extra_patchers=(("glyph-textures", GLYPH_DRAW),))
    return Result(result.output, 1)


def scene_resources(translations=None):
    root = Path(translations or PROJECT_ROOT / "translations")
    scenes = set()
    for pack in sorted(root.iterdir()) if root.is_dir() else ():
        profile = pack / PACK_PROFILE
        if not is_language_pack(pack) or not profile.is_file():
            continue
        with open(profile, newline="", encoding="utf-8") as handle:
            scenes.update(int(row["resource"]) for row in csv.DictReader(handle)
                          if row["kind"] == "scene")
    return sorted(scenes)


def glyph_cells(path):
    check_disc(path)
    with IsoFile(str(path), "rb") as iso:
        draw = bytes(iso.read_entry(glyph_slots.RESOURCE))
        if glyph_slots.patch_resource(draw) != draw:
            raise ValueError("%s does not draw one texture per glyph; patch "
                             "it first" % Path(path).name)
        cells = glyph_textures.collect(iso, scene_resources())
    return {"%x" % glyph_slots.texture_hash(cell): cell for cell in cells}


def prepare_folder(folder, pattern, replace):
    """Create *folder*; with *replace*, clear the files *pattern* matches."""
    folder = Path(folder)
    existing = sorted(folder.glob(pattern)) if folder.is_dir() else []
    if existing and not replace:
        raise FileExistsError("%s already holds %d file(s)"
                              % (folder, len(existing)))
    for path in existing:
        path.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def export_folder(source, output_dir):
    return Path(output_dir) / (Path(source).stem + "-glyphs")


def dds_folder(masters, output_dir):
    return Path(output_dir) / (Path(masters).name + "-dds")


def preview(cell):
    colours = []
    for word in glyph_slots.PALETTES[0]:
        colours.append(bytes(((word >> 8 * c) & 255 for c in range(3)))
                       + bytes((min((word >> 24) * 2, 255),)))
    return b"".join(colours[i] for i in glyph_slots.indices(cell))


def sheet(previews):
    size = glyph_slots.SIZE
    width = SHEET_COLUMNS * SHEET_CELL
    height = -(-len(previews) // SHEET_COLUMNS) * SHEET_CELL
    out = bytearray(bytes(SHEET_BACKGROUND) + b"\xff") * (width * height)
    for n, rgba in enumerate(previews):
        left = (n % SHEET_COLUMNS) * SHEET_CELL + 4
        top = (n // SHEET_COLUMNS) * SHEET_CELL + 4
        for y in range(size):
            for x in range(size):
                at = 4 * (y * size + x)
                alpha = rgba[at + 3]
                if not alpha:
                    continue
                dest = 4 * ((top + y) * width + left + x)
                for c in range(3):
                    out[dest + c] = (rgba[at + c] * alpha
                                     + out[dest + c] * (255 - alpha)
                                     + 127) // 255
    return width, height, bytes(out)


def export_glyphs(source, output_dir, progress=print, replace=False):
    cells = glyph_cells(source)
    folder = prepare_folder(export_folder(source, output_dir), "glyph*.png",
                            replace)
    size = glyph_slots.SIZE
    previews = []
    for n, (name, cell) in enumerate(cells.items(), 1):
        rgba = preview(cell)
        (folder / ("glyph-%s.png" % name)).write_bytes(
            glyph_textures.png(size, size, rgba))
        previews.append(rgba)
        progress("glyphs: %d/%d" % (n, len(cells)))
    width, height, rgba = sheet(previews)
    (folder / SHEET_NAME).write_bytes(glyph_textures.png(width, height, rgba))
    progress("wrote %d glyph(s) and %s to %s" % (len(cells), SHEET_NAME, folder))
    return Result(folder, len(cells))


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def read_png(path):
    try:
        return read_png_bytes(Path(path).read_bytes())
    except ValueError as exc:
        raise ValueError("%s: %s" % (Path(path).name, exc)) from None


def read_png_bytes(blob):
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    header = palette = transparency = None
    data, at = bytearray(), 8
    while at + 8 <= len(blob):
        length, tag = struct.unpack_from(">I4s", blob, at)
        body = blob[at + 8:at + 8 + length]
        at += 12 + length
        if tag == b"IHDR":
            header = struct.unpack(">2I5B", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            transparency = body
        elif tag == b"IDAT":
            data += body
        elif tag == b"IEND":
            break
    if header is None:
        raise ValueError("no IHDR")
    width, height, depth, colour, _c, _f, interlace = header
    if (interlace or colour not in _CHANNELS
            or depth not in ((1, 2, 4, 8) if colour == 3 else (8, 16))):
        raise ValueError("bit depth %d, colour type %d%s is not supported;"
                         " save it as 8-bit RGBA" % (
                             depth, colour,
                             ", interlaced" if interlace else ""))
    if colour == 3 and palette is None:
        raise ValueError("indexed but has no palette")
    bits = _CHANNELS[colour] * depth
    step = max(1, bits // 8)
    stride = (width * bits + 7) // 8
    raw = zlib.decompress(bytes(data))
    if len(raw) != (stride + 1) * height:
        raise ValueError("pixel data is truncated")
    out = bytearray(4 * width * height)
    previous = bytearray(stride)
    for y in range(height):
        head = y * (stride + 1)
        kind = raw[head]
        line = bytearray(raw[head + 1:head + 1 + stride])
        if kind == 1:
            for x in range(step, stride):
                line[x] = (line[x] + line[x - step]) & 255
        elif kind == 2:
            for x in range(stride):
                line[x] = (line[x] + previous[x]) & 255
        elif kind == 3:
            for x in range(stride):
                left = line[x - step] if x >= step else 0
                line[x] = (line[x] + ((left + previous[x]) >> 1)) & 255
        elif kind == 4:
            for x in range(stride):
                a = line[x - step] if x >= step else 0
                b = previous[x]
                c = previous[x - step] if x >= step else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc
                                      else b if pb <= pc else c)) & 255
        elif kind:
            raise ValueError("row %d uses filter %d" % (y, kind))
        _to_rgba(line, width, colour, depth, palette, transparency,
                 out, 4 * width * y)
        previous = line
    return width, height, bytes(out)


def _to_rgba(line, width, colour, depth, palette, transparency, out, at):
    if colour == 3:
        per = 8 // depth
        mask = (1 << depth) - 1
        for x in range(width):
            index = (line[x // per] >> (8 - depth * (x % per + 1))) & mask
            if 3 * index + 3 > len(palette):
                raise ValueError("palette index %d has no colour" % index)
            alpha = (transparency[index] if transparency
                     and index < len(transparency) else 255)
            out[at + 4 * x:at + 4 * x + 4] = palette[3 * index:3 * index + 3] \
                + bytes((alpha,))
        return
    size = depth // 8
    channels = _CHANNELS[colour]
    samples = line[::size]
    for x in range(width):
        pixel = samples[x * channels:(x + 1) * channels]
        if colour == 0:
            rgba = (pixel[0],) * 3 + (255,)
        elif colour == 4:
            rgba = (pixel[0],) * 3 + (pixel[1],)
        elif colour == 2:
            rgba = tuple(pixel) + (255,)
        else:
            rgba = tuple(pixel)
        out[at + 4 * x:at + 4 * x + 4] = bytes(rgba)


DDSD_CAPS, DDSD_HEIGHT, DDSD_WIDTH = 0x1, 0x2, 0x4
DDSD_PITCH, DDSD_PIXELFORMAT = 0x8, 0x1000
DDPF_ALPHAPIXELS, DDPF_RGB = 0x1, 0x40
DDSCAPS_TEXTURE = 0x1000


def dds(width, height, rgba):
    header = struct.pack(
        "<7I", 124,
        DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PITCH | DDSD_PIXELFORMAT,
        height, width, width * 4, 0, 1)
    header += bytes(11 * 4)
    header += struct.pack("<8I", 32, DDPF_RGB | DDPF_ALPHAPIXELS, 0, 32,
                          0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000)
    header += struct.pack("<5I", DDSCAPS_TEXTURE, 0, 0, 0, 0)
    return b"DDS " + header + bytes(rgba)


def master_levels(rgba):
    out = []
    for at in range(0, len(rgba), 4):
        outline = rgba[at + 3] / 255.0
        body = max(rgba[at], rgba[at + 1], rgba[at + 2]) / 255.0 * outline
        out.append(glyph_textures.level(body, outline))
    return out


def glyph_masters(folder):
    found = []
    for path in sorted(Path(folder).iterdir()):
        match = GLYPH_NAME.match(path.name)
        if match and path.is_file():
            found.append((match.group(1).lower(), path))
    return found


def write_dds(masters, output_dir, progress=print, replace=False):
    found = glyph_masters(masters)
    if not found:
        raise ValueError("%s holds no glyph-<hash>.png files" % masters)
    folder = prepare_folder(dds_folder(masters, output_dir), "*.dds", replace)
    palettes = [(glyph_slots.palette_hash(palette), palette)
                for palette in glyph_slots.PALETTES.values()]
    for n, (texture, path) in enumerate(found, 1):
        width, height, rgba = read_png(path)
        if width != height:
            raise ValueError("%s is %dx%d; a glyph master must be square"
                             % (path.name, width, height))
        levels = master_levels(rgba)
        for palette_hash, palette in palettes:
            name = "%s-%x-%08x.dds" % (texture, palette_hash,
                                       glyph_slots.NAME_BITS)
            (folder / name).write_bytes(
                dds(width, height, glyph_textures.colour(levels, palette)))
        progress("glyphs: %d/%d" % (n, len(found)))
    progress("wrote %d texture(s) for %d glyph(s) to %s"
             % (len(found) * len(palettes), len(found), folder))
    return Result(folder, len(found))
