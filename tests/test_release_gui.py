# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Release-window and frozen handoff regressions."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import vp2_tools as tools_launcher  # noqa: E402
from tools import translate_gui as launcher  # noqa: E402
from tools.scripts import public_build  # noqa: E402


class LauncherLogicTests(unittest.TestCase):
    def test_no_arguments_selects_the_gui(self):
        args = tools_launcher._parser().parse_args([])
        self.assertFalse(args.self_check)

    def test_legacy_launchers_are_command_line_only(self):
        import contextlib
        import io
        import vp2_cheats
        import vp2_glyphs
        import vp2_translate
        import vp2_voices

        for module in (vp2_translate, vp2_cheats, vp2_voices,
                       vp2_glyphs):
            with self.subTest(module=module.__name__), \
                    contextlib.redirect_stderr(io.StringIO()):
                try:
                    status = module.main([])
                except SystemExit as exc:
                    status = exc.code
                self.assertEqual(2, status)
            source = (ROOT / (module.__name__ + ".py")).read_text(
                encoding="utf-8")
            self.assertNotIn("tkinter", source)
            self.assertNotIn("run_gui", source)
            self.assertNotIn(".gui import", source)

    def test_language_packs_are_named_for_people(self):
        packs = launcher.language_packs(ROOT)
        self.assertIn("pt-BR", [pack.locale for pack in packs])
        self.assertTrue(all(pack.path.is_dir() for pack in packs))

    def test_output_name_carries_the_locale(self):
        pack = launcher.LanguagePack("Português", "pt-BR", Path("pack"))
        output = launcher.output_path_for(
            Path("Valkyrie Profile 2.iso"), pack, Path("releases"))
        self.assertEqual(
            Path("releases/Valkyrie Profile 2.pt-BR.iso"), output)

    def test_window_is_centred_and_never_uses_the_withdrawn_sentinel(self):
        class Screen:
            def winfo_screenwidth(self):
                return 1920

            def winfo_screenheight(self):
                return 1080

        self.assertEqual(
            "900x570+510+255",
            launcher.initial_window_geometry(Screen(), 900, 570))


class FrozenRuntimeTests(unittest.TestCase):
    def test_source_build_uses_python_module_mode(self):
        command = public_build.runtime_command(["source.iso"])
        self.assertEqual([sys.executable, "-m", "tools.scripts.vp2_build"],
                         command[:3])

    def test_frozen_build_reenters_the_launcher_without_dash_m(self):
        previous = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            command = public_build.runtime_command(["source.iso", "--no-verify"])
        finally:
            if previous is None:
                del sys.frozen
            else:
                sys.frozen = previous
        self.assertEqual(
            [sys.executable, "_runtime-build", "source.iso", "--no-verify"],
            command)


class ReleaseSpecTests(unittest.TestCase):
    def setUp(self):
        self.spec_path = ROOT / "data" / "vp2_tools.spec"
        self.spec = self.spec_path.read_text(encoding="utf-8")

    def test_spec_and_pyinstaller_work_tree_use_internal_build_storage(self):
        """Only the unified GUI ships and it scratches inside the workspace."""
        self.assertTrue(self.spec_path.is_file())
        self.assertFalse((ROOT / "data" / "vp2_release.spec").exists())
        self.assertFalse((ROOT / "data" / "vp2_cheats.spec").exists())
        self.assertFalse((ROOT / "data" / "vp2_voices.spec").exists())
        expected_workpath = "--workpath workspace/internal/build"
        for path in (
                ROOT / "Dockerfile",
                ROOT / ".github" / "workflows" / "release.yml"):
            source = path.read_text(encoding="utf-8")
            self.assertIn("data/vp2_tools.spec", source, path)
            for old in ("data/vp2_release.spec", "data/vp2_cheats.spec",
                        "data/vp2_voices.spec"):
                self.assertNotIn(old, source, "%s: %s" % (path, old))
            self.assertIn(expected_workpath, source, path)
            self.assertIn("build_tac_encoder.py", source, path)
            self.assertNotIn(
                "--workpath workspace/internal/build/vp2_release", source,
                path)

    def test_double_click_builds_for_the_window_subsystem(self):
        self.assertIn("console=False", self.spec)
        self.assertNotIn('"tkinter", "_tkinter"', self.spec)
        self.assertIn("vp2-tac-encode", self.spec)

    def test_icon_and_backdrop_are_bundled(self):
        self.assertIn("icon=", self.spec)
        for name in (launcher.ICON_ICO, launcher.ICON_PNG,
                     launcher.BACKDROP_PNG):
            self.assertTrue((ROOT / name).is_file(), name)
            self.assertIn(name, self.spec)

    def test_self_check_initializes_tcl_not_just_the_python_wrapper(self):
        source = (ROOT / "tools" / "scripts" / "public_release.py").read_text(
            encoding="utf-8")
        self.assertIn("tkinter.Tcl()", source)
        self.assertIn("_tcl_data/init.tcl", source)
        self.assertIn("_tk_data/tk.tcl", source)

    def test_linux_release_uses_the_same_pinned_container_locally_and_in_ci(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8")
        self.assertIn("FROM ubuntu:24.04", dockerfile)
        self.assertIn("ARG PYTHON_VERSION=3.11.16", dockerfile)
        self.assertIn("ARG PYINSTALLER_VERSION=6.22.2", dockerfile)
        self.assertIn("docker build", workflow)
        self.assertIn('pyinstaller==6.22.2', workflow)

    def test_source_runtime_requirements_are_explicit_and_pip_installable(self):
        requirements = ROOT / "requirements.txt"
        lines = requirements.read_text(encoding="utf-8").splitlines()
        packages = [line.strip() for line in lines
                    if line.strip() and not line.lstrip().startswith("#")]
        self.assertEqual([], packages)
        readme = (ROOT / "Readme.md").read_text(encoding="utf-8")
        self.assertIn("pip install -r requirements.txt", readme)
        self.assertIn("import tkinter; tkinter.Tcl()", readme)


class WindowSmokeTests(unittest.TestCase):
    def test_unified_window_owns_navigation_and_footer(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception as exc:
            self.skipTest(f"no usable Tk display: {exc}")
        root.withdraw()
        try:
            app = tools_launcher.App(root)
            self.assertEqual("translate", app.current)
            self.assertEqual(
                tools_launcher.translate_gui.SHORT_NAME,
                app.apps["translate"].canvas.itemcget(
                    app.apps["translate"].title_item, "text"),
            )
            self.assertEqual(
                ["translate", "voices", "cheats", "glyphs"],
                [item[0] for item in tools_launcher.NAVIGATION],
            )
            self.assertIn("v" + tools_launcher.app_meta.VERSION,
                          app.footer_link.master.winfo_children()[0].cget("text"))
            self.assertNotIn(tools_launcher.app_meta.VERSION, root.title())

            for item in app.nav_buttons.values():
                self.assertEqual(0, int(item.icon_label.grid_info()["column"]))
                self.assertEqual(1, int(item.text_label.grid_info()["column"]))
                self.assertEqual(3, int(item.icon_label.cget("width")))
            root.update_idletasks()
            self.assertEqual(1, len({item.icon_label.winfo_rootx()
                                     for item in app.nav_buttons.values()}))
            self.assertEqual(1, len({item.text_label.winfo_rootx()
                                     for item in app.nav_buttons.values()}))

            app.apps["translate"]._set_busy(True)
            self.assertTrue(all(item.disabled
                                for item in app.nav_buttons.values()))
            self.assertFalse(app.show("voices"))
            self.assertEqual("translate", app.current)

            app.apps["translate"]._set_busy(False)
            self.assertFalse(any(item.disabled
                                 for item in app.nav_buttons.values()))
            app.nav_buttons["voices"].invoke()
            self.assertEqual("voices", app.current)
            self.assertEqual(
                tools_launcher.voice_gui.SHORT_NAME,
                app.apps["voices"].canvas.itemcget(
                    app.apps["voices"].title_item, "text"),
            )
            app.show("cheats")
            self.assertEqual("cheats", app.current)
            self.assertEqual(
                tools_launcher.cheat_gui.SHORT_NAME,
                app.apps["cheats"].canvas.itemcget(
                    app.apps["cheats"].title_item, "text"),
            )
            app.show("glyphs")
            self.assertEqual("glyphs", app.current)
            self.assertEqual(
                tools_launcher.glyph_gui.SHORT_NAME,
                app.apps["glyphs"].canvas.itemcget(
                    app.apps["glyphs"].title_item, "text"),
            )
        finally:
            root.destroy()

    def test_window_constructs(self):
        try:
            import tkinter
            root = tkinter.Tk()
        except Exception as exc:
            self.skipTest(f"no usable Tk display: {exc}")
        root.withdraw()
        try:
            app = launcher.App(root)
            self.assertEqual(0, app.progress["value"])
            app._track_progress("copy: 50%")
            self.assertAlmostEqual(10, float(app.progress["value"]))
            app._track_progress("[2/10] scene 0033 ok (0.2s)")
            self.assertGreater(float(app.progress["value"]), 20)

            popdown = root.tk.call(
                "ttk::combobox::PopdownWindow", str(app.language_combo))
            listbox = f"{popdown}.f.l"
            self.assertEqual(
                launcher.DARK["surface_hi"],
                root.tk.call(listbox, "cget", "-background"))
            self.assertEqual(
                launcher.DARK["text"],
                root.tk.call(listbox, "cget", "-foreground"))
            self.assertEqual(
                launcher.DARK["accent_dim"],
                root.tk.call(listbox, "cget", "-selectbackground"))
        finally:
            root.destroy()



def _window():
    if launcher.TK_IMPORT_ERROR is not None:
        raise unittest.SkipTest(f"no Tk: {launcher.TK_IMPORT_ERROR}")
    try:
        root = launcher.Tk()
    except Exception as exc:                     # pragma: no cover - headless
        raise unittest.SkipTest(f"no display: {exc!r}")
    root.withdraw()
    return root


class PackagedReleaseChoiceTests(unittest.TestCase):
    def test_a_packaged_run_does_not_offer_to_narrow_a_build(self):
        previous = getattr(sys, "frozen", None)
        sys.frozen = True
        try:
            self.assertFalse(launcher.picks_resources())
        finally:
            if previous is None:
                del sys.frozen
            else:
                sys.frozen = previous

    def test_a_source_checkout_does(self):
        previous = getattr(sys, "frozen", None)
        if hasattr(sys, "frozen"):
            del sys.frozen
        try:
            self.assertTrue(launcher.picks_resources())
        finally:
            if previous is not None:
                sys.frozen = previous


class PackagedReleaseTests(unittest.TestCase):
    def frozen(self, frozen):
        previous = getattr(sys, "frozen", None)
        if frozen:
            sys.frozen = True
        elif hasattr(sys, "frozen"):
            del sys.frozen

        def restore():
            if previous is None:
                if hasattr(sys, "frozen"):
                    del sys.frozen
            else:
                sys.frozen = previous

        self.addCleanup(restore)

    def app(self):
        root = _window()
        self.addCleanup(root.destroy)
        return launcher.App(root)

    def test_a_source_checkout_offers_it(self):
        self.frozen(False)
        app = self.app()
        self.assertTrue(app.picks_resources)
        self.assertTrue(hasattr(app, "only_btn"))

    def test_a_packaged_release_does_not(self):
        self.frozen(True)
        app = self.app()
        self.assertFalse(app.picks_resources)
        self.assertFalse(hasattr(app, "only_btn"))

    def test_a_packaged_release_still_builds_the_whole_profile(self):
        self.frozen(True)
        self.assertIsNone(self.app().only)

    def test_a_source_checkout_offers_the_japanese_image(self):
        self.frozen(False)
        app = self.app()
        self.assertTrue(hasattr(app, "jp_entry"))
        self.assertTrue(hasattr(app, "jp_btn"))

    def test_a_packaged_release_hides_the_japanese_image(self):
        self.frozen(True)
        app = self.app()
        self.assertFalse(hasattr(app, "jp_entry"))
        self.assertFalse(hasattr(app, "jp_btn"))


class ResourcePickerTests(unittest.TestCase):
    ENTRIES = [
        {"id": "scene-49", "label": "scene-49",
         "kind": "scene", "resource": "49"},
        {"id": "scene-73", "label": "scene-73",
         "kind": "scene", "resource": "73"},
        {"id": "container-31", "label": "container-31",
         "kind": "container", "resource": "31"},
        {"id": "fontless-31", "label": "fontless-31",
         "kind": "fontless", "resource": "31"},
        {"id": "misc-1781", "label": "misc",
         "kind": "misc", "resource": "1781"},
    ]

    def picker(self, selected=None):
        root = _window()
        self.addCleanup(root.destroy)
        launcher.apply_dark_theme(root, 1.0)
        made = {}
        original = launcher.ResourcePicker.__init__

        def capture(picker, parent, entries, chosen, scale=1.0):
            made["picker"] = picker
            original(picker, parent, entries, chosen, scale)

        self.addCleanup(setattr, launcher.ResourcePicker, "__init__", original)
        launcher.ResourcePicker.__init__ = capture
        root.after(10, lambda: self.run_case(made["picker"]))
        launcher.ResourcePicker(root, self.ENTRIES, selected, 1.0)
        return made["picker"]

    def run_case(self, picker):        # overridden per test
        picker.window.destroy()

    def test_clearing_leaves_what_the_filter_hides_alone(self):
        seen = {}

        def case(picker):
            picker.filter_var.set("scene-")
            picker.window.update_idletasks()
            picker._choose_none()
            seen["after"] = set(picker._chosen)
            picker.window.destroy()

        self.run_case = case
        self.picker(selected=None)
        self.assertEqual({"container-31", "fontless-31", "misc-1781"},
                         seen["after"])

    def test_selecting_adds_the_shown_rows_to_what_is_already_chosen(self):
        seen = {}

        def case(picker):
            picker._choose_none()
            picker.filter_var.set("scene-49")
            picker.window.update_idletasks()
            picker._choose_all()
            picker.filter_var.set("misc")
            picker.window.update_idletasks()
            picker._choose_all()
            seen["after"] = set(picker._chosen)
            picker.window.destroy()

        self.run_case = case
        self.picker(selected=None)
        self.assertEqual({"scene-49", "misc-1781"}, seen["after"])

    def test_the_buttons_say_which_rows_they_act_on(self):
        seen = {}

        def case(picker):
            seen["plain"] = (picker.all_var.get(), picker.none_var.get())
            picker.filter_var.set("scene-")
            picker.window.update_idletasks()
            seen["filtered"] = (picker.all_var.get(), picker.none_var.get())
            picker.window.destroy()

        self.run_case = case
        self.picker(selected=None)
        self.assertEqual(("Select all", "Clear all"), seen["plain"])
        self.assertEqual(("Select shown", "Clear shown"), seen["filtered"])

    def test_one_click_toggles_a_row_both_ways(self):
        seen = {}

        class Click:
            def __init__(self, y):
                self.y = y

        def case(picker):
            picker.window.deiconify()
            picker.window.update()
            picker._choose_none()
            box = picker.list.bbox(0)
            picker._toggle_clicked(Click(box[1] + 1))
            seen["on"] = set(picker._chosen)
            picker._toggle_clicked(Click(box[1] + 1))
            seen["off"] = set(picker._chosen)
            seen["below"] = picker._toggle_clicked(Click(10_000))
            seen["after_below"] = set(picker._chosen)
            picker.window.destroy()

        self.run_case = case
        self.picker(selected=None)
        self.assertEqual({self.ENTRIES[0]["id"]}, seen["on"])
        self.assertEqual(set(), seen["off"])
        self.assertEqual(set(), seen["after_below"])

    def test_closing_the_window_is_not_an_answer(self):
        picker = self.picker(selected={"scene-49"})
        self.assertIs(launcher.NOTHING_PICKED, picker.result)

    def test_choosing_everything_reads_as_every_row(self):
        seen = {}

        def case(picker):
            picker._choose_all()
            picker._accept()

        self.run_case = case
        picker = self.picker(selected={"scene-49"})
        self.assertIsNone(picker.result)


class WindowTests(unittest.TestCase):

    def setUp(self):
        self.root = _window()
        self.addCleanup(self.root.destroy)
        self.app = launcher.App(self.root)

    def _label_for(self, locale):
        for pack in self.app.packs:
            if pack.locale == locale:
                return pack.label
        self.fail(f"{locale} is not installed")

    def test_the_dropdown_chooses_which_pack_is_built(self):
        """The build must use the language on screen, not the default."""
        import tempfile
        from unittest import mock

        started = []
        with tempfile.TemporaryDirectory() as folder:
            self.app.output_var.set(folder)
            self.app.pack_var.set(self._label_for("sv-SE"))
            image = Path(folder) / "disc.iso"
            with mock.patch.object(launcher.App, "_validated_usa",
                                   return_value=image), \
                    mock.patch.object(launcher, "workspace_summary",
                                      return_value=(True, "ready")), \
                    mock.patch.object(self.app.runner, "start",
                                      side_effect=lambda *a, **k:
                                          started.append((a, k))):
                self.app._start_build()

        self.assertEqual(1, len(started))
        (kind, function, usa, pack), keywords = started[0]
        self.assertEqual("build", kind)
        self.assertIs(launcher.build_iso, function)
        self.assertEqual(ROOT / "translations" / "sv-SE", pack)
        self.assertEqual("disc.sv-SE.iso", Path(keywords["output"]).name)

    def test_a_running_job_takes_every_control_away(self):
        self.assertTrue(self.app.locked)
        for widget, _idle in self.app.locked:
            self.assertNotEqual("disabled", str(widget.cget("state")))

        self.app._set_busy(True)
        for widget, _idle in self.app.locked:
            with self.subTest(widget=str(widget)):
                self.assertEqual("disabled", str(widget.cget("state")))

        self.app._set_busy(False)
        for widget, idle in self.app.locked:
            with self.subTest(widget=str(widget)):
                self.assertEqual(idle, str(widget.cget("state")))

    def test_the_language_dropdown_never_becomes_typable(self):
        """Re-enabling must restore readonly, not normal."""
        self.app._set_busy(True)
        self.app._set_busy(False)
        self.assertEqual("readonly",
                         str(self.app.language_combo.cget("state")))

    def test_the_controls_a_job_reads_are_all_locked(self):
        locked = {str(widget) for widget, _idle in self.app.locked}
        for name, widget in (("language", self.app.language_combo),
                             ("resources", self.app.only_btn),
                             ("build", self.app.build_btn),
                             ("verify", self.app.verify_chk)):
            with self.subTest(control=name):
                self.assertIn(str(widget), locked)
        self.assertEqual(10, len(self.app.locked))

    def test_verification_is_off_until_asked_for(self):
        """The slow read-back pass is opt-in, so a build finishes sooner."""
        self.assertFalse(self.app.verify_var.get())

    def test_there_is_one_action_and_it_is_the_build(self):
        """Preparing is part of building, so it is not a button any more."""
        self.assertFalse(hasattr(self.app, "prepare_btn"))
        self.assertNotIn("prepare", self.app.items)
        self.assertIn("build", self.app.items)

    def test_the_workspace_line_survives_the_button(self):
        self.app._refresh_workspace()
        self.assertTrue(self.app.workspace_var.get())
        self.assertIn(str(self.app.workspace_label.cget("style")),
                      ("Ok.TLabel", "Warn.TLabel"))

    def test_a_real_percentage_ends_the_indeterminate_sweep(self):
        """An unread disc has no step count, so the bar sweeps until it does."""
        self.app.progress.configure(mode="indeterminate")
        self.app.progress.start(12)
        self.app._track_progress("copy:  40%")
        self.assertEqual("determinate", str(self.app.progress.cget("mode")))
        self.assertAlmostEqual(8, float(self.app.progress["value"]))

    def test_the_workspace_line_updates_as_soon_as_the_disc_is_read(self):
        from unittest import mock
        self.app.workspace_var.set("Workspace not prepared")
        self.app.workspace_ready = False
        with mock.patch.object(launcher, "workspace_summary",
                               return_value=(True, "Workspace ready · 9 rows")):
            self.app._track_progress("workspace: prepared")
        self.assertTrue(self.app.workspace_ready)
        self.assertEqual("Workspace ready · 9 rows",
                         self.app.workspace_var.get())
        self.assertEqual("Ok.TLabel",
                         str(self.app.workspace_label.cget("style")))

    def test_the_runtime_announces_that_it_prepared_one(self):
        """The window can only react to a line the build actually prints."""
        import inspect
        from tools.scripts import public_build
        source = inspect.getsource(public_build.build_iso)
        self.assertIn('"workspace: prepared"', source)


if __name__ == "__main__":
    unittest.main()


class DrainBoundTests(unittest.TestCase):
    RUNNERS = ("tools.translate_gui", "tools.cheat_patcher.gui",
               "tools.voice_patcher.gui")

    def _runner(self, module_name):
        import importlib
        module = importlib.import_module(module_name)
        return module.TaskRunner

    def test_every_runner_bounds_one_callback(self):
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                self.assertGreater(self._runner(name).BATCH, 0)

    def test_a_callback_returns_while_more_is_still_arriving(self):
        """The old loop never reached this assertion; it never returned."""
        import queue as _queue
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                runner_class = self._runner(name)
                runner = runner_class.__new__(runner_class)
                runner.events = _queue.Queue()
                runner.root = None
                seen = []
                runner.on_line = seen.append
                runner.on_done = lambda *a: None
                for index in range(runner_class.BATCH * 3):
                    runner.events.put(("line", "line %d\n" % index))
                drain = getattr(runner, "_drain", None)
                if drain is None:          # this one drains inside _poll
                    continue
                drain()
                self.assertLessEqual(len(seen), runner_class.BATCH)
                self.assertFalse(runner.events.empty(),
                                 "a bounded drain leaves the rest for next time")

    def test_the_unbounded_shape_is_gone(self):
        """`while True: get_nowait()` is the shape that cannot return."""
        import inspect
        for name in self.RUNNERS:
            with self.subTest(runner=name):
                source = inspect.getsource(self._runner(name))
                self.assertNotIn("while True:", source)
                self.assertIn("range(self.BATCH)", source)
