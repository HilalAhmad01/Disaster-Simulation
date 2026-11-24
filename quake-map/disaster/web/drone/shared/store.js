// One client-side copy of the incident state, fed by the websocket. The dashboard and the tablet both use it, so they can never disagree
// about what the messages mean. The server owns the truth; this only mirrors what the server chose to send.
export class Store {
  constructor() { this.handlers = new Map(); this.reset(); }

  reset() {
    this.config = null; this.role = null; this.ready = false; this.needs = null; this.origin = null; this.replay = false;
    this.clock = { sim_t: 0, speed: 1, paused: false };
    this.fleet = new Map(); this.stations = new Map(); this.routes = new Map(); this.cones = new Map(); this.trails = new Map();
    this.queue = []; this.detections = new Map(); this.leases = new Map(); this.sessions = [];
    this.scan = null; this.cells = new Map(); this.buildings = new Map(); this.roads = new Map();
    this.model = null; this.auto = true; this.reach = []; this.alarms = []; this.session = null; this.sortiePlans = new Map();
  }

  on(type, fn) { if (!this.handlers.has(type)) this.handlers.set(type, new Set()); this.handlers.get(type).add(fn); return () => this.handlers.get(type).delete(fn); }
  emit(type, msg) { const s = this.handlers.get(type); if (s) s.forEach((f) => { try { f(msg, this); } catch (e) { console.error(e); } }); }

  apply(m) {
    switch (m.type) {
      case 'hello': this.config = m.config; this.role = m.role; this.ready = m.ready; this.needs = m.needs; this.session = m.session || null; break;
      case 'snapshot': this.load(m); break;
      case 'drone': {
        this.fleet.set(m.id, m);
        const t = this.trails.get(m.id) || [];
        const last = t[t.length - 1];
        if (m.status !== 'ready' && m.status !== 'offline' && m.z > 0.5 && (!last || Math.hypot(m.x - last[0], m.y - last[1]) > 2)) { t.push([m.x, m.y, m.z, m.lon, m.lat]); if (t.length > 400) t.shift(); }
        if (m.status === 'ready' || m.status === 'swapping') this.trails.set(m.id, []); else this.trails.set(m.id, t);
        break;
      }
      case 'cone': this.cones.set(m.drone, m); break;
      case 'route': this.routes.set(m.drone, m.pts); this.routesXY = this.routesXY || new Map(); this.routesXY.set(m.drone, m.xy || []); break;
      case 'clock': this.clock = { sim_t: m.sim_t, speed: m.speed, paused: m.paused }; break;
      case 'queue': this.queue = m.tasks; break;
      case 'detection': this.detections.set(m.id, m); break;
      case 'station': this.stations.set(m.station, m); break;
      case 'lease': this.leases.set(m.drone, m); break;
      case 'sessions': this.sessions = m.sessions; break;
      case 'scan':
        m.cells.forEach((c) => this.cells.set(c[0] + ',' + c[1], c));
        m.buildings.forEach((b) => this.buildings.set(b.id, b));
        this.scan = m.summary; break;
      case 'model': this.model = { ...(this.model || {}), ...m }; break;
      case 'road': m.roads.forEach((r) => this.roads.set(r.road, r)); break;
      case 'sortie_plan': this.sortiePlans.set(m.drone, m.sortie); break;
      case 'auto': this.auto = m.enabled; break;
      case 'alarm': this.alarms.unshift({ ...m, at: Date.now() }); this.alarms.length = Math.min(this.alarms.length, 20); break;
      case 'replay_start': this.replay = true; this.reset(); this.replay = true; break;
      case 'replay_end': this.replay = false; break;
      case 'reset': this.fleet.clear(); this.trails.clear(); this.routes.clear(); this.cones.clear(); this.cells.clear(); this.buildings.clear(); this.detections.clear(); this.roads.clear(); break;
      default: break;
    }
    this.emit(m.type, m);
    this.emit('*', m);
  }

  load(s) {
    const replay = this.replay || !!s.replay;
    const keep = { config: this.config, role: this.role, session: this.session, sessions: this.sessions };
    this.reset();
    Object.assign(this, keep);
    this.replay = replay;
    this.ready = true; this.needs = null; this.config = s.config || this.config; this.origin = s.origin; this.clock = s.clock; this.auto = s.auto;
    s.fleet.forEach((d) => this.fleet.set(d.id, d)); s.stations.forEach((x) => this.stations.set(x.station, x));
    this.routesXY = new Map(); s.routes.forEach((r) => { this.routes.set(r.drone, r.pts); this.routesXY.set(r.drone, r.xy || []); }); this.queue = s.queue; s.detections.forEach((d) => this.detections.set(d.id, d));
    (s.cells || []).forEach((c) => this.cells.set(c[0] + ',' + c[1], c));
    this.scan = s.scan; this.model = s.model; this.reach = s.reach; (s.roads || []).forEach((r) => this.roads.set(r.road, r));
  }

  // ---- convenience for views
  drones() { return [...this.fleet.values()]; }
  airborne() { return this.drones().filter((d) => ['takeoff', 'transit', 'surveying', 'returning', 'landing', 'hold'].includes(d.status)); }
  active() { return this.airborne()[0] || this.drones().find((d) => d.status === 'launching') || this.drones().find((d) => d.status !== 'offline') || this.drones()[0] || null; }
  unverified() { return [...this.detections.values()].filter((d) => d.status === 'unverified'); }
}
