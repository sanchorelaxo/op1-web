"""op1.fun scraping: pure parsing, no network.

Every HTML fixture here is a trimmed copy of the real page markup so the
regexes are tested against the shape the site actually serves.
"""
from __future__ import annotations

import pytest

from conftest import make_aiff
from op1_studio import op1fun

PATCHES_HTML = """
<div class="patch-card">
  <a href="/users/kristinamacias/patches/synth_-_guitar">Synth_Guitar</a>
</div>
<div class="patch-card">
  <a href="/users/honestbystander/patches/patch-drum-f0b6cc3f-6e1b-4c43-9ad8-5e2a5e974770">chill</a>
</div>
<a href="/patches?page=2">next</a>
"""

PATCH_HTML = """
<html><head><title>Synth_Guitar by kristinamacias</title>
<meta property="og:title" content="Synth_Guitar by kristinamacias">
<script>var a = "https://op1fun.s3.amazonaws.com/uploads/patch/17909/Synth_-_Guitar.aiff.mp3";</script>
</head><body></body></html>
"""

PACK_HTML = """
<html><head><meta property="og:title" content="Chill Drums Pack by honestbystander"></head>
<body>
  <a href="/users/honestbystander/patches/patch-drum-f0b6cc3f-6e1b-4c43-9ad8-5e2a5e974770">one</a>
  <a href="/users/honestbystander/patches/patch-drum-11111111-2222-3333-4444-555555555555">two</a>
  <a href="/users/honestbystander/packs/chill-drums-pack">self</a>
</body></html>
"""


def test_parse_patches_reads_hrefs_titles_and_pagination():
    patches = op1fun.parse_patches(PATCHES_HTML)
    assert [p.title for p in patches] == ["Synth_Guitar", "chill"]
    assert patches[0].url == "https://op1.fun/users/kristinamacias/patches/synth_-_guitar"
    assert patches[1].kind == "drum"
    assert patches[0].kind == "synth"
    assert patches[0].user == "kristinamacias"


def test_parse_patches_marks_packs():
    text = '<a href="/users/a/packs/my-pack">p</a>'
    assert op1fun.parse_patches(text)[0].kind == "pack"


def test_patch_type_from_slug():
    assert op1fun.patch_type("patch-drum-abc") == "drum"
    assert op1fun.patch_type("synth_-_guitar") == "synth"
    assert op1fun.patch_type("whatever") == "unknown"


def test_preview_url_regex_handles_both_aif_spellings():
    a = op1fun.preview_url('x = "https://op1fun.s3.amazonaws.com/uploads/patch/9/a.aif.mp3"')
    b = op1fun.preview_url('x = "https://op1fun.s3.amazonaws.com/uploads/patch/9/a.aiff.mp3"')
    assert a.endswith("/a.aiff")
    assert b.endswith("/a.aiff")


def test_preview_url_returns_none_when_absent():
    assert op1fun.preview_url("<html></html>") is None


def test_parse_pack_uses_og_title_and_member_links():
    title, urls = op1fun.parse_pack(PACK_HTML)
    assert title == "Chill Drums Pack"
    assert len(urls) == 2
    assert all("/patches/" in u for u in urls)


def test_read_aiff_metadata_reads_the_op1_appl_chunk(tmp_path):
    meta = {"type": "drum", "start": 0, "end": 42, "octave": 0}
    path = make_aiff(tmp_path / "Kit.aif", meta)
    assert op1fun.read_aiff_metadata(path) == meta


def test_read_aiff_metadata_rejects_non_aiff(tmp_path):
    bad = tmp_path / "bad.aif"
    bad.write_bytes(b"RIFX0000")
    with pytest.raises(op1fun.Op1FunError, match="not an AIFF"):
        op1fun.read_aiff_metadata(bad)


def test_read_aiff_metadata_returns_empty_without_an_appl_chunk(tmp_path):
    path = tmp_path / "plain.aif"
    path.write_bytes(b"FORM" + b"\x00\x00\x00\x04" + b"AIFF" + b"\x00" * 4)
    assert op1fun.read_aiff_metadata(path) == {}


def test_list_patches_uses_ransack_params(monkeypatch):
    seen = {}

    def fake_get(path, **kwargs):
        seen["path"] = path
        seen["params"] = kwargs.get("params")
        return PATCHES_HTML

    monkeypatch.setattr(op1fun, "_get", fake_get)
    result = op1fun.list_patches(page=2, query="guitar", kind="drum")
    assert seen["path"] == "/patches"
    assert seen["params"] == {
        "page": 2,
        "q[name_cont]": "guitar",
        "q[patch_type_cont]": "drum",
    }
    assert result["page"] == 2
    assert len(result["patches"]) == 2
