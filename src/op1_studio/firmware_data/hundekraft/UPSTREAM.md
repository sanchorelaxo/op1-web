# Vendored hundekraft workshop assets

Source: https://github.com/Hundekraft/op1-workshop
Commit: e33139b0514c516c3d87759da79c2e12fe195b6a (tag hq 3.3, release id 3.3.15)
Vendored: 2026-11-17
License: GPL-3.0-or-later with the explicit combination permission for OP-1
works (see LICENSE and NOTICE, copied unchanged).

## Contents

- `runtime.py`  kennel build engine, verbatim (pinned by release.json runtime_sha256)
- `release.json` catalog, variant hashes, product metadata, verbatim
- `base/`      patch base for Kennel 3.0 + plugins (manifest.json, files/, *.bin), verbatim
- `variants/200000000000007ff/` the all-on preset bundle (manifest.json, presets.zip, *.knl), verbatim

`op1_studio/hundekraft.py` mirrors the upstream `worker.js` flow
(fetch release.json, normalize the selection, patch against base + variant
bundles, filter the preset pack) so this copy and the upstream site produce
the same images. Only files listed above are vendored; the workshop repo's
web page, screens, cards, and manual are not.

## Re-sync

1. `git clone https://github.com/Hundekraft/op1-workshop && git checkout <release commit>`
2. Replace `runtime.py`, `release.json`, `base/`, `variants/<variantId>/` (delete old variant dirs first).
3. `python3 -c "import hashlib,pathlib; r=json.load(open('release.json')); print(hashlib.sha256(pathlib.Path('runtime.py').read_bytes()).hexdigest()==r['runtime_sha256'])"`
4. From the op1-web repo root: `OP1_TEST_SLOW=1 .venv/bin/pytest tests/test_hundekraft.py -q`
   (the slow test builds the real image and repacks/verifies CRCs; add `--deselect`
   nothing, it must pass).
5. If the release adds or renames variants, update this file and the vendored list.

## What is not used from upstream

`cores.json` (upstream core-patch path, out of scope), `catalog.json` at repo
root (release.json embeds the authoritative catalog the worker actually uses),
and all browser page assets.
