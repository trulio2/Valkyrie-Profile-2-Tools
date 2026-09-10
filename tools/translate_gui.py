#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only

"""Tk interface for the VP2 translation builder."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import sys
import threading
import time
import tomllib
import traceback
from pathlib import Path
from typing import NamedTuple

from tools.scripts.public_build import build_iso, terminate_active_builds
from tools.scripts.paths import PROJECT_ROOT, WORKSPACE_DIR
from tools.scripts.translation_pack import is_language_pack
from tools.app_meta import VERSION as __version__

try:
    from tkinter import (
        BooleanVar, Canvas, DISABLED, END, NORMAL, PhotoImage, StringVar,
        Text, Tk, filedialog, messagebox,
    )
    from tkinter import font as tkfont
    from tkinter import ttk
except ImportError as exc:  # pragma: no cover - depends on the Python build
    TK_IMPORT_ERROR = exc
    BooleanVar = Canvas = PhotoImage = StringVar = Text = Tk = None
    filedialog = messagebox = tkfont = ttk = None
    DISABLED = END = NORMAL = None
else:
    TK_IMPORT_ERROR = None


APP_NAME = "Valkyrie Profile 2 Translation Builder"
SHORT_NAME = "VP2 Translation Builder"
DEFAULT_WORKSPACE = str(WORKSPACE_DIR)
ICON_ICO = "images/vp2_release.ico"
ICON_PNG = "images/vp2_release.png"
BACKDROP_PNG = "images/vp2_release_bg.png"
UNUSED_TCL_TREES = ("_tcl_data/tzdata", "_tcl_data/msgs", "_tk_data/msgs")

STEP_LINE = re.compile(r"^\[(\d+)/(\d+)\]\s+(\S+)\s+(\S+)")
COPY_LINE = re.compile(r"^copy:\s+(\d+)%")

DARK = {
    "bg": "#14161b",
    "surface": "#1b1e26",
    "surface_hi": "#232733",
    "border": "#2f3542",
    "text": "#e4e8f1",
    "muted": "#98a2b6",
    "accent": "#7aa2f7",
    "accent_hi": "#96b6ff",
    "accent_dim": "#3c5488",
    "ok": "#9ece6a",
    "warn": "#e0af68",
    "error": "#f7768e",
}


class LanguagePack(NamedTuple):
    label: str
    locale: str
    path: Path


def bundle_root() -> Path:
    return PROJECT_ROOT


def asset_path(name: str) -> Path | None:
    path = bundle_root() / name
    return path if path.is_file() else None


def real_exe_dir() -> Path:
    """Directory containing the executable the user launched."""
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        buffer = ctypes.create_unicode_buffer(32768)
        ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, len(buffer))
        return Path(buffer.value).parent
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT / "build"


def output_path_for(source: Path, pack: LanguagePack, directory: Path) -> Path:
    return directory / f"{source.stem}.{pack.locale}.iso"


def language_packs(root: Path = PROJECT_ROOT) -> list[LanguagePack]:
    packs = []
    directory = root / "translations"
    for path in sorted(directory.iterdir() if directory.is_dir() else ()):
        if not is_language_pack(path):
            continue
        metadata = path / "pack.toml"
        try:
            values = tomllib.loads(metadata.read_text(encoding="utf-8"))
            locale = str(values["locale"])
            name = str(values.get("name") or locale)
        except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError):
            continue
        packs.append(LanguagePack(f"{name}  ·  {locale}", locale, path))
    return packs


def workspace_summary(workspace: Path = WORKSPACE_DIR) -> tuple[bool, str]:
    stamp = workspace / "internal" / "generation.json"
    try:
        details = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, ("Workspace not prepared · the first build reads "
                       "your disc, which takes a few minutes")
    rows = details.get("reference_rows")
    if not isinstance(rows, int):
        rows = sum(int(details.get(name, 0)) for name in
                   ("scene_lines", "container_lines", "chapter_lines"))
    japanese = "English + Japanese" if details.get("japanese") else "English"
    return True, f"Workspace ready · {rows:,} reference rows · {japanese}"


def describe_disc(path: Path, expected_region: str) -> tuple[str, str]:
    if not path.is_file():
        return "error", "File does not exist."
    try:
        from tools.scripts.disc_identity import identify
        boot, region = identify(path)
    except Exception as exc:
        return "error", str(exc)
    if region == expected_region:
        role = "USA source" if region == "usa" else "Japanese reference"
        return "ok", f"{role} recognised ({boot})."
    expected = "USA" if expected_region == "usa" else "Japanese"
    return "error", f"This is {boot} ({region}), not the {expected} image."


class _QueueStream:
    def __init__(self, target: queue.Queue):
        self.target = target

    def write(self, value):
        if value:
            self.target.put(("line", value))
        return len(value) if value else 0

    def flush(self):
        pass


class TaskRunner:
    """Run disc work off the Tk thread and stream its output back."""

    #: Lines handled per callback before the window is given back.
    BATCH = 200

    def __init__(self, root, on_line, on_done):
        self.root = root
        self.on_line = on_line
        self.on_done = on_done
        self.events = queue.Queue()
        self.thread = None
        self.after_id = None

    @property
    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, kind, function, *args, **kwargs):
        if self.busy:
            return False
        self.thread = threading.Thread(
            target=self._run, args=(kind, function, args, kwargs), daemon=True)
        self.thread.start()
        self._poll()
        return True

    def _run(self, kind, function, args, kwargs):
        saved = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = _QueueStream(self.events)
        try:
            result = function(*args, **kwargs)
        except BaseException as exc:
            self.events.put(("line", traceback.format_exc()))
            self.events.put(("done", kind, None, exc))
        else:
            self.events.put(("done", kind, result, None))
        finally:
            sys.stdout, sys.stderr = saved

    def _poll(self):
        backlog = self._drain()
        if self.busy or not self.events.empty():
            self.after_id = self.root.after(1 if backlog else 60, self._poll)
        else:
            self.after_id = None

    def _drain(self):
        for _ in range(self.BATCH):
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                return False
            if item[0] == "line":
                self.on_line(item[1])
            else:
                self.on_done(*item[1:])
        return True


def ui_font_family():
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "SF Pro Text"
    return "DejaVu Sans"


def mono_font_family():
    if sys.platform == "win32":
        return "Consolas"
    if sys.platform == "darwin":
        return "Menlo"
    return "DejaVu Sans Mono"


def enable_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def apply_dpi_scaling(root) -> float:
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        dpi = 96.0
    if dpi <= 0:
        dpi = 96.0
    root.tk.call("tk", "scaling", dpi / 72.0)
    return dpi / 96.0


def initial_window_geometry(root, width, height) -> str:
    width, height = max(1, int(width)), max(1, int(height))
    try:
        screen_width = max(1, int(root.winfo_screenwidth()))
        screen_height = max(1, int(root.winfo_screenheight()))
    except Exception:
        screen_width, screen_height = width, height
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return f"{width}x{height}+{x}+{y}"


def apply_dark_theme(root, scale=1.0):
    style = ttk.Style(root)
    style.theme_use("clam")
    ui = ui_font_family()
    fonts = {
        "title": (ui, 17, "bold"), "body": (ui, 11),
        "small": (ui, 10), "button": (ui, 11, "bold"),
        "mono": (mono_font_family(), 10),
    }
    for option, value in {
        "background": DARK["surface_hi"],
        "foreground": DARK["text"],
        "selectBackground": DARK["accent_dim"],
        "selectForeground": DARK["text"],
        "borderWidth": 0,
        "relief": "flat",
        "font": fonts["body"],
    }.items():
        root.option_add(f"*TCombobox*Listbox.{option}", value)
    root.configure(bg=DARK["bg"])
    style.configure(".", background=DARK["bg"], foreground=DARK["text"],
                    fieldbackground=DARK["surface"], font=fonts["body"],
                    borderwidth=0, focuscolor=DARK["accent_dim"])
    style.configure("Card.TFrame", background=DARK["surface"])
    style.configure("Card.TLabel", background=DARK["surface"],
                    foreground=DARK["text"])
    style.configure("CardMuted.TLabel", background=DARK["surface"],
                    foreground=DARK["muted"], font=fonts["small"])
    for name, colour in (("Ok", DARK["ok"]), ("Warn", DARK["warn"]),
                         ("Error", DARK["error"])):
        style.configure(f"{name}.TLabel", background=DARK["surface"],
                        foreground=colour, font=fonts["small"])
    style.configure("TEntry", fieldbackground=DARK["surface_hi"],
                    foreground=DARK["text"], insertcolor=DARK["text"],
                    bordercolor=DARK["border"], padding=6)
    style.map("TEntry", bordercolor=[("focus", DARK["accent"])])
    style.configure("TCombobox", fieldbackground=DARK["surface_hi"],
                    background=DARK["surface_hi"], foreground=DARK["text"],
                    arrowcolor=DARK["muted"], padding=5)
    style.map("TCombobox", fieldbackground=[("readonly", DARK["surface_hi"])],
              selectbackground=[("readonly", DARK["surface_hi"])],
              selectforeground=[("readonly", DARK["text"])])
    style.configure("TButton", background=DARK["surface_hi"],
                    foreground=DARK["text"], padding=(14, 7))
    style.map("TButton", background=[("pressed", DARK["border"]),
                                      ("active", DARK["border"]),
                                      ("disabled", DARK["surface"])],
              foreground=[("disabled", DARK["muted"])])
    style.configure("Accent.TButton", background=DARK["accent"],
                    foreground="#0d1017", font=fonts["button"],
                    padding=(22, 9))
    style.map("Accent.TButton", background=[("pressed", DARK["accent_dim"]),
                                             ("active", DARK["accent_hi"]),
                                             ("disabled", DARK["surface_hi"])],
              foreground=[("disabled", DARK["muted"])])
    indicator = max(14, int(13 * scale))
    style.configure("Chip.TCheckbutton", background=DARK["surface"],
                    foreground=DARK["muted"], font=fonts["small"],
                    indicatorbackground=DARK["surface_hi"],
                    indicatorforeground=DARK["bg"], indicatorsize=indicator,
                    padding=(int(11 * scale), int(8 * scale)))
    style.map("Chip.TCheckbutton",
              background=[("active", DARK["surface_hi"]),
                          ("selected", DARK["surface"])],
              indicatorbackground=[("selected", DARK["accent"]),
                                   ("active", DARK["border"])],
              foreground=[("active", DARK["text"]),
                          ("disabled", DARK["muted"])])
    style.configure("Horizontal.TProgressbar", background=DARK["accent"],
                    troughcolor=DARK["surface_hi"], borderwidth=0,
                    thickness=max(8, int(8 * scale)))
    style.configure("Vertical.TScrollbar", background=DARK["surface_hi"],
                    troughcolor=DARK["bg"], borderwidth=0,
                    arrowcolor=DARK["muted"])
    return fonts


def apply_window_icon(root):
    ico = asset_path(ICON_ICO)
    if ico and sys.platform == "win32":
        try:
            root.iconbitmap(default=str(ico))
            return
        except Exception:
            pass
    png = asset_path(ICON_PNG)
    if png:
        try:
            root._vp2_icon = PhotoImage(file=str(png))
            root.iconphoto(True, root._vp2_icon)
        except Exception:
            pass


def load_backdrop():
    path = asset_path(BACKDROP_PNG)
    if not path:
        return None
    try:
        return PhotoImage(file=str(path))
    except Exception:
        return None


def use_dark_titlebar(root):
    if sys.platform != "win32":
        return
    try:
        root.update_idletasks()
        handle = ctypes.windll.user32.GetParent(root.winfo_id()) or root.winfo_id()
        enabled = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    handle, attribute, ctypes.byref(enabled),
                    ctypes.sizeof(enabled)) == 0:
                break
        ctypes.windll.user32.SetWindowPos(handle, 0, 0, 0, 0, 0, 0x0027)
    except Exception:
        pass


class App:
    def __init__(self, root, parent=None, on_busy_change=None):
        self.root = root
        self.host = parent or root
        self.embedded = parent is not None
        self.on_busy_change = on_busy_change or (lambda _busy: None)
        self.packs = language_packs()
        if not self.packs:
            raise RuntimeError("no language packs are installed")
        self.pack_by_label = {pack.label: pack for pack in self.packs}
        self.usa_var = StringVar()
        self.jp_var = StringVar()
        self.pack_var = StringVar(value=self.packs[0].label)
        self.output_var = StringVar(value=str(real_exe_dir()))
        self.verify_var = BooleanVar(value=False)
        self.log_shown = BooleanVar(value=False)
        ready, note = workspace_summary()
        self.workspace_ready = ready
        self.workspace_var = StringVar(value=note)
        self.status_var = StringVar(value="Choose the USA disc image to build.")
        self.detail_var = StringVar()
        self.started_at = None
        self.last_output = None

        self.locked = []

        self.scale = apply_dpi_scaling(root)
        self.compact_height = int(570 * self.scale)
        self.expanded_height = int(790 * self.scale)
        width = int(900 * self.scale)
        if not self.embedded:
            root.title(SHORT_NAME)
            root.minsize(int(760 * self.scale), self.compact_height)
            root.geometry(initial_window_geometry(
                root, width, self.compact_height))
        self.fonts = apply_dark_theme(root, self.scale)
        self.line_heights = {
            key: tkfont.Font(root=root, font=self.fonts[key]).metrics("linespace")
            for key in ("body", "small")}
        if not self.embedded:
            apply_window_icon(root)
        self._build_ui()
        self.runner = TaskRunner(root, self._on_line, self._on_done)
        if not self.embedded:
            root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _px(self, value):
        return int(value * self.scale)

    def _build_ui(self):
        self.canvas = Canvas(self.host, highlightthickness=0, bd=0,
                             background=DARK["bg"], takefocus=0)
        self.canvas.pack(fill="both", expand=True)
        self.backdrop = load_backdrop()
        self.backdrop_item = (self.canvas.create_image(
            0, 0, anchor="se", image=self.backdrop)
            if self.backdrop is not None else None)
        self.title_item = self.canvas.create_text(
            0, 0, anchor="nw", text=SHORT_NAME,
            fill=DARK["text"], font=self.fonts["title"])
        self.subtitle_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"],
            text="Translation patch builder — prepare source-free build data "
                 "from your disc, then create a translated copy.")

        self.discs_card = self._build_discs_card()
        self.build_card = self._build_settings_card()
        self.build_btn = self._lockable(ttk.Button(
            self.canvas, text="Build translated ISO", style="Accent.TButton",
            command=self._start_build))
        self.verify_chk = self._lockable(ttk.Checkbutton(
            self.canvas, text="Thorough verification",
            style="Chip.TCheckbutton", variable=self.verify_var))
        self.log_btn = ttk.Button(
            self.canvas, text="Show details", command=self._toggle_log)
        self.progress = ttk.Progressbar(
            self.canvas, mode="determinate", maximum=100)
        self.status_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["text"], font=self.fonts["body"],
            text=self.status_var.get())
        self.detail_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"],
            text=self.detail_var.get())
        self.status_var.trace_add("write", self._sync_status)
        self.detail_var.trace_add("write", self._sync_status)

        self.log_frame = ttk.Frame(self.canvas, style="Card.TFrame")
        self.log = Text(
            self.log_frame, wrap="none", height=10, font=self.fonts["mono"],
            relief="flat", background=DARK["surface"], foreground=DARK["muted"],
            insertbackground=DARK["text"], selectbackground=DARK["accent_dim"],
            padx=10, pady=8, borderwidth=0)
        scroll = ttk.Scrollbar(
            self.log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set, state=DISABLED)
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)

        widgets = (
            ("discs", self.discs_card), ("settings", self.build_card),
            ("build", self.build_btn),
            ("verify", self.verify_chk), ("log_btn", self.log_btn),
            ("progress", self.progress), ("log", self.log_frame),
        )
        self.items = {name: self.canvas.create_window(
            0, 0, anchor="nw", window=widget) for name, widget in widgets}
        self.canvas.itemconfigure(self.items["log"], state="hidden")
        self.canvas.bind("<Configure>", self._reflow)
        self._append_log(f"{APP_NAME} {__version__}\n"
                         f"workspace: {WORKSPACE_DIR}\n")

    def _card(self, title):
        frame = ttk.Frame(self.canvas, style="Card.TFrame", padding=(14, 12))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=title, style="CardMuted.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        return frame

    def _build_discs_card(self):
        card = self._card("DISC IMAGES")
        ttk.Label(card, text="USA", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10))
        self._lockable(ttk.Entry(card, textvariable=self.usa_var)).grid(
            row=1, column=1, sticky="ew", padx=(0, 10))
        self._lockable(ttk.Button(
            card, text="Browse…",
            command=lambda: self._pick_disc("usa"))).grid(row=1, column=2,
                                                          sticky="e")
        ttk.Label(card, text="Required · clean USA image used for every build",
                  style="CardMuted.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(5, 10))
        ttk.Label(card, text="Japanese", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10))
        self._lockable(ttk.Entry(card, textvariable=self.jp_var)).grid(
            row=3, column=1, sticky="ew", padx=(0, 10))
        self._lockable(ttk.Button(
            card, text="Browse…",
            command=lambda: self._pick_disc("japan"))).grid(row=3, column=2,
                                                            sticky="e")
        ttk.Label(card, text="Optional · adds the original script to reference tables",
                  style="CardMuted.TLabel").grid(
            row=4, column=1, columnspan=2, sticky="w", pady=(5, 0))
        return card

    def _build_settings_card(self):
        card = self._card("BUILD SETTINGS")
        ttk.Label(card, text="Language", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10))
        self.language_combo = self._lockable(ttk.Combobox(
            card, textvariable=self.pack_var,
            values=[pack.label for pack in self.packs], state="readonly"))
        self.language_combo.grid(
            row=1, column=1, columnspan=2, sticky="ew")
        ttk.Label(card, text="Output", style="Card.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 10), pady=(10, 0))
        self._lockable(ttk.Entry(card, textvariable=self.output_var)).grid(
            row=2, column=1, sticky="ew", padx=(0, 10), pady=(10, 0))
        self._lockable(ttk.Button(
            card, text="Change…", command=self._pick_output)).grid(
            row=2, column=2, sticky="e", pady=(10, 0))
        self.workspace_label = ttk.Label(
            card, textvariable=self.workspace_var,
            style="Ok.TLabel" if self.workspace_ready else "Warn.TLabel")
        self.workspace_label.grid(row=3, column=0, columnspan=3, sticky="w",
                                  pady=(10, 0))
        return card

    def _bottom(self, item, fallback):
        box = self.canvas.bbox(item)
        return box[3] if box else fallback

    def _reflow(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        pad, gap = self._px(22), self._px(14)
        inner = max(self._px(300), width - 2 * pad)
        if self.backdrop_item is not None:
            self.canvas.coords(self.backdrop_item, width, height)
        y = self._px(18)
        self.canvas.coords(self.title_item, pad, y)
        y = self._bottom(self.title_item, y) + self._px(2)
        self.canvas.itemconfigure(self.subtitle_item, width=inner)
        self.canvas.coords(self.subtitle_item, pad, y)
        y = self._bottom(self.subtitle_item, y) + gap
        for name, card in (("discs", self.discs_card),
                           ("settings", self.build_card)):
            self.canvas.coords(self.items[name], pad, y)
            self.canvas.itemconfigure(self.items[name], width=inner)
            y += card.winfo_reqheight() + gap
        row = (self.build_btn, self.verify_chk, self.log_btn)
        row_height = max(widget.winfo_reqheight() for widget in row)
        positions = (
            ("build", self.build_btn, pad),
            ("verify", self.verify_chk,
             pad + self.build_btn.winfo_reqwidth() + self._px(10)),
            ("log_btn", self.log_btn,
             width - pad - self.log_btn.winfo_reqwidth()),
        )
        for name, widget, x in positions:
            self.canvas.coords(self.items[name], x,
                               y + (row_height - widget.winfo_reqheight()) // 2)
        y += row_height + gap
        self.canvas.coords(self.items["progress"], pad, y)
        self.canvas.itemconfigure(self.items["progress"], width=inner)
        y += self.progress.winfo_reqheight() + self._px(8)
        for item, font in ((self.status_item, "body"),
                           (self.detail_item, "small")):
            self.canvas.itemconfigure(item, width=inner)
            self.canvas.coords(item, pad, y)
            y += max(self.line_heights[font], self._bottom(item, y) - y) + 2
        bottom = self._px(16)
        if self.log_shown.get():
            top = y + gap
            self.canvas.coords(self.items["log"], pad, top)
            self.canvas.itemconfigure(
                self.items["log"], width=inner,
                height=max(self._px(80), height - top - bottom))
        else:
            needed = y + bottom
            if needed > self.compact_height:
                self.compact_height = needed
                if not self.embedded:
                    self.root.minsize(self._px(760), needed)
                    if height < needed:
                        self.root.geometry(f"{width}x{needed}")

    def _sync_status(self, *_args):
        self.canvas.itemconfigure(self.status_item, text=self.status_var.get())
        self.canvas.itemconfigure(self.detail_item, text=self.detail_var.get())

    def _pick_disc(self, region):
        title = ("Select the USA Valkyrie Profile 2 ISO" if region == "usa"
                 else "Select the Japanese Valkyrie Profile 2 ISO")
        path = filedialog.askopenfilename(
            title=title, filetypes=[("Disc images", "*.iso"),
                                   ("All files", "*.*")])
        if path:
            (self.usa_var if region == "usa" else self.jp_var).set(path)
            level, note = describe_disc(Path(path), region)
            self.status_var.set(note)
            self.detail_var.set(f"{Path(path).stat().st_size / (1 << 30):.2f} GB")
            if level == "error":
                messagebox.showerror("Unexpected disc image", note)

    def _pick_output(self):
        path = filedialog.askdirectory(title="Where should the ISO be written?")
        if path:
            self.output_var.set(path)

    def _validated_usa(self):
        raw = self.usa_var.get().strip()
        if not raw:
            messagebox.showinfo("Pick an ISO", "Choose the USA ISO first.")
            return None
        path = Path(raw)
        level, note = describe_disc(path, "usa")
        if level != "ok":
            messagebox.showerror("Unusable image", note)
            return None
        return path

    def _images(self, usa):
        """The discs to read, if this build has to read them."""
        images = [usa]
        jp = self.jp_var.get().strip()
        if jp:
            japanese = Path(jp)
            level, note = describe_disc(japanese, "japan")
            if level != "ok":
                messagebox.showerror("Unusable image", note)
                return None
            images.append(japanese)
        return images

    def _start_build(self):
        if self.runner.busy:
            return
        usa = self._validated_usa()
        if usa is None:
            return
        images = self._images(usa)
        if images is None:
            return
        pack = self.pack_by_label[self.pack_var.get()]
        folder = Path(self.output_var.get().strip() or real_exe_dir())
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", str(exc))
            return
        output = output_path_for(usa, pack, folder)
        if output.exists() and not messagebox.askyesno(
                "Overwrite?", f"Output already exists:\n{output}\n\nOverwrite?"):
            return
        self.last_output = output
        self.started_at = time.time()
        self._set_busy(True)
        ready, _note = workspace_summary()
        if ready:
            self.progress.stop()
            self.progress.configure(mode="determinate", value=0)
            self.status_var.set("Preparing the translation build…")
        else:
            self.progress.configure(mode="indeterminate", value=0)
            self.progress.start(12)
            self.status_var.set("Reading your disc for the first time…")
        self.detail_var.set(pack.label)
        self._append_log(f"\n=== build: {usa.name} -> {output.name} ===\n")
        self.runner.start("build", build_iso, usa, pack.path,
                          workspace=DEFAULT_WORKSPACE, output=output,
                          no_verify=not self.verify_var.get(), images=images)

    def _lockable(self, widget):
        """Register a control that a running job takes away."""
        self.locked.append((widget, str(widget.cget("state")) or NORMAL))
        return widget

    def _set_busy(self, busy):
        """Nothing a job read at its start may be changed while it runs."""
        for widget, idle in self.locked:
            widget.configure(state=DISABLED if busy else idle)
        self.on_busy_change(bool(busy))

    def _on_line(self, text):
        self._append_log(text)
        for line in text.splitlines():
            self._track_progress(line.strip())

    def _measured(self, value):
        """First real percentage ends the sweep the unread disc started."""
        if str(self.progress.cget("mode")) != "determinate":
            self.progress.stop()
            self.progress.configure(mode="determinate")
        self.progress.configure(value=value)

    def _track_progress(self, line):
        if line.startswith("workspace: prepared"):
            self._refresh_workspace()
            return
        phases = {
            "inventory:": "Scanning disc inventory…",
            "glyphs:": "Indexing font glyphs…",
            "tables:": "Exporting text records…",
            "reference:": "Arranging translator reference tables…",
        }
        for prefix, status in phases.items():
            if line.startswith(prefix):
                self.status_var.set(status)
                self.detail_var.set(line)
                return
        copied = COPY_LINE.match(line)
        if copied:
            percent = int(copied.group(1))
            self._measured(percent * 0.2)
            self.status_var.set(f"Copying the source image… {percent}%")
            return
        step = STEP_LINE.match(line)
        if step:
            index, total = int(step.group(1)), int(step.group(2))
            self._measured(20 + index * 80 / total)
            self.status_var.set(f"Patching resource {index} of {total}")
            self.detail_var.set(
                f"{step.group(3)} {step.group(4)} · {self._elapsed()} elapsed")
        elif line.startswith("writing to"):
            self.status_var.set("Preparing build data…")
        elif line.startswith("wrote output"):
            self.status_var.set("Finishing…")

    def _elapsed(self):
        seconds = int(time.time() - (self.started_at or time.time()))
        return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"

    def _refresh_workspace(self):
        """A build may have generated it, so the line has to be re-read."""
        self.workspace_ready, note = workspace_summary()
        self.workspace_var.set(note)
        self.workspace_label.configure(
            style="Ok.TLabel" if self.workspace_ready else "Warn.TLabel")

    def _on_done(self, kind, result, error):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_busy(False)
        if error is not None:
            self.progress.configure(value=0)
            self.status_var.set(f"{kind.capitalize()} failed.")
            self.detail_var.set(str(error))
            if not self.log_shown.get():
                self._toggle_log()
            messagebox.showerror(f"{kind.capitalize()} failed", str(error))
            return
        self._refresh_workspace()
        if kind == "generate":
            self.progress.configure(value=100)
            self.status_var.set(f"Workspace ready in {self._elapsed()}.")
            self.detail_var.set(self.workspace_var.get())
            self._append_log("=== workspace ready ===\n")
        else:
            self.progress.configure(value=100)
            self.status_var.set(f"Build complete in {self._elapsed()}.")
            self.detail_var.set(str(result))
            self._append_log("=== build complete ===\n")
            if messagebox.askyesno(
                    "Build complete",
                    f"Translated ISO written to:\n{result}\n\nOpen the output folder?"):
                self._open_output_folder(Path(result).parent)

    def _append_log(self, text):
        self.log.configure(state=NORMAL)
        self.log.insert(END, text)
        self.log.see(END)
        self.log.configure(state=DISABLED)

    def _toggle_log(self):
        showing = not self.log_shown.get()
        self.log_shown.set(showing)
        self.canvas.itemconfigure(self.items["log"],
                                  state="normal" if showing else "hidden")
        self.log_btn.configure(text="Hide details" if showing else "Show details")
        if not self.embedded:
            width = self.root.winfo_width() or self._px(900)
            if showing and self.root.winfo_height() < self.expanded_height:
                self.root.geometry(f"{width}x{self.expanded_height}")
            elif not showing:
                self.root.geometry(f"{width}x{self.compact_height}")
        self._reflow()

    def request_close(self):
        """Closing the window has to stop the work, not just hide it."""
        if self.runner.busy:
            if not messagebox.askyesno(
                    "Still working",
                    "A job is still running. Closing now stops it, and the "
                    "unfinished file it was writing stays on disk.\n\n"
                    "Close anyway?"):
                return False
            terminate_active_builds()
        return True

    def _on_close(self):
        if not self.request_close():
            return
        self.root.destroy()

    def _open_output_folder(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["open", str(path)])
            else:
                __import__("subprocess").Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc))


