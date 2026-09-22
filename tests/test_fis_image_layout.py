# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import struct
import unittest

from tools.scripts import fis_images


def dtt_record(box, x_scale=8, y_scale=16):
    left, top, right, bottom = box
    return struct.pack(
        "<8H", 0x100, 1,
        left * x_scale + x_scale // 2,
        top * y_scale + y_scale // 2,
        right * x_scale + x_scale // 2,
        bottom * y_scale + y_scale // 2,
        right - left + 1, bottom - top + 1)


class FisImageLayoutTests(unittest.TestCase):
    def test_victory_labels_use_independent_half_width_boxes(self):
        root = Path(__file__).parents[1]
        pack = root / "translations" / "pt-BR"
        images = pack / "images"
        name = "fis-1721-decrypted-slz-0x0-80.png"
        document = json.loads(
            (pack / "fis-image-layouts.json").read_text(encoding="utf-8"))
        rows = document["images"][name]["dtt"]["records"]
        configured = {row["index"]: row for row in rows}

        originals = [
            tuple(configured[index]["from"]) if index in configured
            else (4, 9, 400, 60)
            for index in range(1, max(configured) + 1)
        ]
        body = b"".join(dtt_record(box) for box in originals)
        table = struct.pack("<4sI8x", b"DTT\0", len(body)) + body

        patched, changed = fis_images._patch_dtt_layout(
            table, images, name, (512, 256))

        records = fis_images._dtt_tables(patched)[0][1]
        for index, row in configured.items():
            self.assertEqual(tuple(row["to"]),
                             fis_images._dtt_box(patched, records[index - 1]))
            self.assertEqual((8, 16),
                             fis_images._dtt_geometry(
                                 patched, records[index - 1])[1:])
        self.assertGreater(changed, 0)

        same, changed = fis_images._patch_dtt_layout(
            patched, images, name, (512, 256))
        self.assertEqual(patched, same)
        self.assertEqual(0, changed)

    def test_a_view_without_the_table_defers_to_another_view(self):
        images = Path(__file__).parents[1] / "translations" / "pt-BR" / "images"
        name = "fis-1781-unprotected-slz-0x1826C0-0.png"
        blob = b"no table here"

        self.assertEqual((blob, None), fis_images._patch_dtt_layout(
            blob, images, name, (512, 256), missing_ok=True))
        with self.assertRaises(fis_images.FisError):
            fis_images._patch_dtt_layout(blob, images, name, (512, 256))

    def test_title_copyright_mesh_moves_uvs_and_positions_together(self):
        root = Path(__file__).parents[1]
        pack = root / "translations" / "pt-BR"
        images = pack / "images"
        name = "fis-0010-slz-0x2D04-4CE80.png"
        document = json.loads(
            (pack / "fis-image-layouts.json").read_text(encoding="utf-8"))
        rows = sorted(document["images"][name]["mesh"]["vertices"],
                      key=lambda row: row["index"])
        self.assertEqual(list(range(1, 17)), [row["index"] for row in rows])

        count = len(rows)
        uv = b"".join(struct.pack("<2H", int(row["from"][0] * 8),
                                  int(row["from"][1] * 32)) for row in rows)
        xyz = b"".join(struct.pack("<3f", row["from"][2], row["from"][3],
                                   9.08) for row in rows)
        colour = struct.pack("<BBBB", 0, 0, count, 0x6E) + b"\x80" * 4 * count
        mesh = (struct.pack("<BBBB", 0, 0x80, count, 0x75) + uv + colour
                + struct.pack("<BBBB", 0, 0x80, count, 0x68) + xyz)

        patched, changed = fis_images._patch_mesh_layout(
            mesh, images, name, (512, 128))

        uv_at, xyz_at = 4, 4 + len(uv) + len(colour) + 4
        for row in rows:
            u, v, x, y = fis_images._mesh_vertex(patched, uv_at, xyz_at,
                                                 row["index"])
            self.assertEqual(tuple(row["to"][:2]), (u / 8, v / 32))
            self.assertAlmostEqual(row["to"][2], x, places=3)
            self.assertAlmostEqual(row["to"][3], y, places=3)
            self.assertEqual(9.08, round(struct.unpack_from(
                "<f", patched, xyz_at + 12 * (row["index"] - 1) + 8)[0], 2))
        self.assertEqual(len(mesh), len(patched))
        self.assertGreater(changed, 0)

        self.assertEqual((patched, 0), fis_images._patch_mesh_layout(
            patched, images, name, (512, 128)))
        with self.assertRaises(fis_images.FisError):
            fis_images._patch_mesh_layout(b"no mesh", images, name, (512, 128))

    def test_full_scale_dtt_geometry_remains_supported(self):
        record = dtt_record((2, 59, 67, 81), 32, 32)
        self.assertEqual(((2, 59, 67, 81), 32, 32),
                         fis_images._dtt_geometry(record, 0))


if __name__ == "__main__":
    unittest.main()
