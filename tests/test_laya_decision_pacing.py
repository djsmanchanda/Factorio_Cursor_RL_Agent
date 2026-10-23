# Path: tests/test_laya_decision_pacing.py | Purpose: Verify bounded wait ramps, unchanged defaults and exact recovery timing.
import dataclasses
import json
import math

import pytest

from training.laya_clean_runner import Settings
from training.laya_decision_pacing import apply_waits, multiplier


def menu():
    return {"options": [{"option_id": f"wait.{ticks}", "family": "wait", "need_id": "wait",
                          "wait_ticks": ticks, "features": {}} for ticks in (300, 1800)],
            "needs": [{"need_id": "wait", "options": ["wait.300", "wait.1800"]}]}


def test_default_keeps_original_wait_choices_and_never_inserts_a_loop_delay():
    original = menu()
    assert apply_waits(original, Settings(), 1000000) is original
    assert multiplier(Settings(), 1000000) == 1


@pytest.mark.parametrize("schedule,minutes,expected", [("linear", 1, 1.03), ("linear", 30, 1.9), ("linear", 60, 2.8),
                                                       ("exponential", 1, 1.03), ("exponential", 20, 1.03**20),
                                                       ("sqrt", 20, math.sqrt(2)), ("sqrt", 60, 2)])
def test_wait_ramps_follow_explicit_game_time_and_preserve_their_original_menu(schedule, minutes, expected):
    settings = Settings(decision_wait_schedule=schedule)
    assert multiplier(settings, minutes * 3600) == pytest.approx(expected)
    original = menu()
    transformed = apply_waits(original, settings, minutes * 3600)
    assert transformed["options"][0]["wait_ticks"] == max(60, math.ceil(300 / expected))
    assert transformed["needs"][0]["options"] == [o["option_id"] for o in transformed["options"]]
    assert original["options"][0]["wait_ticks"] == 300


@pytest.mark.parametrize("schedule", ["linear", "exponential", "sqrt"])
def test_ramps_saturate_and_keep_nonzero_waits_at_extreme_elapsed_time(schedule):
    settings = Settings(decision_wait_schedule=schedule)
    assert multiplier(settings, 1e308) == 4
    transformed = apply_waits(menu(), settings, 1e308)
    assert [o["wait_ticks"] for o in transformed["options"]] == [75, 450]
    settings = dataclasses.replace(settings, decision_wait_max_multiplier=1e30)
    transformed = apply_waits(menu(), settings, 1e308)
    assert len(transformed["options"]) == 1 and transformed["options"][0]["wait_ticks"] == 60


@pytest.mark.parametrize("field,value", [("decision_wait_schedule", "quadratic"), ("decision_wait_schedule", []),
    ("decision_wait_growth_per_minute", -1), ("decision_wait_growth_per_minute", True),
    ("decision_wait_sqrt_minutes", 0), ("decision_wait_sqrt_minutes", float("nan")),
    ("decision_wait_max_multiplier", .9), ("decision_wait_max_multiplier", float("inf")),
    ("decision_wait_min_ticks", 0), ("decision_wait_min_ticks", 301), ("decision_wait_min_ticks", True),
    ("mall_loan_reconciliation", "yes")])
def test_invalid_controls_are_rejected_before_runtime(field, value):
    with pytest.raises(ValueError):
        Settings(**{field: value})


@pytest.mark.parametrize("variant", ["ops-ready-loans", "ops-wait-linear3"])
def test_geometry_rejects_ops_controls_before_starting_a_world(tmp_path, monkeypatch, capsys, variant):
    from tools import run_laya_clean as cli

    monkeypatch.setattr(cli, "owned_root", lambda root: tmp_path)
    monkeypatch.setattr(cli, "saved_world", lambda *a: pytest.fail("world lifecycle must not run"))
    monkeypatch.setattr(cli, "file_hash", lambda *a: pytest.fail("runtime preparation must not run"))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["run", "--root", str(tmp_path), "--actions", "geometry", "--variant",
                  str(cli.ROOT / "docs/rl/laya_variants" / f"{variant}.json")])
    assert stopped.value.code == 2
    assert "require deterministic actions" in capsys.readouterr().err


@pytest.mark.parametrize("schedule", ["linear", "exponential", "sqrt"])
def test_runner_executes_and_records_scaled_waits_without_changing_the_stall_horizon(tmp_path, schedule):
    from training.laya_guided_policy import GuidedPolicy
    from training.laya_ops_runner import run, STALL_TICKS

    class World:
        root = None
        extra_recipes = ()
        tick = 1000
        def query(self):
            return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [],
                    "networks": {}, "ports": [], "resources": [], "recipes": {}}
        def advance(self, ticks):
            self.tick += ticks

    class WaitPolicy(GuidedPolicy):
        def choose(self, state, choices):
            return super().choose(state, {**choices, "options": [o for o in choices["options"] if o["family"] == "wait"]})

    class Session:
        def close(self): pass
        def run(self, *a, **kw): pytest.fail("only waits selected")

    settings = Settings(decision_wait_schedule=schedule, decision_wait_sqrt_minutes=1)
    result = run(World(), WaitPolicy(2), session=Session(), journal=tmp_path / "journal.jsonl", decisions=8,
                 measure_ticks=600, seed=2, run_id="pacing", settings=settings)
    rows = [json.loads(line) for line in (tmp_path / "decision-states.jsonl").read_text().splitlines()]
    selected = [next(o for o in r["options"] if o["option_id"] == r["choice"]["option_id"]) for r in rows]
    assert any(o["wait_ticks"] < o["base_wait_ticks"] for o in selected)
    assert all(r["outcome"]["ticks"] == o["wait_ticks"] for r, o in zip(rows, selected))
    assert result["progress_guard"]["stall_after_ticks"] == STALL_TICKS
    assert result["decision_timing"]["wait_multiplier"] > 1
    assert all(r["state"]["observation_digest"]["decision_pacing"]["clock"] == "elapsed_game_ticks" for r in rows)
