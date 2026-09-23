#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Unified graphical launcher for the Valkyrie Profile 2 tools."""

from __future__ import annotations

import argparse
import ctypes
import io
import os
import queue
import sys
import threading
import webbrowser

import vp2_cheats
import vp2_glyphs
import vp2_translate
import vp2_voices
from tools import app_meta, translate_gui, update_check
from tools.cheat_patcher import gui as cheat_gui
from tools.glyph_patcher import gui as glyph_gui
from tools.voice_patcher import gui as voice_gui


try:
    from tkinter import Tk
    from tkinter import ttk
except ImportError as exc:  # pragma: no cover - depends on the Python build
    TK_IMPORT_ERROR = exc
    Tk = ttk = None
else:
    TK_IMPORT_ERROR = None


NAVIGATION = (
    ("translate", "▤", "Translate", translate_gui.App),
    ("voices", "♫", "Voices", voice_gui.App),
    ("cheats", "⚙", "Cheats", cheat_gui.App),
    ("glyphs", "Aa", "Glyphs", glyph_gui.App),
)


class NavigationItem:
    """One sidebar row with independently aligned icon and text columns."""

    def __init__(self, parent, icon, label, command):
        self.command = command
        self.selected = False
        self.disabled = False
        self.frame = ttk.Frame(
            parent, style="Nav.TFrame", padding=(10, 10))
        self.frame.columnconfigure(1, weight=1)
        self.icon_label = ttk.Label(
            self.frame, text=icon, width=3, anchor="center",
            style="NavIcon.TLabel",
        )
        self.icon_label.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        self.text_label = ttk.Label(
            self.frame, text=label, anchor="w", style="NavText.TLabel")
        self.text_label.grid(row=0, column=1, sticky="ew")
        for widget in (self.frame, self.icon_label, self.text_label):
            widget.bind("<Button-1>", self._invoke)
            widget.configure(cursor="hand2")

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def invoke(self):
        if not self.disabled:
            self.command()

    def _invoke(self, _event=None):
        self.invoke()

    def set_selected(self, selected):
        self.selected = bool(selected)
        self._sync_style()

    def set_disabled(self, disabled):
        self.disabled = bool(disabled)
        cursor = "arrow" if self.disabled else "hand2"
        for widget in (self.frame, self.icon_label, self.text_label):
            widget.configure(cursor=cursor)
        self._sync_style()

    def _sync_style(self):
        prefix = ("Selected" if self.selected else
                  "Disabled" if self.disabled else "Nav")
        self.frame.configure(style=f"{prefix}.TFrame")
        self.icon_label.configure(style=f"{prefix}Icon.TLabel")
        self.text_label.configure(style=f"{prefix}Text.TLabel")


class App:
    """Own the window chrome and host the tool views."""

    def __init__(self, root):
        self.root = root
        self.scale = translate_gui.apply_dpi_scaling(root)
        self.fonts = translate_gui.apply_dark_theme(root, self.scale)
        self.current = None
        self.pages = {}
        self.apps = {}
        self.nav_buttons = {}
        self.busy_tools = set()

        root.title(app_meta.WINDOW_TITLE)
        width, height = int(1120 * self.scale), int(850 * self.scale)
        root.minsize(int(920 * self.scale), int(700 * self.scale))
        root.geometry(translate_gui.initial_window_geometry(
            root, width, height))
        translate_gui.apply_window_icon(root)
        self._configure_styles()
        self._build_shell()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.show("translate")
        self._check_for_updates()

    def _configure_styles(self):
        style = ttk.Style(self.root)
        ui = translate_gui.ui_font_family()
        dark = translate_gui.DARK
        style.configure("Sidebar.TFrame", background="#101218")
        style.configure(
            "Brand.TLabel", background="#101218", foreground=dark["text"],
            font=(ui, 15, "bold"),
        )
        for prefix, background, foreground, weight in (
                ("Nav", "#101218", dark["muted"], "normal"),
                ("Selected", dark["accent_dim"], dark["text"], "bold"),
                ("Disabled", "#101218", dark["border"], "normal")):
            style.configure(f"{prefix}.TFrame", background=background)
            style.configure(
                f"{prefix}Icon.TLabel", background=background,
                foreground=foreground, font=(ui, 15),
            )
            style.configure(
                f"{prefix}Text.TLabel", background=background,
                foreground=foreground, font=(ui, 11, weight),
            )
        style.configure("Footer.TFrame", background="#101218")
        style.configure(
            "Footer.TLabel", background="#101218", foreground=dark["muted"],
            font=(ui, 9),
        )
        style.configure(
            "FooterLink.TLabel", background="#101218",
            foreground=dark["accent_hi"], font=(ui, 9, "underline"),
        )

    def _build_shell(self):
        dark = translate_gui.DARK
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        body = ttk.Frame(self.root)
        body.grid(row=0, column=0, sticky="nsew")
        body.rowconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)

        sidebar = ttk.Frame(
            body, width=int(190 * self.scale), style="Sidebar.TFrame",
            padding=(10, 22),
        )
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.grid_propagate(False)
        sidebar.columnconfigure(0, weight=1)
        ttk.Label(
            sidebar, text="VP2 TOOLS", style="Brand.TLabel",
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(0, 24))

        for row, (key, icon, label, _factory) in enumerate(NAVIGATION, 1):
            button = NavigationItem(
                sidebar, icon, label,
                command=lambda selected=key: self.show(selected),
            )
            button.grid(row=row, column=0, sticky="ew", pady=2)
            self.nav_buttons[key] = button

        self.content = ttk.Frame(body, style="Card.TFrame")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        footer = ttk.Frame(
            self.root, style="Footer.TFrame", padding=(18, 8))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(1, weight=0)
        footer.columnconfigure(2, weight=1)
        ttk.Label(
            footer,
            text=f"{app_meta.PROJECT_NAME}  ·  v{app_meta.VERSION}",
            style="Footer.TLabel",
        ).grid(row=0, column=0, sticky="w")
        self.update_link = ttk.Label(
            footer, text="", style="FooterLink.TLabel", cursor="hand2")
        self.update_link.grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.update_link.grid_remove()
        link = ttk.Label(
            footer, text="GitHub", style="FooterLink.TLabel", cursor="hand2")
        link.grid(row=0, column=3, sticky="e")
        link.bind("<Button-1>", self._open_project)
        self.footer_link = link
        self.root.configure(background=dark["bg"])

    def _open_project(self, _event=None):
        webbrowser.open(app_meta.PROJECT_URL)

    def _open_release(self, url):
        webbrowser.open(url)

    def _check_for_updates(self):
        """Ask GitHub once for a newer release; surface a link if found."""
        if not update_check.is_release_build():
            return
        self._update_queue = queue.Queue()
        self._update_polls = 0
        thread = threading.Thread(
            target=update_check.worker, args=(self._update_queue,), daemon=True)
        thread.start()
        self.root.after(2000, self._poll_update)

    def _poll_update(self):
        try:
            release = self._update_queue.get_nowait()
        except queue.Empty:
            self._update_polls += 1
            if self._update_polls < 60:
                self.root.after(2000, self._poll_update)
            return
        if release is None:
            return
        self.update_link.configure(text="Update v%s" % release.version)
        self.update_link.bind(
            "<Button-1>", lambda _e, url=release.html_url: self._open_release(url))
        self.update_link.grid()

    def show(self, key):
        if self.busy_tools and key != self.current:
            return False
        if key == self.current:
            return True
        if self.current is not None:
            self.pages[self.current].grid_remove()
            self.nav_buttons[self.current].set_selected(False)
        if key not in self.pages:
            factory = next(item[3] for item in NAVIGATION if item[0] == key)
            page = ttk.Frame(self.content, style="Card.TFrame")
            page.grid(row=0, column=0, sticky="nsew")
            page.rowconfigure(0, weight=1)
            page.columnconfigure(0, weight=1)
            self.pages[key] = page
            self.apps[key] = factory(
                self.root, parent=page,
                on_busy_change=lambda busy, tool=key:
                    self._tool_busy_changed(tool, busy),
            )
        else:
            self.pages[key].grid()
        self.nav_buttons[key].set_selected(True)
        self.current = key
        return True

    def _tool_busy_changed(self, key, busy):
        if busy:
            self.busy_tools.add(key)
        else:
            self.busy_tools.discard(key)
        locked = bool(self.busy_tools)
        for item in self.nav_buttons.values():
            item.set_disabled(locked)

    def _on_close(self):
        for app in self.apps.values():
            if not app.request_close():
                return
        self.root.destroy()


def _wrap_inherited_handle(std_id, name):
    import msvcrt

    kernel32 = ctypes.windll.kernel32
    kernel32.GetStdHandle.restype = ctypes.c_void_p
    handle = kernel32.GetStdHandle(std_id)
    if not handle or handle == ctypes.c_void_p(-1).value:
        return False
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
        stream = os.fdopen(
            descriptor, "w", encoding="utf-8", buffering=1, closefd=False)
    except (OSError, ValueError):
        return False
    setattr(sys, name, stream)
    return True


def attach_console_for_output():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    got_output = _wrap_inherited_handle(-11, "stdout")
    got_error = _wrap_inherited_handle(-12, "stderr")
    if got_output and got_error:
        return
    if not ctypes.windll.kernel32.AttachConsole(-1):
        return
    for name, attached in (("stdout", got_output), ("stderr", got_error)):
        if not attached:
            try:
                setattr(sys, name, open(
                    "CONOUT$", "w", encoding="utf-8", buffering=1))
            except OSError:
                pass


def self_check(stream=None):
    """Exercise every payload plus the shared window runtime."""
    from tools.scripts.public_release import self_check as translation_check

    output = stream or sys.stdout
    checks = (
        ("translation", translation_check),
        ("cheats", vp2_cheats.self_check),
        ("voices", vp2_voices.self_check),
        ("glyphs", vp2_glyphs.self_check),
    )
    failed = False
    for label, check in checks:
        captured = io.StringIO()
        status = check(captured)
        print(f"[{label}]", file=output)
        print(captured.getvalue().rstrip(), file=output)
        failed = failed or bool(status)
    for label, module in (
            ("translate", translate_gui), ("cheats", cheat_gui),
            ("voices", voice_gui), ("glyphs", glyph_gui)):
        if module.TK_IMPORT_ERROR is not None:
            print(f"FAIL  {label} window: {module.TK_IMPORT_ERROR}", file=output)
            failed = True
    if failed:
        print("\nunified self-check failed", file=output)
        return 1
    print("\nunified self-check ok", file=output)
    return 0


def run_gui():
    if TK_IMPORT_ERROR is not None:
        print(f"this build has no Tk: {TK_IMPORT_ERROR}", file=sys.stderr)
        return 3
    translate_gui.enable_dpi_awareness()
    root = Tk()
    root.withdraw()
    App(root)
    translate_gui.use_dark_titlebar(root)
    root.deiconify()
    root.mainloop()
    return 0


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--self-check", action="store_true",
        help="verify the packaged application and exit",
    )
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["_runtime-build"]:
        attach_console_for_output()
        return vp2_translate.run_internal_build(argv[1:])
    args = _parser().parse_args(argv)
    if args.self_check:
        attach_console_for_output()
        return self_check()
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
