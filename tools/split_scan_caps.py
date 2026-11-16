#!/usr/bin/env python3
"""Split a flatbed scan of replacement OP-1 caps into one crop per cap.

The caps are moulded shells: a bright circular top face, a dark shadow ring on
the slope, then a rounded-square skirt. They lie on crinkled packing film. So the
thing that identifies a cap is not its brightness in absolute terms (the lamp
falls off) but that its face is *locally the brightest* thing around it. A white
top-hat isolates exactly that: a bright disc on a darker ring.

Run: /usr/bin/python3 tools/split_scan_caps.py scan.png -o refs/print/caps --debug overlay.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

FACE_MM = (5.0, 16.0)  # accepted top-face diameter range
FILL = (0.50, 0.95)  # a disc fills ~0.79 of its box; a merged pair fills less per box


def face_boxes(gray: np.ndarray, px_per_mm: float, scale: int) -> list[tuple[int, int, int, int]]:
    small = gray[::scale, ::scale]
    spp = px_per_mm / scale

    # White top-hat: subtract a heavily smoothed copy so only structure smaller
    # than the window survives. Window must exceed the cap, so 20 mm.
    window = int(20 * spp) | 1
    background = ndimage.grey_opening(small, size=(window, window), mode="nearest")
    top_hat = small - background

    level = float(np.quantile(top_hat, 0.80))
    bright = top_hat > max(level * 0.45, 0.02)
    bright = ndimage.binary_opening(bright, np.ones((5, 5)))
    bright = ndimage.binary_closing(bright, np.ones((9, 9)))
    bright = ndimage.binary_fill_holes(bright)

    lab, n = ndimage.label(bright)
    out: list[tuple[int, int, int, int]] = []
    for index, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        mm_w, mm_h = w / spp, h / spp
        area = int((lab[sl] == index).sum())
        fill = area / max(w * h, 1)
        if not (FACE_MM[0] <= mm_w <= FACE_MM[1] and FACE_MM[0] <= mm_h <= FACE_MM[1]):
            continue
        if abs(mm_w - mm_h) > 0.45 * max(mm_w, mm_h):
            continue
        if not (FILL[0] <= fill <= FILL[1]):
            continue
        out.append((xs.start * scale, ys.start * scale, xs.stop * scale, ys.stop * scale))
    return sorted(out, key=lambda b: (round(b[1] / (5 * px_per_mm)), b[0]))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("refs/print/caps"))
    ap.add_argument("--dpi", type=float, default=600.0)
    ap.add_argument("--margin", type=float, default=0.22, help="extra around the face, so the rim is available")
    ap.add_argument("--scale", type=int, default=4, help="detection runs downsampled by this factor")
    ap.add_argument("--debug", type=Path, default=None)
    args = ap.parse_args(argv)

    img = Image.open(args.scan).convert("RGB")
    gray = np.asarray(img.convert("L")).astype(np.float32) / 255.0
    px_per_mm = args.dpi / 25.4
    boxes = face_boxes(gray, px_per_mm, args.scale)
    if not boxes:
        raise SystemExit("no cap faces found: wrong --dpi?")
    print(f"{len(boxes)} faces detected, first={boxes[0]}, last={boxes[-1]}")

    args.outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, (x0, y0, x1, y1) in enumerate(boxes, start=1):
        w, h = x1 - x0, y1 - y0
        m = int(round(max(w, h) * args.margin))
        box = (max(x0 - m, 0), max(y0 - m, 0), min(x1 + m, img.width), min(y1 + m, img.height))
        name = f"cap_{i:02d}.png"
        img.crop(box).save(args.outdir / name, optimize=True)
        rows.append(
            {
                "file": name,
                "face_px": [int(w), int(h)],
                "face_mm": [round(w / px_per_mm, 2), round(h / px_per_mm, 2)],
                "box": [int(v) for v in box],
            }
        )

    (args.outdir / "caps.json").write_text(json.dumps(rows, indent=1))
    if args.debug:
        over = img.copy()
        draw = ImageDraw.Draw(over, "RGBA")
        for r in rows:
            draw.rectangle((r["box"][0], r["box"][1], r["box"][2], r["box"][3]), outline=(255, 0, 0, 255), width=8)
        over.resize((over.width // 5, over.height // 5)).save(args.debug)

    for r in rows:
        print(f"{r['file']}  face {r['face_mm'][0]:5.2f} x {r['face_mm'][1]:5.2f} mm  box={r['box']}")
    print(f"{len(rows)} caps -> {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
