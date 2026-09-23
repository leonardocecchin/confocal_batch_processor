"""Display scaling: saturation limits, intensity transforms and colour LUTs.

Nothing in here touches the numbers used for statistics. Display scaling is a
figure-making decision; the measurements in `metrics.py` always run on the raw
(optionally background-subtracted) intensities.
"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np

from .config import COLOR_LUTS, ChannelSettings, ReferenceROI


def project(volume: np.ndarray, method: str = "max") -> np.ndarray:
    """Collapse a (Z, Y, X) volume along Z."""
    if volume.ndim == 2:
        return volume
    if method == "max":
        return volume.max(axis=0)
    if method == "mean":
        return volume.mean(axis=0, dtype=np.float32)
    if method == "sum":
        return volume.sum(axis=0, dtype=np.float32)
    raise ValueError(f"unknown projection {method!r}")


def subtract_background(image: np.ndarray, radius_um: float, pixel_size_um: float) -> np.ndarray:
    """Remove smooth, large-scale background.

    A Gaussian high-pass standing in for Fiji's rolling-ball: for a 1360x1360
    stack the true rolling ball is minutes per slice, this is milliseconds and
    visually equivalent for the smooth illumination gradients seen here.

    On a 3D volume the filter is applied within each z-slice only. Blurring
    along z as well would mix planes that are microns apart and subtract real
    signal rather than background.
    """
    if radius_um <= 0:
        return image.astype(np.float32, copy=False)
    sigma_px = max(1.0, radius_um / max(pixel_size_um, 1e-9))
    data = image.astype(np.float32, copy=False)

    if data.ndim == 3:
        # Work in place when the caller handed us a float32 array it owns; a
        # whole extra copy of one of these volumes is ~400 MB.
        out = data if (data.dtype == np.float32 and image is not data) else np.empty_like(data)
        for z in range(data.shape[0]):
            plane = data[z]
            np.subtract(plane, _smooth_background(plane, sigma_px), out=out[z])
        np.clip(out, 0, None, out=out)
        return out

    return np.clip(data - _smooth_background(data, sigma_px), 0, None)


def _smooth_background(plane: np.ndarray, sigma_px: float) -> np.ndarray:
    """Heavily blurred copy of one slice, used as the background estimate.

    A Gaussian with sigma ~49 px needs a ~200 px kernel and costs about a
    second per slice. The background is smooth by construction, so it is
    estimated on a shrunken copy and scaled back up: mean error well under one
    grey level, and about twenty times faster.
    """
    from scipy.ndimage import gaussian_filter

    if sigma_px <= 8:
        return gaussian_filter(plane, sigma=sigma_px, mode="nearest")

    from skimage.transform import resize

    # sigma/8 costs the same as a coarser grid here (the upscale dominates)
    # and keeps the error well under one grey level.
    factor = max(2, int(sigma_px // 8))
    small = plane[::factor, ::factor]
    blurred = gaussian_filter(small, sigma=sigma_px / factor, mode="nearest")
    return resize(
        blurred, plane.shape, order=1, mode="edge", anti_aliasing=False, preserve_range=True
    ).astype(np.float32, copy=False)


def limits_from_percentiles(
    data: np.ndarray, low_percentile: float, high_percentile: float
) -> Tuple[float, float]:
    """Saturation window from percentiles of the supplied pixels."""
    flat = np.asarray(data).ravel()
    if flat.size == 0:
        return 0.0, 1.0
    # A subsample is plenty for a percentile and keeps this cheap on big stacks.
    if flat.size > 4_000_000:
        step = flat.size // 4_000_000 + 1
        flat = flat[::step]
    lo = float(np.percentile(flat, low_percentile))
    hi = float(np.percentile(flat, high_percentile))
    if not np.isfinite(lo):
        lo = 0.0
    if not np.isfinite(hi) or hi <= lo:
        hi = lo + 1.0
    return lo, hi


def resolve_limits(
    channel: ChannelSettings,
    projection: np.ndarray,
    roi: Optional[ReferenceROI] = None,
    roi_limits: Optional[Tuple[float, float]] = None,
) -> Tuple[float, float]:
    """Final (vmin, vmax) for a channel, honouring its limit mode.

    `roi_limits` is the window precomputed for the whole batch -- from a global
    reference ROI or from pooled percentiles. When given it wins, so every
    image in the batch shares one window.

    None of this affects the statistics: display scaling is applied after the
    measurements are taken, never before.
    """
    mode = channel.limit_mode
    if mode == "manual":
        return float(channel.vmin), float(channel.vmax)

    if mode == "auto_percentile_batch":
        if roi_limits is not None:
            return roi_limits
        # No calibration supplied (e.g. a standalone preview of one image):
        # fall back to this image's own percentiles rather than failing.
        return limits_from_percentiles(
            projection, channel.low_percentile, channel.high_percentile
        )

    if mode == "roi_reference":
        if roi_limits is not None:
            return roi_limits
        if roi is None:
            raise ValueError(
                f"channel {channel.name!r} uses a reference ROI but none was supplied"
            )
        r0, r1, c0, c1 = roi.pixel_bounds(projection.shape[0], projection.shape[1])
        patch = projection[r0:r1, c0:c1]
        return limits_from_percentiles(patch, channel.low_percentile, channel.high_percentile)

    # auto_percentile
    return limits_from_percentiles(projection, channel.low_percentile, channel.high_percentile)


def apply_display_transform(
    image: np.ndarray,
    vmin: float,
    vmax: float,
    transform: str = "linear",
    gamma: float = 0.5,
) -> np.ndarray:
    """Map intensities to [0, 1] through the chosen display curve.

    linear -- what Fiji's setMinAndMax does.
    gamma  -- lifts dim signal; gamma < 1 brightens. The usual choice for
              publication figures with a wide dynamic range.
    log    -- strongest compression; useful when signals differ by orders of
              magnitude, but the result is no longer proportional to intensity,
              so only ever use it for looking, never for measuring.
    """
    data = np.asarray(image, dtype=np.float32)
    span = float(vmax) - float(vmin)
    if span <= 0:
        span = 1.0
    normalised = np.clip((data - float(vmin)) / span, 0.0, 1.0)

    if transform == "linear":
        return normalised
    if transform == "gamma":
        g = max(float(gamma), 1e-6)
        return np.power(normalised, g, dtype=np.float32)
    if transform == "log":
        # log1p(k*x)/log1p(k); k sets the compression strength.
        k = np.float32(1000.0)
        return (np.log1p(k * normalised) / np.log1p(k)).astype(np.float32)
    raise ValueError(f"unknown display transform {transform!r}")


def colorize(normalised: np.ndarray, color_name: str) -> np.ndarray:
    """Turn a [0, 1] image into an (H, W, 3) float image in the channel colour.

    This reproduces the macro's black-to-colour gradient LUT.
    """
    try:
        rgb = COLOR_LUTS[color_name]
    except KeyError as exc:
        raise ValueError(f"unknown colour {color_name!r}") from exc
    scale = np.asarray(rgb, dtype=np.float32) / 255.0
    return normalised[..., np.newaxis].astype(np.float32) * scale


def to_uint8(rgb_float: np.ndarray) -> np.ndarray:
    """Clip a float RGB image in [0, 1] to uint8."""
    return np.clip(rgb_float * 255.0 + 0.5, 0, 255).astype(np.uint8)


def merge_composite(layers: list) -> np.ndarray:
    """Combine coloured channel layers the way Fiji's composite mode does.

    Fiji's "Composite" display sums the channel LUTs and clips, which is what
    makes co-localised signal read as the additive mix of the two colours.
    """
    if not layers:
        raise ValueError("no layers to merge")
    stacked = np.zeros_like(layers[0], dtype=np.float32)
    for layer in layers:
        stacked += layer
    return np.clip(stacked, 0.0, 1.0)
