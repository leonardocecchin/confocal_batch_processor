"""Tkinter front end for the confocal batch processor.

Tabs mirror the order you actually make the decisions in: pick the folders,
describe each channel, set up cell counting, choose the outputs, then run.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import numpy as np

from .config import (
    APP_NAME,
    AUTHORS,
    COLOR_LUTS,
    CREDIT,
    DISPLAY_TRANSFORMS,
    LIMIT_MODES,
    PROJECTIONS,
    THRESHOLD_METHODS,
    THRESHOLD_SCOPES,
    ChannelSettings,
    Settings,
    default_settings_for,
)
from .imageio import list_images, probe
from .plots import DEFAULT_GROUP_PATTERN, PLOT_TYPES

PAD = 6


# --------------------------------------------------------------------- helpers
class ScrollableFrame(ttk.Frame):
    """A frame with a vertical scrollbar, for the channel list."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)

        self.inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        window = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._canvas = canvas
        for widget in (canvas, self.inner):
            widget.bind("<Enter>", self._bind_wheel)
            widget.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event=None):
        self._canvas.bind_all("<Button-4>", self._on_wheel)
        self._canvas.bind_all("<Button-5>", self._on_wheel)
        self._canvas.bind_all("<MouseWheel>", self._on_wheel)

    def _unbind_wheel(self, _event=None):
        for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
            self._canvas.unbind_all(seq)

    def _on_wheel(self, event):
        delta = -1 if getattr(event, "num", None) == 4 else 1
        if getattr(event, "delta", 0):
            delta = -1 if event.delta > 0 else 1
        self._canvas.yview_scroll(delta, "units")


def _labelled(parent, text: str, widget, row: int, col: int = 0, sticky: str = "w"):
    ttk.Label(parent, text=text).grid(row=row, column=col, sticky="e", padx=(0, 4), pady=2)
    widget.grid(row=row, column=col + 1, sticky=sticky, pady=2)
    return widget


class ChannelPanel:
    """Widgets describing one channel."""

    def __init__(self, parent, index: int, settings: ChannelSettings, on_change=None):
        self.index = index
        self.on_change = on_change
        self.frame = ttk.LabelFrame(parent, text=f"Channel {index + 1}")
        self.frame.pack(fill="x", expand=True, padx=PAD, pady=4)

        self.name = tk.StringVar(value=settings.name)
        self.color = tk.StringVar(value=settings.color)
        self.limit_mode = tk.StringVar(value=settings.limit_mode)
        self.vmin = tk.DoubleVar(value=settings.vmin)
        self.vmax = tk.DoubleVar(value=settings.vmax)
        self.low_pct = tk.DoubleVar(value=settings.low_percentile)
        self.high_pct = tk.DoubleVar(value=settings.high_percentile)
        self.transform = tk.StringVar(value=settings.display_transform)
        self.gamma = tk.DoubleVar(value=settings.gamma)
        self.subtract_bg = tk.BooleanVar(value=settings.subtract_background)
        self.bg_radius = tk.DoubleVar(value=settings.background_radius_um)
        self.m_area = tk.BooleanVar(value=settings.measure_area)
        self.m_volume = tk.BooleanVar(value=settings.measure_volume)
        self.m_intensity = tk.BooleanVar(value=settings.measure_intensity)
        self.threshold_method = tk.StringVar(value=settings.threshold_method)
        self.threshold_value = tk.DoubleVar(value=settings.threshold_value)
        self.threshold_pct = tk.DoubleVar(value=settings.threshold_percentile)
        self.threshold_scope = tk.StringVar(value=settings.threshold_scope)
        self.in_composite = tk.BooleanVar(value=settings.include_in_composite)
        self.save_single = tk.BooleanVar(value=settings.save_single_channel)

        body = ttk.Frame(self.frame)
        body.pack(fill="x", expand=True, padx=PAD, pady=PAD)

        # --- row 0: identity -------------------------------------------------
        top = ttk.Frame(body)
        top.grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        ttk.Label(top, text="Name").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.name, width=16)
        entry.pack(side="left", padx=(4, 12))
        entry.bind("<KeyRelease>", lambda e: self._changed())
        ttk.Label(top, text="Colour").pack(side="left")
        ttk.Combobox(
            top, textvariable=self.color, values=list(COLOR_LUTS), width=13, state="readonly"
        ).pack(side="left", padx=(4, 12))
        self.swatch = tk.Canvas(top, width=26, height=18, highlightthickness=1)
        self.swatch.pack(side="left", padx=(0, 12))
        self.color.trace_add("write", lambda *_: self._paint_swatch())
        self._paint_swatch()
        ttk.Checkbutton(top, text="in composite", variable=self.in_composite).pack(side="left")
        ttk.Checkbutton(top, text="save separately", variable=self.save_single).pack(
            side="left", padx=(8, 0)
        )

        # --- saturation ------------------------------------------------------
        sat = ttk.LabelFrame(body, text="Saturation limits")
        sat.grid(row=1, column=0, sticky="nsew", padx=(0, PAD))
        ttk.Label(sat, text="Mode").grid(row=0, column=0, sticky="e", padx=(4, 4), pady=2)
        mode_box = ttk.Combobox(
            sat, textvariable=self.limit_mode, values=list(LIMIT_MODES), width=15,
            state="readonly",
        )
        mode_box.grid(row=0, column=1, sticky="w", pady=2)
        mode_box.bind("<<ComboboxSelected>>", lambda e: self._sync_limit_state())

        self.manual_widgets = []
        for r, (label, var) in enumerate(
            (("Min", self.vmin), ("Max", self.vmax)), start=1
        ):
            lbl = ttk.Label(sat, text=label)
            lbl.grid(row=r, column=0, sticky="e", padx=(4, 4), pady=2)
            spin = ttk.Spinbox(sat, textvariable=var, from_=0, to=65535, width=10)
            spin.grid(row=r, column=1, sticky="w", pady=2)
            self.manual_widgets += [lbl, spin]

        self.pct_widgets = []
        for r, (label, var) in enumerate(
            (("Low %ile", self.low_pct), ("High %ile", self.high_pct)), start=3
        ):
            lbl = ttk.Label(sat, text=label)
            lbl.grid(row=r, column=0, sticky="e", padx=(4, 4), pady=2)
            spin = ttk.Spinbox(sat, textvariable=var, from_=0, to=100, increment=0.1, width=10)
            spin.grid(row=r, column=1, sticky="w", pady=2)
            self.pct_widgets += [lbl, spin]

        # --- display ----------------------------------------------------------
        disp = ttk.LabelFrame(body, text="Display scaling")
        disp.grid(row=1, column=1, sticky="nsew", padx=(0, PAD))
        ttk.Label(disp, text="Curve").grid(row=0, column=0, sticky="e", padx=(4, 4), pady=2)
        ttk.Combobox(
            disp, textvariable=self.transform, values=list(DISPLAY_TRANSFORMS), width=10,
            state="readonly",
        ).grid(row=0, column=1, sticky="w", pady=2)
        ttk.Label(disp, text="Gamma").grid(row=1, column=0, sticky="e", padx=(4, 4), pady=2)
        ttk.Spinbox(
            disp, textvariable=self.gamma, from_=0.05, to=5.0, increment=0.05, width=8
        ).grid(row=1, column=1, sticky="w", pady=2)
        ttk.Checkbutton(
            disp, text="subtract background", variable=self.subtract_bg
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=4, pady=(6, 2))
        ttk.Label(disp, text="radius µm").grid(row=3, column=0, sticky="e", padx=(4, 4))
        ttk.Spinbox(
            disp, textvariable=self.bg_radius, from_=0, to=500, increment=5, width=8
        ).grid(row=3, column=1, sticky="w")

        # --- measurements -------------------------------------------------------
        meas = ttk.LabelFrame(body, text="Measure (total and per cell)")
        meas.grid(row=1, column=2, sticky="nsew")
        ttk.Checkbutton(meas, text="area", variable=self.m_area).grid(
            row=0, column=0, sticky="w", padx=4
        )
        ttk.Checkbutton(meas, text="volume", variable=self.m_volume).grid(
            row=1, column=0, sticky="w", padx=4
        )
        ttk.Checkbutton(meas, text="intensity", variable=self.m_intensity).grid(
            row=2, column=0, sticky="w", padx=4
        )
        ttk.Label(meas, text="Threshold").grid(row=0, column=1, sticky="e", padx=(10, 4))
        ttk.Combobox(
            meas, textvariable=self.threshold_method, values=list(THRESHOLD_METHODS),
            width=11, state="readonly",
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(meas, text="value").grid(row=1, column=1, sticky="e", padx=(10, 4))
        ttk.Spinbox(
            meas, textvariable=self.threshold_value, from_=0, to=65535, width=9
        ).grid(row=1, column=2, sticky="w")
        ttk.Label(meas, text="%ile").grid(row=2, column=1, sticky="e", padx=(10, 4))
        ttk.Spinbox(
            meas, textvariable=self.threshold_pct, from_=0, to=100, increment=0.5, width=9
        ).grid(row=2, column=2, sticky="w")
        ttk.Label(meas, text="scope").grid(row=3, column=1, sticky="e", padx=(10, 4))
        ttk.Combobox(
            meas, textvariable=self.threshold_scope, values=list(THRESHOLD_SCOPES),
            width=9, state="readonly",
        ).grid(row=3, column=2, sticky="w")

        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        self._sync_limit_state()

    def _paint_swatch(self):
        rgb = COLOR_LUTS.get(self.color.get(), (255, 255, 255))
        self.swatch.configure(background="#%02x%02x%02x" % rgb)

    def _changed(self):
        if self.on_change:
            self.on_change()

    def _sync_limit_state(self):
        mode = self.limit_mode.get()
        for widget in self.manual_widgets:
            widget.configure(state="normal" if mode == "manual" else "disabled")
        for widget in self.pct_widgets:
            widget.configure(state="disabled" if mode == "manual" else "normal")

    def to_settings(self) -> ChannelSettings:
        return ChannelSettings(
            name=self.name.get().strip() or f"Channel{self.index + 1}",
            color=self.color.get(),
            limit_mode=self.limit_mode.get(),
            vmin=float(self.vmin.get()),
            vmax=float(self.vmax.get()),
            low_percentile=float(self.low_pct.get()),
            high_percentile=float(self.high_pct.get()),
            display_transform=self.transform.get(),
            gamma=float(self.gamma.get()),
            subtract_background=bool(self.subtract_bg.get()),
            background_radius_um=float(self.bg_radius.get()),
            measure_area=bool(self.m_area.get()),
            measure_volume=bool(self.m_volume.get()),
            measure_intensity=bool(self.m_intensity.get()),
            threshold_method=self.threshold_method.get(),
            threshold_value=float(self.threshold_value.get()),
            threshold_percentile=float(self.threshold_pct.get()),
            threshold_scope=self.threshold_scope.get(),
            include_in_composite=bool(self.in_composite.get()),
            save_single_channel=bool(self.save_single.get()),
        )

    def destroy(self):
        self.frame.destroy()


# ------------------------------------------------------------------------ app
class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master = master
        master.title(APP_NAME)
        master.geometry("1120x800")
        self.pack(fill="both", expand=True)

        self.settings = Settings()
        self.channel_panels: List[ChannelPanel] = []
        self.log_queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.stop_flag = threading.Event()
        self._probe_cache: Dict[str, object] = {}

        self.input_dir = tk.StringVar()
        self.output_dir = tk.StringVar()
        self.n_channels = tk.IntVar(value=4)
        self.max_workers = tk.IntVar(value=0)
        self.batch_sample = tk.IntVar(value=8)
        self.projection = tk.StringVar(value="max")

        self.roi_x0 = tk.DoubleVar(value=0.25)
        self.roi_y0 = tk.DoubleVar(value=0.25)
        self.roi_x1 = tk.DoubleVar(value=0.75)
        self.roi_y1 = tk.DoubleVar(value=0.75)
        self.roi_scope = tk.StringVar(value="global")
        self.roi_source = tk.StringVar(value="")

        self.an_enabled = tk.BooleanVar(value=False)
        self.an_x0 = tk.DoubleVar(value=0.05)
        self.an_y0 = tk.DoubleVar(value=0.05)
        self.an_x1 = tk.DoubleVar(value=0.95)
        self.an_y1 = tk.DoubleVar(value=0.95)
        self.an_show = tk.BooleanVar(value=True)

        master.protocol("WM_DELETE_WINDOW", self.on_close)

        self._build_menu()
        self._build_tabs()
        self._build_statusbar()
        self._rebuild_channel_panels()
        self.after(120, self._drain_log)

    # ------------------------------------------------------------------ chrome
    def _build_menu(self):
        menubar = tk.Menu(self.master)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Load settings...", command=self.load_settings)
        filemenu.add_command(label="Save settings...", command=self.save_settings)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.master.destroy)
        menubar.add_cascade(label="File", menu=filemenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label=f"About {APP_NAME}", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)
        self.master.config(menu=menubar)

    def show_about(self):
        from . import __version__

        messagebox.showinfo(
            f"About {APP_NAME}",
            f"{APP_NAME}  v{__version__}\n\n"
            f"Created by {AUTHORS}.\n\n"
            "Batch processing for confocal z-stacks: composites, 3D cell\n"
            "counting, per-channel statistics and 3D views.",
        )

    def _build_statusbar(self):
        bar = ttk.Frame(self)
        bar.pack(side="bottom", fill="x")
        self.status = tk.StringVar(value="ready")
        ttk.Label(bar, textvariable=self.status, anchor="w").pack(
            side="left", fill="x", expand=True, padx=PAD
        )
        self.progress = ttk.Progressbar(bar, mode="determinate", length=220)
        self.progress.pack(side="right", padx=PAD, pady=3)
        ttk.Label(bar, text=f"by {AUTHORS}", foreground="#777").pack(
            side="right", padx=PAD
        )

    def _build_tabs(self):
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self._tab_folders()
        self._tab_channels()
        self._tab_counting()
        self._tab_output()
        self._tab_run()
        self._tab_plots()

    # ------------------------------------------------------------- tab: folders
    def _tab_folders(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="1. Folders")

        box = ttk.LabelFrame(tab, text="Folders")
        box.pack(fill="x", padx=PAD, pady=PAD)
        for row, (label, var, cmd) in enumerate(
            (
                ("Input folder", self.input_dir, self._pick_input),
                ("Output folder", self.output_dir, self._pick_output),
            )
        ):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="e", padx=PAD, pady=4)
            ttk.Entry(box, textvariable=var, width=80).grid(
                row=row, column=1, sticky="we", pady=4
            )
            ttk.Button(box, text="Browse...", command=cmd).grid(
                row=row, column=2, padx=PAD, pady=4
            )
        box.columnconfigure(1, weight=1)

        listbox_frame = ttk.LabelFrame(tab, text="Images to process (none selected = all)")
        listbox_frame.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self.file_list = tk.Listbox(listbox_frame, selectmode="extended")
        scroll = ttk.Scrollbar(
            listbox_frame, orient="vertical", command=self.file_list.yview
        )
        self.file_list.configure(yscrollcommand=scroll.set)
        self.file_list.pack(side="left", fill="both", expand=True, padx=(PAD, 0), pady=PAD)
        scroll.pack(side="left", fill="y", pady=PAD)

        side = ttk.Frame(listbox_frame)
        side.pack(side="left", fill="y", padx=PAD, pady=PAD)
        ttk.Button(side, text="Refresh", command=self._refresh_files).pack(fill="x", pady=2)
        ttk.Button(side, text="Select all", command=lambda: self.file_list.select_set(0, "end")).pack(
            fill="x", pady=2
        )
        ttk.Button(
            side, text="Clear selection", command=lambda: self.file_list.select_clear(0, "end")
        ).pack(fill="x", pady=2)
        ttk.Button(side, text="Inspect first", command=self._inspect_first).pack(
            fill="x", pady=(12, 2)
        )

        info = ttk.LabelFrame(tab, text="Acquisition")
        info.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Label(info, text="Channels in file").grid(row=0, column=0, sticky="e", padx=PAD)
        spin = ttk.Spinbox(
            info, from_=1, to=8, textvariable=self.n_channels, width=5,
            command=self._rebuild_channel_panels,
        )
        spin.grid(row=0, column=1, sticky="w", pady=4)
        ttk.Label(info, text="Z projection").grid(row=0, column=2, sticky="e", padx=PAD)
        ttk.Combobox(
            info, textvariable=self.projection, values=list(PROJECTIONS), width=8,
            state="readonly",
        ).grid(row=0, column=3, sticky="w")
        ttk.Label(info, text="Parallel workers (0 = auto)").grid(
            row=0, column=4, sticky="e", padx=PAD
        )
        ttk.Spinbox(info, from_=0, to=32, textvariable=self.max_workers, width=5).grid(
            row=0, column=5, sticky="w"
        )
        ttk.Label(info, text="Calibration sample (0 = all)").grid(
            row=1, column=0, sticky="e", padx=PAD
        )
        ttk.Spinbox(
            info, from_=0, to=200, textvariable=self.batch_sample, width=5
        ).grid(row=1, column=1, sticky="w", pady=(0, 4))
        ttk.Label(
            info,
            text="images read up-front to fix one shared threshold and display window for the whole folder",
            foreground="#555",
        ).grid(row=1, column=2, columnspan=4, sticky="w")
        self.acq_label = ttk.Label(info, text="", foreground="#555")
        self.acq_label.grid(row=2, column=0, columnspan=6, sticky="w", padx=PAD, pady=(2, 6))

    def _pick_input(self):
        path = filedialog.askdirectory(title="Select the folder with the raw images")
        if path:
            self.input_dir.set(path)
            self._refresh_files()
            self._inspect_first()

    def _pick_output(self):
        path = filedialog.askdirectory(title="Select the output folder")
        if path:
            self.output_dir.set(path)

    def _refresh_files(self):
        self.file_list.delete(0, "end")
        for path in list_images(self.input_dir.get()):
            self.file_list.insert("end", os.path.basename(path))
        self.status.set(f"{self.file_list.size()} image(s) found")

    def _inspect_first(self):
        images = list_images(self.input_dir.get())
        if not images:
            messagebox.showinfo("No images", "No supported images in that folder.")
            return
        try:
            info = probe(images[0])
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Cannot read", str(exc))
            return
        self.acq_label.configure(text=info.summary())
        if info.n_channels != self.n_channels.get():
            self.n_channels.set(info.n_channels)
            self._rebuild_channel_panels(info.channel_names)
        self.status.set(f"read {info.name}")

    # ------------------------------------------------------------ tab: channels
    def _tab_channels(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="2. Channels")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=PAD, pady=(PAD, 0))
        ttk.Button(bar, text="Preview composite", command=self.preview_composite).pack(
            side="left"
        )
        ttk.Button(
            bar, text="Pick reference area for auto limits...", command=self.pick_reference_roi
        ).pack(side="left", padx=PAD)
        ttk.Label(bar, text="Reference scope").pack(side="left", padx=(PAD, 2))
        ttk.Combobox(
            bar, textvariable=self.roi_scope, values=["global", "per_image"], width=10,
            state="readonly",
        ).pack(side="left")
        ttk.Label(
            bar,
            text="  global = one window for the whole batch",
            foreground="#555",
        ).pack(side="left")

        ttk.Label(
            tab,
            text=(
                "Reference scope 'global' measures the rectangle once and gives every image "
                "the same saturation window, so brightness is comparable across the batch. "
                "'per_image' re-measures the same rectangle on each image, which normalises "
                "away differences in staining or laser power."
            ),
            foreground="#555",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=(4, 0))

        region = ttk.LabelFrame(tab, text="Analysis region (same for every image)")
        region.pack(fill="x", padx=PAD, pady=(PAD, 0))
        row_r = ttk.Frame(region)
        row_r.pack(anchor="w", padx=PAD, pady=4)
        ttk.Checkbutton(
            row_r, text="measure only inside a region", variable=self.an_enabled,
            command=self._sync_analysis_label,
        ).pack(side="left")
        ttk.Button(row_r, text="Select region...", command=self.pick_analysis_roi).pack(
            side="left", padx=PAD
        )
        ttk.Button(row_r, text="Reset to full image", command=self._reset_analysis_roi).pack(
            side="left"
        )
        ttk.Checkbutton(
            row_r, text="mark it on the overlay", variable=self.an_show
        ).pack(side="left", padx=PAD)
        self.an_label = ttk.Label(region, text="", foreground="#555")
        self.an_label.pack(anchor="w", padx=PAD, pady=(0, 2))
        ttk.Label(
            region,
            text=(
                "Counting, area, volume and intensity are measured inside this rectangle "
                "only; composites, single channels and 3D views are always exported as the "
                "full field. Use it to leave out the pillar rows or an out-of-focus margin."
            ),
            foreground="#555",
            wraplength=1000,
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=(0, 6))

        self.channel_container = ScrollableFrame(tab)
        self.channel_container.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self._sync_analysis_label()

    def _sync_analysis_label(self):
        if not self.an_enabled.get():
            self.an_label.configure(text="measuring the whole image")
            return
        w = abs(self.an_x1.get() - self.an_x0.get())
        h = abs(self.an_y1.get() - self.an_y0.get())
        self.an_label.configure(
            text=(
                f"region x {self.an_x0.get():.3f}-{self.an_x1.get():.3f}, "
                f"y {self.an_y0.get():.3f}-{self.an_y1.get():.3f}  "
                f"({w * h * 100:.0f}% of the field)"
            )
        )

    def _reset_analysis_roi(self):
        self.an_enabled.set(False)
        self.an_x0.set(0.05)
        self.an_y0.set(0.05)
        self.an_x1.set(0.95)
        self.an_y1.set(0.95)
        self._sync_analysis_label()

    def _rebuild_channel_panels(self, names: Optional[List[str]] = None):
        if not hasattr(self, "channel_container"):
            return
        previous = [p.to_settings() for p in self.channel_panels]
        for panel in self.channel_panels:
            panel.destroy()
        self.channel_panels = []

        count = max(1, int(self.n_channels.get()))
        template = default_settings_for(count, names)
        for i in range(count):
            base = previous[i] if i < len(previous) and names is None else template.channels[i]
            if names is not None and i < len(names):
                base = template.channels[i]
            self.channel_panels.append(ChannelPanel(self.channel_container.inner, i, base))
        self._refresh_nuclei_choices()

    # ------------------------------------------------------------ tab: counting
    def _tab_counting(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="3. Cell counting")

        self.seg_enabled = tk.BooleanVar(value=True)
        self.seg_channel = tk.StringVar()
        self.seg_smooth_xy = tk.DoubleVar(value=0.7)
        self.seg_smooth_z = tk.DoubleVar(value=1.5)
        self.seg_threshold = tk.StringVar(value="otsu")
        self.seg_threshold_value = tk.DoubleVar(value=0.0)
        self.seg_threshold_pct = tk.DoubleVar(value=99.0)
        self.seg_min_vol = tk.DoubleVar(value=150.0)
        self.seg_max_vol = tk.DoubleVar(value=20000.0)
        self.seg_radius = tk.DoubleVar(value=4.0)
        self.seg_seed_smooth = tk.DoubleVar(value=0.25)
        self.seg_fill = tk.BooleanVar(value=True)
        self.seg_border = tk.BooleanVar(value=False)
        self.seg_downsample = tk.IntVar(value=1)
        self.seg_save_labels = tk.BooleanVar(value=True)
        self.seg_save_overlay = tk.BooleanVar(value=True)
        self.seg_number_cells = tk.BooleanVar(value=False)
        self.seg_id_font = tk.IntVar(value=0)
        self.seg_ov_png = tk.BooleanVar(value=True)
        self.seg_ov_tif = tk.BooleanVar(value=False)
        self.seg_ov_jpg = tk.BooleanVar(value=False)

        head = ttk.Frame(tab)
        head.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Checkbutton(
            head, text="Count cells (3D watershed on the z-stack)", variable=self.seg_enabled
        ).pack(side="left")
        ttk.Label(head, text="  Nuclei channel").pack(side="left", padx=(PAD, 2))
        self.seg_channel_box = ttk.Combobox(
            head, textvariable=self.seg_channel, width=20, state="readonly"
        )
        self.seg_channel_box.pack(side="left")

        ttk.Label(
            tab,
            text=(
                "Counting runs on the full z-stack, not the projection, so nuclei that "
                "overlap in XY but sit at different depths are counted separately."
            ),
            foreground="#555",
            wraplength=900,
            justify="left",
        ).pack(anchor="w", padx=PAD)

        grid = ttk.LabelFrame(tab, text="Parameters")
        grid.pack(fill="x", padx=PAD, pady=PAD)
        rows = [
            ("Smoothing XY (µm)", self.seg_smooth_xy, 0, 20, 0.1),
            ("Smoothing Z (µm)", self.seg_smooth_z, 0, 20, 0.1),
            ("Nucleus radius (µm)", self.seg_radius, 0.5, 50, 0.5),
            ("Seed smoothing (x radius)", self.seg_seed_smooth, 0.0, 2.0, 0.05),
            ("Min volume (µm³)", self.seg_min_vol, 0, 1e6, 10),
            ("Max volume (µm³)", self.seg_max_vol, 0, 1e7, 100),
        ]
        for r, (label, var, lo, hi, inc) in enumerate(rows):
            ttk.Label(grid, text=label).grid(
                row=r % 3, column=(r // 3) * 2, sticky="e", padx=PAD, pady=3
            )
            ttk.Spinbox(
                grid, textvariable=var, from_=lo, to=hi, increment=inc, width=10
            ).grid(row=r % 3, column=(r // 3) * 2 + 1, sticky="w", pady=3)

        ttk.Label(grid, text="Threshold").grid(row=0, column=4, sticky="e", padx=PAD)
        ttk.Combobox(
            grid, textvariable=self.seg_threshold, values=list(THRESHOLD_METHODS), width=11,
            state="readonly",
        ).grid(row=0, column=5, sticky="w")
        ttk.Label(grid, text="value").grid(row=1, column=4, sticky="e", padx=PAD)
        ttk.Spinbox(
            grid, textvariable=self.seg_threshold_value, from_=0, to=65535, width=10
        ).grid(row=1, column=5, sticky="w")
        ttk.Label(grid, text="%ile").grid(row=2, column=4, sticky="e", padx=PAD)
        ttk.Spinbox(
            grid, textvariable=self.seg_threshold_pct, from_=0, to=100, increment=0.5, width=10
        ).grid(row=2, column=5, sticky="w")

        opts = ttk.Frame(tab)
        opts.pack(fill="x", padx=PAD)
        ttk.Checkbutton(opts, text="fill holes", variable=self.seg_fill).pack(side="left")
        ttk.Checkbutton(
            opts, text="drop nuclei touching the XY border", variable=self.seg_border
        ).pack(side="left", padx=PAD)
        ttk.Checkbutton(
            opts, text="save label image", variable=self.seg_save_labels
        ).pack(side="left", padx=PAD)
        ttk.Checkbutton(
            opts, text="save outline overlay", variable=self.seg_save_overlay
        ).pack(side="left", padx=PAD)
        ttk.Label(opts, text="XY downsample").pack(side="left", padx=(PAD, 2))
        ttk.Spinbox(opts, textvariable=self.seg_downsample, from_=1, to=8, width=4).pack(
            side="left"
        )

        annotated = ttk.LabelFrame(tab, text="Annotated counting image")
        annotated.pack(fill="x", padx=PAD, pady=(PAD, 0))
        row_a = ttk.Frame(annotated)
        row_a.pack(anchor="w", padx=PAD, pady=4)
        ttk.Checkbutton(
            row_a, text="number each cell", variable=self.seg_number_cells
        ).pack(side="left")
        ttk.Label(row_a, text="font pt (0 = fit the nuclei)").pack(side="left", padx=(PAD, 2))
        ttk.Spinbox(row_a, textvariable=self.seg_id_font, from_=0, to=72, width=5).pack(
            side="left"
        )
        ttk.Label(row_a, text="   save as").pack(side="left", padx=(PAD, 2))
        for label, var in (
            ("png", self.seg_ov_png), ("tif", self.seg_ov_tif), ("jpg", self.seg_ov_jpg)
        ):
            ttk.Checkbutton(row_a, text=label, variable=var).pack(side="left")
        ttk.Label(
            annotated,
            text=(
                "Nucleus outlines over the nuclei channel, with the total in the corner. "
                "The per-cell numbers match the cell_id column of cells_per_object.csv, so a "
                "cell in the picture can be traced back to its row. Save as tif to zoom in Fiji."
            ),
            foreground="#555",
            wraplength=980,
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=(0, 6))

        tune = ttk.LabelFrame(tab, text="Tune on a crop")
        tune.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        ttk.Label(
            tune,
            text=(
                "Runs the counter on a small crop of the first selected image and draws the "
                "outlines, so you can check the parameters before committing to a full batch.\n"
                "Too many outlines cutting single nuclei in half means the seed smoothing is "
                "too low; nuclei merged into one outline means it is too high."
            ),
            foreground="#555",
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=4)
        row = ttk.Frame(tune)
        row.pack(anchor="w", padx=PAD, pady=4)
        self.tune_size = tk.IntVar(value=400)
        ttk.Label(row, text="Crop size (px)").pack(side="left")
        ttk.Spinbox(row, textvariable=self.tune_size, from_=100, to=1200, increment=50,
                    width=6).pack(side="left", padx=(4, PAD))
        ttk.Button(row, text="Preview cell counting", command=self.preview_counting).pack(
            side="left"
        )
        ttk.Button(
            row, text="Compare seed smoothing", command=self.compare_seed_smoothing
        ).pack(side="left", padx=PAD)

    def _refresh_nuclei_choices(self):
        if not hasattr(self, "seg_channel_box"):
            return
        names = [f"{i + 1}: {p.name.get()}" for i, p in enumerate(self.channel_panels)]
        self.seg_channel_box.configure(values=names)
        if names and self.seg_channel.get() not in names:
            self.seg_channel.set(names[0])

    def _nuclei_index(self) -> int:
        text = self.seg_channel.get()
        try:
            return int(text.split(":", 1)[0]) - 1
        except (ValueError, IndexError):
            return 0

    # -------------------------------------------------------------- tab: output
    def _tab_output(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="4. Output")

        self.out_composite = tk.BooleanVar(value=True)
        self.out_comp_png = tk.BooleanVar(value=True)
        self.out_comp_tif = tk.BooleanVar(value=True)
        self.out_comp_jpg = tk.BooleanVar(value=False)
        self.out_singles = tk.BooleanVar(value=False)
        self.out_iso = tk.BooleanVar(value=False)
        self.out_ortho = tk.BooleanVar(value=False)
        self.out_elev = tk.DoubleVar(value=28.0)
        self.out_azim = tk.DoubleVar(value=35.0)
        self.out_scalebar = tk.BooleanVar(value=True)
        self.out_bar_fraction = tk.DoubleVar(value=0.1)
        self.out_bar_font = tk.IntVar(value=24)
        self.out_csv = tk.BooleanVar(value=True)
        self.out_per_object = tk.BooleanVar(value=False)

        img = ttk.LabelFrame(tab, text="Images")
        img.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Checkbutton(img, text="composite", variable=self.out_composite).grid(
            row=0, column=0, sticky="w", padx=PAD, pady=3
        )
        for c, (label, var) in enumerate(
            (("png", self.out_comp_png), ("tif", self.out_comp_tif), ("jpg", self.out_comp_jpg))
        ):
            ttk.Checkbutton(img, text=label, variable=var).grid(
                row=0, column=1 + c, sticky="w", pady=3
            )
        ttk.Checkbutton(
            img, text="single-channel images (per channel, ticked on the Channels tab)",
            variable=self.out_singles,
        ).grid(row=1, column=0, columnspan=4, sticky="w", padx=PAD, pady=3)

        views = ttk.LabelFrame(tab, text="3D views of the chip")
        views.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Checkbutton(
            views, text="isometric slab render", variable=self.out_iso
        ).grid(row=0, column=0, sticky="w", padx=PAD, pady=3)
        ttk.Label(views, text="elevation°").grid(row=0, column=1, sticky="e")
        ttk.Spinbox(views, textvariable=self.out_elev, from_=0, to=89, width=6).grid(
            row=0, column=2, sticky="w"
        )
        ttk.Label(views, text="azimuth°").grid(row=0, column=3, sticky="e", padx=(PAD, 0))
        ttk.Spinbox(views, textvariable=self.out_azim, from_=0, to=360, width=6).grid(
            row=0, column=4, sticky="w"
        )
        ttk.Checkbutton(
            views, text="orthogonal panel (XY + XZ + YZ)", variable=self.out_ortho
        ).grid(row=1, column=0, sticky="w", padx=PAD, pady=3)

        bar = ttk.LabelFrame(tab, text="Scale bar")
        bar.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Checkbutton(bar, text="burn in a scale bar", variable=self.out_scalebar).grid(
            row=0, column=0, sticky="w", padx=PAD, pady=3
        )
        ttk.Label(bar, text="width as fraction of image").grid(row=0, column=1, sticky="e")
        ttk.Spinbox(
            bar, textvariable=self.out_bar_fraction, from_=0.02, to=0.5, increment=0.01, width=6
        ).grid(row=0, column=2, sticky="w")
        ttk.Label(bar, text="font pt").grid(row=0, column=3, sticky="e", padx=(PAD, 0))
        ttk.Spinbox(bar, textvariable=self.out_bar_font, from_=8, to=96, width=6).grid(
            row=0, column=4, sticky="w"
        )

        tables = ttk.LabelFrame(tab, text="Tables")
        tables.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Checkbutton(
            tables, text="statistics.csv (one row per image)", variable=self.out_csv
        ).pack(anchor="w", padx=PAD, pady=3)
        ttk.Checkbutton(
            tables, text="cells_per_object.csv (one row per counted nucleus)",
            variable=self.out_per_object,
        ).pack(anchor="w", padx=PAD, pady=3)

        ttk.Label(
            tab,
            text=(
                "Output layout:\n"
                "  composites/      Composite_<image>.png/.tif\n"
                "  channels/<name>/ <image>_<channel>.png\n"
                "  3d_views/        <image>_isometric.png, <image>_orthogonal.png\n"
                "  segmentation/    <image>_nuclei_overlay.png, <image>_nuclei_labels.tif\n"
                "  statistics.csv, settings_used.json, processing_log.txt"
            ),
            foreground="#555",
            justify="left",
            font=("TkFixedFont", 9),
        ).pack(anchor="w", padx=PAD, pady=PAD)

    # ----------------------------------------------------------------- tab: run
    def _tab_run(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="5. Run")

        bar = ttk.Frame(tab)
        bar.pack(fill="x", padx=PAD, pady=PAD)
        self.run_button = ttk.Button(bar, text="Run batch", command=self.run_batch)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(
            bar, text="Stop", command=self.stop_batch, state="disabled"
        )
        self.stop_button.pack(side="left", padx=PAD)
        ttk.Button(bar, text="Check settings", command=self.check_settings).pack(side="left")
        ttk.Button(bar, text="Clear log", command=lambda: self.log_text.delete("1.0", "end")).pack(
            side="right"
        )

        self.log_text = tk.Text(tab, wrap="none", height=30, font=("TkFixedFont", 9))
        yscroll = ttk.Scrollbar(tab, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=yscroll.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(PAD, 0), pady=PAD)
        yscroll.pack(side="left", fill="y", pady=PAD)


    # --------------------------------------------------------------- tab: plots
    def _tab_plots(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="6. Plots")

        self.plot_csv = tk.StringVar()
        self.plot_type = tk.StringVar(value="compare groups")
        self.plot_metric = tk.StringVar()
        self.plot_metric2 = tk.StringVar()
        self.plot_group = tk.StringVar(value=DEFAULT_GROUP_PATTERN)
        self.plot_frame_data = None
        self.manual_groups: Dict[str, str] = {}

        source = ttk.LabelFrame(tab, text="Statistics CSV")
        source.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Entry(source, textvariable=self.plot_csv, width=76).grid(
            row=0, column=0, sticky="we", padx=PAD, pady=4
        )
        ttk.Button(source, text="Browse...", command=self._pick_plot_csv).grid(
            row=0, column=1, padx=(0, PAD)
        )
        ttk.Button(
            source, text="From output folder", command=self._plot_csv_from_output
        ).grid(row=0, column=2, padx=(0, PAD))
        source.columnconfigure(0, weight=1)
        ttk.Label(
            source,
            text="Any statistics.csv or cells_per_object.csv works, including one from an earlier session.",
            foreground="#555",
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=PAD, pady=(0, 4))

        controls = ttk.LabelFrame(tab, text="Plot")
        controls.pack(fill="x", padx=PAD, pady=(0, PAD))
        ttk.Label(controls, text="Type").grid(row=0, column=0, sticky="e", padx=(PAD, 4), pady=4)
        type_box = ttk.Combobox(
            controls, textvariable=self.plot_type, values=list(PLOT_TYPES),
            width=15, state="readonly",
        )
        type_box.grid(row=0, column=1, sticky="w")
        type_box.bind("<<ComboboxSelected>>", lambda e: self._sync_plot_controls())

        ttk.Label(controls, text="Value").grid(row=0, column=2, sticky="e", padx=(PAD, 4))
        self.metric_box = ttk.Combobox(
            controls, textvariable=self.plot_metric, width=38, state="readonly"
        )
        self.metric_box.grid(row=0, column=3, sticky="w")

        self.metric2_label = ttk.Label(controls, text="vs (x axis)")
        self.metric2_label.grid(row=0, column=4, sticky="e", padx=(PAD, 4))
        self.metric2_box = ttk.Combobox(
            controls, textvariable=self.plot_metric2, width=32, state="readonly"
        )
        self.metric2_box.grid(row=0, column=5, sticky="w", padx=(0, PAD))

        ttk.Label(controls, text="Group by").grid(row=1, column=0, sticky="e", padx=(PAD, 4), pady=4)
        group_entry = ttk.Entry(controls, textvariable=self.plot_group, width=22)
        group_entry.grid(row=1, column=1, sticky="w")
        ttk.Label(
            controls,
            text="regex on the image name, e.g. (dynamic|static) or chip(\\d+); blank = no grouping",
            foreground="#555",
        ).grid(row=1, column=2, columnspan=4, sticky="w", padx=(PAD, 0))

        buttons = ttk.Frame(tab)
        buttons.pack(fill="x", padx=PAD)
        ttk.Button(buttons, text="Draw plot", command=self.draw_plot).pack(side="left")
        ttk.Button(buttons, text="Save figure...", command=self.save_plot).pack(
            side="left", padx=PAD
        )
        ttk.Button(buttons, text="Show table", command=self.show_table).pack(side="left")
        ttk.Button(buttons, text="Group by hand...", command=self.edit_manual_groups).pack(
            side="left", padx=PAD
        )
        self.plot_hint = ttk.Label(buttons, text="", foreground="#555")
        self.plot_hint.pack(side="left", padx=PAD)

        self.plot_host = ttk.Frame(tab)
        self.plot_host.pack(fill="both", expand=True, padx=PAD, pady=PAD)
        self._plot_canvas = None
        self._plot_figure = None

    def _pick_plot_csv(self):
        path = filedialog.askopenfilename(
            title="Open a statistics CSV",
            filetypes=[("CSV", "*.csv"), ("All files", "*")],
            initialdir=self.output_dir.get() or os.getcwd(),
        )
        if path:
            self.plot_csv.set(path)
            self._load_plot_csv()

    def _plot_csv_from_output(self):
        folder = self.output_dir.get()
        if not folder:
            messagebox.showinfo("No output folder", "Choose an output folder first.")
            return
        path = os.path.join(folder, "statistics.csv")
        if not os.path.isfile(path):
            messagebox.showinfo(
                "Not found", f"No statistics.csv in\n{folder}\n\nRun a batch first."
            )
            return
        self.plot_csv.set(path)
        self._load_plot_csv()

    def _load_plot_csv(self):
        from . import plots as plotting

        try:
            frame = plotting.load_table(self.plot_csv.get())
        except plotting.PlotDataError as exc:
            messagebox.showerror("Could not load CSV", str(exc))
            return
        self.plot_frame_data = frame
        metrics = plotting.numeric_columns(frame)
        if not metrics:
            messagebox.showwarning("Nothing to plot", "That CSV has no numeric columns.")
            return
        self.metric_box.configure(values=metrics)
        self.metric2_box.configure(values=metrics)
        if self.plot_metric.get() not in metrics:
            self.plot_metric.set(metrics[0])
        if self.plot_metric2.get() not in metrics:
            self.plot_metric2.set(metrics[1] if len(metrics) > 1 else metrics[0])
        # A per-object table is a distribution, not one row per image.
        if "cell_id" in frame.columns:
            self.plot_type.set("distribution")
        self._sync_plot_controls()
        self.plot_hint.configure(
            text=f"{len(frame)} rows, {len(metrics)} numeric columns"
        )
        self.status.set(f"loaded {os.path.basename(self.plot_csv.get())}")

    def _sync_plot_controls(self):
        scatter = self.plot_type.get() == "scatter"
        state = "readonly" if scatter else "disabled"
        self.metric2_box.configure(state=state)
        self.metric2_label.configure(foreground="#000" if scatter else "#999")

    def draw_plot(self):
        from . import plots as plotting

        if self.plot_frame_data is None:
            if self.plot_csv.get():
                self._load_plot_csv()
            if self.plot_frame_data is None:
                messagebox.showinfo("No data", "Load a statistics CSV first.")
                return
        try:
            figure, order = plotting.build_figure(
                self.plot_frame_data,
                self.plot_type.get(),
                self.plot_metric.get(),
                self.plot_metric2.get(),
                self.plot_group.get(),
                self.manual_groups,
            )
        except plotting.PlotDataError as exc:
            messagebox.showwarning("Cannot draw that plot", str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Plot failed", f"{type(exc).__name__}: {exc}")
            return
        self._show_figure(figure, order)

    def _show_figure(self, figure, order):
        from matplotlib.backends.backend_tkagg import (
            FigureCanvasTkAgg,
            NavigationToolbar2Tk,
        )

        for child in self.plot_host.winfo_children():
            child.destroy()

        canvas = FigureCanvasTkAgg(figure, master=self.plot_host)
        toolbar = NavigationToolbar2Tk(canvas, self.plot_host, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side="bottom", fill="x")
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()
        self._plot_canvas = canvas
        self._plot_figure = figure
        self._attach_hover(figure, canvas, order)

    def _attach_hover(self, figure, canvas, order):
        """Name the point under the cursor -- the rows are images, not numbers."""
        axes = figure.get_axes()
        if not axes or not order:
            return
        axis = axes[0]
        annotation = axis.annotate(
            "",
            xy=(0, 0),
            xytext=(12, 12),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", fc="#ffffff", ec="#c9c9c2", lw=1),
            fontsize=9,
            zorder=10,
            annotation_clip=False,
        )
        annotation.set_visible(False)

        def on_move(event):
            if event.inaxes is not axis:
                if annotation.get_visible():
                    annotation.set_visible(False)
                    canvas.draw_idle()
                return
            hit = None
            for collection in axis.collections:
                found, info = collection.contains(event)
                if found and len(info.get("ind", [])):
                    hit = int(info["ind"][0])
                    break
            if hit is None:
                for index, patch in enumerate(axis.patches):
                    if patch.contains(event)[0]:
                        hit = index
                        break
            if hit is None or hit >= len(order):
                if annotation.get_visible():
                    annotation.set_visible(False)
                    canvas.draw_idle()
                return
            annotation.xy = (event.xdata, event.ydata)
            annotation.set_text(order[hit])
            annotation.set_visible(True)
            canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", on_move)

    def save_plot(self):
        if self._plot_figure is None:
            messagebox.showinfo("No plot", "Draw a plot first.")
            return
        path = filedialog.asksaveasfilename(
            title="Save figure", defaultextension=".png",
            filetypes=[("PNG", "*.png"), ("PDF", "*.pdf"), ("SVG", "*.svg")],
            initialdir=self.output_dir.get() or os.getcwd(),
        )
        if not path:
            return
        self._plot_figure.savefig(
            path, dpi=200, facecolor=self._plot_figure.get_facecolor()
        )
        self.status.set(f"saved {os.path.basename(path)}")

    def edit_manual_groups(self):
        """Assign images to groups by hand; these override the group pattern."""
        from . import plots as plotting

        if self.plot_frame_data is None:
            messagebox.showinfo("No data", "Load a statistics CSV first.")
            return
        frame = self.plot_frame_data
        names = [str(v) for v in frame[plotting.label_column(frame)]]

        window = tk.Toplevel(self.master)
        window.title("Group images by hand")
        window.geometry("900x580")

        ttk.Label(
            window,
            text=(
                "Select one or more images, type a group name and press Assign. "
                "Images you assign here override the group pattern; the rest keep "
                "whatever the pattern gives them."
            ),
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=PAD)

        columns = ("image", "group")
        tree = ttk.Treeview(window, columns=columns, show="headings", selectmode="extended")
        tree.heading("image", text="image")
        tree.heading("group", text="group (blank = from pattern)")
        tree.column("image", width=520, anchor="w")
        tree.column("group", width=180, anchor="w")
        scroll = ttk.Scrollbar(window, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="top", fill="both", expand=True, padx=(PAD, 0))
        scroll.place(in_=tree, relx=1.0, relheight=1.0, bordermode="outside")

        def refresh():
            tree.delete(*tree.get_children())
            for name in names:
                tree.insert("", "end", values=(name, self.manual_groups.get(name, "")))

        refresh()

        bar = ttk.Frame(window)
        bar.pack(fill="x", padx=PAD, pady=PAD)
        group_var = tk.StringVar()
        ttk.Label(bar, text="Group").pack(side="left")
        entry = ttk.Entry(bar, textvariable=group_var, width=20)
        entry.pack(side="left", padx=(4, PAD))

        def assign():
            label = group_var.get().strip()
            chosen = [tree.item(i, "values")[0] for i in tree.selection()]
            if not chosen:
                messagebox.showinfo("Nothing selected", "Select some images first.")
                return
            for name in chosen:
                if label:
                    self.manual_groups[name] = label
                else:
                    self.manual_groups.pop(name, None)
            refresh()

        def clear_all():
            self.manual_groups.clear()
            refresh()

        def prefill():
            """Seed every row from the current pattern, ready to be edited."""
            try:
                groups = plotting.derive_groups(frame, self.plot_group.get())
            except plotting.PlotDataError as exc:
                messagebox.showwarning("Bad pattern", str(exc))
                return
            if groups is None:
                messagebox.showinfo("No match", "The pattern does not match any image.")
                return
            for name, group in zip(names, groups):
                self.manual_groups[name] = str(group)
            refresh()

        ttk.Button(bar, text="Assign to selected", command=assign).pack(side="left")
        ttk.Button(bar, text="Clear selected", command=lambda: (group_var.set(""), assign())).pack(
            side="left", padx=PAD
        )
        ttk.Button(bar, text="Fill from pattern", command=prefill).pack(side="left")
        ttk.Button(bar, text="Clear all", command=clear_all).pack(side="left", padx=PAD)
        ttk.Button(bar, text="Done", command=window.destroy).pack(side="right")
        entry.focus_set()

    def show_table(self):
        """The numbers behind the plot, for anyone the colours do not reach."""
        if self.plot_frame_data is None:
            messagebox.showinfo("No data", "Load a statistics CSV first.")
            return
        frame = self.plot_frame_data
        window = tk.Toplevel(self.master)
        window.title(f"Table - {os.path.basename(self.plot_csv.get())}")
        window.geometry("1000x600")

        columns = list(frame.columns)
        tree = ttk.Treeview(window, columns=columns, show="headings")
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=150, stretch=False, anchor="w")
        for _, row in frame.iterrows():
            values = [
                f"{v:,.4g}" if isinstance(v, float) else str(v) for v in row.tolist()
            ]
            tree.insert("", "end", values=values)

        yscroll = ttk.Scrollbar(window, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(window, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="we")
        window.rowconfigure(0, weight=1)
        window.columnconfigure(0, weight=1)

    # ------------------------------------------------------------- settings I/O
    def collect_settings(self) -> Settings:
        settings = Settings()
        settings.input_dir = self.input_dir.get().strip()
        settings.output_dir = self.output_dir.get().strip()
        selected = [self.file_list.get(i) for i in self.file_list.curselection()]
        settings.selected_files = selected
        settings.max_workers = int(self.max_workers.get())
        settings.batch_sample_images = int(self.batch_sample.get())
        settings.image_groups = dict(self.manual_groups)
        settings.channels = [p.to_settings() for p in self.channel_panels]

        roi = settings.reference_roi
        roi.x0, roi.y0 = float(self.roi_x0.get()), float(self.roi_y0.get())
        roi.x1, roi.y1 = float(self.roi_x1.get()), float(self.roi_y1.get())
        roi.scope = self.roi_scope.get()
        roi.source_image = self.roi_source.get()

        analysis = settings.analysis_roi
        analysis.enabled = bool(self.an_enabled.get())
        analysis.x0, analysis.y0 = float(self.an_x0.get()), float(self.an_y0.get())
        analysis.x1, analysis.y1 = float(self.an_x1.get()), float(self.an_y1.get())
        analysis.show_on_overlay = bool(self.an_show.get())

        seg = settings.segmentation
        seg.enabled = bool(self.seg_enabled.get())
        seg.channel_index = self._nuclei_index()
        seg.smoothing_xy_um = float(self.seg_smooth_xy.get())
        seg.smoothing_z_um = float(self.seg_smooth_z.get())
        seg.threshold_method = self.seg_threshold.get()
        seg.threshold_value = float(self.seg_threshold_value.get())
        seg.threshold_percentile = float(self.seg_threshold_pct.get())
        seg.min_volume_um3 = float(self.seg_min_vol.get())
        seg.max_volume_um3 = float(self.seg_max_vol.get())
        seg.nucleus_radius_um = float(self.seg_radius.get())
        seg.seed_smoothing_factor = float(self.seg_seed_smooth.get())
        seg.fill_holes = bool(self.seg_fill.get())
        seg.exclude_xy_border = bool(self.seg_border.get())
        seg.downsample_xy = int(self.seg_downsample.get())
        seg.save_label_image = bool(self.seg_save_labels.get())
        seg.save_overlay = bool(self.seg_save_overlay.get())
        seg.annotate_cell_ids = bool(self.seg_number_cells.get())
        seg.cell_id_font_pt = int(self.seg_id_font.get())
        seg.overlay_formats = [
            fmt
            for fmt, var in (
                ("png", self.seg_ov_png), ("tif", self.seg_ov_tif), ("jpg", self.seg_ov_jpg)
            )
            if var.get()
        ] or ["png"]

        out = settings.output
        out.save_composite = bool(self.out_composite.get())
        out.composite_formats = [
            fmt
            for fmt, var in (
                ("png", self.out_comp_png), ("tif", self.out_comp_tif), ("jpg", self.out_comp_jpg)
            )
            if var.get()
        ] or ["png"]
        out.save_single_channels = bool(self.out_singles.get())
        out.save_isometric_3d = bool(self.out_iso.get())
        out.save_orthogonal_view = bool(self.out_ortho.get())
        out.isometric_elevation_deg = float(self.out_elev.get())
        out.isometric_azimuth_deg = float(self.out_azim.get())
        out.scale_bar = bool(self.out_scalebar.get())
        out.scale_bar_fraction = float(self.out_bar_fraction.get())
        out.scale_bar_font_pt = int(self.out_bar_font.get())
        out.projection = self.projection.get()
        out.write_statistics_csv = bool(self.out_csv.get())
        out.write_per_object_csv = bool(self.out_per_object.get())
        return settings

    def apply_settings(self, settings: Settings):
        self.input_dir.set(settings.input_dir)
        self.output_dir.set(settings.output_dir)
        self.max_workers.set(settings.max_workers)
        self.batch_sample.set(settings.batch_sample_images)
        self.manual_groups = dict(settings.image_groups)
        self.projection.set(settings.output.projection)
        self._refresh_files()

        self.n_channels.set(max(1, len(settings.channels)))
        for panel in self.channel_panels:
            panel.destroy()
        self.channel_panels = [
            ChannelPanel(self.channel_container.inner, i, ch)
            for i, ch in enumerate(settings.channels)
        ]
        self._refresh_nuclei_choices()

        roi = settings.reference_roi
        self.roi_x0.set(roi.x0)
        self.roi_y0.set(roi.y0)
        self.roi_x1.set(roi.x1)
        self.roi_y1.set(roi.y1)
        self.roi_scope.set(roi.scope)
        self.roi_source.set(roi.source_image)

        analysis = settings.analysis_roi
        self.an_enabled.set(analysis.enabled)
        self.an_x0.set(analysis.x0)
        self.an_y0.set(analysis.y0)
        self.an_x1.set(analysis.x1)
        self.an_y1.set(analysis.y1)
        self.an_show.set(analysis.show_on_overlay)
        self._sync_analysis_label()

        seg = settings.segmentation
        self.seg_enabled.set(seg.enabled)
        names = self.seg_channel_box.cget("values")
        if names and 0 <= seg.channel_index < len(names):
            self.seg_channel.set(names[seg.channel_index])
        self.seg_smooth_xy.set(seg.smoothing_xy_um)
        self.seg_smooth_z.set(seg.smoothing_z_um)
        self.seg_threshold.set(seg.threshold_method)
        self.seg_threshold_value.set(seg.threshold_value)
        self.seg_threshold_pct.set(seg.threshold_percentile)
        self.seg_min_vol.set(seg.min_volume_um3)
        self.seg_max_vol.set(seg.max_volume_um3)
        self.seg_radius.set(seg.nucleus_radius_um)
        self.seg_seed_smooth.set(seg.seed_smoothing_factor)
        self.seg_fill.set(seg.fill_holes)
        self.seg_border.set(seg.exclude_xy_border)
        self.seg_downsample.set(seg.downsample_xy)
        self.seg_save_labels.set(seg.save_label_image)
        self.seg_save_overlay.set(seg.save_overlay)
        self.seg_number_cells.set(seg.annotate_cell_ids)
        self.seg_id_font.set(seg.cell_id_font_pt)
        self.seg_ov_png.set("png" in seg.overlay_formats)
        self.seg_ov_tif.set(any(f in seg.overlay_formats for f in ("tif", "tiff")))
        self.seg_ov_jpg.set(any(f in seg.overlay_formats for f in ("jpg", "jpeg")))

        out = settings.output
        self.out_composite.set(out.save_composite)
        self.out_comp_png.set("png" in out.composite_formats)
        self.out_comp_tif.set(any(f in out.composite_formats for f in ("tif", "tiff")))
        self.out_comp_jpg.set(any(f in out.composite_formats for f in ("jpg", "jpeg")))
        self.out_singles.set(out.save_single_channels)
        self.out_iso.set(out.save_isometric_3d)
        self.out_ortho.set(out.save_orthogonal_view)
        self.out_elev.set(out.isometric_elevation_deg)
        self.out_azim.set(out.isometric_azimuth_deg)
        self.out_scalebar.set(out.scale_bar)
        self.out_bar_fraction.set(out.scale_bar_fraction)
        self.out_bar_font.set(out.scale_bar_font_pt)
        self.out_csv.set(out.write_statistics_csv)
        self.out_per_object.set(out.write_per_object_csv)

    def load_settings(self):
        path = filedialog.askopenfilename(
            title="Load settings", filetypes=[("JSON", "*.json"), ("All files", "*")]
        )
        if not path:
            return
        try:
            self.apply_settings(Settings.load(path))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not load settings", str(exc))
            return
        self.status.set(f"loaded {os.path.basename(path)}")

    def save_settings(self):
        path = filedialog.asksaveasfilename(
            title="Save settings", defaultextension=".json",
            initialfile="settings.json", filetypes=[("JSON", "*.json")],
        )
        if not path:
            return
        try:
            self.collect_settings().save(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Could not save settings", str(exc))
            return
        self.status.set(f"saved {os.path.basename(path)}")

    def check_settings(self):
        problems = self.collect_settings().validate()
        if problems:
            messagebox.showwarning(
                "Settings need attention", "\n".join(f"- {p}" for p in problems)
            )
        else:
            messagebox.showinfo("Settings", "Settings look consistent.")

    # ------------------------------------------------------------------ previews
    def _first_selected_image(self) -> Optional[str]:
        images = list_images(self.input_dir.get())
        if not images:
            messagebox.showinfo("No images", "Choose an input folder with images first.")
            return None
        selection = self.file_list.curselection()
        if selection:
            name = self.file_list.get(selection[0])
            for path in images:
                if os.path.basename(path) == name:
                    return path
        return images[0]

    def preview_composite(self):
        path = self._first_selected_image()
        if not path:
            return
        settings = self.collect_settings()
        self._run_async(
            "building preview",
            lambda: _build_composite_preview(path, settings),
            lambda result: _show_image_window(
                self.master, result, f"Composite preview - {os.path.basename(path)}"
            ),
        )

    def preview_counting(self):
        path = self._first_selected_image()
        if not path:
            return
        settings = self.collect_settings()
        size = int(self.tune_size.get())
        self._run_async(
            "counting cells on a crop",
            lambda: _build_counting_preview(path, settings, size),
            lambda result: _show_image_window(
                self.master, result[0], f"Cell counting - {result[1]}"
            ),
        )

    def compare_seed_smoothing(self):
        path = self._first_selected_image()
        if not path:
            return
        settings = self.collect_settings()
        size = int(self.tune_size.get())
        self._run_async(
            "comparing seed smoothing values",
            lambda: _build_seed_comparison(path, settings, size),
            lambda result: _show_image_window(
                self.master, result, "Seed smoothing comparison"
            ),
        )

    def pick_reference_roi(self):
        path = self._first_selected_image()
        if not path:
            return
        settings = self.collect_settings()
        index = self._nuclei_index()
        self._run_async(
            "loading image for ROI selection",
            lambda: _load_preview_projection(path, settings, index),
            lambda result: self._open_roi_window(result, path, "reference"),
        )

    def pick_analysis_roi(self):
        path = self._first_selected_image()
        if not path:
            return
        settings = self.collect_settings()
        index = self._nuclei_index()
        self._run_async(
            "loading image to choose the analysis region",
            lambda: _load_preview_projection(path, settings, index),
            lambda result: self._open_roi_window(result, path, "analysis"),
        )

    def _open_roi_window(self, projection: np.ndarray, path: str, purpose: str = "reference"):
        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            from matplotlib.widgets import RectangleSelector
        except ImportError:
            messagebox.showerror("matplotlib missing", "Install matplotlib to pick an ROI.")
            return

        analysis = purpose == "analysis"
        window = tk.Toplevel(self.master)
        window.title(
            f"{'Analysis region' if analysis else 'Reference area'} - {os.path.basename(path)}"
        )
        window.geometry("820x780")

        ttk.Label(
            window,
            text=(
                "Drag a rectangle over the part of the field to measure.\n"
                "Counting, area, volume and intensity use only this region, for every "
                "image in the folder. The exported images stay the full field."
                if analysis
                else "Drag a rectangle over a region that is representative of the signal.\n"
                "The saturation limits for every channel set to 'roi_reference' are taken "
                "from the percentiles inside it."
            ),
            justify="left",
        ).pack(anchor="w", padx=PAD, pady=PAD)

        figure = Figure(figsize=(7, 6.4), dpi=100)
        axis = figure.add_subplot(111)
        vmax = float(np.percentile(projection, 99.7)) or 1.0
        axis.imshow(projection, cmap="gray", vmin=float(projection.min()), vmax=vmax)
        axis.set_title("drag to select")
        axis.axis("off")
        canvas = FigureCanvasTkAgg(figure, master=window)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=PAD)

        height, width = projection.shape
        chosen = {}
        current = self.collect_settings().analysis_roi if analysis else None
        if analysis and current is not None:
            # Start from whatever is configured, so the box can be nudged.
            axis.add_patch(
                __import__("matplotlib").patches.Rectangle(
                    (current.x0 * width, current.y0 * height),
                    (current.x1 - current.x0) * width,
                    (current.y1 - current.y0) * height,
                    fill=False, edgecolor="#39d3ff", linestyle="--", linewidth=1.2,
                )
            )

        def on_select(eclick, erelease):
            x0, x1 = sorted((eclick.xdata, erelease.xdata))
            y0, y1 = sorted((eclick.ydata, erelease.ydata))
            chosen.update(
                x0=max(0.0, x0 / width), x1=min(1.0, x1 / width),
                y0=max(0.0, y0 / height), y1=min(1.0, y1 / height),
            )
            label.configure(
                text=f"selected x {chosen['x0']:.3f}-{chosen['x1']:.3f}, "
                     f"y {chosen['y0']:.3f}-{chosen['y1']:.3f}"
            )

        selector = RectangleSelector(
            axis, on_select, useblit=True, button=[1], interactive=True,
            props=dict(facecolor="none", edgecolor="yellow", linewidth=1.5),
        )
        window._selector = selector  # keep a reference alive

        label = ttk.Label(window, text="nothing selected yet")
        label.pack(anchor="w", padx=PAD)

        def accept():
            if not chosen:
                messagebox.showinfo("No selection", "Drag a rectangle first.")
                return
            if analysis:
                self.an_x0.set(chosen["x0"])
                self.an_x1.set(chosen["x1"])
                self.an_y0.set(chosen["y0"])
                self.an_y1.set(chosen["y1"])
                self.an_enabled.set(True)
                self._sync_analysis_label()
                self.status.set("analysis region set; measurements will use it")
            else:
                self.roi_x0.set(chosen["x0"])
                self.roi_x1.set(chosen["x1"])
                self.roi_y0.set(chosen["y0"])
                self.roi_y1.set(chosen["y1"])
                self.roi_source.set(os.path.basename(path))
                for panel in self.channel_panels:
                    if panel.limit_mode.get() == "auto_percentile":
                        panel.limit_mode.set("roi_reference")
                        panel._sync_limit_state()
                self.status.set("reference area set; channels switched to roi_reference")
            window.destroy()

        buttons = ttk.Frame(window)
        buttons.pack(fill="x", padx=PAD, pady=PAD)
        ttk.Button(buttons, text="Use this area", command=accept).pack(side="left")
        ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="left", padx=PAD)

    # ------------------------------------------------------------ async plumbing
    def _run_async(self, description: str, work, on_success):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "Something is already running.")
            return
        self.status.set(description + "...")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)

        def runner():
            try:
                result = work()
                self.log_queue.put(("done", description, result, on_success))
            except Exception as exc:  # noqa: BLE001
                self.log_queue.put(("error", description, exc, traceback.format_exc()))

        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()

    def run_batch(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Busy", "A run is already in progress.")
            return
        settings = self.collect_settings()
        problems = settings.validate()
        if not settings.output_dir:
            problems.append("no output folder selected")
        if problems:
            messagebox.showwarning(
                "Settings need attention", "\n".join(f"- {p}" for p in problems)
            )
            return
        if not settings.input_dir:
            messagebox.showwarning("No input", "Choose an input folder.")
            return

        self.nb.select(4)
        self.stop_flag.clear()
        self.run_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        n_total = len(settings.selected_files) or len(list_images(settings.input_dir))
        self.progress.configure(mode="determinate", maximum=max(1, n_total), value=0)
        self._done_count = 0

        from .pipeline import run_batch as run_it

        def runner():
            try:
                results = run_it(
                    settings,
                    progress=lambda m: self.log_queue.put(("log", m)),
                    on_result=lambda r: self.log_queue.put(("tick", r)),
                    should_stop=self.stop_flag.is_set,
                )
                self.log_queue.put(("finished", results, settings.output_dir))
            except BaseException as exc:  # noqa: BLE001
                # BaseException on purpose: CancelledError and friends do not
                # derive from Exception, and a runner thread that dies without
                # posting anything leaves the window looking frozen.
                self.log_queue.put(("error", "batch", exc, traceback.format_exc()))

        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()

    def stop_batch(self):
        self.stop_flag.set()
        # Acknowledge the click straight away: the images already running have
        # to finish first, which on this data is up to a minute, and without a
        # visible change the window just looks stuck.
        self.stop_button.configure(state="disabled")
        self.status.set("stopping - waiting for the images already running to finish...")
        self._append_log(
            "stop requested: no new images will be started; the ones already "
            "running will finish first"
        )

    def on_close(self):
        """Closing the window must not leave worker processes behind.

        The pool's children are separate processes: if the interpreter goes
        away without shutting the pool down they survive, holding hundreds of
        MB each and waiting for a parent that is gone.
        """
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askokcancel(
                "Quit",
                "A batch is still running.\n\n"
                "Quit anyway? The images already being processed are abandoned; "
                "anything finished is already written to the output folder.",
            ):
                return
            self.stop_flag.set()
            self.status.set("stopping workers...")
            self.master.update_idletasks()
            # Give the pool a moment to unwind before the interpreter exits.
            self.worker.join(timeout=5.0)
        self.master.destroy()

    def _append_log(self, message: str):
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")

    def _drain_log(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    self._append_log(item[1])
                elif kind == "tick":
                    self._done_count = getattr(self, "_done_count", 0) + 1
                    self.progress.configure(value=self._done_count)
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.status.set(f"{item[1]}: done")
                    item[3](item[2])
                elif kind == "finished":
                    results, output_dir = item[1], item[2]
                    self.progress.stop()
                    failed = [r for r in results if not r.ok]
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    summary = (
                        f"finished: {len(results) - len(failed)} ok, {len(failed)} failed"
                    )
                    self._append_log(summary)
                    self.status.set(summary)
                    messagebox.showinfo(
                        "Batch finished", f"{summary}\n\nOutput written to:\n{output_dir}"
                    )
                elif kind == "error":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=0)
                    self.run_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self._append_log(f"ERROR during {item[1]}: {item[2]}")
                    self._append_log(item[3])
                    self.status.set(f"error during {item[1]}")
                    messagebox.showerror(f"Error during {item[1]}", str(item[2]))
        except queue.Empty:
            pass
        self.after(120, self._drain_log)


# ------------------------------------------------------- preview worker helpers
def _load_preview_projection(path: str, settings: Settings, index: int) -> np.ndarray:
    from . import contrast
    from .imageio import StackReader

    with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
        volume = reader.channel_volume(index)
        info = reader.info
        projection = contrast.project(volume, settings.output.projection)
        channel = settings.channels[index] if index < len(settings.channels) else None
        if channel and channel.subtract_background:
            projection = contrast.subtract_background(
                projection, channel.background_radius_um, info.pixel_size_um
            )
    return np.asarray(projection, dtype=np.float32)


def _build_composite_preview(path: str, settings: Settings, max_side: int = 900) -> np.ndarray:
    """Composite of the whole field, downsampled to keep the preview quick."""
    from . import contrast, render
    from .imageio import StackReader

    layers = []
    with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
        info = reader.info
        assert info is not None
        step = max(1, int(np.ceil(max(info.height, info.width) / max_side)))
        for index, channel in enumerate(settings.channels):
            if index >= info.n_channels or not channel.include_in_composite:
                continue
            volume = reader.channel_volume(index)
            projection = contrast.project(volume, settings.output.projection)
            del volume
            if channel.subtract_background:
                projection = contrast.subtract_background(
                    projection, channel.background_radius_um, info.pixel_size_um
                )
            projection = projection[::step, ::step]
            vmin, vmax = contrast.resolve_limits(
                channel, projection, roi=settings.reference_roi
            )
            normalised = contrast.apply_display_transform(
                projection, vmin, vmax, channel.display_transform, channel.gamma
            )
            layers.append(contrast.colorize(normalised, channel.color))
        if not layers:
            raise ValueError("no channels are included in the composite")
        composite = contrast.merge_composite(layers)
        composite = render.draw_scale_bar(
            composite, info.pixel_size_um * step, settings.output
        )
    return contrast.to_uint8(composite)


def _counting_crop(path: str, settings: Settings, size: int):
    """Centre crop of the nuclei channel, plus its calibration."""
    from .imageio import StackReader

    with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
        info = reader.info
        assert info is not None
        index = min(settings.segmentation.channel_index, info.n_channels - 1)
        volume = reader.channel_volume(index)
        size = max(64, min(int(size), info.height, info.width))
        r0 = max(0, (info.height - size) // 2)
        c0 = max(0, (info.width - size) // 2)
        crop = np.ascontiguousarray(volume[:, r0 : r0 + size, c0 : c0 + size])
        del volume
        return crop, info.pixel_size_um, info.z_step_um


def _outline_panel(crop, labels, pixel_size_um) -> np.ndarray:
    from skimage.segmentation import find_boundaries

    from . import contrast

    projection = crop.max(axis=0).astype(np.float32)
    lo, hi = contrast.limits_from_percentiles(projection, 0.5, 99.7)
    base = contrast.apply_display_transform(projection, lo, hi, "linear")
    rgb = np.stack([base * 0.35, base * 0.55, base], axis=-1)
    flat = labels.max(axis=0) if labels.ndim == 3 else labels
    rgb[find_boundaries(flat, mode="outer")] = (1.0, 1.0, 0.0)
    return rgb


def _build_counting_preview(path: str, settings: Settings, size: int):
    from .render import _draw_text
    from .segmentation import segment_nuclei
    from . import contrast

    crop, pixel_size, z_step = _counting_crop(path, settings, size)
    result = segment_nuclei(crop, settings.segmentation, pixel_size, z_step)
    rgb = _outline_panel(crop, result.labels, pixel_size)

    if settings.segmentation.annotate_cell_ids and result.n_cells:
        # Same annotation the exported image gets, so the preview is honest.
        from .pipeline import _cell_id_font_pt
        from .render import draw_id_labels

        class _Info:
            pixel_size_um = pixel_size

        font_pt = _cell_id_font_pt(settings, result, _Info())
        scale = 1.0 / max(pixel_size, 1e-9)
        radii = ((3.0 * result.object_volumes_um3 / (4.0 * np.pi)) ** (1.0 / 3.0)) * scale
        rgb = draw_id_labels(
            rgb,
            [
                (float(c[1]) * scale - float(r) - font_pt * 0.55, float(c[2]) * scale)
                for c, r in zip(result.object_centroids_um, radii)
            ],
            [str(i) for i in range(1, result.n_cells + 1)],
            font_pt=font_pt,
        )

    area_mm2 = (crop.shape[1] * pixel_size) * (crop.shape[2] * pixel_size) / 1e6
    caption = (
        f"{result.n_cells} nuclei in this crop "
        f"({crop.shape[1]}x{crop.shape[2]} px, {area_mm2 * 1e6:.0f} um^2)"
    )
    rgb = _draw_text(
        rgb, caption, anchor_x=rgb.shape[1] // 2, baseline_y=26, font_pt=18,
        color=(1.0, 1.0, 1.0),
    )
    return contrast.to_uint8(rgb), caption


def _build_seed_comparison(path: str, settings: Settings, size: int) -> np.ndarray:
    """Same crop segmented at several seed-smoothing values, side by side."""
    import copy

    from .render import _draw_text
    from .segmentation import segment_nuclei
    from . import contrast

    crop, pixel_size, z_step = _counting_crop(path, settings, size)
    factors = [0.0, 0.15, 0.25, 0.5]
    panels = []
    for factor in factors:
        seg = copy.deepcopy(settings.segmentation)
        seg.seed_smoothing_factor = factor
        result = segment_nuclei(crop, seg, pixel_size, z_step)
        panel = _outline_panel(crop, result.labels, pixel_size)
        panels.append((factor, result.n_cells, panel))

    gap = 6
    height, width = panels[0][2].shape[:2]
    canvas = np.zeros((height, width * len(panels) + gap * (len(panels) - 1), 3), np.float32)
    for i, (_factor, _n, panel) in enumerate(panels):
        canvas[:, i * (width + gap) : i * (width + gap) + width] = panel
    for i, (factor, n, _panel) in enumerate(panels):
        canvas = _draw_text(
            canvas, f"seed smoothing {factor}  ->  n={n}",
            anchor_x=i * (width + gap) + width // 2, baseline_y=26, font_pt=16,
            color=(1.0, 1.0, 1.0),
        )
    return contrast.to_uint8(canvas)


def _show_image_window(master, rgb: np.ndarray, title: str):
    """Display a uint8 RGB array in a scrollable Toplevel."""
    from PIL import Image, ImageTk

    window = tk.Toplevel(master)
    window.title(title)
    image = Image.fromarray(np.asarray(rgb, dtype=np.uint8))

    screen_w = max(400, master.winfo_screenwidth() - 120)
    screen_h = max(400, master.winfo_screenheight() - 180)
    scale = min(screen_w / image.width, screen_h / image.height, 1.0)
    if scale < 1.0:
        image = image.resize(
            (max(1, int(image.width * scale)), max(1, int(image.height * scale))),
            Image.LANCZOS,
        )

    photo = ImageTk.PhotoImage(image)
    label = ttk.Label(window, image=photo)
    label.image = photo  # keep a reference or Tk will garbage-collect it
    label.pack(fill="both", expand=True)

    def save():
        path = filedialog.asksaveasfilename(
            title="Save preview", defaultextension=".png", filetypes=[("PNG", "*.png")]
        )
        if path:
            Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(path)

    bar = ttk.Frame(window)
    bar.pack(fill="x")
    ttk.Button(bar, text="Save as...", command=save).pack(side="left", padx=PAD, pady=4)
    ttk.Button(bar, text="Close", command=window.destroy).pack(side="left", pady=4)
    return window


def main():
    root = tk.Tk()
    try:
        root.call("source", "azure.tcl")
    except tk.TclError:
        pass
    style = ttk.Style()
    if "clam" in style.theme_names():
        style.theme_use("clam")
    App(root)
    root.mainloop()


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    main()
