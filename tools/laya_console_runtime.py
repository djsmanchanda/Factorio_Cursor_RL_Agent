# Path: tools/laya_console_runtime.py
# Purpose: Detect, derive status for, and control the Laya overnight training driver for the operations console.

from __future__ import annotations

import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.rcon_client import RconClient, RconError

DRIVER_SCRIPT = REPO_ROOT / "scripts" / "laya_overnight.sh"
MANAGE_WORKER = REPO_ROOT / "scripts" / "manage_linux_training_worker.sh"
OUT_DIR = REPO_ROOT / "data" / "laya-pi"
BATCHES_DIR = REPO_ROOT / "data" / "laya-exploration" / "batches"
CHECKPOINTS_DIR = REPO_ROOT / "data" / "laya-checkpoints"
TRAINING_STATE_ROOT = (
    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    / "factorio-rl" / "training"
)

DEFAULT_INSTANCES = "01 02 03 04 06 07 08 09 10 11 12 13".split()
DEFAULT_L1_SEED = 5101
DEFAULT_L2_SEED = 6001
DEFAULT_SEEDS_PER_ROUND = 36
STALE_LABEL_MINUTES = 45
PHASES = (
    "starting", "evaluating baseline", "labelling", "training features",
    "evaluating", "fine-tuning laya", "done",
)


class OperationError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Process discovery. All functions take an optional injected process/environ
# source so status derivation is testable without a real /proc filesystem.
# ---------------------------------------------------------------------------

def list_processes() -> list[tuple[int, list[str]]]:
    """Return (pid, cmdline) for every readable /proc process; best-effort."""
    processes: list[tuple[int, list[str]]] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return processes
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = [part for part in raw.decode("utf-8", errors="replace").split("\x00") if part]
        if cmdline:
            processes.append((int(entry.name), cmdline))
    return processes


def find_driver_processes(processes: list[tuple[int, list[str]]] | None = None) -> list[dict]:
    """Find every running scripts/laya_overnight.sh driver. Two runs (e.g. the
    live overnight run and a separate test run on other workers) can be active
    at once, so callers that act on a specific run must disambiguate by name
    rather than assume there is only one."""
    processes = list_processes() if processes is None else processes
    matches = []
    for pid, cmdline in processes:
        joined = " ".join(cmdline)
        if "laya_overnight.sh" in joined and "laya_console" not in joined:
            matches.append({"pid": pid, "cmdline": cmdline})
    return matches


def find_driver_process(processes: list[tuple[int, list[str]]] | None = None) -> dict | None:
    """Find any running driver; used only to gate starting a new one."""
    matches = find_driver_processes(processes)
    return matches[0] if matches else None


def read_environ(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for part in raw.decode("utf-8", errors="replace").split("\x00"):
        if "=" in part:
            key, _, value = part.partition("=")
            env[key] = value
    return env


def driver_config_from_environ(environ: dict[str, str]) -> dict[str, Any]:
    """Pure derivation of the driver's run knobs from its inherited environment."""
    instances = environ.get("LAYA_INSTANCES", " ".join(DEFAULT_INSTANCES)).split()
    return {
        "run_name": environ.get("LAYA_RUN_NAME") or "overnight",
        "prefix": environ.get("LAYA_PREFIX") or "ov",
        "instances": instances,
        "procs": int(environ.get("LAYA_PROCS", "3") or 3),
        "speed": float(environ.get("LAYA_SPEED", "32") or 32),
        "seeds_per_round": int(environ.get("LAYA_SEEDS_PER_ROUND", str(DEFAULT_SEEDS_PER_ROUND)) or DEFAULT_SEEDS_PER_ROUND),
    }


def process_group_id(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


# ---------------------------------------------------------------------------
# summary.log parsing. Every regex matches one log() line the driver writes;
# see scripts/laya_overnight.sh for the exact formats.
# ---------------------------------------------------------------------------

TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TS = r"\[(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]"

START_LINE_RE = re.compile(
    _TS + r" start: (?P<hours>[\d.]+) h, code (?P<code>\S+), "
    r"instances (?P<instances>.+), procs (?P<procs>\d+), speed (?P<speed>[\d.]+)$"
)
ROUND_START_RE = re.compile(
    _TS + r" (?P<prefix>[A-Za-z0-9_.-]+)-r(?P<round>\d+): level (?P<level>\d+) "
    r"seeds (?P<seed>\d+)\+(?P<count>\d+) continuation (?P<cont>\S+)$"
)
ROUND_DONE_RE = re.compile(
    _TS + r" (?P<prefix>[A-Za-z0-9_.-]+)-r(?P<round>\d+): done in (?P<mins>\d+) min, "
    r"(?P<left>\d+) min left$"
)
NO_FEATURES_RE = re.compile(_TS + r" (?P<prefix>[A-Za-z0-9_.-]+)-r(?P<round>\d+): no features model produced")
FINETUNE_DATASET_RE = re.compile(_TS + r" laya (?P<tag>\S+): dataset from (?P<files>\d+) case files$")
FINETUNE_TRAINED_RE = re.compile(_TS + r" laya (?P<tag>\S+): trained -> (?P<ckpt>\S+)$")
FINETUNE_FAILED_RE = re.compile(_TS + r" laya (?P<tag>\S+): (?:dataset build|training) failed")
FINAL_LINE_RE = re.compile(_TS + r" final features policy: (?P<policy>\S+)$")
DONE_LINE_RE = re.compile(_TS + r" done; training workers stopped$")
BASELINE_BATCH_RE = re.compile(r'^"?(?P<prefix>[A-Za-z0-9_.-]+)-eval-rollout-L(?P<level>\d+)$')


def _parse_ts(value: str) -> datetime:
    return datetime.strptime(value, TIMESTAMP_FORMAT)


def read_text_tail(path: Path, max_bytes: int = 2_000_000) -> str:
    if not path.exists():
        return ""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        return handle.read().decode("utf-8", errors="replace")


def latest_session_lines(lines: list[str]) -> tuple[list[str], re.Match | None]:
    """Return the lines from the most recent 'start:' line onward (this invocation)."""
    last_index = None
    last_match = None
    for index, raw in enumerate(lines):
        match = START_LINE_RE.match(raw.strip())
        if match:
            last_index, last_match = index, match
    if last_index is None:
        return lines, None
    return lines[last_index:], last_match


def parse_batch_lines(lines: list[str]) -> list[dict]:
    """Parse every `{"batch": ...}` JSON comparison line, in file order."""
    rows = []
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith('{"batch"'):
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
    return rows


def detect_prefix(session_lines: list[str], batches: list[dict], default: str = "ov") -> str:
    for raw in session_lines:
        match = ROUND_START_RE.match(raw.strip())
        if match:
            return match.group("prefix")
    for row in batches:
        match = BASELINE_BATCH_RE.match(str(row.get("batch", "")))
        if match:
            return match.group("prefix")
    return default


def parse_rounds(session_lines: list[str], prefix: str) -> list[dict]:
    """One dict per round the current session has started, in round order."""
    rounds: dict[int, dict] = {}
    for raw in session_lines:
        stripped = raw.strip()
        match = ROUND_START_RE.match(stripped)
        if match and match.group("prefix") == prefix:
            n = int(match.group("round"))
            rounds[n] = {
                "round": n, "level": int(match.group("level")), "seed": int(match.group("seed")),
                "count": int(match.group("count")), "continuation": match.group("cont"),
                "started_at": match.group("ts"), "done_at": None, "done_minutes": None,
                "no_features": False,
            }
            continue
        match = ROUND_DONE_RE.match(stripped)
        if match and match.group("prefix") == prefix:
            n = int(match.group("round"))
            if n in rounds:
                rounds[n]["done_at"] = match.group("ts")
                rounds[n]["done_minutes"] = int(match.group("mins"))
            continue
        match = NO_FEATURES_RE.match(stripped)
        if match and match.group("prefix") == prefix:
            n = int(match.group("round"))
            if n in rounds:
                rounds[n]["no_features"] = True
    return [rounds[n] for n in sorted(rounds)]


def parse_finetunes(session_lines: list[str]) -> list[dict]:
    """One dict per fine-tune tag the current session has started, in order started."""
    tags: dict[str, dict] = {}
    order: list[str] = []
    for raw in session_lines:
        stripped = raw.strip()
        match = FINETUNE_DATASET_RE.match(stripped)
        if match:
            tag = match.group("tag")
            tags[tag] = {"tag": tag, "started_at": match.group("ts"), "case_files": int(match.group("files")),
                         "trained_at": None, "checkpoint": None, "failed": False}
            order.append(tag)
            continue
        match = FINETUNE_TRAINED_RE.match(stripped)
        if match and match.group("tag") in tags:
            tags[match.group("tag")]["trained_at"] = match.group("ts")
            tags[match.group("tag")]["checkpoint"] = match.group("ckpt")
            continue
        match = FINETUNE_FAILED_RE.match(stripped)
        if match and match.group("tag") in tags:
            tags[match.group("tag")]["failed"] = True
    return [tags[tag] for tag in order]


# ---------------------------------------------------------------------------
# Phase derivation.
# ---------------------------------------------------------------------------

def _round_batch_exists(batches_dir: Path, prefix: str, round_num: int, level: int) -> bool:
    return (batches_dir / f"eval-{prefix}-r{round_num}-L{level}.jsonl").exists()


def _finetune_eval_exists(batches_dir: Path, prefix: str, tag: str, level: int) -> bool:
    return (batches_dir / f"{prefix}-eval-laya-{tag}-L{level}.jsonl").exists()


def derive_phase(
    *, out_dir: Path, batches_dir: Path, prefix: str,
    rounds: list[dict], finetunes: list[dict], finished: bool,
) -> dict:
    """Return {"phase", "round", "level", "detail"} for the current session."""
    if finished:
        return {"phase": "done", "round": rounds[-1]["round"] if rounds else None, "level": None,
                "detail": "run finished and workers were stopped"}

    # An open fine-tune (including the optional pre-round-1 LAYA_FINETUNE_FIRST
    # pass) always reflects the newest activity, since the driver logs each of
    # its steps immediately instead of buffering them like a round.
    if finetunes:
        tag = finetunes[-1]
        if tag["failed"]:
            pass  # fall through to round/baseline state below
        elif tag["trained_at"] is None:
            return {"phase": "fine-tuning laya", "round": None, "level": None,
                    "detail": f"training checkpoint for {tag['tag']}"}
        elif not (_finetune_eval_exists(batches_dir, prefix, tag["tag"], 1)
                  and _finetune_eval_exists(batches_dir, prefix, tag["tag"], 2)):
            return {"phase": "evaluating", "round": None, "level": None,
                    "detail": f"evaluating fine-tuned checkpoint {tag['tag']}"}

    if not rounds:
        return {"phase": "evaluating baseline", "round": 0, "level": None,
                "detail": "scripted-rollout baseline evaluation"}

    current = rounds[-1]
    if current["done_at"] is not None:
        return {"phase": "done", "round": current["round"], "level": current["level"],
                "detail": "round complete; healing workers before the next round"}
    if current["no_features"]:
        return {"phase": "done", "round": current["round"], "level": current["level"],
                "detail": "round produced no features model"}

    round_num, level = current["round"], current["level"]
    prefix_round = f"{prefix}-r{round_num}"
    cases_file = out_dir / f"{prefix_round}-cases.jsonl"
    features_file = out_dir / f"features-{prefix_round}.json"
    if not cases_file.exists():
        return {"phase": "labelling", "round": round_num, "level": level, "detail": "counterfactual labelling"}
    if not features_file.exists():
        return {"phase": "training features", "round": round_num, "level": level, "detail": "training features policy"}
    if not _round_batch_exists(batches_dir, prefix, round_num, level):
        return {"phase": "evaluating", "round": round_num, "level": level, "detail": "held-out evaluation"}
    return {"phase": "done", "round": round_num, "level": level, "detail": "round complete"}


def labeller_progress(out_dir: Path, prefix: str, round_num: int, procs: int) -> list[dict]:
    """Per-process labeller progress for the round currently being labelled."""
    now = time.time()
    rows = []
    for p in range(procs):
        log_path = out_dir / f"{prefix}-r{round_num}-p{p}.log"
        entry = {"process": p, "labels": 0, "finished": False, "minutes_since_last_write": None, "stale": False}
        if log_path.exists():
            text = log_path.read_text(encoding="utf-8", errors="replace")
            entry["labels"] = text.count("faithful=")
            entry["finished"] = '"labels":' in text.splitlines()[-1] if text.splitlines() else False
            age_minutes = (now - log_path.stat().st_mtime) / 60.0
            entry["minutes_since_last_write"] = round(age_minutes, 1)
            entry["stale"] = (not entry["finished"]) and age_minutes > STALE_LABEL_MINUTES
        rows.append(entry)
    return rows


# ---------------------------------------------------------------------------
# Worker / GPU / host status.
# ---------------------------------------------------------------------------

def worker_status(instance: str) -> dict:
    """Parse `manage_linux_training_worker.sh status --only N` for one instance."""
    n = int(instance)
    try:
        completed = subprocess.run(
            [str(MANAGE_WORKER), "status", "--worker-count", "20", "--only", str(n)],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"instance": instance, "running": False, "detail": f"status check failed: {exc}"}
    text = (completed.stdout or "").strip()
    running = "running native" in text
    return {"instance": instance, "running": running, "detail": text or completed.stderr.strip()}


def gpu_status() -> dict | None:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    util, temp, used, total = (part.strip() for part in completed.stdout.strip().splitlines()[0].split(","))
    return {"utilization_percent": float(util), "temperature_c": float(temp),
            "memory_used_mb": float(used), "memory_total_mb": float(total)}


def host_memory_status() -> dict | None:
    try:
        text = Path("/proc/meminfo").read_text(encoding="utf-8")
    except OSError:
        return None
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            values[key] = int(rest.strip().split()[0]) // 1024
    if "MemTotal" not in values:
        return None
    used = values["MemTotal"] - values.get("MemAvailable", 0)
    return {"total_mb": values["MemTotal"], "available_mb": values.get("MemAvailable", 0), "used_mb": used}


# ---------------------------------------------------------------------------
# Status assembly.
# ---------------------------------------------------------------------------

def _configured_drivers(drivers: list[dict] | None = None) -> list[dict]:
    drivers = find_driver_processes() if drivers is None else drivers
    return [{**d, "config": driver_config_from_environ(read_environ(d["pid"]))} for d in drivers]


def resolve_run_name(requested: str | None, *, driver: dict | None = None) -> tuple[str, dict | None]:
    """Pick which run's data to show. A specific request is matched against
    every active driver (there may be more than one); with no request, an
    unambiguous single active driver wins, otherwise fall back to whichever
    run directory was written to most recently."""
    configured = _configured_drivers([driver] if driver is not None else None)
    if requested is not None:
        for entry in configured:
            if entry["config"]["run_name"] == requested:
                return requested, entry
        return requested, None
    if configured:
        if len(configured) == 1:
            return configured[0]["config"]["run_name"], configured[0]

        def _mtime(entry: dict) -> float:
            path = OUT_DIR / entry["config"]["run_name"] / "summary.log"
            return path.stat().st_mtime if path.exists() else 0.0

        newest = max(configured, key=_mtime)
        return newest["config"]["run_name"], newest
    candidates = sorted(OUT_DIR.glob("*/summary.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0].parent.name, None
    return "overnight", None


def build_status(requested_run: str | None = None, *, driver: dict | None = None) -> dict:
    run_name, driver = resolve_run_name(requested_run, driver=driver)
    run_dir = OUT_DIR / run_name
    summary_path = run_dir / "summary.log"
    lines = read_text_tail(summary_path).splitlines()
    session_lines, start_match = latest_session_lines(lines)
    batches = parse_batch_lines(session_lines)
    default_prefix = driver["config"]["prefix"] if driver and driver["config"]["run_name"] == run_name else "ov"
    prefix = detect_prefix(session_lines, batches, default_prefix)
    rounds = parse_rounds(session_lines, prefix)
    finetunes = parse_finetunes(session_lines)
    finished = any(DONE_LINE_RE.match(raw.strip()) for raw in session_lines)
    stop_requested = (run_dir / "stop-requested").exists()

    active = bool(driver) and driver["config"]["run_name"] == run_name
    if active:
        config = driver["config"]
        pid = driver["pid"]
    else:
        pid = None
        config = {
            "run_name": run_name, "prefix": prefix, "instances": [], "procs": None,
            "speed": None, "seeds_per_round": DEFAULT_SEEDS_PER_ROUND,
        }
    if start_match:
        hours = float(start_match.group("hours"))
        start_time = _parse_ts(start_match.group("ts"))
        deadline = start_time + timedelta(seconds=hours * 3600)
        config = {
            **config,
            "hours": hours, "code": start_match.group("code"),
            "instances": start_match.group("instances").split(),
            "procs": int(start_match.group("procs")), "speed": float(start_match.group("speed")),
            "start_time": start_time.isoformat(), "deadline": deadline.isoformat(),
        }

    phase = derive_phase(
        out_dir=OUT_DIR, batches_dir=BATCHES_DIR, prefix=prefix,
        rounds=rounds, finetunes=finetunes, finished=finished,
    ) if (start_match or rounds or finetunes) else {
        "phase": "starting", "round": None, "level": None, "detail": "waiting for the driver to log its first line",
    }

    labellers: list[dict] = []
    if phase["phase"] == "labelling" and phase["round"] is not None:
        procs = config.get("procs") or 3
        labellers = labeller_progress(OUT_DIR, prefix, phase["round"], procs)

    workers = [worker_status(instance) for instance in config.get("instances") or []] if active else []

    return {
        "active": active,
        "pid": pid,
        "run_name": run_name,
        "prefix": prefix,
        "run_dir": str(run_dir),
        "config": config,
        "rounds": rounds,
        "finetunes": finetunes,
        "phase": phase,
        "labellers": labellers,
        "workers": workers,
        "stop_requested": stop_requested,
        "finished": finished,
        "gpu": gpu_status(),
        "host_memory": host_memory_status(),
    }


# ---------------------------------------------------------------------------
# Results panel.
# ---------------------------------------------------------------------------

def _batch_level(name: str) -> int | None:
    match = re.search(r"-L(\d+)$", name)
    return int(match.group(1)) if match else None


def _batch_kind(name: str) -> str:
    if "eval-rollout-L" in name or name.endswith(tuple(f"-rollout-L{n}" for n in (1, 2))):
        return "rollout"
    if "eval-laya-" in name:
        return "laya"
    if "eval-final-" in name:
        return "final-features"
    if re.search(r"^eval-.+-r\d+-L\d+$", name) or re.search(r"-r\d+-L\d+$", name):
        return "features"
    return "other"


def build_results(run_name: str | None = None) -> dict:
    run_name, _ = resolve_run_name(run_name)
    summary_path = OUT_DIR / run_name / "summary.log"
    lines = read_text_tail(summary_path).splitlines()
    rows = parse_batch_lines(lines)
    for row in rows:
        row["level"] = _batch_level(str(row.get("batch", "")))
        row["kind"] = _batch_kind(str(row.get("batch", "")))

    best_features: dict[int, dict] = {}
    best_laya: dict[int, dict] = {}
    trend: dict[int, list[dict]] = {1: [], 2: []}
    for row in rows:
        level = row.get("level")
        if level not in (1, 2) or row.get("median_cost_ticks") is None:
            continue
        if row["kind"] in ("features", "final-features"):
            match = re.search(r"-r(\d+)-L\d+$", str(row.get("batch", "")))
            round_num = int(match.group(1)) if match else None
            trend[level].append({"round": round_num, "median_cost_ticks": row["median_cost_ticks"], "series": "features"})
            current = best_features.get(level)
            if current is None or row["median_cost_ticks"] < current["median_cost_ticks"]:
                best_features[level] = row
        elif row["kind"] == "laya":
            match = re.search(r"-r(\d+)-L\d+$", str(row.get("batch", "")))
            round_num = int(match.group(1)) if match else None
            trend[level].append({"round": round_num, "median_cost_ticks": row["median_cost_ticks"], "series": "laya"})
            current = best_laya.get(level)
            if current is None or row["median_cost_ticks"] < current["median_cost_ticks"]:
                best_laya[level] = row

    rollout = {row["level"]: row for row in rows if row.get("kind") == "rollout" and row.get("level") in (1, 2)}
    return {
        "run_name": run_name, "rows": rows,
        "rollout_baseline": rollout,
        "best_features": best_features,
        "best_laya": best_laya,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# Auto-resume derivation.
# ---------------------------------------------------------------------------

def derive_autoresume(
    *, prefix: str = "ov", out_dir: Path = OUT_DIR,
    seeds_per_round: int = DEFAULT_SEEDS_PER_ROUND,
    l1_start: int = DEFAULT_L1_SEED, l2_start: int = DEFAULT_L2_SEED,
) -> dict:
    """Derive LAYA_RESUME_ROUND / LAYA_START_POLICY / LAYA_L1_SEED / LAYA_L2_SEED
    from the features models already on disk for this prefix. Rounds are
    36-seed blocks; odd rounds are level 2, even rounds are level 1."""
    pattern = re.compile(rf"^features-{re.escape(prefix)}-r(\d+)\.json$")
    rounds = []
    for path in out_dir.glob(f"features-{prefix}-r*.json"):
        match = pattern.match(path.name)
        if match:
            rounds.append(int(match.group(1)))
    if not rounds:
        return {"resume_round": 0, "start_policy": "rollout", "l1_seed": l1_start, "l2_seed": l2_start}
    last_round = max(rounds)
    l1_rounds = sum(1 for r in range(1, last_round + 1) if r % 2 == 0)
    l2_rounds = sum(1 for r in range(1, last_round + 1) if r % 2 == 1)
    return {
        "resume_round": last_round,
        "start_policy": str(out_dir / f"features-{prefix}-r{last_round}.json"),
        "l1_seed": l1_start + l1_rounds * seeds_per_round,
        "l2_seed": l2_start + l2_rounds * seeds_per_round,
    }


# ---------------------------------------------------------------------------
# Controls: start / stop-after-round / stop-now.
# ---------------------------------------------------------------------------

@dataclass
class StartRequest:
    hours: float = 6.0
    instances: list[str] | None = None
    procs: int = 3
    speed: float = 32.0
    seeds_per_round: int = DEFAULT_SEEDS_PER_ROUND
    min_case_round: int | None = None
    finetune_first: bool = False
    run_name: str = "overnight"
    prefix: str = "ov"
    resume_round: int | None = None
    start_policy: str | None = None
    l1_seed: int | None = None
    l2_seed: int | None = None
    auto_resume: bool = True


def build_start_env(request: StartRequest) -> dict[str, str]:
    """Build the environment for a driver launch, applying auto-resume when requested."""
    env = dict(os.environ)
    if request.auto_resume and request.resume_round is None:
        derived = derive_autoresume(
            prefix=request.prefix, seeds_per_round=request.seeds_per_round,
        )
        resume_round = derived["resume_round"]
        start_policy = request.start_policy or derived["start_policy"]
        l1_seed = request.l1_seed if request.l1_seed is not None else derived["l1_seed"]
        l2_seed = request.l2_seed if request.l2_seed is not None else derived["l2_seed"]
    else:
        resume_round = request.resume_round or 0
        start_policy = request.start_policy or "rollout"
        l1_seed = request.l1_seed if request.l1_seed is not None else DEFAULT_L1_SEED
        l2_seed = request.l2_seed if request.l2_seed is not None else DEFAULT_L2_SEED
    env.update({
        "LAYA_INSTANCES": " ".join(request.instances or DEFAULT_INSTANCES),
        "LAYA_PROCS": str(request.procs),
        "LAYA_SPEED": str(request.speed),
        "LAYA_SEEDS_PER_ROUND": str(request.seeds_per_round),
        "LAYA_RUN_NAME": request.run_name,
        "LAYA_PREFIX": request.prefix,
        "LAYA_RESUME_ROUND": str(resume_round),
        "LAYA_START_POLICY": start_policy,
        "LAYA_L1_SEED": str(l1_seed),
        "LAYA_L2_SEED": str(l2_seed),
    })
    if request.min_case_round is not None:
        env["LAYA_MIN_CASE_ROUND"] = str(request.min_case_round)
    if request.finetune_first:
        env["LAYA_FINETUNE_FIRST"] = "1"
    return env


def start_driver(request: StartRequest) -> dict:
    if find_driver_process() is not None:
        raise OperationError("A Laya training run is already active.")
    run_dir = OUT_DIR / request.run_name
    run_dir.mkdir(parents=True, exist_ok=True)
    driver_log = run_dir / f"driver-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    env = build_start_env(request)
    with driver_log.open("ab") as handle:
        process = subprocess.Popen(
            ["bash", str(DRIVER_SCRIPT), str(request.hours)],
            cwd=REPO_ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return {"pid": process.pid, "run_name": request.run_name, "driver_log": str(driver_log), "env_summary": {
        "LAYA_INSTANCES": env["LAYA_INSTANCES"], "LAYA_PROCS": env["LAYA_PROCS"], "LAYA_SPEED": env["LAYA_SPEED"],
        "LAYA_RESUME_ROUND": env["LAYA_RESUME_ROUND"], "LAYA_START_POLICY": env["LAYA_START_POLICY"],
        "LAYA_L1_SEED": env["LAYA_L1_SEED"], "LAYA_L2_SEED": env["LAYA_L2_SEED"],
    }}


def _select_driver(run_name: str | None) -> dict:
    """Pick exactly one active driver to act on, never guessing among several."""
    configured = _configured_drivers()
    if not configured:
        raise OperationError("No Laya training run is active.")
    if run_name:
        for entry in configured:
            if entry["config"]["run_name"] == run_name:
                return entry
        active_names = [entry["config"]["run_name"] for entry in configured]
        raise OperationError(f"No active run named '{run_name}' (active: {active_names}).")
    if len(configured) > 1:
        active_names = [entry["config"]["run_name"] for entry in configured]
        raise OperationError(f"Multiple Laya runs are active ({active_names}); specify run_name.")
    return configured[0]


def request_stop_after_round(run_name: str | None = None) -> dict:
    driver = _select_driver(run_name)
    active_run = driver["config"]["run_name"]
    run_dir = OUT_DIR / active_run
    run_dir.mkdir(parents=True, exist_ok=True)
    stop_file = run_dir / "stop-requested"
    stop_file.write_text(f"requested at {datetime.now().astimezone().isoformat(timespec='seconds')}\n", encoding="utf-8")
    return {"run_name": active_run, "stop_file": str(stop_file)}


def _unpause_instance(instance: str) -> str:
    n = int(instance)
    port = 28000 + n
    secret_path = TRAINING_STATE_ROOT / f"{n:02d}" / "worker" / "rcon-password"
    try:
        password = secret_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        return f"instance {instance}: could not read RCON secret ({exc})"
    try:
        client = RconClient("127.0.0.1", port, password, timeout=5)
        try:
            client.command("/sc game.tick_paused = false game.speed = 1")
        finally:
            client.close()
        return f"instance {instance}: unpaused"
    except (OSError, RconError, TimeoutError) as exc:
        return f"instance {instance}: unpause failed ({exc})"


def stop_now(run_name: str | None = None) -> dict:
    driver = _select_driver(run_name)
    config = driver["config"]
    active_run = config["run_name"]
    pid = driver["pid"]
    pgid = process_group_id(pid) or pid
    killed = []
    try:
        os.killpg(pgid, signal.SIGTERM)
        killed.append("SIGTERM")
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and process_group_id(pid) == pgid:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.5)
    else:
        try:
            os.killpg(pgid, signal.SIGKILL)
            killed.append("SIGKILL")
        except ProcessLookupError:
            pass

    unpause_results = [_unpause_instance(instance) for instance in config["instances"]]
    stop_results = []
    for instance in config["instances"]:
        try:
            completed = subprocess.run(
                [str(MANAGE_WORKER), "stop", "--worker-count", "20", "--only", str(int(instance))],
                capture_output=True, text=True, timeout=60,
            )
            stop_results.append(completed.stdout.strip() or completed.stderr.strip())
        except (OSError, subprocess.TimeoutExpired) as exc:
            stop_results.append(f"instance {instance}: stop failed ({exc})")
    return {
        "run_name": active_run, "pid": pid, "pgid": pgid, "signals_sent": killed,
        "unpause": unpause_results, "worker_stop": stop_results,
    }
