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
        return sorted(path for path in directory.iterdir()
                      if (path / "pack.toml").is_file())

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

    def test_a_language_that_is_not_installed_lists_the_ones_that_are(self):
        from tools.scripts.public_build import resolve_pack
        from tools.scripts.translation_pack import PackError
        with self.assertRaises(PackError) as raised:
            resolve_pack("xx-XX")
        self.assertIn("pt-BR", str(raised.exception))


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
                side_effect=lambda images, where: generated.append(
                    ([Path(image) for image in images], Path(where)))), \
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
        images, where = generated[0]
        self.assertEqual([source], images)
        self.assertEqual(workspace, where)

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

        with mock.patch.object(public_build, "workspace_is_ready",
                               return_value=True), \
                mock.patch.object(
                    public_build, "compile_build_workspace",
                    return_value={"locale": "pt-BR", "manifest": "m.csv",
                                  "sheets": "sheets", "slots": None}), \
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
