// The live view pane of the drone page: the selected drone's eye (3D), a data line, view modes, and keyboard control for the commander.
// Read-only unless the commander takes control; the tablet pilot panel shows the same view. Drawing pauses when the pane is hidden.
import { DroneView } from '../shared/view3d.js';
import { ChunkCache } from '../shared/chunks.js';
import { jpost, STATUS_LABEL, MODE_LABEL, esc } from '../shared/api.js';
import { icon } from '../shared/icons.js';

const MODES = [['phone', 'Camera'], ['chase', 'Chase'], ['fpv', 'Front'], ['top', 'Top']];
const CAM_FRESH_MS = 2500;                      // a camera frame older than this counts as 'the feed stopped'

export class LivePane {
  constructor(root, store, { send, toast }) {
    this.root = root; this.S = store; this.send = send; this.toast = toast; this.sel = null; this.mode = 'chase'; this.keys = new Set(); this.seq = 0; this.hidden = false;
    root.innerHTML = `<header class="live-h"><div class="live-id"><span class="dot" data-r="dot"></span><b data-r="name">No drone</b><span class="muted" data-r="state"></span></div>
      <div class="seg" role="group" aria-label="View" data-r="modes">${MODES.map(([k, l]) => `<button type="button" data-mode="${k}" aria-pressed="${k === this.mode}">${l}</button>`).join('')}</div>
      <div class="live-acts"><button class="btn sm" data-r="take">Take control</button><button class="btn sm danger" data-r="rth">${icon('home')}Fly home</button>
      <button class="btn sm sq" data-r="fold" aria-label="Hide the live view" title="Hide the live view">${icon('down')}</button></div></header>
      <div class="live-b" data-r="body"><img data-r="cam" alt="Live camera picture from the phone pilot (the flight is simulated)" hidden><canvas tabindex="0" aria-label="Live view of the selected drone (simulated)"></canvas><div class="live-hud"><span data-r="h1" class="mono"></span><span data-r="h2" class="mono"></span></div>
      <div class="live-alarm" data-r="alarm" hidden></div><p class="live-empty" data-r="empty">Launch a drone to see what it sees.</p></div>`;
    this.q = (n) => root.querySelector(`[data-r="${n}"]`);
    this.camT = -1e9; this.camSeen = false; this.camFresh = false;
    this.canvas = root.querySelector('canvas');
    this.view = new DroneView(this.canvas, { fps: 30, dpr: 1, chunks: new ChunkCache() });
    root.addEventListener('click', (e) => this.click(e));
    this.canvas.addEventListener('keydown', (e) => this.key(e, true)); this.canvas.addEventListener('keyup', (e) => this.key(e, false)); this.canvas.addEventListener('blur', () => this.keys.clear());
    store.on('drone', (m) => { if (m.id === this.sel) this.update(m); });
    store.on('points', (m) => { if (m.drone === this.sel) this.view.addPoints(m.pts); });
    store.on('cam', (m) => this.frame(m));                       // the phone camera: the newest frame wins
    setInterval(() => { const f = this.fresh(); if (f !== this.camFresh) { this.camFresh = f; if (!f && this.mode === 'phone') this.mode = 'chase'; this.render(); } }, 1000);      // the phone went away: back to the 3D view, not a dead screen
    store.on('detection', () => this.view.setPins([...store.detections.values()]));
    store.on('snapshot', () => { this.view.clearPoints(); this.view.setPins([...store.detections.values()]); this.pickDefault(); });
    store.on('reset', () => { this.view.clearPoints(); this.view.chunks.clear(); this.sel = null; });
    store.on('alarm', (m) => { this.alarmUntil = Date.now() + 6000; this.q('alarm').textContent = m.message; this.q('alarm').hidden = false; setTimeout(() => { if (Date.now() >= this.alarmUntil) this.q('alarm').hidden = true; }, 6100); });
    for (const t of ['lease', 'mode']) store.on(t, () => this.render());
    setInterval(() => this.sendKeys(), 50);
  }

  showFlight() { if (this.mode === 'phone' && !this.fresh()) { this.mode = 'chase'; this.render(); } }
  fresh() { return performance.now() - this.camT < CAM_FRESH_MS; }
  frame(m) {
    if (m.drone !== this.sel) { if (this.fresh() || !this.S.fleet.has(m.drone)) return; this.select(m.drone); }      // a phone is sending for another drone: show that one
    const wasFresh = this.fresh(); this.camT = performance.now(); this.camFresh = true;
    this.q('cam').src = 'data:image/jpeg;base64,' + m.jpg;
    if (!wasFresh) { this.mode = 'phone'; this.camSeen = true; this.render(); }      // a phone just connected: show it
  }

  setHidden(on) { this.hidden = on; this.root.classList.toggle('folded', on); this.view.active = !on && this.mode !== 'phone'; this.q('fold').innerHTML = icon(on ? 'up' : 'down'); this.q('fold').setAttribute('aria-label', on ? 'Show the live view' : 'Hide the live view'); }
  pickDefault() { const a = this.S.drones().find((d) => d.role === 'camera') || this.S.active(); if (a && (!this.sel || !this.S.fleet.has(this.sel))) this.select(a.id); }
  select(id) {
    if (this.sel === id) return;
    this.sel = id; this.view.clearPoints(); this.view.setTrail([], null);
    const d = this.S.fleet.get(id); if (d) this.update(d); else this.render();
  }
  update(d) { this.view.setDrone(d); this.view.setTrail(this.S.trails.get(d.id) || [], d.bias); this.render(); }

  render() {
    const d = this.S.fleet.get(this.sel), q = this.q;
    const st = d && this.S.stations.get(d.station);
    const air = !!d && d.z > 0.5;
    q('name').textContent = d ? d.label || 'Drone' : 'No drone';
    q('state').textContent = d ? ((d.mode && d.mode !== 'idle' ? MODE_LABEL[d.mode] : STATUS_LABEL[d.status] || d.status) || '').toLowerCase() : '';
    q('dot').className = 'dot st-' + (d ? d.status : 'offline');
    const cam = this.mode === 'phone', camOn = cam && this.fresh();
    q('cam').hidden = !camOn; this.canvas.style.visibility = cam ? 'hidden' : '';
    const speed = Math.max(1, this.S.clock.speed || 1);
    q('empty').hidden = cam ? camOn : air;
    q('empty').textContent = cam ? 'No phone camera yet. Pair a phone (top right) and open /fpv on it.'
      : !d ? 'No drone selected.' : d.status === 'launching' ? `Taking off in ${Math.max(1, Math.ceil((d.launch_in_s ?? 0) / speed))} s…` : d.status === 'ready' ? 'On the ground. Press Take off in the Fleet list.' : d.status === 'offline' ? 'This drone is not available.' : 'Not flying.';
    q('h1').textContent = air ? `${Math.round(d.z)} m · ${d.speed.toFixed(1)} m/s · ${Math.round(d.pct)}%` : '';
    q('h2').textContent = '';
    const lease = d && this.S.leases.get(d.id), mine = lease && lease.holder === 'commander', manual = d && d.mode === 'manual';
    const take = q('take'); take.textContent = manual ? (mine ? 'Release' : `${(lease && lease.name) || 'Pilot'} is flying`) : 'Take control'; take.disabled = !air || (manual && !mine);
    take.classList.toggle('primary', !!(manual && mine));
    q('rth').disabled = !air || (d && d.mode === 'rth');
    this.root.querySelectorAll('[data-mode]').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.mode === this.mode)));
    if (!cam) this.view.setMode(this.mode);
    this.view.active = !this.hidden && !cam;
    this.canvas.classList.toggle('ctl', !!(manual && mine));
  }

  async click(e) {
    const b = e.target.closest('button'); if (!b) return;
    const d = this.S.fleet.get(this.sel);
    try {
      if (b.dataset.mode) { this.mode = b.dataset.mode; this.render(); }
      else if (b.dataset.r === 'fold') this.setHidden(!this.hidden);
      else if (!d) return;
      else if (b.dataset.r === 'take') {
        if (d.mode === 'manual') await jpost(`/drones/${d.id}/release_control`, { then: 'auto' });
        else { await jpost(`/drones/${d.id}/take_control`); this.canvas.focus(); this.toast('You are flying. Click the view, then W A S D move, Q and E go down and up, arrows turn, Space holds, R returns, Esc releases.', 'ok', 8000); }
      } else if (b.dataset.r === 'hold') await jpost(`/drones/${d.id}/hold`);
      else if (b.dataset.r === 'rth') await jpost(`/drones/${d.id}/return_home`);
    } catch (err) { this.toast(err.message, 'bad'); }
  }

  // ---- keyboard control: only while the view has focus and the commander holds the lease
  holds() { const d = this.S.fleet.get(this.sel), l = d && this.S.leases.get(d.id); return d && d.mode === 'manual' && l && l.holder === 'commander' ? d : null; }
  key(e, down) {
    const d = this.holds(); if (!d) return;
    const k = e.key.toLowerCase();
    const post = (p, b) => jpost(p, b).catch(() => {});
    if (down && k === 'escape') { e.preventDefault(); return post(`/drones/${d.id}/release_control`, { then: 'auto' }); }
    if (down && k === 'r') { e.preventDefault(); return post(`/drones/${d.id}/return_home`); }
    if (down && k === ' ') { e.preventDefault(); return post(`/drones/${d.id}/hold`); }
    if ('wasdqe'.includes(k) || k.startsWith('arrow')) { e.preventDefault(); down ? this.keys.add(k) : this.keys.delete(k); }
  }
  sendKeys() {
    const d = this.holds(); if (!d) return;
    const k = this.keys, ax = (a, b) => (k.has(a) ? 1 : 0) - (k.has(b) ? 1 : 0), sp = (this.S.config && this.S.config.manual_speed_ms) || 8;
    this.send({ type: 'input', drone: d.id, seq: ++this.seq, vx: ax('w', 's') * sp, vy: ax('a', 'd') * sp * 0.7, vz: ax('e', 'q') * 2.5, yr: ax('arrowleft', 'arrowright'), assist: d.assist !== false });
  }
}
