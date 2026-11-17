# Path: tests/test_c5_planned_rate_c6a.py | Purpose: c5-v2 (plastic at the declared planned rate, latched) and c6a-v1 (plastic used), their telemetry, readers and restart-bundle migration.
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator.checkpoint_evidence import bounded_history, evidence_success, new_evidence_state, validate_evidence_state
from orchestrator.checkpoint_milestones import (
    MILESTONE_IDS, CheckpointEvaluator, FactorySnapshot, PredicateStatus, evaluate_milestone,
)
from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter, declare_planned_rate, normalize_plastic
from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state, migrate_reached
from tools.laya_strict_sidecar import Measurement
from training import laya_milestone_report as report
from training.laya_ops_runner import declare_plastic_target, planned_plastic_rate

pytestmark = pytest.mark.fast

TARGET = {"rate": 0.5, "tick": 10, "source": "laya_ops_runner.status_board.required_per_s"}
CAPACITY = {"machines": 1, "crafting_speed": 1.0, "products_per_craft": 2, "energy": 1.0, "rate": 2.0}


def sample(tick, produced, *, target=TARGET, consumers=None):
    plastic = {"produced": produced, "declared_target": target, "capacity": CAPACITY, "consumers": consumers}
    return FactorySnapshot.from_mapping({"tick": tick, "plastic": plastic})


def c5(samples):
    return evaluate_milestone("C5", samples[-1], history=samples)


def test_c5_passes_at_ninety_percent_of_planned_rate_after_settle_and_records_both_references():
    # First production at tick 0; settled from 1800; window 1800 -> 5400 makes 27 bars in 60 s = 0.45/s.
    result = c5([sample(-60, 0), sample(0, 10), sample(1800, 20), sample(5400, 47)])
    assert result.passed and result.predicate_version == "c5-v4"
    window = result.evidence["window"]
    assert window["measured_rate"] == 0.45 and window["window_ticks"] == 3600
    assert window["planned_target"] == {"rate": 0.5, "tick": 10, "source": TARGET["source"]}
    assert window["installed_capacity"]["rate"] == 2.0
    assert window["ratio_to_target"] == 0.9 and window["ratio_to_capacity"] == 0.225


def test_c5_fails_at_sixty_percent_of_planned_rate():
    result = c5([sample(-60, 0), sample(0, 10), sample(1800, 20), sample(5400, 38)])  # 0.3/s
    assert result.failed and result.evidence["window"]["ratio_to_target"] == 0.6


def test_production_during_the_settle_does_not_count():
    # 490 bars inside the settle period, then nothing: the only settled window measures 0/s.
    result = c5([sample(-60, 0), sample(0, 10), sample(1700, 500), sample(1800, 500), sample(5400, 500)])
    assert result.failed and result.evidence["window"]["measured_rate"] == 0
    # A window that starts before the settle ends is never formed.
    assert c5([sample(-60, 0), sample(0, 10), sample(1799, 500), sample(3600, 520)]).failed


def test_missing_planned_target_is_unknown_never_guessed():
    result = c5([sample(-60, 0, target=None), sample(0, 10, target=None), sample(1800, 20, target=None), sample(5400, 200, target=None)])
    assert result.status is PredicateStatus.UNKNOWN and result.missing == ("planned_target",)


def test_full_chest_after_earning_keeps_c5_through_bounded_history_and_the_sidecar():
    earned = [sample(-60, 0), sample(0, 10), sample(1800, 20), sample(5400, 47)]
    flat = [sample(5400 + 600 * i, 47) for i in range(1, 41)]  # chest full: production stops for 40 polls
    measurement = Measurement({})
    for item in earned + flat:
        row = measurement.add(item)
    assert len(measurement.history) == 20
    assert row["statuses"]["C5"] is True
    assert measurement.summary("stopped")["strict_current_at_end"]["C5"] is True
    assert row["predicate_evidence"]["C5"]["evidence"]["historical"] is True
    # Trimming without the ledger would lose it; the bounded history is the newest 20, the oldest carrying the pass.
    assert c5((earned + flat)[-20:]).failed
    bounded = bounded_history(earned + flat)
    assert [s.tick for s in bounded] == [s.tick for s in (earned + flat)[-20:]]
    assert bounded[0].c5_ledger["passing"]["tick"] == 5400 and c5(bounded).passed


def test_plastic_collector_normalizes_capacity_and_empty_consumers():
    raw = {"produced": 7, "consumers": {}, "declared_target": TARGET,
           "capacity": {"machines": 2, "crafting_speed": 2, "products_per_craft": 2, "energy": 1}}
    out = normalize_plastic(raw)
    assert out["consumers"] == [] and out["capacity"]["rate"] == 4.0
    assert normalize_plastic({"produced": 1})["declared_target"] is None
    adapter = LiveCheckpointObservationAdapter.__new__(LiveCheckpointObservationAdapter)
    adapter.surface, adapter.force = "nauvis", "player"
    assert adapter._plastic(SimpleNamespace(command=lambda _: json.dumps(raw)))["capacity"]["rate"] == 4.0
    assert adapter._plastic(SimpleNamespace(command=lambda _: "not json")) is None


def test_runner_declares_the_board_plastic_rate_once_per_change():
    commands = []
    client = SimpleNamespace(command=lambda lua: commands.append(lua) or json.dumps({"rate": 0.5, "tick": 9, "source": "s"}))
    board = {"needs": [{"item": "plastic-bar", "required_per_s": 0.5}]}
    assert planned_plastic_rate(board) == 0.5 and planned_plastic_rate({"needs": []}) is None
    declared = {}
    for _ in range(3):
        declare_plastic_target(SimpleNamespace(client=client), board, declared)
    assert len(commands) == 1 and "storage.laya_planned_rates" in commands[0]
    with pytest.raises(ValueError):
        declare_planned_rate(client, "plastic-bar", 0, "s")


def consumer(unit, recipe, count):
    return [{"unit": unit, "recipe": recipe, "count": count}]


def test_c6a_passes_only_on_a_non_chest_plastic_consumer_crafting_after_baseline():
    base = sample(0, 0, consumers=[])
    first = sample(60, 0, consumers=consumer(5, "advanced-circuit", 900))  # inherited lifetime count: baseline only
    later = sample(120, 0, consumers=consumer(5, "advanced-circuit", 903))
    assert evaluate_milestone("C6a", first, baseline=base, history=[base, first]).failed
    result = evaluate_milestone("C6a", later, baseline=base, history=[base, first, later])
    assert result.passed and result.evidence["machine_consumption"] == {"advanced-circuit": 3}
    chest = [sample(60, 0, consumers=consumer(6, "requester-chest", 1)), sample(120, 0, consumers=consumer(6, "requester-chest", 5))]
    assert evaluate_milestone("C6a", chest[-1], baseline=base, history=[base, *chest]).failed
    assert evaluate_milestone("C6a", later, history=[base, first, later]).unknown  # no baseline
    assert evaluate_milestone("C6a", sample(180, 0), baseline=base, history=[base, sample(180, 0)]).unknown


def test_readers_order_c6a_and_accept_journals_with_and_without_it():
    assert MILESTONE_IDS.index("C5") < MILESTONE_IDS.index("C6a") < MILESTONE_IDS.index("C6")
    old = [{"event": "strict_current", "tick": t, "statuses": {"C5": s, "C6": False}} for t, s in ((1, False), (2, True))]
    new = [{"event": "strict_current", "tick": t, "statuses": {"C5": s, "C6a": s, "C6": False},
            "predicate_evidence": {"C5": {"predicate_version": "c5-v4"}, "C6a": {"predicate_version": "c6a-v1"}}}
           for t, s in ((1, False), (2, True))]
    assert report.from_journal_rows(old)["earned"] == ["C5"]
    block = report.from_journal_rows(new)
    assert block["earned"] == ["C5", "C6a"]
    assert block["predicate_versions"] == {"C5": "c5-v4", "C6a": "c6a-v1"}


def test_evidence_state_written_before_c6a_is_still_valid_and_advances():
    state = new_evidence_state()
    statuses = {cid: "failed" for cid in MILESTONE_IDS if cid != "C6a"}
    state.update(last_statuses=statuses, last_tick=0, current_available=True, gap=False)
    validate_evidence_state(state)
    samples = [sample(-60, 0), sample(0, 10), sample(1800, 20), sample(5400, 47)]
    evidence_success(state, samples[0], samples)
    assert "C6a" in state["last_statuses"] and state["events"]["C5"]["predicate_version"] == "c5-v4"


def old_bundle(tmp_path, reached):
    """A bundle as captured before c5-v2/C6a: c5-v1 in the milestone list, no C6a, no plastic field."""
    start = FactorySnapshot.from_mapping({"tick": 0, "base_valid": True})
    evaluator = CheckpointEvaluator()
    evaluator.reached.update(reached)
    bundle = export_restart_state(tmp_path / "source", "episode", "mods", start, [start], evaluator)
    bundle["milestones"] = [{**m, "predicate_version": "c5-v1"} if m["id"] == "C5" else m
                            for m in bundle["milestones"] if m["id"] != "C6a"]
    assert "plastic" not in bundle["history"][0]
    return bundle


def test_restart_bundle_that_reached_c5_v1_drops_it_to_be_reearned(tmp_path):
    bundle = old_bundle(tmp_path, {"C0", "C4", "C5"})
    state = import_restart_state(copy.deepcopy(bundle), tmp_path / "target", episode_id="episode", mod_scope="mods")
    assert state.evaluator.reached == {"C0", "C4"}
    assert state.reearned == ({"id": "C5", "from_version": "c5-v1", "to_version": "c5-v4"},)
    expected, records = migrate_reached(bundle)
    again = export_restart_state(tmp_path / "target", "episode", "mods", state.baseline, list(state.history), state.evaluator)
    assert again["reached"] == expected["reached"] == ["C0", "C4"] and records == list(state.reearned)
    assert again["history"] == bundle["history"]
    # A reached milestone whose version changed without a declared re-earn is still refused.
    changed = old_bundle(tmp_path / "other", {"C0", "C4"})
    changed["milestones"] = [{**m, "predicate_version": "c4-v0"} if m["id"] == "C4" else m for m in changed["milestones"]]
    with pytest.raises(ValueError, match="Strict milestone definitions differ"):
        import_restart_state(changed, tmp_path / "other-target", episode_id="episode", mod_scope="mods")


def test_a_lifetime_counter_carried_by_the_save_is_not_production():
    # Live tip-c3-120 shape (17 November): the C0 save loads with a positive plastic counter and no plants.
    flat = [sample(238_626 + 1800 * i, 466) for i in range(6)]
    result = c5(flat)
    assert result.failed and result.evidence["first_production_tick"] is None
    assert result.evidence["checks"]["plastic_production_started"] is False
    # Production starts at the first rise, not at the first positive sample.
    rising = flat + [sample(250_000, 470), sample(252_000, 500), sample(254_000, 540)]
    assert c5(rising).evidence["first_production_tick"] == 250_000


def test_windows_before_the_declaration_are_judged_so_c5_reads_failed_then_is_credited():
    # The runner declared its target after the first windows; v2 stayed unknown until a pass and was never credited.
    late = {**TARGET, "tick": 7200}
    rows = ([sample(-60, 0, target=None), sample(0, 10, target=None), sample(1800, 12, target=None),
             sample(3600, 14, target=None), sample(7200, 16, target=late)]
            + [sample(9000, 16, target=late), sample(10_800, 50, target=late)])
    early = c5(rows[:4])
    assert early.status is PredicateStatus.UNKNOWN and early.missing == ("planned_target",)
    stalled = c5(rows[:6])
    assert stalled.failed and stalled.evidence["window"]["target_declared_after_window"] is False
    assert c5(rows[:5]).evidence["window"]["planned_target"] == late
    measurement = Measurement({})
    for item in rows:
        measurement.add(item)
    assert measurement.summary("stopped")["strict_reached"].get("C5") == 10_800


def _incremental(rows):
    history = []
    for row in rows:
        history = bounded_history([*history, row])
    return history


def test_dense_sampling_still_forms_a_settled_window():
    # Review P1: 100 samples one second apart; bounded to 20, the start used to move forward forever.
    rows = [sample(60 * i, i) for i in range(100)]
    assert c5(rows).passed
    history = _incremental(rows)
    assert len(history) == 20 and c5(history).passed
    assert c5(history).evidence["first_production_tick"] == 60


def test_a_late_declared_target_stays_pinned_when_the_target_changes():
    # Review P2: the pass was judged against a target declared after its window; a later 10/s target
    # and trimming must not re-judge it.
    first = {**TARGET, "tick": 4000}
    later = {**TARGET, "rate": 10.0, "tick": 6000}
    rows = ([sample(-60, 0, target=None), sample(0, 1, target=None), sample(1800, 10, target=None),
             sample(3600, 30, target=None), sample(4000, 30, target=first)]
            + [sample(6000 + 600 * i, 30, target=later) for i in range(30)])
    assert c5(rows[:5]).passed
    history = _incremental(rows)
    result = c5(history)
    assert result.passed and result.evidence["window"]["planned_target"] == first
    assert result.evidence["window"]["target_declared_after_window"] is True


def test_a_restart_before_the_first_pass_keeps_the_settle_anchor(tmp_path):
    # Captured mid-settle under dense sampling; the restored history must still earn C5 afterwards.
    rows = [sample(60 * i, i) for i in range(40)]
    history = _incremental(rows)
    assert not c5(history).passed
    evaluator = CheckpointEvaluator()
    bundle = export_restart_state(tmp_path / "source", "episode", "mods", rows[0], history, evaluator)
    state = import_restart_state(copy.deepcopy(bundle), tmp_path / "target", episode_id="episode", mod_scope="mods")
    resumed = _incremental([*state.history, *(sample(60 * i, i) for i in range(40, 100))])
    result = c5(resumed)
    assert result.passed and result.evidence["first_production_tick"] == 60
