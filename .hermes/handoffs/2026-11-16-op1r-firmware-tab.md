# Handoff — op1r web functionality as an op1-web firmware tab

Date: 2026-11-16. Stopped by user at "instead write a handoff file. i'll do this later".
Repo: /home/rjodouin/Documents/git/op1-web. Nothing committed yet: everything below is an
uncommitted working tree. Two unrelated untracked paths (refs/monochrome_simplified.png,
refs/sample_drum_wav_upload/) predate this work; leave them alone.

## Original request (verbatim)

> incorporate all the functionality at https://pcppcp.github.io/op1r/web/ into the op1-web
> project but add as a tab in the pane. the drag-n-drop has to be adapted to our impl (i dont
> want 2 frameworks for this). make sure the adaptation (checkbox selection ui, etc) works
> with the original firmware too. i want a select elem that is populated by the filenames in a
> dir where all firmware installer files are (just list the file names) which allows me to
> flash any firmware (any new firmware dragged & dropped should also be saved in this dir).
> finally. be extra descriptive w variable names so i can read the code (i dont want
> comments, just descriptive var names)

## Reference sources (all read in full, this is the feature inventory)

- /home/rjodouin/Documents/git/op1r/web/index.html — op1r web UI: plugin checkbox table
  (kind icons sy/sb/ph/en/fx, sizes, descriptions), dropzone (.op1 = firmware base, .op1p =
  custom plugin), build button, Web MIDI devupd panel (Connect / Update firmware+synths /
  Update synths only / force checkbox), changelog dialog ("what's new" since current build),
  crash paste box.
- /home/rjodouin/Documents/git/op1r/web/devupd.js — Web MIDI sysEx protocol for the patched
  firmware. Vendor this near-verbatim (it documents the wire format).
- /home/rjodouin/Documents/git/op1r/web/worker.js — JS port of the patcher. NOT needed: we
  use the Python patcher server-side instead of a second implementation.
- /home/rjodouin/Documents/git/op1r/web/crash.js + crash.html — crash-log decoder using
  symbols/op1r-<ver>.json maps (load addr 0x00810000, +0x200000 for the .ldr image).
- /home/rjodouin/Documents/git/op1r/patch_op1.py — the authoritative Python patcher (240 lines).

## Device facts (verified this session)

- OP-1 now runs op1r 71: USB 2367:0004, 6-ch S24_3LE/44100 capture fingerprint.
- Installer dir in use: /home/rjodouin/Documents/git/op1-firmware containing op1_246.op1
  (stock R246, sha c5315218f825..., matches manifest original) and op1r-71.op1 (old manual
  build, sha 720a4886..., a recompressed variant).
- te-boot: power off → hold COM → power on → ~15 MB vfat volume appears (seen earlier at
  /media/rjodouin/0021-0000). Menu keys: 1 update, 2 test, 7 factory reset, 8 format
  (7/8 erase — the UI must warn).
- Web MIDI needs a secure context: http://127.0.0.1:8770 qualifies; a Tailscale-IP URL
  (100.x.y.z) does not. Build/flash/changelog/crash features keep working remotely; the MIDI
  devupd sub-panel will only appear on localhost. Note this in README.

## DONE (working tree, verified)

1. `src/op1_studio/firmware_data/` (3.2 MB, 68 files) — vendored verbatim from
   /home/rjodouin/Documents/git/op1r/data plus CHANGELOG.md and NOTICE copied in. Contains
   manifest.json, plugins/ (53 .op1p), symbols/ (69, 70, 71, r246), files/ + *.insert.bin
   blobs.
2. `src/op1_studio/firmware_patcher.py` — copy of patch_op1.py, exactly two deltas (keep it
   diffable against upstream): `DATA = .../firmware_data`, and `fail()` now raises
   `FirmwarePatchError(RuntimeError)` instead of sys.exit.
3. `src/op1_studio/config.py` — added `firmware_dir` dataclass field via
   `_default_installer_dir()`: env `OP1_FIRMWARE_DIR` → else
   `~/Documents/git/op1-firmware` when it exists → else `data_dir/firmware`.
   `ensure_dirs()` now mkdirs it. Resolves correctly today (verified).
4. `src/op1_studio/firmware.py` (441 lines, imports and behaves — verified):
   - `catalog()` — manifest.json, cached by mtime.
   - `safe_installer_name()` — strict `*.op1` basename regex, blocks traversal.
   - `list_installers(config)` — dir scan, .op1 only, sorted by lowercase name,
     [{name, bytes, modified_at, sha256}] with (size, mtime_ns)-keyed sha cache.
   - `installer_path()`, `save_installer(config, name, data)` (atomic .part replace, 25 MB cap),
     `delete_installer()`.
   - `build_output_name(ident)` — "op1r 71" → "op1r71.op1" (no clash with existing op1r-71.op1).
   - `build_custom_image(config, source_name, plugin_names, overwrite=False)` — runs
     firmware_patcher.patch(source_bytes, manifest, log, plugins); writes output atomically
     (timestamp-suffixed name if it exists and not overwrite); returns
     {name, bytes, sha256, reference_sha256, exact_reference_build,
     recompressed_reference_build, plugins, seconds, log[]}. Source must be stock R246
     (checksum enforced by the patcher).
   - `cached_image_members(config, name)` — unpacks LZMA tar, in-process cache keyed by
     path|size|mtime_ns, one image at a time (verified: op1_246.op1 → 107 members in 0.5 s).
     Stock image has NO plugins/ members; patched images do.
   - `image_member_listing(config, name)` / `image_member_bytes(config, name, member_path)` —
     for the devupd "update firmware + synths" flow; member path regex-guarded.
   - `plugin_bytes(file)` — bundled .op1p from firmware_data/plugins, sha-verified against
     manifest.
   - `symbol_map_versions()` / `symbol_map(version)` — only op1r-N or r246 accepted.
   - `changelog_text()`.
   - `is_teboot_volume(path)` — total < 100 MB (te-boot update disk; OP-1 disk-mode is 4 GB),
     writable, no synth/drum/tape/album dirs, entries empty-or-.op1. `find_teboot_mount()`
     scans device.SEARCH_ROOTS (one level deeper). `teboot_state()` returns
     {present, path, free_bytes, help}. Verified present:False with no device in te-boot.
   - `flash_installer(config, name, mount=None)` — copy → fsync → read-back sha compare →
     udisksctl unmount (failure tolerated, guidance string adapts). Returns
     {flashed, installer, bytes, sha256, mount, unmounted, unmount_note, guidance}.
     Test path: pass explicit `mount=` + monkeypatch `_resolve_block_device`/`_unmount_volume`.
   - `firmware_state(config)` — one-shot blob for the UI: {installer_dir, installers,
     catalog{title, ident, original, reference_sha256, reference_bytes, plugins[]},
     teboot, symbol_map_versions}.

## NOT DONE (the actual remaining work, in order)

### A. server.py — import + routes + error mapping
- `from . import firmware` alongside the other service imports.
- The do_GET/do_POST dispatch blocks catch `(ValueError, device.DeviceError)`; extend both
  tuples with `firmware.FirmwareError, firmware_patcher.FirmwarePatchError` (map to 400 on
  GET, 500 on POST like today).
- Handler already has `_json`, `_error`, `_body`, and `_send_audio`; add a sibling
  `_send_bytes(data, content_type, download_name=None)` (octet-stream +
  Content-Disposition attachment for firmware files).
- GET routes (add to the elif chain in do_GET):
  - `/api/firmware` → `firmware.firmware_state(self.config)`
  - `/api/firmware/changelog` → `{"text": firmware.changelog_text()}`
  - `/api/firmware/symbols?version=71` → `firmware.symbol_map(v)`
  - `/api/firmware/plugin?file=aether.op1p` → bytes from `plugin_bytes`
  - `/api/firmware/members?name=X` → `image_member_listing`
  - `/api/firmware/member?name=X&path=P` → bytes from `image_member_bytes`
  - `/api/firmware/download?name=X` → bytes + attachment header
- POST routes (do_POST, all bodies via `_json_body()` except installer/custom plugin):
  - `/api/firmware/installer` — raw bytes body, `?name=` query → `save_installer`
  - `/api/firmware/installer/delete {"name"}` → `delete_installer`
  - `/api/firmware/custom-plugin` — raw bytes body, `?name=` → save into
    `config.firmware_dir / "custom_plugins"` (only `.op1p` basenames) — this is the server-side
    equivalent of op1r's in-memory dropped custom plugins; build reads their bytes back
  - `/api/firmware/build {"installer", "plugins": [names], "custom_plugins": [names], "overwrite"}`
    → `build_custom_image`; for each custom name load bytes from custom_plugins dir and merge
    into the plugins dict via `firmware_patcher.parse_op1p(name, data)` (same shape manifest
    plugins use: {name, file, data, desc, kind}). DECISION: custom .op1p files are saved to
    the installer dir sibling folder, not re-uploaded per build.
  - `/api/firmware/flash {"installer"}` → `flash_installer`
- StudioApp: thin methods per route (match existing style: `def status(self)`, `def browse(...)`),
  each returning plain dicts.
- ThreadingHTTPServer confirmed → a 40–60 s build blocks only its own connection; browser
  fetch has no timeout by default; keep UI busy while awaiting.

### B. client/src/api.js — one function per endpoint
firmwareState(), firmwareChangelog(), firmwareSymbolsUrl/param, firmwarePluginUrl(name),
firmwareMembers(name), firmwareMemberUrl(name, path), firmwareDownloadUrl(name),
postFirmwareInstaller(name, bytesArrayBuffer), postFirmwareCustomPlugin(name, bytes),
postFirmwareBuild(...), postFirmwareFlash(name), postFirmwareDelete(name).
Match the existing fetch + `{error}` throw style in api.js (2.5 KB module).

### C. client files
- `client/index.html` — add `<button class="tab" data-tab="firmware">firmware</button>` next
  to the existing tape/device tabs, and a `<section class="pane" data-pane="firmware" hidden>`
  containing a static skeleton (ids for: installer select, installer meta line, te-boot status,
  flash/download/delete buttons, build source select, plugin checkbox table container, select
  all/none, build button, build log, drop hint, MIDI panel with connect/status/two update
  buttons/force checkbox, changelog button + `<dialog>`, crash textarea + decode button +
  symbols version select + result table). Static HTML was chosen over createElement soup for
  readability.
- `client/src/devupd.js` — vendor op1r's devupd.js (the file was read in full this session;
  keep its structure: sysEx framing 0xF0 0x43 0x77 0x09 + 7 ASCII chars, 100 ms command
  spacing, pluginInfo() header parse, STAT/FADD/FWR/FCRC/FSET/FSAVE push sequence with 4096
  byte chunks, find() handshake). Adaptation: ES module export, replace its internal log()
  with an injected log function.
- `client/src/firmware.js` — new module, the pane logic. NO comments, descriptive names only.
  Init via dependency injection from app.js: `initFirmwarePane({setMessage, setBusy, fmtBytes})`
  plus `onFirmwareTabSelected()` (refreshes state when tab clicked) and
  `tryHandleFirmwareDrop(files)` (returns true if it took the files). Sections to implement:
  1. Installer library: populate `<select>` with option text = filename ONLY (user said
     "just list the file names"); bytes/sha/modified shown beside the select, not in options.
     Flash → POST flash → print guidance (incl. the 7/8-erase warning). Download → the
     download URL. Delete with confirm.
  2. Build: source installer select, plugin checkbox table from state.catalog.plugins
     (identical UI to op1r: kind badge, name, size, description, checkbox default all-on),
     custom dropped .op1p rows appended with a remove button, Build button → busy → render
     returned log[] line by line → on result, refresh installers and preselect the new build;
     show badge exact-reference / recompressed / custom-verified (from the result flags).
  3. MIDI devupd: Connect (navigator.requestMIDIAccess), device find(), installed synth/FX
     list, checkbox state sync from device (tick = present on device, per op1r), "update
     firmware + synths and effects" fetches build members via /api/firmware/member and pushes
     (member path allowlist = same rules devupd.js already enforces), "update synths and
     effects" pushes selected .op1p files via /api/firmware/plugin, force checkbox.
     find() timeout/null → message: "OP-1 does not answer the op1r update protocol — it is
     running the original firmware; use build + te-boot flash instead". KEY POINT for the
     "works with original firmware" requirement: selection UI + build + flash are 100%
     server-side and never require the patched protocol; only the MIDI sub-panel does.
  4. Changelog dialog: parse CHANGELOG.md `## <version>` sections (op1r index.html has the
     parsing snippet to mirror), show releases newer than current ident when a device is
     connected, else the newest few.
  5. Crash decoder: port crash.js logic (regex `pc 0x... lr 0x...`, load base 0x00810000,
     +0x200000 for ldr), symbol map fetched from /api/firmware/symbols?version=, fallback to
     a local file input for custom maps, render nearest-function table + a prefilled GitHub
     issue link.
- `client/src/app.js` (30 KB module, already inspected):
  - Import { initFirmwarePane, onFirmwareTabSelected, tryHandleFirmwareDrop } from
    './firmware.js'.
  - Tab handler: the existing loop toggles `data-pane` sections on data-tab click and
    special-cases the tape tab (loadTapes()); add the same hook calling
    onFirmwareTabSelected().
  - Global drag-drop: wireDrumDrop's drop handler currently takes audio files and rejects the
    rest; call `tryHandleFirmwareDrop(files)` FIRST and return when it takes them. That is the
    "no second framework" adaptation: same single drop listener, routed by file extension
    (.op1/.op1p → firmware pane, audio → device pane as today).
  - Call initFirmwarePane(...) after setMessage/setBusy/fmtBytes exist (module top-level,
    after their definitions).
- `client/style.css` — reuse existing .pane/.btn/.field/.table/.dropzone classes; add a few
  firmware-specific rules only if needed (checkbox table density, log pre block).

### D. tests (user enforces strict TDD — write these alongside/first when resuming)
New `tests/test_firmware.py`, style of tests/test_device.py (tmp_path fixtures, conftest
`config` fixture already provides a StudioConfig rooted in tmp; override its firmware_dir):
- list_installers: only .op1 listed, sorted, sha/bytes correct.
- safe_installer_name: accepts spaces/parens; rejects ../, non-.op1, absolute, empty.
- save_installer: roundtrip + atomic rename; rejects empty/oversized; no partial files left.
- delete_installer: refuses zero-byte.
- plugin_bytes: unknown file rejected; bundled tamper detected (corrupt a tmp copy —
  patch firmware.PLUGINS_DIR or test via a tmp manifest path).
- image_member_listing/bytes against the REAL op1_246.op1 (107 members verified; needs
  OP1_FIRMWARE_DIR pointing at ~/Documents/git/op1-firmware or an env-gated fixture);
  traversal path rejected.
- is_teboot_volume: monkeypatch firmware._volume_total_bytes (tmp dir → small total + no OP1
  dirs = True; synth dir present = False; >100 MB = False).
- flash_installer: explicit mount=tmp te-boot dir, patch _resolve_block_device/_unmount_volume
  → copied + read-back sha + guidance; disk-mode volume guard (mkdir synth/) rejected.
- build_output_name: "op1r 71" → "op1r71.op1".
- END-TO-END build against real firmware_data (~40–60 s): build from op1_246 with all 53
  plugins → expect exact_reference_build == manifest sha 0f743269332e... — gate behind
  env OP1_TEST_SLOW=1 or a `slow` marker; MUST be run once on resume and the real outcome
  recorded here.
- README examples mirrored into tests (user convention) after README section is written.

### E. docs + commit + skill
- README.md: firmware tab section, installer dir + OP1_FIRMWARE_DIR, endpoint table additions,
  localhost-only MIDI note, te-boot procedure (hold COM at power-on, press 1 then COM, 7/8 erase).
- Commit: firmware_data + patcher + firmware.py + config.py are one logical commit ("vendor
  op1r patcher + firmware service"); server/client/tests another; keep trees bisectable.
- Update the op1-web skill (skill_manage patch) with the new architecture + the two gotchas
  below; the op-1 skill already documents te-boot/op1r (commit 4c571a5 in
  hermes-music-sampling — do not redo).

## Pitfalls learned this session (do not rediscover)

1. tarfile member readback: `extractfile()` returns None for dirs — firmware.py already
   filters `info.isfile() and data is not None`.
2. The patcher refuses any base image whose sha246 != R246 original.sha256: dropped builds
   that are ALREADY op1r will error "not the supported original" — surface that error text in
   the UI as "pick the stock installer as source".
3. Stock op1_246.op1 has NO plugins/ members (verified) — devupd's "update firmware +
   synths" must pull members from a BUILT op1r image, not the stock one.
4. FAT volumes on te-boot show 15 MB; the 4 GB disk-mode volume passes an `lsblk` scan too —
   the 100 MB size guard is what distinguishes them; keep it.
5. `Path.stem.removeprefix` needs py>=3.9 (runtime is 3.12 — fine, noted for portability).
6. handler error tuple: if you forget FirmwarePatchError, a bad build dumps a traceback and a
   bare 500 (the existing outer except prints + send_error(500)).
7. Select option text: filename ONLY (explicit user requirement).
8. No comments in new code (explicit user requirement); vendored devupd.js/crash port may keep
   the upstream protocol comments for diffability — call that out in the commit message.
9. ThreadingHTTPServer is per-thread; all module-level caches in firmware.py are lock-guarded
   or single-key atomic — keep any new cache the same way.
10. Existing device.py owns SEARCH_ROOTS (/media, /run/media) — reused by find_teboot_mount,
    don't duplicate the constant.

## Resume checklist (copy-paste next session)

1. Read this file. `git status` in op1-web — expect: M config.py, ?? firmware.py,
   ?? firmware_patcher.py, ?? firmware_data/ (all DONE above).
2. Implement section A (server.py), B (api.js), C (client), D (tests), E (docs/commit/skill).
3. `PYTHONPATH=src python3 -m pytest tests -q` (96 passed baseline was green; new tests must
   run green, slow build test run once with OP1_TEST_SLOW=1 and record the sha result).
4. Live check: start server (tools/run.sh or `PYTHONPATH=src python3 scripts/serve.py`),
   `curl -s localhost:8770/api/firmware | head -c 400`; POST a copy of op1_246.op1 as a new
   installer name via /api/firmware/installer and confirm it lands in the firmware dir.
5. Browser pass is the user's: firmware tab visible, checkbox build flow, drop routing.

## PROGRESS 2026-11-16 (late) — indicators + auto-mount DONE on feat/op1r-firmware-tab

Commits: 379bf13 (WIP baseline), 434bd35 (routes+auto-mount+status modes),
41786da (client pills). 151 tests green via `.venv/bin/pytest tests/ -q`
(system python3 has no pytest — use .venv).

- Section A server routes: DONE, all GET/POST /api/firmware* per plan; GET
  maps FirmwareError→400, /api/firmware/file 404s missing installers itself.
- Auto-mount: DONE + LIVE-VERIFIED on the real OP-1 in firmware mode
  (model "OP-1 Disk", /dev/sda, 15728640 B, label 0021-0000). One caveat:
  a udisisksctl mount issued within ~1 s of a manual unmount can fail EBUSY;
  the 10 s throttle + client 4 s poll self-heals, do not retry faster.
- /api/status now carries firmware{} + modes{disk,firmware,midi:null}.
- Client stage bar: three pills (disk/firmware/midi) w/ green/red dots,
  titles show path/free/auto-mounted/help; device-status pill only shows
  for proxy errors/restart; 4 s setInterval(refreshStatus). MIDI pill is
  data-state unknown (dim) until midi detection lands.
- Still open from plan: section B (api.js firmware helpers — client currently
  has no firmware-tab UI beyond the pills), the firmware tab itself (vendor
  devupd.js + crash.js per sections A/C), installer build UI, flash flow wiring.
- User's long-running instance on :8765 predates this code — its restart
  button (or relaunch) picks up server.py; client files serve from repo dir.
