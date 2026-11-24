// Drone console: the page where everything about the simulated drones lives. The dashboard only links here. All of it is a simulation and
// says so; hidden outcomes never reach this page, only what drones observed.
import { jget, jpost, jpatch, jdel, call, Socket, esc, fmtClock, STATUS_LABEL, MODE_LABEL } from '../shared/api.js';
import { Store } from '../shared/store.js';
import { icon } from '../shared/icons.js';
import { MapLayers } from '../dashboard/layers.js';
import { LivePane } from './liveview.js';

const S = new Store();
const $ = (id) => document.getElementById(id);
const TABS = [['queue', 'Queue'], ['finds', 'Sightings'], ['model', 'Model'], ['tablet', 'Pair']];
const SPEEDS = [1, 5, 10, 30, 60];
const ui = { tab: 'queue', started: false, tool: null, areaPts: [], preview: null, why: new Map(), pair: null, modelView: 'modelled', summary: null, area: null, replays: [], conn: 'connecting', heat: true };
let map, layers, live, socket, toastT, renderT = 0, paintAt = 0, popup = null;
window.__drone = { store: S, ui };

(async function boot() {
  let cfg;
  try { cfg = await jget('/config'); } catch (e) { return gate('The drone module is off', 'Start the app without DRONE_ENABLED=0 to use it.'); }
  if (!cfg.enabled) return gate('The drone module is off', 'Start the app without DRONE_ENABLED=0 to use it.');
  S.config = cfg.config;
  chrome();
  try { ui.area = await (await fetch('/api/live/area')).json(); } catch (e) { ui.area = { loaded: false }; }
  if (!ui.area.loaded) return gate('No area is loaded', 'Load an area and run a simulation on the dashboard, then open this page again.');
  
  initMap(); window.__drone.map = map;
  live = new LivePane($('live'), S, { send: (m) => socket.send(m), toast });
  layers = new MapLayers(S, () => map);
  window.__drone.layers = layers; window.__drone.live = live;
  socket = new Socket('/ws/drone', { onMessage: (m) => S.apply(m), onState: (s) => { ui.conn = s; } });
  wire(); start(); paintAll();
  setInterval(paintTop, 1000);
})();

function gate(title, msg) { $('gateTitle').textContent = title; $('gateMsg').textContent = msg; $('gate').hidden = false; }

// ---------------------------------------------------------------- static chrome
function chrome() {
  $('back').innerHTML = icon('back');
  $('moreBtn').innerHTML = icon('more');
  const setTheme = () => { $('themeBtn').innerHTML = icon(document.documentElement.dataset.theme === 'dark' ? 'sun' : 'moon'); };
  setTheme();
  $('themeBtn').onclick = () => { const t = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = t; try { localStorage.setItem('quake-theme', t); } catch (e) { /* private mode */ } setTheme(); setBase(); };
  $('speed').innerHTML = SPEEDS.map((v) => `<button type="button" data-v="${v}" aria-pressed="false">×${v}</button>`).join('');
  $('speed').onclick = async (e) => { const b = e.target.closest('button'); if (!b) return; try { await jpost2('/api/live/speed', { speed: +b.dataset.v }); S.clock = { ...S.clock, speed: +b.dataset.v }; paintTop(); } catch (er) { toast(er.message, 'bad'); } };
  $('pause').onclick = () => act('pause');
  $('auto').onchange = (e) => jpost('/auto', { enabled: e.target.checked }).catch((er) => toast(er.message, 'bad'));
  $('pairBtn').onclick = () => { ui.tab = 'tablet'; paintAll(true); newPair('pilot', 'fpv'); };
  $('moreBtn').onclick = (e) => moreMenu(e.currentTarget);
  document.querySelectorAll('.tool').forEach((b) => { const t = b.dataset.tool; b.innerHTML = icon({ inspect: 'target', area: 'square', goto: 'send', ban: 'close' }[t]); b.setAttribute('aria-label', b.title); b.onclick = () => setTool(ui.tool === t ? null : t); });
  $('tabs').innerHTML = TABS.map(([k, l]) => `<button class="tab" role="tab" id="tab-${k}" aria-controls="tabBody" data-tab="${k}" aria-selected="false">${l}<span class="count" hidden></span></button>`).join('');
  $('tabBody').setAttribute('role', 'tabpanel');
  $('tabs').onclick = (e) => { const t = e.target.closest('[data-tab]'); if (t) { ui.tab = t.dataset.tab; if (ui.tab === 'model') refreshModel(); paintAll(true); } };
  $('tabBody').onclick = (e) => { const a = e.target.closest('[data-a]'); if (a && a.tagName !== 'INPUT') act(a.dataset.a, a); };
  $('tabBody').onchange = (e) => { if (e.target.dataset.a === 'fb') act('fb', e.target); };
  $('fleet').onclick = (e) => { const a = e.target.closest('[data-a]'); if (a) return act(a.dataset.a, a); const r = e.target.closest('[data-select]'); if (r) select(r.dataset.select); };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && ui.tool) setTool(null); });
  for (const id of ['left', 'right']) { const el = $(id); el.addEventListener('pointerdown', () => { ui.pressing = true; }); for (const t of ['pointerup', 'pointercancel']) document.addEventListener(t, () => { ui.pressing = false; }); }
}
async function jpost2(url, body) { const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); const j = await r.json().catch(() => ({})); if (!r.ok) throw new Error(j.detail || r.statusText); return j; }
function toast(msg, kind = 'ok', ms = 5000) { const t = $('toast'); t.textContent = msg; t.className = 'toast ' + kind; t.hidden = false; clearTimeout(toastT); toastT = setTimeout(() => { t.hidden = true; }, ms); }

// ---------------------------------------------------------------- the service
async function start() {
  try { const r = await jpost('/start'); ui.started = true; if (!S.ready) S.apply({ type: 'snapshot', ...r.snapshot }); layers.reset(); layers.loadScanned(); await loadHeat('modelled'); loadBuildings(); live.pickDefault(); }
  catch (e) {
    const m = { needs_run: ['No simulation has run yet', 'Run an earthquake on the dashboard, then open this page again.'], needs_layer: ['Streets and facilities are not loaded', 'On the dashboard, open Responders and press Show hospitals, fire stations and roads.'] }[e.code];
    gate(...(m || ['The drone service did not start', e.message]));
  }
}

// ---------------------------------------------------------------- map
const BASE = { light: { 'raster-saturation': -0.7, 'raster-opacity': 0.8, 'raster-brightness-max': 1, 'raster-contrast': 0 }, dark: { 'raster-saturation': -1, 'raster-opacity': 1, 'raster-brightness-max': 0.42, 'raster-contrast': 0.3 } };
function setBase() { if (!map || !map.getLayer('osm')) return; const dark = document.documentElement.dataset.theme === 'dark'; for (const [k, v] of Object.entries(BASE[dark ? 'dark' : 'light'])) map.setPaintProperty('osm', k, v); $('mapSec').style.background = dark ? '#141415' : '#fafafa'; }
function initMap() {
  const a = ui.area;
  map = new maplibregl.Map({ container: 'map', center: a.center, zoom: 12.5, maxPitch: 60, style: { version: 8, sources: { osm: { type: 'raster', tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'], tileSize: 256, attribution: '© OpenStreetMap contributors' } }, layers: [{ id: 'osm', type: 'raster', source: 'osm', paint: BASE.light }] } });
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.on('load', () => { setBase(); map.fitBounds([[a.bounds[0], a.bounds[1]], [a.bounds[2], a.bounds[3]]], { padding: 40, duration: 0 }); addHeat(); addBuildings(); });
  map.on('moveend', () => { clearTimeout(ui.bldT); ui.bldT = setTimeout(loadBuildings, 200); });
  new ResizeObserver(() => map.resize()).observe($('map'));
  map.on('click', onMapClick); map.on('dblclick', onMapDbl);
}
function addHeat() {
  if (map.getSource('dmg')) return;
  map.addSource('dmg', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'dmg', type: 'heatmap', source: 'dmg', maxzoom: 17, paint: { 'heatmap-intensity': 2.1, 'heatmap-weight': ['interpolate', ['linear'], ['get', 'pe'], 0, 0, 0.8, 1], 'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 10, 18, 14, 40, 16, 70],
    'heatmap-color': ['interpolate', ['linear'], ['heatmap-density'], 0, 'rgba(58,166,85,0)', 0.08, 'rgba(58,166,85,.35)', 0.3, 'rgba(166,194,59,.55)', 0.5, 'rgba(242,183,5,.65)', 0.75, 'rgba(230,126,34,.75)', 1, 'rgba(215,38,61,.85)'], 'heatmap-opacity': ['interpolate', ['linear'], ['zoom'], 14, 0.8, 16.5, 0] } }, undefined);
  if (ui.pendingHeat) map.getSource('dmg').setData(ui.pendingHeat);
}
// Building footprints at street zoom, coloured by the modelled damage class (the same palette as the dashboard), so "inspect a building" has something to click.
const DMG = ['#3b7dd8', '#3aa655', '#f2b705', '#d7263d'];
function addBuildings() {
  if (map.getSource('bldg')) return;
  map.addSource('bldg', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
  map.addLayer({ id: 'bldg', type: 'fill', source: 'bldg', minzoom: 15, paint: { 'fill-color': ['match', ['get', 'cat'], 0, DMG[0], 1, DMG[1], 2, DMG[2], DMG[3]], 'fill-opacity': 0.55 } });
  map.addLayer({ id: 'bldg-line', type: 'line', source: 'bldg', minzoom: 15, paint: { 'line-color': '#1c2630', 'line-width': 0.5, 'line-opacity': 0.5 } });
  loadBuildings();
}
async function loadBuildings() {
  if (!map || !map.getSource('bldg') || map.getZoom() < 15 || !S.ready) return;
  if (ui.bldCtl) ui.bldCtl.abort();
  const ctl = ui.bldCtl = new AbortController(), b = map.getBounds(), dx = (b.getEast() - b.getWest()) * 0.25, dy = (b.getNorth() - b.getSouth()) * 0.25;
  try { const r = await fetch(`/api/buildings?w=${b.getWest() - dx}&s=${b.getSouth() - dy}&e=${b.getEast() + dx}&n=${b.getNorth() + dy}`, { signal: ctl.signal }); if (r.ok) map.getSource('bldg').setData(await r.json()); } catch (e) { /* superseded or offline */ }
}
async function loadHeat(view) {
  try { const m = await jget('/model/map?view=' + view); ui.pendingHeat = m.heat; if (map.getSource && map.getSource('dmg')) map.getSource('dmg').setData(m.heat); } catch (e) { /* not ready */ }
}
function setTool(t) {
  ui.tool = t; ui.areaPts = []; ui.preview = null; drawDraft();
  document.querySelectorAll('.tool').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.tool === t)));
  map.getCanvas().style.cursor = t ? 'crosshair' : ''; const h = $('toolHint');
  h.hidden = !t; if (t) h.textContent = { inspect: 'Click a building to inspect with a drone. Esc cancels.', area: 'Click to draw the area to survey; double-click to finish. Esc cancels.', goto: 'Click where the flying drone should go. Esc cancels.', ban: 'Click to draw a zone to exclude from the queue; double-click to finish. Esc cancels.' }[t];
  paintTab();
}
function drawDraft() {
  if (!map.getSource('drn-draft')) { map.addSource('drn-draft', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } }); map.addLayer({ id: 'drn-draft', type: 'line', source: 'drn-draft', paint: { 'line-color': '#d7263d', 'line-width': 2, 'line-dasharray': [2, 1] } }); }
  const p = ui.areaPts; map.getSource('drn-draft').setData({ type: 'FeatureCollection', features: p.length > 1 ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: p } }] : [] });
}
async function onMapClick(e) {
  if (!S.ready) return;
  const t = ui.tool;
  try {
    if (t === 'area' || t === 'ban') { ui.areaPts.push([e.lngLat.lng, e.lngLat.lat]); drawDraft(); return; }
    if (t === 'goto') { const d = S.airborne()[0]; if (!d) { toast('No drone is flying. Launch one first.', 'warn'); return; } setTool(null); await jpost(`/drones/${d.id}/goto`, { lon: e.lngLat.lng, lat: e.lngLat.lat }); toast('Flying there.'); return; }
    const b = await jget(`/building_at?lon=${e.lngLat.lng}&lat=${e.lngLat.lat}`).catch(() => null);
    if (!b) return;
    if (t === 'inspect') { setTool(null); previewTask({ type: 'inspect_building', building: b.id }); return; }
    showBuilding(e.lngLat, b);
  } catch (er) { toast(er.message, 'bad'); }
}
async function onMapDbl(e) {
  if (!(ui.tool === 'area' || ui.tool === 'ban') || ui.areaPts.length < 3) return; e.preventDefault();
  const poly = [...ui.areaPts, ui.areaPts[0]], tool = ui.tool; setTool(null);
  try { if (tool === 'ban') { await jpost('/zones/ban', { polygon: poly }); toast('Zone excluded from the queue.'); } else previewTask({ type: 'survey_block', polygon: poly }); } catch (er) { toast(er.message, 'bad'); }
}
function showBuilding(ll, b) {
  if (popup) popup.remove();
  const obs = b.observed ? `Seen by a drone: <b>${b.observed}</b>` : b.scan === 'unscanned' ? 'Not scanned yet' : `Scan ${Math.round(b.scan_pct * 100)}%`;
  popup = new maplibregl.Popup({ offset: 8, closeButton: true, maxWidth: '280px' }).setLngLat(ll).setHTML(`<div class="pop"><b>Building ${b.id}</b><div class="meta">${b.levels ? b.levels + ' storeys · ' : ''}about ${b.occupants} people<br>Modelled collapse chance <b>${Math.round(b.p_collapse * 100)}%</b>${Math.abs(b.p_collapse_updated - b.p_collapse) > 0.004 ? ` (drone-updated ${Math.round(b.p_collapse_updated * 100)}%)` : ''}<br>${obs}</div><button class="btn sm primary" data-pop-inspect="${b.id}">${icon('target')}Inspect with a drone</button><div class="res"></div></div>`).addTo(map);
  popup.getElement().addEventListener('click', (ev) => { const x = ev.target.closest('[data-pop-inspect]'); if (x) { popup.remove(); previewTask({ type: 'inspect_building', building: +x.dataset.popInspect }); } });
}

// ---------------------------------------------------------------- store -> screen
function wire() {
  S.on('*', (m) => {
    if (m.type === 'cam') return;                                   // camera frames only feed the live view; they must not repaint the page
    const L = layers;
    if (['drone', 'cone', 'route', 'clock'].includes(m.type)) { L.mark(m.type === 'drone' ? 'pos' : m.type); if (m.type === 'drone') L.mark('trail'); }
    else if (m.type === 'scan') L.mark('bld', 'cells');
    else if (['detection', 'station', 'road', 'snapshot', 'reset'].includes(m.type)) L.mark('victims', 'roads', 'stations', 'reach', 'pos', 'trail', 'route', 'cells', 'bld');
    if (m.type === 'snapshot') { L.reset(); L.loadScanned(); live.pickDefault(); }
    if (m.type === 'alarm') toast(`${stationName(m.drone)}: ${m.message}`, m.code === 'COLLISION_AVOIDED' ? 'warn' : 'bad');
    if (m.type === 'detection' && m.status === 'unverified' && m.sightings === 1) toast(`Sighting: about ${m.est_people} ${m.est_people === 1 ? 'person' : 'people'}, confidence ${Math.round(m.confidence * 100)}%. Confirm or reject it under Sightings.`, 'warn', 7000);
    if (m.type === 'sos_bump') toast(`An SOS call jumped the queue. ${stationName(m.drone)} is going there.`, 'warn');
    if (m.type === 'notice' || m.type === 'error') toast(m.message, m.type === 'error' ? 'bad' : 'ok');
    if (m.type === 'reset') { toast(m.reason, 'warn'); ui.modelView = 'modelled'; loadHeat('modelled'); }
    if (m.type === 'replay_start') replayFlag(true);
    if (m.type === 'replay_end') replayFlag(false);
    paintAll(m.type);
  });
}
function nearName(lon, lat) {
  let best = null, bd = 1e9;
  for (const st of S.stations.values()) { const d = (st.lon - lon) ** 2 + ((st.lat - lat) * 1.1) ** 2; if (d < bd) { bd = d; best = st; } }
  return best ? best.name.replace(/police( station)?/i, '').trim() || 'a station' : 'the area';
}
const stationName = (id) => { const d = S.fleet.get(id); return d ? d.label || id : id; };
function paintAll(now = false) {                                   // messages repaint at most about twice a second; a click repaints at once
  paintTop();
  if (now) { clearTimeout(renderT); renderT = 0; paintAt = performance.now(); paintFleet(); paintTab(); return; }
  if (renderT) return;
  const go = () => { if (ui.pressing) { renderT = setTimeout(go, 120); return; } renderT = 0; paintAt = performance.now(); paintFleet(); paintTab(); };
  renderT = setTimeout(go, Math.max(0, 450 - (performance.now() - paintAt)));
}
function paintTop() {
  const c = S.clock;
  $('clock').textContent = fmtClock(c.sim_t); $('pause').textContent = c.paused ? 'Resume' : 'Pause';
  $('speed').querySelectorAll('button').forEach((b) => b.setAttribute('aria-pressed', String(+b.dataset.v === Math.round(c.speed || 0))));
  $('auto').checked = !!S.auto;
  const fly = S.airborne().length, un = S.unverified().length, sc = S.scan ? S.scan.pct_scanned : 0;
  $('glance').innerHTML = S.ready ? `<b>${fly}</b> flying · <b>${un}</b> to confirm · <b>${sc}%</b> scanned` : '';
}
function replayFlag(on) { let f = document.querySelector('.replay-flag'); if (on && !f) { f = document.createElement('div'); f.className = 'replay-flag'; f.textContent = 'Replay of a simulated run'; $('mapSec').appendChild(f); } else if (!on && f) f.remove(); }

// ---------------------------------------------------------------- fleet
// The fleet cards are built once and updated in place. Rebuilding them (innerHTML) swallowed clicks: a button that is replaced between the
// mouse going down and coming up never fires. Text is only written when it changed; the buttons are only replaced when the buttons change.
const cards = new Map();
const txt = (el, v) => { if (el._t !== v) { el._t = v; el.textContent = v; } };
const htm = (el, v) => { if (el._h !== v) { el._h = v; el.innerHTML = v; } };
const cls = (el, v) => { if (el._c !== v) { el._c = v; el.className = v; } };
function card(d) {
  let c = cards.get(d.id); if (c) return c;
  const li = document.createElement('li'); li.className = 'drone';
  li.innerHTML = `<button class="drone-main" data-select="${d.id}"><span class="dot"></span><span class="name"></span><span class="state"></span><span class="pct mono"></span></button><div class="bat"><i></i><u></u></div><div class="meta"></div><div class="acts"></div>`;
  const q = (x) => li.querySelector(x);
  c = { li, main: q('.drone-main'), dot: q('.dot'), name: q('.name'), state: q('.state'), pct: q('.pct'), bat: q('.bat'), fill: q('.bat i'), res: q('.bat u'), meta: q('.meta'), acts: q('.acts') };
  cards.set(d.id, c); return c;
}
const AIR = ['takeoff', 'transit', 'surveying', 'returning', 'landing', 'hold'];
const isCam = (d) => d.role === 'camera';
function select(id) { live.select(id); paintFleet(); const d = S.fleet.get(id); if (d && d.z > 1) layers.flyTo(d.lon, d.lat, 15.5); }
function paintFleet() {
  const list = S.drones(), box = $('fleet');
  $('fleetCount').textContent = '';
  if (!S.ready || !list.length) { cards.clear(); box._h = null; box.innerHTML = !S.ready ? '<li class="empty">Starting the drone service…</li>' : '<li class="empty"><b>No drones</b>No working police stations were found in this area.</li>'; return; }
  if (box.querySelector('.empty')) box.innerHTML = '';
  const res = Math.round(100 * (S.config.reserve_fraction || 0.25)), speed = Math.max(1, S.clock.speed || 1);
  list.forEach((d, i) => {
    const c = card(d), st = S.stations.get(d.station) || {}, lease = S.leases.get(d.id), air = AIR.includes(d.status), sel = !!live && live.sel === d.id, pct = Math.round(d.pct);
    const label = d.label || d.id;
    cls(c.dot, 'dot st-' + d.status); txt(c.name, label); c.main.setAttribute('aria-label', `Show ${label} in the live view`);
    txt(c.state, d.mode === 'manual' ? 'Flown by hand' : STATUS_LABEL[d.status] || d.status); txt(c.pct, pct + '%');
    c.bat.title = `Battery ${pct}%. The last ${res}% is kept for the way home.`;
    cls(c.fill, pct < res + 5 ? 'low' : pct < 45 ? 'mid' : ''); if (c.fill._w !== pct) { c.fill._w = pct; c.fill.style.width = pct + '%'; } if (c.res._l !== res) { c.res._l = res; c.res.style.left = res + '%'; }
    let meta;
    if (d.status === 'offline') meta = esc(st.reason || 'Not available');
    else if (air) meta = `<b>${Math.round(d.z)} m</b> · ${d.speed.toFixed(1)} m/s${lease && lease.name ? ` · ${esc(lease.name)} is flying` : ''}`;
    else if (d.status === 'launching') meta = `Taking off in <b>${Math.max(1, Math.ceil((d.launch_in_s ?? 0) / speed))} s</b>`;
    else if (d.status === 'swapping') meta = `New battery in <b>${Math.ceil((d.swap_in_s ?? 0) / speed)} s</b>`;
    else if (d.status === 'waiting_battery') meta = 'Waiting for a battery';
    else meta = isCam(d) ? 'On the ground. Press Take off.' : esc(d.note || 'On the ground. Press Start survey.');
    htm(c.meta, meta);
    const b = [];
    if (d.status === 'ready') b.push(`<button class="btn sm primary" data-a="launch" data-id="${d.id}">${icon('send')}${isCam(d) ? 'Take off' : 'Start survey'}</button>`);
    if (air && d.mode !== 'manual') b.push(`<button class="btn sm" data-a="take" data-id="${d.id}" title="Fly it yourself with the keyboard or the phone">${icon('hand')}Take control</button>`);
    if (d.mode === 'manual') b.push(`<button class="btn sm primary" data-a="resume" data-id="${d.id}" title="${isCam(d) ? 'Stop flying it; it hovers where it is' : 'Give it back to its autopilot'}">${isCam(d) ? 'Release' : 'Back to autopilot'}</button>`);
    if (air && d.mode !== 'rth') b.push(`<button class="btn sm danger" data-a="rth" data-id="${d.id}" title="Fly back to the station and land">${icon('home')}Fly home</button>`);
    htm(c.acts, b.join('')); c.acts.hidden = !b.length;
    if (sel) c.li.setAttribute('aria-current', 'true'); else c.li.removeAttribute('aria-current');
    if (box.children[i] !== c.li) box.insertBefore(c.li, box.children[i] || null);
  });
  while (box.children.length > list.length) box.lastElementChild.remove();
  const lay = [['drone', 'Drones'], ['route', 'Routes'], ['reach', 'Reach rings'], ['coverage', 'Coverage and scanned buildings'], ['victims', 'Sightings'], ['roads', 'Blocked streets'], ['stations', 'Stations'], ['heat', 'Damage heat map']];
  const ll = $('layerList');
  if (!ll.children.length) ll.innerHTML = lay.map(([k, l]) => `<label class="chk"><input type="checkbox" data-layer="${k}">${l}</label>`).join('');        // built once: a rebuild would also swallow checkbox clicks
  ll.querySelectorAll('[data-layer]').forEach((i) => { const on = i.dataset.layer === 'heat' ? !!ui.heat : !!(layers && layers.vis[i.dataset.layer]); if (i.checked !== on) i.checked = on; });
}
document.addEventListener('change', (e) => {
  const l = e.target.closest && e.target.closest('[data-layer]'); if (!l) return;
  if (l.dataset.layer === 'heat') { ui.heat = l.checked; if (map.getLayer('dmg')) map.setLayoutProperty('dmg', 'visibility', l.checked ? 'visible' : 'none'); } else layers.setVisible(l.dataset.layer, l.checked);
});

// ---------------------------------------------------------------- tabs
function paintTab() {
  const un = S.unverified().length;
  $('tabBody').setAttribute('aria-labelledby', 'tab-' + ui.tab);
  document.querySelectorAll('.tab').forEach((b) => { b.setAttribute('aria-selected', String(b.dataset.tab === ui.tab)); const c = b.querySelector('.count'); if (b.dataset.tab === 'finds') { c.hidden = !un; c.textContent = un; } });
  const body = $('tabBody'), keep = body.scrollTop;
  if (!S.ready) { body.innerHTML = '<div class="empty">Waiting for the drone service…</div>'; body._h = null; return; }
  const focused = document.activeElement && body.contains(document.activeElement) ? document.activeElement.id : null;
  const html = { queue: tabQueue, finds: tabFinds, model: tabModel, tablet: tabTablet }[ui.tab]();
  if (body._h === html) return;                                    // nothing changed: leave the DOM alone (no flicker, no lost hover or focus)
  body._h = html; body.innerHTML = html;
  if (focused && $(focused)) $(focused).focus();
  body.scrollTop = keep;
}
const ib = (a, id, ic, label) => `<button class="btn sm sq" data-a="${a}" data-id="${id}" aria-label="${label}" title="${label}">${icon(ic)}</button>`;

function tabQueue() {
  const p = ui.preview;
  let pv = '';
  if (p) {
    if (p.loading) pv = '<div class="preview">Checking the energy budget…</div>';
    else if (p.error) pv = `<div class="preview">${esc(p.error)}<div class="acts"><button class="btn sm" data-a="pv-cancel">Close</button></div></div>`;
    else if (!p.data.feasible) pv = `<div class="preview"><b>This cannot be flown.</b> ${esc(p.data.reasons.join('; '))}<div class="acts"><button class="btn sm" data-a="pv-cancel">Close</button></div></div>`;
    else { const b = p.data.best; pv = `<div class="preview"><b>${esc(b.station)}</b>: about <b>${Math.round(b.duration_s / 60)} min</b> and <b>${Math.round(b.energy_wh)} Wh</b>, ${Math.round(b.distance_m)} m away. <b>${Math.round(100 * b.remaining_wh / S.config.battery_wh)}%</b> battery left on landing.${b.ground !== 'ready' ? ` The drone is not ready: ${esc(b.ground)}${b.wait_s ? ', ' + b.wait_s + ' s' : ''}.` : ''}${p.data.too_tall ? ' A tall building is in the way, so it surveys at maximum altitude.' : ''}<div class="acts"><button class="btn sm primary" data-a="pv-go" ${b.ground !== 'ready' ? 'disabled' : ''}>Send drone</button><button class="btn sm" data-a="pv-cancel">Cancel</button></div></div>`; }
  }
  const q = S.queue;
  const rows = q.length ? q.slice(0, 6).map((t, i) => {
    const reason = t.reason ? `<span class="pill">${t.reason === 'out_of_range' ? 'out of range' : t.reason === 'no_station' ? 'no station' : 'low battery'}</span>` : '';
    return `<li class="row q${t.skipped ? ' skipped' : ''}${t.reason ? ' unreach' : ''}"><div class="row-h"><span class="rank">${i + 1}</span><b>${esc(t.type === 'survey_block' ? 'Survey near ' + nearName(t.lon, t.lat) : { inspect_building: 'Inspect a building near ', verify_sos: 'Look at a call near ', goto: 'Go to a point near ', check_road: 'Check a street near ' }[t.type] ? { inspect_building: 'Inspect a building near ', verify_sos: 'Look at a call near ', goto: 'Go to a point near ', check_road: 'Check a street near ' }[t.type] + nearName(t.lon, t.lat) : t.type)}</b><span class="grow"></span>${t.pinned ? '<span class="pill">pinned</span>' : ''}${t.status !== 'queued' ? `<span class="pill">${t.status}${t.drone ? ' · ' + esc(stationName(t.drone)) : ''}</span>` : ''}${reason}</div>
      <div class="meta">${Math.round(t.est_duration_s / 60)} min · ${t.est_energy_wh} Wh</div>

      <div class="acts">${ib('pin', t.id, 'pin', t.pinned ? 'Unpin' : 'Pin to the top')}${ib('skip', t.id, 'skip', t.skipped ? 'Include again' : 'Skip')}${ib('zoom', t.id, 'target', 'Show on the map')}</div></li>`;
  }).join('') : '<li class="empty"><b>Nothing worth flying right now</b>Every building in reach has been scanned.</li>';
  return `${p ? `<div class="sec">${pv}</div>` : ''}<div class="sec"><h2>Priority queue</h2></div><ul class="rows">${rows}</ul>
    <div class="sec"><div class="acts"><button class="btn sm" data-a="unban">Clear excluded zones</button></div></div>`;
}

function tabFinds() {
  const d = [...S.detections.values()].sort((a, b) => b.t - a.t);
  const un = d.filter((x) => x.status === 'unverified'), rest = d.filter((x) => x.status !== 'unverified').slice(0, 8);
  const label = { unverified: 'unverified', confirmed: 'call ', merged: 'matches call ', rejected: 'rejected' };
  const row = (x, act) => `<li class="row"><div class="row-h"><b>${x.est_people} ${x.est_people === 1 ? 'person' : 'people'}</b><span class="grow"></span><span class="pill">${label[x.status]}${x.status === 'confirmed' || x.status === 'merged' ? x.call_id : ''}</span>${x.flagged ? '<span class="pill">flagged by the pilot</span>' : ''}${x.sightings > 1 ? `<span class="pill">${x.sightings} sightings</span>` : ''}</div>
    <div class="meta">${esc(stationName(x.drone))} · ${fmtClock(x.t)} · confidence <b>${Math.round(x.confidence * 100)}%</b></div><div class="conf"><i style="width:${x.confidence * 100}%"></i></div>${act ? `<div class="acts"><button class="btn sm primary" data-a="confirm" data-id="${x.id}">Confirm and dispatch</button><button class="btn sm" data-a="reject" data-id="${x.id}">Reject</button><button class="btn sm" data-a="zoomdet" data-id="${x.id}">Show</button></div>` : ''}</li>`;
  const sc = S.scan;
  const hoods = sc ? sc.neighbourhoods.map((n) => `<div class="bar"><span>Priority ${n.rank}</span><span class="t"><i style="width:${Math.round(n.pct * 100)}%"></i></span><span class="v mono">${n.scanned}/${n.buildings}</span></div>`).join('') : '';
  const likely = un.filter((x) => x.confidence >= 0.9);
  const bulk = likely.length > 1 ? `<div class="sec"><button class="btn sm" data-a="confirm-likely">Confirm the ${likely.length} most likely (90% or more)</button></div>` : '';
  return `<div class="sec"><h2>To confirm</h2></div>${bulk}<ul class="rows">${un.length ? un.map((x) => row(x, true)).join('') : '<li class="empty"><b>No unverified sightings</b>Drones report people they see here. Some are false alarms, which is why a person confirms them.</li>'}</ul>
    ${rest.length ? `<div class="sec"><h2>Decided</h2></div><ul class="rows">${rest.map((x) => row(x, false)).join('')}</ul>` : ''}
    <div class="sec"><h2>Scanning</h2>${sc ? `<table class="tbl"><tr><td>Buildings scanned</td><td class="mono">${sc.scanned} of ${sc.scanned + sc.partial + sc.unscanned} (${sc.pct_scanned}%)</td></tr><tr><td>Seen collapsed, partly, intact</td><td class="mono">${sc.observed_collapsed} · ${sc.observed_partial} · ${sc.observed_intact}</td></tr><tr><td>Area covered</td><td class="mono">${(sc.cells * 0.0025).toFixed(2)} km²</td></tr></table>${hoods ? `<h2>Priority neighbourhoods</h2>${hoods}` : ''}` : ''}</div>`;
}

function tabModel() {
  const m = ui.summary;
  const r = (n, a, b) => `<tr><td>${n}</td><td class="mono">${Math.round(a).toLocaleString()}</td><td class="mono">${Math.round(b).toLocaleString()}</td><td class="mono">${b - a >= 0 ? '+' : ''}${Math.round(b - a).toLocaleString()}</td></tr>`;
  const t = m ? `<table class="tbl"><tr><th></th><th>Modelled</th><th>Drone-updated</th><th>Change</th></tr>${r('Expected collapses', m.modelled.expected_collapses, m.drone_updated.expected_collapses)}${r('People at risk', m.modelled.people_at_risk, m.drone_updated.people_at_risk)}${m.deaths && m.deaths.modelled != null ? r('Estimated deaths', m.deaths.modelled, m.deaths.drone_updated) : ''}</table><p class="meta" style="margin-top:8px">${m.observed_buildings} buildings observed by a drone.</p>` : '<p class="meta">Loading…</p>';
  return `<div class="sec"><h2>Map shows</h2><div class="seg"><button type="button" data-a="mview" data-v="modelled" aria-pressed="${ui.modelView === 'modelled'}">Modelled</button><button type="button" data-a="mview" data-v="updated" aria-pressed="${ui.modelView === 'updated'}">Drone-updated</button></div>
</div>
    <div class="sec"><h2>Totals</h2>${t}<div class="acts" style="margin-top:12px"><button class="btn sm" data-a="mrefresh">${icon('refresh')}Refresh</button><label class="chk"><input type="checkbox" id="fb" data-a="fb" ${S.config.feedback_enabled !== false ? 'checked' : ''}>Apply drone observations</label></div></div>
`;
}

function tabTablet() {
  const p = ui.pair;
  const how = p && p.page === 'fpv' ? 'On the phone open <b>localhost:8000/fpv</b> (USB: <code>adb reverse tcp:8000 tcp:8000</code>) and type the code.' : 'On the tablet open <b>/pilot</b> and type the code.';
  const box = p ? `<div class="sec"><div class="code">${esc(p.code)}</div><div class="qr" id="qr"></div><p class="meta" style="text-align:center">${how}</p></div>` : '';
  const ses = S.sessions.length ? `<ul class="rows">${S.sessions.map((s) => `<li class="row"><div class="row-h"><span class="dot ${s.connected ? 'st-ready' : ''}" title="${s.connected ? 'connected' : 'offline'}"></span><b>${esc(s.name)}</b><span class="grow"></span><button class="btn sm danger" data-a="revoke" data-id="${s.id}">Remove</button></div></li>`).join('')}</ul>` : '';
  return `<div class="sec"><div class="acts"><button class="btn sm primary" data-a="pair" data-role="pilot" data-page="fpv">Pair a phone</button><button class="btn sm" data-a="pair" data-role="pilot" data-page="pilot">Pair a tablet</button></div></div>${box}${ses}`;
}

// ---------------------------------------------------------------- actions
async function previewTask(spec) {
  ui.tab = 'queue'; ui.preview = { loading: true, spec }; paintAll(true);
  try { ui.preview = { data: await jpost('/tasks/preview', spec), spec }; } catch (e) { ui.preview = { error: e.message }; }
  paintAll(true);
}
async function act(a, el) {
  const id = el && el.dataset.id;
  try {
    switch (a) {
      case 'pause': await jpost('/clock', { paused: !S.clock.paused }); break;
      case 'launch': { const r = await jpost(`/drones/${id}/launch`); live.select(id); live.showFlight(); toast('Starting up. It takes off in a few seconds.'); break; }
      case 'take': await jpost(`/drones/${id}/take_control`); live.select(id); live.setHidden(false); live.canvas.focus(); toast('You are flying. Click the live view, then W A S D move, Q and E go down and up, arrows turn, Space holds, R returns, Esc releases.', 'ok', 8000); break;
      case 'resume': await jpost(`/drones/${id}/release_control`, { then: 'auto' }); break;
      case 'rth': await jpost(`/drones/${id}/return_home`); toast('Flying home.'); break;
      case 'pin': { const t = S.queue.find((x) => x.id === id); await jpatch(`/tasks/${id}`, { pinned: !t.pinned }); break; }
      case 'skip': { const t = S.queue.find((x) => x.id === id); await jpatch(`/tasks/${id}`, { skipped: !t.skipped }); break; }
      case 'why': { if (ui.why.has(id)) ui.why.delete(id); else { ui.why.set(id, 'loading'); paintTab(); try { ui.why.set(id, (await jget(`/tasks/${id}/why`)).why || []); } catch (e) { ui.why.set(id, []); } } paintTab(); break; }
      case 'up': case 'down': await reorder(id, a === 'up' ? -1 : 1); break;
      case 'zoom': { const t = S.queue.find((x) => x.id === id); if (t) layers.flyTo(t.lon, t.lat, 15.5); break; }
      case 'unban': await call('/zones/ban', { method: 'DELETE' }); toast('Excluded zones cleared.'); break;
      case 'pv-cancel': ui.preview = null; paintAll(true); break;
      case 'pv-go': { const spec = ui.preview.spec; ui.preview = null; const r = await jpost('/tasks', spec); toast('A drone is launching.'); live.select(r.drone); paintAll(); break; }
      case 'confirm': { const r = await jpost(`/detections/${id}/confirm`); toast(`Call ${r.call_id} created. Choose a team for it on the dashboard's Dispatch panel.`, 'ok', 8000); break; }
      case 'reject': await jpost(`/detections/${id}/reject`); break;
      case 'confirm-likely': { const ids = S.unverified().filter((x) => x.confidence >= 0.9).map((x) => x.id); const r = await Promise.allSettled(ids.map((i) => jpost(`/detections/${i}/confirm`))); toast(`${r.filter((x) => x.status === 'fulfilled').length} calls created. Choose teams for them on the dashboard's Dispatch panel.`, 'ok', 8000); break; }
      case 'zoomdet': { const x = S.detections.get(id); if (x) layers.flyTo(x.lon, x.lat, 18); break; }
      case 'mview': ui.modelView = el.dataset.v; await loadHeat(ui.modelView === 'updated' ? 'updated' : 'modelled'); toast(ui.modelView === 'updated' ? 'The map shows the drone-updated picture.' : 'The map shows the modelled picture.'); paintTab(); break;
      case 'mrefresh': await refreshModel(); break;
      case 'fb': await jpost('/model/enable', { enabled: el.checked }); S.config.feedback_enabled = el.checked; await refreshModel(); if (ui.modelView === 'updated') await loadHeat('updated'); break;
      case 'pair': await newPair(el.dataset.role, el.dataset.page); break;
      case 'revoke': await jdel(`/sessions/${id}`); break;
      default: break;
    }
  } catch (e) { toast(e.message || String(e), 'bad'); }
}
async function reorder(id, dir) {
  const q = S.queue.filter((t) => !t.skipped).slice(0, 12), i = q.findIndex((t) => t.id === id), j = i + dir;
  if (i < 0 || j < 0 || j >= q.length) return;
  [q[i], q[j]] = [q[j], q[i]];
  await Promise.all(q.map((t, k) => jpatch(`/tasks/${t.id}`, { manual_rank: k })));
}
async function refreshModel() { try { ui.summary = await jget('/model/summary'); } catch (e) { /* not ready */ } paintTab(); }
async function newPair(role, page = 'pilot') {
  const c = await jpost('/pair/code', { role });
  const a = ui.area || {};
  const path = page === 'fpv' ? '/fpv?code=' + c.code : '/pilot';
  ui.pair = { ...c, page, url: a.lan_ip ? (a.https_port ? `https://${a.lan_ip}:${a.https_port}${path}` : `http://${a.lan_ip}:${location.port || 80}${path}`) : null };
  ui.tab = 'tablet'; paintAll(true); setTimeout(() => qr(ui.pair.url || location.origin + path), 60);
  clearTimeout(ui.pairT); ui.pairT = setTimeout(() => { ui.pair = null; paintTab(); }, c.expires_in * 1000);
}
function qr(text) {
  const box = $('qr'); if (!box) return;
  const draw = () => { try { const q = window.qrcode(0, 'M'); q.addData(text); q.make(); box.innerHTML = q.createSvgTag({ cellSize: 4, margin: 2 }); } catch (e) { box.textContent = text; } };
  if (window.qrcode) return draw();
  const sc = document.createElement('script'); sc.src = 'https://cdn.jsdelivr.net/npm/qrcode-generator@1.4.4/qrcode.js'; sc.onload = draw; sc.onerror = () => { box.textContent = text; }; document.head.appendChild(sc);
}
function moreMenu(btn) {
  const old = document.getElementById('moreMenu'); if (old) { old.remove(); return; }
  const m = document.createElement('div'); m.id = 'moreMenu'; m.className = 'menu'; m.setAttribute('role', 'menu');
  const r = btn.getBoundingClientRect(); m.style.top = r.bottom + 8 + 'px'; m.style.right = innerWidth - r.right + 'px';
  const dl = [['Sightings (GeoJSON)', '/api/drone/export/victims.geojson'], ['Coverage (GeoJSON)', '/api/drone/export/coverage.geojson'], ['Point cloud (.ply)', '/api/drone/export/pointcloud.ply'], ['Event log (ndjson)', '/api/drone/export/events.ndjson']];
  m.innerHTML = `<h3>Export</h3>${dl.map(([l, u]) => `<a href="${u}" download role="menuitem">${icon('download')}${l}</a>`).join('')}<h3>Replay</h3><button data-m="save" role="menuitem">${icon('record')}Save this run</button><button data-m="play" role="menuitem">${icon('play')}Replay the last run</button><div id="replayList"></div><h3>Scenario</h3><button data-m="reroll" role="menuitem">${icon('refresh')}Re-roll the simulated outcome</button>`;
  document.body.appendChild(m);
  jget('/replay/list').then((l) => { const el = $('replayList'); if (el) el.innerHTML = l.map((n) => `<button data-m="play:${esc(n)}" role="menuitem">${icon('play')}${esc(n)}</button>`).join(''); }).catch(() => {});
  const close = (e) => { if (!m.contains(e.target) && e.target !== btn && !btn.contains(e.target)) { m.remove(); document.removeEventListener('pointerdown', close); } };
  setTimeout(() => document.addEventListener('pointerdown', close), 0);
  m.onclick = async (e) => {
    const b = e.target.closest('[data-m]'); if (!b) return; const k = b.dataset.m;
    try {
      if (k === 'save') { const x = await jpost('/replay/save', { name: 'run-' + new Date().toISOString().slice(11, 19).replace(/:/g, '') }); toast(`Saved ${x.messages} messages as ${x.saved}.`); }
      else if (k.startsWith('play')) { await jpost('/replay/start', { name: k.includes(':') ? k.split(':')[1] : undefined, speed: 1 }); toast('Replaying a recorded simulated run.', 'warn'); }
      else if (k === 'reroll' && confirm('Re-roll the simulated outcome? Everything the drones have scanned is reset.')) { await jpost('/truth/reroll', {}); toast('New simulated outcome. Scan state reset.', 'warn'); }
    } catch (er) { toast(er.message, 'bad'); }
    m.remove();
  };
}
