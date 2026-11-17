# Path: tests/test_c5_bounded_exactness.py | Purpose: C5 evaluated over bounded (20-sample) history, incrementally and across restart bundles, equals C5 over the full history at every observation.
import copy
import random

import pytest

from orchestrator.checkpoint_evidence import HISTORY_LIMIT, bounded_history, retained_samples
from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot, evaluate_milestone
from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state, migrate_reached

pytestmark = pytest.mark.fast

TARGET = {"rate": 0.5, "tick": 10, "source": "laya_ops_runner.status_board.required_per_s"}
CAPACITY = {"machines": 1, "crafting_speed": 1.0, "products_per_craft": 2, "energy": 1.0, "rate": 2.0}


def sample(tick, produced, *, target=TARGET):
    plastic = {"produced": produced, "declared_target": target, "capacity": CAPACITY, "consumers": None}
    return FactorySnapshot.from_mapping({"tick": tick, "plastic": plastic})


def c5(samples):
    return evaluate_milestone("C5", samples[-1], history=samples)


def comparable(result):
    """Status, missing facts and evidence; ``sample_count`` is the size of the history given, by design."""
    evidence = {key: value for key, value in result.evidence.items() if key != "sample_count"}
    return result.status.value, result.missing, evidence


def restart(history, tmp_path, label):
    """Export the bounded history as a restart bundle and import it into a fresh target."""
    bundle = export_restart_state(tmp_path / f"{label}-source", "episode", "mods", history[0], list(history),
                                  CheckpointEvaluator())
    return list(import_restart_state(copy.deepcopy(bundle), tmp_path / f"{label}-target",
                                     episode_id="episode", mod_scope="mods").history)


def replay(rows, tmp_path=None, restart_at=()):
    """Feed rows one at a time; assert bounded == full after every row. Returns the first-passed tick."""
    history, earned = [], None
    for index, row in enumerate(rows):
        history = bounded_history([*history, row])
        assert len(history) <= HISTORY_LIMIT
        if index in restart_at:
            history = restart(history, tmp_path, f"r{index}")
        # An exact repeat adds a duplicate window to the raw prefix's window count, never a status change.
        bounded, full = c5(history), c5(retained_samples(rows[:index + 1]))
        assert comparable(bounded) == comparable(full), f"observation {index} at tick {row.tick}"
        assert full.status is c5(rows[:index + 1]).status
        if earned is None and full.passed:
            earned = row.tick
    return earned


def test_dense_constant_production_earns_at_the_full_history_tick(tmp_path):
    rows = [sample(60 * i, i) for i in range(100)]
    assert replay(rows, tmp_path, restart_at={25, 61, 70}) == 3660


def test_a_flat_period_does_not_dilute_a_later_recovery(tmp_path):
    # Review re-check of cb897a9b: production starts at 60, stays flat, resumes at 0.4/s from tick 10200.
    rows = [sample(60 * i, 0 if i == 0 else max(1, i - 169)) for i in range(201)]
    assert replay(rows, tmp_path, restart_at={120, 185}) == 10920
    assert c5(rows).evidence["window"]["from_tick"] == 9120


def test_a_target_lowered_after_production_stops_earns_no_synthetic_pass(tmp_path):
    # Review re-check of cb897a9b: nothing meets 100/s; production stops at 3000; the target drops to 0.5/s at 6000.
    rows = [sample(60 * i, min(2 * i, 100), target={**TARGET, "rate": 100 if i < 100 else 0.5}) for i in range(101)]
    assert replay(rows, tmp_path, restart_at={40, 99}) is None
    assert c5(rows).failed and c5(rows).evidence["window"]["measured_rate"] == 0


def _stream(rng):
    """A seeded observation stream: mixed cadence, flat/varying/stopped production, targets that arrive late,
    change or vanish, and occasional repeated, paused or counterless samples or a clock regression."""
    tick, produced, rows = rng.randrange(-5000, 5000), rng.choice([0, 0, 466]), []
    rate = 0.0
    target = None if rng.random() < 0.6 else {**TARGET, "rate": round(rng.uniform(0.2, 2.5), 2)}
    cadence = rng.choice([(60, 300), (300, 2000), (2000, 20_000), (60, 20_000)])
    for _ in range(rng.randrange(30, 110)):
        roll = rng.random()
        if roll < 0.05 and rows:
            rows.append(rows[-1] if rng.random() < 0.5 else copy.deepcopy(rows[-1]))  # repeated observation
            continue
        if roll < 0.15:
            cadence = rng.choice([(60, 120), (60, 600), (1000, 5000), (5000, 20_000)])
        if roll < 0.3:
            rate = rng.choice([0.0, 0.0, rng.uniform(0.05, 3.0)])  # stopped or a new rate
        if rng.random() < 0.08:
            target = rng.choice([None, {**TARGET, "rate": round(rng.uniform(0.2, 2.5), 2), "tick": tick}])
        step = 0 if rng.random() < 0.04 else rng.randrange(*cadence)  # paused game: same tick
        tick += step if rng.random() > 0.01 else -rng.randrange(1, 30_000)  # a reloaded save
        produced += int(rate * max(step, 0) / 60 + rng.random())
        counter = None if rng.random() < 0.03 else produced
        rows.append(sample(tick, counter, target=target))
    return rows


def test_bounded_history_equals_full_history_on_random_streams(tmp_path):
    rng = random.Random(20261117)
    earned = 0
    for stream in range(24):
        rows = _stream(rng)
        restarts = set(rng.sample(range(len(rows)), 2)) if stream % 3 == 0 else set()
        earned += replay(rows, tmp_path / str(stream), restart_at=restarts) is not None
    assert earned >= 5  # the streams exercise passes, not only failures


def test_a_restart_bundle_refuses_a_misplaced_or_malformed_ledger(tmp_path):
    history = bounded_history([sample(60 * i, i) for i in range(40)])
    assert history[0].c5_ledger is not None and all(s.c5_ledger is None for s in history[1:])
    bundle = export_restart_state(tmp_path / "source", "episode", "mods", history[0], history, CheckpointEvaluator())
    moved = copy.deepcopy(bundle)
    moved["history"][1]["c5_ledger"] = moved["history"][0].pop("c5_ledger")
    broken = copy.deepcopy(bundle)
    broken["history"][0]["c5_ledger"]["begins"] = [[1, "x"]]
    for index, bad in enumerate((moved, broken)):
        with pytest.raises(ValueError, match="C5 ledger"):
            import_restart_state(bad, tmp_path / f"target-{index}", episode_id="episode", mod_scope="mods")


def test_no_window_spans_a_clock_regression(tmp_path):
    # c5-v4 differs from c5-v3 only here: the save reloads from 2000 to 1000, and c5-v3 passed 2000 -> 3900 across it.
    rows = [sample(0, 0), sample(60, 10), sample(2000, 50), sample(1000, 50), sample(3900, 80), sample(5800, 90),
            sample(7700, 200)]
    assert c5(rows[:5]).evidence["window_count"] == 0 and c5(rows[:6]).failed
    assert replay(rows, tmp_path, restart_at={3}) == 7700
    assert c5(rows).evidence["window"]["from_tick"] == 5800


def test_a_bundle_that_reached_c5_v3_re_earns_it_under_c5_v4():
    bundle = {"reached": ["C4", "C5"], "milestones": [{"id": "C5", "predicate_version": "c5-v3"}]}
    migrated, records = migrate_reached(bundle)
    assert migrated["reached"] == ["C4"] and records == [{"id": "C5", "from_version": "c5-v3", "to_version": "c5-v4"}]
