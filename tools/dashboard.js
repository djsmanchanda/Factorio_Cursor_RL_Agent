// Path: tools/dashboard.js
// Purpose: Poll live Factorio logs and invoke the dashboard's fixed local actions.

const token = document.querySelector('meta[name="action-token"]').content;
const output = document.querySelector('#log-output');
const follow = document.querySelector('#follow-log');
const actionMessage = document.querySelector('#action-message');
const operationState = document.querySelector('#operation-state');
const gameAddress = document.querySelector('#game-address');
const priorityBody = document.querySelector('#priority-body');
const priorityCount = document.querySelector('#priority-count');
const priorityTabs = [...document.querySelectorAll('[data-priority-tab]')];
const actionButtons = [...document.querySelectorAll('[data-action]')];
const researchInput = document.querySelector('#research-technologies');
const researchSet = document.querySelector('#research-set');
const researchAppend = document.querySelector('#research-append');
const researchMessage = document.querySelector('#research-message');
const researchQueueCount = document.querySelector('#research-queue-count');
const researchQueue = document.querySelector('#research-queue');
const researchButtons = [researchSet, researchAppend];
const helperReport = document.querySelector('#helper-report');
const helperStatus = document.querySelector('#helper-status');
const helperMessage = document.querySelector('#helper-message');
const helperComment = document.querySelector('#helper-comment');
const helperObservations = document.querySelector('#helper-observations');
const helperSkillId = document.querySelector('#helper-skill-id');
const logisticInventoryStatus = document.querySelector('#logistic-inventory-status');
const logisticInventorySummary = document.querySelector('#logistic-inventory-summary');
const logisticInventoryBody = document.querySelector('#logistic-inventory-body');
let latestHelperRun = null;
let supervisedLoopRunning = false;
let dashboardActionBusy = false;
function updateActionAvailability() {
  actionButtons.forEach(button => {
    const action = button.dataset.action;
    button.disabled = dashboardActionBusy || (supervisedLoopRunning && action !== 'stop_observation_loop');
    if (action === 'stop_observation_loop' && !supervisedLoopRunning) button.disabled = true;
  });
  researchButtons.forEach(button => { button.disabled = dashboardActionBusy || supervisedLoopRunning; });
}
const titles = {
  runner: 'Autonomous runner',
  control: 'Dashboard actions',
  server: 'Factorio server',
  errors: 'Server errors',
};
let selectedLog = 'runner';
let offset = 0;
let clearedAtOffset = false;
let selectedPriorityTab = 'active';
let priorityItems = [];

function setOnline(id, online, detail = '') {
  const element = document.querySelector(id);
  element.classList.toggle('online', online);
  element.title = detail;
}

function helperList(label, items) {
  if (!items.length) return null;
  const heading = document.createElement('h3');
  heading.textContent = label;
  const list = document.createElement('ul');
  items.forEach(item => {
    const entry = document.createElement('li');
    entry.textContent = item;
    list.append(entry);
  });
  const section = document.createElement('div');
  section.append(heading, list);
  return section;
}

function renderHelper(data) {
  const report = data.latest;
  if (!report) {
    helperReport.replaceChildren();
    const empty = document.createElement('p');
    empty.className = 'helper-empty';
    empty.textContent = 'No post-run report is available yet.';
    helperReport.append(empty);
    return;
  }
  latestHelperRun = report.run_id;
  helperStatus.textContent = String(data.review_status || 'unreviewed').toUpperCase();
  helperReport.replaceChildren();
  const summary = document.createElement('div');
  summary.className = 'helper-summary';
  [
    ['Run', report.run_id],
    ['Outcome', report.terminal_outcome],
    ['Stage', report.mission_stage],
    ['Confidence', `${report.confidence} · ${report.status}`],
  ].forEach(([label, value]) => {
    const row = document.createElement('p');
    const name = document.createElement('strong');
    const detail = document.createElement('code');
    name.textContent = `${label}: `;
    detail.textContent = value;
    row.append(name, detail);
    summary.append(row);
  });
  const timeline = helperList('Timeline', [report.timeline_summary].filter(Boolean));
  const moments = document.createElement('div');
  moments.append(Object.assign(document.createElement('h3'), {textContent: 'Notable moments'}));
  (report.notable_moments || []).forEach(moment => {
    const card = document.createElement('article');
    card.className = 'notable-moment';
    const title = document.createElement('h4');
    title.textContent = moment.title;
    const body = document.createElement('dl');
    const fields = [
      ['What happened', moment.what_happened],
      ['What was expected', moment.what_was_expected],
      ['Possible cause', moment.cause],
      ['Fix direction', moment.fix_direction],
      ['Confidence', moment.confidence],
      ['Evidence', moment.evidence],
      ['Classification', moment.classification],
      ['Review status', moment.review_status],
    ];
    fields.forEach(([key, value]) => {
      const term = document.createElement('dt');
      const detail = document.createElement('dd');
      term.textContent = key;
      detail.textContent = value;
      body.append(term, detail);
    });
    card.append(title, body);
    moments.append(card);
  });
  const evidence = helperList('Evidence', [
    report.packet_path,
  ].filter(Boolean));
  const skills = helperList('Related skills', report.relevant_casebook_skills || []);
  helperReport.append(summary, timeline, moments, evidence, skills);
}

async function refreshHelper() {
  try {
    const response = await fetch('/api/helper', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Helper Agent request failed');
    renderHelper(data);
  } catch (error) {
    helperStatus.textContent = 'OFFLINE';
    helperMessage.textContent = error.message;
  }
}

async function submitHelperFeedback(verdict, action = null) {
  if (!latestHelperRun) {
    helperMessage.textContent = 'No report is available for feedback.';
    return;
  }
  const payload = {
    run_id: latestHelperRun,
    verdict,
    missed_issues: helperObservations.value.split('\n').map(line => line.trim()).filter(Boolean),
    corrections: [],
    extra_observations: [],
    skill_actions: action && helperSkillId.value.trim() ? [{action, skill_id: helperSkillId.value.trim()}] : [],
    comment: helperComment.value,
  };
  try {
    const response = await fetch('/api/actions/helper_feedback', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Action-Token': token},
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Feedback was rejected');
    helperMessage.textContent = `Feedback stored: ${verdict}.`;
    await refreshHelper();
  } catch (error) {
    helperMessage.textContent = error.message;
  }
}

async function refreshStatus() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    const state = await response.json();
    gameAddress.textContent = state.server.game_address;
    gameAddress.title = `Factorio multiplayer address: ${state.server.game_address}`;
    setOnline('#game-status', state.server.game);
    setOnline('#rcon-status', state.server.rcon);
    setOnline('#runner-status', state.runner.running, `PID: ${state.runner.pids.join(', ') || 'none'}`);
    const busy = Boolean(state.operation.active);
    operationState.textContent = busy ? `RUNNING · ${state.operation.active}` : 'READY';
    operationState.classList.toggle('busy', busy);
    dashboardActionBusy = busy;
    updateActionAvailability();
    actionMessage.textContent = state.operation.last_result;
  } catch (error) {
    operationState.textContent = 'DASHBOARD API OFFLINE';
    operationState.classList.add('busy');
  }
}

async function refreshLog() {
  try {
    const response = await fetch(`/api/logs?name=${encodeURIComponent(selectedLog)}&offset=${offset}`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Log request failed');
    if (data.reset) {
      output.textContent = '';
      clearedAtOffset = false;
    }
    if (data.text) {
      if (output.textContent === 'Waiting for log output…' || !clearedAtOffset) {
        if (!clearedAtOffset) output.textContent = '';
      }
      output.append(document.createTextNode(data.text));
      if (follow.checked) output.scrollTop = output.scrollHeight;
    }
    offset = data.offset;
    clearedAtOffset = true;
  } catch (error) {
    output.append(document.createTextNode(`\n[dashboard] ${error.message}\n`));
  }
}

function priorityCell(text, className = '') {
  const cell = document.createElement('td');
  cell.textContent = text;
  if (className) cell.className = className;
  return cell;
}

function renderPriorities(items) {
  priorityBody.replaceChildren();
  priorityItems = items;
  const activeItems = items.filter(item => item.status !== 'complete');
  const completedItems = items.filter(item => item.status === 'complete');
  const visibleItems = selectedPriorityTab === 'completed' ? completedItems : activeItems;
  const ordered = [...visibleItems].sort((a, b) =>
    b.rating - a.rating ||
    a.created_tick - b.created_tick ||
    a.item.localeCompare(b.item)
  );
  priorityCount.textContent = `${activeItems.length} ACTIVE · ${completedItems.length} COMPLETED`;
  if (!ordered.length) {
    const row = document.createElement('tr');
    const message = selectedPriorityTab === 'completed'
      ? 'Completed tasks will appear here.'
      : 'Queue will appear when the runner starts.';
    const cell = priorityCell(message);
    cell.colSpan = 5;
    cell.className = 'empty-priority';
    row.append(cell);
    priorityBody.append(row);
    return;
  }
  ordered.forEach(item => {
    const row = document.createElement('tr');
    const task = priorityCell(item.item);
    if (item.reason) {
      const reason = document.createElement('small');
      reason.textContent = item.reason;
      task.append(reason);
    }
    const progressCell = document.createElement('td');
    const progress = document.createElement('progress');
    progress.max = 100;
    progress.value = item.progress_percent;
    progressCell.append(progress, ` ${item.progress_percent}%`);
    const minutes = Math.floor(item.elapsed_ticks / 3600);
    row.append(
      task,
      priorityCell(`${item.rating}/100`),
      progressCell,
      priorityCell(`${item.elapsed_ticks.toLocaleString()} ticks · ${minutes}m`),
      priorityCell(item.status, item.status),
    );
    priorityBody.append(row);
  });
}

function formatItemCount(count) {
  return new Intl.NumberFormat().format(count);
}

function renderLogisticInventory(report) {
  const items = Object.entries(report.total_items || {})
    .filter(([, count]) => Number.isFinite(count) && count > 0)
    .sort(([leftName, leftCount], [rightName, rightCount]) =>
      rightCount - leftCount || leftName.localeCompare(rightName));
  const networks = report.networks || [];
  const networkText = `${networks.length} NETWORK${networks.length === 1 ? '' : 'S'}`;
  const ports = networks.reduce((total, network) => total + (network.roboports || 0), 0);
  logisticInventoryStatus.textContent = `${items.length} ITEM TYPES`;
  logisticInventorySummary.textContent = `${networkText} · ${ports} ROBOports · ${report.disconnected_roboports || 0} without a network · tick ${report.tick}`;
  logisticInventoryBody.replaceChildren();
  if (!items.length) {
    const row = document.createElement('tr');
    const cell = priorityCell('No available items in the live logistic networks.');
    cell.colSpan = 2;
    cell.className = 'empty-priority';
    row.append(cell);
    logisticInventoryBody.append(row);
    return;
  }
  items.forEach(([item, count]) => {
    const row = document.createElement('tr');
    row.append(priorityCell(item), priorityCell(formatItemCount(count)));
    logisticInventoryBody.append(row);
  });
}

async function refreshLogisticInventory() {
  try {
    const response = await fetch('/api/logistic-inventory', {cache: 'no-store'});
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || 'Logistic inventory request failed');
    renderLogisticInventory(report);
  } catch (error) {
    logisticInventoryStatus.textContent = 'UNAVAILABLE';
    logisticInventorySummary.textContent = error.message;
  }
}

async function refreshPriorities() {
  try {
    const response = await fetch('/api/priorities', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Priority request failed');
    renderPriorities(data.items);
  } catch (error) {
    renderPriorities([]);
    priorityCount.textContent = 'UNAVAILABLE';
  }
}

async function refreshResearchQueue() {
  try {
    const response = await fetch('/api/research', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Research queue request failed');
    const items = data.items || [];
    const pending = items.filter(item => item.status !== 'completed').length;
    researchQueueCount.textContent = pending ? `${pending} PENDING` : 'NO QUEUE';
    researchQueue.replaceChildren();
    items.forEach(item => {
      const badge = document.createElement('span');
      badge.className = item.status;
      badge.textContent = `${item.technology} · ${item.status}`;
      researchQueue.append(badge);
    });
  } catch (error) {
    researchMessage.textContent = error.message;
  }
}

async function refreshResearchOptions() {
  try {
    const response = await fetch('/api/research/options', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Research options request failed');
    const selected = new Set([...researchInput.selectedOptions].map(option => option.value));
    researchInput.replaceChildren();
    (data.options || []).forEach(item => {
      const option = document.createElement('option');
      option.value = item.technology;
      option.textContent = `${item.technology} · ${item.state}`;
      option.title = `Science packs: ${Object.keys(item.science_packs || {}).join(', ') || 'none'}`;
      option.selected = selected.has(item.technology);
      researchInput.append(option);
    });
    if (!data.options || !data.options.length) {
      const option = document.createElement('option');
      option.textContent = 'No open research targets';
      option.disabled = true;
      researchInput.append(option);
    }
    const active = data.current_target || data.current_research;
    if (active) researchMessage.textContent = `Active target: ${active}. Select a successor to queue it.`;
  } catch (error) {
    researchMessage.textContent = error.message;
  }
}

async function submitResearch(mode) {
  const technologies = [...researchInput.selectedOptions].map(option => option.value);
  if (!technologies.length) {
    researchMessage.textContent = 'Enter at least one technology ID.';
    return;
  }
  const prompt = mode === 'replace'
    ? 'Replace the current research queue and start the first target? This restarts the Linux runner and changes the deterministic Nauvis base.'
    : 'Append these technologies and restart the Linux runner to continue the deterministic Nauvis research queue?';
  if (!confirm(prompt)) return;
  try {
    const response = await fetch('/api/research', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Action-Token': token},
      body: JSON.stringify({technologies, mode}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Research queue request failed');
    researchMessage.textContent = `${mode === 'replace' ? 'Target set' : 'Research queued'}; runner restart accepted.`;
    [...researchInput.options].forEach(option => { option.selected = false; });
    await refreshResearchQueue();
  } catch (error) {
    researchMessage.textContent = error.message;
  }
}

async function runAction(action) {
  let confirmation = '';
  if (action === 'start_observation_loop') {
    if (!confirm('Start a new 12-hour autonomous loop? This authorizes tested code fixes and commits, and fresh resets of the isolated campaign world.')) return;
  } else if (action === 'resume_observation_loop') {
    if (!confirm('Resume the supervised loop with its original deadline and remaining budget?')) return;
  } else if (action === 'stop_observation_loop') {
    if (!confirm('Stop the supervised loop and its owned work? Check the resulting status before operating the current world.')) return;
  } else if (action === 'fresh_campaign') {
    if (!confirm('Stop the current episode, verify the immutable source save, restore it, and start a new deterministic campaign?')) return;
    confirmation = 'START_FRESH_CAMPAIGN';
  } else if (action === 'resume_runner') {
    if (!confirm('Restart the controller on the CURRENT world without restoring the source save?')) return;
  } else if (action === 'restart_server') {
    if (!confirm('Stop the runner and restart the isolated Linux deterministic server?')) return;
  } else if (action === 'deploy_mod') {
    if (!confirm('Replace the isolated server and Linux GUI mod copies with the current repository Lua code? Restart the GUI client before joining.')) return;
  } else if (action === 'stop_factorio') {
    if (!confirm('Stop the Python runner and terminate the isolated Factorio server? The current save will be preserved.')) return;
    confirmation = 'STOP_FACTORIO_SERVER';
  } else if (action === 'stop_console') {
    if (!confirm('Stop the operations console? Factorio and the runner will keep their current state.')) return;
    confirmation = 'STOP_OPERATIONS_CONSOLE';
  }
  try {
    const response = await fetch(`/api/actions/${action}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Action-Token': token},
      body: JSON.stringify({confirmation}),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Action was rejected');
    actionMessage.textContent = `${action} accepted; follow Dashboard actions for progress.`;
    await refreshStatus();
  } catch (error) {
    actionMessage.textContent = error.message;
  }
}

actionButtons.forEach(button => button.addEventListener('click', () => runAction(button.dataset.action)));
researchSet.addEventListener('click', () => submitResearch('replace'));
researchAppend.addEventListener('click', () => submitResearch('append'));
priorityTabs.forEach(button => button.addEventListener('click', () => {
  selectedPriorityTab = button.dataset.priorityTab;
  priorityTabs.forEach(tab => {
    const selected = tab === button;
    tab.classList.toggle('active', selected);
    tab.setAttribute('aria-selected', String(selected));
  });
  renderPriorities(priorityItems);
}));
['confirm', 'partial', 'wrong', 'missed'].forEach(id => {
  document.querySelector(`#helper-${id}`).addEventListener('click', () => {
    submitHelperFeedback(id === 'missed' ? 'missed_something' : id);
  });
});
['promote', 'demote', 'reject'].forEach(action => {
  document.querySelector(`#helper-${action}`).addEventListener('click', () => {
    submitHelperFeedback('partial', action);
  });
});
document.querySelectorAll('.tab').forEach(button => button.addEventListener('click', () => {
  document.querySelector('.tab.active').classList.remove('active');
  button.classList.add('active');
  selectedLog = button.dataset.log;
  document.querySelector('#console-title').textContent = titles[selectedLog];
  offset = 0;
  clearedAtOffset = false;
  output.textContent = 'Waiting for log output…';
  refreshLog();
}));
document.querySelector('#clear-view').addEventListener('click', () => {
  output.textContent = '';
  clearedAtOffset = true;
});
document.querySelector('#copy-log').addEventListener('click', async () => {
  await navigator.clipboard.writeText(output.textContent);
});
document.querySelector('#copy-last-run').addEventListener('click', async event => {
  const button = event.currentTarget;
  const originalLabel = button.textContent;
  try {
    const response = await fetch('/api/logs/last-run', {cache: 'no-store'});
    const body = await response.text();
    let data;
    try {
      data = JSON.parse(body);
    } catch (parseError) {
      if (response.status === 404 && body.trim() === 'not found') {
        throw new Error('Restart the operations console to load Copy last run, then refresh this page.');
      }
      throw new Error(`Operations console returned an invalid response (HTTP ${response.status}).`);
    }
    if (!response.ok) throw new Error(data.error || 'Last run is unavailable');
    await navigator.clipboard.writeText(data.text);
    button.textContent = 'Copied last run';
  } catch (error) {
    actionMessage.textContent = error.message;
    button.textContent = 'Copy failed';
  } finally {
    setTimeout(() => { button.textContent = originalLabel; }, 1800);
  }
});

refreshStatus();
refreshLog();
refreshPriorities();
setInterval(refreshStatus, 1500);
setInterval(refreshLog, 800);
setInterval(refreshPriorities, 1500);
setInterval(refreshResearchQueue, 1500);
setInterval(refreshResearchOptions, 3000);
refreshResearchQueue();
refreshResearchOptions();
refreshHelper();
refreshLogisticInventory();
setInterval(refreshHelper, 3000);
setInterval(refreshLogisticInventory, 5000);

// Evidence uses local files only; it remains useful when the game is stopped.
let evidenceText = '';
let evidenceLoading = false;
const evidenceMessage = document.querySelector('#evidence-message');
async function refreshEvidence() {
  if (evidenceLoading) return;
  evidenceLoading = true;
  const button = document.querySelector('#evidence-refresh');
  button.disabled = true;
  try {
    const response = await fetch('/api/run-context', {cache: 'no-store'});
    if (!response.ok) throw new Error('Evidence unavailable. Restart the Operations Console if it has not loaded the new endpoints.');
    const data = await response.json();
    evidenceText = data.text;
    document.querySelector('#evidence-packet').textContent = evidenceText;
    document.querySelector('#evidence-freshness').textContent = data.log_modified_at
      ? `Log updated ${new Date(data.log_modified_at * 1000).toLocaleString()}` : 'No runner log yet';
    document.querySelector('#evidence-history-state').textContent = data.history_available
      ? 'History index available · search returns up to five cited runs.'
      : 'No history index yet. The campaign builds it after a run ends.';
    document.querySelector('#evidence-copy').disabled = !evidenceText;
    document.querySelector('#evidence-download').disabled = !evidenceText;
    evidenceMessage.textContent = '';
  } catch (error) {
    evidenceMessage.textContent = error.message;
    document.querySelector('#evidence-freshness').textContent = 'Refresh failed · displayed evidence may be stale';
  } finally { evidenceLoading = false; button.disabled = false; }
}
document.querySelector('#evidence-refresh').addEventListener('click', refreshEvidence);
document.querySelector('#evidence-copy').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(evidenceText); evidenceMessage.textContent = 'Agent handoff copied.'; }
  catch { evidenceMessage.textContent = 'Clipboard unavailable. Use Download handoff instead.'; }
});
document.querySelector('#evidence-download').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([evidenceText], {type: 'text/markdown;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = 'factorio-run-context.md'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.querySelector('#evidence-search').addEventListener('submit', async event => {
  event.preventDefault();
  const button = document.querySelector('#evidence-search-button');
  const results = document.querySelector('#evidence-results');
  button.disabled = true; results.hidden = false; results.textContent = 'Searching run history…';
  try {
    const query = document.querySelector('#evidence-query').value.trim();
    const response = await fetch(`/api/run-history?q=${encodeURIComponent(query)}`, {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'History search failed.');
    results.textContent = data.text;
  } catch (error) { results.textContent = error.message; }
  finally { button.disabled = false; }
});
refreshEvidence();
setInterval(refreshEvidence, 10000);

// Bounded plain text prevents agent notes from becoming executable page content.
let loopLoading = false;
function loopText(value, limit = 12000) {
  return (typeof value === 'string' ? value : JSON.stringify(value ?? [], null, 2)).slice(0, limit);
}
async function refreshObservationLoop() {
  if (loopLoading) return;
  loopLoading = true;
  try {
    const response = await fetch('/api/observation-loop', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Supervisor status unavailable');
    supervisedLoopRunning = Boolean(data.running);
    updateActionAvailability();
    document.querySelector('#loop-phase').textContent = loopText(data.phase || (data.running ? 'Running' : 'Stopped'), 100);
    const deadline = data.deadline ? new Date(typeof data.deadline === 'number' ? data.deadline * 1000 : data.deadline).toLocaleString() : 'Not started';
    document.querySelector('#loop-summary').textContent = `Deadline: ${deadline} · Resolved cycles: ${data.completed_runs ?? 0} · Plastic acceptance: ${data.acceptance_streak ?? 0}/3. ${loopText(data.reason || '', 1000)}`;
    document.querySelector('#loop-milestones').textContent = (data.milestones || []).map(run => {
      const achieved = Object.entries(run.milestones || {}).map(([name, seconds]) => `${name.replaceAll('_', ' ')} +${seconds}s`).join(' · ');
      return `${run.episode_id}: ${run.passed ? 'plastic accepted' : 'not accepted'}\n${achieved || 'No milestone timings recorded'}\n${run.reason || ''}`;
    }).join('\n\n') || 'No milestone evidence yet.';
    const agentStatus = Object.entries(data.agents || {}).map(([role, state]) => `${role}: ${state.status || 'unknown'} · checkpoint ${state.checkpoint ?? '—'}${state.error ? ' · ' + state.error : ''}`).join('\n');
    document.querySelector('#loop-board').textContent = loopText(agentStatus + '\n\n' + (data.notes || 'No observer findings yet.'), 16000);
  } catch (error) {
    document.querySelector('#loop-phase').textContent = 'Unavailable · status may be stale';
    document.querySelector('#loop-summary').textContent = error.message;
  } finally { loopLoading = false; }
}
refreshObservationLoop();
setInterval(refreshObservationLoop, 10000);

// Checkpoint fleet -------------------------------------------------------
// The API returns plain data; all fleet cells and log excerpts are built with
// textContent so runner output can never become executable page markup.
const fleetState = document.querySelector('#fleet-state');
const fleetMessage = document.querySelector('#fleet-message');
const fleetApiStatus = document.querySelector('#fleet-api-status');
const fleetMatrixHead = document.querySelector('#fleet-matrix-head');
const fleetMatrixBody = document.querySelector('#fleet-matrix-body');
const fleetQueue = document.querySelector('#fleet-queue');
const fleetFrontier = document.querySelector('#fleet-frontier-runs');
const fleetPause = document.querySelector('#fleet-pause');
const fleetAutoRun = document.querySelector('#fleet-auto-run');
const fleetCustomForm = document.querySelector('#fleet-custom-form');
const fleetVerifyForm = document.querySelector('#fleet-verify-form');
const fleetCheckpoints = document.querySelector('#fleet-checkpoints');
const fleetHelperCheckpoint = document.querySelector('#fleet-helper-checkpoint');
const fleetVerifyCheckpoint = document.querySelector('#fleet-verify-checkpoint');
const fleetPromoteCheckpoint = document.querySelector('#fleet-promote-checkpoint');
const fleetSourceCheckpoint = document.querySelector('#fleet-source-checkpoint');
const fleetRankingPanel = document.querySelector('#fleet-ranking-panel');
const fleetRanking = document.querySelector('#fleet-ranking');
let fleetSnapshot = null;
let fleetLoading = false;

function fleetValue(value, fallback = '—') {
  return value === null || value === undefined || value === '' ? fallback : String(value);
}

async function fleetFetch(action, payload = {}) {
  const response = await fetch(`/api/actions/${action}`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-Action-Token': token},
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Fleet action was rejected');
  return data;
}

function makeFleetOption(select, value, label) {
  const option = document.createElement('option');
  option.value = value;
  option.textContent = label;
  select.append(option);
}

function renderFleetSelectors(checkpoints) {
  [fleetHelperCheckpoint, fleetVerifyCheckpoint, fleetPromoteCheckpoint].forEach(select => select.replaceChildren());
  fleetSourceCheckpoint.replaceChildren();
  makeFleetOption(fleetSourceCheckpoint, '', 'Use checkpoint defaults');
  fleetCheckpoints.replaceChildren();
  checkpoints.forEach(checkpoint => {
    const id = fleetValue(checkpoint.id);
    const name = fleetValue(checkpoint.name, id);
    const ageWarning = checkpoint.age_status && checkpoint.age_status !== 'current'
      ? ` · ⚠️ ${checkpoint.age_status} input` : '';
    [fleetHelperCheckpoint, fleetVerifyCheckpoint, fleetPromoteCheckpoint].forEach(select => makeFleetOption(select, id, `${id} · ${name}`));
    makeFleetOption(fleetSourceCheckpoint, id, `${id}${checkpoint.starred ? '*' : ''} · ${name}${ageWarning}`);
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox'; input.name = 'checkpoint'; input.value = id; input.checked = true;
    label.append(input, document.createTextNode(id));
    fleetCheckpoints.append(label);
  });
}

function fleetReached(data) {
  const values = Array.isArray(data && data.reached_checkpoints)
    ? data.reached_checkpoints
    : (Array.isArray(data && data.contiguous_reached_checkpoints) ? data.contiguous_reached_checkpoints : []);
  return [...new Set(values.filter(value => typeof value === 'string' && value.trim()).map(value => value.trim()))];
}

function fleetWarnings(data) {
  const values = Array.isArray(data && data.warnings)
    ? data.warnings
    : (typeof (data && data.warnings) === 'string' ? [data.warnings] : []);
  const warnings = values.filter(Boolean).map(value => String(value));
  if ((data && (data.slow_warning || data.warning)) && !warnings.length) {
    warnings.push(fleetValue(data.warning_reason, 'slow progress'));
  }
  return [...new Set(warnings)];
}

function fleetLogTail(data) {
  const raw = data && (data.log_tail ?? data.final_log ?? data.log);
  if (raw === null || raw === undefined || raw === '') return [];
  const lines = Array.isArray(raw) ? raw.map(value => String(value)) : String(raw).split(/\r?\n/);
  return lines.slice(-100);
}

function fleetTicks(data) {
  const explicit = Number(data && (data.ticks ?? data.tick_count ?? data.double_ticks));
  if (Number.isFinite(explicit) && explicit >= 0) return explicit;
  return Math.max(0, fleetReached(data).length - 1);
}

function fleetIsTerminalFailure(data) {
  return ['failed', 'functional_failed', 'timed_out', 'incompatible', 'infrastructure_error', 'cancelled', 'preempted'].includes(data && data.status);
}

function fleetOutcome(data) {
  if (!data) return {label: '—', ticks: 0, warnings: []};
  const ticks = fleetTicks(data);
  const parts = [];
  if (data.status === 'queued') parts.push('QUEUE');
  else if (fleetRunOrigin(data) === 'C0' && ticks > 0) parts.push(Array.from({length: ticks}, () => '✓').join(' '));
  else if (ticks >= 2) parts.push('✓✓');
  else if (ticks >= 1) parts.push('✓');
  else if (!fleetIsTerminalFailure(data)) parts.push('·');
  if (fleetIsTerminalFailure(data)) parts.push('X');
  const warnings = fleetWarnings(data);
  if (warnings.length) parts.push('⚠️');
  return {label: parts.join(' ') || '·', ticks, warnings};
}

function fleetRunOrigin(run) {
  return fleetValue(run && (run.origin_checkpoint || run.start_checkpoint || run.checkpoint), '');
}

function fleetCommitOf(item) {
  return typeof item === 'string' ? item : (item && (item.commit || item.commit_sha || item.commit_code));
}

function fleetRunFor(state, commit, checkpoint) {
  return [...(state.runs || [])].reverse().find(run => fleetCommitOf(run) === commit && fleetRunOrigin(run) === checkpoint)
    || [...(state.queue || [])].reverse().find(run => fleetCommitOf(run) === commit && fleetRunOrigin(run) === checkpoint);
}

function renderFleetMatrix(state) {
  const checkpoints = Array.isArray(state.checkpoints) ? state.checkpoints : [];
  const commits = [...new Set([
    ...(state.commits || []).map(fleetCommitOf),
    ...(state.runs || []).map(fleetCommitOf),
    ...(state.queue || []).map(fleetCommitOf),
    ...(state.ranked_commits || []).map(fleetCommitOf),
  ].filter(Boolean))];
  fleetMatrixHead.replaceChildren(); fleetMatrixBody.replaceChildren();
  const header = document.createElement('tr');
  const first = document.createElement('th'); first.scope = 'col'; first.textContent = 'Checkpoint'; header.append(first);
  commits.forEach(commit => { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = commit; header.append(cell); });
  fleetMatrixHead.append(header);
  if (!commits.length) {
    const row = document.createElement('tr'); const cell = document.createElement('td');
    cell.colSpan = 2; cell.textContent = 'No commit suites queued. Run default to start the fleet.'; row.append(cell); fleetMatrixBody.append(row); return;
  }
  checkpoints.forEach(checkpoint => {
    const row = document.createElement('tr'); const label = document.createElement('th');
    const ageWarning = checkpoint.age_status && checkpoint.age_status !== 'current'
      ? ` · ⚠️ ${checkpoint.age_status} input` : '';
    label.scope = 'row'; label.textContent = `${fleetValue(checkpoint.id)}${checkpoint.starred ? '*' : ''} · ${fleetValue(checkpoint.name)}${ageWarning}`; row.append(label);
    commits.forEach(commit => {
      const data = fleetRunFor(state, commit, checkpoint.id); const cell = document.createElement('td');
      const box = document.createElement('div'); box.className = 'fleet-cell';
      if (data) {
        const status = document.createElement('span'); status.className = 'fleet-status';
        const outcome = fleetOutcome(data);
        status.textContent = outcome.label;
        box.classList.toggle('passed', outcome.ticks > 0); box.classList.toggle('failed', fleetIsTerminalFailure(data));
        box.classList.toggle('warning', outcome.warnings.length > 0);
        const detail = document.createElement('small');
        const reached = fleetReached(data);
        const warningText = outcome.warnings.length ? ` · ${outcome.warnings.join(', ').slice(0, 120)}` : '';
        const reason = data.reason || data.failure_reason;
        detail.textContent = `${fleetValue(data.status, 'unknown')}${reached.length ? ` · through ${reached[reached.length - 1]}` : ''}${reason ? ` · ${fleetValue(reason).slice(0, 120)}` : ''}${warningText}`;
        box.append(status, detail);
        if (data.run_id) {
          const actions = document.createElement('span');
          if (data.status === 'running') {
            const stop = document.createElement('button'); stop.type = 'button'; stop.textContent = 'Stop';
            stop.addEventListener('click', () => runFleetAction('fleet_stop_run', {run_id: data.run_id})); actions.append(stop);
          }
          if (fleetIsTerminalFailure(data) && outcome.ticks < 2 && data.kind === 'middle') {
            const helper = document.createElement('button'); helper.type = 'button'; helper.textContent = 'Restart with helper';
            helper.addEventListener('click', () => runFleetAction('fleet_restart_helper', {run_id: data.run_id})); actions.append(helper);
          }
          if (actions.childNodes.length) box.append(actions);
        }
        const logLines = fleetLogTail(data);
        if (logLines.length) {
          const details = document.createElement('details');
          const summary = document.createElement('summary'); summary.textContent = 'Last 100 log lines';
          const log = document.createElement('pre'); log.textContent = logLines.join('\n');
          details.append(summary, log); box.append(details);
        }
      } else box.append(Object.assign(document.createElement('span'), {className: 'fleet-status', textContent: '—'}));
      cell.append(box); row.append(cell);
    });
    fleetMatrixBody.append(row);
  });
}

function renderFleetQueue(state) {
  fleetQueue.replaceChildren();
  const items = (state.queue || []).filter(item => item.status !== 'cancelled');
  if (!items.length) { fleetQueue.textContent = 'No queued lanes.'; return; }
  items.slice(0, 256).forEach(item => {
    const row = document.createElement('div'); row.className = 'fleet-queue-row';
    const label = document.createElement('span'); label.textContent = `${fleetValue(fleetCommitOf(item))} · ${fleetValue(item.checkpoint)} · ${fleetValue(item.kind, 'run')}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'X'; remove.title = 'Remove from queue';
    remove.addEventListener('click', () => runFleetAction('fleet_remove_queue', {queue_id: item.queue_id}));
    row.append(label, remove); fleetQueue.append(row);
  });
}

function renderFleetFrontier(state) {
  fleetFrontier.replaceChildren();
  const runs = (state.frontier_runs || []).slice(-10).reverse();
  if (!runs.length) { fleetFrontier.textContent = 'No frontier runs recorded.'; return; }
  runs.forEach(run => {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    const outcome = fleetOutcome(run);
    const warnings = outcome.warnings.length ? ` · ⚠️ ${outcome.warnings.join(', ')}` : '';
    summary.textContent = `${fleetValue(run.commit || run.commit_code)} · ${fleetValue(run.start_checkpoint || run.origin_checkpoint, run.checkpoint)} → ${fleetValue(run.furthest_checkpoint)} · ${outcome.label} · ${fleetValue(run.reason, run.status)}${warnings}`;
    const excerpt = document.createElement('pre');
    excerpt.textContent = fleetLogTail(run).join('\n') || 'No final runner log excerpt recorded.';
    details.append(summary, excerpt); fleetFrontier.append(details);
  });
}

function renderFleetRanking(state) {
  const ranked = state.ranked_commits;
  fleetRankingPanel.hidden = !Array.isArray(ranked);
  if (!Array.isArray(ranked)) return;
  fleetRanking.replaceChildren();
  if (!ranked.length) {
    fleetRanking.append(Object.assign(document.createElement('p'), {className: 'rank-empty', textContent: 'No ranked commits recorded.'}));
    return;
  }
  const table = document.createElement('table');
  const head = document.createElement('thead');
  const header = document.createElement('tr');
  ['#', 'Commit', 'Stable prefix', 'Furthest', 'Intermediate X', 'Double ticks', 'Slow warnings', 'Duration'].forEach(label => {
    const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; header.append(cell);
  });
  head.append(header); table.append(head);
  const body = document.createElement('tbody');
  ranked.forEach((entry, index) => {
    const item = typeof entry === 'string' ? {commit: entry} : (entry || {});
    const row = document.createElement('tr');
    const cells = [
      index + 1,
      item.commit || item.commit_sha || item.commit_code,
      item.contiguous_failure_free_prefix ?? item.failure_free_prefix,
      item.furthest_checkpoint ?? item.furthest,
      item.intermediate_failures,
      item.double_ticks,
      item.slow_warnings,
      item.normalized_duration ?? item.duration,
    ];
    cells.forEach(value => { const cell = document.createElement('td'); cell.textContent = fleetValue(value); row.append(cell); });
    body.append(row);
  });
  table.append(body); fleetRanking.append(table);
}

function renderFleet(state) {
  fleetSnapshot = state;
  const settings = state.settings || {};
  fleetState.textContent = `${settings.active_servers || 0}/${settings.active_cap || 8} SERVERS${settings.paused ? ' · PAUSED' : ''}`;
  fleetPause.textContent = settings.paused ? 'Resume scheduling' : 'Pause scheduling';
  fleetAutoRun.checked = settings.auto_run_commits !== false;
  const api = state.api || {};
  const helper = state.helper || {};
  fleetApiStatus.textContent = `API ${fleetValue(api.version, '—')} · ${api.coordinator_connected ? 'coordinator connected' : 'facade / intent-only'} · helper ${helper.available ? 'available' : 'not connected'}`;
    renderFleetSelectors(state.checkpoints || []);
    renderFleetMatrix(state); renderFleetQueue(state); renderFleetFrontier(state); renderFleetRanking(state);
}

async function refreshFleet() {
  if (fleetLoading) return; fleetLoading = true;
  try {
    const response = await fetch('/api/checkpoint-fleet', {cache: 'no-store'}); const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Checkpoint fleet unavailable');
    renderFleet(data); fleetMessage.textContent = '';
  } catch (error) { fleetState.textContent = 'UNAVAILABLE'; fleetMessage.textContent = error.message; }
  finally { fleetLoading = false; }
}

async function runFleetAction(action, payload = {}) {
  try { await fleetFetch(action, payload); fleetMessage.textContent = `${action.replaceAll('_', ' ')} accepted.`; await refreshFleet(); }
  catch (error) { fleetMessage.textContent = error.message; }
}

function fleetPlainField(selector, field, maxLength = 200) {
  const value = document.querySelector(selector).value.trim();
  if (!value) return '';
  if (value.length > maxLength || /[\u0000-\u001f\u007f]/.test(value)) {
    throw new Error(`${field} must be plain text of at most ${maxLength} characters`);
  }
  return value;
}

document.querySelector('#fleet-run-default').addEventListener('click', () => runFleetAction('fleet_run_default', {commit: 'HEAD'}));
document.querySelector('#fleet-restart-latest').addEventListener('click', () => runFleetAction('fleet_restart_latest', {commit: 'HEAD'}));
document.querySelector('#fleet-custom-toggle').addEventListener('click', () => { fleetCustomForm.hidden = !fleetCustomForm.hidden; fleetVerifyForm.hidden = true; });
document.querySelector('#fleet-verify-toggle').addEventListener('click', () => { fleetVerifyForm.hidden = !fleetVerifyForm.hidden; fleetCustomForm.hidden = true; });
fleetPause.addEventListener('click', () => runFleetAction('fleet_pause', {paused: !(fleetSnapshot && fleetSnapshot.settings && fleetSnapshot.settings.paused)}));
fleetAutoRun.addEventListener('change', () => runFleetAction('fleet_auto_run', {auto_run_commits: fleetAutoRun.checked}));
fleetCustomForm.addEventListener('submit', event => {
  event.preventDefault();
  try {
    const rawCommits = document.querySelector('#fleet-commits').value.split(/[\s,]+/).map(value => value.trim()).filter(Boolean);
    const commits = rawCommits.map((value, index) => {
      if (value.length > 160 || /[\u0000-\u001f\u007f]/.test(value)) throw new Error(`commit ${index + 1} must be plain text of at most 160 characters`);
      return value;
    });
    if (!commits.length) throw new Error('Enter at least one commit');
    const checkpoints = [...fleetCheckpoints.querySelectorAll('input:checked')].map(input => input.value);
    if (!checkpoints.length) throw new Error('Select at least one checkpoint');
    const helper_checkpoints = [...fleetHelperCheckpoint.selectedOptions].map(option => option.value);
    const source_checkpoint = fleetSourceCheckpoint.value.trim();
    const generation_id = fleetPlainField('#fleet-generation', 'generation ID');
    const save_id = fleetPlainField('#fleet-save', 'save ID', 200);
    const mission_id = fleetPlainField('#fleet-mission', 'mission ID', 160);
    if ((generation_id || save_id) && !source_checkpoint) {
      throw new Error('Select a source checkpoint when using a one-off generation or save');
    }
    const payload = {commits, checkpoints, helper_checkpoints};
    if (source_checkpoint) payload.source_checkpoint = source_checkpoint;
    if (generation_id) payload.generation_id = generation_id;
    if (save_id) payload.save_id = save_id;
    if (mission_id) payload.mission_id = mission_id;
    runFleetAction('fleet_run_custom', payload);
  } catch (error) { fleetMessage.textContent = error.message; }
});
fleetVerifyForm.addEventListener('submit', event => {
  event.preventDefault(); runFleetAction('fleet_verify_checkpoint', {
    commit: document.querySelector('#fleet-verify-commit').value.trim() || 'HEAD',
    checkpoint: fleetVerifyCheckpoint.value,
    candidate_id: document.querySelector('#fleet-verify-candidate').value.trim(),
  });
});
document.querySelector('#fleet-promote-form').addEventListener('submit', event => {
  event.preventDefault(); runFleetAction('fleet_promote_checkpoint', {
    checkpoint: fleetPromoteCheckpoint.value,
    save_id: document.querySelector('#fleet-promote-save').value.trim(),
    creator_commit: document.querySelector('#fleet-promote-commit').value.trim() || null,
  });
});
refreshFleet();
setInterval(refreshFleet, 5000);
