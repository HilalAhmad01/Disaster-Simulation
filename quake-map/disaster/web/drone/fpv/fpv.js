// Phone-camera drone view. The phone's camera is the picture; two sticks and one button fly the SIMULATED drone. No real drone exists.
// It reuses the tablet pilot's pairing, websocket and control lease, so everything the server does for the tablet works the same here.
import { Socket, jpost, clamp } from '../shared/api.js';
import { Store } from '../shared/store.js';

const $ = (id) => document.getElementById(id);
const TOKEN = 'drone.pilot.token';                                   // same key as /pilot: pair once, use either page
const S = new Store();
const st = { socket: null, sel: null, seq: 0, rtt: null, facing: 'environment', stream: null, wake: null, msgT: 0, tookControl: 0, sticks: {}, built: false, hold: 0 };
window.__fpv = { store: S, st };
const FLIP = '<svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><path d="M9 13a3 3 0 0 1 5-2M15 14a3 3 0 0 1-5 2"/></svg>';

// ---------------------------------------------------------------- pairing (a code from the Drone page; ?code=123456 fills it in)
function showPair(msg) {
  $('fly').hidden = true; $('pair').hidden = false; $('pairErr').hidden = !msg; $('pairErr').textContent = msg || '';
  const code = $('code'), go = $('pairGo');
  const fresh = new URLSearchParams(location.search).get('code');
  if (fresh && /^\d{6}$/.test(fresh) && !code.value) { code.value = fresh; }
  const sync = () => { code.value = code.value.replace(/\D/g, '').slice(0, 6); go.disabled = code.value.length !== 6; };
  code.oninput = sync; sync();
  $('pairForm').onsubmit = async (e) => {
    e.preventDefault(); go.disabled = true; $('pairErr').hidden = true;
    try { const r = await jpost('/pair/claim', { code: code.value, name: 'Phone camera' }); localStorage.setItem(TOKEN, r.token); boot(); }
    catch (err) { $('pairErr').hidden = false; $('pairErr').textContent = err.message + ' Check the code and try again.'; code.value = ''; sync(); }
  };
}

function boot() {
  const token = localStorage.getItem(TOKEN);
  if (!token) return showPair();
  $('pair').hidden = true; $('fly').hidden = false;
  if (!st.built) build();
  if (st.socket) st.socket.close();
  S.reset();
  st.socket = new Socket('/ws/pilot', {
    query: '?token=' + encodeURIComponent(token),
    onMessage: (m) => { if (m.type === 'error' && m.code === 'bad_token') { localStorage.removeItem(TOKEN); st.socket.close(); showPair('This phone was removed or the server restarted. Pair it again.'); return; } S.apply(m); },
    onState: (s) => { if (s === 'closed') say('Connection lost. Reconnecting.', 'bad'); },
  });
}

function build() {
  st.built = true;
  $('flip').innerHTML = FLIP;
  $('flip').onclick = () => { st.facing = st.facing === 'environment' ? 'user' : 'environment'; camera(); };
  document.addEventListener('contextmenu', (e) => e.preventDefault());
  document.addEventListener('pointerdown', goFull, { once: true });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { wakeLock(); if (st.stream && st.stream.getTracks().every((t) => t.readyState === 'ended')) camera(); } });
  sticks(); button(); wire(); camera();
  setInterval(() => st.socket && st.socket.send({ type: 'ping', t: performance.now() }), 1000);
  setInterval(sendInput, 50); setInterval(sendFrame, 125);
  requestAnimationFrame(feel);
}

// ---------------------------------------------------------------- camera
async function camera() {
  if (st.stream) st.stream.getTracks().forEach((t) => t.stop());
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) { say(window.isSecureContext ? 'This browser has no camera access.' : 'The camera needs a secure page. Open it over USB at localhost, or over https.', 'bad', 0); return; }
  try {
    st.stream = await navigator.mediaDevices.getUserMedia({ audio: false, video: { facingMode: { ideal: st.facing }, width: { ideal: 1280 }, height: { ideal: 720 } } });
    $('cam').srcObject = st.stream; await $('cam').play().catch(() => {});
    if ($('msg').dataset.cam) say('');
  } catch (e) { say('Camera blocked. Allow it for this site in Chrome (lock icon, Permissions), then reload.', 'bad', 0); $('msg').dataset.cam = '1'; }
}
async function goFull() {
  try { if (!document.fullscreenElement) await document.documentElement.requestFullscreen(); } catch (e) { /* not allowed here */ }
  try { await screen.orientation.lock('landscape'); } catch (e) { /* only works in full screen on some browsers */ }
  wakeLock();
}
async function wakeLock() { try { if ('wakeLock' in navigator) st.wake = await navigator.wakeLock.request('screen'); } catch (e) { /* denied */ } }

// The laptop's Drone page shows this camera as the drone's view: a small JPEG about 8 times a second over the same websocket.
// The server relays only the newest frame to the commander's page; nothing is stored. A busy link skips frames instead of lagging.
const grab = document.createElement('canvas');
function sendFrame() {
  const v = $('cam'), d = cur(), ws = st.socket && st.socket.ws;
  if (!d || !ws || ws.readyState !== 1 || ws.bufferedAmount > 150000 || document.hidden || !v.videoWidth) return;
  const w = 480, h = Math.max(2, Math.round(w * v.videoHeight / v.videoWidth));
  if (grab.width !== w || grab.height !== h) { grab.width = w; grab.height = h; }
  grab.getContext('2d').drawImage(v, 0, 0, w, h);
  const url = grab.toDataURL('image/jpeg', 0.55);
  st.socket.send({ type: 'cam', drone: d.id, w, h, jpg: url.slice(url.indexOf(',') + 1) });
}

// ---------------------------------------------------------------- simulated drone state
const cur = () => (st.sel && S.fleet.get(st.sel)) || null;
const mine = () => { const d = cur(), l = d && S.leases.get(d.id); return !!(l && S.session && l.holder === S.session.id); };
const air = (d) => d && d.z > 0.5;
function wire() {
  const refresh = () => { if (!st.sel || !S.fleet.has(st.sel)) { const a = S.drones().find((d) => d.role === 'camera') || S.active(); st.sel = a ? a.id : null; } paint(); picker(); };   // the camera drone is this phone's until you pick another
  ['snapshot', 'lease', 'mode', 'drone'].forEach((t) => S.on(t, refresh));
  S.on('hello', (m) => { if (!m.ready) say(m.needs === 'run' ? 'Waiting for the commander to run a simulation.' : m.needs === 'layer' ? 'Waiting for the commander to load streets and facilities.' : 'Waiting for the drone service.', 'warn', 0); });
  S.on('snapshot', () => say(''));
  S.on('pong', (m) => { const r = performance.now() - m.t; st.rtt = st.rtt == null ? r : st.rtt * 0.7 + r * 0.3; });
  S.on('alarm', (m) => say(m.message, m.code === 'COLLISION_AVOIDED' ? 'warn' : 'bad', 5000));
  S.on('error', (m) => say(m.message, 'bad', 5000));
  S.on('reset', () => say('The scenario changed. The drone was recalled.', 'warn', 4000));
}

function paint() {
  const d = cur(), b = $('go');
  $('hud').textContent = d ? `${Math.round(d.pct)}%  ·  ${air(d) ? Math.round(d.z) : 0} m` : '–';
  const manual = !!d && d.mode === 'manual' && mine();
  ['stickL', 'stickR'].forEach((id) => $(id).classList.toggle('dead', !manual));
  b.classList.remove('ok', 'hold', 'take'); b.style.removeProperty('--p');
  if (!d) { b.textContent = 'Take off'; b.disabled = true; return; }
  if (d.status === 'ready') { b.textContent = 'Take off'; b.disabled = false; b.classList.add('ok'); }
  else if (d.status === 'launching') { b.textContent = 'Starting up'; b.disabled = true; }
  else if (['returning', 'landing'].includes(d.status) || d.mode === 'rth') { b.textContent = 'Coming home'; b.disabled = true; }
  else if (air(d) && !manual && d.mode !== 'manual') {
    if (d.role === 'camera') { b.textContent = 'Taking control'; b.disabled = true; takeControl(d); }       // the camera drone is yours from take-off
    else { b.textContent = 'Take control'; b.disabled = false; b.classList.add('ok', 'take'); }                // the auto drone keeps flying until you ask
  }
  else if (air(d) && manual) { b.textContent = 'Hold to return'; b.disabled = false; b.classList.add('hold'); }
  else if (air(d)) { b.textContent = 'Someone else is flying'; b.disabled = true; }
  else { b.textContent = 'Wait'; b.disabled = true; }
}
// ---------------------------------------------------------------- choosing the drone: the camera drone, or the auto drone flying around the scene
function picker() {
  const list = S.drones().sort((a, b) => (a.role === 'camera' ? -1 : 1) - (b.role === 'camera' ? -1 : 1));
  const key = list.map((d) => d.id).join() + '|' + st.sel; const box = $('pick');
  if (box._k === key) return; box._k = key;
  const autos = list.filter((d) => d.role !== 'camera');
  box.hidden = list.length < 2;
  box.innerHTML = list.map((d) => `<button type="button" data-id="${d.id}" aria-pressed="${d.id === st.sel}">${d.role === 'camera' ? 'Camera' : autos.length > 1 ? 'Auto ' + (autos.indexOf(d) + 1) : 'Auto'}</button>`).join('');
  box.onclick = (e) => { const b = e.target.closest('[data-id]'); if (b) choose(b.dataset.id); };
}
function choose(id) {
  if (id === st.sel || !S.fleet.has(id)) return;
  const old = cur();
  if (old && old.mode === 'manual' && mine()) st.socket.send({ type: 'release', drone: old.id, then: old.role === 'camera' ? 'hold' : 'auto' });   // the camera drone hovers; the auto drone goes back to its own route
  st.sel = id; st.tookControl = 0; say('');
  paint(); picker();
}
function takeControl(d) {                                          // once the drone is up, the phone becomes its pilot (retried every 2 s at most)
  const now = performance.now(); if (now - st.tookControl < 2000) return;
  st.tookControl = now; st.socket.send({ type: 'take_control', drone: d.id });
}
function button() {
  const b = $('go'); let t0 = 0, raf = 0;
  const reset = () => { cancelAnimationFrame(raf); t0 = 0; b.style.removeProperty('--p'); };
  const step = () => { const p = Math.min(1, (performance.now() - t0) / 800); b.style.setProperty('--p', p * 100 + '%');
    if (p >= 1) { reset(); const d = cur(); if (d) st.socket.send({ type: 'rth', drone: d.id }); buzz(60); } else raf = requestAnimationFrame(step); };
  b.addEventListener('pointerdown', () => { const d = cur(); if (!d || b.disabled) return; goFull();
    if (d.status === 'ready') { st.socket.send({ type: 'launch', drone: d.id }); say('Taking off', 'ok', 2500); return; }
    if (b.classList.contains('take')) { st.tookControl = performance.now(); st.socket.send({ type: 'take_control', drone: d.id }); return; }
    if (b.classList.contains('hold')) { t0 = performance.now(); raf = requestAnimationFrame(step); } });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach((e) => b.addEventListener(e, reset));
}

// ---------------------------------------------------------------- sticks (same dead zone, curve and speed as the tablet)
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
const shape = (v) => { const a = Math.abs(v); const d = a < 0.08 ? 0 : (a - 0.08) / 0.92; return Math.sign(v) * d * d * (1.4 - 0.4 * d); };
function sendInput() {
  const d = cur(); if (!d || !st.socket || d.mode !== 'manual' || !mine()) return;
  const L = st.sticks.stickL, R = st.sticks.stickR, sp = (S.config && S.config.manual_speed_ms || 8) * (st.rtt > 300 ? 0.5 : 1);
  st.socket.send({ type: 'input', drone: d.id, seq: ++st.seq, vx: -shape(L.y) * sp, vy: -shape(L.x) * sp * 0.7, vz: -shape(R.y) * 2.5, yr: -shape(R.x) * 1.2, assist: true });
}

// ---------------------------------------------------------------- feel: the camera picture leans and shifts with the sticks, so the flight reads on screen
function feel() {
  const L = st.sticks.stickL, R = st.sticks.stickR;
  if (L && R) {
    const roll = clamp(shape(R.x) * 8 + shape(L.x) * 4, -12, 12), tx = clamp(-shape(R.x) * 3 - shape(L.x) * 2, -5, 5), ty = clamp(shape(L.y) * 3 - shape(R.y) * 2, -5, 5), zoom = 1 + clamp(-shape(L.y) * 0.04, -0.04, 0.06);
    $('cam').style.transform = `translate(${tx}%,${ty}%) rotate(${roll * 0.5}deg) scale(${zoom})`;
    $('horizon').style.transform = `rotate(${-roll}deg) translateY(${clamp(shape(L.y) * 18, -30, 30)}px)`;
  }
  requestAnimationFrame(feel);
}

// ---------------------------------------------------------------- small helpers
function say(text, kind = 'ok', ms = 4000) {
  const m = $('msg'); clearTimeout(st.msgT); delete m.dataset.cam;
  if (!text) { m.hidden = true; return; }
  m.textContent = text; m.className = 'msg ' + kind; m.hidden = false;
  if (ms) st.msgT = setTimeout(() => { m.hidden = true; }, ms);
}
function buzz(p) { try { navigator.vibrate && navigator.vibrate(p); } catch (e) { /* unsupported */ } }

boot();
