# OP-1 studio

A browser front end for a Teenage Engineering OP-1: the real CAD shell rendered in
three.js, keys you can click and drag, and a small local proxy that installs
patches, packs and tapes onto a mounted OP-1.

![op1-studio with an OP-1 mounted, the Restart bridge service button in the bottom bar](refs/screenshot.png)

The screenshot above is live: 1920x831, an OP-1 in DISK mode with eight synth
patches, eight drum kits, four tape tracks and both album sides listed, the
bottom bar reading "OP-1 mounted · 136 MB free".

## Quick start

```sh
PYTHONPATH=src python3 -m op1_studio.server --port 8770
# then open http://127.0.0.1:8770/
```

or

```sh
python3 scripts/serve.py --port 8770
```

The proxy is stdlib only (`ThreadingHTTPServer`), binds loopback, and serves the
client from `src/op1_studio/client/`. Static files go out with
`Cache-Control: no-store`, so a reload always picks up edited modules.

Pass `--mount /path/to/OP-1` to point at a device mount you already have, or
`--data-dir` to move the library root. Without a mount the UI still renders and
the panel works; only the install endpoints report no device.

## What works

- Panel geometry from `assets/op1.stl` (3.59 MB, parsed by the vendored
  `STLLoader`), cut into individually pickable parts.
- Raycast picking, encoder drag, key press, mode switching, tape mode, patch
  install from the browse panel.
- **Restart bridge service**: the button in the bottom stage bar POSTs
  `/api/restart`, and the proxy reloads the code now on disk in place
  (`execve`, same port, same PID, about half a second of downtime). No unit
  file or supervisor is needed. The client waits for `/api/health` to report a
  fresh uptime and then refreshes the device status.
- `window.op1studio` exposes `{ state, api, scene, camera, controls, renderer,
  op1, enterMode, restartBridge }` for scripting and comparison shots.

Routes: `/api/health`, `/api/restart`, `/api/status`, `/api/library`,
`/api/op1fun/patches`, `/api/op1fun/pack`, `/api/upload`,
`/api/op1fun/install`, `/api/pack/install`, `/api/install/local`,
`/api/tape/install`, `/api/eject`.

## Panel truth

Panel 282 x 102 x 32 mm, corner radius 7 mm, aspect 2.74 to 2.75. Positions,
radii and band layout are written down in `docs/HANDOFF_THREEJS.md` and encoded
in `src/op1_studio/client/src/layout.js`. `layout.js` is the source of truth; the
mesh code follows it.

## Tests

```sh
.venv/bin/python -m pytest -q     # 69 passed
```

`tests/test_pdf_manual.py` needs the `dev` extra (`pip install -e ".[dev]"`,
which pulls pymupdf); on a bare interpreter it skips instead of failing.

## Reference assets

- `refs/manual_pdf/54b7f9bf8681400300255cab_original.pdf` - the OP-1 operator's
  manual, **version 1.2** (Oct 2012, 49 pages). Note the version: the online
  guide at <https://teenage.engineering/guides/op-1/original> is v2.2 and is the
  source of truth for behaviour; this PDF covers hardware and older firmware.
- `refs/manual_pdf/54b7f9bf8681400300255cab_original.md` - the manual as text
  **and** pictures, read with `tools/pdf_manual.py index`. Every figure is
  extracted to `refs/manual_pdf/figures/` and linked back into the page text at
  the place it appears, so a paragraph about the display is followed by the
  display. Do not use `pdftotext` on this document: the manual's OP-1 display
  mock-ups and panel diagrams are vector line art, not bitmaps, and text
  extraction silently loses all of them.
- Page renders, for when the question is about what the screen looks like:
  `.venv/bin/python tools/pdf_manual.py render refs/manual_pdf/<manual>.pdf
  --pages 21 --dpi 200` writes a PNG you can open or hand to a vision model.
  Renders are git-ignored because they are big and regenerable in a second.
- `refs/3d_cad/teenage-engineering-op-1-2.snapshot.7/OP-1a.jpg` - the main
  reference photo for the project.
- `refs/print/white/` - three white-balanced panel plates (2048x2048) plus a
  contact sheet, produced by `tools/whiten_photos.py --lift 1.0 --knee 0.62`. The
  yellowed plastic tint is measured per plate and divided out in linear light, so
  the background stays pure white and the print keeps its hues. See
  `refs/print/white/README.md` for the method and the measured gains.

The CAD is Teenage Engineering's published OP-1 geometry. It is here as a local
reference; it is not covered by this project's licence, and the heavy neutral
formats (`.stp`, `.ipt`, `.iam`, `.zip`) are kept out of git for that reason.

## Known gaps

- Key and mode labels are not yet baked onto the panel faces. The scanned
  replacement caps and `tools/extract_labels.py` (glyph isolation on a 600 dpi
  flatbed scan) are in place, but the extraction is not clean enough to ship.
