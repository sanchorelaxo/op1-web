/**
 * A small polyphonic click synth so the keyboard feels alive.
 * Two detuned saws through a lowpass with a short envelope; nothing fancy,
 * but it responds instantly and never blocks on network.
 */
let ctx = null;

function ensure() {
  if (!ctx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
  }
  if (ctx.state === 'suspended') ctx.resume();
  return ctx;
}

const freq = (midi) => 440 * 2 ** ((midi - 69) / 12);

export function playNote(midi, { velocity = 0.5, duration = 0.9 } = {}) {
  const ac = ensure();
  if (!ac) return;
  const t = ac.currentTime;
  const out = ac.createGain();
  out.gain.setValueAtTime(0, t);
  out.gain.linearRampToValueAtTime(0.28 * velocity, t + 0.008);
  out.gain.exponentialRampToValueAtTime(0.0008, t + duration);

  const filter = ac.createBiquadFilter();
  filter.type = 'lowpass';
  filter.frequency.setValueAtTime(Math.min(14000, freq(midi) * 12), t);
  filter.Q.value = 0.8;

  for (const detune of [-6, 6]) {
    const osc = ac.createOscillator();
    osc.type = 'sawtooth';
    osc.frequency.value = freq(midi);
    osc.detune.value = detune;
    osc.connect(filter);
    osc.start(t);
    osc.stop(t + duration + 0.05);
  }
  filter.connect(out);
  out.connect(ac.destination);
}

export function playClick(frequency = 1800, gain = 0.06) {
  const ac = ensure();
  if (!ac) return;
  const t = ac.currentTime;
  const osc = ac.createOscillator();
  const env = ac.createGain();
  osc.type = 'square';
  osc.frequency.value = frequency;
  env.gain.setValueAtTime(gain, t);
  env.gain.exponentialRampToValueAtTime(0.0001, t + 0.05);
  osc.connect(env);
  env.connect(ac.destination);
  osc.start(t);
  osc.stop(t + 0.06);
}
