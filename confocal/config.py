"""Settings model for the confocal processing pipeline.

Everything the user can choose in the GUI lives here as a dataclass, so that a
run can be fully described -- and reproduced -- by a single JSON file.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

SETTINGS_VERSION = 1

APP_NAME = "Confocal batch processor"
AUTHORS = "Klaudia Saladauskas and Leonardo Cecchin"
CREDIT = f"{APP_NAME} \u2014 by {AUTHORS}"

# Okabe-Ito colour-blind safe palette, same names/values as the reference macro.
COLOR_LUTS: Dict[str, Tuple[int, int, int]] = {
    "Orange": (230, 159, 0),
    "Light Blue": (86, 180, 233),
    "Bluish Green": (0, 158, 115),
    "Amber": (245, 199, 16),
    "Yellow": (240, 228, 66),
    "Blue": (0, 114, 178),
    "Vermillon": (213, 94, 0),
    "Pink": (204, 121, 167),
    "Plain Red": (255, 0, 0),
    "Green": (0, 255, 0),
    "Magenta": (255, 0, 255),
    "Cyan": (0, 255, 255),
    "White": (255, 255, 255),
}

# How the min/max display window is decided for a channel.
#   auto_percentile       -- percentiles of each image, on its own
#   auto_percentile_batch -- percentiles pooled over the folder, one window for all
LIMIT_MODES = ("manual", "auto_percentile", "auto_percentile_batch", "roi_reference")

# How intensities are mapped onto the 0-255 display ramp *after* the min/max
# window is applied. This is display-only and never touches the statistics.
DISPLAY_TRANSFORMS = ("linear", "gamma", "log")

# Threshold used to decide which voxels count as signal for the statistics.
THRESHOLD_METHODS = ("otsu", "triangle", "li", "yen", "mean", "percentile", "manual")

# Whether an automatic threshold is decided per image or once for the folder.
# "batch" is what makes totals comparable between images: an automatic method
# run per image moves the signal/background cut-off from image to image (on the
# EXP17 set Otsu spans a factor of 8-15), so the same structure measures
# differently depending on which picture it is in.
THRESHOLD_SCOPES = ("per_image", "batch")

PROJECTIONS = ("max", "sum", "mean")


def _coerce(value: Any, default: Any) -> Any:
    """Best-effort cast of a JSON value to the type of the dataclass default."""
    if default is None or value is None:
        return value
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "y", "on")
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, str):
        return str(value)
    return value


@dataclass
class ChannelSettings:
    """Per-channel display and quantification settings."""

    name: str = "Channel"
    color: str = "White"

    # --- display window -------------------------------------------------
    limit_mode: str = "auto_percentile"
    vmin: float = 0.0
    vmax: float = 4095.0
    # percentiles used by auto_percentile and roi_reference
    low_percentile: float = 0.5
    high_percentile: float = 99.7

    display_transform: str = "linear"
    gamma: float = 0.5  # only used when display_transform == "gamma"

    # --- preprocessing ---------------------------------------------------
    subtract_background: bool = True
    background_radius_um: float = 35.0  # ~= "rolling=50" px at 0.716 um/px

    # --- what to measure --------------------------------------------------
    measure_area: bool = False
    measure_volume: bool = False
    measure_intensity: bool = False

    threshold_method: str = "otsu"
    threshold_value: float = 0.0  # used by "manual"
    threshold_percentile: float = 99.0  # used by "percentile"
    threshold_scope: str = "batch"

    # --- output ------------------------------------------------------------
    include_in_composite: bool = True
    save_single_channel: bool = False

    @property
    def measures_anything(self) -> bool:
        return self.measure_area or self.measure_volume or self.measure_intensity

    def validate(self, index: int) -> List[str]:
        problems: List[str] = []
        tag = f"channel {index + 1} ({self.name!r})"
        if not self.name.strip():
            problems.append(f"{tag}: name must not be empty")
        if self.color not in COLOR_LUTS:
            problems.append(f"{tag}: unknown colour {self.color!r}")
        if self.limit_mode not in LIMIT_MODES:
            problems.append(f"{tag}: unknown limit mode {self.limit_mode!r}")
        if self.display_transform not in DISPLAY_TRANSFORMS:
            problems.append(f"{tag}: unknown display transform {self.display_transform!r}")
        if self.threshold_method not in THRESHOLD_METHODS:
            problems.append(f"{tag}: unknown threshold method {self.threshold_method!r}")
        if self.threshold_scope not in THRESHOLD_SCOPES:
            problems.append(f"{tag}: unknown threshold scope {self.threshold_scope!r}")
        if self.limit_mode == "manual" and self.vmax <= self.vmin:
            problems.append(f"{tag}: max ({self.vmax}) must be greater than min ({self.vmin})")
        if not 0.0 <= self.low_percentile < self.high_percentile <= 100.0:
            problems.append(
                f"{tag}: percentiles must satisfy 0 <= low < high <= 100 "
                f"(got {self.low_percentile}, {self.high_percentile})"
            )
        if self.display_transform == "gamma" and self.gamma <= 0:
            problems.append(f"{tag}: gamma must be positive")
        return problems


@dataclass
class ReferenceROI:
    """Rectangle used to derive saturation limits from a representative region.

    Coordinates are stored as fractions of the image width/height so the same
    settings file keeps working if the image size changes.
    """

    x0: float = 0.25
    y0: float = 0.25
    x1: float = 0.75
    y1: float = 0.75
    # Image the rectangle was drawn on (informational; limits can be recomputed).
    source_image: str = ""
    # "global": compute limits once on source_image and reuse for every image.
    # "per_image": apply the same rectangle to each image separately.
    scope: str = "global"

    def pixel_bounds(self, height: int, width: int) -> Tuple[int, int, int, int]:
        """Return (row0, row1, col0, col1) clamped to the image, always non-empty."""
        r0 = int(round(min(self.y0, self.y1) * height))
        r1 = int(round(max(self.y0, self.y1) * height))
        c0 = int(round(min(self.x0, self.x1) * width))
        c1 = int(round(max(self.x0, self.x1) * width))
        r0 = max(0, min(r0, height - 1))
        c0 = max(0, min(c0, width - 1))
        r1 = max(r0 + 1, min(r1, height))
        c1 = max(c0 + 1, min(c1, width))
        return r0, r1, c0, c1

    def validate(self) -> List[str]:
        problems = []
        for attr in ("x0", "y0", "x1", "y1"):
            v = getattr(self, attr)
            if not 0.0 <= v <= 1.0:
                problems.append(f"reference ROI: {attr}={v} is outside 0..1")
        if abs(self.x1 - self.x0) < 1e-6 or abs(self.y1 - self.y0) < 1e-6:
            problems.append("reference ROI: rectangle has zero width or height")
        if self.scope not in ("global", "per_image"):
            problems.append(f"reference ROI: unknown scope {self.scope!r}")
        return problems


@dataclass
class AnalysisROI:
    """Region the measurements are restricted to, identical for every image.

    The exported images are always the full field; only counting, area, volume
    and intensity are confined to this rectangle. Use it to exclude the chip
    pillars or an out-of-focus margin without cropping the figures.

    Coordinates are fractions of the image width/height, so one rectangle
    travels between acquisitions of different pixel dimensions.
    """

    enabled: bool = False
    x0: float = 0.05
    y0: float = 0.05
    x1: float = 0.95
    y1: float = 0.95
    # Draw the rectangle on the exported overlay, so what was measured is
    # visible in the picture rather than only in the settings file.
    show_on_overlay: bool = True

    def pixel_bounds(self, height: int, width: int) -> Tuple[int, int, int, int]:
        """(row0, row1, col0, col1) clamped to the image, always non-empty."""
        r0 = int(round(min(self.y0, self.y1) * height))
        r1 = int(round(max(self.y0, self.y1) * height))
        c0 = int(round(min(self.x0, self.x1) * width))
        c1 = int(round(max(self.x0, self.x1) * width))
        r0 = max(0, min(r0, height - 1))
        c0 = max(0, min(c0, width - 1))
        r1 = max(r0 + 1, min(r1, height))
        c1 = max(c0 + 1, min(c1, width))
        return r0, r1, c0, c1

    def validate(self) -> List[str]:
        problems = []
        if not self.enabled:
            return problems
        for attr in ("x0", "y0", "x1", "y1"):
            value = getattr(self, attr)
            if not 0.0 <= value <= 1.0:
                problems.append(f"analysis region: {attr}={value} is outside 0..1")
        if abs(self.x1 - self.x0) < 1e-3 or abs(self.y1 - self.y0) < 1e-3:
            problems.append("analysis region: the rectangle is too small to measure")
        return problems


@dataclass
class SegmentationSettings:
    """3D watershed nucleus counting on the z-stack.

    Counting runs on the full stack rather than the projection precisely so that
    nuclei that overlap in XY but sit at different depths are separated.
    """

    enabled: bool = True
    channel_index: int = 0  # which channel holds the nuclei

    smoothing_xy_um: float = 0.7
    smoothing_z_um: float = 1.5

    threshold_method: str = "otsu"
    threshold_value: float = 0.0
    threshold_percentile: float = 99.0

    # Objects outside this volume range are discarded (in cubic microns).
    min_volume_um3: float = 150.0
    max_volume_um3: float = 20000.0

    # Expected nucleus radius, used for marker separation in the watershed.
    nucleus_radius_um: float = 4.0
    # Distance map smoothing before seed detection, as a fraction of the
    # nucleus radius. Raise it if single nuclei are being cut in two, lower it
    # if touching nuclei are being merged. 0 disables the smoothing.
    seed_smoothing_factor: float = 0.25

    fill_holes: bool = True
    exclude_xy_border: bool = False
    # Downsample XY by this factor before segmenting (1 = full resolution).
    downsample_xy: int = 1
    save_label_image: bool = True
    # The annotated counting image: nucleus outlines over the nuclei channel,
    # with the total in the corner.
    save_overlay: bool = True
    overlay_formats: List[str] = field(default_factory=lambda: ["png"])
    # Write each nucleus's id next to it. The ids match the cell_id column in
    # cells_per_object.csv, so a cell in the picture can be traced to its row.
    annotate_cell_ids: bool = False
    # 0 = size the digits from the nuclei themselves.
    cell_id_font_pt: int = 0

    def validate(self) -> List[str]:
        problems = []
        if self.threshold_method not in THRESHOLD_METHODS:
            problems.append(f"segmentation: unknown threshold method {self.threshold_method!r}")
        if self.min_volume_um3 < 0:
            problems.append("segmentation: min volume must be >= 0")
        if self.max_volume_um3 <= self.min_volume_um3:
            problems.append("segmentation: max volume must exceed min volume")
        if self.nucleus_radius_um <= 0:
            problems.append("segmentation: nucleus radius must be positive")
        if self.seed_smoothing_factor < 0:
            problems.append("segmentation: seed smoothing factor must be >= 0")
        if self.downsample_xy < 1:
            problems.append("segmentation: XY downsample factor must be >= 1")
        if self.cell_id_font_pt < 0:
            problems.append("segmentation: cell id font size must be >= 0")
        allowed = {"png", "tif", "tiff", "jpg", "jpeg"}
        for fmt in self.overlay_formats:
            if fmt.lower() not in allowed:
                problems.append(f"segmentation: unsupported overlay format {fmt!r}")
        return problems


@dataclass
class OutputSettings:
    save_composite: bool = True
    composite_formats: List[str] = field(default_factory=lambda: ["png", "tif"])
    save_single_channels: bool = False
    single_channel_formats: List[str] = field(default_factory=lambda: ["png"])

    save_isometric_3d: bool = False
    save_orthogonal_view: bool = False
    isometric_elevation_deg: float = 28.0
    isometric_azimuth_deg: float = 35.0

    scale_bar: bool = True
    scale_bar_fraction: float = 0.1  # target length as a fraction of image width
    scale_bar_height_px: int = 10
    scale_bar_font_pt: int = 24

    projection: str = "max"
    write_statistics_csv: bool = True
    write_per_object_csv: bool = False
    overwrite_existing: bool = True

    def validate(self) -> List[str]:
        problems = []
        if self.projection not in PROJECTIONS:
            problems.append(f"output: unknown projection {self.projection!r}")
        allowed = {"png", "tif", "tiff", "jpg", "jpeg"}
        for fmt in list(self.composite_formats) + list(self.single_channel_formats):
            if fmt.lower() not in allowed:
                problems.append(f"output: unsupported image format {fmt!r}")
        if not 0.01 <= self.scale_bar_fraction <= 0.9:
            problems.append("output: scale bar fraction must be between 0.01 and 0.9")
        return problems


@dataclass
class Settings:
    """Everything needed to reproduce a run."""

    version: int = SETTINGS_VERSION
    input_dir: str = ""
    output_dir: str = ""
    # Empty means "every readable image in input_dir".
    selected_files: List[str] = field(default_factory=list)

    channels: List[ChannelSettings] = field(default_factory=list)
    reference_roi: ReferenceROI = field(default_factory=ReferenceROI)
    analysis_roi: AnalysisROI = field(default_factory=AnalysisROI)
    segmentation: SegmentationSettings = field(default_factory=SegmentationSettings)
    output: OutputSettings = field(default_factory=OutputSettings)

    # How many images are sampled to work out the batch-wide saturation limits
    # and thresholds. 0 means every selected image. Percentiles and Otsu are
    # stable well before then, and each sampled image costs a read.
    batch_sample_images: int = 8

    # Hand-assigned plot groups: image name -> group label. Overrides the group
    # pattern for the images listed, so an odd chamber can be pulled out of a
    # condition without renaming files.
    image_groups: Dict[str, str] = field(default_factory=dict)

    # 0 means "decide from available RAM and core count at run time".
    max_workers: int = 0
    # Override the voxel size read from the file (microns). None = use metadata.
    pixel_size_um: Optional[float] = None
    z_step_um: Optional[float] = None

    # ---------------------------------------------------------------- helpers
    @property
    def n_channels(self) -> int:
        return len(self.channels)

    def channel_names(self) -> List[str]:
        return [c.name for c in self.channels]

    def validate(self) -> List[str]:
        problems: List[str] = []
        if not self.channels:
            problems.append("no channels configured")
        names = [c.name.strip().lower() for c in self.channels]
        if len(set(names)) != len(names):
            problems.append("channel names must be unique (they are used as file names)")
        for i, ch in enumerate(self.channels):
            problems.extend(ch.validate(i))
        if any(c.limit_mode == "roi_reference" for c in self.channels):
            problems.extend(self.reference_roi.validate())
        problems.extend(self.analysis_roi.validate())
        if self.segmentation.enabled:
            problems.extend(self.segmentation.validate())
            if not 0 <= self.segmentation.channel_index < max(1, self.n_channels):
                problems.append(
                    f"segmentation: nuclei channel index {self.segmentation.channel_index} "
                    f"is outside the {self.n_channels} configured channels"
                )
        problems.extend(self.output.validate())
        if self.max_workers < 0:
            problems.append("max workers must be >= 0")
        if self.batch_sample_images < 0:
            problems.append("batch sample size must be >= 0")
        if self.pixel_size_um is not None and self.pixel_size_um <= 0:
            problems.append("pixel size override must be positive")
        if self.z_step_um is not None and self.z_step_um <= 0:
            problems.append("z step override must be positive")

        # Relative metrics are meaningless without a cell count.
        if not self.segmentation.enabled and any(c.measures_anything for c in self.channels):
            problems.append(
                "cell counting is disabled, so the 'relative' (per-cell) columns "
                "cannot be computed -- enable segmentation or ignore those columns"
            )
        return problems

    # ------------------------------------------------------------ (de)serialise
    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        # Stamped into every saved settings file so a shared run carries its
        # provenance with it.
        data["created_by"] = CREDIT
        return data

    def save(self, path: str) -> None:
        directory = os.path.dirname(os.path.abspath(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=False)
            fh.write("\n")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        data = dict(data or {})
        version = int(data.get("version", SETTINGS_VERSION))
        if version > SETTINGS_VERSION:
            raise ValueError(
                f"settings file version {version} is newer than this tool "
                f"understands (version {SETTINGS_VERSION})"
            )

        settings = cls()
        settings.version = SETTINGS_VERSION
        settings.input_dir = str(data.get("input_dir", "") or "")
        settings.output_dir = str(data.get("output_dir", "") or "")
        settings.selected_files = [str(f) for f in data.get("selected_files", []) or []]
        settings.image_groups = {
            str(k): str(v) for k, v in (data.get("image_groups") or {}).items()
        }
        settings.max_workers = int(data.get("max_workers", 0) or 0)

        for key in ("pixel_size_um", "z_step_um"):
            value = data.get(key)
            setattr(settings, key, None if value in (None, "") else float(value))

        settings.channels = [_load_dc(ChannelSettings, c) for c in data.get("channels", []) or []]
        settings.reference_roi = _load_dc(ReferenceROI, data.get("reference_roi"))
        settings.analysis_roi = _load_dc(AnalysisROI, data.get("analysis_roi"))
        settings.segmentation = _load_dc(SegmentationSettings, data.get("segmentation"))
        settings.output = _load_dc(OutputSettings, data.get("output"))
        return settings

    @classmethod
    def load(cls, path: str) -> "Settings":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def _load_dc(dc_type, data: Optional[Dict[str, Any]]):
    """Build a dataclass from a dict, ignoring unknown keys and coercing types."""
    instance = dc_type()
    if not isinstance(data, dict):
        return instance
    known = {f.name: f for f in fields(dc_type)}
    for key, value in data.items():
        if key not in known:
            continue  # forward compatibility: silently drop unknown keys
        current = getattr(instance, key)
        if isinstance(current, list):
            setattr(instance, key, list(value) if isinstance(value, list) else current)
        else:
            try:
                setattr(instance, key, _coerce(value, current))
            except (TypeError, ValueError):
                pass  # keep the default rather than fail the whole load
    return instance


def default_settings_for(n_channels: int, channel_names: Optional[List[str]] = None) -> Settings:
    """Sensible starting point for a file with `n_channels` channels."""
    palette = ["Blue", "Bluish Green", "Orange", "Pink", "Light Blue", "Amber", "White"]
    settings = Settings()
    for i in range(n_channels):
        name = channel_names[i] if channel_names and i < len(channel_names) else f"Channel{i + 1}"
        settings.channels.append(
            ChannelSettings(name=name, color=palette[i % len(palette)])
        )
    if settings.channels:
        # The first channel is the usual nuclear stain; measure it by default.
        settings.channels[0].measure_area = True
        settings.segmentation.channel_index = 0
    return settings
