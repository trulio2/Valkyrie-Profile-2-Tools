# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.scripts import fis_images, fis_screen_layout, overlay_edits, vp2_build


IMAGE = "fis-1781-unprotected-slz-0xDD740-98C80.png"
BANNERS = fis_screen_layout.BANNERS[IMAGE]
BOTH = [((1, 2), (90, 160), (84, 160)), ((5, 6, 7), (225, 358), (188, 358))]


def retail_output():
    words = [spec for banner in BANNERS.values() for spec in banner.words]
    end = max(overlay_edits.offset_of(address) for address, _a, _p in words)
    output = bytearray(end + 4)
    struct.pack_into("<I", output, 8, overlay_edits.LOAD_ADDRESS)
    for banner in BANNERS.values():
        for address, axis, prefix in banner.words:
            struct.pack_into("<I", output, overlay_edits.offset_of(address),
                             fis_screen_layout.position_word(
                                 banner.original[axis], prefix))
    return bytes(output)


def write_pack(root, images, create=(IMAGE,)):
    pack = Path(root)
    (pack / "images").mkdir(parents=True, exist_ok=True)
    for name in create:
        (pack / "images" / name).write_bytes(b"png")
    (pack / fis_images.LAYOUT_FILE).write_text(
        json.dumps({"version": 1, "images": images}), encoding="utf-8")
    return pack / "images"


class BannerEditTests(unittest.TestCase):
    def test_each_group_moves_only_its_own_instructions(self):
        output = retail_output()
        patched, _ = overlay_edits.edit_output(
            output, fis_screen_layout.edits(IMAGE, BOTH))
        expected = bytearray(output)
        for records, _before, after in BOTH:
            for address, axis, prefix in BANNERS[records].words:
                struct.pack_into("<I", expected, overlay_edits.offset_of(address),
                                 fis_screen_layout.position_word(after[axis], prefix))
        self.assertEqual(bytes(expected), patched)

    def test_one_group_leaves_the_other_banner_alone(self):
        output = retail_output()
        patched, _ = overlay_edits.edit_output(
            output, fis_screen_layout.edits(IMAGE, BOTH[1:]))
        for address, _axis, _prefix in BANNERS[(1, 2)].words:
            at = overlay_edits.offset_of(address)
            self.assertEqual(output[at:at + 4], patched[at:at + 4])

    def test_from_must_be_the_retail_position(self):
        with self.assertRaisesRegex(ValueError, "original position"):
            fis_screen_layout.edits(IMAGE, [((1, 2), (89, 160), (84, 160))])

    def test_an_image_without_a_banner_table_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no banner positions"):
            fis_screen_layout.edits("fis-0010-slz-0x2D04-8BC00.png", BOTH)

    def test_records_that_form_no_known_banner_are_refused(self):
        with self.assertRaisesRegex(ValueError, "form no banner"):
            fis_screen_layout.edits(IMAGE, [((3, 4), (0, 0), (1, 1))])

    def test_an_unencodable_coordinate_is_named(self):
        with self.assertRaisesRegex(ValueError, "coordinate 4095"):
            fis_screen_layout.edits(IMAGE, [((1, 2), (90, 160), (4095, 160))])

    def test_a_moved_constant_nobody_recorded_is_refused(self):
        output = bytearray(retail_output())
        address, axis, prefix = BANNERS[(5, 6, 7)].words[0]
        struct.pack_into("<I", output, overlay_edits.offset_of(address),
                         fis_screen_layout.position_word(192, prefix))
        with self.assertRaisesRegex(ValueError, "expected"):
            overlay_edits.edit_output(bytes(output),
                                      fis_screen_layout.edits(IMAGE, BOTH))


class LayoutFileTests(unittest.TestCase):
    def groups(self):
        return [{"records": list(r), "from": list(f), "to": list(t)}
                for r, f, t in BOTH]

    def test_screen_layouts_come_from_the_pack_beside_the_images(self):
        with tempfile.TemporaryDirectory() as folder:
            pt = write_pack(Path(folder, "pt-BR"),
                            {IMAGE: {"screen": {"groups": self.groups()}}})
            sv = write_pack(Path(folder, "sv-SE"), {})
            self.assertEqual([(IMAGE, BOTH)], fis_images.screen_layouts(pt)[1])
            self.assertEqual([], fis_images.screen_layouts(sv)[1])

    def test_a_layout_for_a_picture_the_pack_lacks_moves_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            images = write_pack(folder, {IMAGE: {"screen": {"groups": self.groups()}}},
                                create=())
            self.assertEqual([], fis_images.screen_layouts(images)[1])

    def test_unknown_keys_are_refused(self):
        for images in ({IMAGE: {"scren": {"groups": self.groups()}}},
                       {IMAGE: {"screen": {"from": [225, 358], "to": [1, 1]}}},
                       {IMAGE: {"screen": {"groups": [dict(self.groups()[0],
                                                           offset=3)]}}}):
            with self.subTest(images=images), \
                    tempfile.TemporaryDirectory() as folder:
                folder = write_pack(folder, images)
                with self.assertRaisesRegex(fis_images.FisError, "unknown"):
                    fis_images.load_layout(folder)

    def test_overlapping_groups_are_refused(self):
        groups = self.groups()
        groups[1]["records"] = [2, 5]
        with tempfile.TemporaryDirectory() as folder:
            folder = write_pack(folder, {IMAGE: {"screen": {"groups": groups}}})
            with self.assertRaisesRegex(fis_images.FisError,
                                        "repeats screen record"):
                fis_images.load_layout(folder)


class BuildStepTests(unittest.TestCase):
    def test_the_label_and_every_screen_move_are_one_edit_list(self):
        with tempfile.TemporaryDirectory() as folder:
            images = write_pack(folder, {IMAGE: {"screen": {"groups": [
                {"records": [5, 6, 7], "from": [225, 358], "to": [188, 358]}]}}})
            misc = Path(folder, "misc.csv")
            misc.write_text("key,translated,offset_x,notes\n"
                            "battle_target,Alvo,,\n", encoding="utf-8")
            rows = [
                {"kind": "image", "resource": "1781", "sheet": str(images)},
                {"kind": "image", "resource": "10", "sheet": str(images)},
                {"kind": "image", "resource": "1781", "sheet": str(images)},
                {"kind": "misc", "resource": "1781", "sheet": str(misc)},
            ]
            edits = vp2_build.battle_overlay_edits(rows)
        label_edits = len(vp2_build.battle_target.edits("Alvo"))
        screen_edits = len(BANNERS[(5, 6, 7)].words)
        self.assertEqual(label_edits + screen_edits, len(edits))

    def test_nothing_to_edit_is_no_step(self):
        self.assertEqual([], vp2_build.battle_overlay_edits([]))

    def test_the_label_is_written_only_when_a_misc_row_asks(self):
        with tempfile.TemporaryDirectory() as folder:
            misc = Path(folder, "misc.csv")
            misc.write_text("key,translated,offset_x,notes\n"
                            "battle_target,Alvo,,\n", encoding="utf-8")
            row = {"kind": "misc", "resource": "1781", "sheet": str(misc)}
            self.assertEqual("Alvo", vp2_build.battle_target_label([row]))
            self.assertIsNone(vp2_build.battle_target_label(
                [{"kind": "scene", "resource": "273", "sheet": str(misc)}]))
            with self.assertRaisesRegex(ValueError, "resource 1781"):
                vp2_build.battle_target_label([dict(row, resource="273")])

    def test_a_blank_label_edits_nothing(self):
        with tempfile.TemporaryDirectory() as folder:
            misc = Path(folder, "misc.csv")
            misc.write_text("key,translated,offset_x,notes\n"
                            "battle_target,,,\n", encoding="utf-8")
            self.assertEqual([], vp2_build.battle_overlay_edits(
                [{"kind": "misc", "resource": "1781", "sheet": str(misc)}]))


if __name__ == "__main__":
    unittest.main()
