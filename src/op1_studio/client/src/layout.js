/**
 * Panel geometry, in millimetres, measured off the real OP-1 CAD mesh.
 *
 * Source of truth: refs/3d_cad/teenage-engineering-op-1-2.snapshot.7/OP-1.stl
 * scanned as a 0.25 mm height field (see tools/measure_stl.py). Every number
 * below is a measured cap edge or centre, not an estimate, so the STL and the
 * clickable parts line up exactly.
 *
 * Panel coordinates: x 0..285 left to right, y 0..102 top edge to keyboard.
 * The mesh keeps the same frame with the origin at the centre of the device,
 * so panel x = stl.x + 142.5 and panel y = stl.z + 51.
 */

export const PANEL = { w: 285, h: 102, height: 16.01, base: 13.0 };

/** Screen well, recessed into the panel; floor sits 2 mm below the plate. */
export const DISPLAY = {
  x: 66.75,
  y: 4.75,
  w: 61.5,
  h: 30.5,
  floor: 14.0,
  bezel: 2.4,
};

/** The four colour encoders: measured top diameter 11 mm on a 31 mm pitch. */
export const ENCODERS = [
  { id: 'enc-blue', cx: 144.0, cy: 20.0, r: 8.35, top: 27.51, color: 0x2f6fd0, label: 'BLUE' },
  { id: 'enc-green', cx: 175.0, cy: 20.0, r: 8.35, top: 27.51, color: 0x49a84a, label: 'GREEN' },
  { id: 'enc-white', cx: 206.0, cy: 20.0, r: 8.35, top: 27.51, color: 0xf0ede2, label: 'WHITE' },
  { id: 'enc-orange', cx: 237.0, cy: 20.0, r: 8.35, top: 27.51, color: 0xe0742a, label: 'ORANGE' },
];

/** Volume knob, top left. */
export const VOLUME = { id: 'volume', cx: 43.25, cy: 12.25, r: 5.0, top: 27.01, label: 'VOLUME' };

/**
 * Every 11 mm cap that is not a key, grouped by the row it sits in.
 * ids are semantic where the app needs them (slots, transport), positional
 * elsewhere. w/h are the measured cap footprint.
 */
export const BUTTONS = [
  { id: 'power', label: 'PWR', kind: 'function', x: 254.75, y: 6.75, w: 11, h: 11 },

  { id: 'aux-1', label: 'SHIFT', kind: 'function', x: 37.75, y: 22.25, w: 11, h: 11 },
  { id: 'aux-2', label: 'COM', kind: 'function', x: 53.25, y: 22.25, w: 11, h: 11 },
  { id: 'aux-3', label: 'AUX', kind: 'function', x: 254.75, y: 22.25, w: 11, h: 11 },

  { id: 'btn-01', label: '1', kind: 'tap', x: 6.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-02', label: '2', kind: 'tap', x: 22.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-03', label: '3', kind: 'tap', x: 37.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-04', label: '4', kind: 'tap', x: 53.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-05', label: '5', kind: 'tap', x: 68.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-06', label: '6', kind: 'tap', x: 84.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-07', label: '7', kind: 'tap', x: 99.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-08', label: '8', kind: 'tap', x: 115.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-09', label: '9', kind: 'tap', x: 130.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-10', label: '10', kind: 'tap', x: 146.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-11', label: '11', kind: 'tap', x: 161.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-12', label: '12', kind: 'tap', x: 177.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-13', label: '13', kind: 'tap', x: 192.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-14', label: '14', kind: 'tap', x: 208.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-15', label: '15', kind: 'tap', x: 223.75, y: 37.75, w: 11, h: 11 },
  { id: 'btn-16', label: '16', kind: 'tap', x: 239.25, y: 37.75, w: 11, h: 11 },
  { id: 'btn-17', label: '17', kind: 'tap', x: 254.75, y: 37.75, w: 11, h: 11 },

  { id: 'play', label: 'PLAY', kind: 'transport', x: 6.75, y: 53.25, w: 11, h: 11 },
  { id: 'stop', label: 'STOP', kind: 'transport', x: 22.25, y: 53.25, w: 11, h: 11 },
  { id: 'rec', label: 'REC', kind: 'transport', x: 37.75, y: 53.25, w: 11, h: 11 },
  { id: 'slot-1', label: '1', kind: 'slot', slot: 1, x: 61.0, y: 53.25, w: 11, h: 11 },
  { id: 'slot-2', label: '2', kind: 'slot', slot: 2, x: 76.5, y: 53.25, w: 11, h: 11 },
  { id: 'slot-3', label: '3', kind: 'slot', slot: 3, x: 92.0, y: 53.25, w: 11, h: 11 },
  { id: 'slot-4', label: '4', kind: 'slot', slot: 4, x: 123.0, y: 53.25, w: 11, h: 11 },
  { id: 'slot-5', label: '5', kind: 'slot', slot: 5, x: 138.5, y: 53.25, w: 11, h: 11 },
  { id: 'slot-6', label: '6', kind: 'slot', slot: 6, x: 169.5, y: 53.25, w: 11, h: 11 },
  { id: 'slot-7', label: '7', kind: 'slot', slot: 7, x: 185.0, y: 53.25, w: 11, h: 11 },
  { id: 'slot-8', label: '8', kind: 'slot', slot: 8, x: 200.5, y: 53.25, w: 11, h: 11 },
  { id: 'prev', label: 'PREV', kind: 'transport', x: 231.5, y: 53.25, w: 11, h: 11 },
  { id: 'next', label: 'NEXT', kind: 'transport', x: 247.0, y: 53.25, w: 11, h: 11 },
];

/** Full-width keybed: 17 white keys, 11 mm caps on a 15.5 mm pitch. */
export const KEYBOARD = {
  x: 6.75,
  y: 68.75,
  pitch: 15.5,
  keyW: 11,
  keyD: 26.5,
  count: 17,
  top: 16.01,
};

const SCALE = [0, 2, 4, 5, 7, 9, 11];
const BASE_NOTE = 48;

/** One entry per key in the CAD keybed, with the note each one plays. */
export const KEYS = Array.from({ length: KEYBOARD.count }, (_, i) => {
  const x = KEYBOARD.x + KEYBOARD.pitch * i;
  return {
    id: `key-${String(i + 1).padStart(2, '0')}`,
    index: i,
    midi: BASE_NOTE + 12 * Math.floor(i / 7) + SCALE[i % 7],
    x,
    y: KEYBOARD.y,
    w: KEYBOARD.keyW,
    h: KEYBOARD.keyD,
    cx: x + KEYBOARD.keyW / 2,
    cy: KEYBOARD.y + KEYBOARD.keyD / 2,
  };
});

export const NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B'];

export function noteName(midi) {
  return `${NOTE_NAMES[midi % 12]}${Math.floor(midi / 12) - 1}`;
}

/** Flat rect list for hit testing, in panel millimetres. */
export function panelRects() {
  return [
    ...KEYS.map((k) => ({ id: k.id, kind: 'key', x0: k.x, y0: k.y, x1: k.x + k.w, y1: k.y + k.h })),
    ...BUTTONS.map((b) => ({ id: b.id, kind: 'button', x0: b.x, y0: b.y, x1: b.x + b.w, y1: b.y + b.h })),
  ];
}
