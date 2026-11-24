// The drone's eye: a small three.js scene (z up, local metres). Buildings, rubble and street debris from chunks; the drone; its trail; the
// lidar point cloud (drawn where the drone *believes* it is, so drift smears the map); sighting pins. Built to be cheap: pixel ratio 1,
// no shadows, instanced meshes, frame cap, and it stops drawing when hidden.
import * as THREE from '../vendor/three.module.js';
import { ChunkCache } from './chunks.js';

const KIND_COLOR = [0x7d8ea3, 0xb48a55, 0x6b5545, 0xd9822b];     // intact, partly collapsed, rubble, street debris
const PIN_COLOR = { unverified: 0xffd24a, confirmed: 0xff5a4d, merged: 0x4aa3ff, rejected: 0x666666 };

export class DroneView {
  constructor(canvas, { fps = 30, dpr = 1, chunks } = {}) {
    this.canvas = canvas; this.fps = fps; this.dpr = dpr; this.mode = 'chase'; this.active = true; this.visible = true;
    this.chunks = chunks || new ChunkCache();
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: false, alpha: false, powerPreference: 'high-performance' });
    this.renderer.setPixelRatio(dpr);
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0x0c1218); this.scene.fog = new THREE.Fog(0x0c1218, 140, 520);
    this.camera = new THREE.PerspectiveCamera(62, 16 / 9, 0.5, 900); this.camera.up.set(0, 0, 1); this.camera.position.set(0, -20, 12);
    this.scene.add(new THREE.AmbientLight(0xffffff, 1.0));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6); sun.position.set(0.6, -0.5, 1.2); this.scene.add(sun);

    const ground = new THREE.Mesh(new THREE.PlaneGeometry(6000, 6000), new THREE.MeshBasicMaterial({ color: 0x141c23 })); ground.position.z = -0.05; this.scene.add(ground);
    this.grid = new THREE.GridHelper(2000, 100, 0x25313c, 0x1b252e); this.grid.rotation.x = Math.PI / 2; this.grid.position.z = -0.02; this.scene.add(this.grid);

    this.world = new THREE.Group(); this.scene.add(this.world);
    this.roadLines = null; this.meshes = [];
    this.drone = this.makeDrone(); this.scene.add(this.drone);
    this.alt = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 0, -1)]), new THREE.LineBasicMaterial({ color: 0x5fd0ff, transparent: true, opacity: 0.5 }));
    this.scene.add(this.alt);
    this.blob = new THREE.Mesh(new THREE.CircleGeometry(1.4, 20), new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.45 })); this.scene.add(this.blob);

    this.MAXP = 200000; this.pn = 0;
    const pg = new THREE.BufferGeometry();
    this.ppos = new Float32Array(this.MAXP * 3); this.pcol = new Float32Array(this.MAXP * 3);
    pg.setAttribute('position', new THREE.BufferAttribute(this.ppos, 3).setUsage(THREE.DynamicDrawUsage));
    pg.setAttribute('color', new THREE.BufferAttribute(this.pcol, 3).setUsage(THREE.DynamicDrawUsage));
    pg.setDrawRange(0, 0);
    this.points = new THREE.Points(pg, new THREE.PointsMaterial({ size: 1.1, vertexColors: true, sizeAttenuation: true })); this.points.frustumCulled = false; this.scene.add(this.points);
    this.pointsOnly = false;

    this.trail = this.makeLine(400, 0x5fd0ff); this.believed = this.makeLine(400, 0xffa94d);
    this.pins = new THREE.Group(); this.scene.add(this.pins); this.pinMap = new Map();
    this.target = new THREE.Vector3(); this.goal = new THREE.Vector3(); this.goalYaw = 0; this.have = false; this.yaw = 0; this.look = { az: 0, el: 0 }; this.camGoal = new THREE.Vector3();
    this.last = 0; this.raf = 0; this.rebuildT = 0;
    this.chunks.onChange(() => this.scheduleRebuild());
    this.attachInput();
    if ('IntersectionObserver' in window) new IntersectionObserver((e) => { this.visible = e[0].isIntersecting; }).observe(canvas);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) this.loop(); });
    this.resize(); this.loop();
  }

  makeDrone() {
    const g = new THREE.Group();
    const body = new THREE.Mesh(new THREE.BoxGeometry(1.4, 0.7, 0.3), new THREE.MeshLambertMaterial({ color: 0x2f9bd6 })); g.add(body);
    for (const a of [Math.PI / 4, -Math.PI / 4]) { const arm = new THREE.Mesh(new THREE.BoxGeometry(2.6, 0.14, 0.1), new THREE.MeshLambertMaterial({ color: 0x20262c })); arm.rotation.z = a; g.add(arm); }
    for (const [x, y] of [[0.95, 0.95], [-0.95, 0.95], [0.95, -0.95], [-0.95, -0.95]]) {
      const r = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.03, 16), new THREE.MeshBasicMaterial({ color: 0xbfe9ff, transparent: true, opacity: 0.35 }));
      r.rotation.x = Math.PI / 2; r.position.set(x, y, 0.15); g.add(r);
    }
    const nose = new THREE.Mesh(new THREE.ConeGeometry(0.28, 0.7, 10), new THREE.MeshBasicMaterial({ color: 0xffd24a })); nose.rotation.z = -Math.PI / 2; nose.position.set(1.0, 0, 0); g.add(nose);
    g.scale.setScalar(1.8);
    return g;
  }
  makeLine(n, color) {
    const geo = new THREE.BufferGeometry(); geo.setAttribute('position', new THREE.BufferAttribute(new Float32Array(n * 3), 3)); geo.setDrawRange(0, 0);
    const l = new THREE.Line(geo, new THREE.LineBasicMaterial({ color, transparent: true, opacity: 0.85 })); l.frustumCulled = false; this.scene.add(l); return l;
  }

  // ---------------------------------------------------------------- world
  scheduleRebuild() { clearTimeout(this.rebuildT); this.rebuildT = setTimeout(() => this.rebuild(), 120); }
  rebuild() {
    for (const m of this.meshes) { this.world.remove(m); m.geometry.dispose(); m.material.dispose(); }
    this.meshes = [];
    if (this.roadLines) { this.world.remove(this.roadLines); this.roadLines.geometry.dispose(); this.roadLines = null; }
    const by = [[], [], [], []], seg = [];
    for (const c of this.chunks.all()) {
      for (const b of c.boxes) by[Math.min(3, Math.max(0, b[7] | 0))].push(b);
      for (const r of c.roads) for (let k = 0; k + 1 < r.pts.length; k++) seg.push(r.pts[k][0], r.pts[k][1], 0.06, r.pts[k + 1][0], r.pts[k + 1][1], 0.06);
    }
    const geo = new THREE.BoxGeometry(1, 1, 1), m4 = new THREE.Matrix4(), q = new THREE.Quaternion(), col = new THREE.Color(), zax = new THREE.Vector3(0, 0, 1);
    by.forEach((list, kind) => {
      if (!list.length) return;
      const im = new THREE.InstancedMesh(geo, new THREE.MeshLambertMaterial({ color: 0xffffff }), list.length);
      list.forEach((b, i) => {
        q.setFromAxisAngle(zax, b[6]); m4.compose(new THREE.Vector3(b[0], b[1], b[2]), q, new THREE.Vector3(b[3] * 2, b[4] * 2, b[5] * 2)); im.setMatrixAt(i, m4);
        col.setHex(KIND_COLOR[kind]); if (kind < 2) col.offsetHSL(0, 0, Math.min(b[5], 12) * 0.006 - 0.03); im.setColorAt(i, col);
      });
      im.instanceMatrix.needsUpdate = true; if (im.instanceColor) im.instanceColor.needsUpdate = true;
      this.world.add(im); this.meshes.push(im);
    });
    if (seg.length) {
      const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(seg, 3));
      this.roadLines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0x4b5a67 })); this.world.add(this.roadLines);
    }
    this.world.visible = !this.pointsOnly;
  }

  // ---------------------------------------------------------------- live data
  setDrone(d) {
    if (!d) return;
    // Telemetry only sets where the drone should be; step() glides the drone and the camera there, so nothing snaps at 10 updates a second.
    this.goal.set(d.x, d.y, d.z); this.goalYaw = d.yaw;
    if (!this.have || this.target.distanceTo(this.goal) > 60) { this.target.copy(this.goal); this.yaw = d.yaw; this.have = true; this.place(); }   // first sight or another drone: no long glide
    this.blob.scale.setScalar(Math.max(0.4, 1.6 - d.z / 120));
    if (d.z > 0.5) this.chunks.ensure(d.x, d.y);          // the server only serves chunks a drone has flown near
    this.drone.visible = d.z > 0.2;
  }
  place() {
    const t = this.target;
    this.drone.position.copy(t); this.drone.rotation.set(0, 0, this.yaw);
    this.blob.position.set(t.x, t.y, 0.05);
    const a = this.alt.geometry.attributes.position; a.setXYZ(0, t.x, t.y, t.z); a.setXYZ(1, t.x, t.y, 0); a.needsUpdate = true;
  }
  step(dt) {                                                  // time-based smoothing: the same feel at 30 or 60 frames a second
    if (!this.have) return;
    const k = 1 - Math.exp(-dt / 0.28);
    this.target.lerp(this.goal, k);
    let dy = this.goalYaw - this.yaw; dy = Math.atan2(Math.sin(dy), Math.cos(dy)); this.yaw += dy * k;
    this.place();
  }
  setTrail(pts, bias) {
    const set = (line, arr, off) => {
      const a = line.geometry.attributes.position; const n = Math.min(arr.length, a.count);
      for (let i = 0; i < n; i++) { const p = arr[arr.length - n + i]; a.setXYZ(i, p[0] + off[0], p[1] + off[1], p[2] + off[2]); }
      line.geometry.setDrawRange(0, n); a.needsUpdate = true;
    };
    set(this.trail, pts || [], [0, 0, 0]); set(this.believed, pts || [], bias || [0, 0, 0]);
    this.believed.visible = !!bias && (Math.abs(bias[0]) + Math.abs(bias[1]) > 0.8);
  }
  addPoints(flat) {
    for (let i = 0; i + 3 < flat.length; i += 4) {
      const k = this.pn % this.MAXP; const x = flat[i], y = flat[i + 1], z = flat[i + 2], kind = flat[i + 3];
      this.ppos[k * 3] = x; this.ppos[k * 3 + 1] = y; this.ppos[k * 3 + 2] = z;
      const h = Math.min(Math.max(z / 20, 0), 1), c = kind < 0 ? [0.35, 0.55, 0.4] : kind === 2 ? [0.9, 0.55, 0.3] : kind === 3 ? [1, 0.4, 0.2] : [0.3 + 0.6 * h, 0.75 - 0.2 * h, 1 - 0.5 * h];
      this.pcol[k * 3] = c[0]; this.pcol[k * 3 + 1] = c[1]; this.pcol[k * 3 + 2] = c[2]; this.pn++;
    }
    const g = this.points.geometry; g.attributes.position.needsUpdate = true; g.attributes.color.needsUpdate = true; g.setDrawRange(0, Math.min(this.pn, this.MAXP));
  }
  clearPoints() { this.pn = 0; this.points.geometry.setDrawRange(0, 0); }
  setPins(list) {
    const seen = new Set();
    for (const d of list) {
      seen.add(d.id);
      let p = this.pinMap.get(d.id);
      if (!p) {
        p = new THREE.Group();
        const cone = new THREE.Mesh(new THREE.ConeGeometry(1.1, 3.2, 10), new THREE.MeshBasicMaterial({ color: 0xffffff })); cone.rotation.x = Math.PI; cone.position.z = 4.6; p.add(cone);
        const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 40, 6), new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.45 })); beam.rotation.x = Math.PI / 2; beam.position.z = 20; p.add(beam);
        this.pins.add(p); this.pinMap.set(d.id, p);
      }
      p.position.set(d.x, d.y, 0); p.visible = d.status !== 'rejected';
      p.children.forEach((c) => c.material.color.setHex(PIN_COLOR[d.status] || 0xffffff));
    }
    for (const [id, p] of this.pinMap) if (!seen.has(id)) { this.pins.remove(p); this.pinMap.delete(id); }
  }
  setMode(m) { this.mode = m; this.pointsOnly = m === 'slam'; this.world.visible = !this.pointsOnly; this.grid.visible = !this.pointsOnly; this.points.material.size = this.pointsOnly ? 1.7 : 1.1; }

  // ---------------------------------------------------------------- camera, input, loop
  attachInput() {
    let drag = null;
    this.canvas.addEventListener('pointerdown', (e) => { if (e.pointerType === 'mouse' && e.button !== 0) return; drag = { x: e.clientX, y: e.clientY, az: this.look.az, el: this.look.el, id: e.pointerId }; });
    this.canvas.addEventListener('pointermove', (e) => { if (!drag || e.pointerId !== drag.id) return; this.look.az = drag.az - (e.clientX - drag.x) * 0.006; this.look.el = Math.max(-0.5, Math.min(1.1, drag.el + (e.clientY - drag.y) * 0.005)); });
    const end = (e) => { if (drag && e.pointerId === drag.id) drag = null; };
    this.canvas.addEventListener('pointerup', end); this.canvas.addEventListener('pointercancel', end);
    this.canvas.addEventListener('dblclick', () => { this.look.az = 0; this.look.el = 0; });
  }
  resize() {
    const w = this.canvas.clientWidth || 320, h = this.canvas.clientHeight || 200;
    if (this.canvas.width !== Math.floor(w * this.dpr) || this.canvas.height !== Math.floor(h * this.dpr)) { this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix(); }
  }
  updateCamera(dt = 0.033) {
    const t = this.target, a = this.yaw + this.look.az;
    let want, look = t.clone();
    if (this.mode === 'top') { want = new THREE.Vector3(t.x, t.y - 0.01, Math.max(140, t.z + 90)); this.camera.up.set(0, 1, 0); }
    else if (this.mode === 'fpv') { want = new THREE.Vector3(t.x + Math.cos(this.yaw) * 1.5, t.y + Math.sin(this.yaw) * 1.5, t.z + 0.3); look = new THREE.Vector3(t.x + Math.cos(a) * 30, t.y + Math.sin(a) * 30, t.z - 8 + this.look.el * 30); this.camera.up.set(0, 0, 1); }
    else {
      const dist = this.mode === 'slam' ? 46 : 14, h = (this.mode === 'slam' ? 26 : 6) + this.look.el * 20;
      want = new THREE.Vector3(t.x - Math.cos(a) * dist, t.y - Math.sin(a) * dist, t.z + h); look = new THREE.Vector3(t.x + Math.cos(a) * 4, t.y + Math.sin(a) * 4, t.z - 1); this.camera.up.set(0, 0, 1);
    }
    this.camera.position.lerp(want, this.mode === 'fpv' ? 1 : 1 - Math.exp(-dt / 0.22));      // the drone is already smoothed, so the front view can sit exactly on it
    this.camera.lookAt(look);
  }
  loop = () => {
    cancelAnimationFrame(this.raf);
    this.raf = requestAnimationFrame((now) => {
      if (this.disposed) return;
      const dt = Math.min(0.1, Math.max(0, (now - (this.prev || now)) / 1000)); this.prev = now;
      if (this.active && this.visible && !document.hidden) {
        this.step(dt);
        const gap = 1000 / this.fps;
        if (now - this.last >= gap - 3) {                     // a small tolerance, so a 30 fps cap on a 60 Hz screen is every second frame, not every third
          const frame = Math.min(0.1, (now - this.last) / 1000); this.last = now;
          this.resize(); this.updateCamera(frame); this.renderer.render(this.scene, this.camera);
        }
      }
      this.loop();
    });
  };
  dispose() { this.disposed = true; cancelAnimationFrame(this.raf); this.renderer.dispose(); }
}
