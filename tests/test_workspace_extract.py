# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

import ast
import csv
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tools.scripts import vp2_container_text, workspace_extract
from tools.scripts.translation_pack import PackError, _read_csv
from tools.scripts.workspace_extract import (
    _export_chapters,
    _export_reference_images,
    _looks_like_stream_chain,
    _normalize_source_sheet,
    _replace_generated_tree,
    load_reference_images,
)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


class WorkspaceExtractTests(unittest.TestCase):
    def test_stream_chain_candidate_uses_header_shape(self):
        structured = bytearray(0x100)
        structured[4:8] = (3).to_bytes(4, "little")
        structured[8:12] = (0x20).to_bytes(4, "little")
        self.assertTrue(_looks_like_stream_chain(bytes(structured)))
        structured[8:12] = (0x21).to_bytes(4, "little")
        self.assertFalse(_looks_like_stream_chain(bytes(structured)))

    def test_runtime_has_no_private_top_level_sibling_imports(self):
        scripts = Path(__file__).parents[1] / "tools" / "scripts"
        sibling_names = {path.stem for path in scripts.glob("*.py")}
        leaks = []
        for path in scripts.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".", 1)[0] in sibling_names:
                            leaks.append(f"{path.name}:{node.lineno}:{alias.name}")
                elif (isinstance(node, ast.ImportFrom) and node.level == 0
                      and (node.module or "").split(".", 1)[0] in sibling_names):
                    leaks.append(f"{path.name}:{node.lineno}:{node.module}")
        self.assertEqual([], leaks)

    def test_scene_sheet_gets_shared_workspace_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scene.csv"
            write_csv(path, [
                "resource", "message_id", "original_en", "original_jp",
                "translated",
            ], [{
                "resource": "31", "message_id": "7",
                "original_en": "English", "original_jp": "Japanese",
                "translated": "must not survive regeneration",
            }])
            self.assertEqual(1, _normalize_source_sheet(path, "scene"))
            fields, rows = _read_csv(path)
            self.assertEqual("kind", fields[0])
            self.assertIn("message_index", fields)
            self.assertIn("notes", fields)
            self.assertEqual("scene", rows[0]["kind"])
            self.assertEqual("", rows[0]["translated"])

    def test_container_record_kind_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "container.csv"
            write_csv(path, [
                "resource", "message_id", "kind", "original_en",
                "original_jp", "translated",
            ], [{
                "resource": "10", "message_id": "2021+1", "kind": "token",
                "original_en": "English", "original_jp": "Japanese",
                "translated": "",
            }])
            self.assertEqual(1, _normalize_source_sheet(path, "container"))
            fields, rows = _read_csv(path)
            self.assertIn("record_kind", fields)
            self.assertNotEqual(fields.index("kind"), fields.index("record_kind"))
            self.assertEqual("container", rows[0]["kind"])
            self.assertEqual("token", rows[0]["record_kind"])

    def test_container_export_joins_japanese_to_string_token_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "japanese.iso"
            image.touch()
            output = root / "container-0010.csv"
            metadata = {
                "text_start": 0, "text_end": 1, "font_start": 1,
                "glyph_count": 1,
            }
            token_rows = [{
                "key": "2000", "offset": 0, "byte_length": 1,
                "original_en": "Danger.",
            }]
            args = SimpleNamespace(
                iso=str(image), csv=str(output), resource=10,
                jp_iso=str(image), jp_glyphs="glyphs.csv",
                jp_names="names.csv",
            )
            with (
                mock.patch.object(
                    vp2_container_text.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    vp2_container_text, "container", return_value=b""),
                mock.patch.object(
                    vp2_container_text, "read_messages",
                    return_value=(metadata, [])),
                mock.patch.object(
                    vp2_container_text, "walk_block", return_value=token_rows),
                mock.patch.object(
                    vp2_container_text, "japanese_text",
                    return_value={"2000": "\u6765\u308b\u308f\u2026"}),
                mock.patch("builtins.print"),
            ):
                vp2_container_text.cmd_export(args)

            _fields, rows = _read_csv(output)
            self.assertEqual("\u6765\u308b\u308f\u2026", rows[0]["original_jp"])

    def test_chapter_export_uses_region_specific_japanese_message_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usa = root / "usa.iso"
            japanese = root / "japanese.iso"
            usa.touch()
            japanese.touch()
            records = root / "chapter-records.csv"
            output = root / "chapters.csv"
            write_csv(records, [
                "chapter", "resource", "message_id", "japanese_message_id",
            ], [{
                "chapter": "1", "resource": "1197", "message_id": "2739",
                "japanese_message_id": "2765",
            }])
            decoded = [
                (0, 2765, (None, None), "\u795e\u306b\u53db\u304d\u3057\u8005"),
            ]
            with (
                mock.patch.object(
                    workspace_extract.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    workspace_extract.vp2_title_face, "FileIsoForTitleFace"),
                mock.patch.object(
                    workspace_extract.vp2_title_face, "build_face",
                    return_value=({}, {})),
                mock.patch.object(
                    workspace_extract.vp2_title_face, "decode_title",
                    return_value="defiers of the Gods"),
                mock.patch.object(
                    workspace_extract.vp2_jp_glyphs, "load_glyph_names",
                    return_value={}),
                mock.patch.object(
                    workspace_extract.vp2_jp_glyphs, "decode_resource",
                    return_value=(decoded, 10, 1)),
            ):
                self.assertEqual(1, _export_chapters(
                    usa, records, output, japanese,
                    root / "japanese-glyphs.csv", root / "jp.csv"))

            _fields, rows = _read_csv(output)
            self.assertEqual("defiers of the Gods", rows[0]["original_en"])
            self.assertEqual(
                "\u795e\u306b\u53db\u304d\u3057\u8005", rows[0]["original_jp"])

    def test_reference_image_table_groups_by_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(root / "reference-images.csv", [
                "resource", "file", "note",
            ], [
                {"resource": "10", "note": "",
                 "file": "fis-0010-slz-0x2D04-4CE80.png"},
                {"resource": "10", "note": "",
                 "file": "fis-0010-slz-0x2D04-55180.png"},
                {"resource": "24", "note": "",
                 "file": "fis-0024-slz-0x437C0-0.png"},
            ])
            grouped = load_reference_images(root)
            self.assertEqual([10, 24], sorted(grouped))
            self.assertEqual(2, len(grouped[10]))
            self.assertEqual("fis-0024-slz-0x437C0-0.png", grouped[24][0])

    def test_reference_image_table_rejects_a_mismatched_resource(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(root / "reference-images.csv", [
                "resource", "file", "note",
            ], [{"resource": "24", "note": "",
                 "file": "fis-0010-slz-0x2D04-4CE80.png"}])
            with self.assertRaisesRegex(PackError, "names resource 10"):
                load_reference_images(root)

    def test_reference_image_table_rejects_a_stray_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_csv(root / "reference-images.csv", [
                "resource", "file", "note",
            ], [{"resource": "10", "note": "", "file": "picture.png"}])
            with self.assertRaisesRegex(PackError, "fis-NNNN"):
                load_reference_images(root)

    def test_reference_image_export_renders_the_listed_item(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usa = root / "usa.iso"
            usa.touch()
            rendered = []
            with (
                mock.patch.object(
                    workspace_extract.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    workspace_extract.dcms, "read_entry", return_value=b"raw"),
                mock.patch.object(
                    workspace_extract.fis_images, "views",
                    return_value=[("slz@0x0", b"blob", None)]),
                mock.patch.object(
                    workspace_extract.fis_images, "items_in",
                    return_value=[(0, b"item")]),
                mock.patch.object(
                    workspace_extract.fis_images, "render",
                    side_effect=lambda item, path: rendered.append(
                        (item, path))),
            ):
                count = _export_reference_images(
                    usa, {24: ["fis-0024-slz-0x0-0.png"]}, root / "images")
            self.assertEqual(1, count)
            self.assertEqual(b"item", rendered[0][0])
            self.assertEqual("fis-0024-slz-0x0-0.png", rendered[0][1].name)

    def test_reference_image_export_follows_a_relocated_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usa = root / "usa.iso"
            usa.touch()
            rendered = []
            items = {
                b"sheet stream": [(0, b"sheet")],
                b"banner stream": [(0x98C80, b"banner")],
                b"nested item": [(0, b"other")],
            }
            with (
                mock.patch.object(
                    workspace_extract.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    workspace_extract.dcms, "read_entry", return_value=b"raw"),
                mock.patch.object(
                    workspace_extract.fis_images, "views", return_value=[
                        ("slz@0x180", b"sheet stream", None),
                        ("slz@0x380", b"banner stream", None),
                        ("slz@0x380/item2", b"nested item", None),
                    ]),
                mock.patch.object(
                    workspace_extract.fis_images, "items_in",
                    side_effect=lambda blob: items[blob]),
                mock.patch.object(
                    workspace_extract.fis_images, "render",
                    side_effect=lambda item, path: rendered.append(
                        (item, path))),
            ):
                names = [
                    "fis-0024-unprotected-slz-0x100-0.png",
                    "fis-0024-unprotected-slz-0x300-98C80.png",
                ]
                count = _export_reference_images(
                    usa, {24: names}, root / "images")
            self.assertEqual(2, count)
            self.assertEqual(b"sheet", rendered[0][0])
            self.assertEqual(names[0], rendered[0][1].name)
            self.assertEqual(b"banner", rendered[1][0])
            self.assertEqual(names[1], rendered[1][1].name)

    def test_reference_image_export_refuses_an_ambiguous_relocation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usa = root / "usa.iso"
            usa.touch()
            with (
                mock.patch.object(
                    workspace_extract.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    workspace_extract.dcms, "read_entry", return_value=b"raw"),
                mock.patch.object(
                    workspace_extract.fis_images, "views", return_value=[
                        ("slz@0x100", b"first", None),
                        ("slz@0x200", b"second", None),
                    ]),
                mock.patch.object(
                    workspace_extract.fis_images, "items_in",
                    side_effect=lambda blob: [(0, blob)]),
            ):
                name = "fis-0024-unprotected-slz-0x300-0.png"
                with self.assertRaisesRegex(PackError, name):
                    _export_reference_images(
                        usa, {24: [name]}, root / "images")

    def test_reference_image_export_names_what_the_disc_lacks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            usa = root / "usa.iso"
            usa.touch()
            with (
                mock.patch.object(
                    workspace_extract.triace, "load_table",
                    return_value=("VP2", 1, [])),
                mock.patch.object(
                    workspace_extract.dcms, "read_entry", return_value=b"raw"),
                mock.patch.object(
                    workspace_extract.fis_images, "views",
                    return_value=[("slz@0x0", b"blob", None)]),
                mock.patch.object(
                    workspace_extract.fis_images, "items_in",
                    return_value=[(0, b"item")]),
            ):
                with self.assertRaisesRegex(
                        PackError, "fis-0024-slz-0x0-8.png"):
                    _export_reference_images(
                        usa, {24: ["fis-0024-slz-0x0-8.png"]},
                        root / "images")

    def test_reference_off_skips_the_translator_tables(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            (data / "glyph-names").mkdir(parents=True)
            for relative in ("glyph-names/en.csv", "glyph-names/jp.csv",
                             "chapter-records.csv", "menu-layout.csv"):
                (data / relative).write_text("x", encoding="utf-8")
            usa = root / "usa.iso"
            usa.write_bytes(b"")
            called = []

            def refuse(*_args, **_kwargs):
                called.append(True)
                raise AssertionError("translator work ran with reference off")

            with (
                mock.patch.object(
                    workspace_extract, "resolve_sources",
                    return_value=(usa, None)),
                mock.patch.object(
                    workspace_extract, "write_inventory", return_value=[]),
                mock.patch.object(
                    workspace_extract, "_export_scenes",
                    return_value=(0, 0)),
                mock.patch.object(
                    workspace_extract, "_export_containers",
                    return_value=(0, 0, 0)),
                mock.patch.object(
                    workspace_extract, "_export_container_subresources",
                    return_value=(0, 0)),
                mock.patch.object(
                    workspace_extract, "_export_dragon_hall_prompts",
                    return_value=(0, 0)),
                mock.patch.object(
                    workspace_extract, "_export_chapters", side_effect=refuse),
                mock.patch.object(
                    workspace_extract, "write_reference_tree",
                    side_effect=refuse),
                mock.patch.object(
                    workspace_extract, "_export_reference_images",
                    side_effect=refuse),
            ):
                details = workspace_extract.generate_workspace(
                    [usa], root / "ws", data_root=data, reference=False)
            self.assertEqual([], called)
            self.assertFalse((root / "ws" / "reference").exists())
            self.assertNotIn("reference_rows", details)
            self.assertNotIn("reference_images", details)

    def test_source_snapshot_replacement_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "internal"
            generated = root / "staging" / "internal"
            target.mkdir(parents=True)
            generated.mkdir(parents=True)
            (target / "old.txt").write_text("old", encoding="utf-8")
            (generated / "new.txt").write_text("new", encoding="utf-8")
            _replace_generated_tree(target, generated)
            self.assertFalse((target / "old.txt").exists())
            self.assertEqual("new", (target / "new.txt").read_text("utf-8"))
            self.assertFalse((root / ".internal-previous").exists())


if __name__ == "__main__":
    unittest.main()
