# SPDX-FileCopyrightText: 2026 Valkyrie Profile 2 Translation Tools contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Window for the glyph texture tool."""

from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from . import build
from ..app_meta import VERSION as __version__
from ..scripts.paths import output_root
from ..voice_patcher.gui import (
    BACKDROP_PNG, DARK, TK_IMPORT_ERROR, TaskRunner, apply_dark_theme,
    apply_dpi_scaling, apply_window_icon, asset_path, enable_dpi_awareness,
    initial_window_geometry, use_dark_titlebar,
)

try:
    from tkinter import (
        BooleanVar, Canvas, DISABLED, END, NORMAL, PhotoImage, StringVar,
        Text, Tk, filedialog, messagebox,
    )
    from tkinter import ttk
except ImportError:  # pragma: no cover - depends on Python build
    BooleanVar = Canvas = PhotoImage = StringVar = Text = Tk = None
    filedialog = messagebox = ttk = None
    DISABLED = END = NORMAL = None


APP_NAME = "Valkyrie Profile 2 Glyph Tool"
SHORT_NAME = "VP2 Glyph Tool"
COPY_LINE = re.compile(r"^copy:\s+(\d+)%")
GLYPH_LINE = re.compile(r"^glyphs: (\d+)/(\d+)")
ISO_TYPES = [("Disc images", "*.iso"), ("All files", "*.*")]


class App:
    def __init__(self, root, parent=None, on_busy_change=None):
        self.root = root
        self.host = parent or root
        self.embedded = parent is not None
        self.on_busy_change = on_busy_change or (lambda _busy: None)
        default = str(output_root())
        self.patch_source_var = StringVar()
        self.patch_output_var = StringVar(value=default)
        self.export_source_var = StringVar()
        self.export_output_var = StringVar(value=default)
        self.masters_var = StringVar()
        self.dds_output_var = StringVar(value=default)
        self.status_var = StringVar(
            value="Patch an ISO, extract its glyphs, or turn edited glyphs "
                  "into PCSX2 textures.")
        self.detail_var = StringVar()
        self.log_shown = BooleanVar(value=False)
        self.locked = []
        self.started_at = None
        self.scale = apply_dpi_scaling(root)
        self.fonts = apply_dark_theme(root)
        self.compact_height = int(700 * self.scale)
        self.expanded_height = int(900 * self.scale)
        if not self.embedded:
            root.title(SHORT_NAME)
            root.minsize(int(760 * self.scale), self.compact_height)
            root.geometry(initial_window_geometry(
                root, int(900 * self.scale), self.compact_height))
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
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        return card

    def _path_row(self, card, row, label, variable, command, button="Browse…"):
        ttk.Label(card, text=label, style="Card.TLabel").grid(
            row=row, column=0, sticky="w", padx=(0, 10), pady=2)
        self._lock(ttk.Entry(card, textvariable=variable)).grid(
            row=row, column=1, sticky="ew", padx=(0, 10), pady=2)
        self._lock(ttk.Button(card, text=button, command=command)).grid(
            row=row, column=2, sticky="e", pady=2)

    def _note(self, card, row, text=""):
        label = ttk.Label(card, text=text, style="CardMuted.TLabel",
                          wraplength=self._px(700), justify="left")
        label.grid(row=row, column=1, columnspan=2, sticky="w", pady=(5, 8))
        return label

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
            0, 0, anchor="nw", text=SHORT_NAME, fill=DARK["text"],
            font=self.fonts["title"])
        self.subtitle_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"],
            text="Replace the game's text glyphs with high-resolution "
                 "textures in PCSX2.")

        self.notebook = ttk.Notebook(self.canvas)
        tabs = []
        for text in ("Patch ISO", "Extract Glyphs", "Create DDS"):
            tab = ttk.Frame(self.notebook, style="Tab.TFrame",
                            padding=(0, 4, 0, 0))
            self.notebook.add(tab, text=text)
            tabs.append(tab)
        patch_tab, export_tab, dds_tab = tabs

        patch = self._card(patch_tab, "PATCH ISO")
        self._path_row(patch, 1, "ISO", self.patch_source_var,
                       self._pick_patch_source)
        self._path_row(patch, 2, "Output folder", self.patch_output_var,
                       lambda: self._pick_folder(
                           self.patch_output_var,
                           "Where should the patched ISO be written?"),
                       "Change…")
        self.patch_note = self._note(patch, 3)
        self.patch_btn = self._lock(ttk.Button(
            patch, text="Patch ISO", style="Accent.TButton",
            command=self._start_patch))
        self.patch_btn.grid(row=4, column=1, sticky="w")
        patch.pack(fill="x")

        export = self._card(export_tab, "EXTRACT GLYPHS")
        self._path_row(export, 1, "Patched ISO", self.export_source_var,
                       self._pick_export_source)
        self._path_row(export, 2, "Output folder", self.export_output_var,
                       lambda: self._pick_folder(
                           self.export_output_var,
                           "Where should the glyph folder be created?"),
                       "Change…")
        self.export_note = self._note(export, 3)
        self.export_btn = self._lock(ttk.Button(
            export, text="Extract glyphs", style="Accent.TButton",
            command=self._start_export))
        self.export_btn.grid(row=4, column=1, sticky="w")
        export.pack(fill="x")

        dds = self._card(dds_tab, "CREATE DDS")
        self._path_row(dds, 1, "Glyph PNGs", self.masters_var,
                       lambda: self._pick_folder(
                           self.masters_var,
                           "Select the folder of glyph-<hash>.png files"))
        self._path_row(dds, 2, "Output folder", self.dds_output_var,
                       lambda: self._pick_folder(
                           self.dds_output_var,
                           "Where should the texture folder be created?"),
                       "Change…")
        self.dds_note = self._note(dds, 3)
        self.dds_btn = self._lock(ttk.Button(
            dds, text="Create DDS", style="Accent.TButton",
            command=self._start_dds))
        self.dds_btn.grid(row=4, column=1, sticky="w")
        dds.pack(fill="x")

        self.tab_cards = {"patch": (patch,), "export": (export,),
                          "dds": (dds,)}
        for variable in (self.patch_source_var, self.patch_output_var,
                         self.export_source_var, self.export_output_var,
                         self.masters_var, self.dds_output_var):
            variable.trace_add("write", self._sync_notes)
        self._sync_notes()

        self.progress = ttk.Progressbar(self.canvas, maximum=100)
        self.log_btn = ttk.Button(self.canvas, text="Show details",
                                  command=self._toggle_log)
        self.status_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["text"], font=self.fonts["body"])
        self.detail_item = self.canvas.create_text(
            0, 0, anchor="nw", fill=DARK["muted"], font=self.fonts["small"])
        self.status_var.trace_add("write", self._sync_status)
        self.detail_var.trace_add("write", self._sync_status)
        self._sync_status()
        self.log_frame = ttk.Frame(self.canvas, style="Card.TFrame")
        self.log = Text(
            self.log_frame, wrap="none", height=8, font=self.fonts["mono"],
            relief="flat", background=DARK["surface"],
            foreground=DARK["muted"], selectbackground=DARK["accent_dim"],
            padx=10, pady=8)
        scrollbar = ttk.Scrollbar(self.log_frame, orient="vertical",
                                  command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set, state=DISABLED)
        self.log.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(0, weight=1)
        widgets = {"notebook": self.notebook, "progress": self.progress,
                   "log_btn": self.log_btn, "log": self.log_frame}
        self.items = {name: self.canvas.create_window(
            0, 0, anchor="nw", window=widget
        ) for name, widget in widgets.items()}
        self.canvas.itemconfigure(self.items["log"], state="hidden")
        self.canvas.bind("<Configure>", self._reflow)
        self._append_log("%s %s\n" % (APP_NAME, __version__))

    def _sync_status(self, *_args):
        self.canvas.itemconfigure(self.status_item, text=self.status_var.get())
        self.canvas.itemconfigure(self.detail_item, text=self.detail_var.get())

    def _sync_notes(self, *_args):
        source = self.patch_source_var.get().strip()
        folder = self.patch_output_var.get().strip()
        self.patch_note.configure(text=(
            "Writes %s · also turns the anti-cheat off if it is on"
            % build.patched_iso_path(source, folder).name
            if source and folder else
            "Choose the USA ISO or a translated build · the source is "
            "never modified"))
        source = self.export_source_var.get().strip()
        folder = self.export_output_var.get().strip()
        self.export_note.configure(text=(
            "Creates %s" % build.export_folder(source, folder)
            if source and folder else
            "Choose an ISO patched on the first tab · writes one "
            "glyph-<hash>.png per glyph and a sheet of them"))
        masters = self.masters_var.get().strip()
        folder = self.dds_output_var.get().strip()
        self.dds_note.configure(text=(
            "Creates %s · copy it into PCSX2's textures/SLUS-21452/"
            "replacements folder" % build.dds_folder(masters, folder)
            if masters and folder else
            "Reads only glyph-<hash>.png files, square, any size · three "
            "textures per glyph"))

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
            for cards in self.tab_cards.values())
        self.canvas.coords(self.items["notebook"], pad, y)
        self.canvas.itemconfigure(self.items["notebook"], width=inner,
                                  height=tab_height)
        y += tab_height + gap
        self.canvas.coords(self.items["progress"], pad, y)
        self.canvas.itemconfigure(self.items["progress"], width=inner)
        y += self.progress.winfo_reqheight() + 8
        self.canvas.coords(self.status_item, pad, y)
        self.canvas.itemconfigure(self.status_item, width=inner - 130)
        self.canvas.coords(
            self.items["log_btn"],
            width - pad - self.log_btn.winfo_reqwidth(), y)
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
                height=max(self._px(90), height - y - self._px(16)))

    def _pick_iso(self, variable, title):
        path = filedialog.askopenfilename(title=title, filetypes=ISO_TYPES)
        if not path:
            return None
        try:
            build.check_disc(path)
        except ValueError as exc:
            messagebox.showerror("Unsupported disc image", str(exc))
            return None
        variable.set(path)
        return path

    def _pick_patch_source(self):
        path = self._pick_iso(self.patch_source_var,
                              "Select a USA Valkyrie Profile 2 ISO")
        if not path or self.runner.busy:
            return
        self.status_var.set("Checking the ISO…")
        self.detail_var.set(str(path))
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self._set_busy(True)
        self.runner.start("check", build.disc_state, path)

    def _finish_check(self, state, error):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_busy(False)
        if error is not None:
            self.status_var.set("Could not read the ISO.")
            self.detail_var.set(str(error))
            return
        self.status_var.set("Glyph textures: %s · anti-cheat: %s" % (
            "already on" if state.glyph_textures else "off",
            "already off" if state.anti_cheat_off else "on"))
        try:
            size = Path(self.patch_source_var.get()).stat().st_size
        except OSError:
            return
        self.detail_var.set("%.2f GB" % (size / (1 << 30)))

    def _pick_export_source(self):
        self._pick_iso(self.export_source_var,
                       "Select an ISO patched on the first tab")

    def _pick_folder(self, variable, title):
        path = filedialog.askdirectory(title=title)
        if path:
            variable.set(path)

    def _required(self, variable, what):
        value = variable.get().strip()
        if not value:
            messagebox.showinfo("Missing input", "Choose %s first." % what)
        return value

    def _confirm_replace(self, path, pattern):
        path = Path(path)
        existing = list(path.glob(pattern)) if path.is_dir() else []
        if not existing:
            return False
        return messagebox.askyesno(
            "Replace?", "%s already holds %d file(s) from an earlier run."
            "\n\nReplace them?" % (path, len(existing))) or None

    def _begin(self, kind, function, *args, **kwargs):
        self.started_at = time.time()
        self._set_busy(True)
        self.progress.configure(value=0)
        self._append_log("\n=== %s ===\n" % kind)
        self.runner.start(kind, function, *args, progress=print, **kwargs)

    def _start_patch(self):
        if self.runner.busy:
            return
        source = self._required(self.patch_source_var, "an ISO")
        folder = self._required(self.patch_output_var, "an output folder")
        if not source or not folder:
            return
        output = build.patched_iso_path(source, folder)
        if output.exists():
            if not messagebox.askyesno(
                    "Overwrite?",
                    "Output already exists:\n%s\n\nReplace it?" % output):
                return
            try:
                output.unlink()
            except OSError as exc:
                messagebox.showerror("Output folder", str(exc))
                return
        self.status_var.set("Patching the ISO…")
        self.detail_var.set(str(output))
        self._begin("patch", build.patch_iso, source, folder)

    def _start_export(self):
        if self.runner.busy:
            return
        source = self._required(self.export_source_var, "a patched ISO")
        folder = self._required(self.export_output_var, "an output folder")
        if not source or not folder:
            return
        replace = self._confirm_replace(
            build.export_folder(source, folder), "glyph*.png")
        if replace is None:
            return
        self.status_var.set("Extracting glyphs…")
        self.detail_var.set(str(build.export_folder(source, folder)))
        self._begin("extract", build.export_glyphs, source, folder,
                    replace=replace)

    def _start_dds(self):
        if self.runner.busy:
            return
        masters = self._required(self.masters_var, "the folder of glyph PNGs")
        folder = self._required(self.dds_output_var, "an output folder")
        if not masters or not folder:
            return
        if not Path(masters).is_dir():
            messagebox.showerror("Glyph PNGs", "No such folder:\n%s" % masters)
            return
        replace = self._confirm_replace(
            build.dds_folder(masters, folder), "*.dds")
        if replace is None:
            return
        self.status_var.set("Creating DDS textures…")
        self.detail_var.set(str(build.dds_folder(masters, folder)))
        self._begin("create DDS", build.write_dds, masters, folder,
                    replace=replace)

    def _set_busy(self, busy):
        for widget, idle in self.locked:
            widget.configure(state=DISABLED if busy else idle)
        self.on_busy_change(bool(busy))

    def _on_line(self, text):
        self._append_log(text)
        for line in text.splitlines():
            line = line.strip()
            copied = COPY_LINE.match(line)
            glyphs = GLYPH_LINE.match(line)
            if copied:
                percent = int(copied.group(1))
                self.progress.configure(value=percent * 0.9)
                self.status_var.set("Copying the ISO… %d%%" % percent)
            elif glyphs:
                current, total = map(int, glyphs.groups())
                self.progress.configure(value=current * 100 / total)
                self.status_var.set("Glyphs… %d/%d" % (current, total))
            elif line.startswith("write:"):
                self.progress.configure(value=92)
                self.status_var.set("Writing the patches…")
            elif line.startswith("verify:"):
                self.progress.configure(value=96)
                self.status_var.set("Verifying the patched ISO…")
        self.detail_var.set("%s elapsed" % self._elapsed())

    def _elapsed(self):
        seconds = int(time.time() - (self.started_at or time.time()))
        return ("%ds" % seconds if seconds < 60
                else "%dm %02ds" % divmod(seconds, 60))

    def _on_done(self, kind, result, error):
        if kind == "check":
            self._finish_check(result, error)
            return
        self._set_busy(False)
        title = kind[0].upper() + kind[1:]
        if error is not None:
            self.progress.configure(value=0)
            self.status_var.set("%s failed." % title)
            self.detail_var.set(str(error))
            if not self.log_shown.get():
                self._toggle_log()
            messagebox.showerror("%s failed" % title, str(error))
            return
        self.progress.configure(value=100)
        self.status_var.set("%s complete in %s." % (title, self._elapsed()))
        self.detail_var.set(str(result.output))
        self._append_log("=== done ===\n")
        if messagebox.askyesno(
                "%s complete" % title,
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
            self.items["log"], state="normal" if shown else "hidden")
        self.log_btn.configure(text="Hide details" if shown else "Show details")
        if not self.embedded:
            self.root.geometry("%dx%d" % (
                self.root.winfo_width(),
                self.expanded_height if shown else self.compact_height))
        self._reflow()

    def request_close(self):
        if self.runner.busy and not messagebox.askyesno(
                "Still working",
                "A glyph operation is still running. Close anyway?"):
            return False
        return True

    def _on_close(self):
        if self.request_close():
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
