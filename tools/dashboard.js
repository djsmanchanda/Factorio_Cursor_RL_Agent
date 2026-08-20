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
      output.textContent += data.text;
      if (follow.checked) output.scrollTop = output.scrollHeight;
    }
    offset = data.offset;
    clearedAtOffset = true;
  } catch (error) {
    output.textContent += `\n[dashboard] ${error.message}\n`;
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
  if (action === 'restore_save') {
    if (!confirm('Back up the isolated deterministic save, restore the configured source save, and restart the Linux server?')) return;
    confirmation = 'RESTORE';
  } else if (action === 'full_refresh') {
    if (!confirm('Stop the runner and server, redeploy the mod to the server and GUI, restart Factorio, then restart the runner?')) return;
  } else if (action === 'restart_server') {
    if (!confirm('Stop the runner and restart the isolated Linux deterministic server?')) return;
  } else if (action === 'deploy_mod') {
    if (!confirm('Replace the isolated server and Linux GUI mod copies with the current repository Lua code? Restart the GUI client before joining.')) return;
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
