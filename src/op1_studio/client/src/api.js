/** Thin wrapper over the proxy's JSON endpoints. */

async function request(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { error: text.slice(0, 300) };
  }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

const post = (url, body) =>
  request(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });

export const api = {
  health: () => request('/api/health'),
  restart: () => post('/api/restart'),
  status: () => request('/api/status'),
  audioUrl: (rel) => `/api/audio?rel=${encodeURIComponent(rel)}`,
  library: () => request('/api/library'),
  patches: ({ page = 1, q = '', kind = '' } = {}) =>
    request(
      `/api/op1fun/patches?page=${page}&q=${encodeURIComponent(q)}&kind=${encodeURIComponent(kind)}`,
    ),
  pack: (url) => request(`/api/op1fun/pack?url=${encodeURIComponent(url)}`),
  installRemote: (url, { force = false, folder = '' } = {}) =>
    post('/api/op1fun/install', { url, force, folder }),
  installPack: (url, { force = false } = {}) => post('/api/pack/install', { url, force }),
  installLocal: (rel, { force = false } = {}) => post('/api/install/local', { rel, force }),
  installTape: (rel, { backup = true } = {}) => post('/api/tape/install', { rel, backup }),
  eject: () => post('/api/eject'),
  upload: (folder, name, file) =>
    request(`/api/upload?folder=${encodeURIComponent(folder)}&name=${encodeURIComponent(name)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file,
    }),
};
