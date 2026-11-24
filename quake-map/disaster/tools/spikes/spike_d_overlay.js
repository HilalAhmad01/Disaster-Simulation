// Spike D (drone Phase 0): does a small three.js canvas floating over the real dashboard hurt the 3D map?
//
// Run the dashboard (uvicorn app:app --port 8000) and this spike's server (spike_e_server.py --three ...), open the dashboard,
// press 3D on the map toolbar, then paste this whole file into the browser console (F12).
//   - It adds a 320x200 canvas bottom-right with ~800 instanced boxes and a dummy drone flying circles over the map.
//   - It prints the map's frame rate and the overlay's frame rate for 10 s with and without the overlay, then removes itself.
// Throw-away: it changes no project file and leaves nothing behind after it finishes.
(async () => {
  const SPIKE = 'http://localhost:8001';            // change to the laptop's address if the dashboard is opened from elsewhere
  const THREE = await import(SPIKE + '/vendor/three.module.js');
  // The dashboard declares `let map`, which is reachable from the console but is NOT window.map (window.map is the <div id="map">).
  let m = null; try { m = typeof map !== 'undefined' ? map : null; } catch (e) { /* not defined yet */ }
  if (!m || typeof m.jumpTo !== 'function') { console.error('[spike D] no MapLibre `map` found: open the dashboard, run a simulation, press 3D, then paste again'); return; }
  const pct = (a, p) => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(p * s.length)]; };
  const measure = ms => new Promise(res => { const d = []; let last = performance.now(); const t0 = last;
    const f = t => { d.push(t - last); last = t; (t - t0 < ms) ? requestAnimationFrame(f) : res(d); }; requestAnimationFrame(f); });
  // keep the map redrawing during the test so its frame rate means something
  let k = 0, pan; const wiggle = () => { k += 0.004; m.jumpTo({ bearing: Math.sin(k) * 25 }); pan = requestAnimationFrame(wiggle); };
  wiggle();
  const base = await measure(10000);
  const fps = d => (1000 / (d.slice(1).reduce((a, b) => a + b, 0) / (d.length - 1))).toFixed(1);
  console.log(`[spike D] map alone: ${fps(base)} fps, p95 frame ${pct(base, .95).toFixed(1)} ms`);

  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;right:12px;bottom:40px;width:320px;height:200px;z-index:99999;border:1px solid #46596b;border-radius:8px;overflow:hidden;background:#000';
  const cv = document.createElement('canvas'); cv.style.cssText = 'width:100%;height:100%'; host.appendChild(cv); document.body.appendChild(host);
  const r = new THREE.WebGLRenderer({ canvas: cv, antialias: false }); r.setPixelRatio(1); r.setSize(320, 200, false);
  const sc = new THREE.Scene(); sc.background = new THREE.Color(0x0b1218); const cam = new THREE.PerspectiveCamera(60, 1.6, 0.5, 600);
  const N = 800, im = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({ color: 0x6f8fb0 }), N); const mx = new THREE.Matrix4();
  for (let i = 0; i < N; i++) { const h = 3 + Math.random() * 15; mx.compose(new THREE.Vector3((Math.random() - .5) * 500, h / 2, (Math.random() - .5) * 500), new THREE.Quaternion(), new THREE.Vector3(8 + Math.random() * 14, h, 8 + Math.random() * 14)); im.setMatrixAt(i, mx); }
  sc.add(im); const drone = new THREE.Mesh(new THREE.BoxGeometry(2, .6, 2), new THREE.MeshBasicMaterial({ color: 0xffd166 })); sc.add(drone);
  let a = 0, run = true; const loop = () => { if (!run) return; a += .01; drone.position.set(Math.cos(a) * 90, 30, Math.sin(a) * 90); cam.position.set(Math.cos(a) * 90 - 14, 38, Math.sin(a) * 90 - 14); cam.lookAt(drone.position); r.render(sc, cam); requestAnimationFrame(loop); };
  loop();
  const withOv = await measure(10000);
  console.log(`[spike D] map + overlay: ${fps(withOv)} fps, p95 frame ${pct(withOv, .95).toFixed(1)} ms   (drop ${(100 - 100 * fps(withOv) / fps(base)).toFixed(0)} %)`);
  run = false; cancelAnimationFrame(pan); r.dispose(); host.remove();
  console.log('[spike D] done; overlay removed. Report both numbers. Target: map stays >= 30 fps with the overlay on.');
})();
