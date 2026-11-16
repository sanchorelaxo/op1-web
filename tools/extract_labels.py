#!/usr/bin/env python3
"""Pull just the printed labels out of a flatbed scan of replacement caps.

No need to find the caps themselves: the print is the darkest thing on the sheet
(ink sits near 0.15-0.38, the plastic face near 0.67, the skirt and shadow ring
0.43-0.55, the packing film 0.61-0.63). So the ink can be taken directly, and the
glyphs of one cap grouped by proximity: two marks on the same cap are at most
~6 mm apart, while the nearest marks on neighbouring caps are ~11 mm apart.

That grouping is the whole trick. It means a merged or slightly wrong cap crop
cannot corrupt a label, and a blank cap simply produces no label.

Output: one transparent PNG per label, tight-cropped, coloured with its own ink,
ready to be multiplied onto the CAD keycaps.

Run: /usr/bin/python3 tools/extract_labels.py scan.png -o refs/print/labels
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from scipy.spatial import cKDTree

LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def ink_mask(rgb: np.ndarray) -> np.ndarray:
    gray = rgb @ LUMA
    mx, mn = rgb.max(axis=2), rgb.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    # Flat-field first: the lamp falls off across the platen, and a global
    # threshold would lose print at the dark end of the page.
    flat = ndimage.gaussian_filter(gray, 150)
    norm = gray / np.maximum(flat, 1e-3)
    ink = (norm < 0.68) | ((sat > 0.25) & (norm < 0.95))
    ink = ndimage.binary_opening(ink, np.ones((3, 3)))
    return ink


def group_glyphs(ink: np.ndarray, max_gap_px: float, min_px: int, max_span_px: float) -> list[dict]:
    """Cluster ink blobs into labels, but never grow a cluster past one cap.

    Single-linkage would chain across neighbouring caps, so each merge is also
    tested against a size limit: the combined box must still fit on one cap face.
    """
    lab, n = ndimage.label(ink, structure=np.ones((3, 3)))
    if n == 0:
        return []
    sizes = ndimage.sum(ink, lab, range(1, n + 1))
    border_labels = set(np.unique(np.concatenate([
        lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]]))) - {0}
    blobs = []
    hole_span = 5.5 * 600 / 25.4
    for index, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None or sizes[index - 1] < min_px or index in border_labels:
            continue
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        # The tall caps are shells with a black through-hole. That hole is a big
        # filled dark disc, and it is the only thing on the sheet that looks like
        # one, so drop it: nothing printed on a cap is that large and that solid.
        if max(w, h) > hole_span and sizes[index - 1] / max(w * h, 1) > 0.75:
            continue
        blobs.append(
            {
                "label": index,
                "box": [xs.start, ys.start, xs.stop, ys.stop],
            }
        )
    if not blobs:
        return []

    def gap(a: dict, b: dict) -> float:
        ax0, ay0, ax1, ay1 = a["box"]
        bx0, by0, bx1, by1 = b["box"]
        return float(np.hypot(
            (ax0 + ax1 - bx0 - bx1) / 2,
            (ay0 + ay1 - by0 - by1) / 2,
        ))

    merged = True
    while merged:
        merged = False
        for i in range(len(blobs)):
            for j in range(i + 1, len(blobs)):
                if gap(blobs[i], blobs[j]) > max_gap_px:
                    continue
                ax0, ay0, ax1, ay1 = blobs[i]["box"]
                bx0, by0, bx1, by1 = blobs[j]["box"]
                union = [min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1)]
                if union[2] - union[0] > max_span_px or union[3] - union[1] > max_span_px:
                    continue
                merged_labels = []
                for side in (blobs[i]["label"], blobs[j]["label"]):
                    merged_labels.extend(side if isinstance(side, list) else [side])
                blobs[i] = {"label": merged_labels, "box": union}
                blobs.pop(j)
                merged = True
                break
            if merged:
                break

    out = []
    for blob in blobs:
        members = blob["label"] if isinstance(blob["label"], list) else [blob["label"]]
        mask = np.isin(lab, members)
        ys, xs = np.where(mask)
        out.append(
            {
                "mask": mask,
                "box": (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1),
                "centroid": (float(xs.mean()), float(ys.mean())),
                "glyphs": len(members),
                "ink_px": int(mask.sum()),
            }
        )
    out.sort(key=lambda g: (round(g["centroid"][1] / 60), g["centroid"][0]))
    return out


def render(rgb: np.ndarray, group: dict, face: float, pad: int) -> tuple[Image.Image, list[int]]:
    x0, y0, x1, y1 = group["box"]
    x0, y0 = max(x0 - pad, 0), max(y0 - pad, 0)
    x1, y1 = min(x1 + pad, rgb.shape[1]), min(y1 + pad, rgb.shape[0])
    mask = group["mask"][y0:y1, x0:x1]
    patch = rgb[y0:y1, x0:x1]

    gray = patch @ LUMA
    alpha = np.clip((face - gray) / max(face * 0.5, 1e-3), 0.0, 1.0)
    alpha = np.where(ndimage.binary_dilation(mask, np.ones((3, 3))), np.maximum(alpha, 0.5), 0.0)
    alpha = ndimage.gaussian_filter(alpha, 0.5)

    px = patch[mask]
    lum = px @ LUMA
    coloured = px[(lum < 0.75) & ((px.max(axis=1) - px.min(axis=1)) > 0.05)]
    colour = np.median(coloured if coloured.size else px, axis=0)

    out = np.empty((y1 - y0, x1 - x0, 4), dtype=np.float32)
    out[..., :3] = colour
    out[..., 3] = alpha
    img = Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8), "RGBA")
    return img, [int(round(v * 255)) for v in colour]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scan", type=Path)
    ap.add_argument("-o", "--outdir", type=Path, default=Path("refs/print/labels"))
    ap.add_argument("--dpi", type=float, default=600.0)
    ap.add_argument("--gap-mm", type=float, default=9.0, help="group glyphs closer than this")
    ap.add_argument("--min-mm2", type=float, default=0.25, help="drop ink blobs smaller than this area")
    ap.add_argument("--span-mm", type=float, default=11.5, help="largest label box, one cap face")
    ap.add_argument("--face", type=float, default=0.67, help="plastic face level used for alpha")
    ap.add_argument("--pad", type=int, default=14)
    args = ap.parse_args(argv)

    rgb = np.asarray(Image.open(args.scan).convert("RGB")).astype(np.float32) / 255.0
    px_per_mm = args.dpi / 25.4
    ink = ink_mask(rgb)
    groups = group_glyphs(ink, args.gap_mm * px_per_mm, int(args.min_mm2 * px_per_mm**2), args.span_mm * px_per_mm)
    if not groups:
        raise SystemExit("no print found: check --dpi")

    args.outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, group in enumerate(groups, start=1):
        img, colour = render(rgb, group, args.face, args.pad)
        name = f"label_{i:02d}.png"
        img.save(args.outdir / name, optimize=True)
        cx, cy = group["centroid"]
        rows.append(
            {
                "file": name,
                "glyphs": group["glyphs"],
                "ink_px": group["ink_px"],
                "ink_rgb": colour,
                "ink_hex": "#%02x%02x%02x" % tuple(colour),
                "sheet_mm": [round(cx / px_per_mm, 1), round(cy / px_per_mm, 1)],
                "px": [img.width, img.height],
            }
        )
        print(f"{name}  {img.width:4d}x{img.height:3d}px  glyphs={group['glyphs']:2d}  ink={rows[-1]['ink_hex']}  at {rows[-1]['sheet_mm']} mm")

    (args.outdir / "labels.json").write_text(json.dumps(rows, indent=1))
    print(f"{len(rows)} labels -> {args.outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))