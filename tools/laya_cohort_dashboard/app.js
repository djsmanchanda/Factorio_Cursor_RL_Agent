/* Path: tools/laya_cohort_dashboard/app.js | Purpose: Render bounded evidence and explicit local controls; mutations only follow a user action. */
'use strict';
const $ = id => document.getElementById(id), $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? 'unknown').replace(/[&<>"']/g, character => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));
const isNum = value => typeof value === 'number' && Number.isFinite(value);
// The host gate samples UPS as 1x-equivalent (measured ÷ game speed). When every
// running world shares one speed, show measured UPS and scale the floors to it, so
// 2x worlds at 115 UPS no longer read as "57" (owner 2026-11-08); mixed speeds stay 1x-eq.
const liveSpeed = m => Array.isArray(m?.game_speeds) && m.game_speeds.length === 1 && m.game_speeds[0] > 0 ? m.game_speeds[0] : 1;
const sx = (v, s) => isNum(v) ? v * s : v;
const fmt = (value, digits = 1) => isNum(value) ? value.toFixed(digits) : 'unknown';
const pct = (part, whole) => isNum(part) && isNum(whole) && whole > 0 ? Math.round(part / whole * 100) : null;
const when = ts => isNum(ts) ? new Date(ts * 1000).toLocaleString([], {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'}) : 'unknown';
const clock = ts => isNum(ts) ? new Date(ts * 1000).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', second: '2-digit'}) : 'unknown';
const span = seconds => !isNum(seconds) ? 'unknown' : seconds < 90 ? `${Math.round(seconds)}s` : seconds < 5400 ? `${Math.round(seconds / 60)}m` : `${Math.floor(seconds / 3600)}h ${Math.round(seconds % 3600 / 60)}m`;
const tail = (value, n = 12) => String(value ?? '').slice(0, n);
const chip = (text, tone = 'muted', title = '') => `<span class="chip" data-tone="${tone}"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;

// Page-session preferences. The snapshot token is never stored anywhere.
const keep = (storage, key, fallback) => { try { return JSON.parse(storage.getItem(key)) ?? fallback; } catch { return fallback; } };
const save = (storage, key, value) => { try { storage.setItem(key, JSON.stringify(value)); } catch { /* storage may be unavailable */ } };
const ui = {tab: 'overview', hours: 6, panels: ['runs', 'ups', 'temp'], hiddenSeries: [], family: 'strict', milestone: 'C6', registryState: 'all', unranked: false, historyView: 'trend', trendMetric: 'ever', trendMilestones: ['C2', 'C3', 'C4', 'C5', 'C6'], trendWindow: 'all', trendExp: 'mean', trendCI: false, ...keep(localStorage, 'laya.ui.v1', {})};
const persistUi = () => save(localStorage, 'laya.ui.v1', ui);
const drafts = new Map(Object.entries(keep(sessionStorage, 'laya.drafts.v1', {})));
const persistDrafts = () => save(sessionStorage, 'laya.drafts.v1', Object.fromEntries(drafts));

let snapshot = null, token = '', lastOk = 0, loadError = null, inflight = false;
let hist = {hours: ui.hours, data: null, loading: false, error: null};
const openState = new Map(), busy = new Set(), results = new Map(), notesState = new Map(), pageSize = new Map(), jobFilter = new Map(), expanded = new Set(JSON.parse(sessionStorage.getItem('laya.expanded.v1') || '[]'));
let ceilingDirty = false, floorsDirty = false, modeDirty = false, bumpDelta = null, bumpedAt = 0, ceilingScope = 'permanent', registryShown = 15, historyShown = 14, liveShown = 4, syncTarget = null;

const REASONS = {manual_target: 'Manual ceiling in effect', hold: 'Holding steady', trial_increase: 'Trying one more run', trial_wait: 'Waiting after a trial', trial_window: 'Measuring a trial', trial_incomplete: 'Trial inconclusive', ups_growth_hold: 'UPS headroom too low to grow', unhealthy_pending: 'Unhealthy sample, confirming', unhealthy: 'Unhealthy, reducing', settling: 'Settling after a change', sweet_spot: 'At measured sweet spot', runner_unstable: 'Runners unstable', no_measurement: 'No measurement'};
const reasonLabel = reason => REASONS[reason] || String(reason ?? 'unknown').replace(/_/g, ' ');
const GROUPS = {ceiling: 'Manual ceiling', ups: 'UPS headroom', thermal: 'Temperature', memory: 'Memory', health: 'Unhealthy / unstable runners', search: 'Search & settling (not a limit)', nodata: 'No measurement', other: 'Other / unclassified'};
const groupOf = reason => { const r = String(reason ?? ''); return /manual/.test(r) ? 'ceiling' : /ups/.test(r) ? 'ups' : /therm|temp|hot|cool/.test(r) ? 'thermal' : /mem/.test(r) ? 'memory' : /unhealthy|unstable/.test(r) ? 'health' : /no_measure|unknown/.test(r) ? 'nodata' : /trial|settl|sweet|hold|search/.test(r) ? 'search' : 'other'; };

// ---------------------------------------------------------------- plumbing
const setHTML = (element, html) => { if (!element || element._html === html) return false; element._html = html; element.innerHTML = html; return true; };
function keepFocus(work) {
 const active = document.activeElement, key = active?.dataset?.fk, start = active?.selectionStart, end = active?.selectionEnd, scroll = active?.scrollTop;
 work();
 if (!key) return;
 const next = $$('[data-fk]').find(element => element.dataset.fk === key);
 if (next && next !== document.activeElement) { next.focus({preventScroll: true}); try { if (isNum(start)) next.setSelectionRange(start, end); } catch { /* not a text control */ } if (isNum(scroll)) next.scrollTop = scroll; }
}
function toast(text, tone = 'info') {
 const box = $('toasts');
 $$('.toast', box).filter(item => item.dataset.text === text).forEach(item => item.remove());
 const item = document.createElement('div');
 item.className = 'toast'; item.dataset.tone = tone; item.dataset.text = text; item.setAttribute('role', tone === 'bad' ? 'alert' : 'status');
 item.innerHTML = `<span>${esc(text)}</span><button type="button" class="secondary" aria-label="Dismiss">×</button>`;
 item.querySelector('button').onclick = () => item.remove();
 box.append(item);
 if (tone !== 'bad') setTimeout(() => item.remove(), tone === 'ok' ? 8000 : 5000);
}
async function getJSON(url) {
 const response = await fetch(url); let data = null;
 try { data = await response.json(); } catch { /* non-JSON error */ }
 if (!response.ok) throw new Error(data?.error || `${url.split('?')[0]} failed (${response.status})`);
 return data;
}
async function post(path, body) {
 const response = await fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Laya-Token': token}, body: JSON.stringify(body)});
 let result = {}; try { result = await response.json(); } catch { /* keep empty */ }
 if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
 return result;
}
// Run an explicit mutation: shows busy/success/error state and refreshes afterwards.
async function act(key, work, okMessage) {
 if (busy.has(key)) return false;
 busy.add(key); results.delete(key); renderBusy();
 try {
  const result = await work(); const message = result?.message || okMessage;
  results.set(key, {ok: true, message, time: Date.now()}); busy.delete(key); toast(message, 'ok'); renderBusy(); await refresh(); return true;
 } catch (error) {
  results.set(key, {ok: false, message: error.message, time: Date.now()}); busy.delete(key); toast(error.message, 'bad'); renderBusy(); return false;
 }
}
const renderBusy = () => { syncControls(); renderLive(); if (ui.tab === 'jobs') renderJobs(); };
const anyControlBusy = () => [...busy].some(key => key.startsWith('control:'));
const resultLine = key => { const r = results.get(key); return busy.has(key) ? '<p class="result-line" role="status"><span class="spinner"></span>Working…</p>' : r ? `<p class="result-line" data-tone="${r.ok ? 'ok' : 'bad'}" role="${r.ok ? 'status' : 'alert'}">${r.ok ? '✓' : '✕'} ${esc(r.message)} · ${esc(clock(r.time / 1000))}</p>` : ''; };

// ---------------------------------------------------------------- refresh loop
async function refresh(manual = false) {
 if (inflight) return;
 inflight = true; if (manual) $('refresh').disabled = true;
 try {
  const next = await getJSON('/api/snapshot');
  token = next.token; delete next.token;
  snapshot = next; lastOk = Date.now(); loadError = null;
  if (ui.hours === 6) hist = {hours: 6, data: next.history_window, loading: false, error: null};
  else loadHistory(true);
  renderAll();
 } catch (error) { loadError = error.message; renderStatus(); }
 finally { inflight = false; $('refresh').disabled = false; }
}
async function loadHistory(quiet = false) {
 const hours = ui.hours;
 if (hours === 6 && snapshot?.history_window) { hist = {hours, data: snapshot.history_window, loading: false, error: null}; return drawPerformance(); }
 if (!quiet || hist.hours !== hours) hist = {hours, data: hist.hours === hours ? hist.data : null, loading: true, error: null};
 drawPerformance();
 try { const data = await getJSON('/api/history?hours=' + hours); if (ui.hours === hours) hist = {hours, data, loading: false, error: null}; }
 catch (error) { if (ui.hours === hours) hist = {hours, data: hist.hours === hours ? hist.data : null, loading: false, error: error.message}; }
 drawPerformance();
}

// ---------------------------------------------------------------- status + tabs
function renderStatus() {
 const fresh = $('fresh'), banner = $('banner');
 if (!snapshot) { fresh.textContent = loadError ? 'Offline' : 'Connecting…'; fresh.dataset.tone = loadError ? 'bad' : 'muted'; }
 else {
  const t = snapshot.target || {}, drift = (Date.now() - lastOk) / 1000, age = isNum(t.updated) ? Math.max(0, snapshot.now - t.updated + drift) : null;
  fresh.textContent = loadError ? `Offline · data from ${clock(lastOk / 1000)}` : age === null ? 'Scheduler sample unknown' : `Scheduler sample ${span(age)} old`;
  fresh.dataset.tone = loadError ? 'bad' : age === null || age > 180 ? 'warn' : 'ok';
  fresh.title = `${snapshot.scheduler_compatibility || ''} · page refreshed ${span(drift)} ago · auto-refresh every 30s`;
 }
 banner.hidden = !loadError;
 setHTML(banner, loadError ? `<div class="callout" data-tone="bad" role="alert"><strong>Cannot reach the dashboard server:</strong> ${esc(loadError)}. ${snapshot ? 'Showing the last loaded data; controls still use the server’s own checks. ' : ''}<button type="button" class="small" data-act="retry">Retry now</button></div>` : '');
}
function showTab(name, focus = false) {
 if (!['overview', 'performance', 'jobs', 'history'].includes(name)) name = 'overview';
 ui.tab = name; persistUi();
 $$('[role=tab]').forEach(tab => { const on = tab.dataset.tab === name; tab.setAttribute('aria-selected', on); tab.tabIndex = on ? 0 : -1; if (on && focus) tab.focus(); });
 $$('.view').forEach(view => view.hidden = view.id !== 'view-' + name);
 history.replaceState(null, '', '#' + name);
 if (snapshot) renderActiveTab();
}
function renderActiveTab() {
 if (ui.tab === 'performance') { drawPerformance(); if (hist.data === null || (ui.hours !== 6 && hist.hours !== ui.hours)) loadHistory(); }
 else if (ui.tab === 'jobs') renderJobs();
 else if (ui.tab === 'history') renderHistory();
}
function renderAll() {
 renderStatus(); renderCapacity(); syncControls(); renderLive(); renderActiveTab();
 const servers = snapshot.cohorts.reduce((n, c) => n + c.jobs.filter(j => j.status === 'running').length, 0);
 $('jobs-count').textContent = servers ? String(servers) : '';
}

// ---------------------------------------------------------------- capacity analysis
function analyze() {
 const t = snapshot.target || {}, m = t.metrics || {}, c = snapshot.config || {}, mode = t.mode || c.mode || 'unknown';
 const manual = isNum(t.manual_target) ? t.manual_target : isNum(c.manual_target) ? c.manual_target : null;
 const manualUntil = isNum(t.manual_target_until) ? t.manual_target_until : isNum(c.manual_target_until) ? c.manual_target_until : null;
 const cap = isNum(t.effective_max) ? t.effective_max : null, caps = [manual, cap].filter(isNum);
 const upper = caps.length ? Math.min(...caps) : isNum(c.max_slots) ? c.max_slots : null;
 const growth = isNum(t.growth_ups_p95) ? t.growth_ups_p95 : isNum(c.min_ups_p95) && isNum(c.growth_margin_ups) ? c.min_ups_p95 + c.growth_margin_ups : null;
 const tempLimit = mode === 'cool' && isNum(c.cool_temp_limit_c) ? c.cool_temp_limit_c : c.temp_limit_c, tempGrow = isNum(tempLimit) && isNum(c.temp_recover_margin_c) ? tempLimit - c.temp_recover_margin_c : null;
 const unstable = Array.isArray(t.unstable) ? t.unstable : [], limits = [];
 if (isNum(upper) && isNum(t.target_slots) && t.target_slots >= upper && (t.queued ?? 1) > 0)
  limits.push({id: 'ceiling', tone: 'limit', label: manual !== null && manual <= (cap ?? Infinity) ? 'Manual ceiling' : `Automatic cap (${mode} mode)`, detail: `target ${t.target_slots} has reached the upper limit ${upper}`});
 const s = liveSpeed(m);
 if (isNum(m.ups_p95) && isNum(c.min_ups_p95) && m.ups_p95 < c.min_ups_p95) limits.push({id: 'ups', tone: 'breach', label: 'UPS below floor', detail: `p95 coverage ${fmt(m.ups_p95 * s)} < floor ${fmt(c.min_ups_p95 * s)}`});
 else if (isNum(m.ups_p98) && isNum(c.min_ups_p98) && m.ups_p98 < c.min_ups_p98) limits.push({id: 'ups', tone: 'breach', label: 'UPS below floor', detail: `p98 coverage ${fmt(m.ups_p98 * s)} < floor ${fmt(c.min_ups_p98 * s)}`});
 else if (isNum(m.ups_p95) && isNum(growth) && m.ups_p95 < growth) limits.push({id: 'ups', tone: 'limit', label: 'UPS headroom', detail: `p95 coverage ${fmt(m.ups_p95 * s)} is under the growth threshold ${fmt(growth * s)}`});
 if (isNum(m.temp_c) && isNum(tempLimit) && m.temp_c >= tempLimit) limits.push({id: 'temp', tone: 'breach', label: 'Over temperature target', detail: `${fmt(m.temp_c)} °C ≥ target ${fmt(tempLimit)} °C`});
 else if (isNum(m.temp_c) && isNum(tempGrow) && m.temp_c >= tempGrow) limits.push({id: 'temp', tone: 'limit', label: 'Temperature headroom', detail: `${fmt(m.temp_c)} °C is within ${fmt(tempLimit - tempGrow)} °C of the ${fmt(tempLimit)} °C target`});
 if (isNum(m.available_gb) && isNum(c.memory_min_gb) && m.available_gb <= c.memory_min_gb) limits.push({id: 'memory', tone: 'breach', label: 'Low memory', detail: `${fmt(m.available_gb)} GB ≤ minimum ${fmt(c.memory_min_gb)} GB`});
 if (unstable.length) limits.push({id: 'unstable', tone: 'limit', label: 'Runner instability', detail: `${unstable.length} unstable run${unstable.length === 1 ? '' : 's'} reported`});
 return {t, m, c, mode, manual, manualUntil, cap, upper, growth, tempLimit, tempGrow, unstable, limits, queued: t.queued, max: c.max_slots};
}
const meter = (value, max, marks = [], min = 0) => {
 const at = v => `${Math.max(0, Math.min(100, (v - min) / Math.max(1e-9, max - min) * 100))}%`;
 return `<div class="meter" aria-hidden="true">${isNum(value) ? `<i style="width:${at(value)}"></i>` : ''}${marks.filter(mark => isNum(mark[0])).map(([v, cls, label]) => `<b class="${cls}" style="left:${at(v)}" title="${esc(label)}"></b>`).join('')}</div>`;
};
const stateChip = state => state === 'breach' ? chip('✕ Past limit', 'bad') : state === 'limit' ? chip('▲ Limiting', 'warn') : state === 'none' ? chip('○ No data', 'muted') : chip('● OK', 'ok');
function renderCapacity() {
 const a = analyze(), {t, m, c} = a, active = t.active_slots, target = t.target_slots;
 const worst = !isNum(t.updated) && !isNum(active) ? 'muted' : a.limits.find(l => l.tone === 'breach') ? 'bad' : a.limits.length ? 'warn' : a.queued === 0 ? 'muted' : 'ok';
 let head, sub;
 if (!isNum(t.updated) && !isNum(active)) { head = 'No scheduler sample yet'; sub = 'The scheduler has not published a sample, so limits cannot be assessed. Nothing below is a measurement.'; }
 else if (a.limits.length) { head = `${isNum(active) ? active : '?'} running · limited by ${a.limits.map(l => l.label).join(' + ')}`; sub = a.limits.map(l => l.detail).join(' · '); }
 else if (a.queued === 0) { head = 'No queued jobs to admit'; sub = 'Nothing is waiting, so no capacity limit is being exercised.'; }
 else { head = `No limit flagged · target ${fmt(target, 0)} of upper limit ${fmt(a.upper, 0)}`; sub = 'Latest sample is inside every growth threshold; the scheduler is still settling or testing one more run.'; }
 const constraints = Array.isArray(t.observed_constraints) && t.observed_constraints.length ? t.observed_constraints.map(x => chip(String(x), 'warn')).join('') : '';
 setHTML($('verdict'), `<h3>${esc(head)}</h3><p>${esc(sub)}</p><div class="chips">${chip('Running ' + fmt(active, 0))}${chip('Admission target ' + fmt(target, 0))}${chip('Upper limit ' + fmt(a.upper, 0))}${chip('Queued ' + fmt(a.queued, 0))}${chip('Mode ' + a.mode)}${chip('Scheduler: ' + reasonLabel(t.reason), 'info', 'Recorded reason: ' + (t.reason ?? 'unknown'))}${constraints}</div>`);
 $('verdict').dataset.tone = worst;
 const by = id => a.limits.find(l => l.id === id)?.tone;
 const scaleRuns = Math.max(a.max || 0, a.upper || 0, target || 0, active || 0, 1);
 const runs = `<div class="gauge" data-state="${by('ceiling') || 'ok'}"><div class="gauge-head"><h3>Concurrent runs</h3>${stateChip(by('ceiling') || (isNum(active) ? 'ok' : 'none'))}</div><div class="gauge-value">${esc(fmt(active, 0))} <small>of ${esc(fmt(a.upper, 0))} allowed</small></div>${meter(active, scaleRuns, [[target, '', 'Admission target'], [a.upper, 'dash', 'Upper limit']])}<dl><dt>Admission target</dt><dd>${esc(fmt(target, 0))}</dd><dt>Manual ceiling</dt><dd>${a.manual === null ? 'none (automatic)' : esc(a.manual) + (a.manualUntil ? ` until ${esc(when(a.manualUntil))}` : ' · permanent')}</dd><dt>Automatic cap</dt><dd>${esc(fmt(a.cap, 0))} (${esc(a.mode)})</dd><dt>Hard maximum</dt><dd>${esc(fmt(a.max, 0))}</dd></dl><div class="gauge-actions"><button type="button" class="link" data-focus="bump-h">Raise admission target</button><button type="button" class="link" data-focus="mode-h">Change power mode</button></div></div>`;
 const s = liveSpeed(m), speeds = (m.game_speeds || []).map(v => `${v}x`).join('/') || '—', basis = (m.game_speeds || []).length > 1 ? ' (1x-equivalent, mixed speeds)' : '';
 const p95 = sx(m.ups_p95, s), floor95 = sx(c.min_ups_p95, s), growthUps = sx(a.growth, s);
 const upsState = by('ups') || (isNum(m.ups_p95) ? 'ok' : 'none'), scaleUps = Math.max(p95 || 0, growthUps || 0, floor95 || 0, 1) * 1.15;
 const ups = `<div class="gauge" data-state="${upsState}"><div class="gauge-head"><h3>Slow-tail UPS${esc(basis)} · p95 coverage</h3>${stateChip(upsState)}</div><div class="gauge-value">${esc(fmt(p95))} <small>UPS · p98 ${esc(fmt(sx(m.ups_p98, s)))} · ${esc(speeds)}</small></div>${meter(p95, scaleUps, [[floor95, 'red', 'p95 floor'], [growthUps, 'dash', 'Growth threshold']])}<dl><dt>p95 floor / growth</dt><dd>${esc(fmt(floor95))} / ${esc(fmt(growthUps))}${s !== 1 ? ` <small>(${esc(fmt(c.min_ups_p95))} / ${esc(fmt(a.growth))} at 1x)</small>` : ''}</dd><dt>p98 floor</dt><dd>${esc(fmt(sx(c.min_ups_p98, s)))}</dd><dt>Runs sampled</dt><dd>${esc(fmt(m.ups_samples, 0))} of ${esc(fmt(active, 0))}</dd><dt>Aggregate UPS${esc(basis)}</dt><dd>${esc(fmt(sx(m.total_ups, s), 0))}</dd><dt>Mean measured UPS · speed</dt><dd>${esc(fmt(m.measured_ups_mean, 0))} · ${esc(speeds)}</dd></dl></div>`;
 const tempState = by('temp') || (isNum(m.temp_c) ? 'ok' : 'none');
 const temp = `<div class="gauge" data-state="${tempState}"><div class="gauge-head"><h3>Temperature</h3>${stateChip(tempState)}</div><div class="gauge-value">${esc(fmt(m.temp_c))} <small>°C of ${esc(fmt(a.tempLimit))} target</small></div>${meter(m.temp_c, Math.max(c.temp_hard_c || 100, m.temp_c || 0), [[a.tempGrow, 'dash', 'Growth boundary'], [a.tempLimit, 'dash', 'Target'], [c.temp_hard_c, 'red', 'Hard limit']], 20)}<dl><dt>Growth boundary</dt><dd>${esc(fmt(a.tempGrow))} °C</dd><dt>Hard limit</dt><dd>${esc(fmt(c.temp_hard_c))} °C</dd><dt>Power profile</dt><dd>${esc(m.power_profile)}</dd></dl><button type="button" class="link" data-focus="floors">Edit temperature target</button></div>`;
 const memState = by('memory') || by('unstable') || (isNum(m.available_gb) ? 'ok' : 'none');
 const mem = `<div class="gauge" data-state="${memState}"><div class="gauge-head"><h3>Memory &amp; stability</h3>${stateChip(memState)}</div><div class="gauge-value">${esc(fmt(m.available_gb))} <small>GB free</small></div>${meter(m.available_gb, Math.max((c.memory_min_gb || 4) * 4, m.available_gb || 0), [[c.memory_min_gb, 'red', 'Minimum free memory']])}<dl><dt>Minimum free</dt><dd>${esc(fmt(c.memory_min_gb))} GB</dd><dt>Unstable runs</dt><dd title="${esc(a.unstable.join(', '))}">${a.unstable.length}${a.unstable.length ? ' · ' + esc(a.unstable.slice(0, 2).join(', ')) + (a.unstable.length > 2 ? '…' : '') : ''}</dd><dt>Mode</dt><dd>${esc(a.mode)}</dd></dl></div>`;
 setHTML($('gauges'), runs + ups + temp + mem);
}

// ---------------------------------------------------------------- controls
const DURATION = /^[1-9][0-9]{0,4}[smhd]$/;
function ceilingDuration() {
 if (ceilingScope === 'permanent') return '';
 const form = $('ceiling'); return form.duration.value === 'custom' ? form.duration_custom.value.trim() : form.duration.value;
}
function ceilingProblem() {
 const form = $('ceiling'), raw = form.slots.value.trim(), n = Number(raw), max = Number(form.slots.max) || 1000;
 if (raw === '' || !Number.isInteger(n) || n < 0) return 'Enter a whole number of runs (0 or more).';
 if (n > max) return `The scheduler maximum is ${max} runs.`;
 if (ceilingScope === 'temporary' && !DURATION.test(ceilingDuration())) return 'Duration must be a number then s, m, h or d, e.g. 90m.';
 return '';
}
function floorsProblem() {
 const f = $('floors'), [p95, p98, temp] = ['p95', 'p98', 'temp'].map(name => Number(f[name].value));
 if (![p95, p98, temp].every(v => Number.isFinite(v) && v > 0) || f.p95.value === '' || f.p98.value === '' || f.temp.value === '') return 'Enter positive numbers for both UPS floors and the temperature target.';
 if (snapshot && isNum(snapshot.config.temp_hard_c) && temp > snapshot.config.temp_hard_c) return `Temperature target cannot exceed the hard limit ${snapshot.config.temp_hard_c} °C.`;
 return '';
}
// Mirrors the scheduler: per-mode run cap and temperature target come from its config, not from constants here.
const MODES = {auto: {label: 'Auto', profile: null}, cool: {label: 'Cool', profile: 'power-saver'}, balanced: {label: 'Balanced', profile: 'balanced'}, performance: {label: 'Performance', profile: 'performance'}};
const modeCap = (c, mode) => !isNum(c.max_slots) ? null : mode === 'cool' ? Math.min(c.max_slots, isNum(c.cool_max_slots) ? c.cool_max_slots : c.max_slots) : mode === 'balanced' ? (isNum(c.balanced_max_slots) ? c.balanced_max_slots : Math.round(c.max_slots * 0.75)) : c.max_slots;
const modeTemp = (c, mode) => mode === 'cool' ? c.cool_temp_limit_c : c.temp_limit_c;
const profileMode = profile => ({'power-saver': 'cool', balanced: 'balanced', performance: 'performance'})[profile] || 'balanced';
function syncControls() {
 if (!snapshot) return;
 const a = analyze(), c = snapshot.config, locked = !snapshot.controls_enabled, working = anyControlBusy();
 $$('[data-control]').forEach(element => element.disabled = locked || working);
 const lock = $('lock-banner'); lock.hidden = !locked;
 lock.innerHTML = locked ? `<strong>Controls are locked.</strong> ${esc(snapshot.scheduler_compatibility)}. They unlock only after the live scheduler passes its protocol, source and heartbeat check.` : '';
 const state = $('ctl-state'); state.textContent = working ? 'Applying…' : locked ? 'Locked' : 'Unlocked'; state.dataset.tone = working ? 'info' : locked ? 'warn' : 'ok';
 const label = (id, key, idle, doing) => { const button = $(id); button.textContent = busy.has(key) ? doing : idle; button.setAttribute('aria-busy', busy.has(key)); };
 label('ceiling-apply', 'control:ceiling', 'Apply limit', 'Applying…'); label('resume', 'control:resume', 'Resume automatic', 'Resuming…'); label('floors-save', 'control:floors', 'Save safeguards', 'Saving…'); label('cool', 'control:cool', 'Cool for 1 hour', 'Starting cooling…'); label('cool-cancel', 'control:cool_cancel', 'Cancel cooling', 'Cancelling…');
 const form = $('ceiling'); form.slots.max = isNum(c.max_slots) ? c.max_slots : 1000;
 $('ceiling-now').textContent = `Now: ${a.manual === null ? 'no manual ceiling' : `manual ceiling ${a.manual}${a.manualUntil ? ' until ' + when(a.manualUntil) : ' (permanent)'}`} · automatic cap ${fmt(a.cap, 0)} (${a.mode} mode) · maximum ${fmt(c.max_slots, 0)}.`;
 if (!ceilingDirty) form.slots.value = a.manual ?? a.cap ?? c.max_slots ?? '';
 $('ceiling-reset').hidden = !ceilingDirty;
 const flo = $('floors'); flo.temp.max = isNum(c.temp_hard_c) ? c.temp_hard_c : 120;
 if (!floorsDirty) { flo.p95.value = c.min_ups_p95; flo.p98.value = c.min_ups_p98; flo.temp.value = c.temp_limit_c; }
 $('floors-reset').hidden = !floorsDirty;
 $('p95-now').textContent = `now ${fmt(c.min_ups_p95)}`; $('p98-now').textContent = `now ${fmt(c.min_ups_p98)}`; $('temp-now').textContent = `now ${fmt(c.temp_limit_c)} · hard ${fmt(c.temp_hard_c)}`;
 const cool = snapshot.cooling || {};
 $('cool-status').textContent = isNum(cool.until) ? `Cooling active · power-saver until ${when(cool.until)} (${Math.max(0, Math.ceil((cool.until - snapshot.now) / 60))} min left), then ${cool.previous_profile || 'balanced'} and your previous mode return automatically.` : 'No cooling request active.';
 setHTML($('bump-result'), resultLine('control:bump')); setHTML($('mode-result'), resultLine('control:mode'));
 updatePreviews(locked, working);
}
function updatePreviews(locked, working) {
 const a = analyze(), problem = ceilingProblem(), form = $('ceiling'), n = Number(form.slots.value), preview = $('ceiling-preview');
 $('duration-row').hidden = ceilingScope !== 'temporary';
 $('duration-custom').hidden = ceilingScope !== 'temporary' || form.duration.value !== 'custom';
 $$('[data-scope]').forEach(button => button.setAttribute('aria-pressed', button.dataset.scope === ceilingScope));
 form.slots.setAttribute('aria-invalid', Boolean(problem) && form.slots.value !== '' ? 'true' : 'false');
 if (problem) { preview.textContent = problem; preview.dataset.tone = 'bad'; }
 else {
  const running = a.t.active_slots, bits = [`Sets at most ${n} concurrent run${n === 1 ? '' : 's'} ${ceilingScope === 'permanent' ? 'permanently' : `for ${ceilingDuration()}, then restores the earlier policy`}.`];
  if (n === 0) bits.push('Zero pauses new admissions.'); else if (isNum(running) && n < running) bits.push(`${running} are running: none are killed; admission waits until runs finish.`); else bits.push('A limit never forces admissions; safeguards may admit fewer.');
  preview.textContent = bits.join(' '); preview.dataset.tone = '';
 }
 $('ceiling-apply').disabled = locked || working || Boolean(problem);
 const fp = floorsProblem(), f = $('floors'), hint = $('floors-hint');
 if (fp) { hint.textContent = fp; hint.dataset.tone = 'bad'; }
 else if (Number(f.p98.value) > Number(f.p95.value)) { hint.textContent = 'p98 coverage is a deeper slow tail, so its floor is normally at or below the p95 floor.'; hint.dataset.tone = 'warn'; }
 else { hint.textContent = 'p95 / p98 = slow-tail coverage: 95% / 98% of sampled runs must reach the floor.'; hint.dataset.tone = ''; }
 $('floors-save').disabled = locked || working || Boolean(fp);
 updateBump(a, locked, working); updateMode(a, locked, working);
}

// Raising the target is a one-off nudge: the scheduler restarts its search from the higher value and every safeguard still applies.
function updateBump(a, locked, working) {
 const {t, c} = a, target = t.target_slots, max = c.max_slots, button = $('bump-apply'), preview = $('bump-preview');
 if (bumpDelta === null) bumpDelta = 2;
 $$('[data-bump]').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.bump) === bumpDelta)));
 if (!isNum(target)) { $('bump-now').textContent = 'No scheduler sample yet, so there is no admission target to raise.'; preview.textContent = ''; button.textContent = 'Raise admission target'; button.disabled = true; return; }
 const to = isNum(max) ? Math.min(max, target + bumpDelta) : target + bumpDelta, atMax = isNum(max) && target >= max;
 const postCap = a.manual === null ? a.cap : modeCap(c, a.mode), bits = [];
 $('bump-now').textContent = `Admission target ${target} · running ${fmt(t.active_slots, 0)} · queued ${fmt(a.queued, 0)} · scheduler maximum ${fmt(max, 0)}.`;
 button.textContent = busy.has('control:bump') ? 'Raising…' : atMax ? 'Already at maximum' : `Raise to ${to}`;
 button.setAttribute('aria-busy', busy.has('control:bump'));
 let tone = '';
 if (atMax) { bits.push(`The target is already at the scheduler maximum (${max}); raise max slots in the scheduler configuration to go higher.`); tone = 'warn'; }
 else {
  bits.push(`Restarts the automatic search from ${to} instead of waiting for the next growth trial. UPS, temperature, memory and stability safeguards still apply and can step it back down.`);
  if (a.manual !== null) { bits.push(`Also clears the manual ceiling of ${a.manual}${a.manualUntil ? ' (until ' + when(a.manualUntil) + ')' : ''}.`); tone = 'warn'; }
  if (isNum(postCap) && to > postCap) { bits.push(`${MODES[a.mode]?.label ?? a.mode} mode${a.manual === null ? ' and the current safeguards' : ''} cap admission at ${postCap}, so anything above that is pulled back on the next cycle. Change the power mode to allow more.`); tone = 'warn'; }
  if (a.queued === 0) bits.push('Nothing is queued, so a higher target admits nothing yet.');
  if (bumpedAt && !(t.updated >= bumpedAt)) bits.unshift('Your last raise is waiting for the next scheduler sample; raising again stacks on top of it.');
 }
 preview.textContent = bits.join(' '); preview.dataset.tone = tone;
 button.disabled = locked || working || atMax;
}

// The persistent mode picks the scheduler policy (run cap, temperature target, growth speed); unlike the 1-hour cooling it never expires.
function modeView(a) {
 const form = $('mode'), c = a.c, current = MODES[c.mode] ? c.mode : 'auto', profile = a.m.power_profile, system = profileMode(profile), cooling = isNum((snapshot.cooling || {}).until);
 if (!modeDirty) form.mode.value = current;
 const pick = form.mode.value || current, wants = form.profile.checked && MODES[pick].profile;
 const same = !cooling && pick === current && (pick === 'auto' || !form.profile.checked || profile === MODES[pick].profile);
 return {form, c, current, profile, system, pick, wants, same, cooling, resolved: pick === 'auto' ? system : pick};
}
function updateMode(a, locked, working) {
 const v = modeView(a), {c} = v, cool = snapshot.cooling || {};
 $('mo-auto').textContent = `Follows the system power profile${v.profile ? ` (now ${v.profile}, so ${MODES[v.system].label.toLowerCase()} policy)` : ''}.`;
 for (const k of ['cool', 'balanced', 'performance']) $('mo-' + k).textContent = `Up to ${fmt(modeCap(c, k), 0)} runs · ${fmt(modeTemp(c, k))} °C target${k === 'cool' ? ' · grows more slowly' : ''}`;
 $$('.mode-opt').forEach(label => { label.dataset.on = String(label.dataset.mode === v.pick); label.dataset.current = String(label.dataset.mode === v.current); });
 $('mode-now').textContent = (v.current === 'auto' ? `Auto: following the system power profile (${v.profile ?? 'unknown'}), so the scheduler runs ${MODES[a.mode]?.label ?? a.mode} policy.` : `${MODES[v.current].label} is fixed${v.profile ? `; the system power profile is ${v.profile}` : ''}.`) + (v.cooling ? ` A 1-hour cooling request is active; ${MODES[cool.previous_mode]?.label ?? 'your earlier mode'} returns when it ends.` : '');
 const preview = $('mode-preview'), bits = [];
 let tone = '';
 if (v.same) bits.push(`${MODES[v.pick].label} is already active${v.wants ? ' and the system power profile matches' : ''}. Choose another mode to change it.`);
 else {
  const capNow = modeCap(c, v.resolved), running = a.t.active_slots;
  bits.push(v.pick === 'auto' ? `Hands the choice to the system power profile (now ${v.profile ?? 'unknown'}), which means ${MODES[v.resolved].label} policy: up to ${fmt(capNow, 0)} runs, ${fmt(modeTemp(c, v.resolved))} °C target.` : `Runs the ${MODES[v.pick].label} policy: up to ${fmt(capNow, 0)} runs, ${fmt(modeTemp(c, v.pick))} °C target.`);
  if (v.pick !== 'auto') { bits.push(v.wants ? `Sets the system power profile to ${MODES[v.pick].profile} (now ${v.profile ?? 'unknown'}).` : `Leaves the system power profile at ${v.profile ?? 'unknown'}; only the scheduler policy changes.`); if (!v.wants) tone = 'warn'; }
  if (isNum(running) && isNum(capNow) && capNow < running) bits.push(`${running} runs are active: none are killed; admission waits until runs finish.`);
  if (a.manual !== null) bits.push(`The manual ceiling of ${a.manual} stays in force.`);
  if (v.cooling) bits.push(v.pick === 'cool' ? 'Makes cool permanent: the 1-hour request is ended, so nothing restores your earlier mode later.' : 'Ends the active 1-hour cooling request.');
  bits.push('Takes effect on the next scheduler cycle (about a minute).');
 }
 preview.textContent = bits.join(' '); preview.dataset.tone = tone;
 const button = $('mode-apply'); button.textContent = busy.has('control:mode') ? 'Applying…' : v.same ? 'Mode is active' : `Switch to ${MODES[v.pick].label}`; button.setAttribute('aria-busy', busy.has('control:mode'));
 button.disabled = locked || working || v.same;
 $('mode-reset').hidden = !modeDirty;
}

// ---------------------------------------------------------------- shared cohort helpers
// Engine strings from old freezes can be multi-line `factorio --version` dumps; show only version and build.
const engineLabel = engine => { if (typeof engine !== 'string') return 'engine unknown'; const m = engine.match(/(?:Factorio\s+|Version:\s*)(\d+\.\d+\.\d+)\D{1,12}(\d{4,6})/); return m ? `Factorio ${m[1]} build ${m[2]}` : engine.length > 48 ? engine.slice(0, 45) + '…' : engine; };
const recency = c => { const created = Date.parse(c.created || ''); if (Number.isFinite(created)) return created; const match = String(c.name).match(/20\d{6}/g); if (match) { const d = match.at(-1); return Date.parse(d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8)); } return isNum(c.updated) ? c.updated * 1000 : 0; };
const byRecency = (a, b) => recency(b) - recency(a) || b.name.localeCompare(a.name, undefined, {numeric: true});
const bucket = status => status === 'completed' ? 'completed' : status === 'running' ? 'running' : status === 'queued' ? 'queued' : /error|fail|interrupt/.test(status) ? 'problem' : 'other';
function tally(c) {
 const sums = {completed: 0, running: 0, queued: 0, problem: 0, other: 0};
 Object.entries(c.counts || {}).forEach(([status, n]) => sums[bucket(status)] += n);
 const planned = isNum(c.planned) ? c.planned : Object.values(sums).reduce((x, y) => x + y, 0);
 return {...sums, planned, unaccounted: isNum(c.unaccounted) ? c.unaccounted : null};
}
const isLive = c => { const s = tally(c); const alloc = (snapshot.target?.cohorts || []).find(x => x.name === c.name); return s.running > 0 || (alloc && (alloc.running > 0 || alloc.queued > 0)) || (s.queued > 0 && ['queued', 'launched', 'running'].includes(c.queue_status)); };
const progressBar = (c, s = tally(c)) => `<div class="progress" role="img" aria-label="${esc(c.name)}: ${s.completed} completed, ${s.running} running, ${s.queued} queued, ${s.problem} faulted, ${s.other} other of ${s.planned} planned">${['completed', 'running', 'queued', 'problem', 'other'].filter(k => s[k] > 0 && s.planned > 0).map(k => `<span class="p-${k}" style="flex:${s[k]}" title="${k} ${s[k]}"></span>`).join('')}<span style="flex:${Math.max(0, s.planned - s.completed - s.running - s.queued - s.problem - s.other)}"></span></div>`;
const countChips = (c, s = tally(c)) => [chip(`${s.completed}/${isNum(s.planned) ? s.planned : '?'} done`, s.completed && s.completed === s.planned ? 'ok' : 'muted'), s.running ? chip(`${s.running} running`, 'info') : '', s.queued ? chip(`${s.queued} queued`) : '', s.problem ? chip(`${s.problem} faulted`, 'bad') : '', s.other ? chip(`${s.other} not run`, 'warn', Object.entries(c.counts).filter(([k]) => bucket(k) === 'other').map(([k, v]) => `${k} ${v}`).join(', ')) : '', isNum(s.unaccounted) && s.unaccounted ? chip(`${s.unaccounted} unaccounted`, 'warn') : ''].join('');

// ---------------------------------------------------------------- overview: live cohorts
function renderLive() {
 if (!snapshot) return;
 const all = snapshot.cohorts.filter(isLive).sort(byRecency), live = all.slice(0, liveShown), alloc = snapshot.target?.cohorts || [];
 setHTML($('live-list'), live.length ? live.map(c => {
  const s = tally(c), a = alloc.find(x => x.name === c.name), servers = c.jobs.filter(j => j.status === 'running').length;
  return `<div class="cohort-card" data-live="${s.running > 0}"><div class="cohort-title"><strong>${esc(c.name)}</strong><div class="chips">${countChips(c, s)}${a ? chip(`scheduler: ${a.running} running · ${a.queued} queued`, 'muted', 'Scheduler allocation') : ''}</div></div>${progressBar(c, s)}<div class="meta">${esc(engineLabel(c.engine))} · ${servers} server${servers === 1 ? '' : 's'} · queue ${esc(c.queue_status)} · updated ${esc(when(c.updated))} <button type="button" class="link" data-open-cohort="${esc(c.name)}">Open in registry →</button></div></div>`;
 }).join('') + (all.length > live.length ? `<div class="pager">Showing ${live.length} of ${all.length} live cohorts<button type="button" class="secondary small" data-more-live>Show all</button></div>` : '') : `<div class="empty"><strong>No running or queued cohorts</strong>Servers appear here when the scheduler admits queued runs.</div>`);
}

// ---------------------------------------------------------------- jobs tab
function listenerChip(job) {
 const l = job.listener; if (!l) return chip('listener unknown', 'warn');
 const tone = /mismatch/.test(l.status) ? 'bad' : /verified|loopback/.test(l.status) ? 'ok' : 'warn';
 return chip(/loopback/.test(l.status) ? 'loopback UDP' : /verified/.test(l.status) ? 'UDP listener' : 'unverified', tone, l.status);
}
function viewing(cohort, job) {
 const key = `view:${cohort}/${job.id}`, data = `data-cohort="${esc(cohort)}" data-run="${esc(job.id)}"`, v = job.viewer;
 if (busy.has(key)) return resultLine(key);
 if (v?.active) return `<div><code>${esc(v.endpoint)}</code> <button type="button" class="copy secondary small" data-value="${esc(v.endpoint)}">Copy</button></div><div class="meta">Starship only · expires ${esc(when(v.until))}</div><div class="actions"><button type="button" class="view secondary small" ${data}>Renew 1h</button><button type="button" class="view secondary small" ${data} data-stop="true">Stop</button></div>${resultLine(key)}`;
 if (snapshot.viewer_available && job.listener?.bind === '127.0.0.1') return `<button type="button" class="view small" ${data}>Enable Starship viewing (1h)</button>${resultLine(key)}`;
 return `<span class="muted small">${snapshot.viewer_available ? esc(job.listener?.status || 'No verified loopback listener') : 'Viewing relay not configured on this dashboard'}</span>${resultLine(key)}`;
}
function renderServers() {
 const groups = snapshot.cohorts.filter(c => c.jobs.some(j => j.status === 'running')).sort(byRecency);
 setHTML($('running-list'), groups.length ? groups.map(c => {
  const jobs = c.jobs.filter(j => j.status === 'running'), key = `srv:${c.name}`, open = openState.get(key) ?? jobs.length <= 5;
  return `<details class="cohort" data-key="${esc(key)}" ${open ? 'open' : ''}><summary data-fk="sum:${esc(key)}"><span class="cohort-title"><strong>${esc(c.name)}</strong><span class="chips">${chip(jobs.length + ' running server' + (jobs.length === 1 ? '' : 's'), 'info')}${open ? '' : '<span class="meta">expand for viewing and sync actions</span>'}</span></span></summary><div class="cohort-body"><div class="scroll"><table class="stack-sm"><thead><tr><th>Run</th><th>Listener</th><th>Starship viewing</th><th>Client mods</th></tr></thead><tbody>${jobs.map(j => {
   const sk = `sync:${c.name}/${j.id}`;
   return `<tr><td data-label="Run"><span class="run-id">${esc(j.id)}</span><br><span class="arm">${esc(j.arm)} · ${esc(tail(j.commit, 9))}</span></td><td data-label="Listener">${listenerChip(j)}<br><span class="meta">UDP ${esc(j.game_port)} · PID ${esc(j.listener?.pid)} · ${esc(j.listener?.bind)}</span></td><td data-label="Starship viewing">${viewing(c.name, j)}</td><td data-label="Client mods">${busy.has(sk) ? '' : `<button type="button" class="sync-mods secondary small" data-cohort="${esc(c.name)}" data-run="${esc(j.id)}">Sync mods to Starship Factorio</button>`}${resultLine(sk)}</td></tr>`;
  }).join('')}</tbody></table></div></div></details>`;
 }).join('') : `<div class="empty"><strong>No running servers</strong>Nothing to view or sync. Running worlds appear here with their verified listener.</div>`);
}
const kv = value => value && typeof value === 'object' && !Array.isArray(value) ? (Object.entries(value).map(([k, v]) => `${esc(k)} ${typeof v === 'object' ? esc(JSON.stringify(v)) : esc(v)}`).join(' · ') || 'none') : value == null ? 'unknown' : esc(Array.isArray(value) ? value.join(', ') || 'none' : value);
const list = value => Array.isArray(value) ? (value.length ? value.map(x => chip(x, 'info')).join('') : chip('none', 'muted')) : chip('unknown', 'warn');
function noteHTML(n) { return `<div class="note"><time>${esc(when(n.time))}${n.tags ? ' · ' + esc(n.tags) : ''}</time>${esc(n.text)}</div>`; }
function reviewBlock(cohort, job) {
 const key = `${cohort}/${job.id}`, rk = `review:${key}`, state = notesState.get(key), draft = drafts.get(key) || {}, big = expanded.has(key);
 const seeded = (snapshot.notes || []).filter(n => n.cohort === cohort && n.run === job.id).reverse(), rows = state?.loaded ? state.rows : seeded;
 return `<details class="review" data-key="${esc(rk)}" ${openState.get(rk) ? 'open' : ''}><summary data-fk="sum:${esc(rk)}">Reviews${rows.length ? ` (${rows.length}${state?.more ? '+' : ''})` : ''}${draft.text ? ' · draft saved' : ''}</summary><div class="review-body">
  <form class="note-form" data-cohort="${esc(cohort)}" data-run="${esc(job.id)}"><label>Add an observation<textarea name="text" required maxlength="10000" class="${big ? 'tall' : ''}" data-fk="note:${esc(key)}" placeholder="What did you see in this run?">${esc(draft.text ?? '')}</textarea></label><label>Tags (optional)<input name="tags" maxlength="500" value="${esc(draft.tags ?? '')}" data-fk="tags:${esc(key)}"></label><div class="row"><span class="counter">${(draft.text || '').length}/10000 · Ctrl+Enter saves · draft survives refresh</span><span class="actions"><button type="button" class="secondary small" data-expand="${esc(key)}" aria-pressed="${big}">${big ? 'Collapse box' : 'Expand box'}</button><button type="submit" class="small" ${busy.has('note:' + key) ? 'disabled aria-busy="true"' : ''}>${busy.has('note:' + key) ? 'Saving…' : 'Save note'}</button></span></div>${resultLine('note:' + key)}</form>
  <div class="notes">${state?.loading && !rows.length ? '<p class="muted small"><span class="spinner"></span>Loading saved reviews…</p>' : rows.length ? rows.map(noteHTML).join('') : state?.loaded ? '<p class="muted small">No saved reviews for this run yet.</p>' : '<p class="muted small">Open to load saved reviews.</p>'}${state?.error ? `<p class="result-line" data-tone="bad" role="alert">${esc(state.error)} <button type="button" class="link" data-notes="${esc(key)}" data-more="0">Retry</button></p>` : ''}</div>${state?.loaded && state.more ? `<button type="button" class="secondary small" data-notes="${esc(key)}" data-more="1" ${state.loading ? 'disabled' : ''}>${state.loading ? 'Loading…' : 'Load older reviews'}</button>` : state?.loaded && rows.length ? '<p class="meta">End of saved reviews.</p>' : ''}</div></details>`;
}
function runRow(c, j) {
 const cause = j.termination_cause || j.runner_stop;
 return `<tr><td data-label="Run"><span class="run-id">${esc(j.id)}</span><br><span class="arm">${esc(j.arm)} · ${esc(tail(j.commit, 9))}</span></td><td data-label="State">${chip(j.status, bucket(j.status) === 'problem' ? 'bad' : j.status === 'completed' ? 'ok' : j.status === 'running' ? 'info' : 'muted')} ${cause ? `<span class="meta">${esc(cause)}</span>` : ''}<br><span class="meta">failure count ${esc(j.failures)}</span><details class="more"><summary>Outcome detail</summary><p class="meta">Typed outcomes: ${kv(j.typed_outcomes)}</p><p class="meta">Classifications: ${kv(j.outcome_classifications)}</p></details></td><td data-label="Milestones">${/^(queued|running|not_started|cancelled)/.test(j.status) && j.accepted_ever == null ? '<span class="meta">No milestone result yet (not finished)</span>' : `<div class="chips">Ever ${list(j.accepted_ever)}</div><div class="chips">Final ${list(j.accepted_final)}</div><span class="meta">C5 ${esc(j.c5_predicate_version)} · Laya C6 ${esc(j.laya_c6_version)} · eligible ${j.comparison_eligible == null ? 'unknown' : esc(j.comparison_eligible)}</span>`}</td><td data-label="Actions"><button type="button" class="inspect secondary small" data-cohort="${esc(c.name)}" data-run="${esc(j.id)}">Inspect timing &amp; mods</button>${reviewBlock(c.name, j)}</td></tr>`;
}
function mixedView(c) {
 const groups = c.mixed?.groups;
 if (!groups) return '';
 return `<details class="more"><summary>Mixed starts · strict evidence by origin, arm and generation</summary><p class="meta">Restart outcomes are conditional. Historical latches and proxy phases do not establish current strict passes. A/A checks wiring; full-origin acceptance remains required.</p><div class="scroll"><table><thead><tr><th>Origin / arm / generation</th><th>Planned / started / terminal</th><th>Strict reach</th><th>Held at end</th><th>Stops / faults</th><th>Progress latency / server seconds</th></tr></thead><tbody>${groups.map(g => `<tr><td>${esc(g.origin)} / ${esc(g.arm)}<br>${esc(g.generation)}</td><td>${g.planned} / ${g.started} / ${g.terminal}</td><td>${esc(JSON.stringify(g.strict_reach))}</td><td>${esc(JSON.stringify(g.held_at_end))}</td><td>${esc(JSON.stringify(g.classifications))}<br>${esc(JSON.stringify(g.faults))}</td><td>${esc(JSON.stringify(g.latency))}</td></tr>`).join('')}</tbody></table></div></details>`;
}
function renderRegistry() {
 const q = $('filter').value.trim().toLowerCase(), state = ui.registryState;
 const match = c => !q || c.name.toLowerCase().includes(q) || c.jobs.some(j => `${j.id} ${j.arm}`.toLowerCase().includes(q));
 const all = snapshot.cohorts.filter(c => match(c) && (state === 'all' || (state === 'active' ? isLive(c) : tally(c).problem > 0))).sort(byRecency), shown = all.slice(0, registryShown);
 $$('#registry-state button').forEach(b => b.setAttribute('aria-pressed', b.dataset.state === state));
 keepFocus(() => setHTML($('cohorts'), shown.map(c => {
  const key = `cohort:${c.name}`, open = openState.get(key) === true, s = tally(c);
  let body = '';
  if (open) {
   const filter = jobFilter.get(c.name) || 'all', visible = c.jobs.filter(j => (!q || c.name.toLowerCase().includes(q) || `${j.id} ${j.arm}`.toLowerCase().includes(q)) && (filter === 'all' || bucket(j.status) === filter)), limit = pageSize.get(c.name) || 20;
   const statuses = ['all', 'running', 'queued', 'completed', 'problem', 'other'].filter(k => k === 'all' || c.jobs.some(j => bucket(j.status) === k));
   body = `<div class="cohort-body"><p class="meta">Created ${esc(when(Date.parse(c.created) / 1000))} · updated ${esc(when(c.updated))} · queue ${esc(c.queue_status)} · ${esc(engineLabel(c.engine))} · report <code>${esc(c.source)}</code></p>${mixedView(c)}<div class="toolbar wrap"><label class="inline">Show <select data-jobfilter="${esc(c.name)}" data-fk="jf:${esc(c.name)}">${statuses.map(k => `<option value="${k}" ${k === filter ? 'selected' : ''}>${k === 'all' ? 'all runs' : k === 'problem' ? 'faulted' : k} (${k === 'all' ? c.jobs.length : c.jobs.filter(j => bucket(j.status) === k).length})</option>`).join('')}</select></label></div>${visible.length ? `<div class="scroll"><table class="stack-sm"><thead><tr><th>Run · frozen commit</th><th>State</th><th>Milestones</th><th>Inspect &amp; review</th></tr></thead><tbody>${visible.slice(0, limit).map(j => runRow(c, j)).join('')}</tbody></table></div>` : '<div class="empty">No runs match this filter.</div>'}${visible.length > limit ? `<div class="pager">Showing ${limit} of ${visible.length}<button type="button" class="secondary small" data-more-jobs="${esc(c.name)}">Show 20 more</button></div>` : ''}</div>`;
  }
  return `<details class="cohort" data-key="${esc(key)}" ${open ? 'open' : ''}><summary data-fk="sum:${esc(key)}"><span class="cohort-title"><strong>${esc(c.name)}</strong><span class="chips">${countChips(c, s)}</span></span>${progressBar(c, s)}</summary>${body}</details>`;
 }).join('') + (all.length > shown.length ? `<div class="pager">Showing ${shown.length} of ${all.length} cohorts<button type="button" class="secondary small" data-more-cohorts>Show 15 more</button></div>` : '') || `<div class="empty"><strong>No matching cohorts</strong>${q ? 'Clear the filter or' : 'Try'} another state.</div>`));
}
function renderJobs() { if (!snapshot) return; keepFocus(() => { renderServers(); renderRegistry(); }); }

// ---------------------------------------------------------------- notes
async function loadNotes(key, more = false) {
 const [cohort, ...rest] = key.split('/'), run = rest.join('/'), previous = notesState.get(key) || {rows: []}; if (previous.loading) return;
 const state = {...previous, loading: true, error: null}; notesState.set(key, state); renderJobs();
 try {
  const q = new URLSearchParams({cohort, run}); if (more && previous.cursor != null) q.set('cursor', previous.cursor);
  const data = await getJSON('/api/notes?' + q), page = [...data.rows].reverse();
  notesState.set(key, {rows: more ? [...previous.rows, ...page] : page, cursor: data.next_cursor, more: data.next_cursor !== null, loaded: true, loading: false, error: null});
 } catch (error) { notesState.set(key, {...previous, loading: false, error: error.message}); }
 renderJobs();
}

// ---------------------------------------------------------------- performance charts
const PANEL_ORDER = ['runs', 'ups', 'temp', 'total', 'mem'];
function panelDefs(c, now = 1) {
 // History rows before 2026-11-05 lack game_speeds and already hold measured UPS.
 const up = (r, key) => sx(r.metrics?.[key], liveSpeed(r.metrics));
 return {
  runs: {title: 'Concurrent runs & caps', digits: 0, zero: true, step: true, series: [
   {id: 'active', name: 'Running', cls: 's-active', get: r => r.active_slots}, {id: 'target', name: 'Admission target', cls: 's-target', get: r => r.target_slots},
   {id: 'cap', name: 'Automatic cap', cls: 's-cap', dash: true, get: r => r.effective_max}, {id: 'manual', name: 'Manual ceiling', cls: 's-manual', dash: true, get: r => r.manual_target}], guards: []},
  ups: {title: 'Slow-tail UPS per run (measured)', digits: 1, series: [{id: 'p95', name: 'p95 coverage', cls: 's-p95', get: r => up(r, 'ups_p95')}, {id: 'p98', name: 'p98 coverage', cls: 's-p98', get: r => up(r, 'ups_p98')}],
   guards: [{series: 'p95', cls: 's-p95', name: `p95 floor (current setting, at ${now}x)`, v: sx(c.min_ups_p95, now)}, {series: 'p98', cls: 's-p98', name: `p98 floor (current setting, at ${now}x)`, v: sx(c.min_ups_p98, now)}]},
  temp: {title: 'Temperature °C', digits: 1, series: [{id: 'temp', name: 'Temperature', cls: 's-temp', get: r => r.metrics?.temp_c}],
   guards: [{series: 'temp', cls: 's-temp', name: 'target (current setting)', v: c.temp_limit_c}, {series: 'temp', cls: 's-temp', name: 'hard limit (current setting)', v: c.temp_hard_c}]},
  total: {title: 'Aggregate UPS (all running worlds)', digits: 0, series: [{id: 'total', name: 'Total UPS', cls: 's-total', get: r => up(r, 'total_ups')}], guards: []},
  mem: {title: 'Available memory GB', digits: 1, series: [{id: 'mem', name: 'Free memory', cls: 's-mem', get: r => r.metrics?.available_gb}], guards: [{series: 'mem', cls: 's-mem', name: 'minimum (current setting)', v: c.memory_min_gb}]},
 };
}
const PANEL_NAMES = {runs: 'Runs & caps', ups: 'UPS', temp: 'Temperature', total: 'Aggregate UPS', mem: 'Memory'};
const niceStep = raw => { const p = 10 ** Math.floor(Math.log10(raw)), f = raw / p; return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p; };
function yAxis(lo, hi, count = 4) {
 if (!(hi > lo)) { lo -= 1; hi += 1; }
 const step = niceStep((hi - lo) / count), start = Math.floor(lo / step) * step, ticks = [];
 for (let v = start; v < hi + step * 0.999; v += step) ticks.push(+v.toFixed(6));
 return {lo: ticks[0], hi: ticks.at(-1), ticks, digits: step >= 1 ? 0 : step >= 0.1 ? 1 : 2};
}
function timeTicks(t0, t1, count = 6) {
 const range = t1 - t0, steps = [300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400], step = steps.find(s => s >= range / count) || 86400, offset = -new Date().getTimezoneOffset() * 60, out = [];
 for (let t = Math.ceil((t0 + offset) / step) * step - offset; t <= t1; t += step) out.push(t);
 return {step, ticks: out, label: t => { const d = new Date(t * 1000); return step >= 86400 || (d.getHours() === 0 && d.getMinutes() === 0) ? d.toLocaleDateString([], {weekday: 'short', day: 'numeric'}) : d.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit', hour12: false}); }};
}
const AUDIT_LABEL = {ceiling: 'Ceiling set', resume: 'Resumed automatic', configure: 'Safeguards changed', cool: 'Cooling started', cool_cancel: 'Cooling cancelled', cool_restore: 'Cooling restored', cool_apply_failed: 'Cooling failed', control_rejected: 'Control rejected', cool_restore_failed: 'Cooling restore failed', cool_rollback_pending: 'Cooling rollback pending'};
function auditText(row) {
 const d = row.details || {}, req = d.request || d;
 let text = AUDIT_LABEL[row.action] || row.action;
 if (row.action === 'ceiling' && req.slots !== undefined) text += `: ${req.slots} run${req.slots === 1 ? '' : 's'}${req.duration ? ' for ' + req.duration : ' (permanent)'}`;
 if (row.action === 'configure') text += `: p95 ${req.p95 ?? '?'} · p98 ${req.p98 ?? '?'} · ${req.temp ?? '?'} °C`;
 if (d.ok === false) text += ' · FAILED' + (d.error ? ': ' + String(d.error).slice(0, 120) : '');
 else if (d.error) text += ': ' + String(d.error).slice(0, 120);
 return text;
}
let chartModel = null;
function drawPerformance() {
 $$('#range button').forEach(b => b.setAttribute('aria-pressed', Number(b.dataset.hours) === ui.hours));
 const defs = snapshot ? panelDefs(snapshot.config, liveSpeed(snapshot.target?.metrics)) : {}, toggles = $('panel-toggles');
 setHTML(toggles, `<span class="muted small">Panels</span><div class="segmented">${PANEL_ORDER.map(id => `<button type="button" data-panel="${id}" aria-pressed="${ui.panels.includes(id)}">${PANEL_NAMES[id]}</button>`).join('')}</div>`);
 const host = $('chart'); host.setAttribute('aria-busy', hist.loading);
 if (ui.tab !== 'performance' || !snapshot) return;
 if (hist.error && !hist.data) { chartModel = null; setHTML(host, `<div class="callout" data-tone="bad" role="alert"><strong>History unavailable:</strong> ${esc(hist.error)} <button type="button" class="small" data-act="retry-history">Retry</button></div>`); $('attribution').innerHTML = ''; return; }
 if (!hist.data) { chartModel = null; setHTML(host, '<div class="skeleton" style="height:300px"></div>'); return; }
 const data = hist.data, rows = (data.rows || []).filter(r => isNum(r.updated)).sort((a, b) => a.updated - b.updated);
 if (!rows.length) { chartModel = null; setHTML(host, `<div class="empty"><strong>No scheduler samples in this range</strong>${data.catching_up ? 'Older history is still being indexed.' : 'The scheduler recorded nothing in this window, so nothing is drawn rather than guessed.'}</div>`); $('attribution').innerHTML = ''; return; }
 const W = Math.max(320, Math.floor(host.clientWidth || 800)), left = 52, right = 14, pw = W - left - right;
 const t0 = isNum(data.start) ? data.start : rows[0].updated, t1 = isNum(data.end) ? data.end : rows.at(-1).updated, x = t => left + (t - t0) / Math.max(1, t1 - t0) * pw;
 const hasFlag = rows.some(r => 'gap_before' in r), gapAt = i => i > 0 && (hasFlag ? r_gap(rows[i]) : rows[i].updated - rows[i - 1].updated > 600);
 const r_gap = r => r.gap_before === true;
 const gaps = [];
 if (rows[0].updated - t0 > 600) gaps.push([t0, rows[0].updated]);
 rows.forEach((r, i) => { if (gapAt(i)) gaps.push([rows[i - 1].updated, r.updated]); });
 if (t1 - rows.at(-1).updated > 600) gaps.push([rows.at(-1).updated, t1]);
 const hidden = new Set(ui.hiddenSeries), ticks = timeTicks(t0, t1, Math.max(3, Math.min(8, Math.floor(pw / 95)))), panels = [];
 const audits = (snapshot.audit || []).filter(r => r.action !== 'note' && AUDIT_LABEL[r.action] && r.time >= t0 && r.time <= t1).sort((a, b) => a.time - b.time);
 const mkLines = h => audits.map(r => `<line class="mkline" x1="${x(r.time)}" x2="${x(r.time)}" y1="0" y2="${h}"/>`).join('');
 const vGrid = h => ticks.ticks.map(t => `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="0" y2="${h}"/>`).join('');
 const sections = ui.panels.filter(id => defs[id]).sort((a, b) => PANEL_ORDER.indexOf(a) - PANEL_ORDER.indexOf(b)).map(id => {
  const def = defs[id], H = 138, top = 8, ph = H - top - 8, visible = def.series.filter(s => !hidden.has(s.id));
  const values = visible.flatMap(s => rows.map(r => s.get(r))).filter(isNum), guards = def.guards.filter(g => isNum(g.v) && !hidden.has(g.series));
  const legend = `<div class="legend">${def.series.map(s => `<button type="button" class="${s.cls}" data-series="${s.id}" aria-pressed="${!hidden.has(s.id)}"><i${s.dash ? ' class="dash"' : ''}></i>${esc(s.name)}</button>`).join('')}${def.guards.length ? '<span class="static">Dashed horizontals: current settings</span>' : ''}</div>`;
  if (!values.length) return `<div class="cp"><div class="cp-head"><h3>${esc(def.title)}</h3>${legend}</div><div class="empty" style="margin-left:${left}px">No measurements recorded in this range${visible.length ? '' : ' (all series hidden)'}.</div></div>`;
  let lo = Math.min(...values, ...guards.map(g => g.v), ...(def.zero ? [0] : [])), hi = Math.max(...values, ...guards.map(g => g.v));
  const pad = (hi - lo) * 0.08 || 1; const axis = yAxis(def.zero ? 0 : lo - pad, hi + pad), y = v => top + ph - (v - axis.lo) / (axis.hi - axis.lo) * ph;
  const lines = visible.map(s => {
   const segs = []; let cur = null;
   rows.forEach((r, i) => { const v = s.get(r); if (!isNum(v)) { cur = null; return; } if (cur === null || gapAt(i)) { cur = []; segs.push(cur); } cur.push([r.updated, v]); });
   return segs.map(seg => seg.length === 1 ? `<circle class="dot ${s.cls}" cx="${x(seg[0][0])}" cy="${y(seg[0][1])}" r="3.5"/>` : `<path class="ln ${s.cls}${s.dash ? ' dash' : ''}" d="${seg.map(([t, v], k) => k === 0 ? `M${x(t).toFixed(1)},${y(v).toFixed(1)}` : def.step ? `H${x(t).toFixed(1)}V${y(v).toFixed(1)}` : `L${x(t).toFixed(1)},${y(v).toFixed(1)}`).join('')}"/>`).join('');
  }).join('');
  const guardLines = guards.map(g => `<line class="guard ${g.cls}" x1="${left}" x2="${W - right}" y1="${y(g.v)}" y2="${y(g.v)}"><title>${esc(g.name)} ${esc(g.v)}</title></line>`).join('');
  panels.push({id, def, y, visible});
  return `<div class="cp"><div class="cp-head"><h3>${esc(def.title)}</h3>${legend}</div><svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><defs><pattern id="hatch-${id}" width="6" height="6" patternTransform="rotate(45)" patternUnits="userSpaceOnUse"><rect width="2" height="6" fill="currentColor" opacity=".18"/></pattern></defs><g style="color:var(--faint)">${gaps.map(([a, b]) => `<rect x="${x(a)}" y="0" width="${Math.max(1, x(b) - x(a))}" height="${H}" fill="url(#hatch-${id})"/>`).join('')}</g>${axis.ticks.map(v => `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/><text x="${left - 6}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v, axis.digits))}</text>`).join('')}${vGrid(H)}${mkLines(H)}${guardLines}${lines}<line class="cursor" y1="0" y2="${H}" x1="-9" x2="-9"/>${visible.map(s => `<circle class="cursor-dot dot ${s.cls}" data-s="${s.id}" r="4.5" cx="-9" cy="-9"/>`).join('')}</svg></div>`;
 });
 // Recorded reason / mode lanes: only where samples exist; gaps stay blank.
 const lane = (valueOf, h, classOf) => { let out = ''; rows.forEach((r, i) => { const next = rows[i + 1], end = next && !gapAt(i + 1) ? next.updated : r.updated + 60; out += `<rect class="${classOf(r)}" x="${x(r.updated).toFixed(1)}" y="0" width="${Math.max(1.5, x(Math.min(end, t1)) - x(r.updated)).toFixed(1)}" height="${h}"/>`; }); return out; };
  const markerSvg = audits.length ? `<div class="band-label">Control events (hover): ◆ limit / safeguard change · <span style="color:var(--c-total)">◆</span> cooling · <span style="color:var(--red)">◆</span> failed or rejected</div><svg width="${W}" height="18" viewBox="0 0 ${W} 18" aria-hidden="true">${audits.map(r => { const bad = r.details?.ok === false || /fail|reject/.test(r.action), cx = x(r.time); return `<path class="mk ${bad ? 'bad' : /cool/.test(r.action) ? 'cool' : ''}" d="M${cx},2 L${cx + 6},9 L${cx},16 L${cx - 6},9 Z"/>`; }).join('')}</svg>` : '';
 const bands = `${markerSvg}<div class="band-label">Scheduler-recorded reason</div><svg width="${W}" height="16" viewBox="0 0 ${W} 16" aria-hidden="true">${lane(r => r.reason, 16, r => 'g-' + groupOf(r.reason))}</svg><div class="band-label">Mode</div><svg width="${W}" height="10" viewBox="0 0 ${W} 10" aria-hidden="true">${lane(r => r.mode, 10, r => r.mode === 'cool' ? 'g-thermal' : r.mode === 'balanced' ? 'g-search' : 'g-other')}</svg>`;
 const axisSvg = `<svg width="${W}" height="22" viewBox="0 0 ${W} 22" aria-hidden="true">${ticks.ticks.map(t => `<line class="axis" x1="${x(t)}" x2="${x(t)}" y1="0" y2="5"/><text x="${Math.min(W - 20, Math.max(left - 10, x(t)))}" y="17" text-anchor="middle">${esc(ticks.label(t))}</text>`).join('')}</svg>`;
 chartModel = {rows, panels, x, left, right, W, pw, t0, t1, gaps, audits, gapAt, idx: -1};
 setHTML(host, `<div class="chart-stack" tabindex="0" role="group" aria-label="Host history charts, ${ui.hours === 168 ? '7 days' : ui.hours === 24 ? '1 day' : '6 hours'}. Hover, touch, or use the left and right arrow keys to inspect samples.">${sections.join('') || '<div class="empty">Choose a panel above.</div>'}${bands}${axisSvg}<div class="tip" hidden></div></div><div class="visually-hidden" id="chart-live" aria-live="polite"></div>${hist.loading ? '<p class="hint"><span class="spinner"></span>Loading this range…</p>' : ''}${hist.error ? `<p class="result-line" data-tone="bad" role="alert">Could not refresh this range: ${esc(hist.error)}</p>` : ''}`);
 host.style.opacity = hist.loading ? .6 : 1;
 renderAttribution(data, rows, t0, t1, gaps); renderSummary(rows, defs, gaps, data);
}
function renderAttribution(data, rows, t0, t1, gaps) {
 const seconds = {...(data.reason_seconds || {})};
 if (!data.reason_seconds) rows.forEach((r, i) => { const next = rows[i + 1]; if (next && next.updated - r.updated <= 600) seconds[r.reason || 'unknown'] = (seconds[r.reason || 'unknown'] || 0) + next.updated - r.updated; });
 const grouped = {}; Object.entries(seconds).forEach(([reason, sec]) => { const g = groupOf(reason); (grouped[g] ||= {sec: 0, reasons: []}); grouped[g].sec += sec; grouped[g].reasons.push([reason, sec]); });
 const window = Math.max(1, t1 - t0), attributed = Object.values(seconds).reduce((a, b) => a + b, 0), rest = Math.max(0, window - attributed);
 const order = Object.keys(grouped).sort((a, b) => grouped[b].sec - grouped[a].sec);
 setHTML($('attribution'), `<h3>Recorded scheduler reasons over ${ui.hours === 168 ? '7 days' : ui.hours === 24 ? '1 day' : '6 hours'}</h3><div class="stack" role="img" aria-label="Share of the window by recorded reason group">${order.map(g => `<span class="g-${g}" style="flex:${grouped[g].sec}" title="${esc(GROUPS[g])}: ${esc(span(grouped[g].sec))}"></span>`).join('')}${rest > 0 ? `<span class="gapseg" style="flex:${rest}" title="No sample or gap: ${esc(span(rest))}"></span>` : ''}</div><div class="reasons">${order.map(g => `<span><i class="g-${g}"></i><b>${esc(GROUPS[g])}</b> ${esc(span(grouped[g].sec))} · ${pct(grouped[g].sec, window)}%</span>`).join('')}${rest > 0 ? `<span><i style="background:repeating-linear-gradient(45deg,var(--border),var(--border) 2px,transparent 2px,transparent 4px)"></i><b>No sample / gap</b> ${esc(span(rest))} · ${pct(rest, window)}%</span>` : ''}</div><details><summary>Individual reasons</summary><div class="reasons">${order.flatMap(g => grouped[g].reasons.sort((a, b) => b[1] - a[1]).map(([reason, sec]) => `<span><i class="g-${g}"></i>${esc(reasonLabel(reason))} <b>${esc(span(sec))}</b></span>`)).join('')}</div></details><p class="hint">Durations come from recorded reasons only; intervals over ten minutes are omitted. Reasons describe what the scheduler reported, not independent causes. ${data.catching_up ? 'Older history is still being indexed.' : ''}</p>`);
}
function renderSummary(rows, defs, gaps, data) {
 const lines = ui.panels.filter(id => defs[id]).flatMap(id => defs[id].series.map(s => {
  const pts = rows.map(r => [r.updated, s.get(r)]).filter(p => isNum(p[1])), values = pts.map(p => p[1]), d = defs[id].digits;
  return `<tr><td>${esc(PANEL_NAMES[id])} · ${esc(s.name)}</td><td class="num">${values.length ? esc(fmt(pts.at(-1)[1], d)) + ` <span class="meta">(${esc(clock(pts.at(-1)[0]))})</span>` : 'not recorded'}</td><td class="num">${values.length ? esc(fmt(Math.min(...values), d)) : 'unknown'}</td><td class="num">${values.length ? esc(fmt(Math.max(...values), d)) : 'unknown'}</td><td class="num">${values.length} of ${rows.length}</td></tr>`;
 }));
 setHTML($('chart-summary'), `<table><thead><tr><th>Series</th><th>Latest</th><th>Min</th><th>Max</th><th>Samples with a value</th></tr></thead><tbody>${lines.join('')}</tbody></table><p class="meta">${esc(data.raw_samples ?? rows.length)} source samples · ${rows.length} drawn · ${gaps.length} gap${gaps.length === 1 ? '' : 's'} (shaded, never connected) · ${esc(data.display || 'measured history')}.</p>`);
}
// Chart pointer / keyboard inspection (event delegation on the host).
function inspect(index, px, py) {
 const m = chartModel, stack = $('chart').querySelector('.chart-stack'); if (!m || !stack) return;
 const row = m.rows[index], tip = stack.querySelector('.tip'), x = m.x(row.updated);
 stack.classList.add('hover'); $$('.cursor', stack).forEach(line => { line.setAttribute('x1', x); line.setAttribute('x2', x); });
 const lines = m.panels.map(p => ({p, items: p.visible.map(s => ({s, v: s.get(row)}))}));
 lines.forEach(({p, items}) => items.forEach(({s, v}) => { const dot = stack.querySelector(`.cursor-dot[data-s="${s.id}"]`); if (!dot) return; dot.style.display = isNum(v) ? '' : 'none'; if (isNum(v)) { dot.setAttribute('cx', x); dot.setAttribute('cy', p.y(v)); } }));
 tip.innerHTML = `<h4>${esc(new Date(row.updated * 1000).toLocaleString())}</h4><div class="mut">${esc(reasonLabel(row.reason))} · mode ${esc(row.mode)}</div><table>${lines.flatMap(({p, items}) => items.map(({s, v}) => `<tr><td><i class="${s.cls}"></i>${esc(s.name)}</td><td>${isNum(v) ? esc(fmt(v, p.def.digits)) : '<span class="mut">not recorded</span>'}</td></tr>`)).join('')}${isNum(row.metrics?.ups_samples) ? `<tr><td class="mut">runs sampled</td><td>${esc(row.metrics.ups_samples)}</td></tr>` : ''}</table>`;
 place(tip, stack, x, py); m.idx = index;
}
function place(tip, stack, x, py) {
 tip.hidden = false; const w = tip.offsetWidth, h = tip.offsetHeight, sw = stack.clientWidth, sh = stack.clientHeight;
 tip.style.left = Math.max(0, Math.min(sw - w, x + 16 + w > sw ? x - w - 16 : x + 16)) + 'px'; tip.style.top = Math.max(0, Math.min(sh - h, (py ?? 30) - h / 2)) + 'px';
}
function leaveChart() { const stack = $('chart').querySelector('.chart-stack'); if (!stack) return; stack.querySelector('.tip').hidden = true; stack.classList.remove('hover'); if (chartModel) chartModel.idx = -1; }
function onChartMove(event) {
 const m = chartModel, stack = event.target.closest?.('.chart-stack'); if (!m || !stack) return;
 const rect = stack.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
 if (px < m.left - 4 || px > m.W - m.right + 4 || event.target.closest('.cp-head')) return leaveChart();
 const t = m.t0 + (px - m.left) / m.pw * (m.t1 - m.t0), tip = stack.querySelector('.tip');
 const mark = m.audits.find(r => Math.abs(m.x(r.time) - px) <= 7);
 if (mark) { tip.innerHTML = `<h4>${esc(new Date(mark.time * 1000).toLocaleString())}</h4><div>${esc(auditText(mark))}</div><div class="mut">Control event; effect appears only in later samples.</div>`; place(tip, stack, px, py); return; }
 let lo = 0, hi = m.rows.length - 1; while (lo < hi) { const mid = (lo + hi) >> 1; if (m.rows[mid].updated < t) lo = mid + 1; else hi = mid; }
 const i = lo > 0 && Math.abs(m.rows[lo - 1].updated - t) < Math.abs(m.rows[lo].updated - t) ? lo - 1 : lo;
 if (Math.abs(m.rows[i].updated - t) > (m.t1 - m.t0) / 40 + 600 || m.gaps.some(([a, b]) => t > a && t < b)) {
  stack.classList.add('hover'); $$('.cursor', stack).forEach(l => { l.setAttribute('x1', px); l.setAttribute('x2', px); }); $$('.cursor-dot', stack).forEach(d => d.style.display = 'none');
  tip.innerHTML = `<h4>${esc(new Date(t * 1000).toLocaleString())}</h4><div>No sample recorded here. Values are unknown, not zero.</div>`; place(tip, stack, px, py); return;
 }
 inspect(i, px, py);
}
function onChartKey(event) {
 const m = chartModel; if (!m || !event.target.matches?.('.chart-stack')) return;
 if (event.key === 'Escape') return leaveChart();
 const step = { ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity }[event.key]; if (step === undefined) return;
 event.preventDefault(); const next = Math.max(0, Math.min(m.rows.length - 1, m.idx < 0 ? m.rows.length - 1 : isFinite(step) ? m.idx + step : step < 0 ? 0 : m.rows.length - 1));
 inspect(next, null, 40); const row = m.rows[next];
 $('chart-live').textContent = `${clock(row.updated)}: ${m.panels.flatMap(p => p.visible.map(s => `${s.name} ${isNum(s.get(row)) ? fmt(s.get(row), p.def.digits) : 'not recorded'}`)).join(', ')}`;
}

// ---------------------------------------------------------------- curated history
function renderHistory() {
 const data = snapshot?.curated, box = $('milestones');
 if (!snapshot) return;
 $$('#history-view button').forEach(b => b.setAttribute('aria-pressed', b.dataset.hview === ui.historyView));
 $$('#family button').forEach(b => b.setAttribute('aria-pressed', b.dataset.family === ui.family));
 $('trend-view').hidden = ui.historyView !== 'trend'; $('results-view').hidden = ui.historyView !== 'results';
 if (!Array.isArray(data?.cohorts)) { const none = '<div class="empty"><strong>Curated history unavailable</strong>The reviewed feed <code>docs/rl/laya_cohort_curated_history.json</code> could not be read. The registry on the Jobs tab is not a substitute for it.</div>'; setHTML(box, none); setHTML($('trend-chart'), none); setHTML($('trend-summary'), ''); return; }
 $('history-sub').textContent = `${data.coverage?.from ?? '?'} → ${data.coverage?.to ?? '?'} · ${data.cohorts.length} reviewed rows · ${data.cohorts.reduce((n, c) => n + (c.arms || []).length, 0)} arms · independent cohorts, not matched replications`;
 if (ui.historyView === 'trend') return renderTrend(data);
 const family = ui.family, UNRANKED = new Set(['pending', 'no_start', 'invalid']);
 const keys = [...new Set(data.cohorts.flatMap(c => (c.arms || []).filter(a => a.predicate_family === family).flatMap(a => Object.keys(a.ever || {}))))].sort();
 if (!keys.includes(ui.milestone)) ui.milestone = keys.at(-1) || ui.milestone;
 const milestone = ui.milestone, q = $('history-filter').value.trim().toLowerCase();
 setHTML($('milestone-seg'), keys.map(k => `<button type="button" data-milestone="${k}" aria-pressed="${k === milestone}">${k}</button>`).join('') || '<span class="muted small">no milestones</span>');
 $$('#family button').forEach(b => b.setAttribute('aria-pressed', b.dataset.family === family));
 $('unranked').checked = ui.unranked;
 const sorted = [...data.cohorts].sort((a, b) => (b.date || '').localeCompare(a.date || '') || String(b.id).localeCompare(String(a.id), undefined, {numeric: true}));
 const matches = sorted.filter(c => !q || `${c.id} ${(c.arms || []).map(a => a.id).join(' ')}`.toLowerCase().includes(q));
 const prepared = matches.map(c => {
  const unranked = UNRANKED.has(c.status), arms = (c.arms || []).filter(a => a.predicate_family === family);
  const ranked = arms.filter(a => isNum(a.ever?.[milestone]) && a.planned > 0);
  return {c, unranked, arms, ranked};
 });
 const visible = prepared.filter(p => (!p.unranked && p.ranked.length) || (ui.unranked && (p.unranked || p.arms.length)));
 const hiddenCount = prepared.length - visible.length;
 let lastDate = '';
 const cards = visible.slice(0, historyShown).map(({c, unranked, arms, ranked}) => {
  const shownArms = ui.unranked ? arms : ranked;
  const field = {version: a => a.predicate_versions?.[milestone] ?? null, window: a => isNum(a.window_max_game_ticks) ? a.window_max_game_ticks : null, eligible: a => a.comparison_eligible ?? null};
  const uniform = {}; ['version', 'window', 'eligible'].forEach(k => { uniform[k] = new Set(shownArms.map(a => String(field[k](a)))).size <= 1; });
  const mixed = !uniform.version || !uniform.window;
  const shared = shownArms.length ? [uniform.version ? versionChip(field.version(shownArms[0])) : '', uniform.window ? windowChip(field.window(shownArms[0])) : '', uniform.eligible ? eligibleChip(field.eligible(shownArms[0])) : ''].join('') : '';
  const armRows = unranked ? `<p class="muted small">Unranked (${esc(c.status.replace(/_/g, ' '))}): no result is implied, and a missing count is unknown, not 0%.</p>` : shownArms.length ? shownArms.map(a => armRow(a, milestone, uniform, field)).join('') : `<p class="muted small">No ${esc(family)} arms with a reported ${esc(milestone)} count.</p>`;
  const links = (c.sources || []).map(path => `<a href="/api/report?${new URLSearchParams({path})}" target="_blank" rel="noopener">${esc(path.split('/').at(-1))}</a>`).join(' · ');
  const head = c.date !== lastDate ? `<div class="period">${esc(c.date)}</div>` : ''; lastDate = c.date;
  return `${head}<article class="hcohort"><div class="hcohort-head"><h3>${esc(c.id)}</h3>${chip(String(c.status).replace(/_/g, ' '), unranked ? 'warn' : /^completed$/.test(c.status) ? 'ok' : 'muted')}${mixed ? chip('arms differ in window or version', 'warn', 'Compare arms only where their chips match') : ''}</div><div class="chips">${shared}</div><details class="finding"><summary class="finding-sum">${esc(String(c.finding ?? '').split(/(?<=[.;])\s/)[0])}</summary><p class="finding">${esc(c.finding)}</p>${links ? `<p class="meta">Sources: ${links}</p>` : ''}</details>${armRows}</article>`;
 }).join('');
 setHTML(box, `<div class="legend-key"><span><i style="background:var(--blue);opacity:.55"></i>ever reached (of planned)</span><span><i style="background:var(--accent)"></i>final (last sampled)</span><span><i style="background:repeating-linear-gradient(45deg,var(--border),var(--border) 4px,transparent 4px,transparent 8px)"></i>not reported (unknown)</span></div>${hiddenCount && !ui.unranked ? `<p class="hint">${hiddenCount} row${hiddenCount === 1 ? '' : 's'} hidden: pending, invalid, no-start, or no ${esc(milestone)} count in the ${esc(family)} family. Tick “Show unranked rows” to list them.</p>` : ''}${cards ? `<div class="hgrid">${cards}</div>` : '<div class="empty"><strong>No qualified numeric results</strong>for this family, milestone and search.</div>'}${visible.length > historyShown ? `<div class="pager">Showing ${historyShown} of ${visible.length} rows<button type="button" class="secondary small" data-more-history>Show 14 more</button></div>` : ''}`);
}
const versionChip = v => chip(v ?? 'version unknown', v ? 'info' : 'warn', 'Predicate version for the selected milestone');
const windowChip = w => chip(isNum(w) ? `${Math.round(w / 1000)}k tick window` : 'window unknown', isNum(w) ? 'muted' : 'warn');
const eligibleChip = e => chip(e === true ? 'comparison eligible' : e === false ? 'not eligible' : 'eligibility unknown', e === true ? 'ok' : 'muted');
function armRow(a, milestone, uniform, field) {
 const ever = a.ever?.[milestone], fin = a.final?.[milestone], caveats = a.caveats || [], latched = caveats.some(x => /latch/i.test(x));
 const known = isNum(ever) && a.planned > 0, w = known ? Math.min(100, ever / a.planned * 100) : 0, fw = isNum(fin) && a.planned > 0 ? Math.min(100, fin / a.planned * 100) : null;
 const label = known ? `ever ${ever}/${a.planned} (${pct(ever, a.planned)}%) · final ${isNum(fin) ? fin : latched ? 'latched, unknown' : 'not reported'}` : `${milestone} not reported for ${a.planned ?? '?'} planned`;
 const own = [uniform.version ? '' : versionChip(field.version(a)), uniform.window ? '' : windowChip(field.window(a)), uniform.eligible ? '' : eligibleChip(field.eligible(a)), latched ? chip('final latched', 'warn') : ''].join('');
 return `<div class="hrow"><div class="lab"><strong>${esc(a.id)}</strong> <span class="meta">${esc(tail(a.commit, 9) || 'commit unknown')}</span>${own ? `<div class="chips">${own}</div>` : ''}</div><div class="bar${known ? '' : ' unk'}" role="img" aria-label="${esc(label)}">${known ? `<i style="width:${w}%"></i>${fw !== null ? `<b style="width:${fw}%"></b>` : ''}` : ''}</div><div class="cv">${known ? `ever <b>${ever}</b>/${a.planned} · ${pct(ever, a.planned)}%<br>final ${isNum(fin) ? `<b>${fin}</b>` : '<span class="meta">' + (latched ? 'latched, unknown' : 'not reported') + '</span>'}` : '<span class="meta">not reported</span>'}${caveats.length ? `<details class="more"><summary>${caveats.length} caveat${caveats.length === 1 ? '' : 's'}</summary><ul>${caveats.map(x => `<li>${esc(x)}</li>`).join('')}</ul></details>` : ''}</div></div>`;
}

// ---------------------------------------------------------------- trend across curated cohorts
// Base arms are recognised by name only (the feed has no role field); a cohort without one simply has no base point.
const BASE_ARM = /^(control|reference|ref|base|baseline|old-baseline)(_[a-z0-9]+)?$/i, UNRANKED_STATUS = new Set(['pending', 'no_start', 'invalid']);
const TREND_MS = ['C1', 'C2', 'C3', 'C4', 'C5', 'C6'];
const METRICS = {ever: ['Reached', 'of planned runs that ever reached the milestone'], final: ['Final sample', 'of planned runs accepted at the last sample (not sustained operation)'], retention: ['Retention', 'of runs that ever reached it still accepted at the end']};
const wilson = (k, n) => { const z = 1.96, p = k / n, d = 1 + z * z / n, c = p + z * z / (2 * n), m = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)); return [Math.max(0, (c - m) / d), Math.min(1, (c + m) / d)]; };
const median = values => { const v = values.filter(isNum).sort((a, b) => a - b); return v.length ? (v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2) : null; };
const shortId = id => String(id).replace(/-?2026\d{4}-?/, '-').replace(/^-|-$/g, '').replace(/^(.{9}).+$/, '$1…');
const ptsPct = v => isNum(v) ? `${Math.round(v * 100)}%` : 'unknown';
function trendModel(data) {
 const fam = ui.family, metric = ui.trendMetric, wf = ui.trendWindow;
 const rate = (a, m) => {
  const planned = a.planned, ever = a.ever?.[m], fin = a.final?.[m], latched = (a.caveats || []).some(x => /latch/i.test(x));
  if (!Number.isInteger(planned) || planned <= 0) return null;
  const r = metric === 'ever' ? (isNum(ever) ? {k: ever, n: planned} : null) : metric === 'final' ? (isNum(fin) && !latched ? {k: fin, n: planned} : null) : (isNum(ever) && ever > 0 && isNum(fin) && !latched ? {k: fin, n: ever} : null);
  if (!r || !Number.isInteger(r.k) || !Number.isInteger(r.n) || r.k < 0 || r.n <= 0 || r.k > r.n) return null;
  const [lo, hi] = wilson(r.k, r.n); return {...r, p: r.k / r.n, lo, hi};
 };
 const windowOf = arms => { const set = new Set(arms.map(a => a.window_max_game_ticks ?? null)); return set.size === 1 ? [...set][0] : arms.length ? 'mixed' : null; };
 const windows = new Map();
 const sorted = data.cohorts.filter(c => !UNRANKED_STATUS.has(c.status)).sort((a, b) => (a.date || '').localeCompare(b.date || '') || String(a.id).localeCompare(String(b.id), undefined, {numeric: true}));
 const rows = sorted.map(c => {
  const arms = (c.arms || []).filter(a => a.predicate_family === fam);
  arms.forEach(a => { const w = a.window_max_game_ticks ?? 'unknown'; windows.set(w, (windows.get(w) || 0) + 1); });
  const use = arms.filter(a => wf === 'all' || String(a.window_max_game_ticks ?? 'unknown') === wf), base = use.filter(a => BASE_ARM.test(a.id)), exp = use.filter(a => !BASE_ARM.test(a.id)), row = {c, base: {}, exp: {}, baseNames: base.map(a => a.id), baseWin: windowOf(base), expWin: windowOf(exp), selectedArms: base.length + exp.length, any: false};
  const aggregate = (group, m, bestOnly = false) => {
   const points = group.map(a => ({arm: a.id, source: a, r: rate(a, m)})).filter(a => a.r);
   if (!points.length) return null;
   const versions = new Set(points.map(a => a.source.predicate_versions?.[m] ?? null)), windows = new Set(points.map(a => a.source.window_max_game_ticks ?? null));
   // Different explicit definitions or budgets cannot form one milestone rate.
   if (versions.size !== 1 || windows.size !== 1) return null;
   const version = [...versions][0], window = [...windows][0], metadata = version && isNum(window) ? JSON.stringify([fam, version, window]) : null;
   const best = points.reduce((a, b) => b.r.p > a.r.p ? b : a), selected = bestOnly ? best : points[0];
   const single = bestOnly || points.length === 1;
   return {p: single ? selected.r.p : points.reduce((sum, a) => sum + a.r.p, 0) / points.length,
    ...(single ? selected.r : {}), arms: points, label: single ? selected.arm : 'mean of ' + points.length, metadata, version, window};
  };
  TREND_MS.forEach(m => {
   row.base[m] = aggregate(base, m);
   row.exp[m] = aggregate(exp, m, ui.trendExp === 'best');
   if (row.base[m] || row.exp[m]) row.any = true;
  });
  return row;
 }).filter(r => r.selectedArms > 0);
 return {rows, windows, metric};
}
function trendSummary(model, series) {
 return ui.trendMilestones.filter(m => TREND_MS.includes(m)).map(m => {
  const groups = new Map();
  for (const row of model.rows) {
   const value = row[series][m]; if (!value?.metadata) continue;
   if (!groups.has(value.metadata)) groups.set(value.metadata, []);
   groups.get(value.metadata).push({id: row.c.id, ...value});
  }
  const list = [...groups.values()].sort((a, b) => b.length - a.length)[0] || [], example = list[0];
  const qualification = example ? `${example.version} · ${Math.round(example.window / 1000)}k ticks` : 'known window and predicate definition required';
  if (list.length < 10) return {m, n: list.length, qualification};
  const prior = list.slice(-10, -5), recent = list.slice(-5), a = median(prior.map(x => x.p)), b = median(recent.map(x => x.p));
  return {m, n: list.length, qualification, a, b, diff: b - a, prior: prior.map(x => x.id), recent: recent.map(x => x.id)};
 });
}
function trendSummaryHTML(model) {
 const cell = r => !isNum(r.a) ? `<span class="muted small">${r.n} points with matching known metadata; 10 needed</span><br><span class="meta">${esc(r.qualification)}</span>` : `${ptsPct(r.a)} → <b>${ptsPct(r.b)}</b> <span class="meta">${r.diff >= 0 ? '+' : '−'}${Math.round(Math.abs(r.diff) * 100)} pp</span><br><span class="meta">${esc(r.qualification)}</span><details class="more"><summary>Cohorts used</summary><p class="meta">Previous: ${esc(r.prior.join(', '))}<br>Latest: ${esc(r.recent.join(', '))}</p></details>`;
 const base = trendSummary(model, 'base'), exp = trendSummary(model, 'exp');
 const head = ui.trendMilestones.length ? `<div class="scroll"><table class="trend-table"><caption class="visually-hidden">Descriptive medians by recorded definition and tick window</caption><thead><tr><th>Milestone</th><th>Base arms · previous 5 → latest 5 (median)</th><th>${ui.trendExp === 'best' ? 'Best experimental arm' : 'Mean experimental arm'} · previous 5 → latest 5</th></tr></thead><tbody>${base.map((r, i) => `<tr><td><i class="swatch m${r.m.slice(1)}"></i><b>${esc(r.m)}</b></td><td>${cell(r)}</td><td>${cell(exp[i])}</td></tr>`).join('')}</tbody></table></div>` : '';
 return `<h3 class="trend-h">Recent descriptive rates <span class="muted small">${esc(METRICS[model.metric][0])}</span></h3>${head}<p class="hint">Each cell uses the most populated group with the same known predicate definition and tick window, and requires ten cohort points. It reports medians, not a test of improvement or regression. Policies, seeds, hosts and engine versions can still differ; repeated seeds are not independent replications. Base roles are inferred from names. Mean arms are unweighted summaries, and a best arm is selected after the fact. Use individual cohort reports for matched comparisons.</p>`;
}
let trendModelCache = null;
function renderTrend(data) {
 const model = trendModel(data); trendModelCache = model;
 $$('#trend-metric button').forEach(b => b.setAttribute('aria-pressed', b.dataset.metric === ui.trendMetric));
 $$('#trend-exp button').forEach(b => b.setAttribute('aria-pressed', b.dataset.texp === ui.trendExp));
 $('trend-ci').checked = ui.trendCI;
 const counts = [...model.windows.entries()].sort((a, b) => b[1] - a[1]);
 setHTML($('trend-window'), `<option value="all">All windows (metadata changes are dotted)</option>${counts.map(([w, n]) => `<option value="${esc(w)}">${isNum(w) ? Math.round(w / 1000) + 'k ticks' : 'window unknown'} · ${n} arms</option>`).join('')}`);
 $('trend-window').value = ui.trendWindow;
 const has = m => model.rows.some(r => r.base[m] || r.exp[m]);
 setHTML($('trend-legend'), `<span class="static">Milestones</span>${TREND_MS.map(m => `<button type="button" class="m${m.slice(1)}" data-tm="${m}" aria-pressed="${ui.trendMilestones.includes(m)}" ${has(m) ? '' : 'disabled title="No data for this selection"'}><i></i>${m}</button>`).join('')}`);
 setHTML($('trend-summary'), model.rows.length >= 2 ? trendSummaryHTML(model) : '');
 $('trend-hint').innerHTML = `${esc(METRICS[ui.trendMetric][0])} = % ${esc(METRICS[ui.trendMetric][1])}. <strong>Solid</strong> segments share a known tick window and predicate definition; <strong>dotted</strong> segments have differing or unknown metadata. Neither establishes a matched comparison. Hollow markers have missing window or definition metadata. Mixed definitions/windows are not averaged into one point. Means use reported arms only, so composition can change. Base roles are inferred from names; missing roles and counts remain gaps. Individual arm binomial ranges assume independent runs; repeated seeds and early stopping can make them optimistic. ${ui.trendExp === 'best' ? 'Best arm is chosen after the fact and overstates a typical experiment. ' : ''}${ui.family === 'proxy' ? 'Proxy rates only cover one early campaign, so there is no trend. ' : ''}`;
 drawTrend(model);
}
function drawTrend(model = trendModelCache) {
 const host = $('trend-chart'); if (!model || ui.tab !== 'history' || ui.historyView !== 'trend') return;
 const rows = model.rows, shown = ui.trendMilestones.filter(m => TREND_MS.includes(m));
 if (rows.filter(r => r.any).length < 2) return setHTML(host, '<div class="empty"><strong>Not enough cohorts to draw a trend</strong>This selection has fewer than two cohorts with a numeric value. Choose another family, window or metric.</div>');
 if (!shown.length) return setHTML(host, '<div class="empty"><strong>No milestone selected</strong>Turn on at least one milestone above.</div>');
 const n = rows.length, left = 44, right = 12, step = Math.max(13, Math.min(34, ((host.clientWidth || 800) - left - right) / n)), W = Math.max(host.clientWidth || 800, left + right + n * 13), pw = W - left - right, px = Math.max(6, pw / n), x = i => left + (i + 0.5) * px;
 const winOrder = [...model.windows.entries()].filter(([w]) => isNum(w)).sort((a, b) => b[1] - a[1]).map(([w]) => w), winFill = w => isNum(w) ? ['var(--g-search)', 'var(--g-ups)', 'var(--g-thermal)', 'var(--g-memory)'][Math.min(3, winOrder.indexOf(w))] : 'var(--g-nodata)';
 const off = m => (shown.indexOf(m) - (shown.length - 1) / 2) * Math.min(3, px / 8), tickEvery = Math.max(1, Math.ceil(16 / px));
 const panel = (series, title, H) => {
  const top = 8, ph = H - top - 6, y = p => top + ph - p * ph, winOf = r => series === 'base' ? r.baseWin : r.expWin;
  const grid = [0, .25, .5, .75, 1].map(v => `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}"/><text x="${left - 6}" y="${y(v) + 4}" text-anchor="end">${v * 100}%</text>`).join('');
  const lines = shown.map(m => {
   let path = '', segs = [], marks = '', whisk = '', prev = null;
   rows.forEach((r, i) => {
    const v = r[series][m]; if (!v) { prev = null; return; }
    const cx = x(i) + off(m), cy = y(v.p), w = winOf(r), known = Boolean(v.metadata);
    if (prev) { const like = prev.metadata && prev.metadata === v.metadata; segs.push(`<path class="ln m${m.slice(1)}${like ? '' : ' dotted'}" d="M${prev.cx.toFixed(1)},${prev.cy.toFixed(1)}L${cx.toFixed(1)},${cy.toFixed(1)}"/>`); }
    marks += `<circle class="dot m${m.slice(1)}${known ? '' : ' hollow'}" cx="${cx.toFixed(1)}" cy="${cy.toFixed(1)}" r="${px < 10 ? 2.6 : 3.4}"/>`;
    if (ui.trendCI && series === 'base' && isNum(v.lo)) whisk += `<line class="whisker m${m.slice(1)}" x1="${cx.toFixed(1)}" x2="${cx.toFixed(1)}" y1="${y(v.lo).toFixed(1)}" y2="${y(v.hi).toFixed(1)}"/>`;
    if (v.arms.length > 1 && (series === 'base' || ui.trendExp === 'mean')) v.arms.forEach(a => { marks += `<circle class="armdot m${m.slice(1)}" cx="${cx.toFixed(1)}" cy="${y(a.r.p).toFixed(1)}" r="1.6"/>`; });
    prev = {cx, cy, w, metadata: v.metadata};
   });
   return whisk + segs.join('') + marks;
  }).join('');
  return `<div class="cp"><div class="cp-head"><h3>${esc(title)}</h3></div><svg class="tsvg" data-series="${series}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${grid}${lines}<g class="hl"></g></svg></div>`;
 };
 const lane = `<div class="band-label" style="margin-left:${left}px">Tick window per cohort</div><svg width="${W}" height="12" aria-hidden="true">${rows.map((r, i) => { const w = r.baseWin ?? r.expWin; return `<rect x="${x(i) - px / 2 + 0.5}" y="0" width="${Math.max(1, px - 1)}" height="10" fill="${winFill(w === 'mixed' ? null : w)}"${w === 'mixed' || !isNum(w) ? ' opacity=".55"' : ''}/>`; }).join('')}</svg><div class="reasons" style="margin:4px 0 0 ${left}px">${winOrder.map(w => `<span><i style="background:${winFill(w)}"></i>${Math.round(w / 1000)}k ticks</span>`).join('')}<span><i style="background:var(--g-nodata)"></i>unknown / mixed</span></div>`;
 const labelY = 62, axis = `<svg width="${W}" height="${labelY}" aria-hidden="true">${rows.map((r, i) => i % tickEvery === 0 ? `<line class="axis" x1="${x(i)}" x2="${x(i)}" y1="0" y2="4"/><text transform="translate(${x(i) + 3},9) rotate(60)" text-anchor="start">${esc(shortId(r.c.id))}</text>` : '').join('')}</svg>`;
 const old = host.querySelector('.trend-scroll'), pinned = !old || old.scrollLeft + old.clientWidth >= old.scrollWidth - 4, keepLeft = old?.scrollLeft;
 setHTML(host, `<div class="scroll trend-scroll"><div class="trend-stack" tabindex="0" role="group" aria-label="Cohort trend. Hover or use the left and right arrow keys to inspect cohorts." style="width:${W}px">${panel('base', 'Base arms (name-inferred; dots = individual arms)', 170)}${panel('exp', ui.trendExp === 'best' ? 'Experimental arms · best arm per cohort' : 'Experimental arms · mean of arms (dots = individual arms)', 170)}${lane}${axis}<div class="tip" hidden></div></div></div><div class="visually-hidden" id="trend-live" aria-live="polite"></div>`);
 const scroller = host.querySelector('.trend-scroll'); if (scroller) scroller.scrollLeft = pinned ? scroller.scrollWidth : keepLeft; // newest cohorts stay in view
 trendGeom = {x, off, left, W, px, n, rows, shown, idx: -1};
}
let trendGeom = null;
function trendInspect(i, py) {
 const g = trendGeom, stack = $('trend-chart').querySelector('.trend-stack'); if (!g || !stack) return;
 const r = g.rows[i], tip = stack.querySelector('.tip'), cx = g.x(i), yOf = p => 8 + 156 * (1 - p);
 stack.classList.add('hover');
 $$('.tsvg', stack).forEach(svg => { const series = svg.dataset.series; svg.querySelector('.hl').innerHTML = `<line class="cursor" x1="${cx}" x2="${cx}" y1="0" y2="170"/>` + g.shown.filter(m => r[series][m]).map(m => `<circle class="cursor-dot m${m.slice(1)}" cx="${(cx + g.off(m)).toFixed(1)}" cy="${yOf(r[series][m].p).toFixed(1)}" r="5"/>`).join(''); });
 const f = v => v ? `${v.k !== undefined ? `${v.k}/${v.n} = ` : ''}${ptsPct(v.p)}${v.lo !== undefined ? ` <span class="mut">(nominal 95% ${Math.round(v.lo * 100)}–${Math.round(v.hi * 100)}%)</span>` : ''}` : '<span class="mut">unknown</span>';
 const winTxt = w => isNum(w) ? `${Math.round(w / 1000)}k ticks` : w === 'mixed' ? 'mixed windows' : 'window unknown';
 const rowsHTML = (series, name) => `<tr><th colspan="2">${name}${series === 'base' ? (r.baseNames.length ? ` · ${esc(r.baseNames.join(', '))}` : ' · none in this cohort') : ''} <span class="mut">· ${winTxt(series === 'base' ? r.baseWin : r.expWin)}</span></th></tr>${g.shown.map(m => {
  const v = r[series][m];
  return `<tr><td><i class="m${m.slice(1)}"></i>${m}</td><td>${v ? `${f(v)} <span class="mut">(${esc(v.label)}; ${esc(v.version ?? 'definition unknown')})</span><div class="mut">${v.arms.map(a => `${esc(a.arm)}: ${a.r.k}/${a.r.n} · ${esc(tail(a.source.commit, 9) || 'commit unknown')} · eligible ${esc(a.source.comparison_eligible ?? 'unknown')}`).join('<br>')}</div>` : '<span class="mut">unknown or mixed metadata</span>'}</td></tr>`;
 }).join('')}`;
 const armsLine = Object.values(r.exp)[0]?.arms?.map(a => esc(a.arm)).join(', ');
 tip.innerHTML = `<h4>${esc(r.c.id)} · ${esc(r.c.date)}</h4><div class="mut">${esc(String(r.c.status).replace(/_/g, ' '))}${armsLine ? ' · arms: ' + armsLine : ''}</div><table>${rowsHTML('base', 'Base')}${rowsHTML('exp', ui.trendExp === 'best' ? 'Best experimental' : 'Experimental mean')}</table>`;
 tip.hidden = false; const w = tip.offsetWidth, h = tip.offsetHeight, sw = stack.clientWidth;
 tip.style.left = Math.max(0, Math.min(sw - w, cx + 16 + w > sw ? cx - w - 16 : cx + 16)) + 'px'; tip.style.top = Math.max(0, (py ?? 40) - h / 2) + 'px'; g.idx = i;
 return r;
}
function trendLeave() { const stack = $('trend-chart').querySelector('.trend-stack'); if (!stack) return; stack.classList.remove('hover'); stack.querySelector('.tip').hidden = true; $$('.hl', stack).forEach(h => h.innerHTML = ''); if (trendGeom) trendGeom.idx = -1; }
function trendMove(event) {
 const g = trendGeom, stack = event.target.closest?.('.trend-stack'); if (!g || !stack) return;
 const rect = stack.getBoundingClientRect(), px = event.clientX - rect.left, py = event.clientY - rect.top;
 if (px < g.left || px > g.W || event.target.closest('.cp-head')) return trendLeave();
 trendInspect(Math.max(0, Math.min(g.n - 1, Math.floor((px - g.left) / g.px))), py);
}
function trendKey(event) {
 const g = trendGeom; if (!g || !event.target.matches?.('.trend-stack')) return;
 if (event.key === 'Escape') return trendLeave();
 const d = {ArrowLeft: -1, ArrowRight: 1, Home: -Infinity, End: Infinity}[event.key]; if (d === undefined) return; event.preventDefault();
 const next = Math.max(0, Math.min(g.n - 1, g.idx < 0 ? g.n - 1 : isFinite(d) ? g.idx + d : d < 0 ? 0 : g.n - 1)), r = trendInspect(next, 60);
 $('trend-live').textContent = `${r.c.id}: ${g.shown.map(m => `${m} base ${r.base[m] ? ptsPct(r.base[m].p) : 'unknown'}, experimental ${r.exp[m] ? ptsPct(r.exp[m].p) : 'unknown'}`).join('; ')}`;
}

// ---------------------------------------------------------------- run inspector
const SERIES = [['Policy', 'policy_seconds', 'var(--c-p95)'], ['Executor', 'executor_seconds', 'var(--c-target)'], ['Model client', 'model_client_seconds', 'var(--c-total)'], ['Decision interval (includes waits)', 'decision_interval_seconds', 'var(--c-cap)']];
async function inspectRun(cohort, run) {
 const dialog = $('run-dialog'); $('run-title').textContent = `${cohort} / ${run}`; setHTML($('run-body'), '<div class="skeleton" style="height:200px"></div>');
 if (!dialog.open) dialog.showModal();
 try {
  const data = await getJSON('/api/run?' + new URLSearchParams({cohort, run})), rows = data.timing || [];
  const W = Math.max(320, Math.min(940, $('run-body').clientWidth || 900)), H = 220, left = 46, right = 10, pw = W - left - right, values = rows.flatMap(r => SERIES.map(s => r[s[1]])).filter(isNum);
  const axis = yAxis(0, Math.max(1, ...values)), y = v => 12 + (H - 38) - (v - axis.lo) / (axis.hi - axis.lo) * (H - 38), x = i => left + i / Math.max(1, rows.length - 1) * pw;
  const chart = values.length ? `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Wall time per decision">${axis.ticks.map(v => `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(v)}" y2="${y(v)}" stroke="var(--border)"/><text x="${left - 6}" y="${y(v) + 4}" text-anchor="end">${esc(fmt(v, axis.digits))}s</text>`).join('')}${SERIES.map(([name, key, color]) => { let d = '', prev = false; const dots = rows.map((r, i) => { const v = r[key]; if (!isNum(v)) { prev = false; return ''; } d += `${prev ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; prev = true; return `<circle cx="${x(i)}" cy="${y(v)}" r="2.6" fill="${color}"><title>Decision ${esc(r.index)} · tick ${esc(r.tick)} · ${esc(name)} ${esc(fmt(v, 3))}s</title></circle>`; }).join(''); return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.8"/>${dots}`; }).join('')}<text x="${left}" y="${H - 6}">decision 1</text><text x="${W - right}" y="${H - 6}" text-anchor="end">decision ${rows.length}</text></svg><div class="legend-key">${SERIES.map(([name, , color]) => `<span><i style="background:${color}"></i>${esc(name)}</span>`).join('')}</div>` : '<div class="empty"><strong>No wall-time measurements</strong>Older journals do not record policy or executor timings. Missing timing is unknown.</div>';
  const mods = data.mod_hashes && typeof data.mod_hashes === 'object' ? Object.entries(data.mod_hashes) : null;
  setHTML($('run-body'), `<section><h3>Decision timing</h3>${chart}<p class="hint">Journal scope: ${esc(data.timing_scope)}. Decision intervals include observation, execution and waits; they do not measure inference latency.</p></section><section><h3>Frozen mod hashes</h3>${mods ? `<details><summary>${mods.length} mods (exact hashes for client parity)</summary><div class="scroll"><table><thead><tr><th>Mod</th><th>Hash</th></tr></thead><tbody>${mods.map(([name, hash]) => `<tr><td>${esc(name)}</td><td><code title="${esc(hash)}">${esc(String(hash).slice(0, 16))}…</code> <button type="button" class="copy secondary small" data-value="${esc(hash)}">Copy full</button></td></tr>`).join('')}</tbody></table></div></details>` : '<p class="muted">Frozen mod hashes are unknown for this run.</p>'}</section>`);
 } catch (error) { setHTML($('run-body'), `<div class="callout" data-tone="bad" role="alert"><strong>Could not load this run:</strong> ${esc(error.message)}</div>`); }
}

// ---------------------------------------------------------------- events
document.addEventListener('click', event => {
 const target = event.target.closest('button, a[data-tab]'); if (!target) return;
 const d = target.dataset;
 if (target.matches('[role=tab]')) return showTab(d.tab);
 if (d.goto) return showTab(d.goto, true);
 if (d.act === 'retry') return refresh(true);
 if (d.act === 'retry-history') return loadHistory();
 if (d.openCohort) { openState.set('cohort:' + d.openCohort, true); $('filter').value = d.openCohort; registryShown = 15; showTab('jobs'); renderJobs(); return setTimeout(() => $('cohorts').querySelector('details')?.scrollIntoView({block: 'start'}), 0); }
 if (d.focus) { const field = $(d.focus).temp || $(d.focus); field.focus(); return field.scrollIntoView({block: 'center'}); }
 if (target.matches('.history-range')) { ui.hours = Number(d.hours); persistUi(); drawPerformance(); return loadHistory(); }
 if (d.panel) { ui.panels = ui.panels.includes(d.panel) ? ui.panels.filter(p => p !== d.panel) : [...ui.panels, d.panel]; persistUi(); return drawPerformance(); }
 if (d.series) { ui.hiddenSeries = ui.hiddenSeries.includes(d.series) ? ui.hiddenSeries.filter(s => s !== d.series) : [...ui.hiddenSeries, d.series]; persistUi(); return drawPerformance(); }
 if (d.state) { ui.registryState = d.state; registryShown = 15; persistUi(); return renderJobs(); }
 if (d.hview) { ui.historyView = d.hview; persistUi(); return renderHistory(); }
 if (d.metric) { ui.trendMetric = d.metric; persistUi(); return renderHistory(); }
 if (d.texp) { ui.trendExp = d.texp; persistUi(); return renderHistory(); }
 if (d.tm) { ui.trendMilestones = ui.trendMilestones.includes(d.tm) ? ui.trendMilestones.filter(m => m !== d.tm) : [...ui.trendMilestones, d.tm]; persistUi(); return renderHistory(); }
 if (d.family) { ui.family = d.family; persistUi(); historyShown = 14; return renderHistory(); }
 if (d.milestone) { ui.milestone = d.milestone; persistUi(); return renderHistory(); }
 if (d.moreLive !== undefined) { liveShown = 999; return renderLive(); }
 if (d.moreHistory !== undefined) { historyShown += 14; return renderHistory(); }
 if (d.moreCohorts !== undefined) { registryShown += 15; return renderJobs(); }
 if (d.moreJobs) { pageSize.set(d.moreJobs, (pageSize.get(d.moreJobs) || 20) + 20); return renderJobs(); }
 if (d.expand) { expanded.has(d.expand) ? expanded.delete(d.expand) : expanded.add(d.expand); sessionStorage.setItem('laya.expanded.v1', JSON.stringify([...expanded])); return renderJobs(); }
 if (d.notes) return loadNotes(d.notes, d.more === '1');
 if (target.matches('[data-close]')) return target.closest('dialog').close();
 if (target.matches('.inspect')) return inspectRun(d.cohort, d.run);
 if (target.matches('.copy')) return (navigator.clipboard?.writeText(d.value) ?? Promise.reject(new Error('Clipboard unavailable here; select and copy the text manually.'))).then(() => toast('Copied.', 'ok')).catch(error => toast(error.message, 'bad'));
 if (target.matches('.view')) { const key = `view:${d.cohort}/${d.run}`; return act(key, () => post('/api/view', {cohort: d.cohort, run: d.run, stop: d.stop === 'true'}), d.stop === 'true' ? 'Viewing route stopped.' : 'Viewing route enabled for 1 hour (Starship only).'); }
 if (target.matches('.sync-mods')) { syncTarget = {cohort: d.cohort, run: d.run}; $('sync-target').textContent = `Run: ${d.cohort} / ${d.run}`; const dialog = $('sync-dialog'); return dialog.showModal ? dialog.showModal() : (confirm('Sync this run’s mods to Starship Factorio?') && startSync()); }
 if (d.bump) { bumpDelta = Number(d.bump); return syncControls(); }
 if (target.matches('.adjust')) { const form = $('ceiling'), next = Math.max(0, (Number(form.slots.value) || 0) + Number(d.delta)); form.slots.value = next; ceilingDirty = true; return syncControls(); }
 if (d.scope) { ceilingScope = d.scope; ceilingDirty = true; return syncControls(); }
 if (target.id === 'refresh') return refresh(true);
 if (target.id === 'rules') return $('rules-dialog').showModal();
 if (target.id === 'resume') return act('control:resume', () => post('/api/control', {action: 'resume'}), 'Manual and temporary ceilings cleared; automatic admission resumes.').then(ok => { if (ok) { ceilingDirty = false; syncControls(); } });
 if (target.id === 'bump-apply') return act('control:bump', () => post('/api/control', {action: 'bump', delta: bumpDelta}), 'Admission target raised.').then(ok => { if (ok) { bumpedAt = snapshot.now; syncControls(); } });
 if (target.id === 'mode-reset') { modeDirty = false; return syncControls(); }
 if (target.id === 'cool') return act('control:cool', () => post('/api/control', {action: 'cool'}), 'Cooling started: power-saver for 1 hour.');
 if (target.id === 'cool-cancel') return act('control:cool_cancel', () => post('/api/control', {action: 'cool_cancel'}), 'Cooling cancelled; your previous profile and mode were restored.');
 if (target.id === 'ceiling-reset') { ceilingDirty = false; ceilingScope = 'permanent'; return syncControls(); }
 if (target.id === 'floors-reset') { floorsDirty = false; return syncControls(); }
 if (target.id === 'export') { if (!snapshot) return; const url = URL.createObjectURL(new Blob([JSON.stringify(snapshot, null, 2)], {type: 'application/json'})), a = document.createElement('a'); a.href = url; a.download = 'laya-cohort-snapshot.json'; a.click(); URL.revokeObjectURL(url); }
});
function startSync() {
 const {cohort, run} = syncTarget || {}; if (!cohort) return;
 act(`sync:${cohort}/${run}`, async () => { const r = await post('/api/sync-mods', {cohort, run}); return {...r, message: `${r.message || 'Mods synced.'}${isNum(r.files_verified) ? ` ${r.files_verified} files verified.` : ''}`}; }, 'Mods synced. Restart Factorio on Starship before joining.');
}
$('sync-form').addEventListener('submit', event => { if (event.submitter?.value === 'confirm') startSync(); });
$('tabs').addEventListener('keydown', event => {
 const tabs = $$('[role=tab]'), i = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true'), next = {ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}[event.key];
 if (next === undefined) return; event.preventDefault(); showTab(tabs[(next + tabs.length) % tabs.length].dataset.tab, true);
});
$('ceiling').addEventListener('input', () => { ceilingDirty = true; syncControls(); });
$('ceiling').addEventListener('change', () => { ceilingDirty = true; syncControls(); });
$('ceiling').addEventListener('submit', event => {
 event.preventDefault(); if (ceilingProblem()) return;
 const f = event.currentTarget, body = {action: 'ceiling', slots: Number(f.slots.value), duration: ceilingDuration()};
 act('control:ceiling', () => post('/api/control', body), body.duration ? `Temporary limit of ${body.slots} runs set for ${body.duration}.` : `Permanent limit of ${body.slots} runs set.`).then(ok => { if (ok) { ceilingDirty = false; syncControls(); } });
});
$('mode').addEventListener('change', () => { modeDirty = true; syncControls(); });
$('mode').addEventListener('submit', event => {
 event.preventDefault(); if (!snapshot) return;
 const v = modeView(analyze()); if (v.same) return;
 act('control:mode', () => post('/api/control', {action: 'mode', mode: v.pick, system_profile: v.form.profile.checked}), `Mode set to ${MODES[v.pick].label}.`).then(ok => { if (ok) { modeDirty = false; syncControls(); } });
});
$('floors').addEventListener('input', () => { floorsDirty = true; syncControls(); });
$('floors').addEventListener('submit', event => {
 event.preventDefault(); if (floorsProblem()) return;
 const f = event.currentTarget;
 act('control:floors', () => post('/api/control', {action: 'configure', p95: Number(f.p95.value), p98: Number(f.p98.value), temp: Number(f.temp.value)}), 'Safeguards saved. They apply on the next scheduler cycle.').then(ok => { if (ok) { floorsDirty = false; syncControls(); } });
});
$('filter').addEventListener('input', () => { registryShown = 15; renderJobs(); });
$('history-filter').addEventListener('input', () => { historyShown = 14; renderHistory(); });
$('unranked').addEventListener('change', event => { ui.unranked = event.target.checked; persistUi(); renderHistory(); });
// Native <details> state is kept in openState so periodic re-renders do not collapse anything.
document.addEventListener('toggle', event => {
 const element = event.target; if (!(element instanceof HTMLDetailsElement) || !element.dataset.key) return;
 const key = element.dataset.key; if (openState.get(key) === element.open) return;
 openState.set(key, element.open);
 if (key.startsWith('review:') && element.open) loadNotes(key.slice(7)); else if (key.startsWith('cohort:')) renderJobs();
}, true);
$('trend-window').addEventListener('change', event => { ui.trendWindow = event.target.value; persistUi(); renderHistory(); });
$('trend-ci').addEventListener('change', event => { ui.trendCI = event.target.checked; persistUi(); renderHistory(); });
document.addEventListener('change', event => { if (event.target.dataset?.jobfilter) { jobFilter.set(event.target.dataset.jobfilter, event.target.value); pageSize.delete(event.target.dataset.jobfilter); renderJobs(); } });
document.addEventListener('input', event => {
 const form = event.target.closest?.('.note-form'); if (!form) return;
 const key = `${form.dataset.cohort}/${form.dataset.run}`, draft = {text: form.text.value, tags: form.tags.value};
 if (draft.text || draft.tags) drafts.set(key, draft); else drafts.delete(key);
 persistDrafts(); const counter = form.querySelector('.counter'); if (counter) counter.textContent = `${draft.text.length}/10000 · Ctrl+Enter saves · draft survives refresh`;
});
document.addEventListener('keydown', event => { if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && event.target.matches?.('.note-form textarea')) { event.preventDefault(); event.target.form.requestSubmit(); } });
document.addEventListener('submit', event => {
 const form = event.target; if (!form.matches?.('.note-form')) return; event.preventDefault();
 const cohort = form.dataset.cohort, run = form.dataset.run, key = `${cohort}/${run}`, submitted = {text: form.text.value, tags: form.tags.value};
 act('note:' + key, async () => {
  const r = await post('/api/note', {cohort, run, ...submitted}), current = drafts.get(key);
  // A review can be edited while the request is in flight; keep the newer draft.
  if (current?.text === submitted.text && current?.tags === submitted.tags) { drafts.delete(key); persistDrafts(); }
  notesState.delete(key); return r;
 }, 'Review saved.').then(ok => { if (ok) loadNotes(key); });
});
const trendHost = $('trend-chart');
trendHost.addEventListener('pointermove', trendMove); trendHost.addEventListener('pointerleave', trendLeave); trendHost.addEventListener('keydown', trendKey); trendHost.addEventListener('blur', trendLeave, true);
let trendFrame = 0, trendWidth = 0;
new ResizeObserver(() => { const width = Math.round(trendHost.clientWidth); if (!width || Math.abs(width - trendWidth) < 3) return; trendWidth = width; cancelAnimationFrame(trendFrame); trendFrame = requestAnimationFrame(() => { trendHost._html = null; drawTrend(); }); }).observe(trendHost);
const chartHost = $('chart');
chartHost.addEventListener('pointermove', onChartMove); chartHost.addEventListener('pointerleave', leaveChart); chartHost.addEventListener('keydown', onChartKey); chartHost.addEventListener('blur', leaveChart, true);
let resizeFrame = 0, lastWidth = 0;
new ResizeObserver(() => { const width = Math.round(chartHost.clientWidth); if (!width || Math.abs(width - lastWidth) < 3) return; lastWidth = width; cancelAnimationFrame(resizeFrame); resizeFrame = requestAnimationFrame(() => { chartHost._html = null; drawPerformance(); }); }).observe(chartHost);
setInterval(() => { if (!document.hidden && !document.querySelector('.note-form:focus-within, #ceiling:focus-within, #floors:focus-within')) refresh(); }, 30000);
setInterval(() => { if (snapshot && !document.hidden) renderStatus(); }, 5000);
document.addEventListener('visibilitychange', () => { if (!document.hidden && Date.now() - lastOk > 15000) refresh(); });
showTab(location.hash.slice(1) || ui.tab);
refresh();
