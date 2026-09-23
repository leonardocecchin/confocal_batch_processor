"""Plots of the statistics CSV, from this run or any earlier one.

Figures are built with matplotlib so they can be shown inside the GUI and saved
straight to a file. The colours are the validated colour-blind-safe categorical
slots; because one of them sits below 3:1 against a white surface, every grouped
figure also carries direct value labels and the GUI offers a table view.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

# Validated categorical slots, in fixed order. Never cycled, never generated:
# past the cap the tail folds into "Other".
SERIES_COLORS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
# Forms where every pair of colours can end up adjacent (scatter) validate only
# for the first three slots.
ALL_PAIRS_CAP = 3
ADJACENT_CAP = 6

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#7a7973"
GRID = "#dcdcd6"
NEUTRAL = "#9a9992"

# Columns that identify an image rather than measure it.
IDENTITY_COLUMNS = {
    "image", "file", "error", "height_px", "width_px", "n_z",
    "pixel_size_um", "z_step_um",
}

DEFAULT_GROUP_PATTERN = r"(dynamic|static)"

PLOT_TYPES = (
    "boxplot",
    "compare groups",
    "bar per image",
    "scatter",
    "distribution",
)


class PlotDataError(ValueError):
    """Raised when the CSV cannot support the requested plot."""


# ------------------------------------------------------------------ loading
def load_table(path: str):
    """Read a statistics CSV written by this tool (or any earlier session)."""
    import pandas as pd

    if not path or not os.path.isfile(path):
        raise PlotDataError(f"no such file: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:  # noqa: BLE001
        raise PlotDataError(f"could not read {os.path.basename(path)}: {exc}") from exc
    if frame.empty:
        raise PlotDataError(f"{os.path.basename(path)} has no rows")
    return frame


def label_column(frame) -> str:
    """The column that names each row."""
    for candidate in ("image", "file", "cell_id"):
        if candidate in frame.columns:
            return candidate
    return frame.columns[0]


def numeric_columns(frame) -> List[str]:
    """Measurement columns worth plotting, most interesting first."""
    import pandas as pd

    columns = [
        c
        for c in frame.columns
        if c not in IDENTITY_COLUMNS and pd.api.types.is_numeric_dtype(frame[c])
    ]

    def rank(name: str) -> tuple:
        # Surface the per-cell and total measurements above the diagnostics.
        if name == "n_cells":
            return (0, name)
        if "relative" in name:
            return (1, name)
        if "total" in name:
            return (2, name)
        if "fraction" in name or "mean" in name:
            return (3, name)
        return (4, name)

    return sorted(columns, key=rank)


def derive_groups(
    frame, pattern: str, manual: Optional[Dict[str, str]] = None
) -> Optional["object"]:
    """Group label per row: a manual assignment if there is one, else the regex.

    `manual` maps an image name to a group and always wins, so a handful of
    hand-placed images can override an otherwise automatic split. Returns None
    when nothing groups the rows, which the plots treat as one series.
    """
    import pandas as pd

    manual = {str(k): str(v) for k, v in (manual or {}).items() if str(v).strip()}
    names = frame[label_column(frame)].astype(str)

    regex = None
    if pattern and pattern.strip():
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error as exc:
            raise PlotDataError(
                f"group pattern is not a valid regular expression: {exc}"
            ) from exc

    if regex is None and not manual:
        return None

    found = []
    for name in names:
        if name in manual:
            found.append(manual[name].strip().lower())
            continue
        match = regex.search(name) if regex is not None else None
        if not match:
            found.append("other")
        else:
            found.append((match.group(1) if match.groups() else match.group(0)).lower())
    series = pd.Series(found, index=frame.index)
    if set(series) == {"other"}:
        return None
    return series


def fold_to_cap(groups, cap: int):
    """Keep the `cap` largest groups and fold the rest into 'other'.

    Generating more colours is never the answer to more groups.
    """
    counts = groups.value_counts()
    if len(counts) <= cap:
        return groups, list(counts.index)
    keep = list(counts.index[:cap])
    folded = groups.where(groups.isin(keep), "other")
    return folded, keep + ["other"]


def pretty(name: str) -> str:
    """Readable axis label from a column name."""
    text = name.replace("_um2", " (µm²)").replace("_um3", " (µm³)").replace("_um", " (µm)")
    text = text.replace("_per_cell", " per cell").replace("_", " ")
    return text[:1].upper() + text[1:]


# ------------------------------------------------------------------ styling
def _new_figure(width=9.0, height=5.4):
    from matplotlib.figure import Figure

    figure = Figure(figsize=(width, height), dpi=100, facecolor=SURFACE)
    axis = figure.add_subplot(111)
    axis.set_facecolor(SURFACE)
    return figure, axis


def _style(axis, value_axis: str = "y"):
    """Recessive grid and axes: the data should be the only strong thing."""
    from matplotlib.ticker import FuncFormatter

    for side in ("top", "right"):
        axis.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axis.spines[side].set_color(GRID)
    axis.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=3, width=0.8)
    axis.grid(axis=value_axis, color=GRID, linewidth=0.8, alpha=0.9)
    axis.set_axisbelow(True)

    # Compact tick labels instead of a shared "1e8" offset, which otherwise
    # floats above the axes and collides with the subtitle.
    formatter = FuncFormatter(lambda v, _pos: _format_value(v))
    for name in (("x", "y") if value_axis == "both" else (value_axis,)):
        target = axis.xaxis if name == "x" else axis.yaxis
        target.set_major_formatter(formatter)
        target.get_offset_text().set_visible(False)
    axis.xaxis.label.set_color(TEXT_SECONDARY)
    axis.yaxis.label.set_color(TEXT_SECONDARY)
    axis.title.set_color(TEXT_PRIMARY)


def _title(axis, text: str, subtitle: str = ""):
    # The subtitle sits in the gap the title pad opens up, so the two never
    # collide however long the title is.
    axis.set_title(text, fontsize=13, loc="left", pad=26 if subtitle else 8)
    if subtitle:
        axis.annotate(
            subtitle,
            xy=(0, 1),
            xycoords="axes fraction",
            xytext=(0, 7),
            textcoords="offset points",
            fontsize=9,
            color=TEXT_MUTED,
            va="bottom",
        )


def _color_for(index: int, name: str = "") -> str:
    if name == "other":
        return NEUTRAL
    return SERIES_COLORS[index % len(SERIES_COLORS)]


# -------------------------------------------------------------------- plots
def plot_bar_per_image(frame, metric: str, groups=None) -> Tuple["object", List[str]]:
    """One horizontal bar per image, sorted by value.

    Horizontal because the image names are long; sorted because the reader's
    job here is to compare magnitudes and spot the outliers.
    """
    if metric not in frame.columns:
        raise PlotDataError(f"{metric!r} is not a column in this CSV")

    labels_col = label_column(frame)
    data = frame[[labels_col, metric]].copy()
    if groups is not None:
        data["_group"] = groups
    data = data.dropna(subset=[metric]).sort_values(metric)
    if data.empty:
        raise PlotDataError(f"no numeric values in {metric!r}")

    height = max(3.2, 0.32 * len(data) + 1.8)
    figure, axis = _new_figure(9.6, height)
    positions = np.arange(len(data))

    if groups is None:
        colors = [SERIES_COLORS[0]] * len(data)
        names: List[str] = []
    else:
        folded, names = fold_to_cap(data["_group"], ADJACENT_CAP)
        data["_group"] = folded
        order = {g: i for i, g in enumerate(names)}
        colors = [_color_for(order[g], g) for g in data["_group"]]

    axis.barh(positions, data[metric].to_numpy(), color=colors, height=0.68)
    axis.set_yticks(positions)
    axis.set_yticklabels([_shorten(str(v)) for v in data[labels_col]], fontsize=8)
    axis.set_xlabel(pretty(metric))
    _style(axis, value_axis="x")
    _title(axis, pretty(metric), f"{len(data)} images, sorted")

    if names:
        _legend(axis, names, proxy=True)
    figure.tight_layout()
    return figure, [str(v) for v in data[labels_col]]


def plot_compare_groups(frame, metric: str, groups) -> Tuple["object", List[str]]:
    """Per-group distribution: every image as a dot, with the mean marked.

    With a handful of images per group, showing the actual points is more
    honest than a box plot -- the reader can see n and the spread directly.
    """
    if groups is None:
        raise PlotDataError(
            "no groups found. Set a group pattern such as (dynamic|static) or chip(\\d+)"
        )
    if metric not in frame.columns:
        raise PlotDataError(f"{metric!r} is not a column in this CSV")

    import pandas as pd

    data = pd.DataFrame(
        {
            "label": frame[label_column(frame)].astype(str),
            "value": pd.to_numeric(frame[metric], errors="coerce"),
            "group": groups,
        }
    ).dropna(subset=["value"])
    if data.empty:
        raise PlotDataError(f"no numeric values in {metric!r}")

    data["group"], names = fold_to_cap(data["group"], ADJACENT_CAP)
    names = [n for n in names if n in set(data["group"])]

    figure, axis = _new_figure(max(6.0, 1.8 * len(names) + 3.0), 5.4)
    rng = np.random.default_rng(0)
    order: List[str] = []

    for i, name in enumerate(names):
        chunk = data[data["group"] == name]
        values = chunk["value"].to_numpy()
        color = _color_for(i, name)
        jitter = rng.uniform(-0.12, 0.12, size=len(values))
        axis.scatter(
            np.full(len(values), i) + jitter,
            values,
            s=54,
            color=color,
            edgecolors=SURFACE,
            linewidths=2.0,  # 2px surface ring so overlapping points stay readable
            zorder=3,
            label=name,
        )
        order.extend(chunk["label"].tolist())
        mean = float(values.mean())
        axis.plot([i - 0.28, i + 0.28], [mean, mean], color=TEXT_PRIMARY, lw=2, zorder=4)
        # Direct label: the relief rule for the low-contrast slots, and it
        # saves the reader estimating the mean off the axis. It is parked
        # above the group's highest point so it cannot land on a dot.
        axis.annotate(
            _format_value(mean),
            xy=(i, max(float(values.max()), mean)),
            xytext=(0, 12),
            textcoords="offset points",
            ha="center",
            fontsize=9.5,
            color=TEXT_PRIMARY,
            fontweight="medium",
            zorder=5,
        )

    axis.set_xticks(np.arange(len(names)))
    axis.set_xticklabels(
        [f"{n}\nn={int((data['group'] == n).sum())}" for n in names], fontsize=10
    )
    axis.set_xlim(-0.6, len(names) - 0.4)
    axis.set_ylabel(pretty(metric))
    _style(axis, value_axis="y")
    # Headroom for the labels that now sit above the tallest point.
    axis.margins(y=0.12)
    _title(axis, pretty(metric), "each dot is one image; the bar is the group mean")
    figure.tight_layout()
    return figure, order


def plot_boxplot(frame, metric: str, groups) -> Tuple["object", List[str]]:
    """Box per group with every image drawn on top.

    The box carries the median and quartiles; the dots keep the reader honest
    about how few images each box is built from, which a bare box hides.
    """
    if groups is None:
        raise PlotDataError(
            "no groups found. Set a group pattern such as (dynamic|static), "
            "or assign groups by hand"
        )
    if metric not in frame.columns:
        raise PlotDataError(f"{metric!r} is not a column in this CSV")

    import pandas as pd

    data = pd.DataFrame(
        {
            "label": frame[label_column(frame)].astype(str),
            "value": pd.to_numeric(frame[metric], errors="coerce"),
            "group": groups,
        }
    ).dropna(subset=["value"])
    if data.empty:
        raise PlotDataError(f"no numeric values in {metric!r}")

    data["group"], names = fold_to_cap(data["group"], ADJACENT_CAP)
    names = [n for n in names if n in set(data["group"])]

    figure, axis = _new_figure(max(6.0, 1.9 * len(names) + 3.0), 5.6)
    rng = np.random.default_rng(0)
    order: List[str] = []
    series = [data.loc[data["group"] == n, "value"].to_numpy() for n in names]

    boxes = axis.boxplot(
        series,
        positions=np.arange(len(names)),
        widths=0.55,
        patch_artist=True,
        showfliers=False,  # the dots below already show every point
        medianprops=dict(color=TEXT_PRIMARY, linewidth=2),
        whiskerprops=dict(color=TEXT_SECONDARY, linewidth=1.2),
        capprops=dict(color=TEXT_SECONDARY, linewidth=1.2),
    )
    for i, patch in enumerate(boxes["boxes"]):
        patch.set_facecolor(_color_for(i, names[i]))
        patch.set_alpha(0.28)
        patch.set_edgecolor(_color_for(i, names[i]))
        patch.set_linewidth(1.6)

    for i, name in enumerate(names):
        chunk = data[data["group"] == name]
        values = chunk["value"].to_numpy()
        jitter = rng.uniform(-0.13, 0.13, size=len(values))
        axis.scatter(
            np.full(len(values), i) + jitter, values, s=46,
            color=_color_for(i, name), edgecolors=SURFACE, linewidths=1.8, zorder=4,
        )
        order.extend(chunk["label"].tolist())
        median = float(np.median(values))
        axis.annotate(
            _format_value(median),
            xy=(i, float(values.max())),
            xytext=(0, 12), textcoords="offset points", ha="center",
            fontsize=9.5, color=TEXT_PRIMARY, fontweight="medium", zorder=5,
        )

    axis.set_xticks(np.arange(len(names)))
    axis.set_xticklabels(
        [f"{n}\nn={int((data['group'] == n).sum())}" for n in names], fontsize=10
    )
    axis.set_xlim(-0.6, len(names) - 0.4)
    axis.set_ylabel(pretty(metric))
    _style(axis, value_axis="y")
    axis.margins(y=0.12)
    _title(axis, pretty(metric), "box = median and quartiles; every dot is one image")
    figure.tight_layout()
    return figure, order


def plot_scatter(frame, x_metric: str, y_metric: str, groups=None) -> Tuple["object", List[str]]:
    """Two measurements against each other, one point per image."""
    for metric in (x_metric, y_metric):
        if metric not in frame.columns:
            raise PlotDataError(f"{metric!r} is not a column in this CSV")
    if x_metric == y_metric:
        raise PlotDataError("choose two different columns for a scatter plot")

    import pandas as pd

    data = pd.DataFrame(
        {
            "label": frame[label_column(frame)].astype(str),
            "x": pd.to_numeric(frame[x_metric], errors="coerce"),
            "y": pd.to_numeric(frame[y_metric], errors="coerce"),
        }
    )
    if groups is not None:
        data["group"] = groups
    data = data.dropna(subset=["x", "y"])
    if data.empty:
        raise PlotDataError("no rows where both columns are numeric")

    figure, axis = _new_figure(8.2, 5.8)
    order: List[str] = []

    if groups is None:
        axis.scatter(
            data["x"], data["y"], s=60, color=SERIES_COLORS[0],
            edgecolors=SURFACE, linewidths=2.0, zorder=3,
        )
        order = data["label"].tolist()
        names: List[str] = []
    else:
        # Every pair of colours can meet in a scatter, so cap at three.
        data["group"], names = fold_to_cap(data["group"], ALL_PAIRS_CAP)
        names = [n for n in names if n in set(data["group"])]
        for i, name in enumerate(names):
            chunk = data[data["group"] == name]
            axis.scatter(
                chunk["x"], chunk["y"], s=60, color=_color_for(i, name),
                edgecolors=SURFACE, linewidths=2.0, zorder=3, label=name,
            )
            order.extend(chunk["label"].tolist())

    axis.set_xlabel(pretty(x_metric))
    axis.set_ylabel(pretty(y_metric))
    _style(axis, value_axis="both")
    _title(axis, f"{pretty(y_metric)} vs {pretty(x_metric)}", f"{len(data)} images")
    if names:
        _legend(axis, names)
    figure.tight_layout()
    return figure, order


def plot_distribution(frame, metric: str, groups=None) -> Tuple["object", List[str]]:
    """Histogram of a column -- made for cells_per_object.csv."""
    if metric not in frame.columns:
        raise PlotDataError(f"{metric!r} is not a column in this CSV")

    import pandas as pd

    values = pd.to_numeric(frame[metric], errors="coerce")
    mask = values.notna()
    if not mask.any():
        raise PlotDataError(f"no numeric values in {metric!r}")

    figure, axis = _new_figure(8.6, 5.2)
    bins = int(np.clip(np.sqrt(mask.sum()), 10, 60))

    if groups is None:
        axis.hist(values[mask], bins=bins, color=SERIES_COLORS[0], edgecolor=SURFACE, linewidth=1.0)
        names: List[str] = []
    else:
        folded, names = fold_to_cap(groups[mask], ALL_PAIRS_CAP)
        names = [n for n in names if n in set(folded)]
        edges = np.histogram_bin_edges(values[mask], bins=bins)
        for i, name in enumerate(names):
            axis.hist(
                values[mask][folded == name], bins=edges, alpha=0.62,
                color=_color_for(i, name), edgecolor=SURFACE, linewidth=1.0, label=name,
            )

    median = float(values[mask].median())
    axis.axvline(median, color=TEXT_PRIMARY, lw=2, zorder=4)
    axis.annotate(
        f"median {_format_value(median)}",
        xy=(median, 1), xycoords=("data", "axes fraction"),
        xytext=(6, -14), textcoords="offset points",
        fontsize=9.5, color=TEXT_PRIMARY,
    )
    axis.set_xlabel(pretty(metric))
    axis.set_ylabel("count")
    _style(axis, value_axis="y")
    _title(axis, pretty(metric), f"{int(mask.sum())} objects")
    if names:
        _legend(axis, names)
    figure.tight_layout()
    return figure, []


def _legend(axis, names: Sequence[str], proxy: bool = False):
    """Identity is never carried by colour alone, so >= 2 series always get one."""
    handles = None
    if proxy:
        from matplotlib.patches import Patch

        handles = [
            Patch(facecolor=_color_for(i, name), label=name) for i, name in enumerate(names)
        ]
    legend = (
        axis.legend(handles=handles, frameon=False, fontsize=9.5, loc="best")
        if handles
        else axis.legend(frameon=False, fontsize=9.5, loc="best")
    )
    for text in legend.get_texts():
        text.set_color(TEXT_SECONDARY)


def _format_value(value: float) -> str:
    if value == 0:
        return "0"
    magnitude = abs(value)
    if magnitude >= 1e6:
        return f"{value / 1e6:.2f}M"
    if magnitude >= 1e3:
        return f"{value:,.0f}"
    if magnitude >= 10:
        # Whole numbers print whole: "900", not "900.0".
        return f"{value:,.0f}" if abs(value - round(value)) < 1e-9 else f"{value:.1f}"
    if magnitude >= 0.01:
        return f"{value:.3g}"
    return f"{value:.2e}"


def _shorten(name: str, limit: int = 42) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def build_figure(
    frame,
    plot_type: str,
    metric: str,
    second_metric: str = "",
    group_pattern: str = "",
    manual_groups: Optional[Dict[str, str]] = None,
) -> Tuple["object", List[str]]:
    """Dispatch to the right plot. Returns (figure, row labels in plot order)."""
    groups = derive_groups(frame, group_pattern, manual_groups)
    if plot_type == "boxplot":
        return plot_boxplot(frame, metric, groups)
    if plot_type == "bar per image":
        return plot_bar_per_image(frame, metric, groups)
    if plot_type == "compare groups":
        return plot_compare_groups(frame, metric, groups)
    if plot_type == "scatter":
        return plot_scatter(frame, second_metric or metric, metric, groups)
    if plot_type == "distribution":
        return plot_distribution(frame, metric, groups)
    raise PlotDataError(f"unknown plot type {plot_type!r}")
