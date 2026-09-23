"""Figure making: scale bars, composites, and 3D views of the chip."""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .config import OutputSettings


def nice_scale_bar_length(image_width_um: float, target_fraction: float = 0.1) -> float:
    """Round a target bar length to the nearest 1/2/5 x 10^n, as Fiji does."""
    target = max(image_width_um * target_fraction, 1e-9)
    exponent = math.floor(math.log10(target))
    base = 10.0**exponent
    for multiple in (1, 2, 5, 10):
        if multiple * base >= target:
            return multiple * base
    return 10.0 * base


def format_length(length_um: float) -> str:
    """Human label for a bar length, switching to mm / nm where it reads better."""
    if length_um >= 1000:
        value = length_um / 1000.0
        unit = "mm"
    elif length_um < 1:
        value = length_um * 1000.0
        unit = "nm"
    else:
        value = length_um
        unit = "µm"
    text = f"{value:g}"
    return f"{text} {unit}"


def draw_scale_bar(
    rgb: np.ndarray,
    pixel_size_um: float,
    settings: OutputSettings,
    color: Tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """Burn a labelled scale bar into the lower-right corner of an RGB image."""
    if not settings.scale_bar or pixel_size_um <= 0:
        return rgb

    height, width = rgb.shape[:2]
    bar_um = nice_scale_bar_length(width * pixel_size_um, settings.scale_bar_fraction)
    bar_px = int(round(bar_um / pixel_size_um))
    bar_px = max(4, min(bar_px, width - 2))
    bar_h = max(2, int(settings.scale_bar_height_px))

    out = rgb.astype(np.float32, copy=True)
    margin = max(8, width // 60)
    x1 = width - margin
    x0 = max(0, x1 - bar_px)
    y1 = height - margin
    y0 = max(0, y1 - bar_h)
    out[y0:y1, x0:x1] = np.asarray(color, dtype=np.float32)

    label = format_length(bar_um)
    out = _draw_text(
        out,
        label,
        anchor_x=(x0 + x1) // 2,
        baseline_y=y0 - max(4, bar_h // 2),
        font_pt=settings.scale_bar_font_pt,
        color=color,
    )
    return out


def _draw_text(
    rgb: np.ndarray,
    text: str,
    anchor_x: int,
    baseline_y: int,
    font_pt: int,
    color: Tuple[float, float, float],
) -> np.ndarray:
    """Render centred text into a float RGB image using PIL."""
    from PIL import Image, ImageDraw, ImageFont

    height, width = rgb.shape[:2]
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)

    def measure(f):
        try:
            bbox = draw.textbbox((0, 0), text, font=f)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:  # very old Pillow
            return draw.textsize(text, font=f)

    # Shrink until the label fits, so a long caption on a small crop stays
    # readable instead of running off the edge.
    size = max(8, int(font_pt))
    font = _load_font(size)
    text_w, text_h = measure(font)
    while text_w > width - 8 and size > 8:
        size = max(8, int(size * 0.85))
        font = _load_font(size)
        text_w, text_h = measure(font)

    x = int(anchor_x - text_w / 2)
    y = int(baseline_y - text_h)
    x = max(0, min(x, width - text_w - 1))
    y = max(0, min(y, height - text_h - 1))
    draw.text((x, y), text, fill=255, font=font)

    alpha = (np.asarray(canvas, dtype=np.float32) / 255.0)[..., np.newaxis]
    tint = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(rgb * (1.0 - alpha) + tint * alpha, 0.0, 1.0)


def _load_font(font_pt: int):
    from PIL import ImageFont

    # Bare names are resolved by Pillow against the system font directories,
    # which is what makes this work on Windows and macOS as well as Linux.
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "arialbd.ttf",
        "segoeuib.ttf",
        "arial.ttf",
        "segoeui.ttf",
    ]
    size = max(8, int(font_pt))
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except (OSError, IOError):
            continue
    try:
        # Last resort still scales, unlike the fixed-size bitmap default.
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


# --------------------------------------------------------------------- 3D views
def orthogonal_view(
    layers_zyx: Sequence[Tuple[np.ndarray, Tuple[float, float, float]]],
    pixel_size_um: float,
    z_step_um: float,
    gap_px: int = 12,
) -> np.ndarray:
    """Fiji-style XY / XZ / YZ panel.

    `layers_zyx` is a list of (normalised (Z, Y, X) volume, rgb colour). The
    side panels are max projections along Y and X, stretched so that one pixel
    means the same distance in every panel.
    """
    if not layers_zyx:
        raise ValueError("no layers supplied")

    n_z, n_y, n_x = layers_zyx[0][0].shape
    # Depth in pixels at the XY pixel scale, so the panel is isotropic.
    depth_px = max(1, int(round(n_z * z_step_um / max(pixel_size_um, 1e-9))))

    xy = np.zeros((n_y, n_x, 3), dtype=np.float32)
    xz = np.zeros((depth_px, n_x, 3), dtype=np.float32)
    yz = np.zeros((n_y, depth_px, 3), dtype=np.float32)

    for volume, color in layers_zyx:
        tint = np.asarray(color, dtype=np.float32)
        xy += volume.max(axis=0)[..., None] * tint
        # (Z, X) projection along Y, then stretched in Z.
        proj_zx = volume.max(axis=1)
        xz += _stretch_rows(proj_zx, depth_px)[..., None] * tint
        # (Z, Y) projection along X -> transpose to (Y, Z).
        proj_zy = volume.max(axis=2)
        yz += _stretch_rows(proj_zy, depth_px).T[..., None] * tint

    xy = np.clip(xy, 0, 1)
    xz = np.clip(xz, 0, 1)
    yz = np.clip(yz, 0, 1)

    total_h = n_y + gap_px + depth_px
    total_w = n_x + gap_px + depth_px
    canvas = np.zeros((total_h, total_w, 3), dtype=np.float32)
    canvas[:n_y, :n_x] = xy
    canvas[n_y + gap_px :, :n_x] = xz
    canvas[:n_y, n_x + gap_px :] = yz
    return canvas


def _stretch_rows(image: np.ndarray, new_rows: int) -> np.ndarray:
    """Nearest-neighbour resize along axis 0 only."""
    if image.shape[0] == new_rows:
        return image
    idx = (np.arange(new_rows) * image.shape[0] // new_rows).clip(0, image.shape[0] - 1)
    return image[idx]


def isometric_view(
    layers_zyx: Sequence[Tuple[np.ndarray, Tuple[float, float, float]]],
    pixel_size_um: float,
    z_step_um: float,
    elevation_deg: float = 28.0,
    azimuth_deg: float = 35.0,
    max_side_px: int = 700,
) -> np.ndarray:
    """Tilted slab rendering of the stack.

    Every voxel is projected onto the viewing plane with a parallel (isometric)
    projection and composited back to front, so the chip reads as a solid block
    seen from a corner. Deeper slices are dimmed slightly to give depth cueing.
    """
    if not layers_zyx:
        raise ValueError("no layers supplied")

    n_z, n_y, n_x = layers_zyx[0][0].shape

    # Downsample so the render stays quick regardless of the input size.
    step_xy = max(1, int(math.ceil(max(n_y, n_x) / max_side_px)))
    dy = dx = pixel_size_um * step_xy
    dz = z_step_um

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)

    # Camera basis for a parallel projection of world (x, y, z) in microns.
    right = np.array([math.cos(az), -math.sin(az), 0.0])
    up = np.array([-math.sin(az) * math.sin(el), -math.cos(az) * math.sin(el), math.cos(el)])

    sub = [(vol[:, ::step_xy, ::step_xy], np.asarray(c, dtype=np.float32)) for vol, c in layers_zyx]
    n_z, n_y, n_x = sub[0][0].shape

    ys = np.arange(n_y, dtype=np.float32) * dy
    xs = np.arange(n_x, dtype=np.float32) * dx
    grid_y, grid_x = np.meshgrid(ys, xs, indexing="ij")

    # Screen extent, from the eight corners of the bounding box.
    corners = np.array(
        [
            [x, y, z]
            for x in (0.0, (n_x - 1) * dx)
            for y in (0.0, (n_y - 1) * dy)
            for z in (0.0, (n_z - 1) * dz)
        ]
    )
    u_corner = corners @ right
    v_corner = corners @ up
    u_min, u_max = float(u_corner.min()), float(u_corner.max())
    v_min, v_max = float(v_corner.min()), float(v_corner.max())

    scale = max_side_px / max(u_max - u_min, v_max - v_min, 1e-9)
    out_w = int(round((u_max - u_min) * scale)) + 2
    out_h = int(round((v_max - v_min) * scale)) + 2
    canvas = np.zeros((out_h, out_w, 3), dtype=np.float32)

    base_u = grid_x * right[0] + grid_y * right[1]
    base_v = grid_x * up[0] + grid_y * up[1]

    # Back to front so nearer slices paint over farther ones.
    z_order = range(n_z - 1, -1, -1) if up[2] >= 0 else range(n_z)
    for zi in z_order:
        z_um = zi * dz
        u = base_u + z_um * right[2]
        v = base_v + z_um * up[2]
        col = np.clip(((u - u_min) * scale).astype(np.int32), 0, out_w - 1)
        row = np.clip((out_h - 1 - (v - v_min) * scale).astype(np.int32), 0, out_h - 1)

        slice_rgb = np.zeros((n_y, n_x, 3), dtype=np.float32)
        for volume, color in sub:
            slice_rgb += volume[zi][..., None] * color

        # Depth cue: slices further from the viewer lose a little brightness.
        depth_factor = 0.55 + 0.45 * (zi / max(n_z - 1, 1))
        if up[2] < 0:
            depth_factor = 1.0 - depth_factor + 0.55
        slice_rgb *= depth_factor

        bright = slice_rgb.max(axis=2)
        visible = bright > 0.02
        if not visible.any():
            continue
        flat_index = row[visible] * out_w + col[visible]
        values = slice_rgb[visible]
        # Painter's algorithm with "brightest wins" inside a slice, so a single
        # scatter per slice is enough and stays vectorised.
        flat_canvas = canvas.reshape(-1, 3)
        np.maximum.at(flat_canvas, flat_index, values)

    return np.clip(canvas, 0.0, 1.0)


def draw_region_outline(
    rgb: np.ndarray,
    bounds: Tuple[int, int, int, int],
    color: Tuple[float, float, float] = (1.0, 1.0, 1.0),
    thickness: int = 2,
    dash: int = 14,
) -> np.ndarray:
    """Mark the analysed region on a full-field image with a dashed rectangle.

    Dashed rather than solid so it reads as an annotation and cannot be mistaken
    for a structure in the sample.
    """
    r0, r1, c0, c1 = bounds
    out = rgb.astype(np.float32, copy=True)
    height, width = out.shape[:2]
    r0 = max(0, min(r0, height - 1))
    c0 = max(0, min(c0, width - 1))
    r1 = max(r0 + 1, min(r1, height))
    c1 = max(c0 + 1, min(c1, width))
    tint = np.asarray(color, dtype=np.float32)
    t = max(1, int(thickness))

    on = np.zeros(max(width, height), dtype=bool)
    if dash > 0:
        on[(np.arange(on.size) // dash) % 2 == 0] = True
    else:
        on[:] = True

    cols = np.arange(c0, c1)
    rows = np.arange(r0, r1)
    col_on = cols[on[: len(cols)]]
    row_on = rows[on[: len(rows)]]

    out[r0 : min(r0 + t, height), col_on] = tint
    out[max(r1 - t, 0) : r1, col_on] = tint
    out[row_on, c0 : min(c0 + t, width)] = tint
    out[row_on, max(c1 - t, 0) : c1] = tint
    return out


def draw_id_labels(
    rgb: np.ndarray,
    positions: Sequence[Tuple[float, float]],
    texts: Sequence[str],
    font_pt: int = 11,
    color: Tuple[float, float, float] = (1.0, 1.0, 1.0),
    halo: bool = True,
) -> np.ndarray:
    """Write a short label at each (row, col), centred on the point.

    All the labels go onto one canvas and are composited in a single pass:
    a field can hold a thousand nuclei, and rendering each one through its own
    full-size image would take minutes.

    A dark halo keeps the digits readable over bright signal.
    """
    from PIL import Image, ImageDraw

    if len(positions) == 0:
        return rgb

    height, width = rgb.shape[:2]
    canvas = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(canvas)
    font = _load_font(font_pt)

    halo_canvas = Image.new("L", (width, height), 0) if halo else None
    halo_draw = ImageDraw.Draw(halo_canvas) if halo_canvas is not None else None
    stroke = max(1, int(round(font_pt / 7)))

    for (row, col), text in zip(positions, texts):
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        except AttributeError:  # very old Pillow
            text_w, text_h = draw.textsize(text, font=font)
        # Clamp rather than skip: a nucleus at the edge still gets its number,
        # which matters because its label is pushed outwards by the offset.
        x = int(min(max(col - text_w / 2, 1), width - text_w - 1))
        y = int(min(max(row - text_h / 2, 1), height - text_h - 1))
        if halo_draw is not None:
            halo_draw.text((x, y), text, fill=255, font=font, stroke_width=stroke,
                           stroke_fill=255)
        draw.text((x, y), text, fill=255, font=font)

    out = rgb.astype(np.float32, copy=True)
    if halo_canvas is not None:
        halo_alpha = (np.asarray(halo_canvas, dtype=np.float32) / 255.0)[..., np.newaxis]
        out *= 1.0 - halo_alpha  # darken behind the glyphs
    alpha = (np.asarray(canvas, dtype=np.float32) / 255.0)[..., np.newaxis]
    tint = np.asarray(color, dtype=np.float32).reshape(1, 1, 3)
    return np.clip(out * (1.0 - alpha) + tint * alpha, 0.0, 1.0)


def add_corner_label(rgb: np.ndarray, text: str, font_pt: int = 20) -> np.ndarray:
    """Small caption in the top-left corner (used to label the 3D views)."""
    margin = max(6, rgb.shape[1] // 100)
    return _draw_text(
        rgb,
        text,
        anchor_x=margin + int(font_pt * len(text) * 0.3),
        baseline_y=margin + int(font_pt * 1.3),
        font_pt=font_pt,
        color=(1.0, 1.0, 1.0),
    )
