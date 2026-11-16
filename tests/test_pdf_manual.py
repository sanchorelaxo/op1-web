"""Tests for tools/pdf_manual.py, the PDF reader that keeps the pictures.

The point of the tool is that text and figures stay in page order and that the
figures actually land on disk, so that is what these tests build a small PDF to
prove: a text block, a picture under it, the same picture again on page two.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="tools/pdf_manual.py needs pymupdf")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import pdf_manual  # noqa: E402


def make_pdf(path: Path) -> Path:
    """Two pages: text above a picture on page one, the same picture again on two."""
    doc = pymupdf.open()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 120, 90), False)
    pix.set_rect(pix.irect, (200, 60, 60))
    png = path.with_suffix(".png")
    png.write_bytes(pix.tobytes("png"))

    for index in range(2):
        page = doc.new_page()
        page.insert_text((72, 90), f"page {index + 1} alpha text")
        page.insert_image(pymupdf.Rect(72, 120, 312, 300), filename=str(png))
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def manual(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "manual.pdf")


def test_index_writes_markdown_manifest_and_one_figure(manual: Path, tmp_path: Path) -> None:
    md = tmp_path / "out.md"
    figures = tmp_path / "figs"
    code = pdf_manual.main(["index", str(manual), "--out", str(md),
                            "--figures-dir", str(figures)])
    assert code == 0

    text = md.read_text(encoding="utf-8")
    assert "## p1 - page 1 alpha text" in text
    assert "## p2 - page 2 alpha text" in text
    # figure linked from markdown, and the file really exists
    linked = [line for line in text.splitlines() if line.startswith("![")]
    assert linked, "no figure link written"
    name = linked[0].split("](")[1].removeprefix("figures/").rstrip(")")
    assert (figures / name).is_file()
    assert (figures / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # the same picture on both pages is stored once and linked twice
    assert len(list(figures.glob("*.png"))) == 1
    assert len(linked) == 2

    manifest = json.loads(md.with_suffix(".figures.json").read_text(encoding="utf-8"))
    assert [entry["page"] for entry in manifest] == [1, 2]
    assert manifest[0]["figures"] == manifest[1]["figures"]


def test_figure_is_placed_after_the_text_above_it(manual: Path, tmp_path: Path) -> None:
    md = tmp_path / "out.md"
    pdf_manual.main(["index", str(manual), "--out", str(md),
                     "--figures-dir", str(tmp_path / "figs")])
    lines = [line for line in md.read_text(encoding="utf-8").splitlines() if line.strip()]
    first_text = next(i for i, line in enumerate(lines) if "alpha text" in line)
    first_figure = next(i for i, line in enumerate(lines) if line.startswith("!["))
    assert first_text < first_figure


def test_render_writes_page_png_at_the_requested_pages(manual: Path, tmp_path: Path) -> None:
    out = tmp_path / "pages"
    assert pdf_manual.main(["render", str(manual), "--pages", "2",
                            "--out", str(out)]) == 0
    written = sorted(out.glob("*.png"))
    assert len(written) == 1 and written[0].name.endswith("p02.png")
    assert written[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_page_range_parsing() -> None:
    assert pdf_manual.parse_pages("3", 10) == [2]
    assert pdf_manual.parse_pages("12-15", 20) == [11, 12, 13, 14]
    assert pdf_manual.parse_pages("1,4,9-11", 12) == [0, 3, 8, 9, 10]
    # out of range pages are dropped rather than crashing
    assert pdf_manual.parse_pages("0,99", 5) == []
    assert pdf_manual.parse_pages(None, 3) == [0, 1, 2]
