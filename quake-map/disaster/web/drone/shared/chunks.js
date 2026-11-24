// World chunks (buildings, rubble, street debris) fetched on demand around a drone and cached. The server only serves chunks a drone has
// been near, so this never shows an area nobody has flown over. Shared by the 3D view and the tactical map.
import { API } from './api.js';

export const CHUNK_M = 250;

export class ChunkCache {
  constructor({ radius = 140, keepRadius = 700, max = 30 } = {}) {
    this.map = new Map(); this.pending = new Set(); this.denied = new Map(); this.listeners = new Set();
    this.radius = radius; this.keepRadius = keepRadius; this.max = max; this.version = 0;
  }
  onChange(fn) { this.listeners.add(fn); return () => this.listeners.delete(fn); }
  key(i, j) { return i + '_' + j; }
  get(i, j) { return this.map.get(this.key(i, j)); }
  all() { return [...this.map.values()]; }

  /** Make sure the chunks within `radius` of (x, y) are loaded; drop far ones. */
  ensure(x, y) {
    const r = this.radius;
    for (let i = Math.floor((x - r) / CHUNK_M); i <= Math.floor((x + r) / CHUNK_M); i++) {
      for (let j = Math.floor((y - r) / CHUNK_M); j <= Math.floor((y + r) / CHUNK_M); j++) this.load(i, j);
    }
    let dropped = false;
    for (const [k, c] of this.map) {
      const cx = (c.i + 0.5) * CHUNK_M, cy = (c.j + 0.5) * CHUNK_M;
      if (Math.hypot(cx - x, cy - y) > this.keepRadius && this.map.size > this.max) { this.map.delete(k); dropped = true; }
    }
    if (dropped) this.changed();
  }

  async load(i, j) {
    const k = this.key(i, j);
    if (this.map.has(k) || this.pending.has(k)) return;
    const t = this.denied.get(k);
    if (t && Date.now() - t < 3000) return;
    this.pending.add(k);
    try {
      const r = await fetch(`${API}/chunk/${i}/${j}`);
      if (r.ok) { this.map.set(k, await r.json()); this.changed(); } else this.denied.set(k, Date.now());
    } catch (e) { this.denied.set(k, Date.now()); } finally { this.pending.delete(k); }
  }
  changed() { this.version++; this.listeners.forEach((f) => f(this)); }
  clear() { this.map.clear(); this.pending.clear(); this.denied.clear(); this.changed(); }
}
