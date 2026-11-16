/**
 * The OP-1 display: a canvas texture the 3D model maps onto its screen.
 *
 * Four modes mirror the real device (synth, drum, tape, mixer). The drawing
 * is deliberately cheap - it runs every animation frame while the tape plays.
 */
const W = 760;
const H = 400;

const INK = '#f2f0e8';
const DIM = '#7d8794';
const BLUE = '#5b9bd5';
const ORANGE = '#e08a3c';
const BG = '#14161c';

export function createScreen(THREE) {
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d');
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.anisotropy = 4;

  const clear = () => {
    ctx.fillStyle = BG;
    ctx.fillRect(0, 0, W, H);
  };

  const header = (title, right) => {
    ctx.fillStyle = ORANGE;
    ctx.fillRect(0, 0, W, 46);
    ctx.fillStyle = '#14161c';
    ctx.font = '600 24px ui-monospace, SFMono-Regular, monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(title.toUpperCase(), 16, 25);
    if (right) {
      ctx.textAlign = 'right';
      ctx.font = '500 20px ui-monospace, monospace';
      ctx.fillText(right, W - 16, 25);
    }
  };

  const footer = (state) => {
    ctx.fillStyle = '#0c0e12';
    ctx.fillRect(0, H - 40, W, 40);
    ctx.fillStyle = state.deviceMounted ? BLUE : ORANGE;
    ctx.beginPath();
    ctx.arc(20, H - 20, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = DIM;
    ctx.font = '500 17px ui-monospace, monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'middle';
    ctx.fillText(state.hint || '', 34, H - 19);
    if (state.message) {
      ctx.fillStyle = INK;
      ctx.textAlign = 'right';
      ctx.fillText(state.message, W - 16, H - 19);
    }
  };

  const drawSynth = (state) => {
    header(`synth  slot ${state.slot}`, state.patchName || 'empty');
    const gx = 30;
    const gy = 90;
    const gw = W - 60;
    const gh = 200;
    ctx.strokeStyle = '#262b34';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = gy + (gh / 4) * i;
      ctx.beginPath();
      ctx.moveTo(gx, y);
      ctx.lineTo(gx + gw, y);
      ctx.stroke();
    }
    // a fake oscillator trace, deterministic per slot
    ctx.strokeStyle = BLUE;
    ctx.lineWidth = 3;
    ctx.beginPath();
    const shape = (state.meta && state.meta.wave) || state.slot;
    for (let x = 0; x <= gw; x += 3) {
      const t = (x / gw) * Math.PI * 2;
      let v = Math.sin(t * (1 + (shape % 4))) * 0.5;
      if (shape % 2) v += Math.sin(t * 5) * 0.2;
      const y = gy + gh / 2 - v * (gh / 2 - 6);
      if (x === 0) ctx.moveTo(gx + x, y);
      else ctx.lineTo(gx + x, y);
    }
    ctx.stroke();
    // four parameter bars, echoing the colour encoders
    const labels = ['BLUE', 'GREEN', 'WHITE', 'ORANGE'];
    const colors = [BLUE, '#4f9d63', INK, ORANGE];
    const bw = (gw - 30) / 4;
    for (let i = 0; i < 4; i += 1) {
      const v = Math.max(0.06, (state.params && state.params[i]) ?? 0.5);
      const x = gx + i * (bw + 10);
      ctx.fillStyle = '#20242c';
      ctx.fillRect(x, gy + gh + 16, bw, 18);
      ctx.fillStyle = colors[i];
      ctx.fillRect(x, gy + gh + 16, bw * v, 18);
      ctx.fillStyle = DIM;
      ctx.font = '500 14px ui-monospace, monospace';
      ctx.textAlign = 'left';
      ctx.fillText(labels[i], x, gy + gh + 50);
    }
  };

  const drawDrum = (state) => {
    header(`drum  kit ${state.slot}`, state.patchName || 'empty');
    const cols = 4;
    const rows = 2;
    const pad = 18;
    const pw = (W - 60 - pad * (cols - 1)) / cols;
    const ph = 110;
    for (let i = 0; i < 8; i += 1) {
      const c = i % cols;
      const r = Math.floor(i / cols);
      const x = 30 + c * (pw + pad);
      const y = 84 + r * (ph + pad);
      const hot = state.hits && state.hits.has(i + 1);
      ctx.fillStyle = hot ? ORANGE : '#232833';
      ctx.fillRect(x, y, pw, ph);
      ctx.strokeStyle = hot ? '#ffd9a8' : '#333a47';
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, pw - 2, ph - 2);
      ctx.fillStyle = hot ? '#14161c' : DIM;
      ctx.font = '600 26px ui-monospace, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(String(i + 1), x + pw / 2, y + ph / 2);
    }
  };

  const drawTape = (state) => {
    const names = ['TRACK 1', 'TRACK 2', 'TRACK 3', 'TRACK 4'];
    header('tape', state.playing ? 'PLAYING' : 'stopped');
    const top = 70;
    const laneH = 62;
    const left = 120;
    const width = W - left - 30;
    for (let i = 0; i < 4; i += 1) {
      const y = top + i * (laneH + 8);
      const active = state.track === i + 1;
      ctx.fillStyle = active ? '#2a3140' : '#1b202a';
      ctx.fillRect(left, y, width, laneH);
      ctx.fillStyle = active ? ORANGE : '#39404e';
      ctx.fillRect(left, y, 6, laneH);
      ctx.fillStyle = active ? INK : DIM;
      ctx.font = '600 18px ui-monospace, monospace';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'middle';
      ctx.fillText(names[i], left - 14, y + laneH / 2);
      // region blocks
      const blocks = (state.regions && state.regions[i]) || 0;
      ctx.fillStyle = active ? 'rgba(224,138,60,0.35)' : 'rgba(91,155,213,0.22)';
      for (let b = 0; b < blocks; b += 1) {
        ctx.fillRect(left + 12 + b * 46, y + 14, 34, laneH - 28);
      }
    }
    // playhead
    const pos = state.playhead ?? 0.25;
    const px = left + 6 + pos * (width - 12);
    ctx.strokeStyle = '#ff6b57';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.moveTo(px, top - 8);
    ctx.lineTo(px, top + 4 * (laneH + 8) - 8);
    ctx.stroke();
  };

  const drawMixer = (state) => {
    header('mixer', `master ${Math.round(((state.params && state.params[0]) ?? 0.7) * 100)}%`);
    const labels = ['T1', 'T2', 'T3', 'T4'];
    const colors = [ORANGE, BLUE, '#4f9d63', INK];
    const colW = (W - 80) / 4;
    for (let i = 0; i < 4; i += 1) {
      const x = 40 + i * colW + colW / 2;
      ctx.fillStyle = '#20242c';
      ctx.fillRect(x - 14, 80, 28, 240);
      const v = (state.params && state.params[i]) ?? 0.5 * (1 - i * 0.12);
      const h = 240 * Math.min(1, Math.max(0.03, v));
      ctx.fillStyle = colors[i];
      ctx.fillRect(x - 14, 80 + 240 - h, 28, h);
      ctx.fillStyle = '#0c0e12';
      ctx.fillRect(x - 22, 80 + 240 - h - 10, 44, 10);
      ctx.fillStyle = DIM;
      ctx.font = '600 18px ui-monospace, monospace';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(labels[i], x, 344);
    }
  };

  const draw = (state) => {
    clear();
    if (state.mode === 'drum') drawDrum(state);
    else if (state.mode === 'tape') drawTape(state);
    else if (state.mode === 'mixer') drawMixer(state);
    else drawSynth(state);
    footer(state);
    texture.needsUpdate = true;
  };

  return { texture, draw, canvas };
}
