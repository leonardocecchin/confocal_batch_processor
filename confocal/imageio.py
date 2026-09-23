"""Reading confocal stacks and writing results.

The reader deliberately hands back one channel at a time: a single .nd2 here is
~840 MB as uint16, and the pipeline only ever needs one channel volume plus its
projection in memory at once.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np

ND2_EXTENSIONS = (".nd2",)
TIFF_EXTENSIONS = (".tif", ".tiff", ".ome.tif", ".ome.tiff")
SUPPORTED_EXTENSIONS = ND2_EXTENSIONS + TIFF_EXTENSIONS


class ImageReadError(RuntimeError):
    """Raised when a file cannot be opened or does not look like a z-stack."""


@dataclass
class StackInfo:
    """Shape and calibration of one acquisition."""

    path: str
    n_channels: int
    n_z: int
    height: int
    width: int
    dtype: str
    pixel_size_um: float
    z_step_um: float
    channel_names: List[str]

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def stem(self) -> str:
        base = os.path.basename(self.path)
        for ext in sorted(SUPPORTED_EXTENSIONS, key=len, reverse=True):
            if base.lower().endswith(ext):
                return base[: -len(ext)]
        return os.path.splitext(base)[0]

    @property
    def voxel_volume_um3(self) -> float:
        return self.pixel_size_um * self.pixel_size_um * self.z_step_um

    @property
    def pixel_area_um2(self) -> float:
        return self.pixel_size_um * self.pixel_size_um

    def summary(self) -> str:
        return (
            f"{self.name}: {self.n_channels}C x {self.n_z}Z x "
            f"{self.height}x{self.width} px, {self.dtype}, "
            f"{self.pixel_size_um:.4f} um/px, {self.z_step_um:.3f} um/z-step"
        )


def list_images(directory: str) -> List[str]:
    """Absolute paths of every supported image in `directory`, sorted by name."""
    if not directory or not os.path.isdir(directory):
        return []
    found = []
    for entry in sorted(os.listdir(directory)):
        if entry.startswith("."):
            continue
        path = os.path.join(directory, entry)
        if os.path.isfile(path) and entry.lower().endswith(SUPPORTED_EXTENSIONS):
            found.append(path)
    return found


class StackReader:
    """Context manager giving per-channel access to a z-stack.

    Usage::

        with StackReader(path) as reader:
            vol = reader.channel_volume(0)   # (Z, Y, X) array
    """

    def __init__(
        self,
        path: str,
        pixel_size_um: Optional[float] = None,
        z_step_um: Optional[float] = None,
    ):
        self.path = path
        self._pixel_override = pixel_size_um
        self._z_override = z_step_um
        self._handle = None
        self._kind = ""
        self._tiff_axes = ""
        self.info: Optional[StackInfo] = None
        self._open()

    # ------------------------------------------------------------------ open
    def _open(self) -> None:
        lower = self.path.lower()
        if not os.path.isfile(self.path):
            raise ImageReadError(f"no such file: {self.path}")
        if lower.endswith(ND2_EXTENSIONS):
            self._open_nd2()
        elif lower.endswith(TIFF_EXTENSIONS):
            self._open_tiff()
        else:
            raise ImageReadError(f"unsupported file type: {os.path.basename(self.path)}")

    def _open_nd2(self) -> None:
        try:
            import nd2
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImageReadError(
                "reading .nd2 files needs the 'nd2' package (pip install nd2)"
            ) from exc

        try:
            handle = nd2.ND2File(self.path)
        except Exception as exc:
            raise ImageReadError(f"could not open {os.path.basename(self.path)}: {exc}") from exc

        self._handle = handle
        self._kind = "nd2"
        sizes = dict(handle.sizes)

        for axis in ("T", "P", "S"):
            if sizes.get(axis, 1) > 1:
                handle.close()
                raise ImageReadError(
                    f"{os.path.basename(self.path)} has {sizes[axis]} {axis} positions; "
                    "this tool expects a single position, single time point z-stack"
                )

        voxel = handle.voxel_size()
        names = []
        try:
            for meta in handle.metadata.channels:
                names.append(str(meta.channel.name))
        except Exception:
            names = []
        n_channels = int(sizes.get("C", 1))
        if len(names) != n_channels:
            names = [f"Channel{i + 1}" for i in range(n_channels)]

        self.info = StackInfo(
            path=self.path,
            n_channels=n_channels,
            n_z=int(sizes.get("Z", 1)),
            height=int(sizes["Y"]),
            width=int(sizes["X"]),
            dtype=str(handle.dtype),
            pixel_size_um=float(self._pixel_override or voxel.x or 1.0),
            z_step_um=float(self._z_override or voxel.z or 1.0),
            channel_names=names,
        )

    def _open_tiff(self) -> None:
        try:
            import tifffile
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ImageReadError(
                "reading TIFF files needs the 'tifffile' package (pip install tifffile)"
            ) from exc

        try:
            handle = tifffile.TiffFile(self.path)
        except Exception as exc:
            raise ImageReadError(f"could not open {os.path.basename(self.path)}: {exc}") from exc

        self._handle = handle
        self._kind = "tiff"
        series = handle.series[0]
        axes = series.axes
        self._tiff_axes = axes
        shape = dict(zip(axes, series.shape))
        if "Y" not in shape or "X" not in shape:
            handle.close()
            raise ImageReadError(f"{os.path.basename(self.path)}: cannot find Y/X axes ({axes})")

        pixel_size = self._pixel_override or _tiff_pixel_size(handle) or 1.0
        z_step = self._z_override or _tiff_z_step(handle) or 1.0
        n_channels = int(shape.get("C", 1))

        self.info = StackInfo(
            path=self.path,
            n_channels=n_channels,
            n_z=int(shape.get("Z", shape.get("I", 1))),
            height=int(shape["Y"]),
            width=int(shape["X"]),
            dtype=str(series.dtype),
            pixel_size_um=float(pixel_size),
            z_step_um=float(z_step),
            channel_names=[f"Channel{i + 1}" for i in range(n_channels)],
        )

    # ----------------------------------------------------------------- access
    def channel_volume(self, channel: int) -> np.ndarray:
        """Return channel `channel` as a (Z, Y, X) array."""
        if self.info is None:
            raise ImageReadError("reader is not open")
        if not 0 <= channel < self.info.n_channels:
            raise ImageReadError(
                f"channel {channel} out of range (file has {self.info.n_channels})"
            )
        if self._kind == "nd2":
            volume = self._nd2_channel(channel)
        else:
            volume = self._tiff_channel(channel)

        volume = np.asarray(volume)
        if volume.ndim == 2:
            volume = volume[np.newaxis, ...]
        if volume.ndim != 3:
            raise ImageReadError(
                f"unexpected volume shape {volume.shape} for channel {channel}"
            )
        return volume

    def _nd2_channel(self, channel: int) -> np.ndarray:
        """Read one channel z-slice by z-slice.

        `asarray()` would pull all four channels (~840 MB) into memory just to
        throw three away; reading frame by frame keeps the resident cost to the
        single channel being returned.
        """
        handle = self._handle
        assert self.info is not None
        n_z = self.info.n_z
        has_c = handle.sizes.get("C", 1) > 1

        out = np.empty((n_z, self.info.height, self.info.width), dtype=handle.dtype)
        for z in range(n_z):
            frame = handle.read_frame(z)
            out[z] = frame[channel] if has_c else frame
        return out

    def _tiff_channel(self, channel: int) -> np.ndarray:
        series = self._handle.series[0]
        data = series.asarray()
        axes = self._tiff_axes
        if "C" in axes:
            data = np.take(data, channel, axis=axes.index("C"))
            axes = axes.replace("C", "")
        # Move the remaining leading axis into Z position.
        if data.ndim == 2:
            data = data[np.newaxis, ...]
        return data

    # ------------------------------------------------------------------ close
    def close(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except Exception:
                pass
            self._handle = None

    def __enter__(self) -> "StackReader":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _tiff_pixel_size(handle) -> Optional[float]:
    """Pixel size in microns from OME metadata or the TIFF resolution tags."""
    try:
        if handle.ome_metadata:
            import re

            match = re.search(r'PhysicalSizeX="([0-9.eE+-]+)"', handle.ome_metadata)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    try:
        tags = handle.pages[0].tags
        res = tags["XResolution"].value
        unit = tags.get("ResolutionUnit")
        value = res[1] / res[0] if isinstance(res, tuple) else 1.0 / float(res)
        # ImageJ TIFFs usually store microns directly in the imagej_metadata unit.
        meta = handle.imagej_metadata or {}
        if str(meta.get("unit", "")).lower() in ("micron", "um", "µm", "microns"):
            return float(value)
        if unit is not None and getattr(unit, "value", None) == 3:  # centimetre
            return float(value) * 10000.0
        return float(value)
    except Exception:
        return None


def _tiff_z_step(handle) -> Optional[float]:
    try:
        meta = handle.imagej_metadata or {}
        if "spacing" in meta:
            return abs(float(meta["spacing"]))
        if handle.ome_metadata:
            import re

            match = re.search(r'PhysicalSizeZ="([0-9.eE+-]+)"', handle.ome_metadata)
            if match:
                return float(match.group(1))
    except Exception:
        pass
    return None


def probe(path: str) -> StackInfo:
    """Open a file just long enough to read its shape and calibration."""
    with StackReader(path) as reader:
        assert reader.info is not None
        return reader.info


def save_rgb(
    array: np.ndarray,
    path_without_ext: str,
    formats: Sequence[str],
    pixel_size_um: Optional[float] = None,
) -> List[str]:
    """Write an (H, W, 3) uint8 image in each requested format.

    TIFFs carry the pixel calibration so they open in Fiji already scaled.
    """
    from PIL import Image
    import tifffile

    array = np.ascontiguousarray(array)
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)

    written: List[str] = []
    os.makedirs(os.path.dirname(os.path.abspath(path_without_ext)), exist_ok=True)
    for fmt in formats:
        fmt = fmt.lower().lstrip(".")
        path = f"{path_without_ext}.{fmt}"
        if fmt in ("tif", "tiff"):
            resolution = None
            metadata: Dict[str, object] = {"axes": "YXS"}
            if pixel_size_um:
                resolution = (1.0 / pixel_size_um, 1.0 / pixel_size_um)
                metadata["unit"] = "um"
            tifffile.imwrite(
                path,
                array,
                photometric="rgb",
                resolution=resolution,
                imagej=True,
                metadata=metadata,
            )
        else:
            image = Image.fromarray(array)
            if fmt in ("jpg", "jpeg"):
                image.save(path, quality=92, subsampling=0)
            else:
                image.save(path)
        written.append(path)
    return written


def save_label_volume(
    labels: np.ndarray,
    path: str,
    pixel_size_um: float,
    z_step_um: float,
) -> str:
    """Write a 3D label image as an ImageJ-compatible TIFF."""
    import tifffile

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    dtype = np.uint16 if labels.max() < 65535 else np.uint32
    tifffile.imwrite(
        path,
        labels.astype(dtype, copy=False),
        imagej=True,
        resolution=(1.0 / pixel_size_um, 1.0 / pixel_size_um),
        metadata={"axes": "ZYX", "spacing": z_step_um, "unit": "um"},
    )
    return path
