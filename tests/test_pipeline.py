"""Self-contained checks for the confocal pipeline.

Everything here runs on small synthetic stacks, so the suite needs no .nd2
files and finishes in a few seconds:

    python -m tests.test_pipeline        # or: pytest tests/test_pipeline.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from confocal import contrast, render  # noqa: E402
from confocal.config import (  # noqa: E402
    AnalysisROI,
    ReferenceROI,
    Settings,
    default_settings_for,
)
from confocal.imageio import ImageReadError, StackReader, probe  # noqa: E402
from confocal.pipeline import (  # noqa: E402
    calibrate_batch,
    process_image,
    run_batch,
    threshold_warnings,
)
from confocal.segmentation import segment_nuclei  # noqa: E402

# Ground truth for the synthetic stack: five well-separated nuclei, two of
# which overlap in XY and are only separable using the z information.
BLOB_CENTRES = [(6, 40, 40), (6, 60, 62), (4, 120, 50), (8, 50, 130), (5, 130, 130)]
OVERLAPPING_PAIR = [(5, 90, 90), (9, 90, 90)]  # same XY, different depth


def make_stack(path: str, centres=None, n_channels: int = 2, shape=(12, 180, 180)):
    """Write an ImageJ-calibrated synthetic z-stack of Gaussian nuclei."""
    import tifffile

    centres = BLOB_CENTRES if centres is None else centres
    n_z, height, width = shape
    rng = np.random.default_rng(1)
    volume = np.zeros((n_z, n_channels, height, width), np.uint16)
    zz, yy, xx = np.mgrid[0:n_z, 0:height, 0:width]
    nuclei = np.zeros((n_z, height, width), np.uint16)
    for cz, cy, cx in centres:
        d = ((zz - cz) * 2.0) ** 2 + ((yy - cy) * 0.5) ** 2 + ((xx - cx) * 0.5) ** 2
        nuclei = np.maximum(nuclei, (2500 * np.exp(-d / 40)).astype(np.uint16))
    volume[:, 0] = nuclei
    if n_channels > 1:
        volume[:, 1] = (rng.random((n_z, height, width)) * 180).astype(np.uint16)
        volume[:, 1] += nuclei // 3
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tifffile.imwrite(
        path,
        volume,
        imagej=True,
        resolution=(1 / 0.5, 1 / 0.5),
        metadata={"axes": "ZCYX", "spacing": 2.0, "unit": "um"},
    )
    return path


def counting_settings(input_dir: str, output_dir: str) -> Settings:
    settings = default_settings_for(2, ["Nuclei", "Stain"])
    settings.input_dir = input_dir
    settings.output_dir = output_dir
    for channel in settings.channels:
        channel.measure_area = channel.measure_volume = channel.measure_intensity = True
    settings.segmentation.nucleus_radius_um = 3.0
    settings.segmentation.min_volume_um3 = 5.0
    return settings


# --------------------------------------------------------------------- tests
def test_settings_roundtrip(tmp):
    settings = default_settings_for(4, ["DAPI", "Vim", "FN", "aSMA"])
    settings.input_dir = "in"
    settings.output_dir = "out"
    settings.max_workers = 3
    settings.channels[1].limit_mode = "roi_reference"
    settings.channels[1].display_transform = "log"
    settings.channels[2].display_transform = "gamma"
    settings.channels[2].gamma = 0.45
    settings.reference_roi = ReferenceROI(0.1, 0.2, 0.6, 0.8, "a.nd2", "per_image")
    settings.segmentation.seed_smoothing_factor = 0.3

    path = os.path.join(tmp, "s.json")
    settings.save(path)
    assert Settings.load(path).to_dict() == settings.to_dict()

    # A settings file from a future version must not lose the keys we know.
    data = json.load(open(path))
    data["future_option"] = "x"
    data["channels"][0]["mystery"] = 1
    assert Settings.from_dict(data).n_channels == 4


def test_validation_catches_mistakes():
    duplicate = default_settings_for(2)
    duplicate.channels[0].name = duplicate.channels[1].name = "A"
    assert any("unique" in p for p in duplicate.validate())

    inverted = default_settings_for(1)
    inverted.channels[0].limit_mode = "manual"
    inverted.channels[0].vmin, inverted.channels[0].vmax = 500, 100
    assert any("greater than" in p for p in inverted.validate())

    # Per-cell numbers are meaningless with no cell count.
    no_count = default_settings_for(1)
    no_count.segmentation.enabled = False
    no_count.channels[0].measure_area = True
    assert any("relative" in p for p in no_count.validate())

    assert default_settings_for(4).validate() == []


def test_reference_roi_bounds():
    assert ReferenceROI(0.25, 0.25, 0.75, 0.75).pixel_bounds(1360, 1360) == (
        340, 1020, 340, 1020,
    )
    # A zero-size rectangle must still yield a usable, non-empty patch.
    r0, r1, c0, c1 = ReferenceROI(0.5, 0.5, 0.5, 0.5).pixel_bounds(100, 100)
    assert r1 > r0 and c1 > c0


def test_display_transforms_are_bounded_and_monotonic():
    x = np.linspace(0, 4000, 1000).astype(np.float32)
    for transform, gamma in (("linear", 1.0), ("gamma", 0.45), ("log", 1.0)):
        y = contrast.apply_display_transform(x, 0, 3000, transform, gamma)
        assert y.min() >= 0.0 and y.max() <= 1.0
        assert np.all(np.diff(y) >= -1e-6)

    # Both compressive curves must lift dim signal relative to linear.
    dim = np.float32([300])
    linear = contrast.apply_display_transform(dim, 0, 3000, "linear")[0]
    assert contrast.apply_display_transform(dim, 0, 3000, "log")[0] > linear
    assert contrast.apply_display_transform(dim, 0, 3000, "gamma", 0.45)[0] > linear


def test_background_subtraction_is_per_slice():
    """Blurring along z would bleed one plane's signal into its neighbours."""
    volume = np.zeros((9, 120, 120), np.float32)
    volume[4] = 1000.0  # one bright plane, uniform in XY
    out = contrast.subtract_background(volume, radius_um=20.0, pixel_size_um=0.5)
    # A plane that was empty must stay empty.
    assert out[0].max() == 0.0
    assert out[8].max() == 0.0


def test_scale_bar_numbers():
    assert render.nice_scale_bar_length(973.4, 0.1) == 100
    assert render.format_length(2000) == "2 mm"
    assert render.format_length(100) == "100 µm"


def test_counts_planted_nuclei(tmp):
    path = make_stack(os.path.join(tmp, "in", "synthetic.tif"))
    info = probe(path)
    assert (info.n_channels, info.n_z) == (2, 12)
    assert abs(info.pixel_size_um - 0.5) < 1e-9
    assert abs(info.z_step_um - 2.0) < 1e-9

    with StackReader(path) as reader:
        volume = reader.channel_volume(0)
    settings = counting_settings(os.path.dirname(path), os.path.join(tmp, "out"))
    result = segment_nuclei(volume, settings.segmentation, 0.5, 2.0)
    assert result.n_cells == len(BLOB_CENTRES), result.n_cells


def test_separates_nuclei_overlapping_in_xy(tmp):
    """The point of counting in 3D: same XY, different z, must be two cells."""
    path = make_stack(
        os.path.join(tmp, "ov", "pair.tif"), centres=OVERLAPPING_PAIR, n_channels=1
    )
    with StackReader(path) as reader:
        volume = reader.channel_volume(0)

    # In the flat projection the pair is a single blob.
    from scipy import ndimage as ndi

    flat = volume.max(axis=0) > (volume.max() * 0.2)
    assert ndi.label(flat)[1] == 1

    settings = counting_settings(os.path.dirname(path), os.path.join(tmp, "out_ov"))
    result = segment_nuclei(volume, settings.segmentation, 0.5, 2.0)
    assert result.n_cells == 2, f"expected 2 nuclei, got {result.n_cells}"


def test_full_batch_and_outputs(tmp):
    in_dir = os.path.join(tmp, "batch_in")
    out_dir = os.path.join(tmp, "batch_out")
    make_stack(os.path.join(in_dir, "a.tif"))
    make_stack(os.path.join(in_dir, "b.tif"))

    settings = counting_settings(in_dir, out_dir)
    settings.output.save_isometric_3d = True
    settings.output.save_orthogonal_view = True
    settings.output.write_per_object_csv = True
    results = run_batch(settings, progress=lambda m: None)

    assert len(results) == 2 and all(r.ok for r in results)
    for name in ("statistics.csv", "settings_used.json", "processing_log.txt",
                 "cells_per_object.csv"):
        assert os.path.exists(os.path.join(out_dir, name)), name
    assert os.path.exists(os.path.join(out_dir, "3d_views", "a_isometric.png"))
    assert os.path.exists(os.path.join(out_dir, "3d_views", "a_orthogonal.png"))
    assert os.path.exists(os.path.join(out_dir, "composites", "Composite_a.png"))

    import pandas as pd

    frame = pd.read_csv(os.path.join(out_dir, "statistics.csv"))
    assert len(frame) == 2
    assert frame["n_cells"].tolist() == [len(BLOB_CENTRES)] * 2
    # Per-cell columns must equal total / count.
    row = frame.iloc[0]
    assert abs(
        row["Nuclei_relative_area_um2_per_cell"]
        - row["Nuclei_total_area_um2"] / row["n_cells"]
    ) < 1e-6
    assert abs(
        row["Nuclei_relative_volume_um3_per_cell"]
        - row["Nuclei_total_volume_um3"] / row["n_cells"]
    ) < 1e-6
    # The settings snapshot must reload.
    assert Settings.load(os.path.join(out_dir, "settings_used.json")).n_channels == 2


def test_bad_file_does_not_kill_the_batch(tmp):
    in_dir = os.path.join(tmp, "mixed_in")
    make_stack(os.path.join(in_dir, "good.tif"))
    with open(os.path.join(in_dir, "broken.tif"), "wb") as fh:
        fh.write(b"not a tiff at all")

    try:
        probe(os.path.join(in_dir, "missing.tif"))
        raise AssertionError("expected ImageReadError for a missing file")
    except ImageReadError:
        pass

    settings = counting_settings(in_dir, os.path.join(tmp, "mixed_out"))
    results = run_batch(settings, progress=lambda m: None)
    assert len(results) == 2
    assert sum(r.ok for r in results) == 1
    assert any("broken.tif" in r.name and r.error for r in results)


def test_measurements_ignore_the_display_settings(tmp):
    """The headline guarantee: saturation limits cannot move a measurement.

    Display scaling is applied after the numbers are taken, so a 600x change
    in the window -- or a log curve -- must leave every statistic identical.
    """
    import copy

    path = make_stack(os.path.join(tmp, "indep", "a.tif"))
    base = counting_settings(os.path.dirname(path), os.path.join(tmp, "indep_out"))
    base.segmentation.enabled = False
    base.segmentation.save_label_image = False
    base.output.save_composite = False

    def measured(mutate):
        settings = copy.deepcopy(base)
        mutate(settings)
        row = process_image(path, settings).row
        return {k: v for k, v in row.items() if "total_" in k or "fraction" in k}

    reference = measured(lambda s: None)
    assert reference, "nothing was measured"

    variants = [
        lambda s: [
            (setattr(c, "limit_mode", "manual"), setattr(c, "vmin", 0), setattr(c, "vmax", 10))
            for c in s.channels
        ],
        lambda s: [
            (setattr(c, "limit_mode", "manual"), setattr(c, "vmin", 0), setattr(c, "vmax", 60000))
            for c in s.channels
        ],
        lambda s: [setattr(c, "display_transform", "log") for c in s.channels],
        lambda s: [
            (setattr(c, "display_transform", "gamma"), setattr(c, "gamma", 0.2))
            for c in s.channels
        ],
    ]
    for mutate in variants:
        assert measured(mutate) == reference


def test_batch_scope_gives_one_threshold_for_every_image(tmp):
    """Comparability: an automatic threshold must not drift between images."""
    in_dir = os.path.join(tmp, "scope_in")
    make_stack(os.path.join(in_dir, "a.tif"))
    # A dimmer second image: per-image Otsu would pick a different cut-off.
    import tifffile

    bright = tifffile.imread(os.path.join(in_dir, "a.tif"))
    tifffile.imwrite(
        os.path.join(in_dir, "b.tif"),
        (bright // 3).astype(bright.dtype),
        imagej=True,
        resolution=(1 / 0.5, 1 / 0.5),
        metadata={"axes": "ZCYX", "spacing": 2.0, "unit": "um"},
    )

    settings = counting_settings(in_dir, os.path.join(tmp, "scope_out"))
    settings.batch_sample_images = 0  # use every image

    for channel in settings.channels:
        channel.threshold_scope = "per_image"
    per_image = run_batch(settings, progress=lambda m: None)
    per_image_thresholds = [r.row["Nuclei_threshold"] for r in per_image]
    assert per_image_thresholds[0] != per_image_thresholds[1]

    for channel in settings.channels:
        channel.threshold_scope = "batch"
    settings.output_dir = os.path.join(tmp, "scope_out_batch")
    batched = run_batch(settings, progress=lambda m: None)
    batch_thresholds = [r.row["Nuclei_threshold"] for r in batched]
    assert batch_thresholds[0] == batch_thresholds[1], batch_thresholds


def test_batch_display_limits_are_shared(tmp):
    in_dir = os.path.join(tmp, "lim_in")
    make_stack(os.path.join(in_dir, "a.tif"))
    make_stack(os.path.join(in_dir, "b.tif"))
    settings = counting_settings(in_dir, os.path.join(tmp, "lim_out"))
    settings.segmentation.enabled = False
    for channel in settings.channels:
        channel.limit_mode = "auto_percentile_batch"
        channel.measure_area = channel.measure_volume = channel.measure_intensity = False

    calibration = calibrate_batch(settings)
    assert calibration.limits, "expected pooled display limits"

    results = run_batch(settings, progress=lambda m: None)
    lows = {r.row["Nuclei_display_min"] for r in results}
    highs = {r.row["Nuclei_display_max"] for r in results}
    assert len(lows) == 1 and len(highs) == 1


def test_plots_build_from_a_csv(tmp):
    import matplotlib

    matplotlib.use("Agg")
    from confocal import plots

    in_dir = os.path.join(tmp, "plot_in")
    out_dir = os.path.join(tmp, "plot_out")
    make_stack(os.path.join(in_dir, "dynamic_chip1.tif"))
    make_stack(os.path.join(in_dir, "static_chip2.tif"))
    settings = counting_settings(in_dir, out_dir)
    run_batch(settings, progress=lambda m: None)

    frame = plots.load_table(os.path.join(out_dir, "statistics.csv"))
    metrics = plots.numeric_columns(frame)
    assert "n_cells" in metrics

    groups = plots.derive_groups(frame, plots.DEFAULT_GROUP_PATTERN)
    assert groups is not None and set(groups) == {"dynamic", "static"}
    assert plots.derive_groups(frame, "") is None

    for plot_type, second in (
        ("bar per image", ""),
        ("compare groups", ""),
        ("scatter", metrics[1] if len(metrics) > 1 else "n_cells"),
        ("distribution", ""),
    ):
        figure, _order = plots.build_figure(
            frame, plot_type, "n_cells", second, plots.DEFAULT_GROUP_PATTERN
        )
        assert figure.get_axes()
        figure.savefig(os.path.join(tmp, f"{plot_type.replace(' ', '_')}.png"))

    # A bad column or pattern is reported, not raised as a crash.
    for bad in (
        lambda: plots.build_figure(frame, "bar per image", "no_such_column"),
        lambda: plots.build_figure(frame, "compare groups", "n_cells", "", ""),
        lambda: plots.derive_groups(frame, "(unclosed"),
    ):
        try:
            bad()
            raise AssertionError("expected PlotDataError")
        except plots.PlotDataError:
            pass


def test_group_folding_never_invents_colours():
    import pandas as pd

    from confocal import plots

    groups = pd.Series(["a"] * 5 + ["b"] * 4 + ["c"] * 3 + ["d"] * 2 + ["e"])
    folded, names = plots.fold_to_cap(groups, 3)
    assert len(names) == 4 and names[-1] == "other"
    assert set(folded) == {"a", "b", "c", "other"}
    assert len(set(names)) <= len(plots.SERIES_COLORS) + 1


def test_credit_is_recorded(tmp):
    from confocal.config import AUTHORS, CREDIT

    assert "Klaudia Saladauskas" in AUTHORS and "Leonardo Cecchin" in AUTHORS

    in_dir = os.path.join(tmp, "credit_in")
    out_dir = os.path.join(tmp, "credit_out")
    make_stack(os.path.join(in_dir, "a.tif"))
    settings = counting_settings(in_dir, out_dir)
    settings.segmentation.enabled = False
    for channel in settings.channels:
        channel.measure_area = channel.measure_volume = channel.measure_intensity = False
    run_batch(settings, progress=lambda m: None)

    log = open(os.path.join(out_dir, "processing_log.txt"), encoding="utf-8").read()
    assert AUTHORS in log
    saved = json.load(open(os.path.join(out_dir, "settings_used.json"), encoding="utf-8"))
    assert saved["created_by"] == CREDIT


def test_annotated_counting_image_is_exported(tmp):
    """Boundaries plus a number per cell, in the requested formats."""
    import numpy as np
    from PIL import Image

    in_dir = os.path.join(tmp, "annot_in")
    make_stack(os.path.join(in_dir, "a.tif"))

    def run(annotate, formats, out_name):
        settings = counting_settings(in_dir, os.path.join(tmp, out_name))
        settings.segmentation.annotate_cell_ids = annotate
        settings.segmentation.overlay_formats = formats
        settings.segmentation.save_overlay = True
        results = run_batch(settings, progress=lambda m: None)
        assert all(r.ok for r in results)
        return os.path.join(tmp, out_name, "segmentation")

    plain_dir = run(False, ["png"], "annot_plain")
    plain = os.path.join(plain_dir, "a_nuclei_overlay.png")
    assert os.path.exists(plain)

    numbered_dir = run(True, ["png", "tif"], "annot_numbered")
    numbered = os.path.join(numbered_dir, "a_nuclei_overlay.png")
    assert os.path.exists(numbered)
    # Both requested formats land on disk.
    assert os.path.exists(os.path.join(numbered_dir, "a_nuclei_overlay.tif"))

    a = np.asarray(Image.open(plain).convert("RGB"))
    b = np.asarray(Image.open(numbered).convert("RGB"))
    assert a.shape == b.shape
    # The numbers must actually add ink somewhere.
    assert (a != b).any(), "numbering changed nothing in the exported image"

    # The outlines survive the numbering: yellow boundary pixels are still there.
    def yellow_pixels(img):
        return int(((img[..., 0] > 180) & (img[..., 1] > 180) & (img[..., 2] < 90)).sum())

    assert yellow_pixels(b) > 0
    assert yellow_pixels(b) >= 0.5 * yellow_pixels(a), "numbers buried the outlines"


def test_cell_ids_match_the_per_object_csv(tmp):
    """A number in the picture must find its row in the CSV."""
    import pandas as pd

    in_dir = os.path.join(tmp, "ids_in")
    out_dir = os.path.join(tmp, "ids_out")
    make_stack(os.path.join(in_dir, "a.tif"))
    settings = counting_settings(in_dir, out_dir)
    settings.segmentation.annotate_cell_ids = True
    settings.output.write_per_object_csv = True
    results = run_batch(settings, progress=lambda m: None)

    per_object = pd.read_csv(os.path.join(out_dir, "cells_per_object.csv"))
    ids = sorted(per_object["cell_id"].tolist())
    assert ids == list(range(1, results[0].n_cells + 1))


def test_id_labels_survive_out_of_frame_positions():
    """Labels pushed off the edge by the offset are clamped, not dropped."""
    import numpy as np

    from confocal import render

    canvas = np.zeros((60, 60, 3), dtype=np.float32)
    out = render.draw_id_labels(
        canvas,
        [(-40.0, 5.0), (30.0, 30.0), (200.0, 200.0)],
        ["1", "2", "3"],
        font_pt=10,
    )
    assert out.shape == canvas.shape
    assert out.max() > 0  # something was drawn


def test_analysis_region_restricts_measurements_not_exports(tmp):
    """Measure inside the region; export the whole field regardless."""
    import numpy as np
    from PIL import Image

    # Nuclei in the left half only, so a right-hand region must find none.
    left = [(6, 40, 30), (6, 90, 40), (4, 130, 25)]
    in_dir = os.path.join(tmp, "roi_in")
    make_stack(os.path.join(in_dir, "a.tif"), centres=left)

    def run(roi, tag):
        settings = counting_settings(in_dir, os.path.join(tmp, tag))
        settings.analysis_roi = roi
        settings.segmentation.save_label_image = False
        settings.channels[0].threshold_scope = "per_image"
        settings.channels[1].threshold_scope = "per_image"
        results = run_batch(settings, progress=lambda m: None)
        assert all(r.ok for r in results)
        return results[0], os.path.join(tmp, tag)

    whole, whole_dir = run(AnalysisROI(enabled=False), "roi_whole")
    # Left half: should keep every nucleus.
    left_res, _ = run(AnalysisROI(enabled=True, x0=0.0, y0=0.0, x1=0.5, y1=1.0), "roi_left")
    # Right half: the nuclei are not there.
    right_res, right_dir = run(
        AnalysisROI(enabled=True, x0=0.6, y0=0.0, x1=1.0, y1=1.0), "roi_right"
    )

    assert whole.n_cells == len(left)
    assert left_res.n_cells == len(left)
    assert right_res.n_cells == 0, right_res.n_cells
    assert right_res.row["DAPI_total_area_um2" if "DAPI_total_area_um2" in right_res.row
                          else "Nuclei_total_area_um2"] == 0

    # The region is recorded, and its area is smaller than the full field.
    assert whole.row["analysis_region"] == "whole image"
    assert right_res.row["analysis_region"] != "whole image"
    assert right_res.row["analysis_area_um2"] < whole.row["analysis_area_um2"]

    # Exports stay full-field in every case.
    def size(directory):
        return Image.open(
            os.path.join(directory, "composites", "Composite_a.png")
        ).size

    assert size(whole_dir) == size(right_dir)
    overlay = np.asarray(
        Image.open(os.path.join(right_dir, "segmentation", "a_nuclei_overlay.png"))
    )
    assert overlay.shape[:2] == (180, 180)


def test_analysis_region_outline_is_drawn(tmp):
    import numpy as np

    from confocal import render

    canvas = np.zeros((50, 60, 3), dtype=np.float32)
    out = render.draw_region_outline(canvas, (10, 40, 15, 50))
    assert out[10, 15].any() and out[39, 49].any()  # corners marked
    assert not out[25, 30].any()  # interior untouched
    assert not out[5, 5].any()  # outside untouched


def test_boxplot_and_manual_grouping(tmp):
    import matplotlib

    matplotlib.use("Agg")
    import pandas as pd

    from confocal import plots

    frame = pd.DataFrame(
        {
            "image": [f"exp_{c}_chip{i}" for c in ("dynamic", "static") for i in range(1, 5)],
            "n_cells": [900, 950, 1000, 880, 600, 650, 700, 620],
        }
    )

    groups = plots.derive_groups(frame, r"(dynamic|static)")
    assert set(groups) == {"dynamic", "static"}
    figure, order = plots.build_figure(frame, "boxplot", "n_cells", "", r"(dynamic|static)")
    assert figure.get_axes() and len(order) == len(frame)

    # A hand assignment beats the pattern for the images it names.
    manual = {"exp_dynamic_chip1": "excluded", "exp_static_chip1": "excluded"}
    mixed = plots.derive_groups(frame, r"(dynamic|static)", manual)
    assert list(mixed).count("excluded") == 2
    assert list(mixed).count("dynamic") == 3

    # Manual alone, with no pattern at all, still groups.
    only = plots.derive_groups(frame, "", {n: "g1" for n in frame.image[:4]})
    assert only is not None and set(only) == {"g1", "other"}

    # Nothing to group by means one ungrouped series.
    assert plots.derive_groups(frame, "", {}) is None

    figure, _ = plots.build_figure(frame, "boxplot", "n_cells", "", "", manual)
    assert figure.get_axes()

    # A boxplot with no grouping at all is refused with an explanation.
    try:
        plots.build_figure(frame, "boxplot", "n_cells", "", "")
        raise AssertionError("expected PlotDataError")
    except plots.PlotDataError as exc:
        assert "group" in str(exc).lower()


def test_manual_groups_survive_a_settings_roundtrip(tmp):
    settings = default_settings_for(2)
    settings.image_groups = {"a.nd2": "ctrl", "b.nd2": "treated"}
    settings.analysis_roi = AnalysisROI(enabled=True, x0=0.2, y0=0.1, x1=0.8, y1=0.9)
    path = os.path.join(tmp, "groups.json")
    settings.save(path)
    back = Settings.load(path)
    assert back.image_groups == settings.image_groups
    assert back.analysis_roi == settings.analysis_roi


def test_number_formatting_has_no_stray_decimals():
    from confocal.plots import _format_value

    assert _format_value(900) == "900"
    assert _format_value(1200) == "1,200"
    assert _format_value(882.5) == "882.5"
    assert _format_value(0) == "0"


def test_threshold_warning_catches_a_useless_threshold():
    """A threshold that calls almost everything signal must be flagged."""
    from confocal.pipeline import ImageResult

    settings = default_settings_for(1, ["Dim"])
    settings.channels[0].measure_area = True

    def results_with(fraction):
        return [
            ImageResult(
                name=f"{i}.tif",
                path="",
                row={
                    "Dim_area_fraction_fov": fraction,
                    "Dim_threshold": 1.3,
                },
            )
            for i in range(3)
        ]

    too_permissive = threshold_warnings(settings, results_with(0.86))
    assert too_permissive and "Dim" in too_permissive[0]
    assert "threshold" in too_permissive[0].lower()

    too_strict = threshold_warnings(settings, results_with(0.0))
    assert too_strict and "near zero" in " ".join(too_strict)

    # A sensible fraction says nothing at all.
    assert threshold_warnings(settings, results_with(0.25)) == []

    # A channel that measures nothing is never flagged.
    silent = default_settings_for(1, ["Dim"])
    silent.channels[0].measure_area = False
    silent.channels[0].measure_volume = False
    silent.channels[0].measure_intensity = False
    assert threshold_warnings(silent, results_with(0.99)) == []


def test_worker_initialiser_is_harmless():
    """It must never raise, whatever the platform."""
    from confocal.pipeline import _worker_init

    _worker_init()


def test_stop_during_calibration_returns_promptly(tmp):
    """Stop must be heard during the calibration pass, not only after it."""
    import threading
    import time

    in_dir = os.path.join(tmp, "stopcal_in")
    for i in range(6):
        make_stack(os.path.join(in_dir, f"s{i}.tif"))

    settings = counting_settings(in_dir, os.path.join(tmp, "stopcal_out"))
    settings.batch_sample_images = 0  # calibrate on every image
    for channel in settings.channels:
        channel.limit_mode = "auto_percentile_batch"

    stop = threading.Event()
    seen = []

    def progress(message):
        seen.append(message)
        # Ask to stop as soon as calibration has started.
        if "calibrating on" in message:
            stop.set()

    started = time.time()
    results = run_batch(settings, progress=progress, should_stop=stop.is_set)
    elapsed = time.time() - started

    assert any("calibration stopped" in m for m in seen), seen[-5:]
    # Nothing should have been processed after the stop.
    assert results == []
    assert elapsed < 60, f"stop took {elapsed:.0f}s"
    # The run still records itself rather than vanishing.
    assert os.path.exists(os.path.join(tmp, "stopcal_out", "processing_log.txt"))


def test_stop_during_processing_leaves_no_workers(tmp):
    """Stop ends the run, keeps finished results, and cleans up the pool."""
    import threading

    in_dir = os.path.join(tmp, "stopproc_in")
    for i in range(6):
        make_stack(os.path.join(in_dir, f"p{i}.tif"), shape=(16, 300, 300))

    settings = counting_settings(in_dir, os.path.join(tmp, "stopproc_out"))
    settings.max_workers = 2
    for channel in settings.channels:
        channel.threshold_scope = "per_image"

    stop = threading.Event()

    def on_result(_result):
        stop.set()  # stop as soon as the first image lands

    results = run_batch(
        settings, progress=lambda m: None, on_result=on_result, should_stop=stop.is_set
    )

    # At least the first image is kept, and fewer than all six ran.
    assert 1 <= len(results) < 6, len(results)
    assert all(r.ok for r in results)
    # Whatever finished is written out.
    import pandas as pd

    frame = pd.read_csv(os.path.join(tmp, "stopproc_out", "statistics.csv"))
    assert len(frame) == len(results)


def test_worker_count_is_memory_bounded():
    from confocal.pipeline import resolve_worker_count

    # An explicit request wins, but never exceeds the number of images.
    assert resolve_worker_count(4, 2, 1 << 20) == 2
    assert resolve_worker_count(3, 10, 1 << 20) == 3
    # A stack far larger than RAM must still leave one usable worker.
    assert resolve_worker_count(0, 10, 1 << 40) == 1
    assert resolve_worker_count(0, 10, 1 << 20) >= 1


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="confocal-tests-")
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failures = 0
    try:
        for test in tests:
            name = test.__name__
            try:
                test(tmp) if test.__code__.co_argcount else test()
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
                import traceback

                traceback.print_exc()
            else:
                print(f"PASS  {name}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


# pytest needs a real fixture for the `tmp` argument.
try:
    import pytest

    @pytest.fixture(name="tmp")
    def _tmp_fixture(tmp_path):
        return str(tmp_path)

except ImportError:  # pragma: no cover
    pass


if __name__ == "__main__":
    sys.exit(main())
