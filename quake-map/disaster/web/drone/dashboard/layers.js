// The drone on the main MapLibre map: stations, drone icon, trail, camera footprint, route, reach rings, coverage, scanned buildings,
// sightings, drone-verified blocked streets. All sources are GeoJSON, updated by throttled setData (the drone at 10 Hz, the rest slower).
// Hidden truth never reaches here: only what the server reported as observed.
import { jget } from '../shared/api.js';

const EMPTY = { type: 'FeatureCollection', features: [] };
const SRC = ['drn-reach', 'drn-cells', 'drn-bld', 'drn-roads', 'drn-cone', 'drn-route', 'drn-trail', 'drn-stations', 'drn-victims', 'drn-pos'];
export const GROUPS = { drone: ['drn-pos', 'drn-trail', 'drn-cone'], route: ['drn-route'], reach: ['drn-reach'], coverage: ['drn-cells', 'drn-bld-f', 'drn-bld-l', 'drn-bld-p'],
  victims: ['drn-victims'], roads: ['drn-roads'], stations: ['drn-stations'] };

export class MapLayers {
  constructor(store, getMap) {
    this.S = store; this.getMap = getMap; this.vis = { drone: true, route: true, reach: true, coverage: true, victims: true, roads: true, stations: true };
    this.dirty = new Set(['all']); this.bld = new Map(); this.need = new Set(); this.missing = new Set(); this.fetching = false; this.lastCells = -1; this.lastFlush = {};
    this.timer = setInterval(() => this.tick(), 100);
  }
  map() { try { return this.getMap(); } catch (e) { return null; } }
  ready() { const m = this.map(); return !!(m && m.getStyle && ((m.style && m.style._loaded === true) || m.isStyleLoaded())); }
  mark(...k) { k.forEach((x) => this.dirty.add(x)); }
  set(id, data) { const s = this.map().getSource(id); if (s) s.setData(data); }

  ensure() {
    const m = this.map();
    if (m.getSource('drn-pos')) return;
    SRC.forEach((id) => m.addSource(id, { type: 'geojson', data: EMPTY }));
    const L = (o) => m.addLayer(o);
    L({ id: 'drn-reach', type: 'line', source: 'drn-reach', paint: { 'line-color': ['match', ['get', 'name'], 'r_max', '#6c8aa8', '10 min', '#3aa655', '#f2b705'], 'line-width': 1.2, 'line-dasharray': [3, 3], 'line-opacity': 0.8 } });
    L({ id: 'drn-cells', type: 'fill', source: 'drn-cells', paint: { 'fill-color': '#2b9bd6', 'fill-opacity': 0.13 } });
    L({ id: 'drn-bld-f', type: 'fill', source: 'drn-bld', paint: { 'fill-color': ['match', ['get', 'o'], 2, '#d7263d', 1, '#f2b705', 0, '#3aa655', '#4aa3ff'], 'fill-opacity': ['match', ['get', 'o'], 2, 0.62, 1, 0.45, 0, 0.35, 0.18] } });
    L({ id: 'drn-bld-l', type: 'line', source: 'drn-bld', filter: ['==', ['get', 's'], 2], paint: { 'line-color': ['match', ['get', 'o'], 2, '#8d0f22', 1, '#9a7200', 0, '#1f7a3a', '#1668b8'], 'line-width': ['match', ['get', 'o'], 2, 2.2, 1.2] }, minzoom: 14 });
    L({ id: 'drn-bld-p', type: 'line', source: 'drn-bld', filter: ['==', ['get', 's'], 1], paint: { 'line-color': '#1668b8', 'line-width': 1, 'line-dasharray': [2, 2] }, minzoom: 14 });
    L({ id: 'drn-roads', type: 'line', source: 'drn-roads', paint: { 'line-color': '#d7263d', 'line-width': 4, 'line-dasharray': [1.2, 1.2] } });
    L({ id: 'drn-cone', type: 'fill', source: 'drn-cone', paint: { 'fill-color': '#ffd24a', 'fill-opacity': 0.22 } });
    L({ id: 'drn-route', type: 'line', source: 'drn-route', paint: { 'line-color': '#1668b8', 'line-width': 2, 'line-dasharray': [2, 2], 'line-opacity': 0.8 } });
    L({ id: 'drn-trail', type: 'line', source: 'drn-trail', paint: { 'line-color': '#2b9bd6', 'line-width': 2.5, 'line-opacity': 0.9 } });
    L({ id: 'drn-stations', type: 'circle', source: 'drn-stations', paint: { 'circle-radius': 6, 'circle-color': ['case', ['get', 'available'], '#1668b8', '#8a8a8a'], 'circle-stroke-color': '#fff', 'circle-stroke-width': 2 } });
    L({ id: 'drn-victims', type: 'circle', source: 'drn-victims', paint: { 'circle-radius': ['interpolate', ['linear'], ['get', 'people'], 1, 7, 6, 12], 'circle-color': ['match', ['get', 'status'], 'confirmed', '#ff5a4d', 'merged', '#4aa3ff', '#ffd24a'], 'circle-stroke-color': '#1c2630', 'circle-stroke-width': 2, 'circle-opacity': 0.92 } });
    L({ id: 'drn-pos', type: 'circle', source: 'drn-pos', paint: { 'circle-radius': 9, 'circle-color': '#1668b8', 'circle-opacity': 0.25, 'circle-stroke-color': '#1668b8', 'circle-stroke-width': 1.5 } });
    this.applyVisibility();
  }

  // HTML markers: this map style has no font glyphs, so labels and the heading arrow are DOM elements.
  syncMarkers(kind, items, make, update) {
    const m = this.map(); this.markers = this.markers || new Map();
    const seen = new Set();
    for (const it of items) {
      const key = kind + ':' + it.key; seen.add(key);
      let mk = this.markers.get(key);
      if (!mk) { const el = make(it); mk = { el, marker: new maplibregl.Marker({ element: el, rotationAlignment: 'map', pitchAlignment: 'map' }).setLngLat([it.lon, it.lat]).addTo(m) }; this.markers.set(key, mk); }
      mk.marker.setLngLat([it.lon, it.lat]); update(mk, it);
    }
    for (const [key, mk] of this.markers) if (key.startsWith(kind + ':') && !seen.has(key)) { mk.marker.remove(); this.markers.delete(key); }
  }
  hideMarkers(kind, on) { (this.markers || new Map()).forEach((mk, k) => { if (k.startsWith(kind + ':')) mk.el.style.display = on ? '' : 'none'; }); }
  applyVisibility() {
    const m = this.map(); if (!m) return;
    for (const [g, ids] of Object.entries(GROUPS)) ids.forEach((id) => { if (m.getLayer(id)) m.setLayoutProperty(id, 'visibility', this.vis[g] ? 'visible' : 'none'); });
    this.hideMarkers('drone', this.vis.drone); this.hideMarkers('station', this.vis.stations); this.hideMarkers('find', this.vis.victims);
  }
  setVisible(g, on) { this.vis[g] = on; if (this.ready() && this.map().getSource('drn-pos')) this.applyVisibility(); }

  tick() {
    if (!this.ready()) return;
    if (!this.map().getSource('drn-pos')) { try { this.ensure(); } catch (e) { return; } this.dirty.add('all'); }
    const all = this.dirty.has('all');
    const now = performance.now(), due = (k, ms) => (all || this.dirty.has(k)) && now - (this.lastFlush[k] || 0) >= ms;
    const S = this.S;
    if (due('pos', 90)) {
      const air = S.drones().filter((d) => ['takeoff', 'transit', 'surveying', 'returning', 'landing', 'hold'].includes(d.status));
      this.syncMarkers('drone', air.map((d) => ({ key: d.id, lon: d.lon, lat: d.lat, d })), (it) => { const el = document.createElement('div'); el.className = 'dr-mk drone'; el.innerHTML = '<svg viewBox="-16 -16 32 32" width="30" height="30"><circle r="14" fill="#fff" fill-opacity=".92"/><path d="M0-11L8 9L0 4L-8 9Z" fill="#1668b8"/></svg><span></span>'; return el; },
        (mk, it) => { mk.marker.setRotation(it.d.hdg); const sp = mk.el.querySelector('span'); sp.textContent = `${this.stationName(it.d)} ${Math.round(it.d.pct)}%`; sp.style.transform = `rotate(${-it.d.hdg}deg)`; mk.el.classList.toggle('manual', it.d.mode === 'manual'); });
      this.hideMarkers('drone', this.vis.drone);
    }
    if (due('pos', 90)) { this.lastFlush.pos = now; this.set('drn-pos', this.fc(S.drones().filter((d) => ['takeoff', 'transit', 'surveying', 'returning', 'landing', 'hold'].includes(d.status)).map((d) => this.pt(d.lon, d.lat, { hdg: d.hdg, pct: Math.round(d.pct), name: this.stationName(d), id: d.id })))); this.dirty.delete('pos'); }
    if (due('trail', 400)) { this.lastFlush.trail = now; this.set('drn-trail', this.fc([...S.trails].filter(([, t]) => t.length > 1).map(([id, t]) => this.line(t.map((p) => [p[3], p[4]]), { id })))); this.dirty.delete('trail'); }
    if (due('cone', 250)) { this.lastFlush.cone = now; this.set('drn-cone', this.fc([...S.cones.values()].filter((c) => c.scanning && S.fleet.get(c.drone) && S.fleet.get(c.drone).z > 2).map((c) => ({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [c.ring] } })))); this.dirty.delete('cone'); }
    if (due('route', 500)) { this.lastFlush.route = now; this.set('drn-route', this.fc([...S.routes].filter(([, p]) => p.length > 1).map(([id, p]) => this.line(p.map((q) => [q[0], q[1]]), { id })))); this.dirty.delete('route'); }
    if (due('stations', 800)) {
      this.syncMarkers('station', [...S.stations.values()].map((x) => ({ key: x.station, lon: x.lon, lat: x.lat, x })), () => { const el = document.createElement('div'); el.className = 'dr-mk station'; el.innerHTML = '<i></i><span></span>'; return el; },
        (mk, it) => { mk.el.querySelector('span').textContent = it.x.name.replace(/police( station)?/i, '').trim().slice(0, 16) || 'Police'; mk.el.classList.toggle('off', !it.x.available); });
      this.hideMarkers('station', this.vis.stations);
    }
    if (due('stations', 800)) { this.lastFlush.stations = now; this.set('drn-stations', this.fc([...S.stations.values()].map((s) => this.pt(s.lon, s.lat, { label: s.name, available: s.available, spare: s.spare })))); this.dirty.delete('stations'); }
    if (due('reach', 800)) {
      this.lastFlush.reach = now;
      const f = []; (S.reach || []).filter((r) => r.available).forEach((r) => r.rings.forEach((g) => f.push(this.line(g.ring, { name: g.name, label: g.name === 'r_max' ? 'max range' : g.name }))));
      this.set('drn-reach', this.fc(f)); this.dirty.delete('reach');
    }
    if (due('victims', 500)) {
      this.syncMarkers('find', [...S.detections.values()].filter((d) => d.status !== 'rejected').map((d) => ({ key: d.id, lon: d.lon, lat: d.lat, d })), () => { const el = document.createElement('div'); el.className = 'dr-mk find'; return el; },
        (mk, it) => { mk.el.textContent = it.d.est_people; mk.el.classList.remove('unverified', 'confirmed', 'merged'); mk.el.classList.add(it.d.status); });
      this.hideMarkers('find', this.vis.victims);
    }
    if (due('victims', 500)) { this.lastFlush.victims = now; this.set('drn-victims', this.fc([...S.detections.values()].filter((d) => d.status !== 'rejected').map((d) => this.pt(d.lon, d.lat, { status: d.status, people: d.est_people, id: d.id })))); this.dirty.delete('victims'); }
    if (due('roads', 800)) { this.lastFlush.roads = now; this.set('drn-roads', this.fc([...S.roads.values()].filter((r) => r.blocked && r.line).map((r) => this.line(r.line, { road: r.road })))); this.dirty.delete('roads'); }
    if (due('cells', 2000) && S.cells.size !== this.lastCells) {
      this.lastCells = S.cells.size; this.lastFlush.cells = now;
      const out = [];
      for (const [, c] of S.cells) { const dl = 25 / (111320 * Math.cos(c[3] * Math.PI / 180)), dp = 25 / 110574; out.push({ type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [[[c[2] - dl, c[3] - dp], [c[2] + dl, c[3] - dp], [c[2] + dl, c[3] + dp], [c[2] - dl, c[3] + dp], [c[2] - dl, c[3] - dp]]] } }); }
      this.set('drn-cells', this.fc(out)); this.dirty.delete('cells');
    }
    if (all || this.dirty.has('bld')) { this.flushBuildings(); }
    this.dirty.delete('all');
  }

  async flushBuildings() {
    const S = this.S;
    for (const [id, b] of S.buildings) {
      const f = this.bld.get(id);
      if (f) { if (f.properties.s !== b.s || f.properties.o !== (b.o ?? null)) { f.properties.s = b.s; f.properties.o = b.o ?? null; this.dirty.add('bldpush'); } } else if (!this.missing.has(id)) this.need.add(id);
    }
    if (this.need.size && !this.fetching) {
      this.fetching = true;
      const ids = [...this.need].slice(0, 400); ids.forEach((i) => this.need.delete(i));
      try {
        const g = await jget('/scan/buildings?ids=' + ids.join(','));
        g.features.forEach((f) => { const b = S.buildings.get(f.properties.id); if (b) { f.properties.s = b.s; f.properties.o = b.o ?? null; } this.bld.set(f.properties.id, f); });
        const got = new Set(g.features.map((f) => f.properties.id)); ids.forEach((i) => { if (!got.has(i)) this.missing.add(i); });      // no footprint: do not ask again
        this.dirty.add('bldpush');
      } catch (e) { /* retry on the next scan message */ ids.forEach((i) => this.need.add(i)); }
      this.fetching = false;
    }
    if (this.dirty.has('bldpush') || !this.need.size) {
      if (this.dirty.has('bldpush')) { this.set('drn-bld', this.fc([...this.bld.values()])); this.dirty.delete('bldpush'); }
    }
    this.dirty.delete('bld');
  }
  async loadScanned() {                                    // after a page reload the server already has scan state
    try { const g = await jget('/scan/geojson'); g.features.forEach((f) => this.bld.set(f.properties.id, f)); this.dirty.add('bldpush'); this.dirty.add('bld'); } catch (e) { /* none yet */ }
  }
  reset() { this.bld.clear(); this.need.clear(); this.missing.clear(); this.lastCells = -1; this.dirty.add('all'); this.dirty.add('bldpush'); }

  stationName(d) { if (d.label) return d.label; const s = this.S.stations.get(d.station); return s ? s.name.replace(/police( station)?/i, '').trim().slice(0, 18) || 'Drone' : 'Drone'; }
  fc(features) { return { type: 'FeatureCollection', features }; }
  pt(lon, lat, props) { return { type: 'Feature', properties: props, geometry: { type: 'Point', coordinates: [lon, lat] } }; }
  line(coords, props) { return { type: 'Feature', properties: props, geometry: { type: 'LineString', coordinates: coords } }; }
  flyTo(lon, lat, zoom = 16) { const m = this.map(); if (m) m.flyTo({ center: [lon, lat], zoom: Math.max(m.getZoom(), zoom), duration: 900 }); }
}
