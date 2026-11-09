/* Path: tools/laya_cohort_dashboard/app.js | Purpose: Render bounded evidence and explicit local controls. */
'use strict';
let snapshot, token, dirty = false, openCohorts = new Set();
const $ = id => document.getElementById(id);
const esc = value => String(value ?? 'unknown').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const fmt = (value, digits=1) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'unknown';
const date = value => typeof value === 'number' ? new Date(value * 1000).toLocaleString() : 'unknown';
const notice = (text, error=false) => { $('notice').textContent = text; $('notice').className = error ? 'warning' : ''; };
async function post(path, body){ const response = await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-Laya-Token':token},body:JSON.stringify(body)}); const result=await response.json(); if(!response.ok) throw new Error(result.error || 'Request failed'); notice('Saved. Changes appear on the next scheduler cycle.'); await refresh(); }
function chart(){
 const rows=snapshot.history.filter(row=>typeof row.updated==='number'), mode=$('metric').value;
 const definitions={ups:[['p95','#74dcb8',r=>r.metrics?.ups_p95],['p98','#ffc879',r=>r.metrics?.ups_p98]],throughput:[['total UPS','#7bbcff',r=>r.metrics?.total_ups]],temperature:[['°C','#ffc879',r=>r.metrics?.temp_c]],slots:[['active','#74dcb8',r=>r.active_slots],['target','#7bbcff',r=>r.target_slots],['effective max','#ffc879',r=>r.effective_max],['manual ceiling','#ef9ad3',r=>r.manual_target]]};
 const series=definitions[mode], values=rows.flatMap(r=>series.map(s=>s[2](r))).filter(Number.isFinite), max=Math.max(1,...values), first=rows[0]?.updated, last=rows.at(-1)?.updated, width=900,height=200;
 if(!values.length){$('chart').textContent='No measurements available.';return;}
 const lines=series.map(([name,color,get])=>{let path='', prev=null;rows.forEach(r=>{const val=get(r);if(!Number.isFinite(val)){prev=null;return}const x=40+(r.updated-first)/Math.max(1,last-first)*840,y=height-val/max*170;path+=`${prev===null||r.updated-prev>600?'M':'L'}${x.toFixed(1)},${y.toFixed(1)} `;prev=r.updated;});return `<path stroke="${color}" d="${path}"><title>${esc(name)}</title></path>`}).join('');
 $('chart').innerHTML=`<svg viewBox="0 0 900 230" role="img" aria-label="${esc(mode)} history"><line x1="40" x2="880" y1="200" y2="200"/><line x1="40" x2="880" y1="30" y2="30"/><text x="0" y="35">${esc(fmt(max))}</text><text x="0" y="203">0</text>${lines}<text x="40" y="222">${esc(date(first))}</text><text x="720" y="222">${esc(date(last))}</text></svg><div class="legend">${series.map(s=>`<span><i style="background:${s[1]}"></i>${esc(s[0])}</span>`).join('')}</div>`;
}
function render(){
 const t=snapshot.target,m=t.metrics||{}, age=typeof t.updated==='number'?Math.max(0,Math.round(snapshot.now-t.updated)):null;
 document.querySelectorAll('#ceiling button, #floors button, #cool, #cool-cancel').forEach(button=>button.disabled=!snapshot.controls_enabled);
 $('cool-status').textContent=snapshot.cooling?.until?`Scheduled restore ${date(snapshot.cooling.until)} (${Math.max(0,Math.ceil((snapshot.cooling.until-snapshot.now)/60))} min remaining)`:'No cooling request active';
 $('fresh').textContent=`Scheduler snapshot: ${age===null?'unknown':age+'s old'} · refresh 30s · ${snapshot.scheduler_compatibility}`;
 $('summary').innerHTML=[['Active runs',t.active_slots],['Admission target',t.target_slots],['p95 / p98 UPS',`${fmt(m.ups_p95)} / ${fmt(m.ups_p98)}`],['Temperature',m.temp_c==null?'unknown':fmt(m.temp_c)+' °C']].map(([a,b])=>`<div class="card"><small>${esc(a)}</small><strong>${esc(b)}</strong></div>`).join('');
 const counts={};for(let i=1;i<snapshot.history.length;i++){const prev=snapshot.history[i-1],next=snapshot.history[i],delta=next.updated-prev.updated;if(delta>0&&delta<=600) counts[prev.reason||'unknown']=(counts[prev.reason||'unknown']||0)+delta;}
 const total=Object.values(counts).reduce((a,b)=>a+b,0);$('attribution').innerHTML=`<p>Current reason: <strong>${esc(t.reason)}</strong> · observed constraints: ${esc(t.observed_constraints?.join(', ') || 'unknown or none reported')} · mode: ${esc(t.mode)} · effective max: ${esc(t.effective_max)} · memory: ${fmt(m.available_gb)} GB · sampled: ${esc(m.ups_samples)} / ${esc(t.active_slots)} runs · unstable: ${esc((t.unstable||[]).length)}</p><p class="hint">Observed reason durations (bounded history, gaps excluded): ${Object.entries(counts).sort((a,b)=>b[1]-a[1]).map(([key,value])=>`${esc(key)} ${Math.round(value/60)}m (${Math.round(value/total*100)}%)`).join(' · ')||'unknown'}</p>`;
 if(!dirty){$('floors').p95.value=snapshot.config.min_ups_p95;$('floors').p98.value=snapshot.config.min_ups_p98;$('floors').temp.value=snapshot.config.temp_limit_c;}
 chart();renderCohorts();renderListeners();renderMilestones();
}
function renderMilestones() {
 const data = snapshot.curated || {};
 const milestone = $('milestone').value;
 const index = (data.ever_order || []).indexOf(milestone);
 if (index < 0 || !Array.isArray(data.rows)) {
  $('milestones').textContent = 'Curated historical data unavailable.';
  return;
 }
 $('milestones').innerHTML = `<div class="milestone-bars">${data.rows.map(row => {
  const count = row.ever?.[index];
  const percentage = Number.isFinite(count) && row.n > 0 ? count / row.n * 100 : null;
  const detail = `C${row.cohort} ${row.arm}; commit ${row.commit}; strict ${milestone} ever ${count ?? 'unknown'}/${row.n}; final ${milestone === 'C6' ? row.c6_final ?? 'unknown' : 'unknown'}; predicate version not established by curated data; max game ticks ${data.window_max_game_ticks ?? 'unknown'}; eligibility not established by curated data`;
  return `<div class="milestone-row" title="${esc(detail)}"><span>C${esc(row.cohort)} ${esc(row.arm)}</span><span class="bar-track"><span class="bar-fill" style="width:${percentage === null ? 0 : Math.max(0, Math.min(100, percentage))}%"></span></span><span>${esc(count)} / ${esc(row.n)}${milestone === 'C6' ? ` · final ${esc(row.c6_final)}` : ''}</span></div>`;
 }).join('')}</div><p class="hint">Hover rows for frozen commit, window and predicate limitations. Source: curated Terra review; consult cohort reports for eligibility and fault accounting.</p>`;
}
function renderListeners() {
 const rows = snapshot.cohorts.flatMap(cohort => cohort.jobs.filter(job => job.listener).map(job => ({cohort: cohort.name, engine: cohort.engine, ...job})));
 $('listeners').innerHTML = rows.length ? `<h3>Running server listener evidence</h3><p>Process and IPv4 socket inspection only; network reachability and mod version parity are not proven. SSH HTTP forwarding does not forward UDP.</p><div class="scroll"><table class="runs"><thead><tr><th>Run</th><th>Process / listener</th><th>Candidate endpoint</th><th>Engine and enabled mods</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.cohort)} / ${esc(row.id)}</td><td>${esc(row.listener.status)}<br>PID ${esc(row.listener.pid)} · bind ${esc(row.listener.bind)} · checked ${esc(date(row.listener.checked_at))}</td><td><code>${esc(row.listener.endpoint)}</code><br>Route not tested</td><td>${esc(row.engine)}<br>${esc(row.mods ? JSON.stringify(row.mods) : 'unknown')}</td></tr>`).join('')}</tbody></table></div>` : '<p>No running listeners inspected.</p>';
}
function renderCohorts(){const query=$('filter').value.toLowerCase();
 $('cohorts').innerHTML=snapshot.cohorts.filter(c=>!query||c.name.toLowerCase().includes(query)||c.jobs.some(j=>`${j.id} ${j.arm}`.toLowerCase().includes(query))).sort((a,b)=>b.name.localeCompare(a.name)).map(c=>`<details class="cohort" data-name="${esc(c.name)}" ${openCohorts.has(c.name)?'open':''}><summary><strong>${esc(c.name)}</strong><span class="counts">planned ${esc(c.planned)} · ${Object.entries(c.counts).map(([k,v])=>`${esc(k)} ${v}`).join(' · ')} · unaccounted ${esc(c.unaccounted)}</span></summary>${openCohorts.has(c.name)?`<p>Updated ${esc(date(c.updated))} · queue ${esc(c.queue_status)} · engine ${esc(c.engine)} · source <code>${esc(c.source)}</code></p><div class="scroll"><table class="runs"><thead><tr><th>Run / frozen commit</th><th>State / fault</th><th>Milestones</th><th>Factorio spectator details</th><th>Review</th></tr></thead><tbody>${c.jobs.filter(j=>!query||c.name.toLowerCase().includes(query)||`${j.id} ${j.arm}`.toLowerCase().includes(query)).map(j=>{const notes=snapshot.notes.filter(n=>n.cohort===c.name&&n.run===j.id);return `<tr><td><strong>${esc(j.id)}</strong><br>${esc(j.arm)} · ${esc(j.commit)}</td><td>${esc(j.status)}<br>${esc(j.termination_cause||j.runner_stop)}<br>failure count ${esc(j.failures)}</td><td>Ever ${esc((j.accepted_ever||[]).join(', ')||'unknown')}<br>Final ${esc((j.accepted_final||[]).join(', ')||'unknown')}<br>C5 ${esc(j.c5_predicate_version)}<br>Laya C6 evidence ${esc(j.laya_c6_version)}<br>Eligible ${esc(j.comparison_eligible)}</td><td>${j.game_port?`Manifest UDP port ${esc(j.game_port)}<br>Address / listener / route unverified<br>Mods ${esc(j.mods?JSON.stringify(j.mods):'unknown')}`:'unknown'}</td><td><details class="review"><summary>Review (${notes.length})</summary>${notes.map(n=>`<div class="note">${esc(date(n.time))} · ${esc(n.tags)}<br>${esc(n.text)}</div>`).join('')}<form class="note-form" data-cohort="${esc(c.name)}" data-run="${esc(j.id)}"><label>Observation<textarea name="text" required maxlength="10000"></textarea></label><label>Tags (optional)<input name="tags" maxlength="500"></label><button>Save note</button></form></details></td></tr>`}).join('')}</tbody></table></div>`:''}</details>`).join('')||'<p>No matching cohorts.</p>';
}
async function refresh(){try{const response=await fetch('/api/snapshot');if(!response.ok)throw Error('Snapshot unavailable');snapshot=await response.json();token=snapshot.token;render()}catch(error){notice(error.message,true)}}
$('metric').addEventListener('change',chart);$('milestone').addEventListener('change',renderMilestones);$('filter').addEventListener('input',renderCohorts);$('floors').addEventListener('input',()=>dirty=true);
$('ceiling').addEventListener('submit',event=>{event.preventDefault();const f=event.currentTarget;post('/api/control',{action:'ceiling',slots:Number(f.slots.value),duration:f.duration.value.trim()}).catch(e=>notice(e.message,true))});
$('resume').addEventListener('click',()=>post('/api/control',{action:'resume'}).catch(e=>notice(e.message,true)));
$('cool').addEventListener('click',()=>post('/api/control',{action:'cool'}).catch(e=>notice(e.message,true)));
$('cool-cancel').addEventListener('click',()=>post('/api/control',{action:'cool_cancel'}).catch(e=>notice(e.message,true)));
$('floors').addEventListener('submit',event=>{event.preventDefault();const f=event.currentTarget;post('/api/control',{action:'configure',p95:Number(f.p95.value),p98:Number(f.p98.value),temp:Number(f.temp.value)}).then(()=>dirty=false).catch(e=>notice(e.message,true))});
$('cohorts').addEventListener('submit',event=>{if(!event.target.matches('.note-form'))return;event.preventDefault();const f=event.target;post('/api/note',{cohort:f.dataset.cohort,run:f.dataset.run,text:f.text.value,tags:f.tags.value}).catch(e=>notice(e.message,true))});
$('cohorts').addEventListener('click',event=>{const summary=event.target.closest('.cohort > summary');if(!summary)return;event.preventDefault();const name=summary.parentElement.dataset.name;if(openCohorts.has(name))openCohorts.delete(name);else openCohorts.add(name);renderCohorts()});
refresh();setInterval(()=>{if(!document.querySelector('.note-form:focus-within'))refresh()},30000);
