# Path: tools/campaign_runtime.py
# Purpose: Runtime backends (deterministic | laya) plugged into the campaign orchestrator.
"""Runtime seam for the persistent campaign controller.

The deterministic controller owns the state machine, durable state, ownership
classification, verification and commit.  This module supplies the *runtime
specific* leaves it needs so the same loop can drive a Laya episode:

- `DeterministicRuntime` delegates to the existing orchestrator helpers (so the
  deterministic behavior and its tests are unchanged);
- `LayaRuntime` drives `tools/run_laya_clean.py` and reads the Muse report from
  `tools/laya_helper_agent.py`, without a four-observer team.

Nothing here starts models or Factorio on import; every subprocess call is made
at method-call time so tests can fake the boundary.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import shutil
import tarfile
import tempfile
import os
from dataclasses import replace
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.run_log_format import parse_timed_run_log_line

REPO_ROOT = Path(__file__).resolve().parents[1]

# Laya-only 8h loop defaults: DeepSeek implements the one focused fix and also
# reviews; a single read-only Muse writer produces the terminal report.
LAYA_MODEL = "opencode-go/deepseek-v4.1-flash"
LAYA_VARIANT = "high"
LAYA_OBSERVER_MODEL = "opencode-go/muse-spark-1.3-contributor"
LAYA_OBSERVER_VARIANT = "xhigh"

# A natural Laya episode ends only on the verified research goal, a hard
# failure, or a measured complete stall.  There is no default proxy checkpoint,
# total game-tick cap, wall cap or 6h episode assumption; an explicit experiment
# may still set ``until_accepted``/``max_game_ticks``/``run_wall_seconds``.
LAYA_UNTIL_ACCEPTED = None
LAYA_MAX_GAME_TICKS = None
LAYA_RUN_WALL_SECONDS = None
LAYA_RESEARCH_GOAL = "mining-productivity-4"

# The fixed fast gate for this fix round.  A candidate passes when the only
# failures are already-known baseline failures; any new failure rejects it.
FAST_GATE = (
    "tests", "-m", "fast", "-k", "not test_runner_records_controller",
    "-q", "--tb=short", "-p", "no:cacheprovider",
)
_GATE_FAILURE = re.compile(r"(?m)^FAILED (?P<nodeid>\S+)")
KNOWN_BASELINE_GATE_FAILURES = frozenset({
    "tests/test_training_isolation.py::test_current_repository_obeys_import_firewall",
})

_LAYA_HELPER_COMPLETE = re.compile(r"LAYA HELPER: findings complete (?P<path>\S+)")
_RUN_START = "RUN START:"
_RUN_ID = re.compile(r"\brun_id=(?P<run_id>\S+)")


def gate_failures(output: str) -> frozenset[str]:
    """Node ids reported by `pytest --tb=line`'s FAILED summary lines."""
    return frozenset(match.group("nodeid") for match in _GATE_FAILURE.finditer(output))


def gate_acceptable(output: str, known: frozenset[str]) -> bool:
    """True only when the gate fails on a non-empty subset of known failures."""
    failed = gate_failures(output)
    invalid = re.search(r"(?m)^(?:ERROR\b|.*Interrupted:|.*errors? during collection|.*INTERNALERROR>|.*TIMEOUT)", output)
    return bool(failed) and failed <= frozen(known) and not invalid


def frozen(values) -> frozenset[str]:
    return values if isinstance(values, frozenset) else frozenset(values)


def mod_hashes(mods_dir: Path | None) -> dict[str, str]:
    """Per-mod directory content hashes, stable across identical copies."""
    result: dict[str, str] = {}
    base = Path(mods_dir) if mods_dir else None
    if base is None or not base.is_dir():
        return result
    for child in sorted(path for path in base.iterdir() if path.is_dir()):
        digest = hashlib.sha256()
        for path in sorted(item for item in child.rglob("*") if item.is_file()):
            digest.update(path.relative_to(child).as_posix().encode() + b"\0" + path.read_bytes())
        result[child.name] = digest.hexdigest()
    return result


def freeze_manifest(root: Path, *, revision: str, source_save_sha256: str, code_hash: str,
                    mod_hashes: dict[str, str], runtime_name: str, identity: dict | None = None) -> dict:
    """Write the immutable per-episode identity record used to reproduce a run."""
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "revision": revision,
        "source_save_sha256": source_save_sha256,
        "code_hash": code_hash,
        "mod_hashes": dict(sorted(mod_hashes.items())),
        "runtime": runtime_name,
        **(identity or {}),
    }
    target = directory / "freeze.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return manifest


def current_run_text(contents: str) -> str:
    """The newest RUN START block in a compiled runner log (either runtime)."""
    lines = contents.splitlines(keepends=True)
    starts = []
    for index, line in enumerate(lines):
        parsed = parse_timed_run_log_line(line.rstrip(), run_started_at=None)
        if line.startswith(_RUN_START) or (parsed and parsed.message.startswith(_RUN_START)):
            starts.append(index)
    return "".join(lines[starts[-1]:]) if starts else ""


def latest_run_from_log(path: Path) -> tuple[bool, str]:
    from tools.run_log_format import is_run_end_line
    try:
        contents = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, ""
    text = current_run_text(contents)
    return any(is_run_end_line(line) for line in text.splitlines()), text[-12000:]


class DeterministicRuntime:
    """Existing deterministic behavior, delegated to the orchestrator helpers."""

    name = "deterministic"
    uses_reliability = True

    def run_log_path(self, config) -> Path:
        return config.state_root / "logs/autonomous-run.log"

    def fresh_command(self, config) -> list[str]:
        from tools import opencode_campaign_orchestrator as orch
        return orch._fresh_command(config)

    def fresh(self, config) -> subprocess.CompletedProcess[str]:
        from tools import opencode_campaign_orchestrator as orch
        return orch._run(orch._fresh_command(config), timeout=900)

    def latest_run(self, config) -> tuple[bool, str]:
        from tools import opencode_campaign_orchestrator as orch
        return orch._latest_run(self.run_log_path(config))

    def snapshot(self, config, cycle: int, checkpoint: int, offset: int):
        from tools import opencode_campaign_orchestrator as orch
        return orch._snapshot(config, cycle, checkpoint, offset)

    def helper_finding(self, config, episode: str) -> tuple[str | None, bool]:
        from tools import opencode_campaign_orchestrator as orch
        return orch._helper_finding(config, episode)

    def episode_id(self, config) -> str | None:
        from tools import opencode_campaign_orchestrator as orch
        return orch._episode_id(config)

    def milestones(self, run_text: str) -> dict:
        from tools import opencode_campaign_orchestrator as orch
        return orch._milestones(run_text)

    def terminal_board(self, config, episode: str) -> Path:
        return config.opencode_log_dir / "observers" / episode / "board.md"

    def verification_commands(self, config, declared: list[str]) -> list[list[str]]:
        from tools import opencode_campaign_orchestrator as orch
        pytest = [str(config.python), "-m", "pytest", "-q", "--tb=short"]
        mandatory = [str(REPO_ROOT / path) for path in orch.MANDATORY_CAMPAIGN_TESTS]
        commands = [pytest + mandatory + ["-m", "not slow and not exhaustive"]]
        changed = _changed_tests(declared)
        if changed:
            commands.append(pytest + changed)
        return commands

    def verification_passed(self, config, command: list[str], result) -> bool:
        return result.returncode == 0

    def commit_message(self, fix_id: str) -> str:
        return f"fix(deterministic): verified campaign repair ({fix_id})"

    def stop(self, config) -> None:
        return None


class LayaRuntime:
    """Laya episodes: `run_laya_clean.py` + a Muse helper, no observer team."""

    name = "laya"
    uses_reliability = False

    def run_log_path(self, config) -> Path:
        return Path(config.world_root) / "logs/laya-runner.log"

    def prepare_command(self, config) -> list[str]:
        command = [
            str(config.python), str(Path(config.code_root or REPO_ROOT) / "tools/run_laya_clean.py"), "prepare",
            "--root", str(config.world_root), "--game-port", str(config.game_port),
            "--rcon-port", str(config.rcon_port), "--source-save", str(config.source_save),
        ]
        if config.mods_source:
            command += ["--mods-source", str(config.mods_source)]
        return command

    def run_command(self, config) -> list[str]:
        command = [
            str(config.python), str(Path(config.code_root or REPO_ROOT) / "tools/run_laya_clean.py"), "run",
            "--root", str(config.world_root), "--seed", str(config.seed),
            "--experiment", str(config.episode_id or "laya-campaign"),
            "--speed", f"{config.run_speed:g}", "--actions", "deterministic", "--no-helper",
            "--source-save", str(config.source_save),
        ]
        # Explicit experiment caps only; a natural goal run sets none of them.
        if config.until_accepted:
            command += ["--until-accepted", str(config.until_accepted)]
        if config.max_game_ticks:
            command += ["--max-game-ticks", str(config.max_game_ticks)]
        if config.run_wall_seconds:
            command += ["--max-wall-seconds", str(config.run_wall_seconds)]
        goal = config.technology or LAYA_RESEARCH_GOAL
        if goal:
            command += ["--research-goal", str(goal)]
        return command

    def fresh_command(self, config) -> list[str]:
        return self.run_command(config)

    def allocate_config(self, config, cycle: int):
        root = Path(config.state_root) / "episodes" / config.episode_id
        return replace(config, code_root=root / "code",
                       world_root=root / "code/data/laya-saved-world" / config.episode_id,
                       mods_source=root / "mods")

    def config_identity(self, config) -> dict:
        return {"experiment": config.episode_id, "code_root": str(config.code_root),
                "world_root": str(config.world_root), "mods_source": str(config.mods_source)}

    def restore_config(self, config, identity):
        if not identity:
            raise RuntimeError("missing durable Laya episode identity")
        return replace(config, episode_id=identity["experiment"],
                       code_root=Path(identity["code_root"]), world_root=Path(identity["world_root"]),
                       mods_source=Path(identity["mods_source"]))

    def freeze(self, config) -> dict:
        """Archive committed source once, build current mods, stamp then freeze."""
        from tools import opencode_campaign_orchestrator as orch
        root = Path(config.code_root).parent
        manifest_path = root / "freeze.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest["experiment"] != config.episode_id:
                raise RuntimeError("frozen experiment identity mismatch")
            return manifest
        root.mkdir(parents=True, exist_ok=True)
        revision = orch._run(["git", "rev-parse", "HEAD"], timeout=120).stdout.strip()
        if not revision:
            raise RuntimeError("cannot freeze unidentified revision")
        archive = root / "source.tar"
        result = orch._run(["git", "archive", "--format=tar", "--output", str(archive), revision], timeout=120)
        if result.returncode:
            raise RuntimeError(f"source archive failed: {result.stderr}")
        code = Path(config.code_root)
        stage_path = root / "freeze-stage.json"
        stage = json.loads(stage_path.read_text()) if stage_path.exists() else {}
        if stage and stage["revision"] != revision:
            raise RuntimeError("writable HEAD changed during unfinished freeze")
        if not stage:
            stage = {"revision": revision}
            stage_path.write_text(json.dumps(stage) + "\n")
        if not stage.get("source_ready"):
            extraction = root / "code.extracting"
            if extraction.exists():
                extraction.rename(root / f"interrupted-extraction-{time.time_ns()}")
            extraction.mkdir()
            with tarfile.open(archive) as stream:
                stream.extractall(extraction, filter="data")
            if code.exists():
                code.rename(root / f"interrupted-code-{time.time_ns()}")
            extraction.rename(code)
            stage["source_ready"] = True
            stage_path.write_text(json.dumps(stage) + "\n")
        mods = Path(config.mods_source)
        if mods.exists() and not stage.get("mods_ready"):
            mods.rename(root / f"interrupted-mods-{time.time_ns()}")
        builder = config.mod_builder or (code / "tools/build_runtime_mods.py" if (code / "tools/build_runtime_mods.py").exists() else None)
        if not stage.get("mods_ready") and builder:
            result = orch._run([str(config.python), str(builder),
                               "--source", str(code / "factorio_mod"), "--output", str(mods)], timeout=900)
            if result.returncode:
                raise RuntimeError(f"mod build failed: {result.stderr}")
            # The shared-source builder emits deterministic and Laya artifacts;
            # the training lab remains a separate source directory.
            if not (mods / "factorio_training_lab").exists():
                shutil.copytree(code / "factorio_training_lab", mods / "factorio_training_lab")
        elif not stage.get("mods_ready"):
            mods.mkdir(parents=True, exist_ok=True)
            for name, source in (("factorio_cursor_rl_agent", "factorio_mod"),
                                 ("factorio_training_lab", "factorio_training_lab")):
                shutil.copytree(code / source, mods / name)
        stage["mods_ready"] = True
        stage_path.write_text(json.dumps(stage) + "\n")
        if not config.stamp_script or not config.gui_mods:
            raise ValueError("Laya freeze requires --stamp-script and --gui-mods")
        tag = config.stamp_tag_base + len(list((config.state_root / "episodes").glob("*/freeze.json")))
        stamped = orch._run([str(config.python), str(config.stamp_script),
                            "--server-mods", str(mods), "--gui-mods", str(config.gui_mods),
                            "--tag", str(tag)], timeout=900)
        if stamped.returncode:
            raise RuntimeError(f"mod stamping failed: {stamped.stderr}")
        source = Path(config.source_save)
        identity = {**self.config_identity(config), "requested_game_speed": config.run_speed,
                    "game_port": config.game_port, "rcon_port": config.rcon_port,
                    "research_goal": config.technology,
                    "until_accepted": config.until_accepted,
                    "max_game_ticks": config.max_game_ticks, "max_wall_seconds": config.run_wall_seconds,
                    "stamp_output": stamped.stdout.strip(), "source_save": str(source)}
        manifest = freeze_manifest(
            root, revision=revision, source_save_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            code_hash=hashlib.sha256(archive.read_bytes()).hexdigest(),
            mod_hashes=mod_hashes(mods), runtime_name="laya", identity=identity)
        # Source files are immutable; evidence and generated data have dedicated
        # mutable directories. No editable Git metadata exists in the archive.
        for path in code.rglob("*"):
            if path.is_file():
                path.chmod(path.stat().st_mode & ~0o222)
        return manifest

    def _lifecycle_record(self, config, **update):
        path = Path(config.code_root).parent / "lifecycle.json"
        state = json.loads(path.read_text()) if path.exists() else {}
        if update:
            state.update(update)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(state, indent=2) + "\n")
            temporary.replace(path)
        return state

    def fresh(self, config) -> subprocess.CompletedProcess[str]:
        """Idempotent prepare/launch using per-episode durable lifecycle phases."""
        from tools import opencode_campaign_orchestrator as orch
        self.freeze(config)
        lifecycle = self._lifecycle_record(config)
        log = self.run_log_path(config)
        if self.episode_id(config):
            return subprocess.CompletedProcess(self.run_command(config), 0, "resumed identified runner", "")
        world = Path(config.world_root)
        if lifecycle.get("phase") == "preparing":
            # prepare isn't repeatable. A complete manifest + initial facts
            # prove its last stage completed; partial roots require diagnosis.
            if not (world / "initial-state.json").exists():
                raise RuntimeError("interrupted prepare incomplete; refusing duplicate prepare")
            self._lifecycle_record(config, phase="prepared")
        elif not lifecycle:
            self._lifecycle_record(config, phase="preparing")
            prepare = orch._run(self.prepare_command(config), timeout=900)
            if prepare.returncode:
                return prepare
            self._lifecycle_record(config, phase="prepared")
        lifecycle = self._lifecycle_record(config)
        console = world / "logs/laya-run-console.log"
        console.parent.mkdir(parents=True, exist_ok=True)
        command = self.run_command(config)
        if lifecycle.get("phase") == "launching":
            pid = lifecycle.get("pid")
            if not pid:
                raise RuntimeError("launch interrupted before pid persisted; inspect before duplicate launch")
            try:
                os.kill(int(pid), 0)
            except ProcessLookupError as error:
                raise RuntimeError(f"runner {pid} ended before RUN START; see {console}") from error
            process = None
        else:
            self._lifecycle_record(config, phase="launching")
            with console.open("ab") as stream:
                process = subprocess.Popen(command, cwd=config.code_root, stdin=subprocess.DEVNULL,
                                           stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            self._lifecycle_record(config, phase="launching", pid=process.pid)
        wait_deadline = time.time() + 900
        while time.time() < wait_deadline:
            if self.episode_id(config):
                self._lifecycle_record(config, phase="running", run_id=self.episode_id(config))
                return subprocess.CompletedProcess(command, 0, "Laya runner identified", "")
            if process is not None and process.poll() is not None:
                raise RuntimeError(f"Laya runner exited {process.returncode} before RUN START; see {console}")
            time.sleep(2)
        raise TimeoutError(f"Laya runner did not reach RUN START within 900s; see {console}")

    def observe(self, config, episode: str) -> dict:
        """Exactly one terminal helper invocation, no live model checkpoints."""
        from tools import opencode_campaign_orchestrator as orch
        directory = Path(config.code_root).parent / "observer"
        directory.mkdir(exist_ok=True)
        state_path = directory / "state.json"
        if state_path.exists():
            previous = json.loads(state_path.read_text())
            if previous.get("status") in {"complete", "failed"}:
                return previous
            # An interrupted helper invocation is not permission to start a
            # second observer. Preserve its reports and wait for its explicit
            # completion line, then skip a fix if it remains incomplete.
            report = directory / "reports" / episode / "findings.md"
            wait_deadline = time.time() + config.helper_wait_seconds
            while True:
                try:
                    announced = f"LAYA HELPER: findings complete {report}" in self.run_log_path(config).read_text()
                except OSError:
                    announced = False
                if announced and report.is_file():
                    record = {"episode": episode, "status": "complete", "path": str(report),
                              "reason": "interrupted invocation finished; observer not duplicated"}
                    break
                if time.time() >= wait_deadline:
                    record = {"episode": episode, "status": "failed", "path": None,
                              "reason": "interrupted terminal observer remains incomplete; no duplicate invocation"}
                    break
                time.sleep(min(5, max(0, wait_deadline - time.time())))
            state_path.write_text(json.dumps(record, indent=2) + "\n")
            return record
        # RUN END is written immediately before the runner appends its index.
        # Wait for that durable row, rather than racing helper current-run lookup.
        index = Path(config.code_root) / "data/laya-saved-world/laya-runs.jsonl"
        ready_deadline = time.time() + max(1, config.helper_wait_seconds)
        while True:
            indexed = False
            if index.exists():
                with index.open("rb") as handle:
                    handle.seek(max(0, index.stat().st_size - 8192))
                    rows = handle.read().decode("utf-8", errors="replace").splitlines()
                for row in rows:
                    try:
                        indexed |= json.loads(row).get("run_id") == episode
                    except (ValueError, AttributeError):
                        continue
            if indexed:
                break
            if time.time() >= ready_deadline:
                record = {"episode": episode, "status": "failed", "path": None,
                          "reason": "natural run ended without durable index row; helper not started"}
                state_path.write_text(json.dumps(record, indent=2) + "\n")
                return record
            time.sleep(min(1, max(0, ready_deadline - time.time())))
        settings = json.loads((Path(config.code_root) / "docs/rl/laya_helper.json").read_text())
        settings.update(model=config.observer_model, variant=config.observer_variant)
        settings_path = directory / "config.json"
        settings_path.write_text(json.dumps(settings, indent=2) + "\n")
        command = [str(config.python), str(Path(config.code_root) / "tools/laya_helper_agent.py"),
                   "--run-id", episode, "--log", str(self.run_log_path(config)),
                   "--config", str(settings_path), "--report-root", str(directory / "reports"),
                   "--data-root", str(directory / "transport"), "--opencode-bin", config.opencode_bin]
        comparators = list(config.comparator_roots)
        for previous in sorted((config.state_root / "episodes").glob("*/freeze.json")):
            if previous.parent != Path(config.code_root).parent:
                comparators.append(Path(json.loads(previous.read_text())["world_root"]))
        records = []
        for comparator in comparators:
            records.extend(comparator_records(Path(comparator)))
        comparator_file = directory / "comparators.json"
        comparator_file.write_text(json.dumps(records, indent=2) + "\n")
        command += ["--comparators", str(comparator_file)]
        state_path.write_text(json.dumps({"episode": episode, "status": "running", "command": command}) + "\n")
        report = directory / "reports" / episode / "findings.md"
        try:
            persisted = orch._load_state(config.state_file)
            deadline = datetime.fromisoformat(persisted.deadline_utc).timestamp() if persisted.deadline_utc else float("inf")
            remaining = deadline - time.time()
            timeout = min(config.model_timeout_seconds, max(1, remaining)) if remaining > 0 else config.model_timeout_seconds
            result = orch._run(command, timeout=timeout)
            (directory / "console.log").write_text(result.stdout + result.stderr)
            complete = result.returncode == 0 and report.is_file()
            record = {"episode": episode, "status": "complete" if complete else "failed",
                      "path": str(report) if complete else None,
                      "reason": f"terminal helper returncode={result.returncode}"}
        except (subprocess.TimeoutExpired, OSError, RuntimeError) as error:
            record = {"episode": episode, "status": "failed", "path": None, "reason": str(error)}
        state_path.write_text(json.dumps(record, indent=2) + "\n")
        return record

    def latest_run(self, config) -> tuple[bool, str]:
        return latest_run_from_log(self.run_log_path(config))

    def snapshot(self, config, cycle: int, checkpoint: int, offset: int):
        if config.dry_run:
            return "Dry run: Laya snapshot skipped.", offset, None
        log = self.run_log_path(config)
        try:
            text = current_run_text(log.read_text(encoding="utf-8", errors="replace"))[-6000:]
        except OSError:
            text = ""
        status_path = Path(config.world_root) / "evidence/latest-status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = {}
        tick = status.get("tick") if isinstance(status, dict) else None
        body = (f"## Laya cycle {cycle} — checkpoint {checkpoint} ({datetime.now(timezone.utc).isoformat(timespec='seconds')})\n\n"
                f"```text\n{text}\n```\n\n### Decision status\n\n```json\n{json.dumps(status)[:2000]}\n```\n")
        context = Path(config.state_root) / "logs/latest-context.md"
        context.parent.mkdir(parents=True, exist_ok=True)
        context.write_text(body, encoding="utf-8")
        next_offset = log.stat().st_size if log.exists() else offset
        return body, next_offset, tick if isinstance(tick, int) else None

    def helper_finding(self, config, episode: str) -> tuple[str | None, bool]:
        log = self.run_log_path(config)
        try:
            contents = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            contents = ""
        for match in _LAYA_HELPER_COMPLETE.finditer(current_run_text(contents)):
            path = Path(match.group("path"))
            if path.is_file():
                return str(path), True
        code_root = Path(config.code_root) if getattr(config, "code_root", None) else REPO_ROOT
        candidate = code_root / "docs/rl/laya_helper_runs" / str(episode) / "findings.md"
        if candidate.is_file():
            return str(candidate), False
        return None, False

    def episode_id(self, config) -> str | None:
        log = self.run_log_path(config)
        try:
            text = current_run_text(log.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None
        run_id = None
        for line in text.splitlines():
            if line.startswith(_RUN_START):
                match = _RUN_ID.search(line)
                if match:
                    run_id = match.group("run_id")
        return run_id

    def milestones(self, run_text: str) -> dict:
        return {}

    def terminal_board(self, config, episode: str) -> Path:
        return config.opencode_log_dir / "observers" / episode / "board.md"

    def verification_commands(self, config, declared: list[str]) -> list[list[str]]:
        commands = [[str(config.python), "-m", "pytest", *FAST_GATE]]
        changed = _changed_tests(declared)
        if changed:
            commands.append([str(config.python), "-m", "pytest", *changed,
                             "-q", "--tb=short", "-p", "no:cacheprovider"])
        return commands

    def verification_passed(self, config, command: list[str], result) -> bool:
        if result.returncode == 0:
            return True
        if result.returncode == 1 and tuple(command[3:]) == FAST_GATE:
            return gate_acceptable(result.stdout + result.stderr, KNOWN_BASELINE_GATE_FAILURES)
        return False

    def regression_proof(self, config, declared: list[str], fields: dict, log: Path) -> bool:
        """Replay the new focused test against HEAD, then require candidate pass."""
        from tools import opencode_campaign_orchestrator as orch
        changes_code = any(Path(path).suffix in {".py", ".lua", ".sh"} and not path.startswith("tests/") for path in declared)
        if not changes_code and fields.get("kind", "behavior") not in {"behavior", "feature", "fix"}:
            return True
        node = fields.get("regression", "").strip()
        if not re.fullmatch(r"tests/[A-Za-z0-9_./-]+\.py::[A-Za-z0-9_:]+", node):
            log.write_text("Behavior fix requires regression: tests/test_file.py::test_name\n")
            return False
        test_path = node.split("::", 1)[0]
        if test_path not in declared or test_path == "tests/test_training_isolation.py":
            log.write_text("Focused regression must be declared and target the candidate behavior\n")
            return False
        repository = Path(config.campaign_worktree or orch.REPO_ROOT)
        with tempfile.TemporaryDirectory(prefix="laya-baseline-") as temporary:
            baseline = Path(temporary)
            archive = baseline / "source.tar"
            exported = orch._run(["git", "archive", "--format=tar", "--output", str(archive), "HEAD"], timeout=120)
            if exported.returncode:
                log.write_text("Baseline export failed\n" + exported.stderr)
                return False
            with tarfile.open(archive) as stream:
                stream.extractall(baseline, filter="data")
            for path in _changed_tests(declared):
                target = baseline / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(repository / path, target)
            command = [str(config.python), "-m", "pytest", node, "-q", "--tb=short", "-p", "no:cacheprovider"]
            environment = {**os.environ, "PYTHONPATH": str(baseline), "PYTHONDONTWRITEBYTECODE": "1"}
            try:
                before = subprocess.run(command, cwd=baseline, env=environment, text=True,
                                        capture_output=True, timeout=120, check=False)
                after = orch._run(command, timeout=120)
            except (subprocess.TimeoutExpired, TimeoutError) as error:
                log.write_text(f"Focused regression timeout: {error}\n")
                return False
            log.write_text(f"BASELINE returncode={before.returncode}\n{before.stdout}{before.stderr}\n"
                           f"CANDIDATE returncode={after.returncode}\n{after.stdout}{after.stderr}")
            return (before.returncode == 1 and node in gate_failures(before.stdout + before.stderr)
                    and not re.search(r"(?m)^ERROR|Interrupted:|INTERNALERROR", before.stdout + before.stderr)
                    and after.returncode == 0)

    def commit_message(self, fix_id: str) -> str:
        return f"fix(RL Laya): verified campaign repair ({fix_id})"

    def stop(self, config) -> subprocess.CompletedProcess[str]:
        from tools import opencode_campaign_orchestrator as orch
        return orch._run(
            [str(config.python), str(Path(config.code_root or REPO_ROOT) / "tools/run_laya_clean.py"), "stop",
             "--root", str(config.world_root)], timeout=120)



def comparator_records(root: Path) -> list[dict]:
    """Translate explicit prior world roots to helper API records, bounded reads."""
    root = Path(root).resolve()
    index = root.parent / "laya-runs.jsonl"
    records = []
    if index.exists():
        with index.open("rb") as handle:
            handle.seek(max(0, index.stat().st_size - 65536))
            lines = handle.read().decode("utf-8", errors="replace").splitlines()
        for line in lines:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("root") and Path(record["root"]).resolve() == root:
                records.append({**record, "kind": "laya"})
    if not records:
        log = root / "logs/laya-runner.log"
        if not log.exists():
            return []
        with log.open() as handle:
            head = handle.read(8192)
        start = next((line for line in head.splitlines() if line.startswith("RUN START:")), "")
        match = _RUN_ID.search(start)
        if not match:
            return []
        commit = re.search(r"\bcommit=(\S+)", start)
        started = re.search(r"\bts=(\S+)", start)
        records = [{"run_id": match["run_id"], "root": str(root), "kind": "laya",
                    "commit": commit[1] if commit else "unknown",
                    "started": started[1] if started else "unknown", "ended": "unknown",
                    "summary": {}, "index_source": "bounded-log-fallback"}]
    for record in records:
        report = root.parents[3] / "observer/reports" / record["run_id"] / "findings.md" if len(root.parents) > 3 else None
        if report and report.exists():
            record["findings"] = str(report)
    return records

def _changed_tests(declared: list[str]) -> list[str]:
    return [
        path for path in declared
        if Path(path).parts[:1] == ("tests",) and Path(path).name.startswith("test_")
        and Path(path).suffix == ".py"
    ]


_DETERMINISTIC = DeterministicRuntime()
_LAYALA = LayaRuntime()


def backend_for(config):
    """Pick the runtime backend from a (possibly lightweight) config object."""
    return _LAYALA if getattr(config, "runtime", "deterministic") == "laya" else _DETERMINISTIC
