"""Nucleus counting by 3D watershed.

The whole point of working on the z-stack rather than the max projection is
that two nuclei sitting on top of each other are one blob in the projection but
two well-separated blobs in the volume. Every distance here is handled in
microns and converted with the real voxel size, which matters because these
stacks are strongly anisotropic (0.72 um in XY against 2.5 um in Z).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .config import SegmentationSettings


@dataclass
class SegmentationResult:
    labels: np.ndarray  # (Z, Y, X) int32 label volume, 0 = background
    n_cells: int
    voxel_volume_um3: float
    object_volumes_um3: np.ndarray
    object_centroids_um: np.ndarray  # (N, 3) as (z, y, x) in microns
    threshold_value: float
    downsample_xy: int

    def per_object_rows(self, image_name: str) -> List[Dict[str, object]]:
        rows = []
        for i, (vol, centroid) in enumerate(
            zip(self.object_volumes_um3, self.object_centroids_um), start=1
        ):
            rows.append(
                {
                    "image": image_name,
                    "cell_id": i,
                    "volume_um3": float(vol),
                    "centroid_z_um": float(centroid[0]),
                    "centroid_y_um": float(centroid[1]),
                    "centroid_x_um": float(centroid[2]),
                    "equivalent_diameter_um": float(
                        2.0 * (3.0 * vol / (4.0 * np.pi)) ** (1.0 / 3.0)
                    ),
                }
            )
        return rows


def compute_threshold(
    data: np.ndarray,
    method: str,
    manual_value: float = 0.0,
    percentile: float = 99.0,
) -> float:
    """Intensity above which a voxel counts as signal."""
    from skimage import filters

    finite = np.asarray(data)
    if method == "manual":
        return float(manual_value)
    if method == "percentile":
        return float(np.percentile(_subsample(finite), percentile))
    if method == "mean":
        return float(finite.mean())

    sample = _subsample(finite)
    try:
        if method == "otsu":
            return float(filters.threshold_otsu(sample))
        if method == "triangle":
            return float(filters.threshold_triangle(sample))
        if method == "li":
            return float(filters.threshold_li(sample))
        if method == "yen":
            return float(filters.threshold_yen(sample))
    except (ValueError, RuntimeError):
        # A flat or empty image defeats every automatic method.
        return float(sample.mean()) if sample.size else 0.0
    raise ValueError(f"unknown threshold method {method!r}")


def _distance_transform(mask: np.ndarray, spacing: Tuple[float, float, float]) -> np.ndarray:
    """Anisotropic Euclidean distance transform, in microns, as float32.

    `edt` is used when available: SciPy's version only works in float64 and
    allocates several full-size intermediates, which on a 57x1360x1360 stack
    peaks around 5 GB against roughly 1 GB here. The two agree to ~1e-6.
    """
    try:
        import edt as _edt
    except ImportError:
        from scipy import ndimage as ndi

        result = ndi.distance_transform_edt(mask, sampling=spacing)
        return np.asarray(result, dtype=np.float32)

    return _edt.edt(
        np.ascontiguousarray(mask),
        anisotropy=tuple(float(s) for s in spacing),
        black_border=False,
        parallel=0,  # 0 = use every available core
    )


def _subsample(data: np.ndarray, limit: int = 8_000_000) -> np.ndarray:
    flat = np.asarray(data).ravel()
    if flat.size > limit:
        step = flat.size // limit + 1
        return flat[::step]
    return flat


def segment_nuclei(
    volume: np.ndarray,
    settings: SegmentationSettings,
    pixel_size_um: float,
    z_step_um: float,
    progress=None,
) -> SegmentationResult:
    """Label individual nuclei in a (Z, Y, X) volume.

    Pipeline: anisotropic Gaussian smoothing -> threshold -> hole filling ->
    3D Euclidean distance transform in real microns -> local maxima as
    watershed seeds -> watershed -> volume filtering.
    """
    from scipy import ndimage as ndi
    from skimage.feature import peak_local_max
    from skimage.measure import regionprops
    from skimage.morphology import remove_small_objects
    from skimage.segmentation import watershed, clear_border

    def report(message: str) -> None:
        if progress is not None:
            progress(message)

    step = max(1, int(settings.downsample_xy))
    if step > 1:
        volume = volume[:, ::step, ::step]
    dy = dx = pixel_size_um * step
    dz = z_step_um
    voxel_volume = dx * dy * dz
    spacing = (dz, dy, dx)

    data = volume.astype(np.float32, copy=False)

    # --- smooth ---------------------------------------------------------
    report("smoothing")
    sigma = (
        settings.smoothing_z_um / dz if dz > 0 else 0.0,
        settings.smoothing_xy_um / dy if dy > 0 else 0.0,
        settings.smoothing_xy_um / dx if dx > 0 else 0.0,
    )
    if any(s > 0 for s in sigma):
        smoothed = ndi.gaussian_filter(data, sigma=sigma, mode="nearest")
    else:
        smoothed = data

    # --- threshold -------------------------------------------------------
    report("thresholding")
    threshold = compute_threshold(
        smoothed,
        settings.threshold_method,
        settings.threshold_value,
        settings.threshold_percentile,
    )
    mask = smoothed > threshold
    if smoothed is not data:
        del smoothed
    del data

    if settings.fill_holes:
        # Fill slice by slice: a 3D fill would close the hollow centre of a
        # ring of nuclei and merge them into one object.
        for z in range(mask.shape[0]):
            mask[z] = ndi.binary_fill_holes(mask[z])

    min_voxels = max(1, int(round(settings.min_volume_um3 / max(voxel_volume, 1e-12))))
    if mask.any():
        mask = remove_small_objects(mask, min_size=min_voxels)

    if not mask.any():
        return SegmentationResult(
            labels=np.zeros(volume.shape, dtype=np.int32),
            n_cells=0,
            voxel_volume_um3=voxel_volume,
            object_volumes_um3=np.zeros(0, dtype=np.float64),
            object_centroids_um=np.zeros((0, 3), dtype=np.float64),
            threshold_value=threshold,
            downsample_xy=step,
        )

    # --- distance transform, in microns ----------------------------------
    report("distance transform")
    distance = _distance_transform(mask, spacing)

    # A ragged nucleus boundary puts several local maxima inside one nucleus,
    # which the watershed would then cut into pieces. Smoothing the distance
    # map at a fraction of the nucleus radius merges those spurious maxima
    # without pulling genuinely separate nuclei together.
    if settings.seed_smoothing_factor > 0:
        seed_sigma_um = settings.nucleus_radius_um * settings.seed_smoothing_factor
        seed_sigma = tuple(seed_sigma_um / s if s > 0 else 0.0 for s in spacing)
        ndi.gaussian_filter(distance, sigma=seed_sigma, mode="nearest", output=distance)

    # --- seeds ------------------------------------------------------------
    report("finding seeds")
    # A footprint sized in microns, so the minimum separation between two seeds
    # is physically meaningful despite the anisotropic voxels.
    radius = max(settings.nucleus_radius_um, 1e-6)
    footprint_shape = tuple(
        max(3, int(2 * round(radius / s)) + 1) if s > 0 else 3 for s in spacing
    )
    footprint_shape = tuple(min(f, max(1, d)) for f, d in zip(footprint_shape, mask.shape))
    footprint = np.ones(footprint_shape, dtype=bool)

    peaks = peak_local_max(
        distance,
        footprint=footprint,
        labels=mask,
        exclude_border=False,
    )
    # The watershed output follows the marker dtype, and these arrays get
    # padded internally, so the narrowest safe integer saves several hundred MB.
    marker_dtype = np.int32 if len(peaks) >= 32000 else np.int16
    markers = np.zeros(mask.shape, dtype=marker_dtype)
    if len(peaks):
        markers[tuple(peaks.T)] = np.arange(1, len(peaks) + 1, dtype=marker_dtype)
    else:
        # No maxima survived: fall back to one marker per connected component.
        markers, _ = ndi.label(mask)
        if markers.max() < 32000:
            markers = markers.astype(np.int16)

    # --- watershed ---------------------------------------------------------
    report("watershed")
    # Negate in place rather than building another full-size array.
    np.negative(distance, out=distance)
    labels = watershed(distance, markers, mask=mask)
    del distance, markers

    if settings.exclude_xy_border:
        # Only XY: objects clipped by the top or bottom of the stack are real
        # cells that happen to be partly outside the acquired depth.
        border_mask = np.zeros(labels.shape, dtype=bool)
        border_mask[:, 0, :] = border_mask[:, -1, :] = True
        border_mask[:, :, 0] = border_mask[:, :, -1] = True
        touching = np.unique(labels[border_mask])
        for lab in touching:
            if lab:
                labels[labels == lab] = 0

    # --- filter by volume ---------------------------------------------------
    report("filtering objects")
    counts = np.bincount(labels.ravel())
    if counts.size:
        counts[0] = 0
    max_voxels = int(round(settings.max_volume_um3 / max(voxel_volume, 1e-12)))
    keep = (counts >= min_voxels) & (counts <= max_voxels)
    keep_ids = np.flatnonzero(keep)

    # Relabel survivors to 1..N so the label image and the CSV agree.
    remap = np.zeros(counts.size, dtype=np.int32)
    remap[keep_ids] = np.arange(1, keep_ids.size + 1, dtype=np.int32)
    labels = remap[labels]

    volumes = counts[keep_ids].astype(np.float64) * voxel_volume
    centroids = np.zeros((keep_ids.size, 3), dtype=np.float64)
    if keep_ids.size:
        props = regionprops(labels)
        for prop in props:
            centroids[prop.label - 1] = np.asarray(prop.centroid) * np.asarray(spacing)

    return SegmentationResult(
        labels=labels,
        n_cells=int(keep_ids.size),
        voxel_volume_um3=voxel_volume,
        object_volumes_um3=volumes,
        object_centroids_um=centroids,
        threshold_value=float(threshold),
        downsample_xy=step,
    )


def labels_to_outline_overlay(
    labels: np.ndarray,
    background_rgb: np.ndarray,
    outline_rgb: Tuple[float, float, float] = (1.0, 1.0, 0.0),
) -> np.ndarray:
    """Draw the outlines of the labelled nuclei over an RGB projection.

    Outlines come from the label projection, so every counted nucleus shows up
    even when it sits underneath another one.
    """
    from skimage.segmentation import find_boundaries

    flat_labels = labels.max(axis=0) if labels.ndim == 3 else labels
    if flat_labels.shape != background_rgb.shape[:2]:
        flat_labels = _resize_nearest(flat_labels, background_rgb.shape[:2])

    boundaries = find_boundaries(flat_labels, mode="outer")
    overlay = background_rgb.astype(np.float32, copy=True)
    overlay[boundaries] = np.asarray(outline_rgb, dtype=np.float32)
    return overlay


def _resize_nearest(image: np.ndarray, shape: Tuple[int, int]) -> np.ndarray:
    rows = (np.arange(shape[0]) * image.shape[0] // shape[0]).clip(0, image.shape[0] - 1)
    cols = (np.arange(shape[1]) * image.shape[1] // shape[1]).clip(0, image.shape[1] - 1)
    return image[rows[:, None], cols[None, :]]
