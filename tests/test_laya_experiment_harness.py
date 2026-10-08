# Path: tests/test_laya_experiment_harness.py | Purpose: Every planned experiment run is counted once, including crashes, so no arm loses failures from its denominator.
import argparse
import json
import threading
from types import SimpleNamespace

import pytest

import tools.laya_experiment as experiment


def test_unfinished_runs_are_indexed_once_and_reported(tmp_path, monkeypatch):
    index = tmp_path / "laya-runs.jsonl"
    monkeypatch.setattr(experiment, "RUN_INDEX", index)
    args = argparse.Namespace(until_phase="C2", max_game_ticks=216000, experiment="exp")
    root = tmp_path / "baseline-s1"
    # The run indexed itself: nothing is added.
    index.write_text(json.dumps({"root": str(root.resolve()), "experiment": "exp", "variant_id": "baseline",
                                 "summary": {"stop": "stalled", "phases_reached": ["C0", "C1"],
                                             "codes": {"untyped_stuck": 2},
                                             "acceptance": {"C1": "failed"}, "accepted": ["C0"]}}) + "\n")
    experiment._finalize("exp", None, 1, root, args, "unindexed", 0)
    # A crash and a failed preparation are recorded with their cause.
    experiment._finalize("exp", None, 2, tmp_path / "baseline-s2", args, "crash", 1)
    experiment._finalize("exp", None, 3, tmp_path / "baseline-s3", args, "prepare_failed", 2)
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert len(rows) == 3 and rows[1]["returncode"] == 1
    score = experiment.score(rows, "C1")
    assert score["runs"] == 3 and score["reached"] == 1 and score["untyped_stuck"] == 2
    # The rate proxy reached C1; the deterministic C1 predicate did not pass.
    assert score["accepted"] == 0 and score["acceptance_checked"] == 1
    assert score["stops"] == {"crash": 1, "prepare_failed": 1, "stalled": 1}


def test_failed_preparation_stops_its_partially_started_world(tmp_path, monkeypatch):
    commands, finalized = [], []

    def invoke(command, **_):
        commands.append(command)
        return SimpleNamespace(returncode=1 if "prepare" in command else 0)

    monkeypatch.setattr(experiment, "ROOT", tmp_path)
    monkeypatch.setattr(experiment.subprocess, "run", invoke)
    monkeypatch.setattr(experiment, "_finalize", lambda *args: finalized.append(args))
    args = SimpleNamespace(game_base=35300, rcon_base=28300, timeout=30)
    result = experiment._job("test", None, 1, 0, args)
    assert "error" in result
    assert "prepare" in commands[0] and "stop" in commands[1]
    assert finalized[0][-2:] == ("prepare_failed", 1)


def test_forty_concurrent_jobs_get_distinct_slots(monkeypatch):
    lock, barrier = threading.Lock(), threading.Barrier(40)
    active, seen = set(), set()

    def job(name, variant, seed, slot, args):
        with lock:
            assert slot not in active
            active.add(slot)
            seen.add(slot)
        barrier.wait(timeout=5)
        with lock:
            active.remove(slot)
        return {}

    monkeypatch.setattr(experiment, "_job", job)
    monkeypatch.setattr(experiment, "report", lambda _: 0)
    args = SimpleNamespace(parallel=40, seeds=20, seed_base=1, game_base=35300, rcon_base=28300,
                           experiment="test", variants=[experiment.ROOT / "docs/rl/laya_variants/ops-other-slots-1.json"])
    assert experiment.run(args) == 0
    assert seen == set(range(40)) and not active


@pytest.mark.parametrize("parallel,game_base,rcon_base", [(0, 35300, 28300), (41, 35300, 28300),
                                                         (40, 65520, 28300), (40, 35300, 35320)])
def test_invalid_capacity_or_port_ranges_fail_before_launch(parallel, game_base, rcon_base):
    args = SimpleNamespace(parallel=parallel, seeds=20, game_base=game_base, rcon_base=rcon_base)
    with pytest.raises(SystemExit):
        experiment.run(args)
