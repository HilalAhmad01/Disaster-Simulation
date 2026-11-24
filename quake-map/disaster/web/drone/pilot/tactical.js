// Offline tactical map for the tablet: a plain 2D canvas drawn from the world chunks the drone has seen, so it needs no tile server and no
// internet. North up, centred on the drone. Shows buildings and rubble, streets, coverage, route, trail, sightings.
const SCALES = [0.35, 0.6, 1, 1.6, 2.6];                   // pixels per metre

export class Tactical {
  constructor(canvas, store, chunks) {
    this.c = canvas; this.g = canvas.getContext('2d'); this.S = store; this.chunks = chunks; this.zi = 2; this.sel = null; this.raf = 0; this.dirty = true;
    chunks.onChange(() => { this.dirty = true; });
    const loop = () => { this.raf = requestAnimationFrame(loop); if (this.dirty && !document.hidden) { this.dirty = false; this.draw(); } };
    loop();
  }
  zoom(d) { this.zi = Math.min(SCALES.length - 1, Math.max(0, this.zi + d)); this.dirty = true; }
  touch() { this.dirty = true; }

  draw() {
    const g = this.g, W = this.c.width, H = this.c.height, d = this.sel && this.S.fleet.get(this.sel);
    g.fillStyle = '#0c1218'; g.fillRect(0, 0, W, H);
    if (!d) return;
    const k = SCALES[this.zi] * (W / 220), cx = d.x, cy = d.y;
    const X = (x) => W / 2 + (x - cx) * k, Y = (y) => H / 2 - (y - cy) * k;
    // coverage
    g.fillStyle = 'rgba(43,155,214,.14)'; const s = 50 * k;
    for (const [, c] of this.S.cells) { const x = c[0] * 50, y = c[1] * 50; if (Math.abs(x - cx) * k < W && Math.abs(y - cy) * k < H) g.fillRect(X(x), Y(y + 50), s, s); }
    // streets
    g.strokeStyle = '#3d4b57'; g.lineWidth = Math.max(1, 3 * k * 0.5); g.beginPath();
    for (const ch of this.chunks.all()) for (const r of ch.roads) { r.pts.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1])))); }
    g.stroke();
    // buildings, rubble, debris
    for (const ch of this.chunks.all()) {
      for (const b of ch.boxes) {
        const px = X(b[0]), py = Y(b[1]); if (px < -40 || py < -40 || px > W + 40 || py > H + 40) continue;
        g.save(); g.translate(px, py); g.rotate(-b[6]);
        g.fillStyle = b[7] === 0 ? '#5d6e82' : b[7] === 1 ? '#a37b4c' : b[7] === 2 ? '#6a5242' : '#d9822b';
        g.fillRect(-b[3] * k, -b[4] * k, b[3] * 2 * k, b[4] * 2 * k); g.restore();
      }
    }
    // route, trail
    const xy = (this.S.routesXY && this.S.routesXY.get(d.id)) || [];
    if (xy.length > 1) { g.strokeStyle = 'rgba(74,163,255,.9)'; g.setLineDash([6, 5]); g.lineWidth = 1.5; g.beginPath(); xy.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1])))); g.stroke(); g.setLineDash([]); }
    const tr = this.S.trails.get(d.id) || [];
    if (tr.length > 1) { g.strokeStyle = '#5fd0ff'; g.lineWidth = 2; g.beginPath(); tr.forEach((p, i) => (i ? g.lineTo(X(p[0]), Y(p[1])) : g.moveTo(X(p[0]), Y(p[1])))); g.stroke(); }
    // sightings
    for (const f of this.S.detections.values()) {
      if (f.status === 'rejected') continue;
      g.fillStyle = f.status === 'confirmed' ? '#ff5a4d' : f.status === 'merged' ? '#4aa3ff' : '#ffd24a'; g.beginPath(); g.arc(X(f.x), Y(f.y), 6, 0, 7); g.fill();
      g.strokeStyle = '#111'; g.lineWidth = 1.5; g.stroke();
    }
    // camera footprint and the drone
    const cone = this.S.cones.get(d.id);
    if (cone && cone.scanning) { g.fillStyle = 'rgba(255,210,74,.2)'; g.beginPath(); g.arc(W / 2, H / 2, cone.radius * k, 0, 7); g.fill(); }
    g.save(); g.translate(W / 2, H / 2); g.rotate(-d.yaw + Math.PI / 2); g.fillStyle = '#fff'; g.beginPath(); g.moveTo(0, -11); g.lineTo(8, 9); g.lineTo(0, 5); g.lineTo(-8, 9); g.closePath(); g.fill(); g.restore();
    g.fillStyle = '#a1a1a6'; g.font = '11px ui-monospace,monospace'; g.fillText('N', W - 18, 16);
    const bar = 100 * k; g.fillStyle = '#a1a1a6'; g.fillRect(10, H - 14, bar, 2); g.fillText('100 m', 10, H - 20);
  }
}
