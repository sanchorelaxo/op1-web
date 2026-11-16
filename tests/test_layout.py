"""Panel geometry checks, read straight out of the client's layout.js.

layout.js is the single source of truth for panel coordinates, so the test does
not restate the numbers. It imports the module with node and asserts the
properties that must hold whatever the numbers become.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

LAYOUT = Path(__file__).resolve().parents[1] / "src" / "op1_studio" / "client" / "src" / "layout.js"

PROBE = """
import { PANEL, DISPLAY, ENCODERS, VOLUME, BUTTONS, KEYS, KEYBOARD } from %s;
console.log(JSON.stringify({ PANEL, DISPLAY, ENCODERS, VOLUME, BUTTONS, KEYS, KEYBOARD }));
"""


@pytest.fixture(scope="module")
def layout() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed, cannot import layout.js")
    script = PROBE % json.dumps(LAYOUT.as_uri())
    out = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if out.returncode != 0:
        pytest.fail(f"node could not import layout.js: {out.stderr.strip()}")
    return json.loads(out.stdout)


def test_panel_is_the_original_op1_size(layout):
    """The original OP-1 is 282 mm wide; the CAD shell measures 285.5."""
    assert 280 <= layout["PANEL"]["w"] <= 286
    assert 100 <= layout["PANEL"]["h"] <= 103


def test_keybed_holds_every_key(layout):
    kb = layout["KEYBOARD"]
    keys = layout["KEYS"]
    assert len(keys) == kb["count"], "one entry per key in the CAD keybed"
    for key in keys:
        assert kb["x"] <= key["x"], f"{key['id']} starts left of the keybed"
        assert key["x"] + key["w"] <= kb["x"] + kb["pitch"] * (kb["count"] - 1) + kb["keyW"] + 0.01
        assert kb["y"] <= key["y"]
        assert key["y"] + key["h"] <= layout["PANEL"]["h"] + 30, f"{key['id']} runs off the panel"


def test_keys_do_not_overlap(layout):
    keys = sorted(layout["KEYS"], key=lambda k: k["x"])
    for left, right in zip(keys, keys[1:]):
        assert left["x"] + left["w"] <= right["x"] + 0.001, (
            f"{left['id']} overlaps {right['id']}"
        )


def test_every_button_is_inside_the_panel_and_unique(layout):
    panel, display = layout["PANEL"], layout["DISPLAY"]
    buttons = layout["BUTTONS"]
    ids = [b["id"] for b in buttons]
    assert len(ids) == len(set(ids)), "button ids must be unique"

    for b in buttons:
        assert 0 <= b["x"] and b["x"] + b["w"] <= panel["w"], f"{b['id']} leaves the panel in x"
        assert 0 <= b["y"] and b["y"] + b["h"] <= panel["h"], f"{b['id']} leaves the panel in y"
        overlaps_display = (
            b["x"] < display["x"] + display["w"]
            and b["x"] + b["w"] > display["x"]
            and b["y"] < display["y"] + display["h"]
            and b["y"] + b["h"] > display["y"]
        )
        assert not overlaps_display, f"{b['id']} sits on the display well"


def test_no_two_buttons_overlap(layout):
    buttons = sorted(layout["BUTTONS"], key=lambda b: (b["y"], b["x"]))
    for i, a in enumerate(buttons):
        for b in buttons[i + 1 :]:
            if b["y"] >= a["y"] + a["h"]:
                break
            separated = b["x"] >= a["x"] + a["w"] or a["x"] >= b["x"] + b["w"]
            assert separated, f"{a['id']} overlaps {b['id']}"


def test_encoders_sit_in_a_row_right_of_the_screen(layout):
    display = layout["DISPLAY"]
    encoders = layout["ENCODERS"]
    assert len(encoders) == 4, "the original OP-1 has four colour encoders"
    assert [e["id"] for e in encoders] == [
        "enc-blue",
        "enc-green",
        "enc-white",
        "enc-orange",
    ]
    for e in encoders:
        assert e["cx"] - e["r"] > display["x"] + display["w"] - 1, f"{e['id']} is not right of the screen"
        assert e["cy"] >= 0 and e["cy"] <= layout["PANEL"]["h"]
    xs = [e["cx"] for e in encoders]
    assert xs == sorted(xs), "encoders run left to right blue, green, white, orange"


def test_volume_knob_is_top_left(layout):
    vol = layout["VOLUME"]
    assert vol["cx"] < layout["DISPLAY"]["x"]
    assert vol["cy"] < layout["DISPLAY"]["y"] + layout["DISPLAY"]["h"]
