"""Headless entry point: run a saved settings file over a folder.

    python -m confocal.cli run  --settings my_settings.json
    python -m confocal.cli probe ./images/raw
    python -m confocal.cli template --input ./images/raw --output ./out -o s.json
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .config import Settings, default_settings_for
from .imageio import list_images, probe


def _print(message: str) -> None:
    print(message, flush=True)


def cmd_probe(args) -> int:
    paths: List[str] = []
    for target in args.paths:
        if os.path.isdir(target):
            paths.extend(list_images(target))
        else:
            paths.append(target)
    if not paths:
        _print("no images found")
        return 1
    for path in paths:
        try:
            _print(probe(path).summary())
        except Exception as exc:  # noqa: BLE001
            _print(f"{os.path.basename(path)}: ERROR {exc}")
    return 0


def cmd_template(args) -> int:
    images = list_images(args.input)
    names: Optional[List[str]] = None
    n_channels = args.channels
    if images:
        info = probe(images[0])
        n_channels = n_channels or info.n_channels
        names = info.channel_names
        _print(f"using {os.path.basename(images[0])} as the template: {info.summary()}")
    n_channels = n_channels or 4

    settings = default_settings_for(n_channels, names)
    settings.input_dir = os.path.abspath(args.input) if args.input else ""
    settings.output_dir = os.path.abspath(args.output) if args.output else ""
    settings.save(args.out)
    _print(f"wrote {args.out}")
    return 0


def cmd_run(args) -> int:
    from .pipeline import run_batch

    settings = Settings.load(args.settings)
    if args.input:
        settings.input_dir = os.path.abspath(args.input)
    if args.output:
        settings.output_dir = os.path.abspath(args.output)
    if args.limit:
        files = list_images(settings.input_dir)[: args.limit]
        settings.selected_files = files
    if args.workers is not None:
        settings.max_workers = args.workers

    problems = settings.validate()
    if problems:
        _print("settings are not valid:")
        for problem in problems:
            _print(f"  - {problem}")
        return 2

    results = run_batch(settings, progress=_print)
    failed = [r for r in results if not r.ok]
    _print(f"\nfinished: {len(results) - len(failed)} ok, {len(failed)} failed")
    for result in failed:
        _print(f"  {result.name}: {result.error}")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="confocal", description="Batch process confocal z-stacks."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_probe = sub.add_parser("probe", help="print the shape and calibration of images")
    p_probe.add_argument("paths", nargs="+", help="image files or folders")
    p_probe.set_defaults(func=cmd_probe)

    p_tpl = sub.add_parser("template", help="write a starter settings file")
    p_tpl.add_argument("--input", default="", help="input folder (read to detect channels)")
    p_tpl.add_argument("--output", default="", help="output folder to record in the file")
    p_tpl.add_argument("--channels", type=int, default=0, help="channel count override")
    p_tpl.add_argument("-o", "--out", default="settings.json", help="settings file to write")
    p_tpl.set_defaults(func=cmd_template)

    p_run = sub.add_parser("run", help="process a folder using a settings file")
    p_run.add_argument("--settings", required=True, help="settings JSON to use")
    p_run.add_argument("--input", default="", help="override the input folder")
    p_run.add_argument("--output", default="", help="override the output folder")
    p_run.add_argument("--limit", type=int, default=0, help="only process the first N images")
    p_run.add_argument("--workers", type=int, default=None, help="parallel worker count")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    import multiprocessing

    # Required on Windows, where worker processes re-import this module.
    multiprocessing.freeze_support()
    sys.exit(main())
