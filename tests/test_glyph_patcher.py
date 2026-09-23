# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.glyph_patcher import build  # noqa: E402
from tools.scripts import glyph_slots, glyph_textures  # noqa: E402


def encode_png(width, height, depth, colour, rows, filters=None,
               palette=None, transparency=None):
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    filters = filters or [0] * height
    raw = b"".join(bytes((f,)) + row for f, row in zip(filters, rows))
    blob = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(
        ">IIBBBBB", width, height, depth, colour, 0, 0, 0))
    if palette is not None:
        blob += chunk(b"PLTE", palette)
    if transparency is not None:
        blob += chunk(b"tRNS", transparency)
    return blob + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


class PngTest(unittest.TestCase):
    def test_rgba_round_trip(self):
        rgba = bytes(range(64))
        self.assertEqual((4, 4, rgba), build.read_png_bytes(
            glyph_textures.png(4, 4, rgba)))

    def test_colour_types(self):
        cases = {
            "grey": (encode_png(2, 1, 8, 0, [b"\x10\x20"]),
                     b"\x10\x10\x10\xff\x20\x20\x20\xff"),
            "grey alpha": (encode_png(1, 1, 8, 4, [b"\x10\x80"]),
                           b"\x10\x10\x10\x80"),
            "rgb": (encode_png(1, 1, 8, 2, [b"\x01\x02\x03"]),
                    b"\x01\x02\x03\xff"),
            "rgba 16-bit": (encode_png(1, 1, 16, 6, [bytes(
                (1, 9, 2, 9, 3, 9, 4, 9))]), b"\x01\x02\x03\x04"),
            "indexed": (encode_png(3, 1, 2, 3, [b"\x24"],
                                   palette=b"\0\0\0\xff\0\0\0\xff\0",
                                   transparency=b"\x00"),
                        b"\0\0\0\0" + b"\0\xff\0\xff" + b"\xff\0\0\xff"),
        }
        for name, (blob, expected) in cases.items():
            with self.subTest(name):
                self.assertEqual(expected, build.read_png_bytes(blob)[2])

    def test_filters(self):
        rows = [b"\x01\x02\x03\x04\x05\x06\x07\x08"] * 5
        plain = build.read_png_bytes(encode_png(2, 5, 8, 6, rows))[2]
        sub = [bytes((row[x] - (row[x - 4] if x >= 4 else 0)) & 255
                     for x in range(8)) for row in rows]
        self.assertEqual(plain, build.read_png_bytes(
            encode_png(2, 5, 8, 6, sub, filters=[1] * 5))[2])
        up = [rows[0]] + [bytes(8)] * 4
        self.assertEqual(plain, build.read_png_bytes(
            encode_png(2, 5, 8, 6, up, filters=[0, 2, 2, 2, 2]))[2])

    def test_refuses_interlaced(self):
        blob = bytearray(encode_png(1, 1, 8, 6, [bytes(4)]))
        blob[28] = 1
        blob[29:33] = struct.pack(">I", zlib.crc32(bytes(blob[12:29])))
        with self.assertRaises(ValueError):
            build.read_png_bytes(bytes(blob))


class DdsTest(unittest.TestCase):
    def test_header(self):
        data = build.dds(8, 4, bytes(range(128)))
        self.assertEqual(b"DDS ", data[:4])
        size, _flags, height, width, pitch = struct.unpack_from("<5I", data, 4)
        self.assertEqual((124, 4, 8, 32), (size, height, width, pitch))
        pixel_format = struct.unpack_from("<8I", data, 76)
        self.assertEqual((32, 0x41, 0, 32, 0xFF, 0xFF00, 0xFF0000,
                          0xFF000000), pixel_format)
        self.assertEqual(bytes(range(128)), data[128:])


class MasterTest(unittest.TestCase):
    def test_native_master_matches_the_game(self):
        cell = bytes((i * 37) & 0x77 for i in range(glyph_slots.CELL_BYTES))
        levels = build.master_levels(build.preview(cell))
        native = [float(i) for i in glyph_slots.indices(cell)]
        for palette in glyph_slots.PALETTES.values():
            drawn = glyph_textures.colour(levels, palette)
            game = glyph_textures.colour(native, palette)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(drawn, game)),
                                 2)

    def test_write_dds_names_and_skips(self):
        cell = bytes(range(64)) * 7
        name = "%x" % glyph_slots.texture_hash(cell)
        with tempfile.TemporaryDirectory() as folder:
            masters = Path(folder) / "ups"
            masters.mkdir()
            size = glyph_slots.SIZE
            (masters / ("glyph-%s.png" % name)).write_bytes(
                glyph_textures.png(size, size, build.preview(cell)))
            (masters / "fis-0010-a-0.png").write_bytes(b"not read")
            result = build.write_dds(masters, folder, progress=lambda _: None)
            written = sorted(p.name for p in result.output.iterdir())
            self.assertEqual(Path(folder) / "ups-dds", result.output)
            self.assertEqual(
                sorted(glyph_slots.replacement_name(cell, palette) + ".dds"
                       for palette in glyph_slots.PALETTES.values()),
                written)
            with self.assertRaises(FileExistsError):
                build.write_dds(masters, folder, progress=lambda _: None)
            build.write_dds(masters, folder, progress=lambda _: None,
                            replace=True)

    def test_refuses_non_square(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "glyph-1.png").write_bytes(
                glyph_textures.png(2, 1, bytes(8)))
            with self.assertRaises(ValueError):
                build.write_dds(folder, folder, progress=lambda _: None)

    def test_empty_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                build.write_dds(folder, folder, progress=lambda _: None)


class LayoutTest(unittest.TestCase):
    def test_sheet(self):
        preview = build.preview(bytes(glyph_slots.CELL_BYTES))
        width, height, rgba = build.sheet([preview] * 17)
        self.assertEqual((640, 80), (width, height))
        self.assertEqual(4 * width * height, len(rgba))

    def test_output_names(self):
        self.assertEqual(Path("out") / "game-glyphs.iso",
                         build.patched_iso_path("x/game.iso", "out"))
        self.assertEqual(Path("out") / "game-glyphs",
                         build.export_folder("x/game.iso", "out"))

    def test_scene_list(self):
        self.assertTrue(build.scene_resources())


if __name__ == "__main__":
    unittest.main()
