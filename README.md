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
- **Drop a sound on the Device pane** and it becomes a drum kit in
  `drum/user/`. Any format the proxy can decode goes in (wav, mp3, m4a, flac,
  ogg, aif…); it is downmixed to mono 44.1 kHz, refused above 12 seconds, and
  written as an AIFC the OP-1 lists. The pane refreshes itself, so the new kit
  is one click away from playing.
- **x beside any device entry deletes that file** from the OP-1, after a
  confirm. The path must resolve inside the mounted volume; directories and
  `..` are refused.
- `window.op1studio` exposes `{ state, api, scene, camera, controls, renderer,
  op1, enterMode, restartBridge, clip, toggleClip, deleteEntry }` for scripting
  and comparison shots.

Routes: `/api/health`, `/api/restart`, `/api/status`, `/api/audio`,
`/api/library`, `/api/op1fun/patches`, `/api/op1fun/pack`, `/api/upload`,
`/api/drum/upload`, `/api/device/delete`, `/api/op1fun/install`,
`/api/pack/install`, `/api/install/local`, `/api/tape/install`, `/api/eject`.

## Making a drum kit

The teenage engineering guide asks for one sound file, twelve seconds at most,
saved as `.aif` into the user folder inside the drum folder. Dropping a file on
the Device pane does that: `POST /api/drum/upload?name=<file>` decodes it,
takes it to mono 44.1 kHz, and writes `drum/user/<name>.aif`.

The container is AIFC (`FORM`/`AIFF`, `FVER`, `COMM` with `sowt`, `APPL`,
`SSND`) and the kit's 24 pads all start at 0 and end at the file's end, so any
key plays the whole sound. Slice values are fractions of a 24 second timeline,
not frame counts: twelve seconds is exactly `2**31`. A kit built here parses
with the same `COMM`/`APPL` shape the OP-1's own `bronze` kit uses.

## Playing sounds

Click a clip under the Device tab and it plays in the browser. Chromium has no
AIFF decoder, so the proxy converts on the way out:
`GET /api/audio?rel=drum/user/1.aif` returns WAV, converted in pure Python from
the file's own `COMM` chunk (rate and channels preserved, 8/24/32-bit narrowed
to 16-bit, compressed AIFC refused). One WAV per source `(path, size, mtime)` is
cached in the data dir; nothing is written to the OP-1. Range requests are
honoured, so a 30 MB tape track starts playing before it finishes converting.

The payload is byte-identical to `ffmpeg`'s decode of the same `.aif`, so what
you hear is the file, not the proxy.

Two things worth knowing before you judge a clip by ear:

- **A drum kit is a container, not a one-shot.** Up to 24 slices sit end to end
  with a pad map in `APPL`. The button plays the whole file, so a 1.31 s kit is
  every pad crammed into one burst. The OP-1 plays one pad per key; slice
  playback is not implemented yet.
- **Kits differ in rate and in what they contain.** Tape and synth files are
  44.1 kHz; the three 58 kB drum kits are 22.05 kHz `dbox` engines whose audio
  really is a robotic voice, and the three of them are the same audio saved
  under different names.

## Panel truth

Panel 282 x 102 x 32 mm, corner radius 7 mm, aspect 2.74 to 2.75. Positions,
radii and band layout are written down in `docs/HANDOFF_THREEJS.md` and encoded
in `src/op1_studio/client/src/layout.js`. `layout.js` is the source of truth; the
mesh code follows it.

## Tests

```sh
.venv/bin/python -m pytest -q     # 101 passed
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
