/* Front-end layer for index.html (loaded after its script). Adds the topbar behaviour, map toolbar, ask box, dispatch extras (optimise, fleet,
   demo clock), AI report, aftershock, knock-on failures, uncertainty hatching, scenario compare, before/after swipe, fly-through, sound and the
   guided tour. It talks to the page through its globals (map, lastRun, LIVE, ...). Layout rules: one place for each thing, nothing floats over
   the map except its own toolbar, one notice or caption at a time. */
(()=>{
'use strict';
const reduce=matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmt=n=>n==null||!isFinite(n)?'–':Math.round(n).toLocaleString();
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const el=(tag,cls,html)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(html!=null)e.innerHTML=html;return e};
const when=(test,cb,ms=250)=>{(function w(){let ok=false;try{ok=test()}catch(e){}ok?cb():setTimeout(w,ms)})()};
// isStyleLoaded() is false while any tile is still loading (always, when offline), which must not block adding layers: ask the style itself
const mapReady=()=>{if(typeof map==='undefined'||!map)return false;try{return (map.style&&map.style._loaded===true)||map.isStyleLoaded()}catch(e){return false}};
const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});
let AREA_C=null,AI={claude:false};

/* ================================================================ icons: one set, 16px, 1.5 stroke */
const IC={panel:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16"/>',play:'<path d="M7 4.5l12 7.5-12 7.5z"/>',pause:'<path d="M9 5v14M15 5v14"/>',
  chat:'<path d="M20 15a2 2 0 0 1-2 2H8l-4 4V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2z"/>',file:'<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h4"/>',
  sliders:'<path d="M3 6h9M16 6h5M3 12h3M10 12h11M3 18h13M20 18h1"/><circle cx="14" cy="6" r="2"/><circle cx="8" cy="12" r="2"/><circle cx="18" cy="18" r="2"/>',
  list:'<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',cube:'<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
  split:'<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16M7 10l-2 2 2 2M17 10l2 2-2 2"/>',route:'<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6"/>',
  zap:'<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',compare:'<path d="M8 3L4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4"/>',x:'<path d="M6 6l12 12M18 6L6 18"/>',
  mic:'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',send:'<path d="M5 12h14M13 6l6 6-6 6"/>',back:'<path d="M15 6l-6 6 6 6"/>',next:'<path d="M9 6l6 6-6 6"/>'};
const icon=n=>`<svg class="i" viewBox="0 0 24 24" aria-hidden="true">${IC[n]}</svg>`;
const fillIcons=root=>(root||document).querySelectorAll('[data-i]').forEach(e=>e.outerHTML=icon(e.dataset.i));
fillIcons();

/* ================================================================ sound: soft tones, off with one switch in View */
const sfx=(()=>{
  let on=store.get('quake-sfx')!=='0';
  const ctx=()=>{try{return lvBeep.ctx||(lvBeep.ctx=new (window.AudioContext||window.webkitAudioContext)())}catch(e){return null}};
  const tone=(f,t0,d,type='sine',g=.06)=>{const a=ctx();if(!a||!on)return;const t=a.currentTime+t0,o=a.createOscillator(),v=a.createGain();o.type=type;o.frequency.setValueAtTime(f,t);
    v.gain.setValueAtTime(.0001,t);v.gain.exponentialRampToValueAtTime(g,t+.02);v.gain.exponentialRampToValueAtTime(.0001,t+d);o.connect(v).connect(a.destination);o.start(t);o.stop(t+d+.05)};
  return{get on(){return on},set(v){on=v;store.set('quake-sfx',v?'1':'0')},
    chime(){tone(659,0,.5);tone(988,.12,.8,'sine',.05)},                                   // a team was sent
    done(){tone(784,0,.25,'triangle');tone(1046,.14,.25,'triangle');tone(1318,.28,.55,'triangle',.045)},   // a team arrived
    alarm(){for(let i=0;i<4;i++){tone(880,i*.44,.2,'square',.04);tone(640,i*.44+.22,.2,'square',.04)}},     // an alert went out
    sos(){tone(1040,0,.12,'square',.045);tone(1040,.18,.12,'square',.045)},
    rumble(){const a=ctx();if(!a||!on)return;const n=a.sampleRate*1.6,b=a.createBuffer(1,n,a.sampleRate),d=b.getChannelData(0);for(let i=0;i<n;i++)d[i]=(Math.random()*2-1)*Math.pow(1-i/n,1.6);
      const s=a.createBufferSource(),f=a.createBiquadFilter(),g=a.createGain();s.buffer=b;f.type='lowpass';f.frequency.value=130;g.gain.value=1;s.connect(f).connect(g).connect(a.destination);s.start()}};
})();

/* ================================================================ helpers */
function countTo(e,to,fmtf=fmt){
  const from=+e.dataset.v||0;e.dataset.v=to;
  if(reduce||from===to||to==null){e.textContent=to==null?'–':fmtf(to);return}
  const t0=performance.now(),dur=700;cancelAnimationFrame(e._r);
  const st=n=>{const k=Math.min(1,(n-t0)/dur),x=1-Math.pow(1-k,3);e.textContent=fmtf(from+(to-from)*x);if(k<1)e._r=requestAnimationFrame(st)};e._r=requestAnimationFrame(st);
}
function md(src){
  const inl=t=>t.replace(/\*\*(.+?)\*\*/g,'<b>$1</b>').replace(/(^|[^*])\*([^*]+)\*/g,'$1$2');
  let out='',list=null;const close=()=>{if(list){out+=`</${list}>`;list=null}};
  for(const ln of esc(src).split('\n')){let m;
    if(m=ln.match(/^(#{1,3})\s+(.*)/)){close();out+=`<h${m[1].length+1}>${inl(m[2])}</h${m[1].length+1}>`}
    else if(m=ln.match(/^\s*[-*]\s+(.*)/)){if(list!=='ul'){close();out+='<ul>';list='ul'}out+=`<li>${inl(m[1])}</li>`}
    else if(m=ln.match(/^\s*\d+[.)]\s+(.*)/)){if(list!=='ol'){close();out+='<ol>';list='ol'}out+=`<li>${inl(m[1])}</li>`}
    else if(!ln.trim())close();else{close();out+=`<p>${inl(ln)}</p>`}}
  close();return out;
}
const plain=t=>t.replace(/[#*_`]/g,'').replace(/\s+/g,' ').trim();
function speak(t){try{speechSynthesis.cancel();const u=new SpeechSynthesisUtterance(plain(t));u.rate=1.02;speechSynthesis.speak(u)}catch(e){}}
async function waitRunning(){await sleep(60);while(running)await sleep(120)}
// one caption and one notice at a time, never over the controls
const stage=el('div','stage');stage.id='stage';$('map').appendChild(stage);
let sayT=0;
function say(html,sub,ms=4200){
  let s=$('say');if(!s){s=el('div','say');s.id='say';stage.prepend(s)}
  s.innerHTML=html+(sub?`<small>${sub}</small>`:'');s.hidden=false;clearTimeout(sayT);if(ms)sayT=setTimeout(()=>s.hidden=true,ms);
}
const hideSay=()=>{const s=$('say');if(s)s.hidden=true};
let noteT=0;
function notice(html,ms=12000){
  let n=$('notice');if(!n){n=el('div','notice');n.id='notice';$('map').appendChild(n)}
  n.innerHTML=`<button class="x" aria-label="Dismiss">${icon('x')}</button>${html}`;n.hidden=false;n.querySelector('.x').onclick=()=>n.hidden=true;
  clearTimeout(noteT);if(ms)noteT=setTimeout(()=>n.hidden=true,ms);
}

/* ================================================================ topbar */
const stat=(id,v,f)=>{const e=$(id);e.hidden=v==null;if(v!=null)countTo(e.querySelector('b'),v,f)};
function kpiRun(){
  const s=lastRun&&lastRun.s;if(!s)return;
  stat('tsRisk',s.people_at_risk);stat('tsCol',s.expected_collapses);stat('tsDead',s.casualties?s.casualties.deaths:null);
}
const kpiAcc=a=>stat('tsAcc',a.median_minutes,v=>Math.round(v)+' min');
setInterval(()=>{const a=($('areaSum').textContent||'').split(' · ')[0];$('tbCtx').textContent=[a,$('scnSum').textContent].filter(Boolean).join(' · ')},700);
$('tbPanel').onclick=()=>$('panel').click();$('tbDisp').onclick=()=>$('dispBtn').click();
const syncPressed=()=>{$('tbPanel').setAttribute('aria-pressed',String(!$('app').classList.contains('nopanel')));$('tbDisp').setAttribute('aria-pressed',String(!$('app').classList.contains('nodisp')))};
new MutationObserver(syncPressed).observe($('app'),{attributes:true,attributeFilter:['class']});syncPressed();

// one popover at a time, closed by a click outside or Esc
let POP=null;
function popover(anchor,html,mount){
  closeP();const p=el('div','pop2',html);p.setAttribute('role','dialog');document.body.appendChild(p);fillIcons(p);POP={p,anchor};mount&&mount(p);
  const r=$(anchor).getBoundingClientRect();p.style.right=Math.max(16,innerWidth-r.right)+'px';return p;
}
function closeP(){if(POP){POP.p.remove();POP=null}}
document.addEventListener('mousedown',e=>{if(POP&&!POP.p.contains(e.target)&&!$(POP.anchor).contains(e.target))closeP()});
document.addEventListener('keydown',e=>{if(e.key==='Escape')closeP()});

$('tbView').onclick=()=>{
  if(POP&&POP.anchor==='tbView'){closeP();return}
  const seg=(k,opts)=>`<span class="seg" data-k="${k}">${opts.map(([v,l])=>`<button type="button" data-v="${v}" aria-pressed="false">${l}</button>`).join('')}</span>`;
  popover('tbView',`<h3>View</h3><div class="opt2"><span>Theme</span>${seg('theme',[['light','Light'],['dark','Dark']])}</div><div class="opt2"><span>Map</span>${seg('map',[['light','Light'],['dark','Dark']])}</div><div class="opt2"><span>Sound</span>${seg('sound',[['on','On'],['off','Off']])}</div>`,p=>{
    const sync=()=>{const cur={theme:document.documentElement.dataset.theme,map:basemap,sound:sfx.on?'on':'off'};p.querySelectorAll('.seg').forEach(s=>s.querySelectorAll('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.v===cur[s.dataset.k]))))};
    p.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const k=b.parentNode.dataset.k,v=b.dataset.v;
      if(k==='theme')setTheme(v,true);else if(k==='map')setBasemap(v,true);else{sfx.set(v==='on');if(sfx.on)sfx.chime()}sync()});sync()});
};

/* ================================================================ enable and disable by state */
function updState(){
  const has=!!runNo&&!!lastRun,quake=has&&lastRun.hz==='earthquake',area=document.getElementById('areaSel').options.length>0;
  const set=(id,on,why)=>{const b=$(id);if(!b)return;b.disabled=!on;if(!on&&why)b.dataset.why=why;};
  ['tbReport','tbAsk','mt3d','mtSwipe','mtTour'].forEach(id=>set(id,has));set('cmpBtn',area);set('aftBtn',quake);
  $('mt3d').setAttribute('aria-pressed',String(is3d));
}
setInterval(updState,800);

/* ================================================================ wrap the page's functions */
const _show=show;show=function(d){
  const r=_show.apply(this,arguments);
  kpiRun();$('tsAcc').hidden=true;resetCascade();resetConf();clearAsk();updState();sfx.rumble();
  return r;
};
const _sr=showRanges;showRanges=function(j){_sr.apply(this,arguments);kpiRun();const cb=$('confChk');if(cb){cb.disabled=false;$('confMsg').textContent='Hatch the squares where the damage estimate is least certain.'}};
const _sa=showAccess;showAccess=function(s){_sa.apply(this,arguments);kpiAcc(s)};
const _aa=applyArea;applyArea=function(d){AREA_C=d.center;const r=_aa.apply(this,arguments);['tsRisk','tsCol','tsDead','tsAcc'].forEach(id=>{$(id).hidden=true;$(id).querySelector('b').dataset.v=0});resetCascade();resetConf();clearAsk();updState();return r};
const seen=new Map();let lastChime=0;
const _lr=lvRender;lvRender=function(){
  const r=_lr.apply(this,arguments);
  for(const s of LIVE.sos.values()){const p=seen.get(s.id);if(p&&p!==s.status&&s.status==='assigned'&&Date.now()-lastChime>700){lastChime=Date.now();sfx.chime()}seen.set(s.id,s.status)}
  $('tbBadge').textContent=$('dispBadge').textContent;meshUpdate();return r;
};
$('aSend').addEventListener('click',()=>sfx.alarm());
$('lSim').addEventListener('click',()=>sfx.sos());

/* ================================================================ dispatch panel */
{
  const dh=document.querySelector('.dh'),tools=dh.querySelector('.dtools');
  dh.querySelector('.dht').insertAdjacentHTML('afterend','<div class="dlive" aria-live="polite"><div id="dlOn"><b data-v="0">0</b><span>phones online</span></div><div id="dlSafe"><b data-v="0">0</b><span>marked safe</span></div><div id="dlSos"><b data-v="0">0</b><span>SOS open</span></div></div>');
  tools.insertAdjacentHTML('beforebegin','<button type="button" id="dOptB" aria-expanded="false" aria-controls="dOpt">Optimise all teams</button>');
  tools.insertAdjacentHTML('beforeend','<button type="button" class="alt" id="dFleetB" aria-expanded="false" aria-controls="dFleet">Fleet</button>');
  $('dDemo').insertAdjacentHTML('beforeend',`<div class="dspeed"><span>Team speed</span><span class="seg" id="dSpeed" role="group" aria-label="Team speed">${[1,5,10,30,60].map(v=>`<button type="button" data-v="${v}" aria-pressed="false">×${v}</button>`).join('')}</span></div>
    <label class="chk"><input type="checkbox" id="dAuto" checked> Close a call when its team arrives</label>`);
  $('dDemo').insertAdjacentHTML('afterend','<div class="dpop" id="dOpt" hidden><div id="oBody"></div></div><div class="dpop" id="dFleet" hidden><div id="fBody"></div></div>');
  $('lClrSim').textContent='Remove demo calls';$('lClr').textContent='Clear all';
  POPS.dOptB='dOpt';POPS.dFleetB='dFleet';
  $('dOptB').onclick=()=>{pop('dOptB');if(!$('dOpt').hidden)optPreview()};
  $('dFleetB').onclick=()=>{pop('dFleetB');if(!$('dFleet').hidden)fleetLoad()};
  const sp=()=>document.querySelectorAll('#dSpeed button').forEach(b=>b.setAttribute('aria-pressed',String(+b.dataset.v===Math.round(LIVE.speed||1))));
  $('dSpeed').onclick=async e=>{const b=e.target.closest('button');if(!b)return;LIVE.speed=+b.dataset.v;sp();await post('/api/live/speed',{speed:+b.dataset.v})};
  setInterval(sp,1000);
}
function meshUpdate(){
  const ph=[...LIVE.phones.values()],on=ph.filter(p=>p.online).length,safe=ph.filter(p=>p.safe).length,open=[...LIVE.sos.values()].filter(s=>s.status==='new').length;
  [['dlOn',on],['dlSafe',safe],['dlSos',open]].forEach(([id,v])=>countTo($(id).querySelector('b'),v));
  HAS_OPEN=open>0;drawMesh(ph);
}
let HAS_OPEN=false;
let OPT=null;
async function optPreview(){
  const b=$('oBody');b.innerHTML='<p class="hint" style="margin:0">Planning every open call at once</p><div class="sk"></div><div class="sk" style="width:70%"></div>';
  try{const r=await post('/api/live/optimise',{apply:false});if(!r.ok)throw new Error(await errOf(r,'Could not plan.'));OPT=await r.json();optShow()}
  catch(e){OPT=null;b.innerHTML=`<p class="hint" style="margin:0">${esc(e.message)}</p>${/no open SOS/i.test(e.message)?'<button type="button" id="oSim" class="alt">Add 20 made-up SOS calls</button>':''}`;
    const s=$('oSim');if(s)s.onclick=async()=>{await post('/api/live/simulate',{n:20});await sleep(400);optPreview()}}
}
function optShow(){
  const p=OPT,st=p.stats,b=$('oBody'),u=st.urgent_calls>0,nv=u?st.urgent_avg_naive:st.avg_naive,bs=u?st.urgent_avg_best:st.avg_best,max=Math.max(nv,bs,1);
  b.innerHTML=`<div class="ohead"><big>${st.weighted_saved_pct>0?'−':''}${Math.abs(st.weighted_saved_pct)}%</big><span>priority-weighted response time against answering the ${st.calls} open calls one by one</span></div>
  <div class="cbar"><div><span>One by one</span><i style="width:${60*nv/max}%;background:var(--line-2)"></i><b>${nv} min</b></div><div><span>Optimised</span><i style="width:${60*bs/max}%;background:var(--ink)"></i><b>${bs} min</b></div></div>
  <p class="hint">Average wait for ${u?'critical and high calls':'all calls'}. ${st.urgent_minutes_saved>0?`Saved ${Math.round(st.urgent_minutes_saved)} minutes across ${st.urgent_calls} critical and high calls. `:''}${p.unserved.length?`${p.unserved.length} call${p.unserved.length>1?'s have':' has'} no free unit of the kind needed.`:'Every call gets a team.'}</p>
  <button type="button" id="oGo" style="margin-top:12px">Dispatch ${p.plan.length} team${p.plan.length===1?'':'s'}</button>
  <div class="plist">${p.plan.slice(0,40).map(x=>`<div><b>#${x.sos}</b><span>${esc(x.name)}</span><em>${x.eta_min} min${x.naive_eta_min!=null&&x.naive_eta_min>x.eta_min?` (was ${x.naive_eta_min})`:''}</em></div>`).join('')}</div>`;
  $('oGo').onclick=optApply;
}
async function optApply(){
  const g=$('oGo');g.disabled=true;g.textContent='Sending teams';
  try{const r=await post('/api/live/optimise',{apply:true});if(!r.ok)throw new Error(await errOf(r,'Could not dispatch.'));const d=await r.json();
    lvToast(`${d.applied} teams dispatched`+(d.stats.urgent_minutes_saved>0?`, saving ${Math.round(d.stats.urgent_minutes_saved)} min on critical calls`:''));
    LIVE.filter='enroute';lvRender();pop(null);fleetSoon()}catch(e){lvToast(e.message,true);g.disabled=false;g.textContent='Try again'}
}
let fleetT=0;const fleetSoon=()=>{clearTimeout(fleetT);fleetT=setTimeout(()=>{if(!$('dFleet').hidden)fleetLoad()},600)};
async function fleetLoad(){
  const b=$('fBody');if(!b.children.length)b.innerHTML='<p class="hint" style="margin:0">Loading</p>';
  try{const r=await fetch('/api/live/resources');const d=await r.json();
    if(!d.loaded){b.innerHTML='<p class="hint" style="margin:0">Load streets and facilities first (Responders tab: Show hospitals, fire stations and roads).</p>';return}
    const NAME={fire_station:'Fire and rescue',ambulance:'Ambulances',police:'Police'};
    const bar=(l,v,t,low,ex)=>`<div class="fl${low?' low':''}"><div><span>${l}</span><b>${v}${t!=null?' / '+t:''}${ex||''}</b></div><i><s style="width:${t?Math.min(100,100*v/t):0}%"></s></i></div>`;
    const h=d.hospitals;
    b.innerHTML=d.kinds.map(k=>bar(NAME[k.kind],k.free,k.units,k.units&&k.free/k.units<.25,` free, ${k.busy} out`)).join('')+
      bar('Fuel (simulated)',d.fuel_pct==null?0:d.fuel_pct,100,d.fuel_pct!=null&&d.fuel_pct<40,'%')+
      (h?bar('Working hospital beds',h.beds_working,h.serious_injuries_expected,h.beds_per_serious_injury!=null&&h.beds_per_serious_injury<1,' vs seriously injured')+`<p class="hint">${h.working} of ${h.total} hospitals working${h.beds_assumed?'; beds assumed where unknown':''}.${h.beds_per_serious_injury!=null?` About ${h.beds_per_serious_injury} beds per seriously injured person.`:''}</p>`:'')+
      (d.low_fuel.length?`<p class="hint">Low fuel: ${d.low_fuel.map(x=>esc(x.name)+' '+x.fuel_pct+'%').join(', ')}</p>`:'')+
      `<button type="button" class="alt" id="fRefuel">Refuel all stations</button><p class="hint">${esc(d.note)}</p>`;
    $('fRefuel').onclick=async()=>{await post('/api/live/refuel');fleetLoad()};
  }catch(e){b.innerHTML='<p class="hint" style="margin:0">Could not load the fleet.</p>'}
}
setInterval(()=>{if(!$('dFleet').hidden)fleetLoad()},4000);

/* ================================================================ map layers: mesh, SOS rings, moving teams, route dashes */
let meshLast='';
function drawMesh(ph){
  if(!mapReady())return;lvLayers();if(!map.getLayer('lv-sos'))return;
  if(!map.getSource('lvMesh')){map.addSource('lvMesh',{type:'geojson',data:EMPTY});map.addSource('lvTeams',{type:'geojson',data:EMPTY});
    map.addLayer({id:'lv-mesh',type:'line',source:'lvMesh',paint:{'line-color':'#9aa0ab','line-width':1,'line-opacity':.5}},'lv-ph');
    map.addLayer({id:'lv-sos-ping',type:'circle',source:'lvSos',filter:['==',['get','status'],'new'],paint:{'circle-color':'rgba(0,0,0,0)','circle-stroke-color':['get','col'],'circle-stroke-width':2,'circle-radius':12,'circle-stroke-opacity':.8}},'lv-sel');
    map.addLayer({id:'lv-team',type:'circle',source:'lvTeams',paint:{'circle-radius':7,'circle-color':'#ededed','circle-stroke-color':'#111','circle-stroke-width':2.5}},'lv-sos');
    LIVE_IDS.splice(LIVE_IDS.indexOf('lv-ph'),0,'lv-mesh');LIVE_IDS.splice(LIVE_IDS.indexOf('lv-sel'),0,'lv-sos-ping');LIVE_IDS.splice(LIVE_IDS.indexOf('lv-sos'),0,'lv-team');
  }
  const pts=ph.filter(p=>p.online&&p.lat!=null).slice(0,300),k=Math.cos(pts.length?pts[0].lat*Math.PI/180:.88),feats=[];
  pts.forEach((p,i)=>{const near=pts.map((q,j)=>[j,Math.hypot((q.lon-p.lon)*k,q.lat-p.lat)]).filter(x=>x[0]>i&&x[1]<0.05).sort((a,b)=>a[1]-b[1]).slice(0,2);   // each phone links to its 2 nearest neighbours within ~5 km
    near.forEach(([j])=>feats.push({type:'Feature',geometry:{type:'LineString',coordinates:[[p.lon,p.lat],[pts[j].lon,pts[j].lat]]},properties:{}}))});
  const sig=feats.length+':'+pts.map(p=>p.lon.toFixed(4)+p.lat.toFixed(4)).join();if(sig!==meshLast){meshLast=sig;map.getSource('lvMesh').setData({type:'FeatureCollection',features:feats})}
}
const ROUTES=new WeakMap();
function routeInfo(r){let i=ROUTES.get(r);if(i)return i;const c=r.geometry.coordinates,k=Math.cos(c[0][1]*Math.PI/180),cum=[0];
  for(let j=1;j<c.length;j++)cum.push(cum[j-1]+Math.hypot((c[j][0]-c[j-1][0])*k,c[j][1]-c[j-1][1]));i={c,cum,len:cum[cum.length-1]};ROUTES.set(r,i);return i}
function along(r,f){const{c,cum,len}=routeInfo(r);if(!len)return c[0];const d=f*len;let j=1;while(j<cum.length-1&&cum[j]<d)j++;const u=(d-cum[j-1])/Math.max(cum[j]-cum[j-1],1e-12);return[c[j-1][0]+(c[j][0]-c[j-1][0])*u,c[j-1][1]+(c[j][1]-c[j-1][1])*u]}
const TEAMS=new Map();
const DASH=[[0,4,3],[.5,4,2.5],[1,4,2],[1.5,4,1.5],[2,4,1],[2.5,4,.5],[3,4,0],[0,.5,3,3.5],[0,1,3,3],[0,1.5,3,2.5],[0,2,3,2],[0,2.5,3,1.5],[0,3,3,1],[0,3.5,3,.5]];
let lastT=0,lastDash=-1,tickTeams=false;
function teamsTick(dt){
  const feats=[],speed=LIVE.speed||1;
  for(const s of LIVE.sos.values()){
    if(s.status!=='assigned'||!s.route||s.eta_min==null){TEAMS.delete(s.id);continue}
    const dur=Math.max(4,s.eta_min*60);let t=TEAMS.get(s.id);
    if(!t||t.at!==s.assigned_at){const e0=Date.now()/1000+(LIVE.off||0)-(s.assigned_at||0);t={at:s.assigned_at,prog:Math.min(1,Math.max(0,e0*speed/dur)),done:false};TEAMS.set(s.id,t)}
    if(!t.done){t.prog=Math.min(1,t.prog+dt*speed/dur);
      if(t.prog>=1){t.done=true;sfx.done();lvToast(`${s.team||'A team'} has reached SOS ${s.id}`);if($('dAuto')&&$('dAuto').checked)fetch(`/api/live/sos/${s.id}/resolve?reason=arrived`,{method:'POST'})}}
    if(!t.done)feats.push({type:'Feature',geometry:{type:'Point',coordinates:along(s.route,t.prog)},properties:{id:s.id}});
  }
  const src=map.getSource('lvTeams');if(src&&(feats.length||tickTeams))src.setData({type:'FeatureCollection',features:feats});tickTeams=feats.length>0;
}
function tick(now){
  requestAnimationFrame(tick);
  if(document.hidden||!mapReady())return;
  if(now-lastT<55)return;const dt=Math.min(.25,(now-lastT)/1000);lastT=now;const t=now/1000;
  if(!reduce){
    if(HAS_OPEN&&map.getLayer('lv-sos-ping')){const ph=(t%1.8)/1.8;map.setPaintProperty('lv-sos-ping','circle-radius',['interpolate',['linear'],['zoom'],10,9+22*ph,15,15+40*ph]);map.setPaintProperty('lv-sos-ping','circle-stroke-opacity',.8*(1-ph))}   // waiting for help: the only thing that pulses
    const di=Math.floor(t*10)%DASH.length;if(map.getLayer('lv-route')&&di!==lastDash&&[...LIVE.sos.values()].some(s=>s.status==='assigned')){lastDash=di;map.setPaintProperty('lv-route','line-dasharray',DASH[di])}
  }
  if(map.getSource('lvTeams'))teamsTick(dt);else drawMesh([...LIVE.phones.values()]);
}
requestAnimationFrame(tick);

/* ================================================================ left panel: scenarios, compare, aftershock */
let PRESETS=[];
{
  $('scn').querySelector('.secb').prepend(el('div','grp','<label for="preset">Quick scenarios</label><select id="preset"><option value="">Replay a real earthquake</option></select><p class="hint" id="presetNote"></p>'));
  fetch('/api/presets').then(r=>r.json()).then(d=>{
    PRESETS=d.presets;const groups={};d.presets.forEach(p=>(groups[p.group]=groups[p.group]||[]).push(p));
    $('preset').insertAdjacentHTML('beforeend',Object.entries(groups).map(([g,ps])=>`<optgroup label="${esc(g)}">${ps.map(p=>`<option value="${p.id}">${esc(p.label)}</option>`).join('')}</optgroup>`).join(''));
  }).catch(()=>{});
  $('preset').onchange=()=>{const p=PRESETS.find(x=>x.id===$('preset').value);if(p)applyPreset(p,true)};
  $('run').insertAdjacentHTML('afterend',`<div class="runmore"><button type="button" class="alt" id="cmpBtn" title="Run two scenarios side by side">${icon('compare')}Compare</button><button type="button" class="alt" id="aftBtn" title="A second earthquake on the damage already done" disabled>${icon('zap')}Aftershock</button></div>`);
}
async function applyPreset(p,go){
  $('hz').value='earthquake';$('hz').onchange();
  const c=AREA_C||[+$('lon').value,+$('lat').value];
  $('lon').value=(p.where==='real'?p.lon:c[0]).toFixed(4);$('lat').value=(p.where==='real'?p.lat:c[1]).toFixed(4);
  $('mag').value=p.magnitude;$('depth').value=p.depth_km;$('tod').value=p.time_of_day||'night';
  ['strike','dip','rake'].forEach(k=>$(k).value=p[k]!=null?p[k]:'');$('adv').open=p.strike!=null;
  placeEpi();scnUpdate();
  $('presetNote').innerHTML=`${esc(p.blurb)}${p.observed?`<br>${esc(p.observed)}`:''}`;
  if(go){if(map&&p.where==='real')map.flyTo({center:[p.lon,p.lat],zoom:8,duration:1200});await sleep(p.where==='real'?900:0);$('run').click();await waitRunning();
    notice(`<b>${esc(p.label)}</b><br>${esc(p.blurb)}${p.observed?`<br>${esc(p.observed)} A quake placed on another city is not expected to match.`:''}`,20000)}
}
$('aftBtn').onclick=async()=>{
  const b=$('aftBtn');if(!lastRun||lastRun.hz!=='earthquake')return;b.disabled=true;
  try{
    const r=await post('/api/aftershock',{});if(!r.ok)throw new Error(await errOf(r,'Could not run the aftershock.'));const d=await r.json(),a=d.aftershock;
    runNo=d.run;lastRun={hz:'earthquake',tod:lastRun.tod,mag:a.magnitude,depth:a.depth_km,lat:a.lat,lon:a.lon,s:d.summary,aft:a};show(d);shakeNow(7);
    notice(`<b>Aftershock M${a.magnitude}</b><br>Added <span class="n">${fmt(a.added_collapses)}</span> collapses and <span class="n">${fmt(a.added_people_at_risk)}</span> people at risk${a.added_deaths!=null?`, <span class="n">${fmt(a.added_deaths)}</span> deaths`:''}. Totals now include both shocks. ${esc(a.weakening)}`,20000);
  }catch(e){notice(esc(e.message),8000)}
  updState();
};

/* ================================================================ Summary tab: ask about the results */
{
  const qs=['Which hospital is most at risk?','Where do I send the first team?','How many SOS calls are open?'];
  const sec=el('details','sec');sec.id='askSec';sec.open=true;
  sec.innerHTML=`<summary>Ask about these results<span class="sum" id="askSrc"></span></summary><div class="secb"><form class="askrow" id="askForm"><input id="askIn" placeholder="Ask a question" maxlength="400" autocomplete="off" aria-label="Ask a question">
    ${(window.SpeechRecognition||window.webkitSpeechRecognition)?`<button type="button" class="alt" id="askMic" aria-label="Speak a question" title="Speak a question">${icon('mic')}</button>`:''}<button type="submit" aria-label="Ask" title="Ask">${icon('send')}</button></form>
    <div class="qs" id="askQs">${qs.map(q=>`<button type="button">${q}</button>`).join('')}</div><div id="askLog" aria-live="polite"></div>
    <label class="chk"><input type="checkbox" id="askVoice"> Read answers aloud</label></div>`;
  $('unc').after(sec);
  $('askQs').onclick=e=>{const b=e.target.closest('button');if(b)ask(b.textContent)};
  $('askForm').onsubmit=e=>{e.preventDefault();const v=$('askIn').value.trim();if(v){$('askIn').value='';ask(v)}};
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(SR)$('askMic').onclick=()=>{const rec=new SR();rec.lang=navigator.language||'en-IN';rec.interimResults=false;$('askMic').classList.add('rec');
    rec.onresult=e=>ask(e.results[0][0].transcript);rec.onend=rec.onerror=()=>$('askMic').classList.remove('rec');try{rec.start()}catch(e){}};
}
function clearAsk(){if($('askLog'))$('askLog').innerHTML=''}
async function ask(q){
  $('askSec').open=true;const row=el('div','qa',`<div class="q">${esc(q)}</div><div class="a"><span class="cur"></span></div>`);$('askLog').prepend(row);const a=row.querySelector('.a');
  if(!runNo){a.textContent='Run a simulation first, then ask about it.';return}
  try{const r=await post('/api/assistant/'+runNo,{question:q});if(!r.ok)throw new Error(await errOf(r,'No answer.'));const d=await r.json();
    a.innerHTML=md(d.answer);row.insertAdjacentHTML('beforeend',`<div class="s">${d.source==='claude'?'Claude':'Built-in'}</div>`);$('askSrc').textContent=d.source==='claude'?'Claude':'';
    if(d.focus&&map)map.flyTo({center:[d.focus.lon,d.focus.lat],zoom:Math.max(map.getZoom(),d.focus.zoom),duration:1200});
    if($('askVoice').checked)speak(d.answer)}catch(e){a.textContent=e.message}
}
$('tbAsk').onclick=()=>{if($('app').classList.contains('nopanel'))$('panel').click();tab('sum');$('askSec').open=true;$('askIn').focus();$('askSec').scrollIntoView({block:'nearest'})};

/* ================================================================ Map tab: overlays (hatching, knock-on failures) */
{
  const sec=el('details','sec');sec.id='ovSec';sec.setAttribute('data-keep','');sec.open=true;
  sec.innerHTML=`<summary>Overlays</summary><div class="secb">
  <label class="chk" style="margin-top:0"><input type="checkbox" id="confChk" disabled> Uncertainty hatching</label><p class="hint" id="confMsg">Available once the uncertainty ranges are ready.</p>
  <label class="chk"><input type="checkbox" id="casChk"> Knock-on failures</label>
  <div id="casBox" hidden><div class="cas-row"><label class="chk"><input type="checkbox" data-c="fire" checked> Fires</label><label class="chk"><input type="checkbox" data-c="gas" checked> Gas leaks</label><label class="chk"><input type="checkbox" data-c="power" checked> Power cuts</label><label class="chk"><input type="checkbox" data-c="bridge" checked> Bridges</label></div>
  <div class="casplay"><button type="button" class="alt" id="casPlay">Play</button><input type="range" id="casT" min="0" max="120" value="0" aria-label="Minutes after the earthquake"><span id="casTT">0 min</span></div>
  <div id="casOut"></div></div></div>`;
  $('expSec').before(sec);
}
let CAS=null,casPlayT=0;
const FIRE_COL='#ff7a1a';
function ensureCas(){
  if(map.getSource('casFire'))return;
  ['casFire','casFirePt','casGas','casPower','casBridge'].forEach(s=>map.addSource(s,{type:'geojson',data:EMPTY}));
  const before=map.getLayer('bld')?'bld':undefined;
  map.addLayer({id:'cas-power',type:'circle',source:'casPower',paint:{'circle-color':'#04060c','circle-opacity':['*',.8,['get','p']],'circle-blur':.6,'circle-radius':['interpolate',['exponential',2],['zoom'],10,4,17,512]}},before);
  map.addLayer({id:'cas-fire',type:'fill',source:'casFire',paint:{'fill-color':FIRE_COL,'fill-opacity':.4}});
  map.addLayer({id:'cas-fire-l',type:'line',source:'casFire',paint:{'line-color':'#ffd166','line-width':1.5,'line-opacity':.9}});
  map.addLayer({id:'cas-fire-pt',type:'circle',source:'casFirePt',paint:{'circle-color':'#fff3b0','circle-radius':['interpolate',['linear'],['zoom'],10,3,16,7],'circle-stroke-color':FIRE_COL,'circle-stroke-width':2.5}});
  map.addLayer({id:'cas-gas',type:'circle',source:'casGas',paint:{'circle-color':'#a78bfa','circle-radius':['interpolate',['linear'],['zoom'],10,3,16,7],'circle-stroke-color':'#fff','circle-stroke-width':1.5,'circle-opacity':.9}});
  map.addLayer({id:'cas-bridge',type:'circle',source:'casBridge',paint:{'circle-color':['interpolate',['linear'],['get','p'],0,'#4cc38a',.3,'#f2b705',.6,'#e5484d'],'circle-radius':['interpolate',['linear'],['zoom'],10,4,16,8],'circle-stroke-color':'#111','circle-stroke-width':2}});
  map.on('click','cas-bridge',e=>{const p=e.features[0].properties;openPop(e.lngLat,`<b>${esc(p.name||'Bridge')}</b><br>Chance it is out of service: ${Math.round(p.p*100)}%<br>Shaking there: ${p.pga} g`)});
  map.on('click','cas-fire-pt',e=>{const p=e.features[0].properties;openPop(e.lngLat,`<b>${p.gas==='true'||p.gas===true?'Fire from a gas leak':'Fire'}</b><br>Starts about ${Math.round(p.t0)} min after the quake`)});
  map.on('mouseenter','cas-bridge',()=>map.getCanvas().style.cursor='pointer');map.on('mouseleave','cas-bridge',()=>map.getCanvas().style.cursor='');
}
async function loadCas(){
  const o=$('casOut');o.innerHTML='<p class="hint">Working out what else may fail</p>';
  try{const r=await fetch('/api/cascade/'+runNo);if(!r.ok)throw new Error(await errOf(r,'Could not compute.'));CAS=await r.json();
    if(!CAS.supported){o.innerHTML=`<p class="hint">${esc(CAS.reason)}</p>`;return}
    ensureCas();drawCas();const s=CAS.summary,t=s.threatened['120'];
    o.innerHTML=`<div class="stat"><span>Fires that may start</span><b>${fmt(s.ignitions)}</b></div><div class="stat"><span>Buildings threatened within 2 h</span><b>${fmt(t.buildings)}<small>${fmt(t.people)} people</small></b></div><div class="stat"><span>Gas leaks possible</span><b>${fmt(s.gas_leaks)}</b></div>
    <div class="stat"><span>People without power</span><b>${fmt(s.people_without_power)}<small>${Math.round(s.share_without_power*100)}%</small></b></div><div class="stat"><span>Bridges likely out</span><b>${s.bridges_likely_closed} of ${s.bridges}</b></div><p class="hint">${CAS.assumptions.map(esc).join(' ')}</p>`;
    $('lgCasBox').hidden=false;
  }catch(e){o.innerHTML=`<p class="hint">${esc(e.message)}</p>`}
}
function drawCas(){
  if(!CAS||!CAS.supported||!map.getSource('casFire'))return;
  const t=+$('casT').value,on=k=>document.querySelector(`#casBox [data-c=${k}]`).checked,fp=CAS.fire_params;
  const fires=on('fire')?CAS.fires.filter(f=>f.t0<=t):[];
  map.getSource('casFire').setData({type:'FeatureCollection',features:fires.map(f=>({type:'Feature',properties:{},geometry:{type:'Polygon',coordinates:[circlePoly(f.lon,f.lat,Math.min(fp.rmax_m,fp.r0_m+f.v*(t-f.t0))/1000,28)]}}))});
  map.getSource('casFirePt').setData({type:'FeatureCollection',features:fires.map(f=>({type:'Feature',geometry:{type:'Point',coordinates:[f.lon,f.lat]},properties:{t0:f.t0,gas:f.gas}}))});
  map.getSource('casGas').setData(on('gas')?{type:'FeatureCollection',features:CAS.gas.map(g=>({type:'Feature',geometry:{type:'Point',coordinates:[g.lon,g.lat]},properties:{}}))}:EMPTY);
  map.getSource('casPower').setData(on('power')?{type:'FeatureCollection',features:CAS.power.map(c=>({type:'Feature',geometry:{type:'Point',coordinates:[c.lon,c.lat]},properties:{p:c.p}}))}:EMPTY);
  map.getSource('casBridge').setData(on('bridge')?{type:'FeatureCollection',features:CAS.bridges.filter(b=>b.p>=0.15).map(b=>({type:'Feature',geometry:{type:'Point',coordinates:[b.lon,b.lat]},properties:b}))}:EMPTY);
  $('casTT').textContent=Math.round(t)+' min';
}
function resetCascade(){CAS=null;stopCasPlay();if(map&&map.getSource&&map.getSource('casFire'))['casFire','casFirePt','casGas','casPower','casBridge'].forEach(s=>map.getSource(s).setData(EMPTY));
  if($('casChk')){$('casChk').checked=false;$('casBox').hidden=true;$('casOut').innerHTML='';$('casT').value=0}if($('lgCasBox'))$('lgCasBox').hidden=true}
$('casChk').onchange=async()=>{$('casBox').hidden=!$('casChk').checked;
  if($('casChk').checked){if(!runNo){$('casOut').innerHTML='<p class="hint">Run a simulation first.</p>';return}await loadCas()}
  else{['casFire','casFirePt','casGas','casPower','casBridge'].forEach(s=>map.getSource(s)&&map.getSource(s).setData(EMPTY));$('lgCasBox').hidden=true}};
document.querySelectorAll('#casBox [data-c]').forEach(c=>c.onchange=drawCas);$('casT').oninput=()=>{stopCasPlay();drawCas()};
function stopCasPlay(){cancelAnimationFrame(casPlayT);casPlayT=0;if($('casPlay'))$('casPlay').textContent='Play'}
$('casPlay').onclick=()=>{if(casPlayT){stopCasPlay();return}
  $('casPlay').textContent='Pause';const t0=performance.now(),from=+$('casT').value>=120?0:+$('casT').value;
  const st=n=>{const t=from+(n-t0)/1000*10;$('casT').value=Math.min(120,t);drawCas();if(t>=120){stopCasPlay();return}casPlayT=requestAnimationFrame(st)};casPlayT=requestAnimationFrame(st)};
document.getElementById('legend').insertAdjacentHTML('beforeend',`<div class="lg" id="lgCasBox" hidden><h3>Knock-on failures</h3><div class="legend"><div><span style="background:#ff7a1a"></span>Fire, growing with time</div><div><span style="background:#a78bfa"></span>Possible gas leak</div><div><span style="background:#04060c;border:1px solid #888"></span>Likely power cut</div><div><span style="background:linear-gradient(90deg,#4cc38a,#e5484d)"></span>Bridge: chance out of service</div></div></div>
<div class="lg" id="lgConfBox" hidden><h3>Confidence</h3><div class="legend"><div><span style="background:repeating-linear-gradient(45deg,#fff 0 2px,#777 2px 5px)"></span>Hatched: estimate uncertain</div></div></div>`);

/* ---- uncertainty hatching ---- */
function hatchImage(){
  const n=16,d=new Uint8ClampedArray(n*n*4);
  for(let y=0;y<n;y++)for(let x=0;x<n;x++){const v=(x+y)%8,i=(y*n+x)*4;if(v<2){d[i]=d[i+1]=d[i+2]=255;d[i+3]=235}else if(v===2||v===7){d[i]=d[i+1]=d[i+2]=0;d[i+3]=120}}
  return{width:n,height:n,data:d};
}
async function loadConf(){
  const m=$('confMsg');m.textContent='Loading';
  try{const r=await fetch('/api/confmap/'+runNo);if(!r.ok)throw new Error(await errOf(r,'Not ready.'));const g=await r.json();
    if(!map.hasImage('hatch'))map.addImage('hatch',hatchImage(),{pixelRatio:2});
    if(!map.getSource('conf')){map.addSource('conf',{type:'geojson',data:g});
      map.addLayer({id:'conf-h',type:'fill',source:'conf',paint:{'fill-pattern':'hatch','fill-opacity':['interpolate',['linear'],['get','u'],.45,0,.7,.35,1,.85]}},map.getLayer('bld')?'bld':undefined)}
    else map.getSource('conf').setData(g);
    $('lgConfBox').hidden=false;m.textContent='Squares are ranked by the spread of the Monte Carlo damage chance. Stronger hatching means less certain.'}
  catch(e){m.textContent=e.message;$('confChk').checked=false}
}
$('confChk').onchange=async()=>{if($('confChk').checked){if(!runNo)return;await loadConf()}else{if(map.getLayer('conf-h'))map.setLayoutProperty('conf-h','visibility','none');$('lgConfBox').hidden=true;return}
  if(map.getLayer('conf-h'))map.setLayoutProperty('conf-h','visibility','visible')};
function resetConf(){if(map&&map.getSource&&map.getSource('conf'))map.getSource('conf').setData(EMPTY);if($('confChk')){$('confChk').checked=false;$('confChk').disabled=!mcRep;$('lgConfBox').hidden=true;
  $('confMsg').textContent=mcRep?'Hatch the squares where the damage estimate is least certain.':'Available once the uncertainty ranges are ready.'}}

/* ================================================================ modals and the AI report */
function modal(title,body,foot,cls){
  const m=el('div','modal');m.setAttribute('role','dialog');m.setAttribute('aria-modal','true');
  m.innerHTML=`<div class="mbox ${cls||''}"><div class="mh"><h2>${title}</h2><span id="mSrc"></span><button type="button" class="alt sq" data-x aria-label="Close">${icon('x')}</button></div><div class="mb" id="mBody">${body}</div>${foot?`<div class="mf" id="mFoot">${foot}</div>`:''}</div>`;
  m._close=()=>{m.remove();try{speechSynthesis.cancel()}catch(e){}};
  m.querySelector('[data-x]').onclick=()=>m._close();m.addEventListener('mousedown',e=>{if(e.target===m)m._close()});
  document.body.appendChild(m);return m;
}
async function openReport(){
  if(!runNo)return;
  const m=modal('Situation report','<div class="sk"></div><div class="sk" style="width:80%"></div><div class="sk" style="width:90%"></div><div class="sk" style="width:60%"></div>',
    '<button type="button" class="alt" id="rRe">Regenerate</button><button type="button" class="alt" id="rSay">Read aloud</button><button type="button" class="alt" id="rCopy">Copy</button><button type="button" class="alt" id="rPrint">Print or save as PDF</button>');
  const gen=async()=>{
    $('mBody').innerHTML='<div class="sk"></div><div class="sk" style="width:80%"></div><div class="sk" style="width:90%"></div>';
    try{const r=await post('/api/brief/'+runNo);if(!r.ok)throw new Error(await errOf(r,'Could not write the report.'));const d=await r.json();m._text=d.text;
      $('mSrc').innerHTML=`<span class="src" title="${esc(d.note||'')}">${d.source==='claude'?'Written by Claude':'Built-in writer'}</span>`;
      await typeInto($('mBody'),d.text)}catch(e){$('mBody').innerHTML=`<p>${esc(e.message)}</p>`}
  };
  $('rRe').onclick=gen;$('rSay').onclick=()=>m._text&&speak(m._text);
  $('rCopy').onclick=async()=>{try{await navigator.clipboard.writeText(m._text||'');$('rCopy').textContent='Copied'}catch(e){}};
  $('rPrint').onclick=()=>{const w=window.open('','_blank');if(!w)return;w.document.write(`<title>Situation report</title><body style="font:15px/1.5 system-ui;max-width:720px;margin:32px auto">${md(m._text||'')}</body>`);w.document.close();w.print()};
  await gen();
}
async function typeInto(host,text){
  const box=el('div','rep');host.innerHTML='';host.appendChild(box);
  if(reduce){box.innerHTML=md(text);return}
  const words=text.split(/(\s+)/),n=words.length,step=Math.max(2,Math.ceil(n/50));
  for(let i=step;i<n+step;i+=step){box.innerHTML=md(words.slice(0,i).join(''));host.scrollTop=host.scrollHeight;await sleep(24)}
}
$('tbReport').onclick=openReport;

/* ================================================================ map toolbar: 3D, before/after, fly-through */
$('mt3d').onclick=()=>{$('v3d').click();$('mt3d').setAttribute('aria-pressed',String(is3d))};
let touring=false;
function stopFly(){if(!touring)return;touring=false;hideSay();$('mtTour').setAttribute('aria-pressed','false');try{map.stop()}catch(e){}}
async function flyThrough(){
  if(touring){stopFly();return}
  if(!runNo||!hot.length)return;
  touring=true;$('mtTour').setAttribute('aria-pressed','true');closePop();stopOrbit();
  const names=[...document.querySelectorAll('#hots .hot')].map(b=>b.innerText.replace(/\s+/g,' ').trim());
  if(!is3d)set3d(true);await sleep(1100);
  const stops=hot.slice(0,Math.min(3,hot.length));
  for(let i=0;i<stops.length&&touring;i++){
    const h=stops[i];say(`Priority ${h.rank}`,esc(names[i]||''),5600);
    map.flyTo({center:h.c,zoom:16.3,pitch:66,bearing:-35+i*70,duration:5200,curve:1.5,essential:true});await sleep(5400);if(!touring)break;
    map.easeTo({bearing:map.getBearing()+55,duration:3800,easing:t=>t,essential:true});await sleep(3800);
  }
  if(touring){say('The whole area','Red is collapse risk. Numbered pins are where help is needed first.',6000);
    map.flyTo({center:AREA_C||map.getCenter(),zoom:12.4,pitch:58,bearing:map.getBearing()+100,duration:7000,essential:true});await sleep(7200)}
  stopFly();
}
$('mtTour').onclick=flyThrough;
document.addEventListener('keydown',e=>{if(e.key==='Escape')stopFly()});

let SW=null;
function toggleSwipe(){
  if(SW){SW.map.remove();SW.bar.remove();SW.tags.forEach(t=>t.remove());SW.host.remove();map.off('move',SW.sync);SW=null;$('mtSwipe').setAttribute('aria-pressed','false');return}
  $('mtSwipe').setAttribute('aria-pressed','true');
  const host=el('div','swipe-map'),inner=el('div');inner.style.cssText='position:absolute;inset:0';host.appendChild(inner);$('map').insertBefore(host,stage);
  const b=basemap,m2=new maplibregl.Map({container:inner,center:map.getCenter(),zoom:map.getZoom(),bearing:map.getBearing(),pitch:map.getPitch(),interactive:false,attributionControl:false,
    style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256}},layers:[{id:'osm',type:'raster',source:'osm',paint:BASE[b]}]}});
  m2.on('load',()=>{if(tileInfo){m2.addSource('bldv',{type:'vector',tiles:[location.origin+tileInfo.url],minzoom:tileInfo.minzoom,maxzoom:tileInfo.maxzoom});
    m2.addLayer({id:'bf3d',type:'fill-extrusion',source:'bldv','source-layer':tileInfo.layer,minzoom:13.5,paint:{'fill-extrusion-color':b==='dark'?'#5b6472':'#b7bec7','fill-extrusion-height':['*',['get','levels'],SM],'fill-extrusion-opacity':.9}})}});
  const sync=()=>m2.jumpTo({center:map.getCenter(),zoom:map.getZoom(),bearing:map.getBearing(),pitch:map.getPitch()});map.on('move',sync);
  const bar=el('div','swipe-bar',`<i>${icon('split')}</i>`);$('map').insertBefore(bar,stage);
  const tags=[el('div','swipe-tag','Before'),el('div','swipe-tag','After')];tags[0].style.left='60px';tags[1].style.right='12px';tags.forEach(t=>$('map').insertBefore(t,stage));
  const set=x=>{const w=$('map').clientWidth,px=Math.max(24,Math.min(w-24,x));bar.style.left=px+'px';host.style.clipPath=`inset(0 ${w-px}px 0 0)`};
  set($('map').clientWidth/2);
  bar.addEventListener('pointerdown',e=>{bar.setPointerCapture(e.pointerId);const mv=ev=>set(ev.clientX-$('map').getBoundingClientRect().left);bar.addEventListener('pointermove',mv);bar.addEventListener('pointerup',()=>bar.removeEventListener('pointermove',mv),{once:true})});
  new ResizeObserver(()=>{m2.resize();set(parseFloat(bar.style.left)||200)}).observe($('map'));
  SW={map:m2,bar,tags,host,sync};say('Drag the handle','Left: the area as it is. Right: after the earthquake.',4500);
}
$('mtSwipe').onclick=toggleSwipe;

/* ================================================================ compare scenarios */
let CM=null;
function openCompare(preset){
  if(CM)CM._close();
  const mag=+$('mag').value||6.5,tod=$('tod').value,dep=+$('depth').value||10;
  const side=(k,m,t,d)=>`<div class="sd"><b>${k}</b><div><label>Magnitude</label><input type="number" id="c${k}m" step="0.1" value="${m}"></div><div><label>Depth, km</label><input type="number" id="c${k}d" value="${d}"></div><div><label>Time</label><select id="c${k}t"><option value="night"${t==='night'?' selected':''}>Night</option><option value="day"${t==='day'?' selected':''}>Day</option><option value="commute"${t==='commute'?' selected':''}>Commute</option></select></div></div>`;
  const m=CM=modal('Compare two scenarios',
    `<div class="cmp-ctl"><div style="display:flex;gap:8px;flex-wrap:wrap"><button type="button" class="alt" data-p="mag">M6.5 and M7.5</button><button type="button" class="alt" data-p="tod">Night and day</button><button type="button" class="alt" data-p="dep">Shallow and deep</button></div>${side('A',6.5,tod,dep)}${side('B',7.5,tod,dep)}<button type="button" id="cGo" style="margin-left:auto">Compare</button></div>
     <div class="cmp-body" id="cBody"><p class="hint" style="padding:20px;margin:0">Both run at the epicentre in the form. Your scenario on the map is not changed.</p></div>`,null,'wide');
  $('mBody').style.cssText='padding:0;display:flex;flex-direction:column';
  const setv=(a,b)=>{['m','d','t'].forEach((k,i)=>{$('cA'+k).value=a[i];$('cB'+k).value=b[i]})};
  m.querySelectorAll('[data-p]').forEach(b=>b.onclick=()=>{const p=b.dataset.p;if(p==='mag')setv([6.5,dep,tod],[7.5,dep,tod]);else if(p==='tod')setv([mag,dep,'night'],[mag,dep,'day']);else setv([mag,10,tod],[mag,35,tod]);runCompare()});
  $('cGo').onclick=runCompare;
  if(preset==='mag')m.querySelector('[data-p=mag]').click();
}
async function runCompare(){
  const body=$('cBody'),go=$('cGo');go.disabled=true;go.textContent='Running';
  body.innerHTML='<div style="padding:20px"><div class="sk"></div><div class="sk" style="width:70%"></div><p class="hint">Running both scenarios on this area</p></div>';
  const sc=k=>({lat:+$('lat').value,lon:+$('lon').value,depth_km:+$('c'+k+'d').value,magnitude:+$('c'+k+'m').value,time_of_day:$('c'+k+'t').value,...optNum({strike:'strike',dip:'dip',rake:'rake'}),
    label:`${k}: M${$('c'+k+'m').value}, ${$('c'+k+'d').value} km, ${$('c'+k+'t').value}`});
  try{
    const r=await post('/api/compare',{a:sc('A'),b:sc('B')});if(!r.ok)throw new Error(await errOf(r,'Could not compare.'));const d=await r.json();
    body.innerHTML=`<div class="cmp-maps"><div class="cmp-map"><div class="m" id="cmA"></div><div class="cmp-tag">${esc(d.a.label)}<b data-v="0" id="cvA">0</b>people at risk</div></div><div class="cmp-map"><div class="m" id="cmB"></div><div class="cmp-tag">${esc(d.b.label)}<b data-v="0" id="cvB">0</b>people at risk</div></div></div><div class="cmp-tab" id="cTab"></div>`;
    countTo($('cvA'),d.a.summary.people_at_risk);countTo($('cvB'),d.b.summary.people_at_risk);
    const A=miniMap('cmA',d.a,d.bounds),B=miniMap('cmB',d.b,d.bounds);let lock=false;
    const link=(x,y)=>x.on('move',()=>{if(lock)return;lock=true;y.jumpTo({center:x.getCenter(),zoom:x.getZoom(),bearing:x.getBearing(),pitch:x.getPitch()});lock=false});link(A,B);link(B,A);
    const old=CM._close;CM._close=()=>{[A,B].forEach(x=>x.remove());CM=null;old()};
    const rows=[['People at risk',s=>s.people_at_risk],['Expected collapses',s=>s.expected_collapses],['Heavily damaged buildings',s=>s.expected_heavy!=null?s.expected_heavy:s.heavy],['Estimated deaths',s=>s.casualties&&s.casualties.deaths],
      ['Seriously injured',s=>s.casualties&&s.casualties.injury_2+s.casualties.injury_3],['Peak shaking (MMI)',s=>s.max_mmi]];
    $('cTab').innerHTML=`<table><tr><th></th><th>${esc(d.a.label)}</th><th>${esc(d.b.label)}</th><th>B compared with A</th></tr>${rows.map(([n,f])=>{const a=f(d.a.summary),b=f(d.b.summary);if(a==null||b==null)return'';
      const mmi=n.startsWith('Peak'),f1=v=>mmi?Number(v).toFixed(1):fmt(v),df=b-a,pc=a?Math.round(100*df/a):0;return`<tr><td>${n}</td><td>${f1(a)}</td><td>${f1(b)}</td><td class="${df>0?'d-up':df<0?'d-dn':''}">${df>0?'+':''}${Number.isInteger(df)?fmt(df):df.toFixed(1)}${a&&!mmi?` (${pc>0?'+':''}${pc}%)`:''}</td></tr>`}).join('')}</table>`;
  }catch(e){body.innerHTML=`<p class="hint" style="padding:20px">${esc(e.message)}</p>`}
  go.disabled=false;go.textContent='Compare';
}
function miniMap(id,res,bounds){
  const m=new maplibregl.Map({container:id,bounds:[[bounds[0],bounds[1]],[bounds[2],bounds[3]]],fitBoundsOptions:{padding:30},attributionControl:false,
    style:{version:8,sources:{osm:{type:'raster',tiles:['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],tileSize:256}},layers:[{id:'osm',type:'raster',source:'osm',paint:BASE.dark}]}});
  m.on('load',()=>{
    const sp=(res.heat.spacing_m||200),lat=(bounds[1]+bounds[3])/2,k=2.6*sp/(156543.03*Math.cos(lat*Math.PI/180));
    m.addSource('h',{type:'geojson',data:res.heat});
    m.addLayer({id:'h',type:'heatmap',source:'h',paint:{'heatmap-intensity':2.1,'heatmap-weight':['interpolate',['linear'],['get','pe'],0,0,HEAT_PE,1],'heatmap-radius':['interpolate',['exponential',2],['zoom'],8,k*256,17,k*131072],
      'heatmap-color':['interpolate',['linear'],['heatmap-density'],0,'rgba(58,166,85,0)',.08,'rgba(58,166,85,.35)',.3,'rgba(166,194,59,.55)',.5,'rgba(242,183,5,.65)',.75,'rgba(230,126,34,.75)',1,'rgba(215,38,61,.9)'],'heatmap-opacity':.92}});
    const e=document.createElement('div');e.className='epi';new maplibregl.Marker({element:e}).setLngLat([res.scenario.lon,res.scenario.lat]).addTo(m);
  });
  return m;
}
$('cmpBtn').onclick=()=>openCompare();

/* ================================================================ guided tour */
const STEPS=[
  {t:'Replay a real earthquake',s:'A Gorkha-size M7.8 on this area',go:async()=>{const p=PRESETS.find(x=>x.id==='gorkha-city');if(p){$('preset').value=p.id;await applyPreset(p,true)}else{$('run').click();await waitRunning()}tab('sum');say('Real data and published models','Buildings, ground motion, fragility curves and Monte Carlo ranges',5200);await sleep(3000)}},
  {t:'Watch the damage land',s:'Seismic waves and the heat map',go:async()=>{if(!$('waveBox').hidden){tab('map');$('tl').value=0;$('play').click();await sleep(2500)}shakeNow(7);sfx.rumble();say('Red is collapse risk','Numbered pins are where help is needed first',5000);tab('pri');await sleep(3200)}},
  {t:'Fly through the worst areas',s:'3D buildings coloured by damage',go:async()=>{await flyThrough()}},
  {t:'Can responders get there',s:'Hospitals, blocked streets and routes',go:async()=>{tab('resp');if(!respReady){say('Finding hospitals, fire stations and streets','',0);$('resp').click();for(let i=0;i<240&&!respReady;i++)await sleep(500)}
      if(respReady){hideSay();say('Which streets will debris block','Routes avoid streets likely to be closed',5500);try{await accessFor($('accOut'))}catch(e){}if(hot.length)showRoute(1)}await sleep(4200)}},
  {t:'Bring phones into the mesh',s:'Alerts and SOS calls',go:async()=>{dispOpen(true);pop('dLinkB',true);say('Scan the QR code with a phone','Open the link and allow location to join',8000);await sleep(5500);
      pop(null);$('aSend').click();await sleep(300);$('lSim').click();await sleep(1500);say('Twenty SOS calls arrive','A ring means a call is waiting for help',5200);await sleep(4000)}},
  {t:'Optimise all teams',s:'One plan for every call',go:async()=>{await post('/api/live/speed',{speed:30});LIVE.speed=30;pop('dOptB',true);await optPreview();say('One plan for every call','Compared with answering them one at a time',5200);await sleep(4800);if($('oGo'))await optApply();await sleep(500);dispOpen(true);
      say('Teams are on the way','They follow routes that avoid blocked streets, and phones see them coming',6500);await sleep(6500)}},
  {t:'Send in a drone',s:'A simulated drone scouts the worst blocks',go:async()=>{
      try{const r=await post('/api/drone/start');if(r.ok){const j=await r.json(),d=j.snapshot.fleet.find(x=>x.status==='ready');if(d){await post('/api/live/speed',{speed:30});LIVE.speed=30;await post('/api/drone/drones/'+d.id+'/launch')}}}catch(e){}
      const b=document.getElementById('drBtn');if(b)b.classList.add('on');
      say('A simulated drone scouts the worst blocks','It ranks blocks by people at risk per watt-hour, then reports what it sees',6500);await sleep(6800);
      say('Open the Drone console','Watch it fly, pair a tablet, confirm sightings, compare the updated picture',6000);await sleep(6200);if(b)b.classList.remove('on')}},
  {t:'What else could fail',s:'Fire, gas, power, bridges, aftershock',go:async()=>{tab('map');$('ovSec').open=true;if(!$('casChk').checked){$('casChk').checked=true;await $('casChk').onchange()}
      $('casT').value=0;$('casPlay').click();say('Fires spread through dense blocks','Power cuts and bridge failures are shown too',5200);await sleep(7500);stopCasPlay();$('aftBtn').click();await sleep(2600)}},
  {t:'Compare scenarios',s:'M6.5 against M7.5',go:async()=>{openCompare('mag');await sleep(7500);if(CM)CM._close()}},
  {t:'Situation report',s:'What a commander would read',go:async()=>{await openReport();await sleep(7500)}},
];
let jStep=-1,jAuto=true,jRun=0,tourEl=null;
const preflight=()=>{
  const ok=(c,t)=>`<div class="${c?'ok':'no'}"><i></i>${t}</div>`,https=/^https:/.test($('lUrl').value);
  return ok(document.getElementById('areaSel').options.length>0,'Area loaded')+ok(respReady,'Hospitals and roads')+ok(https,'Phone link is https')+ok([...LIVE.phones.values()].some(p=>p.online),'A phone is connected')+ok(AI.claude,AI.claude?'Reports written by Claude':'Reports from the built-in writer')+ok(!!mcRep,'Uncertainty ranges ready');
};
$('tbTour').onclick=()=>{
  if(tourEl){endTour();return}
  if(POP&&POP.anchor==='tbTour'){closeP();return}
  popover('tbTour',`<h3>Guided tour</h3><p class="hint" style="margin:0 0 12px">${STEPS.length} steps, about two minutes. It drives the real app.</p><div class="chk2" id="gtPre">${preflight()}</div><ol class="steps2">${STEPS.map(s=>`<li>${s.t}</li>`).join('')}</ol><button type="button" class="go" id="gtGo" style="width:100%">Start tour</button>`,p=>{
    const t=setInterval(()=>{if(POP&&POP.p===p)p.querySelector('#gtPre').innerHTML=preflight();else clearInterval(t)},1500);
    p.querySelector('#gtGo').onclick=()=>{closeP();jAuto=true;startTour()}});
};
function startTour(){
  const n=$('notice');if(n)n.hidden=true;
  tourEl=el('div','tourbar');tourEl.innerHTML=`<div class="t1"><div><b id="trT"></b><small id="trS"></small></div><button type="button" class="alt" id="trBack" aria-label="Previous step">${icon('back')}</button><button type="button" class="alt" id="trPlay" aria-label="Pause">${icon('pause')}</button><button type="button" class="alt" id="trNext" aria-label="Next step">${icon('next')}</button><button type="button" class="alt" id="trX" aria-label="End tour">${icon('x')}</button></div><div class="segs" id="trSegs"></div>`;
  stage.appendChild(tourEl);
  $('trX').onclick=endTour;$('trBack').onclick=()=>{jAuto=false;setPlay();runStep(Math.max(0,jStep-1))};$('trNext').onclick=()=>{jAuto=false;setPlay();runStep(Math.min(STEPS.length-1,jStep+1))};
  $('trPlay').onclick=()=>{jAuto=!jAuto;setPlay();if(jAuto&&!stepBusy)runStep(Math.min(STEPS.length-1,jStep+1))};
  runStep(0);
}
function setPlay(){const b=$('trPlay');if(b){b.innerHTML=icon(jAuto?'pause':'play');b.setAttribute('aria-label',jAuto?'Pause':'Play')}}
function endTour(){jRun++;stepBusy=false;if(tourEl){tourEl.remove();tourEl=null}jStep=-1;stopFly();stopCasPlay();hideSay()}
let stepBusy=false;
function drawTour(){if(!tourEl)return;$('trT').textContent=`${jStep+1} of ${STEPS.length}: ${STEPS[jStep].t}`;$('trS').textContent=STEPS[jStep].s;
  $('trSegs').innerHTML=STEPS.map((s,i)=>`<i class="${i<jStep?'done':i===jStep?'now':''}"></i>`).join('');setPlay()}
async function runStep(i){
  const my=++jRun;jStep=i;stepBusy=true;drawTour();stopFly();
  try{await STEPS[i].go()}catch(e){console.warn('tour step failed',e)}
  if(my!==jRun||!tourEl)return;stepBusy=false;
  if(jAuto&&i<STEPS.length-1)runStep(i+1);
  else if(i>=STEPS.length-1&&jAuto){say('That is the whole loop','Simulate, prioritise, reach, coordinate, report',6000);endTour()}
}

/* ================================================================ start */
when(()=>typeof map!=='undefined'&&map,()=>{
  fetch('/api/meta').then(r=>r.json()).then(m=>{if(!m.empty)AREA_C=m.center}).catch(()=>{});
  fetch('/api/ai/status').then(r=>r.json()).then(a=>{AI=a;if(a.claude)$('askSrc').textContent='Claude'}).catch(()=>{});
  updState();
});
when(()=>mapReady()&&lvLayers(),()=>meshUpdate());
})();
