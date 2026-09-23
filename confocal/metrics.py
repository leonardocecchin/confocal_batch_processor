"""Per-channel quantification.

Definitions used throughout (all on raw or background-subtracted intensities,
never on the display-scaled image):

  total area      -- area of signal-positive pixels in the Z projection (um^2)
  total volume    -- volume of signal-positive voxels in the stack (um^3)
  total intensity -- sum of intensities over signal-positive voxels (a.u.)

  relative <x>    -- total <x> divided by the number of counted cells, i.e.
                     the per-cell value the user asked for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

import numpy as np

from .config import ChannelSettings
from .segmentation import compute_threshold


@dataclass
class ChannelMetrics:
    channel_name: str
    threshold: float = float("nan")
    total_area_um2: Optional[float] = None
    total_volume_um3: Optional[float] = None
    total_intensity: Optional[float] = None
    relative_area_um2: Optional[float] = None
    relative_volume_um3: Optional[float] = None
    relative_intensity: Optional[float] = None
    area_fraction_fov: Optional[float] = None
    volume_fraction_fov: Optional[float] = None
    mean_intensity_in_signal: Optional[float] = None
    extra: Dict[str, float] = field(default_factory=dict)

    def as_columns(self) -> Dict[str, object]:
        """Flatten into CSV columns prefixed with the channel name."""
        prefix = self.channel_name
        columns: Dict[str, object] = {f"{prefix}_threshold": self.threshold}
        mapping = {
            "total_area_um2": self.total_area_um2,
            "relative_area_um2_per_cell": self.relative_area_um2,
            "area_fraction_fov": self.area_fraction_fov,
            "total_volume_um3": self.total_volume_um3,
            "relative_volume_um3_per_cell": self.relative_volume_um3,
            "volume_fraction_fov": self.volume_fraction_fov,
            "total_intensity": self.total_intensity,
            "relative_intensity_per_cell": self.relative_intensity,
            "mean_intensity_in_signal": self.mean_intensity_in_signal,
        }
        for key, value in mapping.items():
            if value is not None:
                columns[f"{prefix}_{key}"] = value
        for key, value in self.extra.items():
            columns[f"{prefix}_{key}"] = value
        return columns


def measure_channel(
    channel: ChannelSettings,
    volume: np.ndarray,
    projection: np.ndarray,
    pixel_area_um2: float,
    voxel_volume_um3: float,
    n_cells: Optional[int],
    threshold_override: Optional[float] = None,
) -> ChannelMetrics:
    """Measure one channel according to the boxes ticked for it.

    `volume` and `projection` must already be background-subtracted if that
    option is on, so the threshold sees the same data the measurement does.

    Measurements are made on these intensities and never on the display-scaled
    image, so the saturation limits and the display curve cannot influence any
    number reported here.

    `threshold_override` is the batch-wide threshold when the channel is set to
    "batch" scope; using one cut-off for every image is what makes the totals
    comparable between images.
    """
    metrics = ChannelMetrics(channel_name=channel.name)
    if not channel.measures_anything:
        return metrics

    if threshold_override is not None:
        threshold = float(threshold_override)
    else:
        needs_volume = channel.measure_volume or channel.measure_intensity
        # Threshold once, on the data that decides the most voxels.
        source = volume if needs_volume else projection
        threshold = compute_threshold(
            source,
            channel.threshold_method,
            channel.threshold_value,
            channel.threshold_percentile,
        )
    metrics.threshold = float(threshold)

    cells = n_cells if n_cells and n_cells > 0 else None

    if channel.measure_area:
        positive = projection > threshold
        n_positive = int(np.count_nonzero(positive))
        total_area = n_positive * pixel_area_um2
        metrics.total_area_um2 = total_area
        metrics.area_fraction_fov = n_positive / float(projection.size)
        if cells:
            metrics.relative_area_um2 = total_area / cells

    if channel.measure_volume or channel.measure_intensity:
        positive_3d = volume > threshold
        n_positive_3d = int(np.count_nonzero(positive_3d))

        if channel.measure_volume:
            total_volume = n_positive_3d * voxel_volume_um3
            metrics.total_volume_um3 = total_volume
            metrics.volume_fraction_fov = n_positive_3d / float(volume.size)
            if cells:
                metrics.relative_volume_um3 = total_volume / cells

        if channel.measure_intensity:
            # `where=` avoids materialising a copy of every signal voxel, which
            # for a bright channel is most of a 400 MB volume.
            total_intensity = float(np.sum(volume, where=positive_3d, dtype=np.float64))
            metrics.total_intensity = total_intensity
            metrics.mean_intensity_in_signal = (
                total_intensity / n_positive_3d if n_positive_3d else 0.0
            )
            if cells:
                metrics.relative_intensity = total_intensity / cells

    return metrics
