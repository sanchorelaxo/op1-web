#!/usr/bin/env python3
"""Turn a PDF into something an agent and a human can both read.

Plain text extraction throws away the half of this manual that lives in
pictures: the OP-1 display, the knob positions, the Finder window, the key
combinations drawn on the panel. So this tool keeps both layers and keeps them
in order:

  index    one markdown file per document, with every image written out to
           figures/ and linked back into the page text at its own vertical
           position. Grep-able like text, viewable like the page.
  render   page images at a chosen dpi, for when a question is about what the
           screen looks like rather than what the paragraph says.
  text     the fast path: text of a page range on stdout.

Needs pymupdf (``pip install pymupdf``). No models, no OCR, instant.

Usage:
    python3 tools/pdf_manual.py index refs/manual_pdf/manual.pdf
    python3 tools/pdf_manual.py render refs/manual_pdf/manual.pdf --pages 12-14
    python3 tools/pdf_manual.py text refs/manual_pdf/manual.pdf --pages 12-14
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pymupdf

# Anything smaller than this in both directions is a rule, bullet or logo dot,
# not a figure: dropping them keeps the markdown readable and the diff small.
MIN_FIGURE_PX = 60


def parse_pages(spec: str | None, count: int) -> list[int]:
    """'3', '12-15', '1,4,9-11' -> zero-based page indices, clamped to the doc."""
    if not spec:
        return list(range(count))
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            lo, hi = int(a), int(b)
            out.extend(range(lo, hi + 1))
        else:
            out.append(int(chunk))
    return [p - 1 for p in out if 1 <= p <= count]


def slug(text: str, limit: int = 40) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:limit].strip("-") or "page"


def page_figures(doc: pymupdf.Document, page: pymupdf.Page, stem: str,
                 out_dir: Path, written: dict[str, str]) -> list[dict]:
    """Write this page's images to disk and describe them with their position."""
    figures: list[dict] = []
    seen_on_page: set[str] = set()
    for info in page.get_images(full=True):
        xref = info[0]
        width, height = info[2], info[3]
        if width < MIN_FIGURE_PX and height < MIN_FIGURE_PX:
            continue
        rects = page.get_image_rects(xref)
        if not rects:
            continue  # drawn but clipped away: not visible on the page
        try:
            pix = pymupdf.Pixmap(doc, xref)
            # JPX/indexed colour spaces cannot be written as PNG directly: ask
            # pymupdf for an RGB rendering of them first.
            if not pix.colorspace or pix.colorspace.name not in ("DeviceGray", "DeviceRGB"):
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            data = pix.tobytes("png")
        except Exception as err:                # noqa: BLE001 - report, keep going
            print(f"  ! page {page.number + 1} xref {xref}: {err}", file=sys.stderr)
            continue
        digest = hashlib.sha1(data).hexdigest()
        if digest in seen_on_page:
            continue
        seen_on_page.add(digest)
        if digest in written:                   # same picture on another page
            name = written[digest]
        else:
            name = f"{stem}-p{page.number + 1:02d}-{len(written) + 1:03d}.png"
            (out_dir / name).write_bytes(data)
            written[digest] = name
        figures.append({
            "file": name,
            "page": page.number + 1,
            "width": pix.width,
            "height": pix.height,
            "y": round(rects[0].y0, 1),
        })
    return figures


def page_markdown(page: pymupdf.Page, figures: list[dict]) -> str:
    """Text blocks and figure links, merged in reading order by vertical position."""
    items: list[tuple[float, str]] = []
    for block in page.get_text("blocks"):
        y0, y1, text = block[1], block[3], block[4]
        text = text.strip()
        if not text:
            continue
        items.append((y0, text.replace("\r", "")))
    for fig in figures:
        items.append((fig["y"], f"![{fig['file']}](figures/{fig['file']})"))
    items.sort(key=lambda pair: pair[0])
    body = "\n\n".join(text for _, text in items)
    return body.strip()


def cmd_index(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    out_dir = Path(args.figures_dir) if args.figures_dir else pdf.parent / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = Path(args.out) if args.out else pdf.with_suffix(".md")
    stem = slug(pdf.stem)

    pages = parse_pages(args.pages, doc.page_count)
    written: dict[str, str] = {}
    manifest: list[dict] = []
    parts: list[str] = [
        f"# {doc.metadata.get('title') or pdf.stem}",
        "",
        f"- source: `{pdf.name}`",
        f"- pages: {doc.page_count}",
        f"- figures: `{out_dir.name}/` (one file per picture, named by page)",
        "",
        "Text and figures are interleaved in the order they appear on the page, so",
        "a paragraph about the display is followed by the display.",
        "",
        "Only bitmap art is extracted here. Diagrams and display mock-ups drawn as",
        "vector lines are not images in the PDF at all, so they survive only in the",
        "page renders:",
        "",
        "```",
        "python3 tools/pdf_manual.py render refs/manual_pdf/<manual>.pdf --pages 21",
        "```",
        "",
    ]

    for index in pages:
        page = doc[index]
        figures = page_figures(doc, page, stem, out_dir, written)
        manifest.append({
            "page": index + 1,
            "figures": [f["file"] for f in figures],
            "text_chars": len(page.get_text()),
        })
        first_line = page.get_text().strip().splitlines()
        heading = first_line[0][:70] if first_line else f"page {index + 1}"
        parts.append(f"## p{index + 1} - {heading}")
        parts.append("")
        parts.append(page_markdown(page, figures) or "_(no text on this page)_")
        parts.append("")

    md_path.write_text("\n".join(parts), encoding="utf-8")
    manifest_path = md_path.with_suffix(".figures.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    total_figures = len(written)
    print(f"{md_path}  ({md_path.stat().st_size:,} bytes, {len(pages)} pages)")
    print(f"{manifest_path}  ({total_figures} unique figures in {out_dir})")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    pdf = Path(args.pdf)
    doc = pymupdf.open(pdf)
    out_dir = Path(args.out) if args.out else pdf.parent / "pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    for index in parse_pages(args.pages, doc.page_count):
        target = out_dir / f"{slug(pdf.stem)}-p{index + 1:02d}.png"
        if target.exists() and not args.force:
            print(target)
            continue
        pix = doc[index].get_pixmap(dpi=args.dpi)
        pix.save(target)
        print(target)
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    doc = pymupdf.open(Path(args.pdf))
    for index in parse_pages(args.pages, doc.page_count):
        print(f"--- p{index + 1} ---")
        print(doc[index].get_text().strip())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read a PDF as text and pictures, not text alone.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("pdf")
        p.add_argument("--pages", help="1-based range: 3, 12-15, 1,4,9-11")
        return p

    p_index = common("index", "markdown + extracted figures, interleaved")
    p_index.add_argument("--out", help="markdown path (default: beside the pdf)")
    p_index.add_argument("--figures-dir", help="figure directory (default: <pdf dir>/figures)")

    p_render = common("render", "page images at --dpi")
    p_render.add_argument("--dpi", type=int, default=150)
    p_render.add_argument("--out", help="output directory (default: <pdf dir>/pages)")
    p_render.add_argument("--force", action="store_true", help="re-render existing files")

    common("text", "plain text of a page range")
    p_text = sub.choices["text"]
    p_text.set_defaults(pages=None)

    args = parser.parse_args(argv)
    return {"index": cmd_index, "render": cmd_render, "text": cmd_text}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
