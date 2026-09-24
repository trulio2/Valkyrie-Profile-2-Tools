# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Dark, branded window for voice extraction and fixed-slot patching."""

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

from .build import (
    default_japanese_audio_output, default_patch_output, default_voice_root,
    describe_disc, extract_voices, import_japanese_audio, patch_iso,
)
from . import movie
from .layout import (
    JAPAN_BOOT, JAPANESE_AUDIO_TARGET_BOOTS, VOICE_SOURCE_BOOTS,
)
from ..app_meta import VERSION as __version__

try:
    from tkinter import (
        BooleanVar, Canvas, DISABLED, END, NORMAL, PhotoImage, StringVar,
        Text, Tk, filedialog, messagebox,
    )
    from tkinter import font as tkfont
    from tkinter import ttk
except ImportError as exc:  # pragma: no cover - depends on Python build
    TK_IMPORT_ERROR = exc
    BooleanVar = Canvas = PhotoImage = StringVar = Text = Tk = None
    filedialog = messagebox = tkfont = ttk = None
    DISABLED = END = NORMAL = None
else:
    TK_IMPORT_ERROR = None


APP_NAME = "Valkyrie Profile 2 Voice Tool"
SHORT_NAME = "VP2 Voice Tool"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ICON_ICO = "images/vp2_release.ico"
ICON_PNG = "images/vp2_release.png"
BACKDROP_PNG = "images/vp2_release_bg.png"
COPY_LINE = re.compile(r"^copy:\s+(\d+)%")
EXTRACT_LINE = re.compile(r"^extract: bank \d+ \((\d+)/(\d+)\)")
IMPORT_LINE = re.compile(r"^repack: resource \d+ \((\d+)/(\d+)\)")
RELEASE_LABELS = {
    "en": "USA (English)", "jp": "Japan (Japanese)",
    "pal-en": "Europe/Australia (English)", "fr": "France",
    "de": "Germany", "it": "Italy", "es": "Spain",
}

DARK = {
    "bg": "#14161b", "surface": "#1b1e26", "surface_hi": "#232733",
    "border": "#2f3542", "text": "#e4e8f1", "muted": "#98a2b6",
    "accent": "#7aa2f7", "accent_hi": "#96b6ff",
    "accent_dim": "#3c5488", "ok": "#9ece6a", "warn": "#e0af68",
    "error": "#f7768e",
}


def asset_path(name):
    path = PROJECT_ROOT / name
    return path if path.is_file() else None


def initial_window_geometry(root, width, height):
    screen_width = max(1, int(root.winfo_screenwidth()))
    screen_height = max(1, int(root.winfo_screenheight()))
    width = min(int(width), screen_width)
    height = min(int(height), max(1, screen_height - 80))
    return "%dx%d+%d+%d" % (
        width, height, max(0, (screen_width - width) // 2),
        max(0, (screen_height - height) // 2),
    )


def _font_family():
    if sys.platform == "win32":
        return "Segoe UI"
    if sys.platform == "darwin":
        return "SF Pro Text"
    return "DejaVu Sans"


def _mono_family():
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
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


def apply_dpi_scaling(root):
    try:
        dpi = float(root.winfo_fpixels("1i"))
    except Exception:
        dpi = 96.0
    root.tk.call("tk", "scaling", max(dpi, 1.0) / 72.0)
    return max(dpi, 1.0) / 96.0


def apply_dark_theme(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    ui = _font_family()
    fonts = {
        "title": (ui, 17, "bold"), "body": (ui, 11), "small": (ui, 10),
        "button": (ui, 11, "bold"), "mono": (_mono_family(), 10),
    }
    root.configure(bg=DARK["bg"])
    style.configure(".", background=DARK["bg"], foreground=DARK["text"],
                    fieldbackground=DARK["surface"], font=fonts["body"],
                    borderwidth=0, focuscolor=DARK["accent_dim"])
    style.configure("Card.TFrame", background=DARK["surface"])
    style.configure("Tab.TFrame", background=DARK["bg"])
    style.configure(
        "TNotebook", background=DARK["bg"], borderwidth=0, relief="flat",
        bordercolor=DARK["bg"], lightcolor=DARK["bg"],
        darkcolor=DARK["bg"], tabmargins=(0, 0, 0, 8),
    )
    style.configure("TNotebook.Tab", background=DARK["surface_hi"],
                    foreground=DARK["muted"], padding=(18, 9),
                    font=fonts["button"], borderwidth=0, relief="flat",
                    bordercolor=DARK["surface_hi"],
                    lightcolor=DARK["surface_hi"],
                    darkcolor=DARK["surface_hi"])
    style.map("TNotebook.Tab",
              background=[("selected", DARK["accent"]),
                          ("active", DARK["border"])],
              foreground=[("selected", "#0d1017"),
                          ("active", DARK["text"])],
              bordercolor=[("selected", DARK["accent"]),
                           ("active", DARK["border"])],
              lightcolor=[("selected", DARK["accent"]),
                          ("active", DARK["border"])],
              darkcolor=[("selected", DARK["accent"]),
                         ("active", DARK["border"])])
    style.configure("Card.TLabel", background=DARK["surface"],
                    foreground=DARK["text"])
    style.configure("CardMuted.TLabel", background=DARK["surface"],
                    foreground=DARK["muted"], font=fonts["small"])
    style.configure("TEntry", fieldbackground=DARK["surface_hi"],
                    foreground=DARK["text"], insertcolor=DARK["text"],
                    bordercolor=DARK["border"], padding=6)
    style.configure("TButton", background=DARK["surface_hi"],
                    foreground=DARK["text"], padding=(14, 7))
    style.map("TButton", background=[("active", DARK["border"]),
                                     ("disabled", DARK["surface"])],
              foreground=[("disabled", DARK["muted"])])
    style.configure("Accent.TButton", background=DARK["accent"],
                    foreground="#0d1017", font=fonts["button"],
                    padding=(20, 9))
    style.map("Accent.TButton", background=[("active", DARK["accent_hi"]),
                                            ("disabled", DARK["surface_hi"])],
              foreground=[("disabled", DARK["muted"])])
    style.configure("Horizontal.TProgressbar", background=DARK["accent"],
                    troughcolor=DARK["surface_hi"], borderwidth=0,
                    thickness=8)
    style.configure("Chip.TCheckbutton", background=DARK["surface"],
                    foreground=DARK["muted"], font=fonts["small"],
                    indicatorbackground=DARK["surface_hi"],
                    indicatorforeground=DARK["bg"], padding=(0, 5))
    style.map("Chip.TCheckbutton",
              background=[("disabled", DARK["surface"]),
                          ("pressed", DARK["surface"]),
                          ("active", DARK["surface"])],
              indicatorbackground=[("selected", DARK["accent"]),
                                   ("active", DARK["border"])],
              foreground=[("active", DARK["text"]),
                          ("disabled", DARK["muted"])])
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
            root._voice_icon = PhotoImage(master=root, data=png.read_bytes())
            root.iconphoto(True, root._voice_icon)
        except Exception:
            pass


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
    except Exception:
        pass


class _QueueStream:
    def __init__(self, events):
        self.events = events

    def write(self, value):
        if value:
            self.events.put(("line", value))
        return len(value or "")

    def flush(self):
        pass


class TaskRunner:
    #: Lines handled per callback before the window is given back.
    BATCH = 200

    def __init__(self, root, on_line, on_done):
        self.root, self.on_line, self.on_done = root, on_line, on_done
        self.events = queue.Queue()
        self.thread = None

    @property
    def busy(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, kind, function, *args, **kwargs):
        if self.busy:
            return False
        self.thread = threading.Thread(
            target=self._run, args=(kind, function, args, kwargs), daemon=True
        )
        self.thread.start()
        self.root.after(60, self._poll)
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
        backlog = True
        for _ in range(self.BATCH):
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                backlog = False
                break
            if item[0] == "line":
                self.on_line(item[1])
            else:
                self.on_done(*item[1:])
        if self.busy or not self.events.empty():
            self.root.after(1 if backlog else 60, self._poll)


class App:
    def __init__(self, root, parent=None, on_busy_change=None):
        self.root = root
        self.host = parent or root
        self.embedded = parent is not None
        self.on_busy_change = on_busy_change or (lambda _busy: None)
        self.source_var = StringVar()
        self.undub_base_var = StringVar()
        self.japan_var = StringVar()
        self.extract_root_var = StringVar(value=str(default_voice_root()))
        self.voices_var = StringVar()
        self.iso_output_var = StringVar(
            value=str(default_patch_output("Example.iso").parent)
        )
        self.undub_output_var = StringVar(
            value=str(default_japanese_audio_output("Example.iso").parent)
        )
        self.status_var = StringVar(
            value="Choose a voice workflow or create a Japanese-audio edition."
        )
        self.detail_var = StringVar()
        self.log_shown = BooleanVar(value=False)
        self.allow_overlong_var = BooleanVar(value=False)
        self.v1_scope_var = BooleanVar(value=False)
        self.movie_sync_vars = {
            11: StringVar(value="0.0000"),
            20: StringVar(value="0.0000"),
            14: StringVar(value="0.0000"),
        }
        self.locked = []
        self.started_at = None
        self.scale = apply_dpi_scaling(root)
        self.fonts = apply_dark_theme(root)
        self.compact_height = int(790 * self.scale)
        self.expanded_height = int(970 * self.scale)
        width = int(900 * self.scale)
        if not self.embedded:
            root.title(SHORT_NAME)
            root.minsize(int(760 * self.scale), self.compact_height)
            root.geometry(initial_window_geometry(
                root, width, self.compact_height))
            apply_window_icon(root)
        self._build_ui()
        self.runner = TaskRunner(root, self._on_line, self._on_done)
        if not self.embedded:
            root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _px(self, value):
        return int(value * self.scale)

    def _lock(self, widget):
        self.locked.append((widget, str(widget.cget("state")) or NORMAL))
        return widget

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 12))
        card.columnconfigure(1, weight=1)
        ttk.Label(card, text=title, style="CardMuted.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        return card

    def _path_row(self, card, row, label, variable, command, button="Browse…"):
        ttk.Label(card, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 10)
        )
        self._lock(ttk.Entry(card, textvariable=variable)).grid(
            row=row, column=1, sticky="ew", padx=(0, 10)
        )
        self._lock(ttk.Button(card, text=button, command=command)).grid(
            row=row, column=2, sticky="e"
        )

    def _build_ui(self):
        self.canvas = Canvas(self.host, highlightthickness=0, bd=0,
                             background=DARK["bg"])
        self.canvas.pack(fill="both", expand=True)
        backdrop_path = asset_path(BACKDROP_PNG)
        self.backdrop = (PhotoImage(
            master=self.root, data=backdrop_path.read_bytes()
        ) if backdrop_path else None)
        self.backdrop_item = (self.canvas.create_image(
            0, 0, anchor="se", image=self.backdrop
        ) if self.backdrop else None)
        self.title_item = self.canvas.create_text(
            0, 0, anchor="nw", text=SHORT_NAME,
            fill=DARK["text"], font=self.fonts["title"]
        )
        self.subtitle_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"],
            text="Extract and replace identified WAVs for a future fan dub, "
                 "or create a complete Japanese-audio edition."
        )

        self.notebook = ttk.Notebook(self.canvas)
        extract_tab = ttk.Frame(
            self.notebook, style="Tab.TFrame", padding=(0, 4, 0, 0)
        )
        patch_tab = ttk.Frame(
            self.notebook, style="Tab.TFrame", padding=(0, 4, 0, 0)
        )
        undub_tab = ttk.Frame(
            self.notebook, style="Tab.TFrame", padding=(0, 4, 0, 0)
        )
        self.notebook.add(extract_tab, text="Extract Voices")
        self.notebook.add(patch_tab, text="Patch Voices")
        self.notebook.add(undub_tab, text="Japanese Audio / Undub")

        for parent in (extract_tab, patch_tab):
            disc = self._card(parent, "DISC IMAGE")
            self._path_row(disc, 1, "Source", self.source_var,
                           self._pick_source)
            ttk.Label(
                disc,
                text="USA (English) or Japan (Japanese) · source is never modified",
                style="CardMuted.TLabel"
            ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(5, 0))
            disc.pack(fill="x", pady=(0, 12))

        extract = self._card(extract_tab, "EXTRACT VOICES")
        self._path_row(
            extract, 1, "Voice root", self.extract_root_var,
            self._pick_extract_root, "Change…"
        )
        ttk.Label(
            extract, text="Creates en/ or jp/, grouped by known cutscene resource",
            style="CardMuted.TLabel"
        ).grid(row=2, column=1, columnspan=2, sticky="w", pady=(5, 8))
        self.extract_btn = self._lock(ttk.Button(
            extract, text="Extract every voice", style="Accent.TButton",
            command=self._start_extract
        ))
        self.extract_btn.grid(row=3, column=1, sticky="w")
        extract.pack(fill="x", pady=(0, 12))

        patch = self._card(patch_tab, "PATCH VOICES")
        self._path_row(patch, 1, "WAV folder", self.voices_var,
                       self._pick_voices)
        self._path_row(patch, 2, "ISO output", self.iso_output_var,
                       self._pick_iso_output, "Change…")
        self.output_name = ttk.Label(patch, text="", style="CardMuted.TLabel")
        self.output_name.grid(row=3, column=1, columnspan=2, sticky="w",
                              pady=(5, 8))
        ttk.Label(
            patch,
            text="USA cutscene WAVs can use the larger cached USA/Japanese "
                 "slot; affected banks are rebuilt automatically.",
            style="CardMuted.TLabel", wraplength=self._px(690),
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=(0, 7))
        self.allow_overlong = self._lock(ttk.Checkbutton(
            patch, text="Allow WAVs beyond available slots (trim their tails)",
            variable=self.allow_overlong_var, style="Chip.TCheckbutton"
        ))
        self.allow_overlong.grid(row=5, column=1, columnspan=2, sticky="w")
        self.v1_scope = self._lock(ttk.Checkbutton(
            patch,
            text="V1 scope: cutscenes 0010–1389, Alicia and Lezard lines",
            variable=self.v1_scope_var, style="Chip.TCheckbutton",
        ))
        self.v1_scope.grid(row=6, column=1, columnspan=2, sticky="w")
        self.patch_btn = self._lock(ttk.Button(
            patch, text="Patch ISO", style="Accent.TButton",
            command=self._start_patch
        ))
        self.patch_btn.grid(row=7, column=1, sticky="w", pady=(5, 0))
        patch.pack(fill="x", pady=(0, 12))

        sync = self._card(patch_tab, "MOVIE WAV SYNC")
        ttk.Label(
            sync,
            text="Seconds · negative starts earlier · TAC values round to "
                 "21.333 ms frames",
            style="CardMuted.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 7))
        for row, (entry, scene) in enumerate(
                ((11, "0010"), (20, "1323"), (14, "1337")), start=2):
            ttk.Label(
                sync, text="Scene %s" % scene, style="Card.TLabel"
            ).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=2)
            control = self._lock(ttk.Spinbox(
                sync, from_=-10.0, to=10.0, increment=movie.TAC_FRAME_SECONDS,
                textvariable=self.movie_sync_vars[entry], width=12,
                format="%.4f",
            ))
            control.grid(row=row, column=1, sticky="w", pady=2)
        sync.pack(fill="x")

        target = self._card(undub_tab, "TARGET AND JAPANESE DONOR")
        self._path_row(
            target, 1, "Target ISO", self.undub_base_var,
            self._pick_undub_base,
        )
        self._path_row(
            target, 2, "Japanese ISO", self.japan_var,
            self._pick_japan,
        )
        self._path_row(
            target, 3, "Output folder", self.undub_output_var,
            self._pick_undub_output, "Change…",
        )
        ttk.Label(
            target,
            text="Targets: USA, Europe/Australia, France, Germany, Italy, Spain",
            style="CardMuted.TLabel",
        ).grid(row=4, column=1, columnspan=2, sticky="w", pady=(5, 0))
        target.pack(fill="x", pady=(0, 12))

        import_jp = self._card(undub_tab, "CREATE JAPANESE-AUDIO ISO")
        ttk.Label(
            import_jp,
            text="Keeps the target release's text and creates a separate, "
                 "fully verified ISO. Extracted WAV files are not required.",
            style="Card.TLabel",
            wraplength=self._px(720),
        ).grid(row=1, column=0, columnspan=3, sticky="w")
        self.jp_output_name = ttk.Label(
            import_jp,
            text="Choose a target ISO to see the output name",
            style="CardMuted.TLabel",
        )
        self.jp_output_name.grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(7, 10)
        )
        self.import_btn = self._lock(ttk.Button(
            import_jp, text="Create Japanese-audio ISO",
            style="Accent.TButton", command=self._start_import,
        ))
        self.import_btn.grid(row=3, column=0, sticky="w")
        import_jp.pack(fill="x")

        self.tab_cards = {
            "voice": (disc, extract, patch, sync),
            "undub": (target, import_jp),
        }
        self.source_var.trace_add("write", self._sync_output_name)
        self.undub_base_var.trace_add("write", self._sync_output_name)
        self.iso_output_var.trace_add("write", self._sync_output_name)
        self.undub_output_var.trace_add("write", self._sync_output_name)

        self.progress = ttk.Progressbar(self.canvas, maximum=100)
        self.log_btn = ttk.Button(
            self.canvas, text="Show details", command=self._toggle_log
        )
        self.status_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["text"], font=self.fonts["body"]
        )
        self.detail_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"]
        )
        self.status_var.trace_add("write", self._sync_status)
        self.detail_var.trace_add("write", self._sync_status)
        self._sync_status()
        self.log_frame = ttk.Frame(self.canvas, style="Card.TFrame")
        self.log = Text(
            self.log_frame, wrap="none", height=8, font=self.fonts["mono"],
            relief="flat", background=DARK["surface"], foreground=DARK["muted"],
            selectbackground=DARK["accent_dim"], padx=10, pady=8
        )
        scrollbar = ttk.Scrollbar(
            self.log_frame, orient="vertical", command=self.log.yview
        )
        self.log.configure(yscrollcommand=scrollbar.set, state=DISABLED)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        widgets = {
            "notebook": self.notebook,
            "progress": self.progress, "log_btn": self.log_btn,
            "log": self.log_frame,
        }
        self.items = {name: self.canvas.create_window(
            0, 0, anchor="nw", window=widget
        ) for name, widget in widgets.items()}
        self.canvas.itemconfigure(self.items["log"], state="hidden")
        self.canvas.bind("<Configure>", self._reflow)
        self._append_log("%s %s\n" % (APP_NAME, __version__))

    def _sync_status(self, *_args):
        self.canvas.itemconfigure(self.status_item, text=self.status_var.get())
        self.canvas.itemconfigure(self.detail_item, text=self.detail_var.get())

    def _reflow(self, _event=None):
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        pad, gap = self._px(22), self._px(12)
        inner = width - pad * 2
        if self.backdrop_item is not None:
            self.canvas.coords(self.backdrop_item, width, height)
        y = self._px(18)
        self.canvas.coords(self.title_item, pad, y)
        title_box = self.canvas.bbox(self.title_item)
        y = (title_box[3] if title_box else y) + 2
        self.canvas.itemconfigure(self.subtitle_item, width=inner)
        self.canvas.coords(self.subtitle_item, pad, y)
        subtitle_box = self.canvas.bbox(self.subtitle_item)
        y = (subtitle_box[3] if subtitle_box else y) + gap
        tab_height = max(
            sum(card.winfo_reqheight() for card in cards) +
            gap * max(0, len(cards) - 1) + self._px(52)
            for cards in self.tab_cards.values()
        )
        self.canvas.coords(self.items["notebook"], pad, y)
        self.canvas.itemconfigure(
            self.items["notebook"], width=inner, height=tab_height
        )
        y += tab_height + gap
        self.canvas.coords(self.items["progress"], pad, y)
        self.canvas.itemconfigure(self.items["progress"], width=inner)
        y += self.progress.winfo_reqheight() + 8
        self.canvas.coords(self.status_item, pad, y)
        self.canvas.itemconfigure(self.status_item, width=inner - 130)
        self.canvas.coords(
            self.items["log_btn"], width - pad - self.log_btn.winfo_reqwidth(), y
        )
        status = self.canvas.bbox(self.status_item)
        y = (status[3] if status else y + 20) + 2
        self.canvas.coords(self.detail_item, pad, y)
        self.canvas.itemconfigure(self.detail_item, width=inner)
        detail = self.canvas.bbox(self.detail_item)
        y = (detail[3] if detail else y + 18) + gap
        if self.log_shown.get():
            self.canvas.coords(self.items["log"], pad, y)
            self.canvas.itemconfigure(
                self.items["log"], width=inner,
                height=max(self._px(90), height - y - self._px(16))
            )

    def _pick_source(self):
        path = filedialog.askopenfilename(
            title="Select the USA or Japanese Valkyrie Profile 2 ISO",
            filetypes=[("Disc images", "*.iso"), ("All files", "*.*")]
        )
        if not path:
            return
        self.source_var.set(path)
        try:
            region, boot = describe_disc(path)
        except ValueError as exc:
            self.status_var.set("Unsupported disc image.")
            self.detail_var.set(str(exc))
            messagebox.showerror("Unexpected disc image", str(exc))
        else:
            if boot not in VOICE_SOURCE_BOOTS:
                messagebox.showerror(
                    "USA or Japanese ISO required",
                    "The Voice WAVs tab currently supports the USA and "
                    "Japanese releases. Use PAL images on the Japanese "
                    "Audio / Undub tab.",
                )
                self.source_var.set("")
                return
            label = RELEASE_LABELS[region]
            self.status_var.set("%s voice source recognised." % label)
            self.detail_var.set("%s · %.2f GB" % (
                boot, Path(path).stat().st_size / (1 << 30)
            ))

    def _pick_undub_base(self):
        path = filedialog.askopenfilename(
            title="Select a USA or PAL Valkyrie Profile 2 ISO",
            filetypes=[("Disc images", "*.iso"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            release, boot = describe_disc(path)
        except ValueError as exc:
            messagebox.showerror("Unexpected disc image", str(exc))
            return
        if boot not in JAPANESE_AUDIO_TARGET_BOOTS:
            messagebox.showerror(
                "Target ISO required",
                "Choose a supported USA or PAL Valkyrie Profile 2 ISO.",
            )
            return
        self.undub_base_var.set(path)
        self.status_var.set("%s target recognised." % RELEASE_LABELS[release])
        self.detail_var.set("%s · %.2f GB" % (
            boot, Path(path).stat().st_size / (1 << 30)
        ))

    def _pick_extract_root(self):
        path = filedialog.askdirectory(title="Where should en/ or jp/ be created?")
        if path:
            self.extract_root_var.set(path)

    def _pick_voices(self):
        path = filedialog.askdirectory(
            title="Select a folder of replacement WAV files"
        )
        if path:
            self.voices_var.set(path)

    def _pick_japan(self):
        path = filedialog.askopenfilename(
            title="Select the Japanese Valkyrie Profile 2 ISO",
            filetypes=[("Disc images", "*.iso"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            region, _boot = describe_disc(path)
        except ValueError as exc:
            messagebox.showerror("Unexpected disc image", str(exc))
            return
        if _boot != JAPAN_BOOT:
            messagebox.showerror(
                "Japanese ISO required",
                "Choose the original Japanese Valkyrie Profile 2 ISO.",
            )
            return
        self.japan_var.set(path)

    def _pick_iso_output(self):
        path = filedialog.askdirectory(title="Where should the patched ISO be written?")
        if path:
            self.iso_output_var.set(path)

    def _pick_undub_output(self):
        path = filedialog.askdirectory(
            title="Where should the Japanese-audio ISO be written?"
        )
        if path:
            self.undub_output_var.set(path)

    def _sync_output_name(self, *_args):
        source = self.source_var.get().strip()
        folder = self.iso_output_var.get().strip()
        if source and folder:
            self.output_name.configure(
                text="Writes %s" % (Path(folder) / default_patch_output(source).name).name
            )
        else:
            self.output_name.configure(text="")

        target = self.undub_base_var.get().strip()
        undub_folder = self.undub_output_var.get().strip()
        if target and undub_folder:
            self.jp_output_name.configure(
                text="Writes %s" % default_japanese_audio_output(target).name
            )
        else:
            self.jp_output_name.configure(
                text="Choose a target ISO to see the output name"
            )

    def _validated_source(self):
        raw = self.source_var.get().strip()
        if not raw:
            messagebox.showinfo("Pick an ISO", "Choose the USA or Japanese ISO first.")
            return None
        try:
            _region, boot = describe_disc(raw)
        except ValueError as exc:
            messagebox.showerror("Unusable image", str(exc))
            return None
        if boot not in VOICE_SOURCE_BOOTS:
            messagebox.showerror(
                "USA or Japanese ISO required",
                "Use a USA or Japanese ISO for extracted WAV operations.",
            )
            return None
        return Path(raw)

    def _validated_undub_base(self):
        raw = self.undub_base_var.get().strip()
        if not raw:
            messagebox.showinfo(
                "Pick a target ISO", "Choose a supported USA or PAL ISO first."
            )
            return None
        try:
            _release, boot = describe_disc(raw)
        except ValueError as exc:
            messagebox.showerror("Unusable image", str(exc))
            return None
        if boot not in JAPANESE_AUDIO_TARGET_BOOTS:
            messagebox.showerror(
                "Target ISO required",
                "Choose a supported USA or PAL Valkyrie Profile 2 ISO.",
            )
            return None
        return Path(raw)

    def _begin(self, kind, function, *args, **kwargs):
        self.started_at = time.time()
        self._set_busy(True)
        self.progress.configure(value=0)
        self._append_log("\n=== %s ===\n" % kind)
        self.runner.start(kind, function, *args, progress=print, **kwargs)

    def _start_extract(self):
        if self.runner.busy:
            return
        source = self._validated_source()
        if source is None:
            return
        root = Path(self.extract_root_var.get().strip() or default_voice_root())
        try:
            region, _boot = describe_disc(source)
        except ValueError:
            return
        if (root / region).exists():
            messagebox.showerror(
                "Output already exists",
                "Move or remove this existing extraction first:\n%s" % (root / region),
            )
            return
        self.status_var.set("Extracting every voice line…")
        self.detail_var.set("Output: %s" % (root / region))
        self._begin("extract", extract_voices, source, root)

    def _start_patch(self):
        if self.runner.busy:
            return
        source = self._validated_source()
        if source is None:
            return
        voices = Path(self.voices_var.get().strip())
        if not voices.is_dir():
            messagebox.showinfo(
                "Pick voice files", "Choose the folder containing replacement WAVs."
            )
            return
        try:
            movie_sync = {
                entry: float(variable.get().strip() or "0")
                for entry, variable in self.movie_sync_vars.items()
            }
            movie_sync.update({
                entry: movie_sync[14] for entry in (15, 16, 18)
            })
            for seconds in movie_sync.values():
                movie.tac_sync_frames(seconds)
        except ValueError as exc:
            messagebox.showerror("Movie WAV sync", str(exc))
            return
        folder = Path(
            self.iso_output_var.get().strip()
            or default_patch_output(source).parent
        )
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", str(exc))
            return
        output = folder / default_patch_output(source).name
        if output.exists():
            if not messagebox.askyesno(
                    "Overwrite?", "Output already exists:\n%s\n\nReplace it?" % output):
                return
            try:
                output.unlink()
            except OSError as exc:
                messagebox.showerror("Output folder", str(exc))
                return
        self.status_var.set("Reading and encoding replacement voices…")
        self.detail_var.set(str(voices))
        self._begin(
            "patch", patch_iso, source, voices, output,
            allow_overlong=self.allow_overlong_var.get(),
            scope="v1" if self.v1_scope_var.get() else "all",
            movie_sync=movie_sync,
        )

    def _start_import(self):
        if self.runner.busy:
            return
        source = self._validated_undub_base()
        if source is None:
            return
        japan = Path(self.japan_var.get().strip())
        try:
            _donor_region, donor_boot = describe_disc(japan)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Japanese ISO required", str(exc))
            return
        if donor_boot != JAPAN_BOOT:
            messagebox.showerror(
                "Japanese ISO required",
                "Choose the original Japanese Valkyrie Profile 2 ISO.",
            )
            return
        folder = Path(
            self.undub_output_var.get().strip()
            or default_japanese_audio_output(source).parent
        )
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Output folder", str(exc))
            return
        output = folder / default_japanese_audio_output(source).name
        if output.exists():
            if not messagebox.askyesno(
                    "Overwrite?", "Output already exists:\n%s\n\nReplace it?"
                    % output):
                return
            try:
                output.unlink()
            except OSError as exc:
                messagebox.showerror("Output folder", str(exc))
                return
        self.status_var.set("Creating the Japanese-audio edition…")
        self.detail_var.set(str(japan))
        self._begin(
            "Japanese audio import", import_japanese_audio,
            source, japan, output,
        )

    def _set_busy(self, busy):
        for widget, idle in self.locked:
            widget.configure(state=DISABLED if busy else idle)
        self.on_busy_change(bool(busy))

    def _on_line(self, text):
        self._append_log(text)
        for line in text.splitlines():
            line = line.strip()
            copied = COPY_LINE.match(line)
            extracted = EXTRACT_LINE.match(line)
            imported = IMPORT_LINE.match(line)
            if copied:
                percent = int(copied.group(1))
                self.progress.configure(value=percent * 0.9)
                self.status_var.set("Copying source ISO… %d%%" % percent)
            elif extracted:
                current, total = map(int, extracted.groups())
                self.progress.configure(value=current * 100 / total)
                self.status_var.set("Extracting voice banks… %d/%d" % (current, total))
            elif imported:
                current, total = map(int, imported.groups())
                self.progress.configure(value=90 + current * 5 / total)
                self.status_var.set(
                    "Repacking the complete game archive… %d/%d"
                    % (current, total)
                )
            elif line.startswith("write:"):
                self.progress.configure(value=92)
                self.status_var.set("Writing replacement voices…")
            elif line.startswith("verify:"):
                self.progress.configure(value=96)
                self.status_var.set("Verifying every rebuilt resource…")
        self.detail_var.set("%s elapsed" % self._elapsed())

    def _elapsed(self):
        seconds = int(time.time() - (self.started_at or time.time()))
        return "%ds" % seconds if seconds < 60 else "%dm %02ds" % divmod(seconds, 60)

    def _on_done(self, kind, result, error):
        self._set_busy(False)
        if error is not None:
            self.progress.configure(value=0)
            self.status_var.set("%s failed." % kind.capitalize())
            self.detail_var.set(str(error))
            if not self.log_shown.get():
                self._toggle_log()
            messagebox.showerror("%s failed" % kind.capitalize(), str(error))
            return
        self.progress.configure(value=100)
        self.status_var.set("%s complete in %s." % (kind.capitalize(), self._elapsed()))
        self.detail_var.set(str(result.output))
        self._append_log("=== done ===\n")
        if messagebox.askyesno(
                "%s complete" % kind.capitalize(),
                "Output written to:\n%s\n\nOpen its folder?" % result.output):
            self._open_folder(result.output if result.output.is_dir()
                              else result.output.parent)

    def _append_log(self, text):
        self.log.configure(state=NORMAL)
        self.log.insert(END, text)
        self.log.see(END)
        self.log.configure(state=DISABLED)

    def _toggle_log(self):
        shown = not self.log_shown.get()
        self.log_shown.set(shown)
        self.canvas.itemconfigure(
            self.items["log"], state="normal" if shown else "hidden"
        )
        self.log_btn.configure(text="Hide details" if shown else "Show details")
        if not self.embedded:
            width = self.root.winfo_width()
            self.root.geometry("%dx%d" % (
                width, self.expanded_height if shown else self.compact_height
            ))
        self._reflow()

    def request_close(self):
        if self.runner.busy and not messagebox.askyesno(
                "Still working", "A voice operation is still running. Close anyway?"):
            return False
        return True

    def _on_close(self):
        if not self.request_close():
            return
        self.root.destroy()

    def _open_folder(self, path):
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["open", str(path)])
            else:
                __import__("subprocess").Popen(["xdg-open", str(path)])
        except OSError as exc:
            messagebox.showerror("Could not open folder", str(exc))


def run_gui():
    if TK_IMPORT_ERROR is not None:
        print("this build has no Tk: %s" % TK_IMPORT_ERROR, file=sys.stderr)
        return 3
    enable_dpi_awareness()
    root = Tk()
    root.withdraw()
    App(root)
    use_dark_titlebar(root)
    root.deiconify()
    root.mainloop()
    return 0
