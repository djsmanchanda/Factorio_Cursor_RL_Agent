# Path: tools/persistent_laya_run.py | Purpose: Archive fresh worlds and launch frozen Laya runners on the persistent server.
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from training.laya_saved_world import file_hash
from tools.persistent_factorio import validate, verify_process


def identity():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def profile_for_world(world):
    profile_path = ROOT / "data/persistent-factorio/laya.json"
    profile = json.loads(profile_path.read_text())
    server, _ = validate(profile)
    manifest = json.loads((world / "manifest.json").read_text())
    if (server != (world / "server").resolve() or profile["runtime"] != "laya"
            or not manifest.get("persistent_server") or manifest.get("production_authority") is not False
            or manifest.get("server_unit") != profile["server_unit"]):
        raise ValueError("console world does not match the isolated persistent Laya profile")
    verify_process(profile)
    return profile_path, profile, manifest


def service(profile, action):
    subprocess.run(["systemctl", "--user", action, profile["server_unit"]], check=True,
                   capture_output=True, text=True, timeout=120)


def fresh_world(world):
    """An explicit fresh action archives the previous world before resetting it."""
    world = Path(world).resolve()
    profile_path, profile, manifest = profile_for_world(world)
    source = ROOT / "saves/mod_playground.zip"
    source_hash = file_hash(source)
    server = world / "server"
    for rel, digest in manifest["mod_hashes"].items():
        if file_hash(server / "mods" / rel) != digest:
            raise ValueError("persistent server mods differ from their recorded freeze")
    backup = ROOT / "data/persistent-factorio/history" / identity()
    backup.mkdir(parents=True)
    transaction = {"backup": str(backup), "world": str(world), "source_sha256": source_hash, "phase": "validated"}
    record = backup / "fresh-transaction.json"
    def phase(value):
        transaction["phase"] = value
        record.write_text(json.dumps(transaction, indent=2) + "\n")
    phase("validated")
    service(profile, "stop")
    phase("stopped")
    # Keep the process root, mods, configuration and secret. Archive every
    # controller ledger and report so the fresh world inherits no old claims.
    for parent, names in [(world, ("baseline.zip", "manifest.json", "initial-state.json", "evidence", "logs",
                                    "laya-runner.heartbeat.json", "laya-runner.pid.json", "run-owner.json")),
                           (server, ("saves", "logs", "script-output", "factorio-current.log", "factorio-previous.log"))]:
        destination = backup / ("world" if parent == world else "server")
        destination.mkdir()
        for name in names:
            path = parent / name
            if path.exists():
                path.rename(destination / name)
    phase("archived")
    # Index rows must follow their archived evidence, not the reused live root.
    index = ROOT / "data/persistent-factorio/laya-runs.jsonl"
    if index.exists():
        rows = [json.loads(line) for line in index.read_text().splitlines() if line.strip()]
        for row in rows:
            if Path(row["root"]).resolve() == world:
                row["root"] = str(backup / "world")
        staged = index.with_suffix(".tmp")
        staged.write_text("".join(json.dumps(row) + "\n" for row in rows))
        staged.replace(index)
    (server / "saves").mkdir()
    for path in (server / "logs", server / "script-output", world / "logs", world / "evidence"):
        path.mkdir()
    shutil.copy2(source, world / "baseline.zip")
    shutil.copy2(source, server / "saves/mod_playground.zip")
    manifest.update(source_save=str(source), source_sha256=source_hash, initial_copy_sha256=source_hash,
                    quad_centers=[], fresh_backup=str(backup), comparison_eligible=False)
    manifest.pop("continuation_origin", None)
    (world / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    profile.update(source_save=str(source), source_sha256=source_hash)
    profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    phase("reset")
    service(profile, "start")
    phase("started")
    return backup


def freeze_runner(world, speed, seed):
    _, profile, manifest = profile_for_world(world)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    episode = ROOT / "data/persistent-factorio/runs" / identity()
    episode.mkdir(parents=True)
    archive = episode / "source.tar"
    subprocess.run(["git", "archive", "--output", str(archive), revision], cwd=ROOT, check=True)
    code = episode / "code"
    code.mkdir()
    with tarfile.open(archive) as stream:
        stream.extractall(code, filter="data")
    shutil.copytree(world / "server/mods", episode / "mods")
    for rel, digest in manifest["mod_hashes"].items():
        if file_hash(episode / "mods" / rel) != digest:
            raise ValueError(f"frozen mod copy differs from the recorded stamp: {rel}")
    manifest["repository_revision"] = revision
    (world / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    frozen = {"revision": revision, "owner_repository": str(ROOT), "world": str(world),
              "speed": speed, "seed": seed, "actions": "deterministic", "goal": "mining-productivity-4",
              "game_port": profile["game_port"], "rcon_port": profile["rcon_port"],
              "source_sha256": manifest["source_sha256"], "mod_hashes": manifest["mod_hashes"],
              "code_hashes": {str(p.relative_to(code)): file_hash(p) for p in code.rglob("*") if p.is_file()}}
    helper = json.loads((code / "docs/rl/laya_helper.json").read_text())
    frozen["helper"] = {key: helper[key] for key in ("model", "variant")}
    frozen["helper"]["opencode_bin"] = shutil.which("opencode")
    path = episode / "freeze.json"
    path.write_text(json.dumps(frozen, indent=2) + "\n")
    return path


def launch(world, python, speed, seed=50616):
    world = Path(world).resolve()
    (world / "logs").mkdir(exist_ok=True)
    freeze = freeze_runner(world, speed, seed)
    code = freeze.parent / "code"
    unit = "laya-persistent-run-" + freeze.parent.name
    subprocess.run(["systemd-run", "--user", "--unit", unit, "--property", f"WorkingDirectory={code}",
                    "--setenv", "PATH=" + os.environ.get("PATH", ""),
                    "--property", "Restart=no", "--property", "TimeoutStopSec=120",
                    "--property", f"StandardOutput=append:{world / 'logs/laya-runner.stdout.log'}",
                    "--property", "StandardError=inherit",
                    str(python), str(code / "tools/persistent_laya_run.py"), "--freeze", str(freeze)],
                   check=True, capture_output=True, text=True)
    owner = {"unit": unit + ".service", "freeze": str(freeze)}
    (world / "run-owner.json").write_text(json.dumps(owner, indent=2) + "\n")
    return f"Frozen Laya runner started on persistent port {json.loads(freeze.read_text())['game_port']} ({unit})."


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    args = parser.parse_args()
    freeze = args.freeze.resolve()
    facts = json.loads(freeze.read_text())
    if ROOT != freeze.parent / "code":
        raise ValueError("persistent runner must execute its frozen code copy")
    for rel, digest in facts["code_hashes"].items():
        if file_hash(ROOT / rel) != digest:
            raise ValueError(f"frozen code changed: {rel}")
    world = Path(facts["world"])
    for rel, digest in facts["mod_hashes"].items():
        if file_hash(world / "server/mods" / rel) != digest:
            raise ValueError(f"frozen server mod changed: {rel}")
    if file_hash(world / "baseline.zip") != facts["source_sha256"]:
        raise ValueError("persistent run baseline changed")
    from training.laya_saved_world import register_persistent_world
    register_persistent_world(world, Path(facts["owner_repository"]))
    from tools.run_laya_clean import main as run
    owner = Path(facts["owner_repository"])
    return run(["run", "--root", str(world), "--actions", facts["actions"], "--research-goal", facts["goal"],
                "--seed", str(facts["seed"]), "--speed", str(facts["speed"]),
                "--helper-report-root", str(owner / "docs/rl/laya_helper_runs"),
                "--helper-data-root", str(owner / "data/persistent-factorio/helper-state"),
                "--run-index", str(owner / "data/persistent-factorio/laya-runs.jsonl"),
                "--source-save", str(world / "baseline.zip"), "--experiment", freeze.parent.name])


if __name__ == "__main__":
    raise SystemExit(main())
