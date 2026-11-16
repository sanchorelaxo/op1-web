#!/usr/bin/env python3
"""Turn phone photos of the OP-1 into clean white reference plates.

Two things go wrong when you shoot an aged OP-1 on a desk: the background is
whatever the desk was, and the ABS has yellowed so "white" reads as beige. This
script fixes both without inventing detail:

  1. Segment the device out of the background and composite onto pure white.
  2. Measure the plastic's actual tint (the low-saturation bright pixels inside
     the device), then divide it out in linear light so the plastic reads
     neutral.
  3. Nudge the top of the low-saturation range to true white with a soft knee,
     because yellowed ABS rarely reaches 255 even after white balance. Dark
     print and the coloured encoder caps keep their values.

Run: /usr/bin/python3 tools/whiten_photos.py <in.jpg>... -o <outdir>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

# sRGB <-> linear, because channel gains only behave if applied in linear light.
_TO_LINEAR = np.array([((c / 255.0 + 0.055) / 1.055) ** 2.4 if c / 255.0 > 0.04045
                       else (c / 255.0) / 12.92 for c in range(256)], dtype=np.float32)


def to_linear(rgb: np.ndarray) -> np.ndarray:
    idx = np.clip(rgb * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return _TO_LINEAR[idx]


def to_srgb(lin: np.ndarray) -> np.ndarray:
    lin = np.clip(lin, 0.0, 1.0)
    out = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)
    return out


def otsu(values: np.ndarray, bins: int = 256) -> float:
    hist, edges = np.histogram(values, bins=bins, range=(0.0, 1.0))
    hist = hist.astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 0.5
    centres = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist)
    w1 = total - w0
    m0 = np.cumsum(hist * centres)
    mt = m0[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mt * w0 / total - m0) ** 2 / (w0 * w1 / total**2)
    between = np.nan_to_num(between)
    return float(centres[int(np.argmax(between))])


def device_mask(gray: np.ndarray) -> np.ndarray:
    """Solid mask of the device: threshold, then fill each row between its edges."""
    threshold = max(otsu(gray), 0.18)
    raw = gray > threshold
    # Drop stray speckle before the row fill so a bright reflection cannot
    # stretch a row across the whole frame.
    img = Image.fromarray((raw * 255).astype(np.uint8))
    img = img.filter(ImageFilter.MedianFilter(5))
    raw = np.asarray(img) > 127

    filled = np.zeros_like(raw)
    rows = np.where(raw.any(axis=1))[0]
    if rows.size == 0:
        raise SystemExit("could not find the device: the frame is too dark")
    for y in range(rows[0], rows[-1] + 1):
        xs = np.where(raw[y])[0]
        if xs.size < 8:  # ignore near-empty rows
            continue
        # Fill between the leftmost and rightmost bright pixel of the row.
        filled[y, xs[0]: xs[-1] + 1] = True

    # Keep the tallest run of filled rows so a separate bright object cannot join in.
    col_any = filled.any(axis=1)
    best = cur = (0, 0)
    start = None
    for y, v in enumerate(col_any):
        if v and start is None:
            start = y
        elif not v and start is not None:
            if y - start > best[1] - best[0]:
                best = (start, y)
            start = None
    if start is not None and len(col_any) - start > best[1] - best[0]:
        best = (start, len(col_any))
    keep = np.zeros_like(filled)
    keep[best[0]: best[1]] = filled[best[0]: best[1]]

    soft = Image.fromarray((keep * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.2))
    return np.asarray(soft).astype(np.float32) / 255.0


def plastic_white(rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Per-channel colour of the brightest low-saturation pixels: the aged ABS."""
    m = mask > 0.5
    if m.sum() < 1000:
        raise SystemExit("not enough device pixels to measure the plastic")
    px = rgb[m]
    mx = px.max(axis=1)
    mn = px.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    plastic = px[(sat < 0.22) & (mx > 0.55)]
    if plastic.shape[0] < 500:  # fall back to the plain bright pixels
        plastic = px[mx > np.quantile(mx, 0.90)]
    return np.quantile(plastic, 0.92, axis=0)


def whiten(rgb: np.ndarray, gains: np.ndarray, lift: float, knee: float = 0.72) -> np.ndarray:
    lin = to_linear(rgb) * gains
    out = to_srgb(lin)
    # Soft knee: low-saturation pixels above `knee` ease towards 1.0, so
    # yellowed plastic lands on white while print and caps stay put.
    mx = out.max(axis=2, keepdims=True)
    mn = out.min(axis=2, keepdims=True)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    neutral = sat < 0.18
    t = np.clip((out - knee) / max(1.0 - knee, 1e-3), 0.0, 1.0)
    boost = lift * (t * t * (3 - 2 * t)) * neutral
    return np.clip(out + (1.0 - out) * boost, 0.0, 1.0)


def process(src: Path, dest: Path, lift: float, knee: float) -> dict:
    img = Image.open(src).convert("RGB")
    rgb = np.asarray(img).astype(np.float32) / 255.0
    gray = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    mask = device_mask(gray)
    white = plastic_white(rgb, mask)
    gains = (1.0 / np.maximum(white, 0.05)).astype(np.float32)
    corrected = whiten(rgb, gains, lift, knee)
    alpha = mask[:, :, None]
    out = corrected * alpha + (1.0 - alpha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((out * 255 + 0.5).astype(np.uint8)).save(dest, optimize=True)
    return {
        "src": src.name,
        "dest": str(dest),
        "measured_white": [round(float(c), 3) for c in white],
        "gains": [round(float(g), 3) for g in gains],
        "device_fraction": round(float(mask.mean()), 3),
        "size": img.size,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("refs/print/white"))
    ap.add_argument("--lift", type=float, default=0.9, help="how hard to push plastic to white")
    ap.add_argument("--knee", type=float, default=0.72, help="brightness where the white push starts")
    args = ap.parse_args(argv)
    for src in args.images:
        info = process(src, args.outdir / (src.stem + "_white.png"), args.lift, args.knee)
        print(info)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
