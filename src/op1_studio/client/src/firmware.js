/**
 * Firmware-mode workspace for the device pane.
 *
 * When the OP-1 sits in te-boot (firmware) mode the drive is gone, so this
 * replaces the file listing with the job the upstream web installers do —
 * pick a firmware file, see what is inside it, build a personalised image,
 * flash it — driven by our own /api/firmware endpoints, so the picker at the
 * top is the local firmware library rather than a browser file dialog.
 *
 * What the view shows depends on the selected file:
 *   stock original    -> op1r plugin table + hundekraft 3.3 content builder
 *   op1r build        -> plugins inside it (toggle to rebuild), flash
 *   hundekraft build  -> build info, preset pack, flash
 *   anything else     -> member listing, flash, download
 */
import { api } from './api.js';

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function fmtBytes(n) {
  if (!n) return '0 B';
  const units = ['B', 'kB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 ? 1 : 0)} ${units[i]}`;
}

const shortSha = (sha) => (sha ? `${sha.slice(0, 8)}…` : '—');

// the builder names outputs hundekraft33<configid>.op1 (lowercased slug)
const HK_NAME_RE = /^hundekraft33([0-9a-f]{2,40})\.op1$/i;

// virtual picker entry: the builder is a job, not a file in the library
const HK_BUILDER = '__hundekraft_builder__';

/** Classify a library row: hashes first, names second. */
function classify(row, catalog, hk) {
  const sha = row.sha256 || '';
  const name = (row.name || '').toLowerCase();
  if (catalog && catalog.reference_sha256 && sha === catalog.reference_sha256) return 'op1r';
  if (HK_NAME_RE.test(name) || name.startsWith('hundekraft')) return 'hundekraft';
  if (catalog && catalog.original && sha === catalog.original.sha256) return 'original';
  if (name.includes('op1r')) return 'op1r';
  return 'unknown';
}

export function createFirmwareWorkspace(container) {
  let fwState = null;
  let hk = null;
  let active = '';
  let pluginsSel = null; // Set of catalog plugin names for the op1r builder
  let hkSel = null; // {synths:Set, effects:Set, utilities:Set, cap}
  let members = null; // member listing of the selected image
  let note = '';
  let busy = false;
  let building = '';

  const hkAvailable = () => Boolean(hk && (hk.images || []).some((i) => i.buildable));

  function initHkSel() {
    if (!hkAvailable() || hkSel) return;
    hkSel = {
      synths: new Set(hk.synths.map((i) => i.id)),
      effects: new Set(hk.effects.map((i) => i.id)),
      utilities: new Set((hk.utilities || []).map((i) => i.id)),
      cap: hk.default_config ? hk.default_config.preset_cap : hk.preset_caps[hk.preset_caps.length - 1],
    };
  }

  /** Recompute per-file selections when the active file changes. */
  function resetForActive() {
    members = null;
    building = '';
    if (!fwState) return;
    if (active === HK_BUILDER) {
      initHkSel();
      render();
      return;
    }
    const row = fwState.installers.find((r) => r.name === active) || {};
    const kind = classify(row, fwState.catalog, hk);
    if (kind === 'original' && !pluginsSel) {
      pluginsSel = new Set();
      initHkSel();
    }
    if (kind === 'hundekraft') initHkSel();
    if (active) loadMembers();
  }

  async function loadMembers() {
    if (!active || !fwState) return;
    let list = null;
    try {
      const res = await api.firmwareMembers(active);
      list = res.members || [];
    } catch {
      list = null;
    }
    members = list;
    const row = fwState.installers.find((r) => r.name === active) || {};
    if (list && classify(row, fwState.catalog, hk) === 'op1r' && fwState.catalog) {
      const files = new Set(list.filter((m) => m.path.startsWith('plugins/')).map((m) => m.path.split('/').pop()));
      pluginsSel = new Set(fwState.catalog.plugins.map((p) => p.name).filter((n) => files.has(`${n}.op1p`) || files.has(n)));
    }
    render();
  }

  async function refresh({ select } = {}) {
    const [state, summary] = await Promise.all([api.firmwareState(), api.firmwareHundekraft()]);
    fwState = state;
    hk = summary;
    const names = state.installers.map((r) => r.name);
    const previousOriginal = active;
    if (select && names.includes(select)) active = select;
    else if (active !== HK_BUILDER && !names.includes(active)) {
      const installed = state.installers.find((r) => r.installed);
      active = installed ? installed.name : names[0] || '';
    }
    if (active !== previousOriginal || !hkSel) pluginsSel = null;
    resetForActive();
  }

  // ---- actions -----------------------------------------------------------
  async function buildOp1r() {
    if (busy || !active) return;
    busy = true;
    building = 'op1r';
    note = 'building op1r image (can take a minute)…';
    render();
    try {
      const plugins = [...(pluginsSel || [])];
      // the patcher always works from the stock original; rebuilding an
      // existing op1r image means re-adding the kept plugins to R246
      const row = fwState.installers.find((r) => r.name === active) || {};
      const source = classify(row, fwState.catalog, hk) === 'original'
        ? active
        : (fwState.catalog && fwState.catalog.original && fwState.catalog.original.name) || active;
      const res = await api.firmwareBuild(source, plugins);
      note = `built ${res.name} from ${source}: ${fmtBytes(res.bytes)}, ${(res.plugins || []).length} plugins, ${res.seconds}s`;
      await refresh({ select: res.name });
    } catch (err) {
      note = `build failed: ${err.message}`;
    } finally {
      busy = false;
      building = '';
      render();
    }
  }

  async function buildHundekraft() {
    if (busy || !hkAvailable() || !hkSel) return;
    busy = true;
    building = 'hundekraft';
    note = 'building hundekraft 3.3 (about ten seconds)…';
    render();
    try {
      // the runtime accepts exactly {schema, base, synths, effects, preset_cap}
      // plus optional {utilities, hidden, lander}; schema/base come from the
      // server's default_config, everything else is the live selection
      const def = hk.default_config || { schema: 1, base: hk.base };
      const config = {
        schema: def.schema,
        base: hk.base || def.base,
        synths: [...hkSel.synths],
        effects: [...hkSel.effects],
        utilities: [...hkSel.utilities],
        preset_cap: hkSel.cap,
      };
      // the backend defaults the base to the stock original; passing a
      // library file only makes sense when one is really selected
      const source = active === HK_BUILDER ? undefined : active;
      const res = await api.hundekraftBuild(config, source);
      note = `${res.reused ? 'reused' : 'built'} ${res.name}: ${fmtBytes(res.bytes)} in ${res.seconds}s, pack ${fmtBytes(res.presets_bytes)}`;
      await refresh({ select: res.name });
    } catch (err) {
      note = `hundekraft build failed: ${err.message}`;
    } finally {
      busy = false;
      building = '';
      render();
    }
  }

  async function flash(name) {
    if (busy) return;
    const go = window.confirm(
      `Flash ${name} to the OP-1?\n\nThe te-boot menu must be on the device screen ` +
        '(power off, hold COM, power on, keep holding). This overwrites the current firmware.',
    );
    if (!go) return;
    busy = true;
    note = `copying ${name} to te-boot…`;
    render();
    try {
      const res = await api.firmwareFlash(name);
      note =
        `copied ${res.bytes ? `${fmtBytes(res.bytes)} → ${res.device || 'te-boot'}` : JSON.stringify(res)}. ` +
        'Pick it in the te-boot menu on the device to install. After install the first boot is a safe start: ' +
        'it says "N plugin files not loaded" — power the OP-1 off and on once and they all load.';
      await refresh();
    } catch (err) {
      note = `flash failed: ${err.message}`;
    } finally {
      busy = false;
      render();
    }
  }

  async function uploadInstaller(file) {
    if (busy || !file) return;
    busy = true;
    note = `adding ${file.name} to the library…`;
    render();
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
      const res = await api.firmwareSave(file.name, btoa(binary));
      await refresh({ select: res.name });
      note = `added ${res.name} (${shortSha(res.sha256)})`;
    } catch (err) {
      note = `could not add ${file.name}: ${err.message}`;
      render();
    } finally {
      busy = false;
    }
  }

  // ---- markup ------------------------------------------------------------
  function pluginRows() {
    return ((fwState.catalog && fwState.catalog.plugins) || [])
      .map((p) => {
        const on = pluginsSel && pluginsSel.has(p.name);
        return `<label class="fw-row${on ? ' on' : ''}">
          <input type="checkbox" data-plugin="${escapeHtml(p.name)}" ${on ? 'checked' : ''}/>
          <span class="fw-name">${escapeHtml(p.name)}</span>
          <span class="dim">${escapeHtml(p.kind || '')}</span>
          <span class="dim fw-size">${fmtBytes(p.size)}</span>
        </label>`;
      })
      .join('');
  }

  function hkChips(group) {
    const items = hk[group] || [];
    const sel = hkSel[group];
    return items
      .map((item) => {
        const on = sel.has(item.id);
        return `<span class="chip fw-chip${on ? ' on' : ''}" data-hk="${escapeHtml(item.id)}"
          title="${escapeHtml(item.description || item.type || '')} — ${on ? 'remove' : 'add'}">${escapeHtml(item.name)}</span>`;
      })
      .join('');
  }

  /** hundekraft content builder; shared by the stock view and the builder entry. */
  function hkBuilderBlock() {
    if (!hkAvailable() || !hkSel) return '';
    const base = (fwState.catalog && fwState.catalog.original && fwState.catalog.original.name) || 'op1_246.op1';
    const capOpts = hk.preset_caps
      .map((c) => `<option value="${c}" ${c === hkSel.cap ? 'selected' : ''}>${c} presets</option>`)
      .join('');
    return `<h3>hundekraft 3.3 <span class="dim">kennel builder, patched from ${escapeHtml(base)}</span></h3>
        <p class="dim">same builder as <a href="https://hundekraft.github.io/op1-workshop/" target="_blank" rel="noopener">hundekraft.github.io/op1-workshop</a>,
        run against the stock firmware on our side: uncheck what should not ship, keep the rest.</p>
        <p class="fw-group-title">synths <span class="dim">${hkSel.synths.size}/${(hk.synths || []).length}</span></p>
        <div class="fw-chips">${hkChips('synths')}</div>
        <p class="fw-group-title">effects <span class="dim">${hkSel.effects.size}/${(hk.effects || []).length}</span></p>
        <div class="fw-chips">${hkChips('effects')}</div>
        ${hk.utilities && hk.utilities.length ? `<p class="fw-group-title">utilities <span class="dim">${hkSel.utilities.size}/${hk.utilities.length}</span></p>
        <div class="fw-chips">${hkChips('utilities')}</div>` : ''}
        <div class="fw-actions">
          <label class="dim">presets per pack <select data-hk-cap>${capOpts}</select></label>
          <button data-action="build-hk" ${busy ? 'disabled' : ''}>
            ${building === 'hundekraft' ? 'building…' : `build hundekraft (${hkSel.synths.size + hkSel.effects.size + hkSel.utilities.size} selected)`}
          </button>
        </div>`;
  }

  /** The picker's virtual entry: build without browsing to the base file. */
  function builderView() {
    return hkBuilderBlock();
  }

  function originalView() {
    const plugins = (fwState.catalog && fwState.catalog.plugins) || [];
    const hkBlock = hkBuilderBlock();
    return `
      <h3>op1r plugins <span class="dim">${pluginsSel ? pluginsSel.size : 0}/${plugins.length}</span></h3>
      <p class="dim">the classic patcher behind <a href="https://pcppcp.github.io/op1r/web/" target="_blank" rel="noopener">pcppcp.github.io/op1r/web</a>:
      pick .op1p files, we build a new image from this original and you flash it here.</p>
      <div class="fw-table">${pluginRows()}</div>
      <div class="fw-actions">
        <button class="wide" data-action="build-op1r" ${busy ? 'disabled' : ''}>
          ${building === 'op1r' ? 'building…' : `build op1r image (${pluginsSel ? pluginsSel.size : 0} plugins)`}
        </button>
      </div>
      ${hkBlock}`;
  }

  function builtOp1rView() {
    const plugins = (fwState.catalog && fwState.catalog.plugins) || [];
    return `
      <h3>plugins in this image <span class="dim">${pluginsSel ? pluginsSel.size : 0}/${plugins.length}</span></h3>
      <p class="dim">checked ones are inside ${escapeHtml(active)}. toggle and rebuild: the builder
      starts from the stock original every time and keeps your slot layout.</p>
      <div class="fw-table">${pluginRows()}</div>
      <div class="fw-actions">
        <button class="wide" data-action="build-op1r" ${busy ? 'disabled' : ''}>
          ${building === 'op1r' ? 'rebuilding…' : `rebuild with ${pluginsSel ? pluginsSel.size : 0} plugins`}
        </button>
      </div>`;
  }

  function hundekraftView() {
    const m = HK_NAME_RE.exec((active || '').toLowerCase());
    const cfgId = m ? m[1] : '';
    const selected = hkSel ? hkSel.synths.size + hkSel.effects.size + hkSel.utilities.size : 0;
    return `
      <h3>hundekraft build <span class="dim">config ${escapeHtml(cfgId || shortSha('—'))}</span></h3>
      <p class="dim">built with the vendored op1-workshop kennel runtime (release ${escapeHtml((hk && hk.release_id) || '?')}).
      To change what is inside, pick the stock op1_246.op1 and build again — every selection gets its
      own ${selected ? `${selected} components` : 'component set'} and preset pack.</p>
      ${cfgId ? `<div class="fw-actions">
        <a class="buttonlike" href="${api.hundekraftPresetsUrl(cfgId)}" download>download preset pack</a>
      </div>` : ''}`;
  }

  function unknownView() {
    if (!members) return '<p class="dim">listing image contents…</p>';
    return `
      <h3>image contents <span class="dim">${members.length} members</span></h3>
      <ul class="plain">
        ${members
          .map((m) => `<li class="clip"><span class="clip-name">${escapeHtml(m.path)}</span><span class="dim">${fmtBytes(m.bytes)}</span></li>`)
          .join('')}
      </ul>
      <p class="dim">the op1r and hundekraft builders both use op1_246.op1 as their base;
      any other file can still be flashed as it is.</p>`;
  }

  function tebootLine() {
    const fw = (fwState && fwState.teboot) || {};
    return fw.mounted
      ? `te-boot volume: ${fw.path} (${fmtBytes(fw.free_bytes)} free)`
      : fw.help || 'no te-boot device: power off, hold COM, power on until the menu appears';
  }

  function render() {
    if (!fwState) return;
    const isBuilder = active === HK_BUILDER;
    const row = isBuilder ? {} : fwState.installers.find((r) => r.name === active) || {};
    const kind = isBuilder ? 'builder' : classify(row, fwState.catalog, hk);
    const modeTag = { builder: 'hundekraft 3.3 builder', original: 'stock original', op1r: 'op1r build', hundekraft: 'hundekraft build', unknown: 'firmware file' }[kind];
    const builderOpt = hkAvailable()
      ? `<option value="${HK_BUILDER}" ${isBuilder ? 'selected' : ''}>hundekraft 3.3 (build new content)</option>`
      : '';
    const options = builderOpt
      + fwState.installers
        .map((r) => `<option value="${escapeHtml(r.name)}" ${r.name === active ? 'selected' : ''}>${escapeHtml(r.name)}</option>`)
        .join('');
    const meta = isBuilder
      ? `base: ${((fwState.catalog && fwState.catalog.original) || {}).name || 'op1_246.op1'} · the build lands in this library`
      : `${fmtBytes(row.bytes || 0)} · ${shortSha(row.sha256)}`;

    container.innerHTML = `
      <div class="fw-head">
        <select data-action="pick-firmware" ${busy ? 'disabled' : ''} title="firmware library: ${fwState.installers.length} files">${options}</select>
        <button data-action="upload-open" title="add a .op1 file to the firmware library">add .op1</button>
        <input type="file" data-action="upload-file" accept=".op1" hidden/>
      </div>
      <p class="dim">${escapeHtml(modeTag)} · ${escapeHtml(meta)}</p>
      <p class="fw-teboot${fwState.teboot && fwState.teboot.mounted ? ' on' : ''}">${escapeHtml(tebootLine())}</p>
      ${note ? `<p class="fw-note">${escapeHtml(note)}</p>` : ''}
      ${kind === 'builder' ? builderView() : ''}
      ${kind === 'original' ? originalView() : ''}
      ${kind === 'op1r' ? builtOp1rView() : ''}
      ${kind === 'hundekraft' ? hundekraftView() : ''}
      ${kind === 'unknown' ? unknownView() : ''}
      ${isBuilder ? '' : `<div class="fw-actions">
        <a class="buttonlike" href="${api.firmwareFileUrl(active)}" download>download</a>
        <button class="wide danger" data-action="flash" ${busy || !active ? 'disabled' : ''}>flash te-boot</button>
      </div>`}`;
    wire();
  }

  function hkItemCategory(id) {
    for (const group of ['synths', 'effects', 'utilities']) {
      if ((hk[group] || []).some((i) => i.id === id)) return group;
    }
    return null;
  }

  function wire() {
    const pick = container.querySelector('[data-action="pick-firmware"]');
    if (pick) {
      pick.addEventListener('change', () => {
        active = pick.value;
        pluginsSel = null;
        members = null;
        note = '';
        building = '';
        resetForActive();
      });
    }
    const uploadBtn = container.querySelector('[data-action="upload-open"]');
    const uploadInput = container.querySelector('[data-action="upload-file"]');
    if (uploadBtn && uploadInput) {
      uploadBtn.addEventListener('click', () => uploadInput.click());
      uploadInput.addEventListener('change', () => uploadInstaller(uploadInput.files[0]));
    }
    for (const box of container.querySelectorAll('[data-plugin]')) {
      box.addEventListener('change', () => {
        pluginsSel = pluginsSel || new Set();
        if (box.checked) pluginsSel.add(box.dataset.plugin);
        else pluginsSel.delete(box.dataset.plugin);
        render();
      });
    }
    for (const chipEl of container.querySelectorAll('[data-hk]')) {
      chipEl.addEventListener('click', () => {
        if (!hkSel) return;
        const id = chipEl.dataset.hk;
        const group = hkItemCategory(id);
        if (!group) return;
        if (hkSel[group].has(id)) hkSel[group].delete(id);
        else hkSel[group].add(id);
        render();
      });
    }
    const cap = container.querySelector('[data-hk-cap]');
    if (cap) {
      cap.addEventListener('change', () => {
        if (hkSel) hkSel.cap = Number(cap.value);
      });
    }
    const buildBtn = container.querySelector('[data-action="build-op1r"]');
    if (buildBtn) buildBtn.addEventListener('click', buildOp1r);
    const hkBtn = container.querySelector('[data-action="build-hk"]');
    if (hkBtn) hkBtn.addEventListener('click', buildHundekraft);
    const flashBtn = container.querySelector('[data-action="flash"]');
    if (flashBtn) flashBtn.addEventListener('click', () => flash(active));
  }

  /** Device-pane render hook; returns true when the workspace is showing. */
  function update(status) {
    const fw = (status && status.firmware) || {};
    if (!fw.present) return false;
    if (!fwState) {
      refresh().catch((err) => {
        container.innerHTML = `<p class="empty">firmware API: ${escapeHtml(err.message)}</p>`;
      });
      return true;
    }
    const signature = `${fw.mounted ? 1 : 0}|${fw.path || ''}|${fwState.installers.map((r) => r.name).join(',')}`;
    if (signature !== update._sig) {
      update._sig = signature;
      refresh().catch(() => {});
    } else {
      const line = container.querySelector('.fw-teboot');
      if (line) {
        line.classList.toggle('on', Boolean(fw.mounted));
        line.textContent = tebootLine();
      }
    }
    return true;
  }

  return { update };
}
