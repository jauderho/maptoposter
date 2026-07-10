# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pillow",
#     "numpy",
# ]
# ///
"""merge_bands.py - merge N same-size poster PNGs into alternating bands.

Intended usage
--------------
This is a standalone PEP 723 script. Run it with `uv run`, which will set
up an ephemeral environment with the declared dependencies (pillow, numpy)
automatically - no project install required:

    uv run scripts/merge_bands.py IMG1.png IMG2.png -o OUTPUT.png

Typical use case: stripe the same city, rendered in different themes,
together into a single comparison image (e.g. issue #125's request to
compare theme variants side by side).

Algorithm
---------
1. Load all input images and verify they have identical (width, height).
   If any input's dimensions differ from the first, the script exits with
   a clear error identifying the mismatched file.
2. Split the output canvas into `--bands` stripes along `--direction`:
     - "vertical" (default): stripes run top-to-bottom (full height) and
       are laid out left-to-right across the *width* of the canvas.
     - "horizontal": stripes run left-to-right (full width) and are
       laid out top-to-bottom across the *height* of the canvas.
   `--bands` defaults to the number of input images (one band each). When
   `--bands` is larger than the number of inputs, images are reused in
   round-robin order. Stripe widths/heights are split as evenly as
   possible; any remainder pixels are distributed across the leading
   stripes so every input pixel column/row is covered exactly once.
3. Stripe i is copied from image `i % len(inputs)`, sliced from the
   corresponding region of that source image (not stretched or resampled)
   and pasted into the same region of the output canvas.
4. The result is saved as a single PNG. If any input has an alpha
   channel, the output is RGBA; otherwise it is RGB.

CLI switches
------------
IMG1.png IMG2.png ...   Two or more input PNGs of identical dimensions
                        (positional, required, at least 2).
-o, --output PATH       Output file path (required).
--direction {vertical,horizontal}
                        Stripe orientation. "vertical" stripes are laid
                        out across the width (default); "horizontal"
                        stripes are laid out across the height.
--bands N               Number of stripes to split the canvas into.
                        Default: number of input images (one band per
                        input, in order).
-v, --verbose           Print step-level progress messages. The script is
                        silent otherwise (aside from --dryrun's report).
--dryrun                Compute and report what would be written (output
                        path, size, mode, band boundaries) without writing
                        any file.
--force                 Allow overwriting an existing output file. Without
                        this flag, the script refuses to overwrite.

Examples
--------
    uv run scripts/merge_bands.py a.png b.png c.png -o merged.png
    uv run scripts/merge_bands.py a.png b.png -o merged.png --direction horizontal --bands 6
    uv run scripts/merge_bands.py a.png b.png -o merged.png --dryrun -v
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image


def log(message: str, *, verbose: bool) -> None:
    """Print a step-level progress message if --verbose is set."""
    if verbose:
        print(f"[merge_bands] {message}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="merge_bands.py",
        description="Merge N same-size poster PNGs into alternating bands.",
    )
    parser.add_argument(
        "inputs",
        type=Path,
        nargs="+",
        help="Two or more input PNGs of identical dimensions.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        required=True,
        help="Output file path.",
    )
    parser.add_argument(
        "--direction",
        choices=("vertical", "horizontal"),
        default="vertical",
        help='Stripe orientation: "vertical" (default) or "horizontal".',
    )
    parser.add_argument(
        "--bands",
        type=int,
        default=None,
        help="Number of stripes (default: number of input images).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Print step-level progress messages.",
    )
    parser.add_argument(
        "--dryrun",
        action="store_true",
        help="Report what would be written without writing any file.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow overwriting an existing output file.",
    )
    return parser.parse_args(argv)


def band_boundaries(total: int, bands: int) -> list[tuple[int, int]]:
    """Split `total` pixels into `bands` contiguous, non-overlapping ranges.

    Uses as-even-as-possible splitting: the first `total % bands` bands get
    one extra pixel so every band's size differs by at most 1 and the
    ranges exactly cover [0, total).
    """
    base, remainder = divmod(total, bands)
    boundaries: list[tuple[int, int]] = []
    start = 0
    for i in range(bands):
        size = base + (1 if i < remainder else 0)
        boundaries.append((start, start + size))
        start += size
    return boundaries


def merge_bands(
    images: list[Image.Image],
    *,
    direction: str,
    bands: int,
    verbose: bool,
) -> Image.Image:
    width, height = images[0].size
    has_alpha = any("A" in img.getbands() for img in images)
    mode = "RGBA" if has_alpha else "RGB"
    log(f"canvas size={width}x{height} mode={mode} bands={bands} direction={direction}", verbose=verbose)

    arrays = [np.asarray(img.convert(mode)) for img in images]
    out = np.empty_like(arrays[0])

    axis_size = width if direction == "vertical" else height
    boundaries = band_boundaries(axis_size, bands)

    for i, (start, end) in enumerate(boundaries):
        src = arrays[i % len(arrays)]
        log(f"band {i}: [{start}, {end}) <- input {i % len(arrays)}", verbose=verbose)
        if direction == "vertical":
            out[:, start:end] = src[:, start:end]
        else:
            out[start:end, :] = src[start:end, :]

    return Image.fromarray(out, mode=mode)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    inputs: list[Path] = args.inputs
    if len(inputs) < 2:
        print("error: at least 2 input images are required", file=sys.stderr)
        return 1

    for p in inputs:
        if not p.is_file():
            print(f"error: input file not found: {p}", file=sys.stderr)
            return 1

    bands = args.bands if args.bands is not None else len(inputs)
    if bands < 1:
        print("error: --bands must be >= 1", file=sys.stderr)
        return 1

    output_path: Path = args.output
    if output_path.exists() and not args.force and not args.dryrun:
        print(
            f"error: output file already exists: {output_path} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    log(f"loading {len(inputs)} input(s)", verbose=args.verbose)
    images: list[Image.Image] = []
    for p in inputs:
        img = Image.open(p)
        img.load()
        images.append(img)

    reference_size = images[0].size
    for p, img in zip(inputs, images):
        if img.size != reference_size:
            print(
                f"error: dimension mismatch: {inputs[0]} is {reference_size[0]}x{reference_size[1]} "
                f"but {p} is {img.size[0]}x{img.size[1]}",
                file=sys.stderr,
            )
            return 1

    axis_size = reference_size[0] if args.direction == "vertical" else reference_size[1]
    if bands > axis_size:
        print(
            f"error: --bands ({bands}) cannot exceed the {args.direction} extent ({axis_size}px)",
            file=sys.stderr,
        )
        return 1

    result = merge_bands(images, direction=args.direction, bands=bands, verbose=args.verbose)

    if args.dryrun:
        would_overwrite = output_path.exists()
        print(
            "[dryrun] would write "
            f"{output_path} (mode={result.mode}, size={result.size[0]}x{result.size[1]}, "
            f"bands={bands}, direction={args.direction})"
            + (
                " -- output already exists, would require --force"
                if would_overwrite and not args.force
                else ""
            )
        )
        return 0

    log(f"writing {output_path}", verbose=args.verbose)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.save(output_path)
    log("done", verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
