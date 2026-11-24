// Tablet pilot panel: pairing, live view, HUD, touch sticks, control lease, link quality, offline tactical map. Everything is a simulation.
import { Socket, jpost, esc, fmtClock, STATUS_LABEL, MODE_LABEL, clamp } from '../shared/api.js';
import { Store } from '../shared/store.js';
import { ChunkCache } from '../shared/chunks.js';
import { DroneView } from '../shared/view3d.js';
import { Tactical } from './tactical.js';

const $ = (id) => document.getElementById(id);
const TOKEN = 'drone.pilot.token';
const S = new Store(); window.__pilot = { store: S };
const st = { sel: null, socket: null, view: null, tac: null, rtt: null, seq: 0, assistOff: false, ready: false, sticks: {}, relMenu: false, sheetFor: null, bannerT: 0, wake: null };
const ICON = {
  full: '<svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>',
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>', minus: '<svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>',
};

// ---------------------------------------------------------------- pairing
function showPair(msg) {
  $('cockpit').hidden = true; $('pair').hidden = false; $('pairErr').hidden = !msg; $('pairErr').textContent = msg || '';
  let code = '';
  const kp = $('keypad'); kp.innerHTML = '';
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', 'Clear', '0', 'Back'].forEach((k) => {
    const b = document.createElement('button'); b.textContent = k; b.type = 'button'; if (k.length > 1) b.style.fontSize = '14px';
    b.onclick = () => { if (k === 'Clear') code = ''; else if (k === 'Back') code = code.slice(0, -1); else if (code.length < 6) code += k; paint(); };
    kp.appendChild(b);
  });
  const paint = () => { [...$('codeBoxes').children].forEach((el, i) => { el.textContent = code[i] || ''; el.classList.toggle('on', i === code.length); }); $('pairGo').disabled = code.length !== 6; };
  paint();
  $('pairGo').onclick = async () => {
    $('pairGo').disabled = true; $('pairErr').hidden = true;
    try { const r = await jpost('/pair/claim', { code, name: $('devName').value.trim() || 'Tablet' }); localStorage.setItem(TOKEN, r.token); boot(); }
    catch (e) { $('pairErr').hidden = false; $('pairErr').textContent = e.message + ' Check the code and try again.'; code = ''; paint(); }
  };
}

// ---------------------------------------------------------------- boot
function boot() {
  const token = localStorage.getItem(TOKEN);
  if (!token) return showPair();
  $('pair').hidden = true; $('cockpit').hidden = false;
  if (!st.ready) buildCockpit();
  if (st.socket) st.socket.close();
  S.reset();
  st.socket = new Socket('/ws/pilot', { query: '?token=' + encodeURIComponent(token), onMessage: (m) => { if (m.type === 'error' && m.code === 'bad_token') { localStorage.removeItem(TOKEN); st.socket.close(); showPair('This tablet was removed or the server restarted. Pair it again.'); return; } S.apply(m); }, onState: (s) => linkState(s) });
}

function buildCockpit() {
  st.ready = true;
  const chunks = new ChunkCache({ radius: 150 });
  st.view = new DroneView($('view'), { fps: 30, dpr: 1, chunks });
  st.tac = new Tactical($('tmap'), S, chunks);
  $('fsBtn').innerHTML = ICON.full; $('zIn').innerHTML = ICON.plus; $('zOut').innerHTML = ICON.minus;
  $('zIn').onclick = () => st.tac.zoom(1); $('zOut').onclick = () => st.tac.zoom(-1);
  $('fsBtn').onclick = goFull;
  document.addEventListener('pointerdown', goFull, { once: true });
  document.addEventListener('contextmenu', (e) => e.preventDefault());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) wakeLock(); });
  $('tasks').hidden = true; sticks(); buttons(); wireStore();
  setInterval(ping, 1000); setInterval(sendInput, 50); setInterval(() => paintTop(), 500);
}

async function goFull() {
  try { if (!document.fullscreenElement) await document.documentElement.requestFullscreen(); } catch (e) { /* not allowed here */ }
  try { await screen.orientation.lock('landscape'); } catch (e) { /* only works in full screen on some browsers */ }
  wakeLock();
}
async function wakeLock() { try { if ('wakeLock' in navigator) st.wake = await navigator.wakeLock.request('screen'); } catch (e) { /* denied */ } }

// ---------------------------------------------------------------- link quality
function ping() { st.socket && st.socket.send({ type: 'ping', t: performance.now() }); }
function linkState(s) {
  const el = $('link'); if (!el) return;
  if (s !== 'open') { el.textContent = s === 'connecting' ? 'connecting' : 'link lost'; el.style.color = 'var(--neg)'; $('gate').hidden = true; if (s === 'closed') banner('Connection lost. Reconnecting; the drone hovers, then returns home if this lasts.', 'bad', 8000); }
}
S.on('pong', (m) => {
  const r = performance.now() - m.t; st.rtt = st.rtt == null ? r : st.rtt * 0.7 + r * 0.3;
  const el = $('link'); el.textContent = `link ${Math.round(st.rtt)} ms`; el.style.color = st.rtt > 300 ? 'var(--neg)' : st.rtt > 120 ? 'var(--warn)' : 'var(--text2)';
});

// ---------------------------------------------------------------- store wiring
function mine() { const d = cur(); const l = d && S.leases.get(d.id); return !!(l && S.session && l.holder === S.session.id); }
function cur() { return st.sel ? S.fleet.get(st.sel) : null; }
function wireStore() {
  const refresh = () => { pick(); paintAll(); };
  S.on('snapshot', refresh); S.on('lease', paintAll); S.on('mode', paintAll); S.on('queue', paintTasks); S.on('station', paintAll);
  S.on('drone', (m) => { if (m.id === st.sel) { st.view.setDrone(m); st.view.setTrail(S.trails.get(m.id) || [], m.bias); st.tac.touch(); paintDrone(m); } else if (!st.sel) pick(); });
  S.on('cone', () => st.tac.touch()); S.on('route', () => st.tac.touch()); S.on('scan', () => st.tac.touch());
  S.on('points', (m) => { if (m.drone === st.sel) st.view.addPoints(m.pts); });
  S.on('detection', (m) => { st.view.setPins([...S.detections.values()]); st.tac.touch(); sheet(); if (m.status === 'unverified' && m.sightings === 1) { buzz([80, 60, 80]); banner(`Sighting: about ${m.est_people} ${m.est_people === 1 ? 'person' : 'people'}`, 'warn', 5000); } });
  S.on('alarm', (m) => { buzz([200, 100, 200]); banner(m.message, m.code === 'COLLISION_AVOIDED' ? 'warn' : 'bad', 6000); });
  S.on('sos_bump', () => banner('An SOS call jumped the queue', 'warn', 5000));
  S.on('notice', (m) => banner(m.message, 'ok', 4000));
  S.on('error', (m) => { banner(m.message, 'bad', 5000); });
  S.on('hello', (m) => { if (!m.ready) gate(m.needs === 'run' ? 'The commander has not run a simulation yet.' : m.needs === 'layer' ? 'The commander needs to load streets and facilities.' : 'Waiting for the drone service.'); });
  S.on('snapshot', () => { $('gate').hidden = true; });
  S.on('reset', () => { st.view.clearPoints(); gate('The scenario changed. Drones were recalled.', true); setTimeout(() => { $('gate').hidden = true; }, 3000); });
  S.on('replay_start', () => banner('Replay of a recorded simulated run', 'warn', 4000));
}
function gate(msg, soft) { $('gateMsg').textContent = msg; $('gateBtn').hidden = !!soft; $('gate').hidden = false; $('gateBtn').onclick = () => { ping(); st.socket.send({ type: 'snapshot' }); }; }
function pick() { if (!st.sel || !S.fleet.has(st.sel)) { const a = S.active(); if (a) setDrone(a.id); } }
function setDrone(id) { st.sel = id; st.tac.sel = id; st.view.clearPoints(); st.view.setPins([...S.detections.values()]); const d = S.fleet.get(id); if (d) { st.view.setDrone(d); st.view.setTrail(S.trails.get(id) || [], d.bias); } paintAll(); }

// ---------------------------------------------------------------- painting
function paintAll() { paintTop(); const d = cur(); if (d) paintDrone(d); paintTasks(); sheet(); }
function paintTop() {
  $('clock').textContent = fmtClock(S.clock.sim_t) + (S.clock.paused ? ' paused' : '');
  const d = cur(); const stn = d && S.stations.get(d.station);
  $('dName').textContent = d ? d.label || 'Drone' : 'No drone';
  $('dState').textContent = d ? (d.mode && d.mode !== 'idle' ? MODE_LABEL[d.mode] : STATUS_LABEL[d.status] || d.status).toLowerCase() : '';
  $('stDot').className = 'dot ' + (!d ? '' : ['lost', 'crashed', 'offline'].includes(d.status) ? 'bad' : d.mode === 'manual' || d.mode === 'rth' ? 'warn' : d.z > 0.5 ? 'live' : '');
}
function paintDrone(d) {
  const p = Math.round(d.pct), res = Math.round(100 * (S.config.reserve_fraction || 0.25));
  $('batBar').style.width = p + '%'; $('batBar').className = p < res + 5 ? 'low' : p < 45 ? 'mid' : ''; $('batRes').style.left = res + '%';
  $('batPct').textContent = p + '%'; $('batMin').textContent = d.mins_left != null ? `about ${Math.max(0, Math.round(d.mins_left))} min before it must return` : (d.status === 'swapping' ? `ready in ${d.swap_in_s} s` : d.status === 'launching' ? `takes off in ${d.launch_in_s} s` : '');
  const air = d.z > 0.5;
  $('vAlt').textContent = air ? Math.round(d.z) + ' m' : '–'; $('vSpd').textContent = air ? d.speed.toFixed(1) + ' m/s' : '–'; $('vHome').textContent = air ? d.dist_home_m + ' m' : '–';
  const l = S.leases.get(d.id); $('vLease').textContent = !l || !l.holder ? 'free' : mine() ? 'you' : (l.name || 'someone');
  const manual = d.mode === 'manual', me = mine();
  const take = $('takeBtn'); take.textContent = manual && me ? 'Release' : 'Take control'; take.classList.toggle('manual', manual && me); take.disabled = !air || (manual && !me);
  $('holdBtn').disabled = !air; $('rthBtn').disabled = !air || d.mode === 'rth';
  $('launchBtn').hidden = d.status !== 'ready'; $('launchBtn').disabled = false;
  const ab = $('assistBtn'); ab.textContent = d.assist === false ? 'Assist off' : 'Assist on'; ab.setAttribute('aria-pressed', String(d.assist !== false)); ab.disabled = !air;
  ['stickL', 'stickR'].forEach((id) => $(id).classList.toggle('dead', !(manual && me)));
}
function paintTasks() {
  return;                                                         // removed: the cockpit shows the picture and the controls only
  const el = $('tasks'); const d = cur();
  const q = S.queue.filter((t) => !t.skipped && t.status !== 'done').slice(0, 4);
  const max = Math.max(...q.map((t) => t.score), 1e-9);
  el.innerHTML = q.map((t) => { const r = t.score / max; const col = r > 0.66 ? 'var(--neg)' : r > 0.33 ? 'var(--warn)' : 'var(--accent)'; return `<div class="task"><span class="rk" style="background:${col}"></span><span>${esc(t.type === 'survey_block' ? 'Survey ' + t.id : t.type.replace('_', ' '))}</span><small>${t.status === 'active' ? 'active' : Math.round(t.est_duration_s / 60) + ' min'}</small></div>`; }).join('');
}
function sheet() {
  const un = S.unverified(); const box = $('sheet');
  if (!un.length) { box.hidden = true; return; }
  const f = un.sort((a, b) => b.t - a.t)[0];
  box.hidden = false;
  box.innerHTML = `<h2>${f.est_people} ${f.est_people === 1 ? 'person' : 'people'} sighted</h2><span class="muted">Confidence ${Math.round(f.confidence * 100)}%${un.length > 1 ? ` · ${un.length - 1} more waiting` : ''}. ${f.flagged ? 'Flagged for the commander.' : 'The commander confirms or rejects it.'}</span>
    <div class="row"><button class="btn primary" data-f="${f.id}" ${f.flagged ? 'disabled' : ''}>Flag for the commander</button><button class="btn" data-x="1">Dismiss</button></div>`;
  box.onclick = (e) => { const b = e.target.closest('button'); if (!b) return; if (b.dataset.f) st.socket.send({ type: 'confirm', id: b.dataset.f }); else box.hidden = true; };
}

// ---------------------------------------------------------------- controls
function buttons() {
  $('droneBtn').onclick = () => {
    const m = $('menu'); if (!m.hidden) { m.hidden = true; return; }
    m.innerHTML = S.drones().map((d) => { const s = S.stations.get(d.station); return `<button data-id="${d.id}"><span class="dot ${d.z > 0.5 ? 'live' : ''}"></span>${esc(d.label || d.id)}<span class="muted" style="margin-left:auto">${Math.round(d.pct)}%</span></button>`; }).join('') || '<button disabled>No drones</button>';
    m.hidden = false; m.onclick = (e) => { const b = e.target.closest('[data-id]'); if (b) setDrone(b.dataset.id); m.hidden = true; };
  };
  $('views').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; st.view.setMode(b.dataset.v); [...$('views').children].forEach((x) => x.setAttribute('aria-pressed', String(x === b))); };
  $('takeBtn').onclick = () => {
    const d = cur(); if (!d) return;
    if (d.mode === 'manual' && mine()) { const m = $('relMenu'); m.hidden = !m.hidden; return; }
    st.socket.send({ type: 'take_control', drone: d.id }); goFull();
  };
  $('relMenu').onclick = (e) => { const b = e.target.closest('button'); if (!b) return; st.socket.send({ type: 'release', drone: cur().id, then: b.dataset.then }); $('relMenu').hidden = true; };
  $('holdBtn').onclick = () => cur() && st.socket.send({ type: 'hold', drone: cur().id });
  $('launchBtn').onclick = () => { if (cur()) { $('launchBtn').disabled = true; st.socket.send({ type: 'launch', drone: cur().id }); } };
  $('assistBtn').onclick = () => {
    const d = cur(); if (!d) return; const b = $('assistBtn');
    if (d.assist !== false) { if (st.assistOff) { clearTimeout(st.assistOff); st.assistOff = false; st.noAssist = true; paintDrone({ ...d, assist: false }); } else { b.textContent = 'Tap again to turn off'; st.assistOff = setTimeout(() => { st.assistOff = false; paintDrone(cur()); }, 3000); } }
    else { st.noAssist = false; paintDrone({ ...d, assist: true }); }
  };
  // return home: press and hold so it is never an accident
  const r = $('rthBtn'); let t0 = 0, raf = 0;
  const step = () => { const p = Math.min(1, (performance.now() - t0) / 700); r.querySelector('i').style.width = p * 100 + '%'; if (p >= 1) { cancelAnimationFrame(raf); t0 = 0; r.querySelector('i').style.width = 0; if (cur()) st.socket.send({ type: 'rth', drone: cur().id }); buzz(60); } else raf = requestAnimationFrame(step); };
  r.addEventListener('pointerdown', () => { if (r.disabled) return; t0 = performance.now(); raf = requestAnimationFrame(step); });
  const stop = () => { cancelAnimationFrame(raf); t0 = 0; r.querySelector('i').style.width = 0; };
  r.addEventListener('pointerup', stop); r.addEventListener('pointerleave', stop); r.addEventListener('pointercancel', stop);
}
function sticks() {
  for (const id of ['stickL', 'stickR']) {
    const el = $(id), knob = el.querySelector('i'), v = st.sticks[id] = { x: 0, y: 0, id: null };
    const move = (e) => { const r = el.getBoundingClientRect(), R = r.width / 2 - 20; let dx = e.clientX - (r.left + r.width / 2), dy = e.clientY - (r.top + r.height / 2); const m = Math.hypot(dx, dy); if (m > R) { dx *= R / m; dy *= R / m; } knob.style.transform = `translate(${dx}px,${dy}px)`; v.x = dx / R; v.y = dy / R; };
    el.addEventListener('pointerdown', (e) => { if (v.id != null) return; v.id = e.pointerId; el.setPointerCapture(e.pointerId); el.classList.add('active'); move(e); });
    el.addEventListener('pointermove', (e) => { if (e.pointerId === v.id) move(e); });
    const end = (e) => { if (e.pointerId !== v.id) return; v.id = null; v.x = v.y = 0; knob.style.transform = ''; el.classList.remove('active'); };
    el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
  }
}
const shape = (v) => { const a = Math.abs(v); const d = a < 0.08 ? 0 : (a - 0.08) / 0.92; return Math.sign(v) * d * d * (1.4 - 0.4 * d); };   // dead zone and a gentle curve
function sendInput() {
  const d = cur(); if (!d || !st.socket || d.mode !== 'manual' || !mine()) return;
  const L = st.sticks.stickL, R = st.sticks.stickR, sp = (S.config.manual_speed_ms || 8) * (st.rtt > 300 ? 0.5 : 1);
  st.socket.send({ type: 'input', drone: d.id, seq: ++st.seq, vx: -shape(L.y) * sp, vy: -shape(L.x) * sp * 0.7, vz: -shape(R.y) * 2.5, yr: -shape(R.x) * 1.2, assist: !st.noAssist });
}

// ---------------------------------------------------------------- small helpers
function banner(msg, kind = 'ok', ms = 5000) { const b = $('banner'); b.textContent = msg; b.className = 'banner ' + kind; b.hidden = false; clearTimeout(st.bannerT); st.bannerT = setTimeout(() => { b.hidden = true; }, ms); }
function buzz(p) { try { navigator.vibrate && navigator.vibrate(p); } catch (e) { /* unsupported */ } }

boot();
