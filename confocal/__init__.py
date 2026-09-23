"""Confocal z-stack batch processing: composites, 3D cell counting, statistics."""

from .config import (
    COLOR_LUTS,
    ChannelSettings,
    OutputSettings,
    ReferenceROI,
    SegmentationSettings,
    Settings,
    default_settings_for,
)
from .imageio import StackInfo, StackReader, list_images, probe
from .pipeline import ImageResult, process_image, run_batch

__version__ = "1.0.0"

__all__ = [
    "COLOR_LUTS",
    "ChannelSettings",
    "OutputSettings",
    "ReferenceROI",
    "SegmentationSettings",
    "Settings",
    "default_settings_for",
    "StackInfo",
    "StackReader",
    "list_images",
    "probe",
    "ImageResult",
    "process_image",
    "run_batch",
    "__version__",
]
