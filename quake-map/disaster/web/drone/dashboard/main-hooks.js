// The only drone code that runs on the main dashboard: a button to the drone page (with a count of sightings waiting for a decision), a
// "Send drone" button in the building card, and "Verify with drone" on a phone SOS. Everything else lives on /drone.
import { jget, jpost, esc } from '../shared/api.js';

const $ = (id) => document.getElementById(id);
let lastBld = null, cfg = null;

(async function boot() {
  try { cfg = await jget('/config'); } catch (e) { return; }
  if (!cfg.enabled) return;
  await new Promise((ok) => { (function w() { $('tbAct') && $('tbDisp') ? ok() : setTimeout(w, 200); })(); });
  const css = document.createElement('style');
  css.textContent = '#drBtn .badge{min-width:16px;height:16px;padding:0 4px;border-radius:8px;background:var(--red);color:#fff;font:500 11px/16px var(--mono);text-align:center}.dr-pop{margin-top:8px}.dr-pop button{width:auto;height:28px;margin:0;padding:0 10px;font-size:12px}.dr-pop .res{margin-top:6px;font-size:12px;line-height:16px;color:var(--text2)}';
  document.head.appendChild(css);
  const b = document.createElement('button');
  b.id = 'drBtn'; b.type = 'button'; b.className = 'alt'; b.title = 'Open the simulated drone console';
  b.innerHTML = '<svg class="i" viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="5" r="2.5"/><circle cx="19" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M7 7l3.5 3.5M17 7l-3.5 3.5M7 17l3.5-3.5M17 17l-3.5-3.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/></svg><span class="lbl">Drone</span><span class="badge" id="drBadge" hidden></span>';
  b.onclick = () => { location.href = '/drone'; };
  $('tbAct').insertBefore(b, $('tbDisp'));
  const poll = async () => {
    try { const c = await jget('/config'); if (c.bound) { const d = await jget('/detections'); const n = d.filter((x) => x.status === 'unverified').length; $('drBadge').hidden = !n; $('drBadge').textContent = n; } } catch (e) { /* quiet */ }
  };
  poll(); setInterval(poll, 5000);
  hooks();
})();

function hooks() {
  const wait = setInterval(() => {
    let m = null; try { m = typeof map !== 'undefined' ? map : null; } catch (e) { /* not yet */ }
    if (!m || !m.on) return; clearInterval(wait);
    m.on('click', (e) => { const f = m.queryRenderedFeatures(e.point, { layers: ['bldv-3d', 'bldv-2d', 'bld3d', 'far3d', 'bld'].filter((id) => m.getLayer(id) && m.getLayoutProperty(id, 'visibility') !== 'none') })[0]; lastBld = f && f.id != null ? f.id : null; });
  }, 400);
  new MutationObserver(() => {
    document.querySelectorAll('.maplibregl-popup-content').forEach((c) => {
      if (c.querySelector('[data-dr-send]') || !/Est\. occupants/.test(c.textContent) || lastBld == null) return;
      const d = document.createElement('div'); d.className = 'dr-pop'; d.innerHTML = `<button class="alt" data-dr-send="${lastBld}">Send a drone</button><div class="res"></div>`; c.appendChild(d);
    });
    const det = $('dDet');
    if (det && !det.hidden && det.querySelector('.dacts') && !det.querySelector('[data-dr-verify]') && typeof LIVE !== 'undefined' && LIVE.sel != null) {
      const s = LIVE.sos.get(LIVE.sel);
      if (s && s.status === 'new') { const b = document.createElement('button'); b.type = 'button'; b.className = 'alt'; b.dataset.drVerify = s.id; b.textContent = 'Verify with a drone'; det.querySelector('.dacts').appendChild(b); }
    }
  }).observe(document.body, { childList: true, subtree: true });
  document.addEventListener('click', async (e) => {
    const sb = e.target.closest('[data-dr-send]');
    if (sb) {
      const box = sb.parentElement.querySelector('.res'); box.textContent = 'Checking the energy budget…';
      try {
        await jpost('/start');
        const spec = { type: 'inspect_building', building: +sb.dataset.drSend }, p = await jpost('/tasks/preview', spec);
        if (!p.feasible) { box.textContent = 'This cannot be flown: ' + (p.reasons.join('; ') || 'not feasible'); return; }
        const o = p.best; box.innerHTML = `${esc(o.station)}: about ${Math.round(o.duration_s / 60)} min, ${Math.round(o.energy_wh)} Wh. <button class="alt" data-dr-go='${JSON.stringify(spec)}'>Confirm</button>`;
      } catch (er) { box.textContent = er.message; }
    }
    const go = e.target.closest('[data-dr-go]');
    if (go) { try { await jpost('/tasks', JSON.parse(go.dataset.drGo)); go.parentElement.innerHTML = 'A drone is launching. <a href="/drone">Watch it in the drone console</a>.'; } catch (er) { go.parentElement.textContent = er.message; } }
    const vb = e.target.closest('[data-dr-verify]');
    if (vb) { vb.disabled = true; try { await jpost('/start'); await jpost(`/calls/${vb.dataset.drVerify}/verify_with_drone`); vb.textContent = 'A drone is on its way'; } catch (er) { vb.disabled = false; vb.textContent = er.message; } }
  });
}
