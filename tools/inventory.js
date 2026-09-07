// Path: tools/inventory.js
// Purpose: Render raw per-run logistic inventory snapshots without chart dependencies.

const itemSelect = document.querySelector('#inventory-item');
const runPicker = document.querySelector('#run-picker');
const chart = document.querySelector('#inventory-chart');
const chartTitle = document.querySelector('#chart-title');
const chartLegend = document.querySelector('#chart-legend');
const status = document.querySelector('#inventory-status');
const message = document.querySelector('#inventory-message');
const sampleSummary = document.querySelector('#sample-summary');
const colors = ['#e8872f', '#71bf72', '#69b9d9', '#d7b34a', '#c080d7'];
let history = {runs: []};
let selectedRuns = new Set();

function element(name, attributes = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, String(value)));
  return node;
}

function countItems(runs) {
  const names = new Set();
  runs.forEach(run => run.samples.forEach(sample => Object.keys(sample.items || {}).forEach(name => names.add(name))));
  return [...names].sort((left, right) => left.localeCompare(right));
}

function formatRun(run) {
  const date = new Date(run.started_at);
  const time = Number.isNaN(date.valueOf()) ? run.id : date.toLocaleString();
  return `${time} · ${run.samples.length} sample${run.samples.length === 1 ? '' : 's'}`;
}

function renderRunPicker() {
  runPicker.replaceChildren();
  history.runs.slice().reverse().forEach((run, index) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `run${selectedRuns.has(run.id) ? ' selected' : ''}`;
    button.textContent = index === 0 ? `CURRENT / NEWEST · ${formatRun(run)}` : formatRun(run);
    const detail = document.createElement('small');
    detail.textContent = run.label || 'deterministic runner';
    button.append(detail);
    button.addEventListener('click', () => {
      if (selectedRuns.has(run.id) && selectedRuns.size > 1) selectedRuns.delete(run.id);
      else selectedRuns.add(run.id);
      renderRunPicker();
      renderChart();
    });
    runPicker.append(button);
  });
}

function renderItemChoices() {
  const items = countItems(history.runs);
  const prior = itemSelect.value;
  itemSelect.replaceChildren();
  if (!items.length) {
    itemSelect.append(Object.assign(document.createElement('option'), {textContent: 'No inventory observations yet'}));
    itemSelect.disabled = true;
    return;
  }
  items.forEach(item => {
    const option = document.createElement('option');
    option.value = item;
    option.textContent = item;
    itemSelect.append(option);
  });
  itemSelect.disabled = false;
  itemSelect.value = items.includes(prior) ? prior : items[0];
}

function renderChart() {
  const selected = history.runs.filter(run => selectedRuns.has(run.id));
  const item = itemSelect.value;
  chart.replaceChildren();
  chartLegend.replaceChildren();
  if (!selected.length || !item) {
    chart.append(element('text', {x: 450, y: 210, 'text-anchor': 'middle', class: 'chart-empty'}));
    chart.lastChild.textContent = 'Waiting for an active run and inventory observations.';
    sampleSummary.textContent = '0 SAMPLES';
    return;
  }
  const width = 900, height = 420, margin = {top: 24, right: 26, bottom: 54, left: 82};
  const points = selected.flatMap(run => run.samples.map(sample => ({
    x: Number(sample.elapsed_seconds) || 0,
    y: Number(sample.items?.[item]) || 0,
  })));
  const maxX = Math.max(1, ...points.map(point => point.x));
  const maxY = Math.max(1, ...points.map(point => point.y));
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const x = value => margin.left + value / maxX * chartWidth;
  const y = value => margin.top + chartHeight - value / maxY * chartHeight;
  for (let index = 0; index <= 4; index += 1) {
    const value = index / 4 * maxY;
    chart.append(element('line', {x1: margin.left, x2: width - margin.right, y1: y(value), y2: y(value), class: 'chart-grid'}));
    const label = element('text', {x: margin.left - 10, y: y(value) + 4, 'text-anchor': 'end', class: 'axis-label'});
    label.textContent = Math.round(value).toLocaleString(); chart.append(label);
  }
  chart.append(element('line', {x1: margin.left, x2: width - margin.right, y1: y(0), y2: y(0), class: 'chart-axis'}));
  chart.append(element('line', {x1: margin.left, x2: margin.left, y1: margin.top, y2: y(0), class: 'chart-axis'}));
  [0, .25, .5, .75, 1].forEach(fraction => {
    const value = Math.round(maxX * fraction);
    const label = element('text', {x: x(value), y: height - 20, 'text-anchor': 'middle', class: 'axis-label'});
    label.textContent = `${value}s`; chart.append(label);
  });
  selected.forEach((run, index) => {
    const color = colors[index % colors.length];
    const path = run.samples.map((sample, pointIndex) => `${pointIndex ? 'L' : 'M'}${x(Number(sample.elapsed_seconds) || 0)},${y(Number(sample.items?.[item]) || 0)}`).join(' ');
    if (path) chart.append(element('path', {d: path, fill: 'none', stroke: color, 'stroke-width': 2.5, 'stroke-linejoin': 'round', 'stroke-linecap': 'round'}));
    const legendItem = document.createElement('span'); legendItem.className = 'legend-item';
    const swatch = document.createElement('i'); swatch.className = 'legend-swatch'; swatch.style.backgroundColor = color;
    const label = document.createElement('span'); label.textContent = formatRun(run);
    legendItem.append(swatch, label); chartLegend.append(legendItem);
  });
  chartTitle.textContent = `${item} available over run time`;
  sampleSummary.textContent = `${points.length} SAMPLE${points.length === 1 ? '' : 'S'}`;
}

function render(data) {
  history = {runs: Array.isArray(data.runs) ? data.runs : []};
  const ids = new Set(history.runs.map(run => run.id));
  selectedRuns = new Set([...selectedRuns].filter(id => ids.has(id)));
  if (!selectedRuns.size && history.runs.length) selectedRuns.add(history.runs.at(-1).id);
  renderItemChoices(); renderRunPicker(); renderChart();
  const samples = history.runs.reduce((total, run) => total + run.samples.length, 0);
  status.textContent = history.runs.length ? `${history.runs.length} / 5 RUNS RETAINED` : 'WAITING FOR A RUN';
  message.textContent = data.live_error || (samples ? 'The newest inventory point is recorded on each live report; duplicate game ticks are ignored.' : 'The runner has not produced an inventory observation yet.');
}

async function refresh() {
  try {
    const response = await fetch('/api/inventory-history', {cache: 'no-store'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Inventory history request failed');
    render(data);
  } catch (error) { status.textContent = 'UNAVAILABLE'; message.textContent = error.message; }
}

itemSelect.addEventListener('change', renderChart);
refresh(); setInterval(refresh, 5000);
