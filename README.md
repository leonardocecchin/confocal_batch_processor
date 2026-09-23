# Confocal batch processor

*by Klaudia Saladauskas and Leonardo Cecchin*

Batch processing for confocal z-stacks: composites with per-channel colours and
saturation limits, nucleus counting in 3D, per-channel statistics, and optional
3D views of the chip. It replaces `reference_fiji_macros/merge.ijm` and adds the
quantification the macro did not do.

Reads Nikon `.nd2` and ImageJ/OME `.tif` stacks directly — no Fiji, no
Bio-Formats, no Java.

---

> **On Windows?** Follow [WINDOWS.md](WINDOWS.md) instead — it covers
> installing Python and Git from scratch, then `setup.bat` and `run_gui.bat`.

## Install

Linux and macOS:

```bash
./setup.sh
```

Windows: double-click `setup.bat`, then `run_gui.bat`. See
[WINDOWS.md](WINDOWS.md).

That creates an isolated `.venv/` and installs everything into it. To do it by
hand:

```bash
python3 -m venv --without-pip .venv
curl -sS -o /tmp/get-pip.py https://bootstrap.pypa.io/get-pip.py
.venv/bin/python /tmp/get-pip.py
.venv/bin/pip install -r requirements.txt
```

The GUI needs Tk, which on Debian/Ubuntu is a separate package:

```bash
sudo apt install python3-tk
```

> **Why `--without-pip` on this machine.** `python3 -m venv` on its own fails
> here because `python3-venv` is not installed, and the system `virtualenv`
> silently produces a *non-isolating* environment: it lays the interpreter out
> under `.venv/local/bin`, and pip then resolves against `~/.local`, so
> `pip install` reports success while quietly installing into your user
> site-packages and upgrading things other projects pin. Creating the venv
> without pip and bootstrapping pip into it avoids both problems. Installing
> `python3-venv` (`sudo apt install python3-venv`) makes the plain
> `python3 -m venv .venv` work too.

Nothing is installed outside `.venv/`, so your `~/.local` packages — and the
versions `awebox` and friends pin — are left alone.

## Run

```bash
.venv/bin/python run_gui.py
```

Or headless, reusing a settings file the GUI saved:

```bash
.venv/bin/python -m confocal.cli probe    ./images/raw
.venv/bin/python -m confocal.cli template --input ./images/raw --output ./out -o settings.json
.venv/bin/python -m confocal.cli run      --settings settings.json
```

`run` accepts `--input`, `--output`, `--limit N` and `--workers N` to override
the file.

---

## The GUI, tab by tab

**1. Folders** — input folder, output folder, and which images to process
(selecting none means all). *Inspect first* reads the first file and fills in
the channel count and calibration.

**2. Channels** — the analysis region, then per channel: name, colour,
saturation limits, display curve, background subtraction, and which
measurements to compute. *Preview composite* renders one image with the current
settings before you commit to a batch.

**3. Cell counting** — the 3D watershed and its parameters, the annotated
counting image, plus two tuning tools that run on a crop of one image and draw
the resulting outlines.

**4. Output** — which images, 3D views and tables to write, and the scale bar.

**5. Run** — progress and a live log. Settings load/save live in the *File* menu.

**6. Plots** — charts of the statistics CSV, from this run or any earlier one.

---

## The analysis region

By default every measurement uses the whole field. Tick **measure only inside a
region** on the *Channels* tab and drag a rectangle on a preview, and from then
on:

- **counting, area, volume and intensity use that rectangle only**, the same
  one for every image in the folder — including the batch threshold, which is
  calibrated on the same pixels it will be applied to;
- **the exported images are unchanged** — composites, single channels and 3D
  views are always the full field;
- the overlay marks the region with a dashed rectangle, so a figure shows what
  was measured rather than hiding it in a settings file.

The rectangle is stored as fractions of the image size, so it keeps working
across acquisitions with different pixel dimensions.

Two extra columns record it per image: `analysis_region` (the pixel bounds, or
`whole image`) and `analysis_area_um2` / `analysis_volume_um3`. The
`*_fraction_fov` columns are fractions *of the analysed region*, so they stay
meaningful either way.

This is the clean way to leave out the pillar rows, an out-of-focus margin or a
torn edge without cropping the figures. On the EXP17 set, restricting to the
central 28 % of the field took the count from 448 to 132 — 29 %, as it should.

## Saturation limits

Each channel picks one of three modes:

| Mode | What it does | Use when |
|---|---|---|
| `manual` | Fixed min/max, exactly like the macro's `setMinAndMax`. | You already know the right window. |
| `auto_percentile` | Min/max from percentiles of **each image on its own**. | Making every image individually legible. |
| `auto_percentile_batch` | Min/max from percentiles **pooled over the folder** — one window for every image. | Comparing images by eye. |
| `roi_reference` | Min/max from percentiles inside a rectangle you drag on a preview. | Images differ a lot and you want one honest reference region. |

`roi_reference` has two scopes:

- **global** — the rectangle is measured once and every image gets the *same*
  window, so brightness stays comparable across the batch. This is what you
  want when comparing conditions.
- **per_image** — the same rectangle is re-measured on each image, normalising
  away differences in staining or laser power. Use it to make every image
  legible, not to compare them.

### Do the saturation limits affect the measurements?

**No — and this is enforced, not just intended.** Display scaling is applied
*after* every statistic has been computed, so the measurements come from the
raw (optionally background-subtracted) intensities and nothing in the display
path can reach them.

Checked on a real image by running it with display windows differing 600-fold
(`0–100`, `0–60000`, auto, and a log curve): every total area, volume and
intensity came back bit-identical. `tests/test_pipeline.py` asserts this, so it
cannot regress silently.

### What *does* break comparability: the threshold

The number that decides which voxels count as signal is the **threshold**, not
the display window — and an automatic threshold run per image moves from image
to image. On the EXP17 set, per-image Otsu spans a factor of 8 on DAPI and
nearly 15 on Vimentin, so the same structure would measure differently
depending on which picture it was in.

Every channel therefore has a **threshold scope**:

- **`batch` (default)** — the threshold is computed once, from pixels pooled
  across the folder, and the *same* cut-off is applied to every image. This is
  what makes totals comparable between images and conditions.
- **`per_image`** — the old behaviour, each image thresholded on its own. Use
  it only when images are genuinely not meant to be compared.

Both the shared threshold and the shared display window are computed in one
pass before the run, over an evenly spaced sample of the folder (*Calibration
sample*, default 8 images; 0 means all). Percentiles and Otsu are stable well
before eight images, and each sampled image costs a read. The values actually
used are printed in the log and recorded per image in `statistics.csv`, so you
can always see what cut-off produced a number.

> Note: this default changed the numbers relative to a per-image run. Totals
> measured with `batch` scope are the comparable ones; if you have earlier
> results from this tool, re-run them before comparing against new output.

### Should you use a log intensity scale?

You asked whether a log scale makes sense for signals of very different
strength. Short answer: **gamma, usually; log, occasionally; and never for the
numbers.**

All three curves are available per channel and all three are *display only* —
the statistics are always computed on the raw (optionally
background-subtracted) intensities, so changing the display never changes a
number in `statistics.csv`.

- **linear** — what Fiji does. Faithful, but dim structures disappear when one
  channel is much brighter than another.
- **gamma** (default 0.5 when selected) — lifts the dim end while keeping the
  ordering and rough proportionality of intensities. This is the conventional
  choice for microscopy figures and is what I would reach for first.
- **log** — compresses hardest, so signal spanning orders of magnitude fits in
  one image. It also makes background noise look like real signal and destroys
  any visual sense of proportion, so use it to *see* something, never to let a
  reader judge relative brightness by eye.

In practice, the bigger win for wildly varying images is a well-chosen
`roi_reference` window; the curve is a second-order adjustment on top of it.
Whichever you pick, the display limits actually used are recorded per image in
`statistics.csv` (`<channel>_display_min` / `_display_max`), so figures stay
reproducible.

---

## Cell counting

Counting runs on the **full z-stack**, not the projection. Two nuclei stacked on
top of each other are a single blob in a max projection but two clearly separate
objects in the volume, and separating them is the whole reason to work in 3D.
(`tests/test_pipeline.py` asserts exactly this on a synthetic pair.)

The pipeline:

1. Anisotropic Gaussian smoothing — sigmas are given in microns and converted
   using the real voxel size, which matters because these stacks are 0.72 µm in
   XY against 2.5 µm in Z.
2. Threshold (Otsu by default; Triangle / Li / Yen / mean / percentile / manual
   also available).
3. Fill holes slice by slice, drop objects below the minimum volume.
4. 3D Euclidean distance transform, computed in microns.
5. Smooth the distance map, find local maxima — these are the seeds.
6. Watershed in 3D, then discard objects outside the volume range.

### Tuning: `seed smoothing` is the parameter that matters

A ragged nucleus boundary produces several local maxima inside one nucleus, and
the watershed then cuts that nucleus into pieces. Smoothing the distance map
before seed-finding merges those spurious maxima. The setting is expressed as a
fraction of the nucleus radius.

On the EXP17 images the effect is dramatic and easy to see:

| seed smoothing | nuclei counted (one image) |
|---|---|
| 0.0 | 2854 — almost every nucleus split in two |
| 0.15 | 1095 |
| **0.25 (default)** | **1059** |
| 0.5 | 929 |

The value sits on a plateau between 0.15 and 0.5, which is why 0.25 is the
default: the answer is insensitive to the exact choice, and only turning the
smoothing off entirely breaks it.

Use the two buttons on the *Cell counting* tab rather than guessing:

- **Preview cell counting** — outlines on a crop, with the count.
- **Compare seed smoothing** — the same crop at 0.0 / 0.15 / 0.25 / 0.5 side by
  side. Outlines cutting single nuclei in half mean the value is too low;
  nuclei merged into one outline mean it is too high.

Other parameters worth knowing:

- `nucleus radius` — sets the minimum separation between two seeds.
- `min / max volume` — discards debris and merged clumps (µm³).
- `drop nuclei touching the XY border` — off by default. The Z borders are never
  used for this, since a cell at the top or bottom of the stack is a real cell
  that happens to extend past the acquired depth.
- `XY downsample` — 2 roughly quarters the time and memory at some loss of
  precision on small nuclei.

---

### The annotated counting image

Every run can export the picture the count came from, so a number in a table is
always checkable against the image behind it:

- **nucleus outlines** over the nuclei channel, one per counted cell, taken
  from the 3D labels — so a nucleus hidden under another in the projection
  still gets its own outline;
- **the total** (`n = 448 cells`) burned into the corner;
- optionally **a number on every cell**, matching the `cell_id` column of
  `cells_per_object.csv`. Pick a cell out of the picture, look up its row, and
  you have its volume, centroid and equivalent diameter.

Each number sits just *above* its own outline rather than on top of it: three
digits are wider than a small nucleus, and centring them would hide the very
boundary being labelled. The digits are sized from the median nucleus by
default (override with *font pt*) and carry a dark halo so they stay readable
over bright signal.

Numbering ~450 cells on a 1360x1360 field is legible but dense. Save the
overlay as **tif** as well as png (both are offered) and zoom in Fiji when you
need to read individual ids; the tif carries the pixel calibration.

Files land in `segmentation/<image>_nuclei_overlay.png`, alongside
`<image>_nuclei_labels.tif` — the raw 3D label image, if you would rather do
your own overlay.

## What gets measured

For every channel you can tick **area**, **volume** and **intensity**. Each
produces a total and a per-cell value, where per-cell divides by the counted
nuclei — the "relative" figure.

| Column | Meaning |
|---|---|
| `<ch>_total_area_um2` | Area of signal-positive pixels in the projection (µm²) |
| `<ch>_relative_area_um2_per_cell` | …divided by the cell count |
| `<ch>_area_fraction_fov` | …as a fraction of the field of view |
| `<ch>_total_volume_um3` | Volume of signal-positive voxels in the stack (µm³) |
| `<ch>_relative_volume_um3_per_cell` | …divided by the cell count |
| `<ch>_volume_fraction_fov` | …as a fraction of the imaged volume |
| `<ch>_total_intensity` | Summed intensity over signal-positive voxels |
| `<ch>_relative_intensity_per_cell` | …divided by the cell count |
| `<ch>_mean_intensity_in_signal` | Mean intensity where there is signal |
| `<ch>_threshold` | The threshold actually used (identical across images in `batch` scope) |
| `<ch>_display_min` / `_display_max` | The saturation window actually used |

Plus `n_cells`, `nuclei_threshold`, `mean_nucleus_volume_um3` and the
calibration for each image. "Signal-positive" is decided by that channel's own
threshold method, so pick it deliberately — it is the single most consequential
choice for the area and volume numbers.

Enable **cells_per_object.csv** for one row per nucleus (volume, centroid,
equivalent diameter) if you want distributions rather than per-image totals.

---

## Plots

The **Plots** tab draws the statistics CSV — the one just written, or any from
an earlier session via *Browse*. `cells_per_object.csv` works too and switches
to a distribution automatically.

| Plot | What it shows |
|---|---|
| **boxplot** | Median and quartiles per group, with every image drawn on top as a dot. The default, and the one to use for conditions. |
| **compare groups** | The same split without the box — dots and the group mean. |
| **bar per image** | One bar per image, sorted — spot outliers and rank images. |
| **scatter** | Two measurements against each other, one point per image. |
| **distribution** | Histogram with the median marked — made for per-nucleus data. |

The boxplot always draws the individual images over the box. A box built from
four chambers looks exactly like one built from forty unless the points are
shown, and at these sample sizes that difference is the whole story.

### Grouping

**Group by** takes a regular expression matched against the image name, and the
first capture group becomes the group label. `(dynamic|static)` splits the
EXP17 set by condition; `chip(\d+)` splits it by chip; blank means no pattern.

**Group by hand...** opens a list of the images where you assign groups
yourself. Hand assignments override the pattern for the images they name, so
you can pull one leaking chamber into an `excluded` group without renaming
files, or build groups the filenames do not encode at all. *Fill from pattern*
seeds the list from the current regex so you only edit the exceptions. The
assignments are saved with the settings file.

Hovering a point names the image it came from. *Save figure* writes PNG, PDF or
SVG at 200 dpi, and *Show table* opens the underlying numbers.

The colours are a validated colour-blind-safe set, assigned in fixed order and
never generated: past the safe series count the smallest groups fold into
"other" rather than inventing a new hue. Group means are always labelled
directly and a table view is always available, so nothing depends on telling
two colours apart.

## 3D views

Both are optional and off by default.

- **Isometric slab render** — the stack drawn as a tilted block, back to front
  with depth shading, so the chip reads as a solid slab. Elevation and azimuth
  are adjustable.
- **Orthogonal panel** — XY with the XZ projection below and YZ to the right,
  all at the same micron scale. Good for checking how thick the construct is and
  whether cells sit on one plane.

---

## Output layout

```
<output folder>/
  composites/      Composite_<image>.png / .tif
  channels/<name>/ <image>_<channel>.png
  3d_views/        <image>_isometric.png, <image>_orthogonal.png
  segmentation/    <image>_nuclei_overlay.png   (outlines + total + cell ids
                                                + the analysis region)
                   <image>_nuclei_labels.tif    (3D label image)
  statistics.csv       one row per image
  cells_per_object.csv one row per nucleus (optional)
  settings_used.json   the exact settings, reloadable
  processing_log.txt   what ran, how long, what failed
```

TIFFs carry the pixel calibration, so they open in Fiji already scaled. The
label image is a 3D TIFF you can overlay on the original stack.

---

## Performance and memory

Measured on the EXP17 set (18 files, 4 channels, ~55 z-slices, 1360×1360,
~800 MB each) on an 8-core machine with 15 GB of RAM:

- **~30 s per image**, 9 minutes for all 18.
- **~2.6 GB peak per worker**, 6.2 GB total with 2 workers.

Three things dominate, and each is handled deliberately:

- **Reading.** Channels are read one at a time, slice by slice. Decoding the
  whole file to keep one channel would cost 840 MB per read.
- **The distance transform.** SciPy's only works in float64 and peaked at
  5.4 GB; the optional `edt` package does the same computation in float32 in
  0.8 GB and about three times faster. Results agree to ~1e-6. If `edt` is not
  installed the SciPy path still works — it just needs far more memory.
- **Background subtraction.** A σ≈49 px Gaussian needs a ~200 px kernel and took
  47 s per channel. The background is smooth by definition, so it is estimated
  on a shrunken copy and scaled back up: ~20× faster, mean error well under one
  grey level.

**The worker count is limited by memory, not cores.** It is chosen from measured
per-image usage and capped against both free and total RAM; on this machine that
means 2 workers, not 8. Overriding it too high gets workers OOM-killed rather
than making the run faster — the *Parallel workers* box is there if you move to
a machine with more RAM.

---

## When a run looks stuck

The log updates after every image, so a silent window is the symptom to read.

- **No output and 0% CPU, started from a terminal.** The process was suspended,
  almost always by `Ctrl+Z` in the terminal it was launched from. Type `fg` in
  that terminal to resume it, or `kill %1` to end it. This stops the workers
  too, which is why nothing moves.
- **Still working.** A full 18-image run of the EXP17 set takes about 15
  minutes: a calibration pass over the sample first, then roughly 30 s per
  image. The 3D views are the slowest part; turn them off for a quick run.
- **You clicked Stop.** Stop is honoured during the calibration pass as well as
  during processing, but the images *already running* are allowed to finish
  rather than being killed mid-write — up to about a minute on this data. The
  log says so when you click, and the Stop button greys out to show the click
  registered. Only one spare image is ever queued ahead of the workers, so the
  wait stays close to one image.
- **Leftover worker processes.** Workers now die with the parent, so a closed
  or killed window leaves nothing behind. To check:

  ```bash
  ps -eo pid,stat,rss,args | grep "[.]venv/bin/python"
  ```

  Nothing should be listed once the window is gone. A `T` in the STAT column
  means suspended, not busy.

Closing the window during a run asks first, then stops the workers before
quitting.

If the window is ever genuinely frozen, `kill -USR1 <pid>` makes it print the
stack of every thread to the terminal it was started from, which says exactly
where it is stuck. (Linux and macOS; Windows has no SIGUSR1.)

## Tests

```bash
.venv/bin/python -m tests.test_pipeline      # or: pytest tests/
```

Runs on small synthetic stacks, so no `.nd2` files are needed. It checks the
settings round-trip, validation, display curves, scale-bar rounding, that
background subtraction does not blur along z, that a corrupt file does not kill
a batch, that planted nuclei are counted exactly, that two nuclei overlapping in
XY but separated in z are counted as two, **that the display settings cannot
change a measurement**, that batch scope really does give every image the same
threshold and display window, that every plot type builds, and that the
annotated counting image is written in every requested format with its cell
ids matching `cells_per_object.csv`, that the analysis region restricts the
measurements while leaving the exports full-field, and that manual groups
override the group pattern, and that a threshold which calls almost everything
(or almost nothing) signal is reported rather than passed off as a measurement.

---

## Differences from `merge.ijm`

The macro's behaviour is kept where it was deliberate: the same colour-blind
safe palette and names, black-to-colour gradient LUTs, additive composite,
max-intensity projection, background subtraction, and the "nice number" scale
bar at ~10 % of image width.

What is new: 3D cell counting, an analysis region that confines the
measurements without cropping the figures, annotated counting images with
per-cell ids,
per-channel quantification, automatic and ROI-based saturation limits,
batch-wide thresholds so images stay comparable, gamma/log display curves, 3D
views, plots of the statistics, a settings file you can reload, per-image
logging, batch parallelism, and direct `.nd2` reading without Bio-Formats.

One deliberate difference: the macro's rolling-ball background subtraction is
replaced by a Gaussian high-pass, which is visually equivalent for the smooth
illumination gradients in these images and orders of magnitude faster.

---

## Credits

Written by **Klaudia Saladauskas** and **Leonardo Cecchin**, from the Fiji macro
`reference_fiji_macros/merge.ijm`. The authorship is shown under *Help > About*
in the GUI and recorded in every `processing_log.txt` and `settings_used.json`.
