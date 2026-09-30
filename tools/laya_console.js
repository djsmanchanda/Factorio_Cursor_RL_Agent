// Path: tools/laya_console.js
// Purpose: Poll the Laya console API for status/results/logs and invoke its start/stop controls.

const token = document.querySelector('meta[name="action-token"]').content;
const driverStatus = document.querySelector('#driver-status');
const runNameLabel = document.querySelector('#run-name');
const phaseState = document.querySelector('#phase-state');
const configGrid = document.querySelector('#config-grid');
const roundDetail = document.querySelector('#round-detail');
const labellerWrap = document.querySelector('#labeller-wrap');
const labellerBody = document.querySelector('#labeller-body');
const workerBody = document.querySelector('#worker-body');
const gpuGrid = document.querySelector('#gpu-grid');
const controlsMessage = document.querySelector('#controls-message');
const actionMessage = document.querySelector('#action-message');
const startForm = document.querySelector('#start-form');
const autoResumeInput = document.querySelector('#start-auto-resume');
const manualResumeFields = document.querySelector('#manual-resume-fields');
const stopAfterRoundButton = document.querySelector('#stop-after-round');
const stopNowButton = document.querySelector('#stop-now');
const resultsState = document.querySelector('#results-state');
const bestGrid = document.querySelector('#best-grid');
const trendGrid = document.querySelector('#trend-grid');
const resultsBody = document.querySelector('#results-body');
const output = document.querySelector('#log-output');
const follow = document.querySelector('#follow-log');

let currentRunName = null;
let selectedLog = 'summary';
let offset = 0;

function setOnline(id, online, detail = '') {
  const element = document.querySelector(id);
  element.classList.toggle('online', online);
  element.title = detail;
}

function fillGrid(grid, rows) {
  grid.replaceChildren();
  rows.forEach(([label, value]) => {
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.textContent = value;
    grid.append(dt, dd);
  });
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    const state = await response.json();
    if (!response.ok) throw new Error(state.error || 'Status request failed');
    currentRunName = state.run_name;
    runNameLabel.textContent = state.run_name || '—';
    setOnline('#driver-status', state.active, state.pid ? `PID ${state.pid}` : 'no driver process');
    const phase = state.phase || {};
    const busy = state.active;
    phaseState.textContent = busy ? `${phase.phase.toUpperCase()}` : (state.finished ? 'FINISHED' : 'INACTIVE');
    phaseState.classList.toggle('busy', busy);

    const config = state.config || {};
    fillGrid(configGrid, [
      ['Instances', (config.instances || []).join(' ') || '—'],
      ['Procs', config.procs ?? '—'],
      ['Speed', config.speed ?? '—'],
      ['Hours', config.hours ?? '—'],
      ['Start', config.start_time || '—'],
      ['Deadline', config.deadline || '—'],
      ['Prefix', state.prefix],
      ['Stop requested', state.stop_requested ? 'yes (finishing current round)' : 'no'],
    ]);

    let detail = phase.detail || '';
    if (phase.round !== null && phase.round !== undefined) {
      detail = `Round ${phase.round}${phase.level ? ` · level ${phase.level}` : ''} — ${detail}`;
    }
    roundDetail.textContent = detail || 'No round activity yet.';

    if (phase.phase === 'labelling' && (state.labellers || []).length) {
      labellerWrap.hidden = false;
      labellerBody.replaceChildren();
      state.labellers.forEach(row => {
        const tr = document.createElement('tr');
        const stateText = row.finished ? 'finished' : (row.stale ? 'stale' : 'running');
        [
          `p${row.process}`, row.labels,
          row.minutes_since_last_write === null ? '—' : `${row.minutes_since_last_write} min ago`,
          stateText,
        ].forEach((value, index) => {
          const td = document.createElement('td');
          td.textContent = value;
          if (index === 3 && row.stale) td.className = 'stale';
          tr.append(td);
        });
        labellerBody.append(tr);
      });
    } else {
      labellerWrap.hidden = true;
    }

    workerBody.replaceChildren();
    if (!(state.workers || []).length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 2;
      td.className = 'empty-row';
      td.textContent = state.active ? 'No worker data.' : 'No active run.';
      tr.append(td);
      workerBody.append(tr);
    } else {
      state.workers.forEach(worker => {
        const tr = document.createElement('tr');
        const nameCell = document.createElement('td');
        nameCell.textContent = worker.instance;
        const stateCell = document.createElement('td');
        stateCell.textContent = worker.running ? 'running' : 'stopped';
        if (!worker.running) stateCell.className = 'stale';
        tr.append(nameCell, stateCell);
        workerBody.append(tr);
      });
    }

    const gpu = state.gpu;
    const mem = state.host_memory;
    fillGrid(gpuGrid, [
      ['GPU util', gpu ? `${gpu.utilization_percent}%` : 'n/a'],
      ['GPU temp', gpu ? `${gpu.temperature_c} C` : 'n/a'],
      ['GPU memory', gpu ? `${Math.round(gpu.memory_used_mb)} / ${Math.round(gpu.memory_total_mb)} MB` : 'n/a'],
      ['Host memory', mem ? `${mem.used_mb} / ${mem.total_mb} MB` : 'n/a'],
    ]);

    stopAfterRoundButton.disabled = !state.active || state.stop_requested;
    stopNowButton.disabled = !state.active;
  } catch (error) {
    phaseState.textContent = 'CONSOLE API OFFLINE';
    phaseState.classList.add('busy');
  }
}

function formatRow(row) {
  return [
    row.batch, row.level ?? '—', row.seeds, row.completed,
    row.median_cost_ticks ?? '—', row.mean_cost_ticks ?? '—', row.p95_cost_ticks ?? '—',
    row.completion_losses ?? '—', row.mean_delta_vs_baseline ?? '—',
    row.lower_cost_pairs ?? row.faster_than_baseline ?? '—',
  ];
}

function renderTrend(container, level, series) {
  const card = document.createElement('div');
  card.className = 'trend-card';
  const heading = document.createElement('h3');
  heading.textContent = `Level ${level} median cost (ticks)`;
  card.append(heading);
  const points = (series || []).filter(p => p.round !== null && Number.isFinite(p.median_cost_ticks));
  if (!points.length) {
    const empty = document.createElement('p');
    empty.className = 'empty-row';
    empty.textContent = 'No round evaluations yet.';
    card.append(empty);
    container.append(card);
    return;
  }
  const width = 300, height = 100, pad = 8;
  const rounds = points.map(p => p.round);
  const values = points.map(p => p.median_cost_ticks);
  const minRound = Math.min(...rounds), maxRound = Math.max(...rounds, minRound + 1);
  const minValue = Math.min(...values, 0), maxValue = Math.max(...values, minValue + 1);
  const scaleX = r => pad + ((r - minRound) / (maxRound - minRound || 1)) * (width - 2 * pad);
  const scaleY = v => height - pad - ((v - minValue) / (maxValue - minValue || 1)) * (height - 2 * pad);
  const bySeries = {};
  points.forEach(p => { (bySeries[p.series] ||= []).push(p); });
  const colors = {features: '#e8872f', laya: '#71bf72'};
  const svgParts = [];
  Object.entries(bySeries).forEach(([name, pts]) => {
    const path = pts.map(p => `${scaleX(p.round)},${scaleY(p.median_cost_ticks)}`).join(' ');
    svgParts.push(`<polyline points="${path}" fill="none" stroke="${colors[name] || '#a89f91'}" stroke-width="2"/>`);
  });
  card.insertAdjacentHTML('beforeend',
    `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Level ${level} trend">${svgParts.join('')}</svg>`);
  container.append(card);
}

async function refreshResults() {
  try {
    const response = await fetch('/api/results', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Results request failed');
    resultsState.textContent = `${(data.rows || []).length} EVALUATIONS`;

    bestGrid.replaceChildren();
    [1, 2].forEach(level => {
      const features = (data.best_features || {})[level];
      const laya = (data.best_laya || {})[level];
      const card = document.createElement('div');
      card.className = 'best-card';
      card.innerHTML = `<strong>Level ${level} best</strong>` +
        `Features: ${features ? `${features.batch} · ${features.median_cost_ticks} ticks` : 'none yet'}<br>` +
        `Laya: ${laya ? `${laya.batch} · ${laya.median_cost_ticks} ticks` : 'none yet'}`;
      bestGrid.append(card);
    });

    trendGrid.replaceChildren();
    renderTrend(trendGrid, 1, (data.trend || {})[1]);
    renderTrend(trendGrid, 2, (data.trend || {})[2]);

    resultsBody.replaceChildren();
    const rows = data.rows || [];
    if (!rows.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 10;
      td.className = 'empty-row';
      td.textContent = 'No evaluations yet.';
      tr.append(td);
      resultsBody.append(tr);
      return;
    }
    [...rows].reverse().forEach(row => {
      const tr = document.createElement('tr');
      formatRow(row).forEach(value => {
        const td = document.createElement('td');
        td.textContent = value;
        tr.append(td);
      });
      resultsBody.append(tr);
    });
  } catch (error) {
    resultsState.textContent = 'UNAVAILABLE';
  }
}

async function refreshLog() {
  try {
    const response = await fetch(`/api/logs?name=${encodeURIComponent(selectedLog)}&offset=${offset}`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Log request failed');
    if (data.reset) output.textContent = '';
    if (data.text) {
      output.append(document.createTextNode(data.text));
      if (follow.checked) output.scrollTop = output.scrollHeight;
    }
    offset = data.offset;
  } catch (error) {
    output.append(document.createTextNode(`\n[laya console] ${error.message}\n`));
  }
}

document.querySelectorAll('[data-log]').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('[data-log]').forEach(other => other.classList.toggle('active', other === tab));
    selectedLog = tab.dataset.log;
    offset = 0;
    output.textContent = '';
  });
});
document.querySelector('#clear-view').addEventListener('click', () => { output.textContent = ''; });

autoResumeInput.addEventListener('change', () => {
  manualResumeFields.hidden = autoResumeInput.checked;
});

async function postAction(action, payload) {
  const response = await fetch(`/api/actions/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Action-Token': token},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `${action} was rejected`);
  return data;
}

startForm.addEventListener('submit', async event => {
  event.preventDefault();
  if (!confirm('Start a new Laya overnight training run with these settings?')) return;
  const payload = {
    confirmation: 'START_LAYA_RUN',
    hours: parseFloat(document.querySelector('#start-hours').value),
    instances: document.querySelector('#start-instances').value.trim(),
    procs: parseInt(document.querySelector('#start-procs').value, 10),
    speed: parseFloat(document.querySelector('#start-speed').value),
    seeds_per_round: parseInt(document.querySelector('#start-seeds').value, 10),
    run_name: document.querySelector('#start-run-name').value.trim() || 'overnight',
    prefix: document.querySelector('#start-prefix').value.trim() || 'ov',
    min_case_round: document.querySelector('#start-min-case-round').value || null,
    finetune_first: document.querySelector('#start-finetune-first').checked,
    auto_resume: autoResumeInput.checked,
  };
  if (!autoResumeInput.checked) {
    payload.resume_round = document.querySelector('#start-resume-round').value;
    payload.start_policy = document.querySelector('#start-policy').value.trim();
    payload.l1_seed = document.querySelector('#start-l1-seed').value;
    payload.l2_seed = document.querySelector('#start-l2-seed').value;
  }
  try {
    const data = await postAction('start', payload);
    actionMessage.textContent = `Started PID ${data.result.pid} for run '${data.result.run_name}'.`;
    await refreshStatus();
  } catch (error) {
    actionMessage.textContent = error.message;
  }
});

stopAfterRoundButton.addEventListener('click', async () => {
  if (!confirm('Request a graceful stop after the current round finishes? The driver will still run its final evaluation and stop the workers.')) return;
  try {
    const data = await postAction('stop_after_round', {confirmation: 'STOP_LAYA_RUN_AFTER_ROUND', run_name: currentRunName});
    actionMessage.textContent = `Stop requested for '${data.result.run_name}'.`;
    await refreshStatus();
  } catch (error) {
    actionMessage.textContent = error.message;
  }
});

stopNowButton.addEventListener('click', async () => {
  if (!confirm('Stop the run immediately? This kills the driver process group, unpauses its instances, and stops its workers without a final evaluation.')) return;
  try {
    const data = await postAction('stop_now', {confirmation: 'STOP_LAYA_RUN_NOW', run_name: currentRunName});
    actionMessage.textContent = `Stopped PID ${data.result.pid} (${data.result.signals_sent.join(', ')}).`;
    await refreshStatus();
  } catch (error) {
    actionMessage.textContent = error.message;
  }
});

refreshStatus();
refreshResults();
refreshLog();
setInterval(refreshStatus, 3000);
setInterval(refreshResults, 10000);
setInterval(refreshLog, 1500);
