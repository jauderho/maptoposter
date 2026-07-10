# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pillow",
#     "numpy",
# ]
# ///
"""add_glow.py - add a neon/bloom glow effect to a map-poster PNG.

Intended usage
--------------
This is a standalone PEP 723 script. Run it with `uv run`, which will set
up an ephemeral environment with the declared dependencies (pillow, numpy)
automatically - no project install required:

    uv run scripts/add_glow.py INPUT.png

Algorithm
---------
1. Load the input image as RGB (or RGBA, preserving the alpha channel).
2. Compute per-pixel luminance (Rec. 709 weights) as a fraction of the
   maximum possible brightness (0..1).
3. Build a "bloom source" image: pixels whose luminance fraction exceeds
   `--threshold` contribute to the glow. A soft-knee ramp is used instead
   of a hard cutoff - contribution scales from 0 to 1 as luminance rises
   from `threshold` to 1.0, and each contributing pixel keeps its original
   color, scaled by that contribution.
4. Gaussian-blur the bloom source using `--radius`. The radius is defined
   relative to a 1000px-wide image and is scaled proportionally to the
   actual image width, so the same --radius value looks similarly "sized"
   regardless of input resolution.
5. Composite the blurred bloom back over the original image using screen
   blending (screen(a, b) = 1 - (1 - a) * (1 - b)), with the bloom's
   effective strength multiplied by `--intensity` before blending.
6. The alpha channel (if present in the input) is preserved unchanged and
   is not affected by the glow computation.

CLI switches
------------
INPUT.png             Path to the source PNG (positional, required).
-o, --output PATH      Output file path. Default: "<input stem>_glow.png"
                        written next to the input file.
--intensity FLOAT       Strength multiplier applied to the blurred bloom
                        before it is screen-blended over the original.
                        Default: 1.0. Higher values produce a stronger
                        glow; 0.0 effectively disables the effect.
--radius FLOAT          Gaussian blur radius (in pixels) for a reference
                        1000px-wide image; scaled proportionally to the
                        actual image width. Default: 8.
--threshold FLOAT       Luminance fraction (0..1) above which pixels start
                        contributing to the bloom, with a soft knee up to
                        1.0. Default: 0.5.
-v, --verbose           Print step-level progress messages. The script is
                        silent otherwise (aside from --dryrun's report).
--dryrun                Compute and report what would be written (output
                        path, size, mode) without writing any file.
--force                 Allow overwriting an existing output file. Without
                        this flag, the script refuses to overwrite.

Examples
--------
    uv run scripts/add_glow.py posters/paris_pastel_dream_20260118_141126.png
    uv run scripts/add_glow.py in.png -o out.png --intensity 1.5 --radius 12
    uv run scripts/add_glow.py in.png --dryrun -v
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# Rec. 709 luma weights.
_LUMA_WEIGHTS = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)

# --radius is calibrated against this reference image width (px).
_REFERENCE_WIDTH = 1000.0


def log(message: str, *, verbose: bool) -> None:
    """Print a step-level progress message if --verbose is set."""
    if verbose:
        print(f"[add_glow] {message}", file=sys.stderr)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="add_glow.py",
        description="Add a neon/bloom glow effect to a map-poster PNG.",
    )
    parser.add_argument("input", type=Path, help="Path to the source PNG.")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help='Output file path. Default: "<input stem>_glow.png".',
    )
    parser.add_argument(
        "--intensity",
        type=float,
        default=1.0,
        help="Bloom strength multiplier (default: 1.0).",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=8.0,
        help="Gaussian blur radius for a 1000px-wide image (default: 8).",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="Luminance fraction (0..1) above which pixels bloom (default: 0.5).",
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


def compute_luminance(rgb: np.ndarray) -> np.ndarray:
    """Return per-pixel luminance as a fraction of max brightness (0..1).

    `rgb` must be a float32 array in the 0..1 range with shape (H, W, 3).
    """
    return rgb @ _LUMA_WEIGHTS


def build_bloom_source(rgb: np.ndarray, luminance: np.ndarray, threshold: float) -> np.ndarray:
    """Return an RGB array holding only the "bright" contribution.

    Pixels below `threshold` contribute nothing. Pixels between `threshold`
    and 1.0 ramp up linearly (soft knee), keeping their original color
    scaled by the contribution factor.
    """
    if threshold >= 1.0:
        # Nothing can exceed the threshold; bloom source is all black.
        return np.zeros_like(rgb)
    denom = 1.0 - threshold
    contribution = np.clip((luminance - threshold) / denom, 0.0, 1.0)
    return rgb * contribution[..., np.newaxis]


def gaussian_blur_rgb(rgb: np.ndarray, radius: float) -> np.ndarray:
    """Gaussian-blur a float32 (H, W, 3) 0..1 array, returning the same shape/dtype."""
    if radius <= 0:
        return rgb
    u8 = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    blurred = Image.fromarray(u8, mode="RGB").filter(ImageFilter.GaussianBlur(radius=radius))
    return np.asarray(blurred, dtype=np.float32) / 255.0


def screen_blend(base: np.ndarray, top: np.ndarray) -> np.ndarray:
    """Screen-blend two float32 0..1 arrays: 1 - (1 - base) * (1 - top)."""
    return 1.0 - (1.0 - base) * (1.0 - top)


def apply_glow(
    image: Image.Image,
    *,
    intensity: float,
    radius: float,
    threshold: float,
    verbose: bool,
) -> Image.Image:
    has_alpha = "A" in image.getbands()
    log(f"input mode={image.mode} size={image.size} has_alpha={has_alpha}", verbose=verbose)

    if has_alpha:
        rgba = np.asarray(image.convert("RGBA"), dtype=np.float32) / 255.0
        rgb, alpha = rgba[..., :3], rgba[..., 3]
    else:
        rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
        alpha = None

    log("computing luminance", verbose=verbose)
    luminance = compute_luminance(rgb)

    log(f"building bloom source (threshold={threshold})", verbose=verbose)
    bloom_source = build_bloom_source(rgb, luminance, threshold)

    width = image.size[0]
    scaled_radius = radius * (width / _REFERENCE_WIDTH)
    log(
        f"gaussian-blurring bloom source (radius={radius} -> scaled={scaled_radius:.2f} "
        f"for width={width})",
        verbose=verbose,
    )
    bloom_blurred = gaussian_blur_rgb(bloom_source, scaled_radius)

    log(f"screen-blending bloom (intensity={intensity})", verbose=verbose)
    bloom_weighted = np.clip(bloom_blurred * intensity, 0.0, 1.0)
    result_rgb = screen_blend(rgb, bloom_weighted)
    result_rgb = np.clip(result_rgb, 0.0, 1.0)

    if alpha is not None:
        result = np.concatenate([result_rgb, alpha[..., np.newaxis]], axis=-1)
        out_u8 = np.clip(result * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return Image.fromarray(out_u8, mode="RGBA")

    out_u8 = np.clip(result_rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return Image.fromarray(out_u8, mode="RGB")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    input_path: Path = args.input
    if not input_path.is_file():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        return 1

    output_path: Path = args.output if args.output is not None else input_path.with_name(
        f"{input_path.stem}_glow.png"
    )

    if not 0.0 <= args.threshold <= 1.0:
        print("error: --threshold must be between 0.0 and 1.0", file=sys.stderr)
        return 1
    if args.radius < 0:
        print("error: --radius must be >= 0", file=sys.stderr)
        return 1

    if output_path.exists() and not args.force and not args.dryrun:
        print(
            f"error: output file already exists: {output_path} (use --force to overwrite)",
            file=sys.stderr,
        )
        return 1

    log(f"loading {input_path}", verbose=args.verbose)
    image = Image.open(input_path)
    image.load()

    result = apply_glow(
        image,
        intensity=args.intensity,
        radius=args.radius,
        threshold=args.threshold,
        verbose=args.verbose,
    )

    if args.dryrun:
        would_overwrite = output_path.exists()
        print(
            "[dryrun] would write "
            f"{output_path} (mode={result.mode}, size={result.size[0]}x{result.size[1]})"
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
