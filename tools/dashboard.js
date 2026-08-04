// Path: tools/dashboard.js
// Purpose: Poll live Factorio logs and invoke the dashboard's fixed local actions.

const token = document.querySelector('meta[name="action-token"]').content;
const output = document.querySelector('#log-output');
const follow = document.querySelector('#follow-log');
const actionMessage = document.querySelector('#action-message');
const operationState = document.querySelector('#operation-state');
const priorityBody = document.querySelector('#priority-body');
const priorityCount = document.querySelector('#priority-count');
const priorityTabs = [...document.querySelectorAll('[data-priority-tab]')];
const actionButtons = [...document.querySelectorAll('[data-action]')];
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
    setOnline('#game-status', state.server.game);
    setOnline('#rcon-status', state.server.rcon);
    setOnline('#runner-status', state.runner.running, `PID: ${state.runner.pids.join(', ') || 'none'}`);
    const busy = Boolean(state.operation.active);
    operationState.textContent = busy ? `RUNNING · ${state.operation.active}` : 'READY';
    operationState.classList.toggle('busy', busy);
    actionButtons.forEach(button => { button.disabled = busy; });
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

async function runAction(action) {
  let confirmation = '';
  if (action === 'restore_save') {
    if (!confirm('Back up the current dedicated save, restore the original mod_playground.zip, and restart the server?')) return;
    confirmation = 'RESTORE';
  } else if (action === 'full_refresh') {
    if (!confirm('Stop the runner and server, redeploy the mod, restart the server, then restart the runner?')) return;
  } else if (action === 'restart_server') {
    if (!confirm('Stop the runner and restart the dedicated server?')) return;
  } else if (action === 'deploy_mod') {
    if (!confirm('Replace both deployed mod copies with the current repository Lua code?')) return;
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
