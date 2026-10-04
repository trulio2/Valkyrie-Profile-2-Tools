# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The cheat patcher's window."""

from __future__ import annotations

import ctypes
import os
import queue
import re
import sys
import threading
import time
import traceback
from pathlib import Path

from .build import PATCHERS, build_iso, default_output_path
from .catalog import ANTI_CHEAT, CHEATS, required_with
from ..app_meta import VERSION as __version__
from ..scripts import disc_identity

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


APP_NAME = "Valkyrie Profile 2 Cheat Patcher"
SHORT_NAME = "VP2 Cheat Patcher"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_BOOT = "SLUS_214.52"
ICON_ICO = "images/vp2_release.ico"
ICON_PNG = "images/vp2_release.png"
BACKDROP_PNG = "images/vp2_release_bg.png"

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


def asset_path(name):
    path = PROJECT_ROOT / name
    return path if path.is_file() else None


def output_path_for(source, directory):
    return Path(directory) / default_output_path(source).name


def describe_disc(path):
    """Recognise the one game release whose addresses this patcher owns."""
    path = Path(path)
    if not path.is_file():
        return "error", "File does not exist."
    try:
        boot, region = disc_identity.identify(path)
    except disc_identity.DiscError as exc:
        return "error", str(exc)
    if boot == SUPPORTED_BOOT:
        return "ok", "USA source recognised (%s)." % boot
    return (
        "error",
        "This is %s (%s), not the supported USA image (%s)."
        % (boot, region, SUPPORTED_BOOT),
    )


class _QueueStream:
    def __init__(self, target):
        self.target = target

    def write(self, value):
        if value:
            self.target.put(("line", value))
        return len(value) if value else 0

    def flush(self):
        pass


class TaskRunner:
    """Run the patch off the Tk thread and stream its output back."""

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
        # Bounded on purpose: an unbounded drain never returns while a
        # tool produces faster than the window consumes.
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
    """Centred, and never taller than the screen it opens on."""
    width, height = max(1, int(width)), max(1, int(height))
    try:
        screen_width = max(1, int(root.winfo_screenwidth()))
        screen_height = max(1, int(root.winfo_screenheight()))
    except Exception:
        screen_width, screen_height = width, height
    width = min(width, screen_width)
    height = min(height, max(1, screen_height - 80))
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
    # The cheat list sits on a card and needs the full-strength label colour;
    # the chip style above is deliberately muted for a lone toggle.
    style.configure("Cheat.TCheckbutton", background=DARK["surface"],
                    foreground=DARK["text"], font=fonts["body"],
                    indicatorbackground=DARK["surface_hi"],
                    indicatorforeground=DARK["bg"], indicatorsize=indicator,
                    padding=(0, int(4 * scale)))
    style.map("Cheat.TCheckbutton",
              background=[("active", DARK["surface"]),
                          ("selected", DARK["surface"])],
              indicatorbackground=[("selected", DARK["accent"]),
                                   ("active", DARK["border"])],
              foreground=[("disabled", DARK["muted"])])
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
        self.source_var = StringVar()
        self.output_var = StringVar(value=str(PROJECT_ROOT / "build"))
        self.log_shown = BooleanVar(value=False)
        self.cheat_vars = {
            cheat.name: BooleanVar(value=cheat.name == ANTI_CHEAT)
                           for cheat in CHEATS}
        self.corrupt_save_var = BooleanVar(value=False)
        self.cheat_boxes = {}
        self.status_var = StringVar(
            value="Choose a clean USA disc image, then pick your cheats.")
        self.detail_var = StringVar()
        self.started_at = None
        self.locked = []

        self.scale = apply_dpi_scaling(root)
        self.compact_height = int(684 * self.scale)
        self.expanded_height = int(904 * self.scale)
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
        for name in self.cheat_vars:
            self.cheat_vars[name].trace_add("write", self._sync_dependency)
        self._sync_dependency()

    def _px(self, value):
        return int(value * self.scale)

    def _lockable(self, widget):
        self.locked.append((widget, str(widget.cget("state")) or NORMAL))
        return widget

    def _card(self, title):
        frame = ttk.Frame(self.canvas, style="Card.TFrame", padding=(14, 12))
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=title, style="CardMuted.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 8))
        return frame

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
            text="Cheat patcher — write chosen cheats into a copy of "
                 "your disc, so they need no emulator to run.")

        self.disc_card = self._build_disc_card()
        self.cheat_card = self._build_cheat_card()
        self.patch_btn = self._lockable(ttk.Button(
            self.canvas, text="Patch ISO", style="Accent.TButton",
            command=self._start_patch))
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
            ("disc", self.disc_card), ("cheats", self.cheat_card),
            ("patch", self.patch_btn), ("log_btn", self.log_btn),
            ("progress", self.progress), ("log", self.log_frame),
        )
        self.items = {name: self.canvas.create_window(
            0, 0, anchor="nw", window=widget) for name, widget in widgets}
        self.canvas.itemconfigure(self.items["log"], state="hidden")
        self.canvas.bind("<Configure>", self._reflow)
        self.root.bind("<MouseWheel>", self._scroll_cheats)
        self.root.bind("<Button-4>", self._scroll_cheats)
        self.root.bind("<Button-5>", self._scroll_cheats)
        self._append_log(f"{APP_NAME} {__version__}\n")

    def _scroll_cheats(self, event):
        widget = event.widget
        while widget is not None and widget is not self.root:
            if widget is self.cheat_canvas or widget is self.cheat_content:
                break
            widget = getattr(widget, "master", None)
        else:
            return
        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            units = -int(getattr(event, "delta", 0) / 120) * 3
        if units:
            self.cheat_canvas.yview_scroll(units, "units")

    def _build_disc_card(self):
        card = self._card("DISC IMAGE")
        ttk.Label(card, text="Source", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 10))
        self._lockable(ttk.Entry(card, textvariable=self.source_var)).grid(
            row=1, column=1, sticky="ew", padx=(0, 10))
        self._lockable(ttk.Button(
            card, text="Browse…", command=self._pick_source)).grid(
            row=1, column=2, sticky="e")
        ttk.Label(card, text="A clean USA image · never modified, only read",
                  style="CardMuted.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", pady=(5, 10))
        ttk.Label(card, text="Output", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 10))
        self._lockable(ttk.Entry(card, textvariable=self.output_var)).grid(
            row=3, column=1, sticky="ew", padx=(0, 10))
        self._lockable(ttk.Button(
            card, text="Change…", command=self._pick_output)).grid(
            row=3, column=2, sticky="e")
        self.output_label = ttk.Label(
            card, text="", style="CardMuted.TLabel")
        self.output_label.grid(row=4, column=1, columnspan=2, sticky="w",
                               pady=(5, 0))
        self.source_var.trace_add("write", self._sync_output_name)
        self.output_var.trace_add("write", self._sync_output_name)
        return card

    def _build_cheat_card(self):
        card = self._card("CHEATS")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        self.toggle_all_cheats_btn = self._lockable(ttk.Button(
            card, text="Disable all cheats", command=self._toggle_all_cheats
        ))
        self.toggle_all_cheats_btn.grid(
            row=0, column=2, columnspan=2, sticky="e", pady=(0, 8)
        )
        self.cheat_canvas = Canvas(
            card, highlightthickness=0, bd=0,
            background=DARK["surface"], takefocus=0
        )
        self.cheat_scroll = ttk.Scrollbar(
            card, orient="vertical", command=self.cheat_canvas.yview
        )
        self.cheat_canvas.configure(yscrollcommand=self.cheat_scroll.set)
        self.cheat_canvas.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.cheat_scroll.grid(row=1, column=3, sticky="ns", padx=(8, 0))
        self.cheat_content = ttk.Frame(
            self.cheat_canvas, style="Card.TFrame"
        )
        self.cheat_content.columnconfigure(0, weight=1)
        self.cheat_window = self.cheat_canvas.create_window(
            0, 0, anchor="nw", window=self.cheat_content
        )
        self.cheat_summaries = []
        for index, cheat in enumerate(CHEATS):
            row = index * 2
            box = self._lockable(ttk.Checkbutton(
                self.cheat_content, text=cheat.title,
                style="Cheat.TCheckbutton",
                variable=self.cheat_vars[cheat.name]))
            box.grid(row=row, column=0, sticky="w")
            self.cheat_boxes[cheat.name] = box
            summary = ttk.Label(
                self.cheat_content, text=cheat.summary,
                style="CardMuted.TLabel", wraplength=self._px(720),
                justify="left"
            )
            summary.grid(
                row=row + 1, column=0, sticky="w",
                padx=(self._px(24), 0),
                pady=(0, self._px(9) if index < len(CHEATS) - 1 else 0))
            self.cheat_summaries.append(summary)
        self.corrupt_save_box = self._lockable(ttk.Checkbutton(
            self.cheat_content, text="Enable corrupt-save",
            style="Cheat.TCheckbutton", variable=self.corrupt_save_var))
        self.corrupt_save_box.grid(row=len(CHEATS) * 2, column=0, sticky="w",
                                   pady=(self._px(10), 0))
        self.corrupt_save_note = ttk.Label(
            self.cheat_content,
            text="Writes the corrupted-save and memory-card recovery words. "
                 "Turn it off for an OPL build, which is what freezes after "
                 "a battle.",
            style="CardMuted.TLabel", wraplength=self._px(720),
            justify="left")
        self.corrupt_save_note.grid(row=len(CHEATS) * 2 + 1, column=0,
                                    sticky="w", padx=(self._px(24), 0))
        self.dependency_label = ttk.Label(
            self.cheat_content, text="", style="Warn.TLabel",
            wraplength=self._px(720),
            justify="left")
        self.dependency_label.grid(row=len(CHEATS) * 2 + 2, column=0,
                                   sticky="w", pady=(self._px(10), 0))
        self.cheat_content.bind("<Configure>", self._sync_cheat_scrollregion)
        self.cheat_canvas.bind("<Configure>", self._resize_cheat_content)
        return card

    def _sync_cheat_scrollregion(self, _event=None):
        bounds = self.cheat_canvas.bbox("all")
        if bounds:
            self.cheat_canvas.configure(scrollregion=bounds)

    def _resize_cheat_content(self, event):
        width = max(1, event.width)
        self.cheat_canvas.itemconfigure(self.cheat_window, width=width)
        wrap = max(self._px(280), width - self._px(24))
        for label in (*self.cheat_summaries, self.corrupt_save_note,
                      self.dependency_label):
            label.configure(wraplength=wrap)

    def _sync_output_name(self, *_args):
        source = self.source_var.get().strip()
        folder = self.output_var.get().strip()
        if not source or not folder:
            self.output_label.configure(text="")
            return
        self.output_label.configure(
            text="Writes %s" % output_path_for(Path(source), folder).name)

    def _sync_dependency(self, *_args):
        """Keep the anti-cheat box ticked and locked while a (!) cheat needs it."""
        needed = [cheat for cheat in CHEATS
                  if cheat.requires_anti_cheat
                  and self.cheat_vars[cheat.name].get()]
        anti = self.cheat_vars[ANTI_CHEAT]
        if needed:
            if not anti.get():
                anti.set(True)
            self.dependency_label.configure(
                text="%s needs Disable Anti-Cheat Systems, so it stays on."
                     % needed[0].title)
        else:
            self.dependency_label.configure(text="")
        self.toggle_all_cheats_btn.configure(
            text=("Disable all cheats"
                  if any(variable.get() for variable in self.cheat_vars.values())
                  else "Enable all cheats")
        )
        if anti.get():
            self.corrupt_save_box.grid()
            self.corrupt_save_note.grid()
        else:
            self.corrupt_save_box.grid_remove()
            self.corrupt_save_note.grid_remove()
        if not self.runner.busy if hasattr(self, "runner") else True:
            self._set_anti_cheat_state(DISABLED if needed else NORMAL)

    def _toggle_all_cheats(self):
        if any(variable.get() for variable in self.cheat_vars.values()):
            for name, variable in self.cheat_vars.items():
                if name != ANTI_CHEAT:
                    variable.set(False)
            self.cheat_vars[ANTI_CHEAT].set(False)
        else:
            self.cheat_vars[ANTI_CHEAT].set(True)
            for name, variable in self.cheat_vars.items():
                if name != ANTI_CHEAT:
                    variable.set(True)

    def _set_anti_cheat_state(self, state):
        for widget, _idle in self.locked:
            if str(widget.cget("style") or "") == "Cheat.TCheckbutton" and \
                    str(widget.cget("text")) == CHEATS[0].title:
                widget.configure(state=state)

    def _sync_status(self, *_args):
        self.canvas.itemconfigure(self.status_item, text=self.status_var.get())
        self.canvas.itemconfigure(self.detail_item, text=self.detail_var.get())

    def _bottom(self, item, fallback):
        box = self.canvas.bbox(item)
        return box[3] if box else fallback

    def _cheat_panel_height(self, window_height, top, row_height):
        """Give the middle panel space without displacing fixed controls."""
        gap = self._px(14)
        reserved = (
            gap + row_height + gap + self.progress.winfo_reqheight() +
            self._px(8) + self.line_heights["body"] + self._px(2) +
            2 * self.line_heights["small"] + self._px(2) + self._px(16)
        )
        if self.log_shown.get():
            reserved += gap + self._px(90)
        available = window_height - top - reserved
        return max(self._px(180), min(self._px(300), available))

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
        self.canvas.coords(self.items["disc"], pad, y)
        self.canvas.itemconfigure(self.items["disc"], width=inner)
        y += self.disc_card.winfo_reqheight() + gap
        row = (self.patch_btn, self.log_btn)
        row_height = max(widget.winfo_reqheight() for widget in row)
        cheat_height = self._cheat_panel_height(height, y, row_height)
        self.canvas.coords(self.items["cheats"], pad, y)
        self.canvas.itemconfigure(
            self.items["cheats"], width=inner, height=cheat_height
        )
        y += cheat_height + gap
        positions = (
            ("patch", self.patch_btn, pad),
            ("log_btn", self.log_btn, width - pad - self.log_btn.winfo_reqwidth()),
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
            log_height = max(self._px(90), height - top - bottom)
            self.canvas.itemconfigure(
                self.items["log"], width=inner,
                height=log_height)

    def _pick_source(self):
        path = filedialog.askopenfilename(
            title="Select the USA Valkyrie Profile 2 ISO",
            filetypes=[("Disc images", "*.iso"), ("All files", "*.*")])
        if path:
            self.source_var.set(path)
            level, note = describe_disc(Path(path))
            size = Path(path).stat().st_size / (1 << 30)
            self.status_var.set(note)
            self.detail_var.set(f"{size:.2f} GB")
            if level == "error":
                messagebox.showerror("Unexpected disc image", note)

    def _pick_output(self):
        path = filedialog.askdirectory(title="Where should the ISO be written?")
        if path:
            self.output_var.set(path)

    def selected_cheats(self):
        return required_with(
            [name for name, var in self.cheat_vars.items() if var.get()])

    def _validated_source(self):
        raw = self.source_var.get().strip()
        if not raw:
            messagebox.showinfo("Pick an ISO", "Choose the USA ISO first.")
            return None
        source = Path(raw)
        level, note = describe_disc(source)
        if level != "ok":
            messagebox.showerror("Unusable image", note)
            return None
        return source

    def _start_patch(self):
        if self.runner.busy:
            return
        source = self._validated_source()
        if source is None:
            return
        selected = self.selected_cheats()
        if not selected:
            messagebox.showinfo("Pick a cheat", "Choose at least one cheat.")
            return
        folder = Path(self.output_var.get().strip() or (PROJECT_ROOT / "build"))
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", str(exc))
            return
        output = output_path_for(source, folder)
        if output.exists():
            if not messagebox.askyesno(
                    "Overwrite?",
                    f"Output already exists:\n{output}\n\nReplace it?"):
                return
            try:
                output.unlink()
            except OSError as exc:
                messagebox.showerror("Output folder", str(exc))
                return

        self.started_at = time.time()
        self._set_busy(True)
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.status_var.set("Reading the disc and rebuilding the patches…")
        self.detail_var.set("%d cheat(s)" % len(selected))
        self._append_log(
            "\n=== patch: %s -> %s ===\n" % (source.name, output.name))
        for name in selected:
            self._append_log("  %s\n" % name)
        self.runner.start("patch", build_iso, source, output,
                          selected=selected, progress=print,
                          corrupt_save=self.corrupt_save_var.get())

    def _set_busy(self, busy):
        for widget, idle in self.locked:
            widget.configure(state=DISABLED if busy else idle)
        if not busy:
            self._sync_dependency()
        self.on_busy_change(bool(busy))

    def _on_line(self, text):
        self._append_log(text)
        for line in text.splitlines():
            self._track_progress(line.strip())

    def _track_progress(self, line):
        copied = COPY_LINE.match(line)
        if copied:
            percent = int(copied.group(1))
            self.progress.configure(value=percent * 0.9)
            self.status_var.set(f"Copying the source image… {percent}%")
            self.detail_var.set(f"{self._elapsed()} elapsed")
            return
        if line.startswith("patch: "):
            self.status_var.set("Reading the disc and rebuilding the patches…")
            self.detail_var.set(line[len("patch: "):])
        elif line.startswith("write: "):
            self.progress.configure(value=92)
            self.status_var.set("Writing the patched regions…")
        elif line.startswith("verify: "):
            self.progress.configure(value=96)
            self.status_var.set("Verifying every patched byte from disk…")

    def _elapsed(self):
        seconds = int(time.time() - (self.started_at or time.time()))
        return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"

    def _on_done(self, kind, result, error):
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self._set_busy(False)
        if error is not None:
            self.progress.configure(value=0)
            self.status_var.set("Patch failed.")
            self.detail_var.set(str(error))
            if not self.log_shown.get():
                self._toggle_log()
            messagebox.showerror("Patch failed", str(error))
            return
        self.progress.configure(value=100)
        self.status_var.set(f"Patched ISO written in {self._elapsed()}.")
        self.detail_var.set(str(result.output))
        self._append_log("=== done ===\n")
        if messagebox.askyesno(
                "Patch complete",
                f"Patched ISO written to:\n{result.output}\n\n"
                "Open the output folder?"):
            self._open_output_folder(Path(result.output).parent)

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
        if self.runner.busy and not messagebox.askyesno(
                "Still working",
                "A patch is still running. Closing now abandons it; the "
                "unfinished .partial file is removed on the next run."
                "\n\nClose anyway?"):
            return False
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


def run_gui() -> int:
    if TK_IMPORT_ERROR is not None:
        print(f"this build has no Tk, so the window cannot open: "
              f"{TK_IMPORT_ERROR}", file=sys.stderr)
        return 3
    enable_dpi_awareness()
    root = Tk()
    root.withdraw()
    App(root)
    use_dark_titlebar(root)
    root.deiconify()
    root.mainloop()
    return 0
