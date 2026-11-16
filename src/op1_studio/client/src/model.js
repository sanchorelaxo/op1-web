/**
 * The OP-1 body, built from the real CAD mesh.
 *
 * assets/op1.stl is the Teenage Engineering OP-1 export from the Fusion 360
 * snapshot in refs/3d_cad. It is used as-is: no hand-modelled boxes, no
 * guessed shapes. At load time the mesh is cut into one piece per control so
 * every key and button can move and be hit-tested on its own.
 *
 * The cut is purely geometric. Every cap in the CAD is an 11 mm island
 * standing 2 mm proud of the panel, and layout.js holds the measured
 * footprint of each one. A triangle belongs to a control when its centroid
 * falls inside that control's measured footprint and it sits above the
 * chassis. Everything else stays in the body.
 */

import { STLLoader } from '../vendor/loaders/STLLoader.js';
import { PANEL, DISPLAY, ENCODERS, VOLUME, BUTTONS, KEYS, KEYBOARD, noteName } from './layout.js';

/** Mesh frame: stl.x + 142.5 = panel x, stl.z + 51 = panel y, base at y -2. */
const OFFSET_X = 142.5;
const OFFSET_Y = 2.0;
const OFFSET_Z = 51.0;

const ABOVE_CHASSIS = PANEL.base + 0.7;
const PRESS_DEPTH = 0.8;
const STL_URL = new URL('../assets/op1.stl', import.meta.url).href;

function makeMaterials(THREE, screenTexture) {
  return {
    body: new THREE.MeshStandardMaterial({
      color: 0xdedad0,
      roughness: 0.4,
      metalness: 0.4,
    }),
    key: new THREE.MeshStandardMaterial({
      color: 0xf6f3ea,
      roughness: 0.52,
      metalness: 0.02,
    }),
    button: new THREE.MeshStandardMaterial({
      color: 0xe2ded2,
      roughness: 0.56,
      metalness: 0.03,
    }),
    volume: new THREE.MeshStandardMaterial({
      color: 0xcdc8bb,
      roughness: 0.46,
      metalness: 0.12,
    }),
    encoders: ENCODERS.map(
      (e) =>
        new THREE.MeshStandardMaterial({
          color: e.color,
          roughness: 0.34,
          metalness: 0.16,
        }),
    ),
    screen: new THREE.MeshStandardMaterial({
      color: 0x101318,
      map: screenTexture,
      emissive: 0xffffff,
      emissiveMap: screenTexture,
      emissiveIntensity: 0.95,
      roughness: 0.28,
      metalness: 0.0,
    }),
  };
}

/**
 * Measured footprints, one per control. Rectangles are padded by 0.3 mm so the
 * cap's own side walls land inside their own piece instead of the body.
 */
function controlFootprints() {
  const footprints = [];

  for (const b of BUTTONS) {
    footprints.push({
      id: b.id,
      part: { kind: 'button', id: b.id, label: b.label, slot: b.slot, index: b.slot },
      x0: b.x - 0.3,
      y0: b.y - 0.3,
      x1: b.x + b.w + 0.3,
      y1: b.y + b.h + 0.3,
      material: 'button',
    });
  }

  for (const k of KEYS) {
    footprints.push({
      id: k.id,
      part: { kind: 'key', id: k.id, label: noteName(k.midi), midi: k.midi, index: k.index },
      x0: k.x - 0.3,
      y0: k.y - 0.3,
      x1: k.x + k.w + 0.3,
      y1: k.y + k.h + 0.3,
      material: 'key',
    });
  }

  return footprints;
}

function roundFootprints() {
  const rounds = ENCODERS.map((e, i) => ({
    id: e.id,
    part: { kind: 'encoder', id: e.id, label: e.label, index: i },
    cx: e.cx,
    cy: e.cy,
    r2: (e.r + 1.2) ** 2,
    material: 'encoder',
    colorIndex: i,
  }));
  rounds.push({
    id: VOLUME.id,
    part: { kind: 'encoder', id: VOLUME.id, label: VOLUME.label, index: 4 },
    cx: VOLUME.cx,
    cy: VOLUME.cy,
    r2: (VOLUME.r + 1.2) ** 2,
    material: 'volume',
  });
  return rounds;
}
export function buildOp1(THREE, screenTexture) {
  const group = new THREE.Group();
  group.name = 'op1';

  const parts = new Map();
  const keys = new Map();
  const encoders = new Map();
  const pickables = [];
  const pressables = [];

  const materials = makeMaterials(THREE, screenTexture);

  const handle = {
    group,
    parts,
    keys,
    encoders,
    pickables,
    screen: null,
    ready: null,
    setKeyDown,
    setKeyActive,
    update,
  };

  function setKeyDown(id, on) {
    const mesh = keys.get(id) || parts.get(id);
    if (!mesh) return;
    mesh.userData.down = !!on;
  }

  function setKeyActive(id, on) {
    const mesh = parts.get(id) || keys.get(id);
    if (!mesh?.material?.emissive) return;
    mesh.material.emissive.setHex(on ? 0x335c8f : 0x000000);
    mesh.material.emissiveIntensity = on ? 0.5 : 0;
  }

  function update(dt) {
    const k = Math.min(1, dt * 18);
    for (const mesh of pressables) {
      const base = mesh.userData.baseY;
      const target = base - (mesh.userData.down ? PRESS_DEPTH : 0);
      mesh.position.y += (target - mesh.position.y) * k;
    }
  }

  handle.ready = load();

  async function load() {
    const res = await fetch(STL_URL, { cache: 'no-cache' });
    if (!res.ok) throw new Error(`op1.stl ${res.status}`);
    const buffer = await res.arrayBuffer();
    const geometry = new STLLoader().parse(buffer);
    build(geometry);
    geometry.dispose();
    return handle;
  }

  function build(geometry) {
    const footprints = controlFootprints();
    const rounds = roundFootprints();

    // one vertex array per control, keyed by control id
    const buckets = new Map(); // id -> {positions, normals, footprint}
    const bodyPos = [];
    const bodyNrm = [];

    const pos = geometry.attributes.position.array;
    const nrm = geometry.attributes.normal.array;

    for (let t = 0; t < pos.length; t += 9) {
      const px = (pos[t] + pos[t + 3] + pos[t + 6]) / 3 + OFFSET_X;
      const py = (pos[t + 1] + pos[t + 4] + pos[t + 7]) / 3;
      const pz = (pos[t + 2] + pos[t + 5] + pos[t + 8]) / 3 + OFFSET_Z;

      let hit = null;

      if (py > 16.4) {
        let best = Infinity;
        for (const r of rounds) {
          const d2 = (px - r.cx) ** 2 + (pz - r.cy) ** 2;
          if (d2 <= r.r2 && d2 < best) {
            best = d2;
            hit = r;
          }
        }
      }

      if (!hit && py > ABOVE_CHASSIS) {
        for (const f of footprints) {
          if (px >= f.x0 && px <= f.x1 && pz >= f.y0 && pz <= f.y1) {
            hit = f;
            break;
          }
        }
      }

      if (!hit) {
        for (let i = 0; i < 9; i += 1) bodyPos.push(pos[t + i]);
        for (let i = 0; i < 9; i += 1) bodyNrm.push(nrm[t + i]);
        continue;
      }

      let bucket = buckets.get(hit.id);
      if (!bucket) {
        bucket = { positions: [], normals: [], footprint: hit };
        buckets.set(hit.id, bucket);
      }
      for (let i = 0; i < 9; i += 1) bucket.positions.push(pos[t + i]);
      for (let i = 0; i < 9; i += 1) bucket.normals.push(nrm[t + i]);
    }

    const bodyGeometry = new THREE.BufferGeometry();
    bodyGeometry.setAttribute('position', new THREE.Float32BufferAttribute(bodyPos, 3));
    bodyGeometry.setAttribute('normal', new THREE.Float32BufferAttribute(bodyNrm, 3));
    bodyGeometry.translate(0, OFFSET_Y, 0);
    const bodyMesh = new THREE.Mesh(bodyGeometry, materials.body);
    bodyMesh.name = 'body';
    bodyMesh.castShadow = true;
    bodyMesh.receiveShadow = true;
    bodyMesh.userData.part = { kind: 'body', id: 'body', label: 'OP-1' };
    group.add(bodyMesh);
    pickables.push(bodyMesh);
    parts.set('body', bodyMesh);

    for (const [id, bucket] of buckets) {
      const f = bucket.footprint;
      const material =
        f.material === 'encoder'
          ? materials.encoders[f.colorIndex]
          : materials[f.material] || materials.body;

      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(bucket.positions, 3));
      geo.setAttribute('normal', new THREE.Float32BufferAttribute(bucket.normals, 3));

      const centreX = (f.cx ?? (f.x0 + f.x1) / 2) - OFFSET_X;
      const centreZ = (f.cy ?? (f.y0 + f.y1) / 2) - OFFSET_Z;
      const pivotY = PANEL.height;

      geo.translate(-centreX, -pivotY, -centreZ);

      const mesh = new THREE.Mesh(geo, material.clone());
      mesh.name = id;
      mesh.position.set(centreX, pivotY + OFFSET_Y, centreZ);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      mesh.userData.part = f.part;
      mesh.userData.baseY = pivotY + OFFSET_Y;
      mesh.userData.down = false;
      mesh.material.emissive.setHex(0x000000);

      group.add(mesh);
      pickables.push(mesh);
      parts.set(id, mesh);
      pressables.push(mesh);

      if (f.part.kind === 'key') keys.set(id, mesh);
      if (f.part.kind === 'encoder') encoders.set(id, mesh);
    }

    // display sits in the recessed well, a hair above its floor
    const screenW = DISPLAY.w - DISPLAY.bezel * 2;
    const screenH = DISPLAY.h - DISPLAY.bezel * 2;
    const screenGeo = new THREE.PlaneGeometry(screenW, screenH);
    screenGeo.rotateX(-Math.PI / 2);

    const screenMesh = new THREE.Mesh(screenGeo, materials.screen);
    screenMesh.name = 'display';
    screenMesh.position.set(
      DISPLAY.x + DISPLAY.w / 2 - OFFSET_X,
      DISPLAY.floor + 0.4 + OFFSET_Y,
      DISPLAY.y + DISPLAY.h / 2 - OFFSET_Z,
    );
    screenMesh.userData.part = { kind: 'display', id: 'display', label: 'DISPLAY' };
    group.add(screenMesh);
    pickables.push(screenMesh);
    parts.set('display', screenMesh);
    handle.screen = screenMesh;

    const unused = KEYBOARD.count - keys.size;
    if (unused !== 0) {
      console.warn(`op1: ${keys.size} of ${KEYBOARD.count} keys were cut from the mesh`);
    }
  }

  return handle;
}
