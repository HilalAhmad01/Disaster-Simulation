// REST and websocket helpers shared by the dashboard module and the tablet pilot panel.
export const API = '/api/drone';

export class ApiError extends Error {
  constructor(message, code, status) { super(message); this.code = code; this.status = status; }
}

export async function call(path, { method = 'GET', body, token } = {}) {
  const r = await fetch(API + path, {
    method, headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let j = null;
  try { j = await r.json(); } catch (e) { /* not json */ }
  if (!r.ok) throw new ApiError((j && (j.detail && j.detail.message || j.detail)) || r.statusText, j && j.code, r.status);
  return j;
}
export const jget = (p) => call(p);
export const jpost = (p, body = {}) => call(p, { method: 'POST', body });
export const jpatch = (p, body = {}) => call(p, { method: 'PATCH', body });
export const jdel = (p) => call(p, { method: 'DELETE' });

/** A websocket that reconnects with backoff and reports its state. send() drops messages while closed (input is latest-wins anyway). */
export class Socket {
  constructor(path, { onMessage, onState, query = '' } = {}) {
    this.path = path; this.query = query; this.onMessage = onMessage || (() => {}); this.onState = onState || (() => {});
    this.ws = null; this.tries = 0; this.closed = false; this.opened = false;
    this.connect();
  }
  url() { return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${this.path}${this.query}`; }
  connect() {
    if (this.closed) return;
    this.onState('connecting');
    let ws;
    try { ws = new WebSocket(this.url()); } catch (e) { this.retry(); return; }
    this.ws = ws;
    ws.onopen = () => { this.tries = 0; this.opened = true; this.onState('open'); };
    ws.onmessage = (e) => { try { this.onMessage(JSON.parse(e.data)); } catch (err) { console.warn('bad message', err); } };
    ws.onclose = () => { this.onState('closed'); this.retry(); };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* already closed */ } };
  }
  retry() {
    if (this.closed) return;
    const wait = Math.min(8000, 400 * 2 ** Math.min(this.tries++, 5));
    setTimeout(() => this.connect(), wait);
  }
  send(obj) { if (this.ws && this.ws.readyState === 1) { this.ws.send(JSON.stringify(obj)); return true; } return false; }
  close() { this.closed = true; try { this.ws && this.ws.close(); } catch (e) { /* ignore */ } }
}

export const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
export const fmtClock = (s) => { s = Math.max(0, Math.round(s)); const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), x = s % 60; return `T+${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(x).padStart(2, '0')}`; };
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const STATUS_LABEL = { ready: 'Ready', launching: 'Launching', takeoff: 'Take-off', transit: 'In transit', surveying: 'Surveying', returning: 'Returning', landing: 'Landing',
  hold: 'Holding', swapping: 'Battery swap', waiting_battery: 'Waiting for battery', offline: 'Offline', lost: 'Lost', crashed: 'Crashed', landed: 'Landed' };
export const MODE_LABEL = { auto: 'AUTO', assisted: 'ASSISTED', manual: 'MANUAL', rth: 'RETURN', hold: 'HOLD', idle: '' };
