"""Batch processing: one image in, composites + statistics out."""

from __future__ import annotations

import gc
import multiprocessing
import os
import signal
import sys
import traceback
from concurrent.futures import CancelledError, ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from . import contrast, render
from .config import COLOR_LUTS, CREDIT, ChannelSettings, Settings
from .imageio import (
    StackInfo,
    StackReader,
    list_images,
    probe,
    save_label_volume,
    save_rgb,
)
from .metrics import ChannelMetrics, measure_channel
from .segmentation import compute_threshold, labels_to_outline_overlay, segment_nuclei

SUBDIR_COMPOSITE = "composites"
SUBDIR_CHANNELS = "channels"
SUBDIR_ISO = "3d_views"
SUBDIR_SEG = "segmentation"

# 3D views are rendered from XY-downsampled volumes; full resolution would cost
# ~1.5 GB of float32 per image for no visible gain at figure size.
VIEW_MAX_SIDE = 900

ProgressFn = Callable[[str], None]


@dataclass
class ImageResult:
    name: str
    path: str
    ok: bool = True
    error: str = ""
    n_cells: Optional[int] = None
    info_summary: str = ""
    row: Dict[str, object] = field(default_factory=dict)
    per_object_rows: List[Dict[str, object]] = field(default_factory=list)
    written_files: List[str] = field(default_factory=list)


# --------------------------------------------------------------------- helpers
def resolve_worker_count(requested: int, n_images: int, bytes_per_image: int) -> int:
    """Pick a worker count that will not push the machine into swap.

    The budget comes from measurement, not arithmetic: a 57x1360x1360x4 stack
    (211 MB per raw channel) peaks at ~3.6 GB resident per worker, dominated by
    the watershed and the seed search, plus the mapped pages of the file being
    read. That is ~18x the raw single-channel size.

    Free memory is also capped against *total* RAM, not just what is free right
    now: this is decided once, before the run, when the page cache is still
    cold and MemAvailable reads at its most optimistic. Getting it wrong is not
    a slowdown but an out-of-memory kill, so the estimate errs low. Set
    `max_workers` explicitly to override.
    """
    n_cpu = os.cpu_count() or 1
    if requested and requested > 0:
        return max(1, min(requested, n_images))

    budget_per_worker = max(bytes_per_image * 18, 512 << 20)
    by_available = int((_available_memory_bytes() * 0.7) // budget_per_worker)
    by_total = int((_total_memory_bytes() * 0.5) // budget_per_worker)
    workers = min(n_cpu, max(1, by_available), max(1, by_total))
    return max(1, min(workers, n_images))


def _windows_memory() -> Optional[Tuple[int, int]]:
    """(total, available) bytes via the Win32 API, or None off Windows."""
    if os.name != "nt":
        return None
    try:
        import ctypes

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(_MemoryStatus)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys), int(status.ullAvailPhys)
    except Exception:  # noqa: BLE001 - never let memory probing break a run
        return None


def _total_memory_bytes() -> int:
    windows = _windows_memory()
    if windows:
        return windows[0]
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 8 << 30


def _available_memory_bytes() -> int:
    windows = _windows_memory()
    if windows:
        return windows[1]
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 4 << 30


def resolve_input_files(settings: Settings) -> List[str]:
    """Absolute paths of the images to process."""
    if settings.selected_files:
        paths = []
        for entry in settings.selected_files:
            path = entry if os.path.isabs(entry) else os.path.join(settings.input_dir, entry)
            paths.append(os.path.abspath(path))
        return paths
    return list_images(settings.input_dir)


@dataclass
class BatchCalibration:
    """Display windows and thresholds that are shared by every image in a run.

    Anything decided once for the whole folder lives here. The point is
    comparability: a threshold chosen per image moves the definition of
    "signal" from picture to picture, so totals stop being comparable even
    though each one is individually reasonable.
    """

    limits: Dict[int, Tuple[float, float]] = field(default_factory=dict)
    thresholds: Dict[int, float] = field(default_factory=dict)
    sampled_images: List[str] = field(default_factory=list)

    def to_payload(self) -> Dict[str, object]:
        return {
            "limits": {str(k): list(v) for k, v in self.limits.items()},
            "thresholds": {str(k): float(v) for k, v in self.thresholds.items()},
            "sampled_images": list(self.sampled_images),
        }

    @classmethod
    def from_payload(cls, payload: Optional[Dict[str, object]]) -> "BatchCalibration":
        payload = payload or {}
        return cls(
            limits={int(k): tuple(v) for k, v in (payload.get("limits") or {}).items()},
            thresholds={int(k): float(v) for k, v in (payload.get("thresholds") or {}).items()},
            sampled_images=list(payload.get("sampled_images") or []),
        )


def _sample_paths(paths: Sequence[str], limit: int) -> List[str]:
    """Evenly spaced subset of `paths`, so the sample spans the whole folder."""
    if limit <= 0 or len(paths) <= limit:
        return list(paths)
    step = len(paths) / float(limit)
    return [paths[min(len(paths) - 1, int(i * step))] for i in range(limit)]


def calibrate_batch(
    settings: Settings,
    progress: Optional[ProgressFn] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> BatchCalibration:
    """One pass over a sample of the folder to fix shared limits and thresholds.

    Only the channels that actually need it are read, and only the images in
    the sample, so a run that uses per-image settings throughout skips this
    entirely.
    """
    calibration = BatchCalibration()

    needs_limits = [
        i for i, c in enumerate(settings.channels) if c.limit_mode == "auto_percentile_batch"
    ]
    needs_threshold = [
        i
        for i, c in enumerate(settings.channels)
        if c.measures_anything
        and c.threshold_scope == "batch"
        and c.threshold_method not in ("manual",)
    ]
    if not needs_limits and not needs_threshold:
        return calibration

    paths = _sample_paths(resolve_input_files(settings), settings.batch_sample_images)
    if not paths:
        return calibration
    calibration.sampled_images = [os.path.basename(p) for p in paths]

    if progress:
        progress(
            f"calibrating the batch on {len(paths)} image(s) so every image shares "
            "the same limits and thresholds"
        )

    # Pooled pixel samples, per channel, across the sampled images.
    limit_samples: Dict[int, List[np.ndarray]] = {i: [] for i in needs_limits}
    threshold_samples: Dict[int, List[np.ndarray]] = {i: [] for i in needs_threshold}
    wanted = sorted(set(needs_limits) | set(needs_threshold))

    for path in paths:
        # Calibration reads several images before any of them is processed,
        # which on this data is minutes. Without this check Stop would appear
        # to do nothing for the whole pass.
        if should_stop is not None and should_stop():
            if progress:
                progress("  calibration stopped")
            break
        if progress:
            progress(f"  calibrating on {os.path.basename(path)}")
        try:
            with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
                info = reader.info
                assert info is not None
                for index in wanted:
                    if index >= info.n_channels:
                        continue
                    channel = settings.channels[index]
                    raw = reader.channel_volume(index)
                    if channel.subtract_background:
                        volume = contrast.subtract_background(
                            raw, channel.background_radius_um, info.pixel_size_um
                        )
                        del raw
                    else:
                        volume = raw.astype(np.float32, copy=False)
                        del raw
                    if index in threshold_samples:
                        # Sample inside the analysis region only: the threshold
                        # is used for measurements, which are confined to it.
                        tr0, tr1, tc0, tc1 = (
                            settings.analysis_roi.pixel_bounds(info.height, info.width)
                            if settings.analysis_roi.enabled
                            else (0, info.height, 0, info.width)
                        )
                        threshold_samples[index].append(
                            _thin(volume[:, tr0:tr1, tc0:tc1], 400_000)
                        )
                    if index in limit_samples:
                        projection = contrast.project(volume, settings.output.projection)
                        limit_samples[index].append(_thin(projection, 400_000))
                    del volume
        except Exception as exc:  # noqa: BLE001 - a bad file must not stop the run
            if progress:
                progress(f"  skipped {os.path.basename(path)} during calibration: {exc}")

    for index, chunks in limit_samples.items():
        if not chunks:
            continue
        channel = settings.channels[index]
        pooled = np.concatenate(chunks)
        calibration.limits[index] = contrast.limits_from_percentiles(
            pooled, channel.low_percentile, channel.high_percentile
        )
        if progress:
            lo, hi = calibration.limits[index]
            progress(f"  {channel.name}: batch display window {lo:.1f} - {hi:.1f}")

    for index, chunks in threshold_samples.items():
        if not chunks:
            continue
        channel = settings.channels[index]
        pooled = np.concatenate(chunks)
        calibration.thresholds[index] = compute_threshold(
            pooled,
            channel.threshold_method,
            channel.threshold_value,
            channel.threshold_percentile,
        )
        if progress:
            progress(
                f"  {channel.name}: batch {channel.threshold_method} threshold "
                f"{calibration.thresholds[index]:.1f} (used for every image)"
            )
    return calibration


def threshold_warnings(settings: Settings, results: Sequence["ImageResult"]) -> List[str]:
    """Flag thresholds that plainly did not separate signal from background.

    An automatic method assumes the histogram has a background peak and a
    signal peak. On a dim channel -- especially once an analysis region has
    narrowed the field -- there may be no signal peak at all, and the method
    cuts somewhere inside the noise instead. The threshold it returns looks
    perfectly ordinary; what gives it away is the fraction of the region it
    ends up calling signal.

    This runs on the measurements themselves rather than on a sample, so it
    reports what actually happened.
    """
    messages: List[str] = []
    rows = [r.row for r in results if r.ok and r.row]
    if not rows:
        return messages

    for channel in settings.channels:
        if not channel.measures_anything:
            continue
        for kind, column in (
            ("area", f"{channel.name}_area_fraction_fov"),
            ("volume", f"{channel.name}_volume_fraction_fov"),
        ):
            values = [row[column] for row in rows if isinstance(row.get(column), float)]
            if not values:
                continue
            median = float(np.median(values))
            threshold = next(
                (row[f"{channel.name}_threshold"] for row in rows
                 if f"{channel.name}_threshold" in row),
                float("nan"),
            )
            if median > 0.8:
                messages.append(
                    f"  {channel.name}: the {channel.threshold_method} threshold "
                    f"({threshold:.1f}) calls {median * 100:.0f}% of the measured region "
                    f"signal, so {channel.name} {kind} is close to the region itself "
                    "and carries little information."
                )
                messages.append(
                    "      Most likely there is no distinct signal peak in this channel. "
                    "Try threshold 'triangle', or set it by hand."
                )
                break
            if median < 1e-4:
                messages.append(
                    f"  {channel.name}: the {channel.threshold_method} threshold "
                    f"({threshold:.1f}) calls only {median * 100:.3f}% of the region "
                    f"signal, so {channel.name} {kind} is near zero."
                )
                messages.append(
                    "      The threshold has probably cut above the signal. Try "
                    "'triangle' or 'li', or set it by hand."
                )
                break
    return messages


def _thin(data: np.ndarray, target: int) -> np.ndarray:
    """A small, evenly spread 1D sample of an array."""
    flat = np.asarray(data).ravel()
    if flat.size > target:
        flat = flat[:: flat.size // target + 1]
    return np.ascontiguousarray(flat, dtype=np.float32)


def compute_reference_limits(
    settings: Settings, reference_path: Optional[str] = None, progress: Optional[ProgressFn] = None
) -> Dict[int, Tuple[float, float]]:
    """Saturation windows from the reference ROI, for channels that want them.

    Only used for `scope == "global"`; a per-image ROI is resolved during the
    run instead, against each image's own pixels.
    """
    roi = settings.reference_roi
    wanted = [i for i, c in enumerate(settings.channels) if c.limit_mode == "roi_reference"]
    if not wanted or roi.scope != "global":
        return {}

    path = reference_path or roi.source_image
    if path and not os.path.isabs(path):
        path = os.path.join(settings.input_dir, path)
    if not path or not os.path.isfile(path):
        candidates = resolve_input_files(settings)
        if not candidates:
            raise FileNotFoundError("no images available to compute the reference ROI limits")
        path = candidates[0]

    if progress:
        progress(f"reading reference ROI from {os.path.basename(path)}")

    limits: Dict[int, Tuple[float, float]] = {}
    with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
        info = reader.info
        assert info is not None
        for index in wanted:
            if index >= info.n_channels:
                continue
            channel = settings.channels[index]
            volume = reader.channel_volume(index)
            projection = contrast.project(volume, settings.output.projection)
            if channel.subtract_background:
                projection = contrast.subtract_background(
                    projection, channel.background_radius_um, info.pixel_size_um
                )
            r0, r1, c0, c1 = roi.pixel_bounds(projection.shape[0], projection.shape[1])
            patch = projection[r0:r1, c0:c1]
            limits[index] = contrast.limits_from_percentiles(
                patch, channel.low_percentile, channel.high_percentile
            )
            del volume, projection
    return limits


# ------------------------------------------------------------- single image
def process_image(
    path: str,
    settings: Settings,
    calibration: Optional[BatchCalibration] = None,
    progress: Optional[ProgressFn] = None,
) -> ImageResult:
    """Process one acquisition end to end. Never raises; errors land in the result."""
    name = os.path.basename(path)
    result = ImageResult(name=name, path=path)
    calibration = calibration or BatchCalibration()

    def report(message: str) -> None:
        if progress:
            progress(f"{name}: {message}")

    try:
        with StackReader(path, settings.pixel_size_um, settings.z_step_um) as reader:
            info = reader.info
            assert info is not None
            result.info_summary = info.summary()

            n_configured = len(settings.channels)
            if info.n_channels < n_configured:
                raise ValueError(
                    f"settings describe {n_configured} channels but the file has "
                    f"{info.n_channels}"
                )

            row: Dict[str, object] = {
                "image": info.stem,
                "file": name,
                "n_z": info.n_z,
                "height_px": info.height,
                "width_px": info.width,
                "pixel_size_um": info.pixel_size_um,
                "z_step_um": info.z_step_um,
                "fov_area_um2": info.height * info.width * info.pixel_area_um2,
                "fov_volume_um3": info.height * info.width * info.n_z * info.voxel_volume_um3,
            }

            # The analysis region confines every measurement. Exports stay full
            # field: the crop is a view, so restricting costs nothing.
            roi = settings.analysis_roi
            if roi.enabled:
                r0, r1, c0, c1 = roi.pixel_bounds(info.height, info.width)
                report(
                    f"measuring inside the analysis region "
                    f"x {c0}-{c1}, y {r0}-{r1} px of {info.width}x{info.height}"
                )
            else:
                r0, r1, c0, c1 = 0, info.height, 0, info.width
            row["analysis_region"] = (
                f"x{c0}-{c1} y{r0}-{r1}" if roi.enabled else "whole image"
            )
            row["analysis_area_um2"] = (r1 - r0) * (c1 - c0) * info.pixel_area_um2
            row["analysis_volume_um3"] = (
                (r1 - r0) * (c1 - c0) * info.n_z * info.voxel_volume_um3
            )

            # --- cell counting first: the per-cell columns need the count ----
            seg_result = None
            n_cells: Optional[int] = None
            if settings.segmentation.enabled:
                report("counting cells (3D watershed)")
                nuclei_volume = reader.channel_volume(settings.segmentation.channel_index)
                seg_result = segment_nuclei(
                    nuclei_volume[:, r0:r1, c0:c1],
                    settings.segmentation,
                    info.pixel_size_um,
                    info.z_step_um,
                    progress=lambda m: report(f"cell counting - {m}"),
                )
                n_cells = seg_result.n_cells
                result.n_cells = n_cells
                del nuclei_volume
                report(f"{n_cells} cells")

                # The 3D label volume is ~400 MB and is only needed here: write
                # it now, keep the flat projection the overlay needs, and let
                # the volume go before the channel loop allocates its own.
                if settings.segmentation.save_label_image:
                    label_path = os.path.join(
                        settings.output_dir,
                        SUBDIR_SEG,
                        f"{_safe(info.stem)}_nuclei_labels.tif",
                    )
                    result.written_files.append(
                        save_label_volume(
                            seg_result.labels,
                            label_path,
                            info.pixel_size_um * seg_result.downsample_xy,
                            info.z_step_um,
                        )
                    )
                # The labels cover the analysis region only; put them back where
                # they belong so the overlay lines up with the full-field image.
                labels_2d = _place_in_frame(
                    seg_result.labels.max(axis=0),
                    (info.height, info.width),
                    (r0, r1, c0, c1),
                )
                seg_result.labels = np.zeros((0, 0, 0), dtype=np.int32)

            row["n_cells"] = n_cells if n_cells is not None else ""
            if seg_result is not None:
                row["nuclei_threshold"] = seg_result.threshold_value
                row["mean_nucleus_volume_um3"] = (
                    float(seg_result.object_volumes_um3.mean())
                    if seg_result.n_cells
                    else 0.0
                )
                if settings.output.write_per_object_csv:
                    result.per_object_rows = seg_result.per_object_rows(info.stem)

            # --- per channel: display + measurements -------------------------
            composite_layers: List[np.ndarray] = []
            volumes_for_3d: List[Tuple[np.ndarray, Tuple[float, float, float]]] = []
            need_3d = settings.output.save_isometric_3d or settings.output.save_orthogonal_view
            nuclei_projection_rgb: Optional[np.ndarray] = None
            view_pixel_size = info.pixel_size_um

            for index, channel in enumerate(settings.channels):
                report(f"channel {index + 1} ({channel.name})")
                raw = reader.channel_volume(index)
                if channel.subtract_background:
                    # Hand it the uint16 array: the conversion to float32 makes
                    # the one copy we need and the subtraction then runs in it.
                    volume = contrast.subtract_background(
                        raw, channel.background_radius_um, info.pixel_size_um
                    )
                    del raw
                else:
                    volume = raw.astype(np.float32, copy=False)
                    del raw
                projection = contrast.project(volume, settings.output.projection)

                vmin, vmax = contrast.resolve_limits(
                    channel,
                    projection,
                    roi=settings.reference_roi,
                    roi_limits=calibration.limits.get(index),
                )
                row[f"{channel.name}_display_min"] = vmin
                row[f"{channel.name}_display_max"] = vmax

                metrics = measure_channel(
                    channel,
                    volume[:, r0:r1, c0:c1],
                    projection[r0:r1, c0:c1],
                    info.pixel_area_um2,
                    info.voxel_volume_um3,
                    n_cells,
                    threshold_override=calibration.thresholds.get(index),
                )
                if channel.measures_anything:
                    row.update(metrics.as_columns())

                normalised = contrast.apply_display_transform(
                    projection, vmin, vmax, channel.display_transform, channel.gamma
                )
                colored = contrast.colorize(normalised, channel.color)

                if channel.include_in_composite:
                    composite_layers.append(colored)
                if index == settings.segmentation.channel_index:
                    nuclei_projection_rgb = colored

                if settings.output.save_single_channels and channel.save_single_channel:
                    single = render.draw_scale_bar(
                        colored, info.pixel_size_um, settings.output
                    )
                    out_base = os.path.join(
                        settings.output_dir,
                        SUBDIR_CHANNELS,
                        _safe(channel.name),
                        f"{_safe(info.stem)}_{_safe(channel.name)}",
                    )
                    result.written_files += save_rgb(
                        contrast.to_uint8(single),
                        out_base,
                        settings.output.single_channel_formats,
                        info.pixel_size_um,
                    )

                if need_3d and channel.include_in_composite:
                    # Downsample in XY before keeping the volume around: a 3D
                    # view is never printed at 1360 px, and holding four
                    # full-resolution float volumes costs 1.5 GB.
                    step = max(1, int(np.ceil(max(volume.shape[1:]) / VIEW_MAX_SIDE)))
                    norm_volume = contrast.apply_display_transform(
                        volume[:, ::step, ::step],
                        vmin,
                        vmax,
                        channel.display_transform,
                        channel.gamma,
                    )
                    rgb = tuple(v / 255.0 for v in COLOR_LUTS[channel.color])
                    volumes_for_3d.append((norm_volume, rgb))
                    view_pixel_size = info.pixel_size_um * step

                del volume, projection, normalised

            # --- composite -----------------------------------------------------
            if settings.output.save_composite and composite_layers:
                report("writing composite")
                composite = contrast.merge_composite(composite_layers)
                composite = render.draw_scale_bar(
                    composite, info.pixel_size_um, settings.output
                )
                out_base = os.path.join(
                    settings.output_dir, SUBDIR_COMPOSITE, f"Composite_{_safe(info.stem)}"
                )
                result.written_files += save_rgb(
                    contrast.to_uint8(composite),
                    out_base,
                    settings.output.composite_formats,
                    info.pixel_size_um,
                )
                del composite
            del composite_layers

            # --- segmentation outputs -------------------------------------------
            if seg_result is not None and settings.segmentation.save_overlay:
                report("writing segmentation overlay")
                background = (
                    nuclei_projection_rgb
                    if nuclei_projection_rgb is not None
                    else np.zeros((info.height, info.width, 3), dtype=np.float32)
                )
                overlay = labels_to_outline_overlay(labels_2d, background)

                if settings.segmentation.annotate_cell_ids and seg_result.n_cells:
                    # Centroids come back in microns, so dividing by the pixel
                    # size lands them on the full-resolution overlay whatever
                    # downsampling the segmentation used.
                    centroids = seg_result.object_centroids_um
                    scale = 1.0 / max(info.pixel_size_um, 1e-9)
                    font_pt = _cell_id_font_pt(settings, seg_result, info)
                    # Sit each number just above its own outline rather than on
                    # top of it: three digits are wider than a small nucleus and
                    # would hide the very boundary they are labelling.
                    radii_px = (
                        (3.0 * seg_result.object_volumes_um3 / (4.0 * np.pi)) ** (1.0 / 3.0)
                    ) * scale
                    positions = [
                        (
                            r0 + float(c[1]) * scale - float(r) - font_pt * 0.55,
                            c0 + float(c[2]) * scale,
                        )
                        for c, r in zip(centroids, radii_px)
                    ]
                    overlay = render.draw_id_labels(
                        overlay,
                        positions,
                        [str(i) for i in range(1, len(positions) + 1)],
                        font_pt=font_pt,
                    )

                if roi.enabled and roi.show_on_overlay:
                    overlay = render.draw_region_outline(overlay, (r0, r1, c0, c1))
                overlay = render.add_corner_label(overlay, f"n = {seg_result.n_cells} cells")
                overlay = render.draw_scale_bar(overlay, info.pixel_size_um, settings.output)
                out_base = os.path.join(
                    settings.output_dir, SUBDIR_SEG, f"{_safe(info.stem)}_nuclei_overlay"
                )
                result.written_files += save_rgb(
                    contrast.to_uint8(overlay),
                    out_base,
                    settings.segmentation.overlay_formats or ["png"],
                    info.pixel_size_um,
                )
                del overlay
            seg_result = None
            labels_2d = None

            # --- 3D views ---------------------------------------------------------
            if volumes_for_3d:
                if settings.output.save_orthogonal_view:
                    report("rendering orthogonal view")
                    ortho = render.orthogonal_view(
                        volumes_for_3d, view_pixel_size, info.z_step_um
                    )
                    ortho = render.draw_scale_bar(ortho, view_pixel_size, settings.output)
                    out_base = os.path.join(
                        settings.output_dir, SUBDIR_ISO, f"{_safe(info.stem)}_orthogonal"
                    )
                    result.written_files += save_rgb(
                        contrast.to_uint8(ortho), out_base, ["png"], view_pixel_size
                    )
                    del ortho

                if settings.output.save_isometric_3d:
                    report("rendering isometric view")
                    iso = render.isometric_view(
                        volumes_for_3d,
                        view_pixel_size,
                        info.z_step_um,
                        settings.output.isometric_elevation_deg,
                        settings.output.isometric_azimuth_deg,
                    )
                    iso = render.add_corner_label(
                        iso,
                        f"{info.n_z} slices x {info.z_step_um:g} µm "
                        f"= {info.n_z * info.z_step_um:.0f} µm deep",
                    )
                    out_base = os.path.join(
                        settings.output_dir, SUBDIR_ISO, f"{_safe(info.stem)}_isometric"
                    )
                    result.written_files += save_rgb(
                        contrast.to_uint8(iso), out_base, ["png"], None
                    )
                    del iso
                volumes_for_3d.clear()

            result.row = row
            report("done")
    except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
        result.ok = False
        result.error = f"{type(exc).__name__}: {exc}"
        result.row = {"image": os.path.splitext(name)[0], "file": name, "error": result.error}
        if progress:
            progress(f"{name}: FAILED - {result.error}")
            progress(traceback.format_exc())
    finally:
        # A worker handles several images in turn; hand the big arrays back
        # before the next one starts rather than at the next gc pause.
        gc.collect()
    return result


def _place_in_frame(
    patch: np.ndarray, frame_shape: Tuple[int, int], bounds: Tuple[int, int, int, int]
) -> np.ndarray:
    """Put a region-sized label image back at its place in the full frame.

    The patch may also be at a coarser resolution than the region, when the
    segmentation ran with XY downsampling, so it is scaled up first.
    """
    from .segmentation import _resize_nearest

    r0, r1, c0, c1 = bounds
    if patch.shape == frame_shape and (r0, c0) == (0, 0):
        return patch
    if patch.shape != (r1 - r0, c1 - c0):
        patch = _resize_nearest(patch, (r1 - r0, c1 - c0))
    frame = np.zeros(frame_shape, dtype=patch.dtype)
    frame[r0:r1, c0:c1] = patch
    return frame


def _cell_id_font_pt(settings: Settings, seg_result, info) -> int:
    """Digit size for the per-cell ids.

    Sized from the nuclei themselves by default: the number should sit inside
    the outline it belongs to rather than sprawl across its neighbours.
    """
    configured = int(settings.segmentation.cell_id_font_pt)
    if configured > 0:
        return configured
    volumes = seg_result.object_volumes_um3
    if len(volumes) == 0:
        return 11
    median_volume = float(np.median(volumes))
    diameter_um = 2.0 * (3.0 * median_volume / (4.0 * np.pi)) ** (1.0 / 3.0)
    diameter_px = diameter_um / max(info.pixel_size_um, 1e-9)
    # Roughly half a nucleus wide, clamped to something legible.
    return int(np.clip(round(diameter_px * 0.45), 8, 40))


def _safe(name: str) -> str:
    """Filesystem-safe version of a name, keeping it recognisable."""
    keep = []
    for ch in name:
        keep.append(ch if (ch.isalnum() or ch in " ._-()[]") else "_")
    return "".join(keep).strip() or "image"


# ------------------------------------------------------------------ batch run
def _worker_init() -> None:
    """Make a worker process die with its parent.

    Without this, closing the window mid-run leaves the pool's children alive:
    they block forever waiting for work from a parent that no longer exists,
    each holding on to a couple of hundred MB. They are then invisible except
    in a process list, and quietly starve the next run of memory.
    """
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            PR_SET_PDEATHSIG = 1
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(
                PR_SET_PDEATHSIG, signal.SIGTERM
            )
        except Exception:  # noqa: BLE001 - best effort, never fail a run for this
            pass


def _worker(args) -> ImageResult:
    path, settings_dict, calibration_payload = args
    settings = Settings.from_dict(settings_dict)
    calibration = BatchCalibration.from_payload(calibration_payload)
    return process_image(path, settings, calibration, progress=None)


def run_batch(
    settings: Settings,
    progress: Optional[ProgressFn] = None,
    on_result: Optional[Callable[[ImageResult], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> List[ImageResult]:
    """Process every selected image and write the CSV, settings and log."""

    def report(message: str) -> None:
        if progress:
            progress(message)

    problems = settings.validate()
    if problems:
        raise ValueError("settings are not valid:\n  - " + "\n  - ".join(problems))

    paths = resolve_input_files(settings)
    if not paths:
        raise FileNotFoundError(f"no supported images found in {settings.input_dir!r}")

    os.makedirs(settings.output_dir, exist_ok=True)
    started = datetime.now()

    # Everything that must be identical across the folder is decided here,
    # before any image is processed.
    calibration = calibrate_batch(settings, progress=progress, should_stop=should_stop)
    roi_limits = compute_reference_limits(settings, progress=progress)
    for index, (lo, hi) in sorted(roi_limits.items()):
        calibration.limits[index] = (lo, hi)
        report(
            f"reference ROI limits for {settings.channels[index].name}: {lo:.1f} - {hi:.1f}"
        )

    first_info: Optional[StackInfo] = None
    try:
        first_info = probe(paths[0])
    except Exception:
        pass
    bytes_per_channel = (
        first_info.n_z * first_info.height * first_info.width * 2 if first_info else 256 << 20
    )
    workers = resolve_worker_count(settings.max_workers, len(paths), bytes_per_channel)

    if should_stop is not None and should_stop():
        report("stopped before any image was processed")
        write_outputs(settings, [], started, workers, report, calibration)
        return []
    if settings.max_workers <= 0:
        report(
            f"processing {len(paths)} image(s) with {workers} worker(s) "
            f"(limited by memory, not cores: ~{bytes_per_channel * 18 / 2**30:.1f} GB each; "
            "set 'Parallel workers' to override)"
        )
    else:
        report(f"processing {len(paths)} image(s) with {workers} worker(s)")

    results: List[ImageResult] = []

    if workers == 1:
        for i, path in enumerate(paths, start=1):
            if should_stop and should_stop():
                report("stopped by user")
                break
            report(f"[{i}/{len(paths)}] {os.path.basename(path)}")
            result = process_image(path, settings, calibration, progress)
            results.append(result)
            if on_result:
                on_result(result)
    else:
        payload = settings.to_dict()
        calibration_payload = calibration.to_payload()
        jobs = [(p, payload, calibration_payload) for p in paths]
        # Deliberately not a `with` block. The context manager would call
        # shutdown() a second time with different arguments, and the two calls
        # fight over whether the queued work is cancelled or waited for.
        #
        # "spawn", never "fork" (the Linux default before Python 3.14): a
        # forked worker inherits the GUI's heap, including tkinter objects
        # left over from closed preview windows. The gc.collect() at the end
        # of each image then runs their __del__, which calls into Tcl and
        # waits forever for a Tk main thread that does not exist in the child
        # -- every worker froze after its first image. A spawned worker starts
        # clean; it costs a second or two of imports, once per worker.
        pool = ProcessPoolExecutor(
            max_workers=workers,
            initializer=_worker_init,
            mp_context=multiprocessing.get_context("spawn"),
        )
        try:
            # Submit in a sliding window rather than all at once. The pool
            # pre-loads its call queue, and anything already queued there
            # cannot be cancelled, so queueing all 18 up front means Stop has
            # to wait for several more images. One spare job keeps every
            # worker busy while leaving little that Stop cannot drop.
            pending = list(jobs)
            futures: Dict[object, str] = {}
            window = workers + 1

            def submit_next() -> None:
                while pending and len(futures) < window:
                    job = pending.pop(0)
                    futures[pool.submit(_worker, job)] = job[0]

            submit_next()
            done = 0
            while futures:
                for future in as_completed(list(futures)):
                    path = futures.pop(future)
                    done += 1
                    try:
                        result = future.result()
                    except CancelledError:
                        # CancelledError derives from BaseException, so it
                        # would slip past `except Exception` and kill this
                        # thread rather than ending the loop.
                        continue
                    except Exception as exc:  # noqa: BLE001
                        result = ImageResult(
                            name=os.path.basename(path),
                            path=path,
                            ok=False,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    results.append(result)
                    status = "ok" if result.ok else f"FAILED - {result.error}"
                    cells = (
                        f", {result.n_cells} cells" if result.n_cells is not None else ""
                    )
                    report(f"[{done}/{len(paths)}] {result.name}: {status}{cells}")
                    if on_result:
                        on_result(result)

                    if should_stop and should_stop():
                        report(
                            "stopping: nothing further will be started; the "
                            "images already running will finish first"
                        )
                        pending.clear()
                        futures.clear()
                        break
                    submit_next()
                    break  # re-enter as_completed with the refreshed set
        finally:
            # One call: cancel what has not started, wait for what has.
            pool.shutdown(wait=True, cancel_futures=True)

    results.sort(key=lambda r: r.name)

    warnings = threshold_warnings(settings, results)
    if warnings:
        report("")
        report("Check these thresholds before using the numbers:")
        for line in warnings:
            report(line)

    write_outputs(settings, results, started, workers, report, calibration, warnings)
    return results


def write_outputs(
    settings: Settings,
    results: Sequence[ImageResult],
    started: datetime,
    workers: int,
    report: ProgressFn,
    calibration: Optional["BatchCalibration"] = None,
    warnings: Optional[Sequence[str]] = None,
) -> None:
    """Write statistics.csv, the settings snapshot and the run log."""
    import pandas as pd

    os.makedirs(settings.output_dir, exist_ok=True)

    if settings.output.write_statistics_csv:
        rows = [r.row for r in results if r.row]
        if rows:
            frame = pd.DataFrame(rows)
            ordered = _order_columns(frame, settings)
            csv_path = os.path.join(settings.output_dir, "statistics.csv")
            ordered.to_csv(csv_path, index=False)
            report(f"wrote {csv_path}")

    per_object = [row for r in results for row in r.per_object_rows]
    if per_object:
        frame = pd.DataFrame(per_object)
        path = os.path.join(settings.output_dir, "cells_per_object.csv")
        frame.to_csv(path, index=False)
        report(f"wrote {path}")

    settings_path = os.path.join(settings.output_dir, "settings_used.json")
    settings.save(settings_path)
    report(f"wrote {settings_path}")

    finished = datetime.now()
    failures = [r for r in results if not r.ok]
    log_path = os.path.join(settings.output_dir, "processing_log.txt")
    with open(log_path, "w", encoding="utf-8") as fh:
        fh.write("Confocal batch processing log\n")
        fh.write(CREDIT + "\n")
        fh.write("=" * 60 + "\n")
        fh.write(f"started:   {started:%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"finished:  {finished:%Y-%m-%d %H:%M:%S}\n")
        fh.write(f"duration:  {(finished - started).total_seconds():.1f} s\n")
        fh.write(f"workers:   {workers}\n")
        fh.write(f"input:     {settings.input_dir}\n")
        fh.write(f"output:    {settings.output_dir}\n")
        fh.write(f"images:    {len(results)} ({len(failures)} failed)\n\n")

        fh.write("Channels\n--------\n")
        for i, channel in enumerate(settings.channels, start=1):
            measured = [
                label
                for flag, label in (
                    (channel.measure_area, "area"),
                    (channel.measure_volume, "volume"),
                    (channel.measure_intensity, "intensity"),
                )
                if flag
            ]
            fh.write(
                f"  {i}. {channel.name}: colour={channel.color}, "
                f"limits={channel.limit_mode}, display={channel.display_transform}, "
                f"measures={', '.join(measured) if measured else 'none'}"
                + (
                    f", threshold={channel.threshold_method} ({channel.threshold_scope})"
                    if measured
                    else ""
                )
                + "\n"
            )
        seg = settings.segmentation
        fh.write("\nCell counting\n-------------\n")
        if seg.enabled:
            name = (
                settings.channels[seg.channel_index].name
                if seg.channel_index < len(settings.channels)
                else f"channel {seg.channel_index + 1}"
            )
            fh.write(
                f"  3D watershed on {name}; threshold={seg.threshold_method}, "
                f"nucleus radius={seg.nucleus_radius_um} um, "
                f"volume {seg.min_volume_um3}-{seg.max_volume_um3} um^3\n"
            )
        else:
            fh.write("  disabled\n")

        if calibration is not None and (calibration.limits or calibration.thresholds):
            fh.write("\nShared across the batch\n-----------------------\n")
            if calibration.sampled_images:
                fh.write(
                    f"  calibrated on {len(calibration.sampled_images)} image(s): "
                    + ", ".join(calibration.sampled_images[:4])
                    + (" ..." if len(calibration.sampled_images) > 4 else "")
                    + "\n"
                )
            for index, (lo, hi) in sorted(calibration.limits.items()):
                if index < len(settings.channels):
                    fh.write(
                        f"  {settings.channels[index].name}: display window "
                        f"{lo:.1f} - {hi:.1f} (same for every image)\n"
                    )
            for index, value in sorted(calibration.thresholds.items()):
                if index < len(settings.channels):
                    fh.write(
                        f"  {settings.channels[index].name}: "
                        f"{settings.channels[index].threshold_method} threshold "
                        f"{value:.1f} (same for every image)\n"
                    )

        if warnings:
            fh.write("\nCheck these thresholds before using the numbers\n")
            fh.write("-" * 47 + "\n")
            for line in warnings:
                fh.write(line + "\n")

        fh.write("\nPer image\n---------\n")
        for result in results:
            status = "ok" if result.ok else "FAILED"
            cells = "" if result.n_cells is None else f", {result.n_cells} cells"
            fh.write(f"  {result.name}: {status}{cells}\n")
            if result.info_summary:
                fh.write(f"      {result.info_summary}\n")
            if not result.ok:
                fh.write(f"      {result.error}\n")
    report(f"wrote {log_path}")


def _order_columns(frame, settings: Settings):
    """Put identity columns first, then each channel's columns grouped together."""
    leading = [
        "image",
        "file",
        "n_cells",
        "n_z",
        "height_px",
        "width_px",
        "pixel_size_um",
        "z_step_um",
        "fov_area_um2",
        "fov_volume_um3",
        "nuclei_threshold",
        "mean_nucleus_volume_um3",
    ]
    ordered = [c for c in leading if c in frame.columns]
    for channel in settings.channels:
        prefix = f"{channel.name}_"
        ordered += [c for c in frame.columns if c.startswith(prefix) and c not in ordered]
    ordered += [c for c in frame.columns if c not in ordered]
    return frame[ordered]
