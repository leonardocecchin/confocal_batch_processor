# Confocal batch processor

*by Klaudia Saladauskas and Leonardo Cecchin*

Batch processing for confocal z-stacks: composites with per-channel colours and
saturation limits, 3D nucleus counting, per-channel statistics, plots and
optional 3D views. It replaces `reference_fiji_macros/merge.ijm` and adds the
quantification the macro did not do.

Reads Nikon `.nd2` and ImageJ/OME `.tif` stacks directly: no Fiji, Bio-Formats
or Java.

## Install

### Windows: pre-built app (no Python needed)

1. On the repository page open **Releases** and download
   `ConfocalProcessor-windows.zip` from the newest release (no GitHub account
   needed).
2. Right-click the zip → **Extract All** into a short local folder such as
   `C:\Users\<you>\ConfocalProcessor` (not OneDrive or a network drive).
3. Double-click `ConfocalProcessor.exe`. Keep the folder together.

Windows SmartScreen may warn because the app is not code-signed: **More info →
Run anyway**. To update, delete the folder and repeat with the newer release.

Collaborators with a GitHub login can also download the build from **Actions →
Build Windows app → a green run → Artifacts**. Artifacts need a login and expire
after 90 days, so hand out Releases.

Maintainers publish a release by pushing a tag, which builds the app and
attaches the zip:

```bash
git tag v0.1.0 && git push origin v0.1.0
```

### From source

| OS | Steps |
|---|---|
| Linux / macOS | `./setup.sh`, then `sudo apt install python3-tk` on Debian/Ubuntu |
| Windows | Install Python and Git, double-click `setup.bat`; see [WINDOWS.md](WINDOWS.md) |

This creates an isolated `.venv/`; nothing is installed outside it. By hand:

```bash
python3 -m venv --without-pip .venv
curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
.venv/bin/python /tmp/get-pip.py
.venv/bin/pip install -r requirements.txt
```

`--without-pip` avoids two traps: `python3 -m venv` fails without the
`python3-venv` package, and the system `virtualenv` can produce a
non-isolating environment whose `pip install` silently writes to `~/.local`.

## Run

```bash
.venv/bin/python run_gui.py         # Windows: run_gui.bat
```

Headless, reusing a settings file saved by the GUI:

```bash
.venv/bin/python -m confocal.cli probe    ./images/raw
.venv/bin/python -m confocal.cli template --input ./images/raw --output ./out -o settings.json
.venv/bin/python -m confocal.cli run      --settings settings.json
```

`run` accepts `--input`, `--output`, `--limit N` and `--workers N` to override
the file.

## The GUI

1. **Folders**: input and output folders, which images to process (none selected
   = all). *Inspect first* fills in the channel count and calibration.
2. **Channels**: analysis region, then per channel the name, colour, display
   settings and measurements. *Preview composite* renders one image.
3. **Cell counting**: the 3D watershed, the annotated counting image, and two
   tuning tools that run on a crop.
4. **Output**: which images, 3D views and tables to write, and the scale bar.
5. **Run**: progress and live log. Load/save settings from the *File* menu.
6. **Plots**: charts of the statistics CSV, from this run or an earlier one.

## Display versus measurement

Each channel panel has two kinds of settings, labelled in the GUI:

- **DISPLAY** (saturation limits, curve, gamma): affect the images only.
- **MEASURE** (threshold, area/volume/intensity): affect the numbers only.

Display scaling is applied after every statistic is computed, and
`tests/test_pipeline.py` checks that changing it leaves all measurements
bit-identical.

A measurement counts a voxel as signal when it is above the channel's
**threshold**:

| Method | Cut-off |
|---|---|
| `otsu`, `triangle`, `li`, `yen`, `mean` | Found automatically from the data |
| `percentile` | The given percentile of the data |
| `manual` | The value you type, in raw (background-subtracted) intensity |
| `display_min` | The lower saturation limit of the display, so the numbers follow the picture |

The GUI greys out fields that the current choice does not use and prints a hint
under each frame stating what is applied.

**Threshold scope** (automatic methods only):

- `batch` (default): one cut-off computed from pixels pooled over the folder and
  applied to every image, so totals are comparable.
- `per_image`: each image thresholded on its own. On the EXP17 set per-image
  Otsu varies 8-15-fold between images; use this only when images are not meant
  to be compared.

The shared threshold and shared display window are computed in one pass before
the run, on an evenly spaced sample (*Calibration sample*, default 8 images; 0 =
all). The values used are logged and written per image to `statistics.csv`.

## Saturation limits

| Mode | Min/max from | Use when |
|---|---|---|
| `manual` | The values you type (like the macro's `setMinAndMax`) | You know the window |
| `auto_percentile` | Percentiles of each image on its own | Making each image legible |
| `auto_percentile_batch` | Percentiles pooled over the folder, one window for all | Comparing images by eye |
| `roi_reference` | Percentiles inside a rectangle dragged on a preview | One honest reference region |

`roi_reference` is either **global** (measured once, same window for every
image, comparable) or **per_image** (re-measured on each image, legible but not
comparable).

**Curve.** `linear` is faithful but hides dim structures next to bright ones.
`gamma` (0.5) lifts the dim end and is the usual choice for figures. `log`
compresses hardest but makes noise look like signal; use it to see, not to judge
brightness. A well-chosen `roi_reference` window usually helps more than the
curve. The limits used are recorded per image (`<channel>_display_min` /
`_display_max`).

## The analysis region

Tick **measure only inside a region** on the *Channels* tab and drag a rectangle
on a preview. Then:

- counting, area, volume and intensity use that rectangle only, the same for
  every image, including the batch threshold calibration;
- exported composites, single channels and 3D views stay full-field;
- the overlay draws the region as a dashed rectangle.

It is stored as fractions of the image, so it works across acquisitions of
different sizes. `analysis_region`, `analysis_area_um2` and
`analysis_volume_um3` are recorded per image, and `*_fraction_fov` columns are
fractions of the analysed region. Use it to leave out pillar rows or a torn edge.

## Cell counting

Counting runs on the full z-stack, so two nuclei stacked in z are separated
rather than merged as in a projection.

1. Anisotropic Gaussian smoothing (sigmas in µm, using the real voxel size).
2. Threshold (Otsu by default).
3. Fill holes per slice, drop objects below the minimum volume.
4. 3D Euclidean distance transform in µm.
5. Smooth the distance map and find local maxima as seeds.
6. 3D watershed, then discard objects outside the volume range.

**`seed smoothing` is the parameter that matters.** A ragged boundary gives
several maxima per nucleus and the watershed cuts it into pieces. Counts on one
EXP17 image:

| seed smoothing | nuclei |
|---|---|
| 0.0 | 2854 (nearly every nucleus split) |
| 0.15 | 1095 |
| **0.25 (default)** | **1059** |
| 0.5 | 929 |

The result is stable between 0.15 and 0.5. Use **Preview cell counting** and
**Compare seed smoothing** (0.0 / 0.15 / 0.25 / 0.5 side by side): outlines
cutting a nucleus in half mean too low; merged nuclei mean too high.

Other parameters:

- `nucleus radius`: minimum separation between seeds.
- `min / max volume`: discards debris and merged clumps (µm³).
- `drop nuclei touching the XY border`: off by default; Z borders are never used.
- `XY downsample`: 2 roughly quarters time and memory at some cost on small nuclei.

### Annotated counting image

Each run can export the picture the count came from: nucleus outlines from the
3D labels, the total (`n = 448 cells`), and optionally a number above each cell
matching `cell_id` in `cells_per_object.csv`. Digits are sized from the median
nucleus (override with *font pt*). For dense fields save the overlay as **tif**
too and zoom in Fiji. Files go to `segmentation/` as `<image>_nuclei_overlay.png`
and `<image>_nuclei_labels.tif` (the raw 3D labels).

## What gets measured

Tick **area**, **volume** and **intensity** per channel. Each gives a total and a
per-cell value (divided by the counted nuclei).

| Column | Meaning |
|---|---|
| `<ch>_total_area_um2` | Area of signal pixels in the projection |
| `<ch>_relative_area_um2_per_cell` | …per cell |
| `<ch>_area_fraction_fov` | …as a fraction of the field |
| `<ch>_total_volume_um3` | Volume of signal voxels |
| `<ch>_relative_volume_um3_per_cell` | …per cell |
| `<ch>_volume_fraction_fov` | …as a fraction of the volume |
| `<ch>_total_intensity` | Summed intensity over signal voxels |
| `<ch>_relative_intensity_per_cell` | …per cell |
| `<ch>_mean_intensity_in_signal` | Mean intensity where there is signal |
| `<ch>_threshold` | Threshold actually used |
| `<ch>_display_min` / `_display_max` | Saturation window actually used |

Plus `n_cells`, `nuclei_threshold`, `mean_nucleus_volume_um3` and the
calibration. The threshold method is the most consequential choice for area and
volume. Enable `cells_per_object.csv` for one row per nucleus (volume, centroid,
equivalent diameter).

## Plots

The **Plots** tab draws the statistics CSV (this run's or any earlier one via
*Browse*); `cells_per_object.csv` switches to a distribution automatically.

| Plot | Shows |
|---|---|
| boxplot | Median and quartiles per group, every image as a dot (default) |
| compare groups | Dots and group mean |
| bar per image | One sorted bar per image, to spot outliers |
| scatter | Two measurements against each other |
| distribution | Histogram with median, for per-nucleus data |

**Group by** is a regular expression on the image name whose first capture group
is the label, e.g. `(dynamic|static)` or `chip(\d+)`. **Group by hand...**
assigns groups manually and overrides the pattern for the images it names (e.g.
an `excluded` group); *Fill from pattern* seeds the list. Assignments are saved
with the settings. Hovering a point names its image; *Save figure* writes PNG,
PDF or SVG at 200 dpi. Colours are a fixed colour-blind-safe set; beyond it the
smallest groups fold into "other".

## 3D views

Both optional and off by default:

- **Isometric slab**: the stack as a tilted, depth-shaded block.
- **Orthogonal panel**: XY with XZ below and YZ to the right at the same µm scale.

## Output layout

```
<output folder>/
  composites/      Composite_<image>.png / .tif
  channels/<name>/ <image>_<channel>.png
  3d_views/        <image>_isometric.png, <image>_orthogonal.png
  segmentation/    <image>_nuclei_overlay.png, <image>_nuclei_labels.tif
  statistics.csv       one row per image
  cells_per_object.csv one row per nucleus (optional)
  settings_used.json   the exact settings, reloadable
  processing_log.txt   what ran, how long, what failed
```

TIFFs carry the pixel calibration, so they open in Fiji already scaled.

## Performance and memory

On the EXP17 set (18 files, 4 channels, ~55 slices, 1360×1360, ~800 MB each; 8
cores, 15 GB RAM): ~30 s per image, ~9 min total, ~2.6 GB peak per worker.

- Channels are read one at a time, slice by slice.
- The optional `edt` package does the distance transform in float32 (0.8 GB
  instead of 5.4 GB with SciPy, ~3× faster).
- Background subtraction is estimated on a shrunken copy (~20× faster, error well
  under one grey level).

The worker count is limited by memory, not cores (2 on this machine). Setting
*Parallel workers* too high gets workers OOM-killed.

## When a run looks stuck

- **No output, 0% CPU, started from a terminal**: the process was suspended
  (usually `Ctrl+Z`). Type `fg` to resume or `kill %1` to end it.
- **Still working**: a full 18-image run takes ~15 min (calibration pass, then
  ~30 s per image). The 3D views are slowest; turn them off for quick runs.
- **After clicking Stop**: images already running finish rather than being
  killed mid-write (up to about a minute). Workers die with the parent.
- **Frozen window** (Linux/macOS): `kill -USR1 <pid>` prints every thread's stack
  to the launching terminal.

Closing the window during a run asks first, then stops the workers.

## Tests

```bash
.venv/bin/python -m tests.test_pipeline      # or: pytest tests/
```

Uses small synthetic stacks, so no `.nd2` files are needed. Covers settings
round-trip and validation, display curves, background subtraction, corrupt
files, exact nucleus counts (including nuclei overlapping in XY but not z),
display settings not changing measurements, batch-wide thresholds and windows,
plots, the annotated counting image, the analysis region, manual groups, and
warnings for implausible thresholds.

## Differences from `merge.ijm`

Kept from the macro: the colour-blind-safe palette and names, black-to-colour
LUTs, additive composite, max projection, background subtraction and the
"nice number" scale bar at ~10 % of image width.

New: 3D cell counting, analysis region, annotated counting images,
per-channel quantification, automatic and ROI-based saturation limits,
batch-wide thresholds, gamma/log curves, 3D views, plots, reloadable settings,
per-image logging, parallelism, and `.nd2` reading without Bio-Formats.

The macro's rolling-ball background subtraction is replaced by a Gaussian
high-pass, visually equivalent for smooth illumination gradients and much faster.

## Credits

Written by **Klaudia Saladauskas** and **Leonardo Cecchin** from the Fiji macro
`reference_fiji_macros/merge.ijm`. Authorship is shown under *Help > About* and
recorded in every `processing_log.txt` and `settings_used.json`.
