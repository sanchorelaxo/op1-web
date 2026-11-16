# Handoff to the `threejs` profile — finish the OP-1 model

Project: `/home/rjodouin/Documents/git/op1-web`
Client root: `/home/rjodouin/Documents/git/op1-web/src/op1_studio/client`
Owner of this card: profile **threejs**. Everything below is verified state as of
2026-11-15 evening, not a plan.

## The job in one sentence

The OP-1 in three.js already renders, clicks and installs patches; the *panel
geometry* is stale. Rebuild the keys, speaker, screen and encoders against the
reference photos so the model reads as the original OP-1, and prove it with a
side-by-side render.

## What is already working — do not rebuild it

- Proxy: `PYTHONPATH=src python3 -m op1_studio.server` on `http://127.0.0.1:8770/`
  (stdlib only, `ThreadingHTTPServer`, port 8770, loopback). Restart with
  `hermes`-independent shell: start it as a background task, never `&` in a
  foreground call.
- Static assets are served with `Cache-Control: no-store` (added in
  `server.py::end_headers`) — no stale-module reloads any more.
- `window.op1studio` exposes `{ state, api, scene, camera, controls, renderer, op1, enterMode }`.
  `camera` / `controls` / `renderer` are there for posing comparison shots.
- Raycast picking, encoder drag, key press, tape mode, mode switching, patch
  install from the browse panel: all exercised in the browser and working.
- `layout.js` was **rewritten this session** to the real hardware arrangement
  (see "Panel truth" below). Treat `layout.js` as the source of truth; the mesh
  code is what has not caught up.

## Panel truth (measured from the reference set, do not re-invent)

Panel 282 x 102 x 32 mm, corner radius 7. Aspect verified at 2.74–2.75 in the
photos, matching the spec.

```
top band     speaker grille (8,6,38x38) | volume (cx 58, cy 25, r 9)
             | display (86,7,54x40, bezel 2.6) | encoders cy 25 at cx 184/206/228/250, r 8.8
middle band  T1-T4 2x2 block at x 8/19, y 52/63
             engine modes SYNTH/DRUM/TAPE/MIXER under the display, x 86..141, y 52
             patches 1-8 two rows under the encoders, x 156..230, y 52/63
             M1/M2 at x 236, y 52/63
bottom left  SHIFT, COM, REC, PLAY, STOP (y 74) ; arrows, JOIN, up/down (y 85)
bottom right keyboard x 84, y 78, 190 x 20 ; 14 white + 10 black keys, black keys
             are PILL shaped (layout already flags `oval: true`)
```

Colour encoders, left to right: blue `0x4a7fd4`, green `0x4f9d63`, white
`0xe6e2d8`, orange `0xd97436`. Keyboard base note C3 (MIDI 48).

## Reference material (use it, this is the whole point)

- `refs/op-1_3d_photos/Screenshot_2026-11-15_19-16-52.png` and `19-13-09.png`:
  clean straight-on top views, device fills the frame, aspect 2.75. **Primary
  reference.**
- `refs/op-1_3d_photos/3d-lined/`: wireframe/line renders of the same object.
  Good for checking silhouettes and part extents, useless for material and legend
  colour.
- The rest of `refs/op-1_3d_photos/`: closer crops of the button block, the
  keyboard, the far-right edge. Useful for legend text and part shape.
- `refs/manual_pdf/`: the OP-1 user manual.
- `/home/rjodouin/.hermes/cache/scratch/measure_panel.py` — measures a photo and
  prints millimetre coordinates for display, encoders, speaker, keyboard pitch
  in the same space as `layout.js`. Re-run it instead of eyeballing numbers:
  `/usr/bin/python3 measure_panel.py refs/op-1_3d_photos/Screenshot_2026-11-15_19-16-52.png`
  (needs `/usr/bin/python3` for numpy/PIL; the default `python3` in the project
  venv does not have them.)

Constraint from the user, verbatim: the reference hardware is the **original
OP-1, never the OP-1 Field**.

## Exact defects left (this is the card)

1. **Keys are still `BoxGeometry`.** `model.js` keyboard loop builds boxes. Use
   the new `capGeometry(THREE, w, h, r, height, bevel)` helper: white keys
   `r = 1.4`, bevel `0.5`; black keys pill shaped, `r = min(w,h)/2`. Keep
   `register(mesh, ...)`, `keys` map, `baseY`, `setKeyDown`, `setKeyActive`
   behaviour intact — app.js depends on `part.baseY` for the press animation.
2. **Speaker** is still a 7x7 dotted box sitting proud of the plate.
   `SPEAKER` is now `cols: 11, rows: 11`; confirm the dot pitch against the
   photo, then recess it ~0.6 mm with a rim, not a raised tile.
3. **Screen** is a flat plane on a box. It needs: a dark recessed well, the
   emissive screen plane sunk ~0.6 mm below the plate surface, and a thin glass
   layer above it (`MeshPhysicalMaterial`, roughness 0.05, opacity ~0.1,
   transparent) so it stops looking like a sticker. Screen texture is
   `screen.js` (760x400 canvas, four modes) — do not touch it.
4. **Encoders** are straight cylinders. Taper them
   (`CylinderGeometry(r*0.9, r, ...)`) and give the cap a slight skirt so they
   read like the hardware. Wells already exist.
5. **Body** reads as a slab: `bevelSegments` 2 is coarse at this camera
   distance, and the top plate gap is too shallow. Smoother chamfer, and the
   side material needs to be visibly darker than the plate.
6. Optional if it lands cheaply: the `labelTexture`/`brand` helpers are now only
   used by the brand plane — either reuse `legendPlane` or delete the dead code.

## Verification protocol — no claims without this

1. Start the proxy as a background task, then `curl -s http://127.0.0.1:8770/api/health`
   must return `{"ok": true, "version": "0.1"}`.
2. Drive the real browser (browser harness, `local=True`), `goto_url("http://127.0.0.1:8770/")`,
   `wait_for_load`, wait ~2 s, then pose the camera through `window.op1studio`:
   ```js
   const A = window.op1studio;
   A.camera.position.set(-150, 170, 178);
   A.controls.target.set(0, -6, 4);
   A.controls.update();
   ```
   and screenshot. Three-quarter view *and* straight top view (`(0, 300, 1)`
   target `(0,0,0)`).
3. Produce `refs/compare/model_3q.png` (three-quarter) and
   `refs/compare/model_top.png` (top) and put the top render next to
   `refs/op-1_3d_photos/Screenshot_2026-11-15_19-16-52.png` in one montage
   (`convert +append` on equally-scaled crops) at `refs/compare/side_by_side.png`.
4. Write a real pytest test for the pure geometry: `tests/test_layout.py`
   asserting the invariants that can regress silently — 24 keys total (14 white,
   10 black), every key inside `KEYBOARD` bounds, no two buttons overlapping,
   every button inside the 282x102 panel. `tests/conftest.py` already exists with
   `make_aiff`; pytest is installed in `.venv` (`/home/rjodouin/Documents/git/op1-web/.venv/bin/python -m pytest`).
5. Report with the screenshot paths, the montage, and the test output. A
   description of the model is not acceptance; the render is.

## House rules for this repo

- Client is dependency-free ES modules, no build step, `three` vendored in
  `client/vendor/`. Do not add npm packages.
- Panel geometry lives in `layout.js` only. Mesh code reads it; it never
  hardcodes a coordinate.
- Do not touch the proxy's device-writing paths, the S3 scraping, or anything
  under `refs/`. Read-only for references.
- No em dash / en dash in prose or comments; ASCII in filenames.
