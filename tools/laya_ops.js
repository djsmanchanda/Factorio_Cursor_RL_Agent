// Path: tools/laya_ops.js
// Purpose: Poll the Laya Operations Console API and render status, decisions, stock, guidelines and live logs.

const token = document.querySelector('meta[name="action-token"]').content;
const $ = (id) => document.getElementById(id);
let logName = "runner";
let logOffset = 0;

function cell(row, text, cls) {
  const td = document.createElement("td");
  td.textContent = text === null || text === undefined ? "—" : String(text);
  if (cls) td.className = cls;
  row.appendChild(td);
  return td;
}

function fill(body, rows, columns, empty) {
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement("tr");
    const td = cell(tr, empty, "empty-priority");
    td.colSpan = columns;
    body.appendChild(tr);
  }
  rows.forEach((tr) => body.appendChild(tr));
}

function chip(el, label, online, warning) {
  el.textContent = label;
  el.classList.toggle("online", Boolean(online));
  el.classList.toggle("warning", Boolean(warning));
}

async function getJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}

async function refreshStatus() {
  const status = await getJSON("/api/status");
  const op = status.operation;
  $("operation-state").textContent = op.status === "running" ? `${op.name}…` : (op.name ? `${op.name}: ${op.status}` : "Ready");
  $("operation-state").classList.toggle("busy", op.status === "running");
  $("action-message").textContent = op.message;
  const ep = status.episode;
  if (!ep) {
    chip($("game-status"), "Game: none"); chip($("rcon-status"), "RCON"); chip($("runner-status"), "Runner");
    return status;
  }
  $("episode-name").textContent = ep.name;
  $("game-address").textContent = ep.game_address;
  chip($("game-status"), `Game: ${ep.game}`, ep.game === "running");
  chip($("rcon-status"), `RCON: ${ep.rcon}`, ep.rcon === "listening");
  const stale = ep.runner === "running" && ep.heartbeat_age_s !== null && ep.heartbeat_age_s > 30;
  chip($("runner-status"), `Runner: ${ep.runner}${stale ? " (stale)" : ""}`, ep.runner === "running" && !stale, stale);
  const hb = ep.heartbeat;
  $("heartbeat").textContent = ep.runner === "running" && hb
    ? `PID ${ep.runner_pid} · heartbeat ${ep.heartbeat_age_s}s ago · ${hb.elapsed_s}s elapsed`
    : (ep.last_summary ? `Last run: ${ep.last_summary.stop} after ${ep.last_summary.decisions} decisions, ${ep.last_summary.failures} failures` : "No heartbeat.");
  return status;
}

function renderLadder(phases, latest) {
  const ladder = $("phase-ladder");
  ladder.replaceChildren();
  const reached = new Set(latest.reached || []);
  const current = latest.phase ? latest.phase.phase_id : null;
  phases.forEach((p) => {
    const li = document.createElement("li");
    li.textContent = `${p.phase_id} ${p.name}`;
    if (reached.has(p.phase_id)) li.className = "reached";
    else if (p.phase_id === current) li.className = "current";
    ladder.appendChild(li);
  });
  if (latest.phase) {
    $("phase-state").textContent = `Current: ${latest.phase.phase_id}`;
    $("phase-targets").textContent = "Targets: " + Object.entries(latest.phase.targets).map(([k, v]) => `${k} ${v}/s`).join(", ");
  }
}

function renderLatest(latest) {
  if (!latest.board) return;
  $("board-tick").textContent = `tick ${latest.tick} · decision ${latest.index}`;
  fill($("board-body"), latest.board.needs.map((n) => {
    const tr = document.createElement("tr");
    cell(tr, n.item);
    cell(tr, n.condition, `cond-${n.condition}`);
    cell(tr, n.required); cell(tr, n.measured); cell(tr, n.capacity); cell(tr, n.machines); cell(tr, n.stock);
    cell(tr, (n.short_inputs || []).join(", ")); cell(tr, n.upgrade);
    return tr;
  }), 9, "No needs.");
  $("bottleneck").textContent = latest.board.bottleneck.length ? latest.board.bottleneck.join("  |  ") : "none";
  const blocked = Object.entries(latest.blocked || {});
  $("blocked").textContent = blocked.length
    ? blocked.map(([need, reasons]) => `${need} (${Object.entries(reasons).map(([r, c]) => `${r}×${c}`).join(", ")})`).join("; ")
    : "none";
  const choice = latest.choice || {};
  $("last-choice").textContent = `[${choice.need_id}] ${choice.summary} · p=${Number(choice.probability || 0).toFixed(3)} · authority ${latest.authority}`;
  $("menu").textContent = Object.entries(latest.menu || {}).map(([need, opts]) => `${need}\n  - ${opts.join("\n  - ")}`).join("\n");
  const stock = Object.entries(latest.stock || {}).filter(([, v]) => v > 0);
  $("stock-count").textContent = `${stock.length} items`;
  fill($("stock-body"), stock.map(([item, count]) => {
    const tr = document.createElement("tr"); cell(tr, item); cell(tr, count); return tr;
  }), 2, "Empty.");
  const plan = latest.site_plan;
  $("plan-state").textContent = plan ? (plan.mall_site ? `Mall at (${plan.mall_site.join(", ")})` : "Mall unzoned") : "Unplanned";
  fill($("plan-body"), (plan ? plan.zones : []).map((z) => {
    const tr = document.createElement("tr");
    cell(tr, z.zone_id); cell(tr, z.purpose); cell(tr, z.rect.map((v) => Math.round(v)).join(", "));
    return tr;
  }), 3, "No site plan yet.");
  const list = $("guidelines");
  list.replaceChildren();
  (latest.guidelines || []).forEach((g) => {
    const li = document.createElement("li");
    li.textContent = `${g.kind.toUpperCase()} ${g.rule_id}: ${g.statement}`;
    if (g.kind === "hard") li.className = "hard";
    list.appendChild(li);
  });
}

async function refreshDecisions() {
  const rows = await getJSON("/api/decisions?limit=60");
  const decisions = rows.filter((r) => r.index !== undefined && !r.event);
  $("decision-count").textContent = `${decisions.length} shown`;
  fill($("decision-body"), rows.slice().reverse().map((r) => {
    const tr = document.createElement("tr");
    if (r.event === "failure") {
      tr.className = "row-failure";
      cell(tr, r.index); cell(tr, r.tick); cell(tr, r.phase); cell(tr, "FAILURE");
      const td = cell(tr, `${r.error_type}: ${r.error}`); td.colSpan = 4;
    } else if (r.event === "phase_reached") {
      tr.className = "row-phase";
      cell(tr, ""); cell(tr, r.tick); cell(tr, r.phase); const td = cell(tr, "PHASE REACHED"); td.colSpan = 5;
    } else if (r.event === "summary") {
      cell(tr, ""); cell(tr, ""); cell(tr, ""); cell(tr, "RUN SUMMARY");
      const td = cell(tr, `stop=${r.stop} decisions=${r.decisions} failures=${r.failures}`); td.colSpan = 4;
    } else {
      cell(tr, r.index); cell(tr, r.tick); cell(tr, r.phase); cell(tr, r.need); cell(tr, r.summary);
      cell(tr, Number(r.p).toFixed(3)); cell(tr, r.status);
      cell(tr, r.measured_per_s ? Object.entries(r.measured_per_s).map(([k, v]) => `${k}=${v}`).join(" ") : "");
    }
    return tr;
  }), 8, "No decisions yet.");
}

async function refreshLog() {
  const data = await getJSON(`/api/logs?name=${encodeURIComponent(logName)}&offset=${logOffset}`);
  const out = $("log-output");
  if (logOffset === 0) out.textContent = data.truncated ? "… (showing the newest part)\n" : "";
  if (data.text) out.textContent += data.text;
  if (data.offset < logOffset) out.textContent = data.text;
  logOffset = data.offset;
  if ($("follow-log").checked) out.scrollTop = out.scrollHeight;
}

async function tick() {
  try {
    const status = await refreshStatus();
    const latest = await getJSON("/api/latest");
    renderLadder(status.phases, latest);
    renderLatest(latest);
    await refreshDecisions();
  } catch (error) {
    $("action-message").textContent = `Console update failed: ${error.message}`;
  }
}

document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t === tab));
  logName = tab.dataset.log; logOffset = 0;
  $("console-title").textContent = tab.textContent;
  refreshLog();
}));
$("clear-view").addEventListener("click", () => { $("log-output").textContent = ""; });
$("copy-log").addEventListener("click", () => navigator.clipboard.writeText($("log-output").textContent));

document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", async () => {
  const action = button.dataset.action;
  const body = {};
  if (button.dataset.confirm) {
    if (!window.confirm(`${button.querySelector("strong").textContent}?`)) return;
    body.confirmation = button.dataset.confirm;
  }
  const response = await fetch(`/api/actions/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-Action-Token": token }, body: JSON.stringify(body),
  });
  const result = await response.json();
  $("action-message").textContent = response.ok ? `${action} accepted.` : result.error;
  tick();
}));

tick();
refreshLog();
setInterval(tick, 3000);
setInterval(refreshLog, 2000);
