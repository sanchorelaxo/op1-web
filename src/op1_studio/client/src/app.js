/**
 * op1-studio client.
 *
 * One three.js scene holds a clickable OP-1, one HTML panel holds the library
 * browser. Every control on the model maps to a real action, and every install
 * goes through the local proxy to the OP-1 mounted in disk mode.
 */
import * as THREE from '../vendor/three.module.js';
import { OrbitControls } from '../vendor/OrbitControls.js';
import { RoomEnvironment } from '../vendor/RoomEnvironment.js';
import { buildOp1 } from './model.js';
import { createScreen } from './screen.js';
import { api } from './api.js';
import { noteName } from './layout.js';
import { playClick, playNote } from './audio.js';

const el = (id) => document.getElementById(id);

const state = {
  mode: 'synth',
  slot: 1,
  track: 1,
  playing: false,
  recording: false,
  playhead: 0.18,
  params: [0.62, 0.4, 0.78, 0.3],
  hits: new Set(),
  regions: [3, 0, 5, 1],
  slots: {},
  patchName: '',
  meta: null,
  deviceMounted: false,
  hint: '',
  message: '',
  dirty: true,
};

// ---------------------------------------------------------------- scene
const canvas = el('scene');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.02;
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0d0f13);
scene.fog = new THREE.Fog(0x0d0f13, 900, 1800);

const camera = new THREE.PerspectiveCamera(32, 1, 1, 4000);
camera.position.set(-14, 205, 232);

const controls = new OrbitControls(camera, canvas);
controls.target.set(0, -6, 4);
controls.enableDamping = true;
controls.dampingFactor = 0.075;
controls.minDistance = 170;
controls.maxDistance = 900;
controls.maxPolarAngle = Math.PI * 0.49;
controls.update();

const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;

scene.add(new THREE.HemisphereLight(0xb9c6da, 0x14161a, 0.55));
const key = new THREE.DirectionalLight(0xffffff, 2.1);
key.position.set(-140, 320, 180);
key.castShadow = true;
key.shadow.mapSize.set(2048, 2048);
key.shadow.camera.near = 40;
key.shadow.camera.far = 900;
key.shadow.camera.left = -260;
key.shadow.camera.right = 260;
key.shadow.camera.top = 260;
key.shadow.camera.bottom = -260;
key.shadow.bias = -0.0008;
key.shadow.radius = 4;
scene.add(key);
const rim = new THREE.DirectionalLight(0x9fc4ff, 0.85);
rim.position.set(220, 140, -240);
scene.add(rim);

const floorTex = (() => {
  const c = document.createElement('canvas');
  c.width = c.height = 512;
  const ctx = c.getContext('2d');
  const g = ctx.createRadialGradient(256, 256, 40, 256, 256, 256);
  g.addColorStop(0, '#20262f');
  g.addColorStop(0.45, '#171b22');
  g.addColorStop(1, '#0d0f13');
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, 512, 512);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
})();

const floor = new THREE.Mesh(
  new THREE.PlaneGeometry(1600, 1600),
  new THREE.MeshStandardMaterial({ map: floorTex, roughness: 0.92, metalness: 0 }),
);
floor.rotation.x = -Math.PI / 2;
floor.position.y = -60;
floor.receiveShadow = true;
scene.add(floor);

// ---------------------------------------------------------------- model
const screen = createScreen(THREE);
const op1 = buildOp1(THREE, screen.texture);
op1.group.position.y = 16;
scene.add(op1.group);

// pickables fill in when the CAD mesh finishes loading, so look them up live
const byPart = {
  get(id) {
    const mesh = op1.parts.get(id) || op1.keys.get(id);
    return mesh ? [mesh] : null;
  },
};

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let hovered = null;
let drag = null;

function setHighlight(id, on) {
  const meshes = byPart.get(id);
  if (!meshes) return;
  for (const mesh of meshes) {
    const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
    for (const m of mats) {
      if (!m.emissive) continue;
      m.emissive.setHex(on ? 0x2a4a72 : 0x000000);
      m.emissiveIntensity = on ? 0.55 : 0;
    }
  }
}

function pick(ev) {
  const rect = canvas.getBoundingClientRect();
  pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
  pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
  raycaster.setFromCamera(pointer, camera);
  const hits = raycaster.intersectObjects(op1.pickables, false);
  return hits.length ? hits[0].object.userData.part : null;
}

// ---------------------------------------------------------------- actions
function setMessage(text) {
  state.message = text || '';
  state.dirty = true;
  if (text) {
    clearTimeout(setMessage._t);
    setMessage._t = setTimeout(() => {
      state.message = '';
      state.dirty = true;
    }, 4200);
  }
}

function enterMode(mode) {
  state.mode = mode;
  state.dirty = true;
}

function pressKey(meshKey, part) {
  op1.setKeyDown(part.id, true);
  playNote(part.midi, { velocity: 0.55 });
  state.hint = noteName(part.midi);
  state.dirty = true;
  setTimeout(() => {
    op1.setKeyDown(part.id, false);
    state.hint = '';
    state.dirty = true;
  }, 190);
  if (state.mode === 'drum') {
    state.hits.add(((part.index % 8) + 1));
    state.dirty = true;
    setTimeout(() => {
      state.hits.delete(((part.index % 8) + 1));
      state.dirty = true;
    }, 140);
  }
}

function bumpEncoder(part, delta) {
  const idx = ['enc-blue', 'enc-green', 'enc-white', 'enc-orange'].indexOf(part.id);
  if (idx < 0) return;
  const group = op1.encoders.get(part.id);
  group.rotation.y += delta * 0.012;
  const next = Math.min(1, Math.max(0, state.params[idx] + delta * 0.004));
  state.params[idx] = next;
  const slot = (idx + 1);
  const mesh = op1.parts.get(`slot-${slot}`);
  if (mesh) mesh.scale.setScalar(1 + next * 0.06);
  state.dirty = true;
}

function activate(part) {
  playClick();
  switch (part.kind) {
    case 'mode':
      enterMode(part.mode);
      setMessage(`${part.mode} mode`);
      break;
    case 'track':
      state.track = part.track;
      state.mode = 'tape';
      state.dirty = true;
      setMessage(`track ${part.track}`);
      break;
    case 'slot': {
      state.slot = part.slot;
      state.patchName = state.slots[part.slot]?.name || blankSlotName(part.slot);
      state.meta = state.slots[part.slot]?.meta || null;
      state.dirty = true;
      setMessage(state.patchName);
      break;
    }
    case 'transport':
      if (part.id === 'play') {
        state.playing = !state.playing;
        state.hint = state.playing ? 'tape rolling' : '';
      } else if (part.id === 'stop') {
        state.playing = false;
        state.recording = false;
        state.playhead = 0;
        state.hint = '';
      } else if (part.id === 'rec') {
        state.recording = !state.recording;
        state.hint = state.recording ? 'arming record' : '';
      } else if (part.id === 'prev' || part.id === 'next') {
        const step = part.id === 'prev' ? -1 : 1;
        state.track = ((state.track - 1 + step + 4) % 4) + 1;
      }
      state.dirty = true;
      break;
    case 'encoder':
      bumpEncoder(part, 40);
      break;
    case 'volume':
      setMessage('volume ' + Math.round(state.params[0] * 100));
      break;
    case 'display':
      enterMode(nextMode(state.mode));
      break;
    case 'speaker':
      setMessage('speaker');
      break;
    case 'key':
      break;
    default:
      setMessage(part.label || part.id);
  }
}

function blankSlotName(slot) {
  return state.mode === 'drum' ? `kit ${slot}` : `patch ${slot}`;
}

const MODES = ['synth', 'drum', 'tape', 'mixer'];
const nextMode = (m) => MODES[(MODES.indexOf(m) + 1) % MODES.length];

// ---------------------------------------------------------------- pointer
canvas.addEventListener('pointerdown', (ev) => {
  const part = pick(ev);
  if (!part) return;
  controls.enabled = false;
  if (part.kind === 'encoder') {
    drag = { part, y: ev.clientY, moved: 0 };
  } else {
    drag = { part, y: ev.clientY, moved: 0 };
    if (part.kind === 'key') {
      const mesh = op1.keys.get(part.id);
      if (mesh) pressKey(mesh, part);
    } else {
      activate(part);
    }
  }
  canvas.setPointerCapture(ev.pointerId);
});

canvas.addEventListener('pointermove', (ev) => {
  if (drag) {
    const dy = drag.y - ev.clientY;
    drag.y = ev.clientY;
    drag.moved += Math.abs(dy);
    if (drag.part.kind === 'encoder') bumpEncoder(drag.part, dy * 2.2);
    return;
  }
  const part = pick(ev);
  const id = part ? part.id : null;
  if (id !== hovered) {
    if (hovered) setHighlight(hovered, false);
    if (id) setHighlight(id, true);
    hovered = id;
    canvas.style.cursor = id ? (part.kind === 'key' ? 'pointer' : 'grab') : 'default';
    updateTooltip(part, ev);
  } else if (part) {
    updateTooltip(part, ev);
  }
});

const endDrag = () => {
  drag = null;
  controls.enabled = true;
};
canvas.addEventListener('pointerup', endDrag);
canvas.addEventListener('pointercancel', endDrag);

const tooltip = el('tooltip');
function updateTooltip(part, ev) {
  if (!part) {
    tooltip.style.opacity = '0';
    return;
  }
  const label =
    part.kind === 'key' ? noteName(part.midi) : part.label || part.id;
  tooltip.textContent = label;
  tooltip.style.opacity = '1';
  const rect = canvas.getBoundingClientRect();
  tooltip.style.left = `${ev.clientX - rect.left + 14}px`;
  tooltip.style.top = `${ev.clientY - rect.top + 12}px`;
}

// ---------------------------------------------------------------- resize
function resize() {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (!w || !h) return;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(canvas);

// ---------------------------------------------------------------- ui
const statusPill = el('device-status');
const browseGrid = el('browse-grid');
const browseMeta = el('browse-meta');
const devicePane = el('device-pane');
const deviceSection = document.querySelector('[data-pane="device"]');

// Anything the proxy can decode with ffmpeg goes straight to drum/user: the
// guide wants one sound, twelve seconds, and the device files it as a kit.
const AUDIO_EXTENSIONS = /^(wav|wave|mp3|m4a|aac|flac|ogg|oga|opus|aif|aiff|aifc|wma|webm|mp4|caf|au|aiff)$/i;

function looksLikeAudio(file) {
  if ((file.type || '').startsWith('audio/')) return true;
  return AUDIO_EXTENSIONS.test((file.name.split('.').pop() || '').trim());
}

async function dropDrumFile(file) {
  if (!looksLikeAudio(file)) {
    setMessage(`cannot read ${file.name}: drop an audio file (wav, mp3, flac, ogg…)`);
    return;
  }
  setBusy(true);
  setMessage(`uploading ${file.name}…`);
  try {
    const res = await api.uploadDrum(file.name, file);
    setMessage(`${res.saved} → drum/user (${res.seconds.toFixed(2)} s) — ready to play`);
    await refreshStatus();
    const btn = devicePane.querySelector(`[data-clip="${CSS.escape(res.rel)}"]`);
    if (btn) btn.scrollIntoView({ block: 'nearest' });
  } catch (err) {
    setMessage(`${file.name}: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

function wireDrumDrop() {
  if (!deviceSection) return;
  const stop = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
  deviceSection.addEventListener('dragenter', (ev) => {
    if (![...(ev.dataTransfer?.types || [])].includes('Files')) return;
    stop(ev);
    deviceSection.dataset.dropping = 'true';
  });
  deviceSection.addEventListener('dragover', (ev) => {
    if (![...(ev.dataTransfer?.types || [])].includes('Files')) return;
    stop(ev);
    ev.dataTransfer.dropEffect = 'copy';
  });
  deviceSection.addEventListener('dragleave', (ev) => {
    if (deviceSection.contains(ev.relatedTarget)) return;
    delete deviceSection.dataset.dropping;
  });
  deviceSection.addEventListener('drop', (ev) => {
    stop(ev);
    delete deviceSection.dataset.dropping;
    const files = [...(ev.dataTransfer?.files || [])];
    if (files.length !== 1) {
      setMessage('drop one sound at a time: one file becomes one kit');
      return;
    }
    dropDrumFile(files[0]);
  });
}

wireDrumDrop();
const tapesPane = el('tapes-pane');

let browsePage = 1;
let browseQuery = '';
let browseKind = '';
let statusSnapshot = null;

async function refreshStatus() {
  try {
    const s = await api.status();
    statusSnapshot = s;
    state.deviceMounted = s.mounted;
    state.hint = s.mounted ? `${s.label} · ${fmtBytes(s.free_bytes)} free` : 'no OP-1 volume';
    statusPill.textContent = s.mounted ? `OP-1 mounted · ${fmtBytes(s.free_bytes)} free` : 'OP-1 not mounted';
    statusPill.dataset.on = String(s.mounted);
    el('guidance').textContent = s.guidance;
    state.dirty = true;
    renderDevice(s);
  } catch (err) {
    statusPill.textContent = 'proxy error';
    statusPill.dataset.on = 'false';
    el('guidance').textContent = String(err);
  }
}

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

function renderDevice(s) {
  devicePane.innerHTML = '';
  devicePane.insertAdjacentHTML(
    'afterbegin',
    '<p class="dropzone">drop one sound here — wav, mp3, flac… up to 12 s<br>'
      + '<span class="dim">it lands in drum/user as a kit</span></p>',
  );
  if (!s || !s.mounted) {
    devicePane.innerHTML = `<p class="empty">${escapeHtml(s?.guidance || 'No device.')}</p>`;
    return;
  }
  const rows = [];
  rows.push(`<p class="path">${escapeHtml(s.path)}</p>`);
  rows.push(listBlock('synth', s.synth || []));
  rows.push(listBlock('drum', s.drum || []));
  rows.push(listBlock('tape', s.tape_tracks || []));
  rows.push(listBlock('album', s.album || []));
  devicePane.insertAdjacentHTML('beforeend', rows.join(''));
  for (const btn of devicePane.querySelectorAll('[data-clip]')) {
    btn.addEventListener('click', () => toggleClip(btn.dataset.clip));
  }
  for (const btn of devicePane.querySelectorAll('[data-del]')) {
    btn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      deleteEntry(btn.dataset.del);
    });
  }
  syncClipRows();
}

function listBlock(title, items) {
  const body = items.length
    ? items.map(clipRow).join('')
    : '<li class="dim">empty</li>';
  return `<h3>${title} <span class="dim">${items.length}</span></h3><ul class="plain">${body}</ul>`;
}

function clipRow(entry) {
  const rel = entry.rel || '';
  const name = entry.name || rel;
  return `<li class="clip"><button class="clip-btn" data-clip="${escapeHtml(rel)}"
      title="play ${escapeHtml(name)}">
      <span class="clip-icon">▶</span>
      <span class="clip-name">${escapeHtml(name)}</span>
    </button><span class="dim">${fmtBytes(entry.bytes)}</span>
    <button class="clip-del" data-del="${escapeHtml(rel)}"
      title="delete ${escapeHtml(name)} from the OP-1" aria-label="delete ${escapeHtml(name)}">×</button></li>`;
}

async function deleteEntry(rel) {
  const name = rel.split('/').pop();
  if (!window.confirm(`Delete ${name} from the OP-1?\n\nThis removes the file from the device.`)) {
    return;
  }
  setBusy(true);
  setMessage(`deleting ${name}…`);
  try {
    await api.deleteEntry(rel);
    if (clipRel === rel) {
      clip.pause();
      clip.removeAttribute('src');
      clipRel = '';
    }
    setMessage(`${name} deleted`);
  } catch (err) {
    setMessage(`cannot delete ${name}: ${err.message}`);
  } finally {
    setBusy(false);
    await refreshStatus();
  }
}

// ---------------------------------------------------------------- clips
// One element for the whole page: clicking a second clip stops the first, and
// the converted WAV is streamed from the proxy, which is the only party that
// can read an AIFF (Chromium has no AIFF decoder at all).
const clip = new Audio();
clip.preload = 'metadata';
let clipRel = '';

function toggleClip(rel) {
  if (!rel) return;
  if (clipRel === rel && !clip.paused) {
    clip.pause();
    return;
  }
  if (clipRel !== rel || !clip.src) {
    clipRel = rel;
    clip.src = api.audioUrl(rel);
  }
  clip.play().catch((err) => {
    setMessage(`cannot play ${rel.split('/').pop()}: ${err.message}`);
    syncClipRows();
  });
}

function syncClipRows() {
  for (const btn of devicePane.querySelectorAll('[data-clip]')) {
    const active = btn.dataset.clip === clipRel;
    const playing = active && !clip.paused && !clip.ended;
    btn.classList.toggle('playing', playing);
    btn.classList.toggle('paused', active && !playing);
    const icon = btn.querySelector('.clip-icon');
    if (icon) icon.textContent = playing ? '❚❚' : '▶';
  }
}

clip.addEventListener('play', syncClipRows);
clip.addEventListener('pause', syncClipRows);
clip.addEventListener('ended', syncClipRows);
clip.addEventListener('error', async () => {
  // The element only says "error"; ask the proxy for the sentence it wrote.
  // A one-byte range keeps this cheap even for a 60 MB album side.
  const rel = clipRel;
  if (!rel) return;
  let why = 'the proxy could not serve this clip';
  try {
    const res = await fetch(api.audioUrl(rel), { headers: { Range: 'bytes=0-0' } });
    const text = await res.text();
    try {
      why = JSON.parse(text).error || why;
    } catch {
      /* not JSON: leave the generic sentence */
    }
  } catch {
    /* the proxy is unreachable: the generic sentence is all we have */
  }
  setMessage(why.slice(0, 120));
  syncClipRows();
});

const escapeHtml = (s) =>
  String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

async function loadBrowse(page = browsePage) {
  browsePage = page;
  browseGrid.innerHTML = '<p class="empty">loading op1.fun…</p>';
  try {
    const res = await api.patches({ page, q: browseQuery, kind: browseKind });
    const items = res.patches || [];
    browseMeta.textContent = `${res.total_visible ?? items.length} patches · page ${res.page ?? page}`;
    browseGrid.innerHTML = items.length
      ? items.map(patchCard).join('')
      : '<p class="empty">nothing found</p>';
    for (const node of browseGrid.querySelectorAll('[data-install]')) {
      node.addEventListener('click', () => installRemote(node.dataset.install, node.dataset.name));
    }
    el('page-prev').disabled = page <= 1;
  } catch (err) {
    browseGrid.innerHTML = `<p class="empty">op1.fun unavailable: ${escapeHtml(err.message)}</p>`;
  }
}

function patchCard(p) {
  const kind = (p.kind || p.type || 'patch').toLowerCase();
  const name = p.title || p.name || 'untitled';
  const author = p.user || p.author || '';
  return `<button class="card" data-install="${escapeHtml(p.url)}" data-name="${escapeHtml(name)}">
    <span class="kind ${kind}">${escapeHtml(kind)}</span>
    <strong>${escapeHtml(name)}</strong>
    <span class="dim">${escapeHtml(author)}</span>
  </button>`;
}

async function installRemote(url, name) {
  setBusy(true);
  setMessage(`installing ${name}…`);
  try {
    const res = await api.installRemote(url, { force: true });
    const slot = state.slots[state.slot] ? nextFreeSlot() : state.slot;
    state.slots[slot] = { name: res.installed || name, meta: res.meta || null };
    state.slot = slot;
    state.patchName = res.installed || name;
    state.meta = res.meta || null;
    if (state.meta && String(state.meta.type).toLowerCase() === 'drum') state.mode = 'drum';
    state.dirty = true;
    setMessage(`slot ${slot}: ${state.patchName}`);
    await refreshStatus();
  } catch (err) {
    setMessage(`failed: ${err.message}`);
  } finally {
    setBusy(false);
  }
}

function nextFreeSlot() {
  for (let i = 1; i <= 8; i += 1) if (!state.slots[i]) return i;
  return Math.floor(Math.random() * 8) + 1;
}

async function loadTapes() {
  tapesPane.innerHTML = '<p class="empty">loading library…</p>';
  try {
    const lib = await api.library();
    const tapes = lib.tapes || [];
    const patches = lib.patches || [];
    const tapeRows = tapes.length
      ? tapes
          .map(
            (t) => `<div class="row"><span>${escapeHtml(t.name)}</span>
              <button data-tape="${escapeHtml(t.rel || t.path || t.name)}">load to tape</button></div>`,
          )
          .join('')
      : '<p class="empty">no tapes in the library</p>';
    const patchRows = patches.length
      ? patches
          .map(
            (p) => `<div class="row"><span>${escapeHtml(p.name)} <span class="dim">${escapeHtml(p.kind || '')}</span></span>
              <button data-local="${escapeHtml(p.rel || p.path || p.name)}">install</button></div>`,
          )
          .join('')
      : '<p class="empty">no local patches</p>';
    tapesPane.innerHTML = `<h3>Tapes</h3>${tapeRows}<h3>Local patches</h3>${patchRows}`;
    for (const b of tapesPane.querySelectorAll('[data-tape]')) {
      b.addEventListener('click', async () => {
        setBusy(true);
        try {
          const r = await api.installTape(b.dataset.tape);
          setMessage(`tape installed (${r.bytes} bytes)`);
          await refreshStatus();
        } catch (err) {
          setMessage(err.message);
        } finally {
          setBusy(false);
        }
      });
    }
    for (const b of tapesPane.querySelectorAll('[data-local]')) {
      b.addEventListener('click', async () => {
        setBusy(true);
        try {
          const r = await api.installLocal(b.dataset.local, { force: true });
          setMessage(`installed ${r.installed}`);
          await refreshStatus();
        } catch (err) {
          setMessage(err.message);
        } finally {
          setBusy(false);
        }
      });
    }
  } catch (err) {
    tapesPane.innerHTML = `<p class="empty">${escapeHtml(err.message)}</p>`;
  }
}

function setBusy(on) {
  document.body.dataset.busy = String(on);
}

// tabs
for (const tab of document.querySelectorAll('[data-tab]')) {
  tab.addEventListener('click', () => {
    for (const t of document.querySelectorAll('[data-tab]')) t.dataset.active = String(t === tab);
    for (const pane of document.querySelectorAll('[data-pane]')) {
      pane.hidden = pane.dataset.pane !== tab.dataset.tab;
    }
    if (tab.dataset.tab === 'tapes') loadTapes();
  });
}

el('search').addEventListener('input', (e) => {
  browseQuery = e.target.value.trim();
  clearTimeout(loadBrowse._t);
  loadBrowse._t = setTimeout(() => loadBrowse(1), 320);
});
el('kind').addEventListener('change', (e) => {
  browseKind = e.target.value;
  loadBrowse(1);
});
el('page-prev').addEventListener('click', () => loadBrowse(Math.max(1, browsePage - 1)));
el('page-next').addEventListener('click', () => loadBrowse(browsePage + 1));
el('pack-load').addEventListener('click', async () => {
  const url = el('pack-url').value.trim();
  if (!url) return;
  setBusy(true);
  setMessage('fetching pack…');
  try {
    const r = await api.installPack(url, { force: true });
    setMessage(`${r.installed.length} installed, ${r.failed.length} failed`);
    await refreshStatus();
  } catch (err) {
    setMessage(err.message);
  } finally {
    setBusy(false);
  }
});
el('eject').addEventListener('click', async () => {
  setBusy(true);
  try {
    await api.eject();
    setMessage('ejected, safe to unplug');
    await refreshStatus();
  } catch (err) {
    setMessage(err.message);
  } finally {
    setBusy(false);
  }
});

// ---------------------------------------------------------------- restart
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Wait until the restarted proxy answers again.
 *
 * The old process keeps serving for a moment after it replies, so a health
 * response only counts once the socket has dropped at least once (or once the
 * uptime is smaller than the one recorded before the restart).
 */
async function waitForBridge(before, deadlineMs = 15000) {
  const started = performance.now();
  const staleUptime = before ? before.uptime_s : Infinity;
  let dropped = !before;
  while (performance.now() - started < deadlineMs) {
    await sleep(400);
    let health = null;
    try {
      health = await api.health();
    } catch {
      dropped = true; // the listener is gone: the image is being replaced
      continue;
    }
    if (health.ok && (dropped || health.uptime_s < staleUptime)) {
      return health;
    }
  }
  return null;
}

async function restartBridge() {
  const button = el('restart');
  if (button.disabled) return;
  button.disabled = true;
  const before = await api.health().catch(() => null);
  statusPill.textContent = 'restarting bridge…';
  statusPill.dataset.on = 'false';
  setMessage('restarting bridge…');

  try {
    await api.restart();
  } catch (err) {
    button.disabled = false;
    setMessage(`restart refused: ${err.message}`);
    await refreshStatus();
    return;
  }

  const back = await waitForBridge(before);
  if (!back) {
    setMessage('bridge did not come back within 15s');
    statusPill.textContent = 'proxy down';
    statusPill.dataset.on = 'false';
    el('guidance').textContent =
      'The proxy did not answer after the restart. Check the terminal running it.';
    button.disabled = false;
    return;
  }

  button.disabled = false;
  await refreshStatus();
  setMessage(`bridge restarted — v${back.version}, up ${Math.round(back.uptime_s)}s`);
}

el('restart').addEventListener('click', restartBridge);

// ---------------------------------------------------------------- loop
const clock = new THREE.Clock();
function frame() {
  const dt = clock.getDelta();
  if (state.playing) {
    state.playhead = (state.playhead + dt / 26) % 1;
    state.dirty = true;
  }
  screen.draw(state);
  op1.update(dt);
  controls.update();
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

resize();
screen.draw(state);
refreshStatus();
loadBrowse(1);
frame();

// expose for the browser console and for e2e checks
window.op1studio = {
  state, api, scene, camera, controls, renderer, op1, enterMode, restartBridge,
  clip, toggleClip, deleteEntry,
};
