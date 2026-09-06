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
    [...actionButtons, ...researchButtons].forEach(button => { button.disabled = busy; });
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
  if (action === 'fresh_campaign') {
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
