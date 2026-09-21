"""Rules about what a public build writes and what it keeps."""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CacheLocationTests(unittest.TestCase):
    def test_a_checkout_caches_inside_the_workspace(self):
        from tools.scripts import paths
        self.assertFalse(paths.FROZEN)
        self.assertEqual(
            (paths.PROJECT_ROOT / "workspace" / "internal" / ".cache"),
            paths.CACHE_ROOT)

    def test_a_packaged_run_writes_its_iso_where_it_was_invoked(self):
        from tools.scripts import paths
        self.assertEqual(paths.BUILD_DIR, paths.output_root())
        try:
            paths.FROZEN = True
            self.assertEqual(Path.cwd(), paths.output_root())
        finally:
            paths.FROZEN = False

    def test_the_cache_is_not_beside_the_output_iso(self):
        from tools.scripts import paths
        self.assertNotEqual(paths.BUILD_DIR / ".cache", paths.CACHE_ROOT)

    def test_no_seed_promotion_machinery_exists(self):
        import importlib
        for name in ("build_cache", "promote_slz_cache"):
            with self.subTest(module=name):
                with self.assertRaises(ImportError):
                    importlib.import_module(f"tools.scripts.{name}")

        source = (ROOT / "tools" / "scripts").glob("*.py")
        offenders = [p.name for p in source
                     if "PROMOTES_TRACKED_SEEDS" in p.read_text(encoding="utf-8")]
        self.assertEqual([], offenders)



class PackProfileTests(unittest.TestCase):
    def _packs(self):
        directory = ROOT / "translations"
        from tools.scripts.translation_pack import is_language_pack
        return sorted(path for path in directory.iterdir()
                      if is_language_pack(path))

    def test_every_installed_pack_carries_a_valid_profile(self):
        from tools.scripts.public_build import check_pack_profile
        packs = self._packs()
        self.assertTrue(packs)
        for pack in packs:
            with self.subTest(pack=pack.name):
                self.assertTrue((pack / "build-profile.csv").is_file())
                self.assertGreater(check_pack_profile(pack), 0)

    def test_a_profile_is_not_read_as_a_translation_sheet(self):
        """It sits at the pack root, where load_pack walks for CSVs."""
        from tools.scripts.translation_pack import load_pack
        load_pack(ROOT / "translations" / "sv-SE")

    def test_every_installed_pack_loads(self):
        """A duplicate id or stray column is a build-breaking pack defect."""
        from tools.scripts.translation_pack import load_pack
        for pack in self._packs():
            with self.subTest(pack=pack.name):
                load_pack(pack)

    def test_a_pack_without_a_profile_says_so(self):
        import shutil
        import tempfile
        from tools.scripts.public_build import check_pack_profile
        from tools.scripts.translation_pack import PackError
        with tempfile.TemporaryDirectory() as elsewhere:
            pack = Path(elsewhere) / "xx-XX"
            pack.mkdir()
            shutil.copy(ROOT / "translations" / "sv-SE" / "pack.toml", pack)
            with self.assertRaises(PackError) as raised:
                check_pack_profile(pack)
            self.assertIn("build-profile.csv", str(raised.exception))

    def test_a_profile_cannot_name_pack_assets_that_are_missing(self):
        import tempfile
        from tools.scripts.public_build import check_pack_profile
        from tools.scripts.translation_pack import PackError
        with tempfile.TemporaryDirectory() as elsewhere:
            pack = Path(elsewhere) / "xx-XX"
            pack.mkdir()
            (pack / "pack.toml").write_text(
                'format = 2\nlocale = "xx-XX"\nname = "Test"\n',
                encoding="utf-8")
            (pack / "build-profile.csv").write_text(
                "kind,resource,sheet,flags,verify,subresource\n"
                "image,1781,images,,,,\n"
                "misc,1781,misc.csv,,,,\n",
                encoding="utf-8")
            with self.assertRaises(PackError) as raised:
                check_pack_profile(pack)
            self.assertIn("images", str(raised.exception))

    def test_a_profile_cannot_name_a_missing_misc_file(self):
        import tempfile
        from tools.scripts.public_build import check_pack_profile
        from tools.scripts.translation_pack import PackError
        with tempfile.TemporaryDirectory() as elsewhere:
            pack = Path(elsewhere) / "xx-XX"
            pack.mkdir()
            (pack / "pack.toml").write_text(
                'format = 2\nlocale = "xx-XX"\nname = "Test"\n',
                encoding="utf-8")
            (pack / "build-profile.csv").write_text(
                "kind,resource,sheet,flags,verify,subresource\n"
                "misc,1781,misc.csv,,,,\n",
                encoding="utf-8")
            with self.assertRaises(PackError) as raised:
                check_pack_profile(pack)
            self.assertIn("misc.csv", str(raised.exception))

    def test_a_language_that_is_not_installed_lists_the_ones_that_are(self):
        from tools.scripts.public_build import resolve_pack
        from tools.scripts.translation_pack import PackError
        with self.assertRaises(PackError) as raised:
            resolve_pack("xx-XX")
        self.assertIn("pt-BR", str(raised.exception))

    def test_battle_target_comes_from_misc_csv(self):
        import tempfile
        from tools.scripts.public_build import _pack_battle_target
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder)
            (pack / "misc.csv").write_text(
                "key,translated,offset_x,notes\n"
                "battle_target,Alvo,,Floating label\n",
                encoding="utf-8")
            self.assertEqual("Alvo", _pack_battle_target(pack))

    def test_invalid_battle_name_is_refused_while_compiling_the_pack(self):
        import tempfile
        from tools.scripts.public_build import _validated_misc
        from tools.scripts.translation_pack import PackError
        with tempfile.TemporaryDirectory() as folder:
            pack = Path(folder)
            (pack / "misc.csv").write_text(
                "key,translated,offset_x,notes\n"
                "battle_name_0A,VALQUÍRIA,,VALKYRIE\n",
                encoding="utf-8")
            with self.assertRaisesRegex(PackError, "A-Z or hyphen"):
                _validated_misc(pack)


class UnlistedFolderTests(unittest.TestCase):
    """A `_` folder under translations/ is a starting point, not a language."""

    def test_it_is_not_offered_bundled_or_checked(self):
        import shutil
        import tempfile
        from unittest import mock
        from tools import translate_gui
        from tools.scripts import public_build, public_release
        with tempfile.TemporaryDirectory() as elsewhere:
            root = Path(elsewhere)
            for name in ("xx-XX", "_draft"):
                pack = root / "translations" / name
                pack.mkdir(parents=True)
                shutil.copy(ROOT / "translations" / "sv-SE" / "pack.toml", pack)
                (pack / "chapter.csv").write_text(
                    "resource,message_id,translated,notes\n", encoding="utf-8")
            with mock.patch.object(public_build, "TRANSLATIONS",
                                   root / "translations"):
                self.assertEqual(["xx-XX"], public_build.installed_locales())
            self.assertEqual(["xx-XX"], public_release._packs(root))
            self.assertEqual(
                [root / "translations" / "xx-XX"],
                [pack.path for pack in translate_gui.language_packs(root)])
            bundled = [name for _source, name
                       in public_release.payload_members(root)]
            self.assertIn("translations/xx-XX/chapter.csv", bundled)
            self.assertEqual(
                [], [name for name in bundled if "/_draft/" in name])

    def test_translation_pack_assets_are_bundled_but_replacements_are_not(self):
        import tempfile
        from tools.scripts import public_release
        with tempfile.TemporaryDirectory() as elsewhere:
            root = Path(elsewhere)
            pack = root / "translations" / "xx-XX"
            pack.mkdir(parents=True)
            (pack / "pack.toml").write_text(
                'format = 2\nlocale = "xx-XX"\nname = "Test"\n',
                encoding="utf-8")
            layout = pack / "fis-image-layouts.json"
            layout.write_text('{"version": 1, "images": {}}',
                              encoding="utf-8")
            misc = pack / "misc.csv"
            misc.write_text(
                "key,translated,offset_x,notes\n"
                "battle_target,Target,,Label\n",
                encoding="utf-8")
            image = pack / "images" / "fis-1781-raw-0.png"
            image.parent.mkdir()
            image.write_bytes(b"authored image")
            generated = pack / "replacements" / "manifest.json"
            generated.parent.mkdir()
            generated.write_text("{}", encoding="utf-8")
            bundled = [name for _source, name
                       in public_release.payload_members(root)]
            self.assertIn(
                "translations/xx-XX/fis-image-layouts.json", bundled)
            self.assertIn("translations/xx-XX/misc.csv", bundled)
            self.assertIn("translations/xx-XX/images/fis-1781-raw-0.png",
                          bundled)
            self.assertNotIn(
                "translations/xx-XX/replacements/manifest.json", bundled)


class ChapterProfileSelectionTests(unittest.TestCase):
    def test_chapter_outside_profile_is_ignored(self):
        import csv
        import json
        import tempfile
        from tools.scripts import public_build

        def write_csv(path, fields, rows):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / "workspace"
            records = workspace / "internal" / "records" / "scenes"
            records.mkdir(parents=True)
            (workspace / "internal" / "generation.json").write_text(
                json.dumps({"format": 2}), encoding="utf-8")
            write_csv(records / "resource-0043-scenes.csv", [
                "kind", "resource", "message_id", "message_index",
                "translated",
            ], [{
                "kind": "scene", "resource": "43", "message_id": "1",
                "message_index": "", "translated": "",
            }])

            pack = root / "xx-XX"
            pack.mkdir()
            (pack / "pack.toml").write_text(
                'format = 2\nlocale = "xx-XX"\nname = "Test"\n',
                encoding="utf-8")
            write_csv(pack / "build-profile.csv", [
                "kind", "resource", "sheet", "flags", "verify",
            ], [{
                "kind": "scene", "resource": "43",
                "sheet": "resource-0043-scenes.csv", "flags": "",
                "verify": "yes",
            }])
            write_csv(pack / "chapter.csv", [
                "resource", "original_en", "message_id", "original_jp",
                "translated", "speaker", "notes",
            ], [
                {"resource": "43", "message_id": "900",
                 "translated": "Included Title", "notes": "",
                 "original_en": "Included", "original_jp": "\u53ce\u9332",
                 "speaker": ""},
                {"resource": "1197", "message_id": "2739",
                 "translated": "Excluded Title", "notes": "",
                 "original_en": "Excluded", "original_jp": "\u9664\u5916",
                 "speaker": ""},
            ])
            menu_layout = root / "menu-layout.csv"
            write_csv(menu_layout, [
                "menu", "unit", "resource", "message_id", "message_index",
            ], [])

            compiled = public_build.compile_build_workspace(
                workspace, pack, menu_layout=menu_layout)
            with Path(compiled["manifest"]).open(
                    encoding="utf-8-sig", newline="") as handle:
                manifest = next(csv.DictReader(handle))

        self.assertEqual("Included Title", manifest["chapter_title"])
        self.assertEqual("900", manifest["chapter_title_message"])
        self.assertEqual(1, compiled["outside_profile"])


class PartialProfileTests(unittest.TestCase):
    PACK = Path(__file__).resolve().parents[1] / "translations" / "pt-BR"

    def entries(self):
        from tools.scripts import public_build
        return public_build.profile_entries(self.PACK)

    def test_a_row_is_named_by_its_kind_and_resource(self):
        ids = {entry["id"] for entry in self.entries()}
        self.assertIn("scene-1195", ids)
        self.assertIn("chapter-label-60", ids)

    def test_one_resource_under_two_kinds_stays_two_rows(self):
        ids = {entry["id"] for entry in self.entries()}
        self.assertIn("container-31", ids)
        self.assertIn("fontless-31", ids)

    def test_every_row_has_its_own_id(self):
        entries = self.entries()
        self.assertEqual(len(entries), len({e["id"] for e in entries}))

    def test_the_one_misc_row_reads_as_misc(self):
        labels = {entry["id"]: entry["label"] for entry in self.entries()}
        misc = [key for key in labels if key.startswith("misc-")]
        self.assertEqual(1, len(misc))
        self.assertEqual("misc", labels[misc[0]])

    def test_a_selection_reaches_the_manifest_and_nothing_else_does(self):
        import csv
        from tools.scripts import public_build
        from tools.scripts.paths import WORKSPACE_DIR

        if not public_build.workspace_is_ready(WORKSPACE_DIR):
            self.skipTest("no generated workspace to compile against")
        wanted = {"scene-49", "fontless-31", "chapter-label-60"}
        compiled = public_build.compile_build_workspace(
            WORKSPACE_DIR, self.PACK, only=wanted)
        with Path(compiled["manifest"]).open(
                encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            wanted, {"%s-%s" % (row["kind"], row["resource"]) for row in rows})

    def test_an_id_that_names_nothing_is_refused(self):
        from tools.scripts import public_build
        from tools.scripts.paths import WORKSPACE_DIR

        if not public_build.workspace_is_ready(WORKSPACE_DIR):
            self.skipTest("no generated workspace to compile against")
        with self.assertRaises(public_build.PackError) as caught:
            public_build.compile_build_workspace(
                WORKSPACE_DIR, self.PACK, only={"scene-49", "scene-999999"})
        self.assertIn("scene-999999", str(caught.exception))

    def test_selecting_nothing_is_refused_rather_than_built_empty(self):
        from tools.scripts import public_build
        from tools.scripts.paths import WORKSPACE_DIR

        if not public_build.workspace_is_ready(WORKSPACE_DIR):
            self.skipTest("no generated workspace to compile against")
        with self.assertRaises(public_build.PackError):
            public_build.compile_build_workspace(
                WORKSPACE_DIR, self.PACK, only=set())


class PackFileRowTests(unittest.TestCase):
    CARRIERS = ("60", "1196")

    def compile(self, extra_rows, misc_rows=None):
        import csv
        import json
        import tempfile
        from tools.scripts import public_build

        def write_csv(path, fields, rows):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

        profile = [{"kind": "scene", "resource": "43",
                    "sheet": "resource-0043-scenes.csv", "flags": "",
                    "verify": ""}] + extra_rows
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workspace = root / "workspace"
            records = workspace / "internal" / "records" / "scenes"
            records.mkdir(parents=True)
            (workspace / "internal" / "generation.json").write_text(
                json.dumps({"format": 2}), encoding="utf-8")
            write_csv(records / "resource-0043-scenes.csv",
                      ["kind", "resource", "message_id", "message_index",
                       "translated"],
                      [{"kind": "scene", "resource": "43", "message_id": "1",
                        "message_index": "", "translated": ""}])
            pack = root / "xx-XX"
            pack.mkdir()
            (pack / "pack.toml").write_text(
                'format = 2\nlocale = "xx-XX"\nname = "Test"\n',
                encoding="utf-8")
            write_csv(pack / "build-profile.csv",
                      ["kind", "resource", "sheet", "flags", "verify"],
                      profile)
            write_csv(pack / "chapter.csv",
                      ["resource", "message_id", "translated", "notes"],
                      [{"resource": "60", "message_id": "51",
                        "translated": "KAPITEL", "notes": ""}])
            write_csv(
                pack / "misc.csv", ["key", "translated", "offset_x", "notes"],
                misc_rows if misc_rows is not None else
                [{"key": "battle_target", "translated": "Sikta",
                  "offset_x": "", "notes": ""}])
            menu_layout = root / "menu-layout.csv"
            write_csv(menu_layout, ["menu", "unit", "resource",
                                    "message_id", "message_index"], [])
            compiled = public_build.compile_build_workspace(
                workspace, pack, menu_layout=menu_layout)
            with Path(compiled["manifest"]).open(
                    encoding="utf-8-sig", newline="") as handle:
                manifest = list(csv.DictReader(handle))
            for row in manifest:
                if row["kind"] in ("chapter-label", "misc"):
                    self.assertTrue(Path(row["sheet"]).is_file(), row)
        return compiled, manifest

    def test_a_scene_alone_writes_no_label_and_no_target(self):
        compiled, manifest = self.compile([])
        self.assertEqual(["scene"], [row["kind"] for row in manifest])
        self.assertIsNone(compiled["battle_target"])
        self.assertEqual(2, compiled["outside_profile"])

    def test_listed_rows_reach_the_manifest(self):
        rows = [{"kind": "chapter-label", "resource": carrier,
                 "sheet": "chapter.csv", "flags": "", "verify": ""}
                for carrier in self.CARRIERS]
        rows.append({"kind": "misc", "resource": "1781", "sheet": "misc.csv",
                     "flags": "", "verify": ""})
        compiled, manifest = self.compile(rows)
        self.assertEqual(
            [("scene", "43"), ("chapter-label", "60"),
             ("chapter-label", "1196"), ("misc", "1781")],
            [(row["kind"], row["resource"]) for row in manifest])
        self.assertEqual("Sikta", compiled["battle_target"])
        self.assertEqual(0, compiled["outside_profile"])

    def test_a_battle_name_alone_carries_misc_into_the_manifest(self):
        rows = [{"kind": "misc", "resource": "1781",
                 "sheet": "misc.csv", "flags": "", "verify": ""}]
        compiled, manifest = self.compile(
            rows, [{"key": "battle_name_0A", "translated": "VALQUIRIA",
                    "offset_x": "", "notes": "VALKYRIE"}])
        self.assertEqual([("scene", "43"), ("misc", "1781")],
                         [(row["kind"], row["resource"]) for row in manifest])
        self.assertIsNone(compiled["battle_target"])

    def test_a_row_naming_the_wrong_resource_or_file_is_refused(self):
        from tools.scripts.translation_pack import PackError
        for row, message in (
                ({"kind": "misc", "resource": "273", "sheet": "misc.csv"},
                 "has no misc"),
                ({"kind": "chapter-label", "resource": "43",
                  "sheet": "chapter.csv"}, "has no chapter-label"),
                ({"kind": "misc", "resource": "1781",
                  "sheet": "chapter.csv"}, "names misc.csv")):
            with self.subTest(row=row):
                with self.assertRaisesRegex(PackError, message):
                    self.compile([dict(row, flags="", verify="")])


class ChildProcessTests(unittest.TestCase):
    def _sleeper(self):
        import subprocess
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(process.kill)
        return process

    def test_a_tracked_child_is_stopped_and_waited_for(self):
        from tools.scripts.public_build import _tracked, terminate_active_builds
        process = self._sleeper()
        with _tracked(process):
            self.assertIsNone(process.poll())
            self.assertEqual(1, terminate_active_builds())
        self.assertIsNotNone(process.poll())

    def test_a_finished_build_leaves_nothing_to_stop(self):
        from tools.scripts.public_build import _tracked, terminate_active_builds
        process = self._sleeper()
        with _tracked(process):
            pass
        self.assertEqual(0, terminate_active_builds())
        process.kill()

    def test_the_window_stops_the_child_before_it_closes(self):
        """The close handler must call it, not merely ask about it."""
        import inspect
        from tools import translate_gui as launcher
        guard = inspect.getsource(launcher.App.request_close)
        close = inspect.getsource(launcher.App._on_close)
        self.assertIn("terminate_active_builds()", guard)
        self.assertIn("self.root.destroy()", close)


class AutomaticWorkspaceTests(unittest.TestCase):
    def _build(self, workspace, source):
        import tempfile
        from unittest import mock
        from tools.scripts import public_build
        generated = []
        with mock.patch.object(
                public_build, "generate_workspace",
                side_effect=lambda images, where, **kwargs: generated.append({
                    "images": [Path(image) for image in images],
                    "workspace": Path(where), **kwargs})), \
                mock.patch.object(public_build, "compile_build_workspace",
                                  side_effect=RuntimeError("far enough")):
            with self.assertRaises(RuntimeError):
                public_build.build_iso(source, "pt-BR", workspace=workspace)
        return generated

    def test_an_unprepared_workspace_is_generated_from_the_given_image(self):
        import tempfile
        from tools.scripts.public_build import workspace_is_ready
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "disc.iso"
            source.write_bytes(b"not really an iso")
            workspace = Path(folder) / "workspace"
            self.assertFalse(workspace_is_ready(workspace))
            generated = self._build(workspace, source)
        self.assertEqual(1, len(generated))
        self.assertEqual([source], generated[0]["images"])
        self.assertEqual(workspace, generated[0]["workspace"])

    def test_a_packaged_build_skips_the_translator_tables(self):
        import tempfile
        from unittest import mock
        from tools.scripts import public_build
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "disc.iso"
            source.write_bytes(b"not really an iso")
            with mock.patch.object(public_build, "FROZEN", True):
                generated = self._build(Path(folder) / "workspace", source)
        self.assertIs(False, generated[0]["reference"])

    def test_a_source_build_asks_for_the_translator_tables(self):
        import tempfile
        from unittest import mock
        from tools.scripts import public_build
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "disc.iso"
            source.write_bytes(b"not really an iso")
            with mock.patch.object(public_build, "FROZEN", False):
                generated = self._build(Path(folder) / "workspace", source)
        self.assertIs(True, generated[0]["reference"])

    def test_a_prepared_workspace_is_left_alone(self):
        import tempfile
        from tools.scripts.public_build import workspace_is_ready
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "disc.iso"
            source.write_bytes(b"not really an iso")
            workspace = Path(folder) / "workspace"
            internal = workspace / "internal"
            (internal / "records").mkdir(parents=True)
            (internal / "generation.json").write_text("{}", encoding="utf-8")
            self.assertTrue(workspace_is_ready(workspace))
            self.assertEqual([], self._build(workspace, source))

    def test_readiness_needs_both_the_stamp_and_the_records(self):
        import tempfile
        from tools.scripts.public_build import workspace_is_ready
        with tempfile.TemporaryDirectory() as folder:
            internal = Path(folder) / "internal"
            internal.mkdir()
            (internal / "generation.json").write_text("{}", encoding="utf-8")
            self.assertFalse(workspace_is_ready(folder))
            (internal / "records").mkdir()
            self.assertTrue(workspace_is_ready(folder))


if __name__ == "__main__":
    unittest.main()


class BuildRootInstallTests(unittest.TestCase):
    def _tree(self, root, name, marker):
        path = Path(root) / name
        path.mkdir()
        (path / "build.json").write_text(marker, encoding="utf-8")
        return path

    def test_a_fresh_name_is_just_taken(self):
        import tempfile
        from tools.scripts.public_build import _install_build_root
        with tempfile.TemporaryDirectory() as root:
            staging = self._tree(root, "staging", "new")
            target = Path(root) / "pt-BR"
            _install_build_root(staging, target)
            self.assertEqual("new", (target / "build.json").read_text(encoding="utf-8"))
            self.assertFalse(staging.exists())

    def test_an_existing_tree_is_replaced_and_removed(self):
        import tempfile
        from tools.scripts.public_build import _install_build_root
        with tempfile.TemporaryDirectory() as root:
            staging = self._tree(root, "staging", "new")
            target = self._tree(root, "pt-BR", "old")
            _install_build_root(staging, target)
            self.assertEqual("new", (target / "build.json").read_text(encoding="utf-8"))
            leftovers = [p.name for p in Path(root).iterdir() if p.name != "pt-BR"]
            self.assertEqual([], leftovers)

    def test_a_replace_that_argues_once_still_lands(self):
        import tempfile
        from unittest import mock
        from tools.scripts import public_build
        with tempfile.TemporaryDirectory() as root:
            staging = self._tree(root, "staging", "new")
            target = self._tree(root, "pt-BR", "old")
            real = Path.replace
            calls = []

            def flaky(self, other):
                calls.append(other)
                if len(calls) == 2:
                    raise PermissionError(5, "Access is denied")
                return real(self, other)

            with mock.patch.object(Path, "replace", flaky), \
                    mock.patch.object(public_build.time, "sleep"):
                public_build._install_build_root(staging, target)
            self.assertEqual("new", (target / "build.json").read_text(encoding="utf-8"))
            self.assertGreater(len(calls), 2)


class ChildOutputTests(unittest.TestCase):
    def test_a_console_that_cannot_encode_it_still_gets_the_line(self):
        import io as _io
        from unittest import mock
        from tools.scripts import public_build

        class Cp1252Stream(_io.StringIO):
            encoding = "cp1252"

            def write(self, text):
                text.encode("cp1252")      # raises exactly as the real one does
                return super().write(text)

        stream = Cp1252Stream()
        with mock.patch.object(public_build.sys, "stdout", stream):
            public_build._echo("scene 1213 \ufffd ok\n")
        self.assertIn("scene 1213", stream.getvalue())
        self.assertIn("ok", stream.getvalue())

    def test_an_ordinary_console_is_untouched(self):
        import io as _io
        from unittest import mock
        from tools.scripts import public_build

        class Utf8Stream(_io.StringIO):
            encoding = "utf-8"

        stream = Utf8Stream()
        with mock.patch.object(public_build.sys, "stdout", stream):
            public_build._echo("caf\u00e9 \ufffd\n")
        self.assertEqual("caf\u00e9 \ufffd\n", stream.getvalue())


class CandidateExtentWiringTests(unittest.TestCase):
    """The runtime could record an unmeasured ceiling; nothing asked it to."""

    def _runtime_args(self, **keywords):
        from unittest import mock
        from tools.scripts import public_build
        seen = []

        def stop(arguments):
            seen.append(list(arguments))
            raise RuntimeError("far enough")

        compiled = {"locale": "pt-BR", "manifest": "m.csv",
                    "sheets": "sheets", "slots": None}
        with mock.patch.object(public_build, "workspace_is_ready",
                               return_value=True), \
                mock.patch.object(
                    public_build, "compile_build_workspace",
                    return_value=compiled), \
                mock.patch.object(public_build, "ensure_glyph_pool",
                                  return_value=None), \
                mock.patch.object(public_build, "runtime_command",
                                  side_effect=stop):
            import tempfile
            with tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / "disc.iso"
                source.write_bytes(b"not really an iso")
                with self.assertRaises(RuntimeError):
                    public_build.build_iso(
                        source, "pt-BR",
                        output=Path(folder) / "out.iso", **keywords)
        return seen[0]

    def test_an_ordinary_build_asks_the_runtime_to_record_candidates(self):
        self.assertIn("--record-candidate-extents", self._runtime_args())

    def test_strict_extents_asks_it_to_refuse_instead(self):
        self.assertNotIn("--record-candidate-extents",
                         self._runtime_args(strict_extents=True))

    def test_the_command_line_exposes_the_strict_option(self):
        """A release build needs the refusal the default gives up."""
        import vp2_translate
        arguments = vp2_translate._parser().parse_args(
            ["build", "disc.iso", "pt-BR", "--strict-extents"])
        self.assertTrue(arguments.strict_extents)
        self.assertFalse(vp2_translate._parser().parse_args(
            ["build", "disc.iso"]).strict_extents)

    def test_the_command_line_passes_it_through(self):
        import inspect
        import vp2_translate
        self.assertIn("strict_extents=args.strict_extents",
                      inspect.getsource(vp2_translate.main))
