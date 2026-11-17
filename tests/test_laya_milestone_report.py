# Path: tests/test_laya_milestone_report.py | Purpose: every milestone reader reports earned / holds-now / inherited from one block, and labels the fallback for journals that predate the evidence contract.
import json
from datetime import datetime, timedelta, timezone

import pytest

import tools.laya_experiment as experiment
import tools.run_ledger as run_ledger
from tools import laya_cohort_dashboard
from tools import laya_regression_scheduler as scheduler
from tools.run_laya_batch import summarize
from training import laya_milestone_report as report
from training.laya_ops_runner import run

pytestmark = pytest.mark.fast


def poll(tick, **statuses):
    return {"event": "strict_current", "tick": tick, "loaded_tick": 100, "statuses": statuses,
            "elapsed_seconds": 1, "decisions": 1}


# Recorded shape of C95b: C0 passes at the first poll, C2 is earned, C5 is earned then lost.
POLLS = [poll(100, C0=True, C1=False, C2=False, C5=False), poll(200, C0=True, C1=False, C2=True, C5=False),
         poll(300, C0=True, C1=False, C2=True, C5=True), poll(400, C0=True, C1=False, C2=True, C5=False)]


LEGACY_KEYS = ("source", "earned", "holds_now", "inherited")  # the block's original shape; new fields are additive


def contract(earned, present, inherited):
    return {"version": "checkpoint-evidence-v1",
            "observed_events": [{"kind": "predicate_transition", "checkpoint": c, "tick": t} for c, t in earned],
            "present": {c: {"status": s} for c, s in present.items()},
            "inherited_start": {"predicates": {c: {"status": s} for c, s in inherited.items()},
                                "provenance": "first_observation_not_earned"}}


CONTRACT_ROW = {**poll(400, C0=True, C2=True), "predicate_evidence": {"_contract": contract(
    [("C2", 200), ("C5", 300)], {"C0": "passed", "C2": "passed", "C5": "failed"}, {"C0": "passed", "C2": "failed"})}}


def test_polls_separate_earned_from_inherited_and_held():
    block = report.from_polls(POLLS, loaded_tick=100)
    assert {k: block[k] for k in LEGACY_KEYS} == {"source": "legacy_polls", "earned": ["C2", "C5"],
                                                  "holds_now": ["C0", "C2"], "inherited": ["C0"]}
    assert [(e["id"], e["tick"]) for e in block["events"]] == [("C2", 200), ("C5", 300)]


def test_an_unknown_poll_does_not_make_an_earned_pass():
    rows = [poll(100, C2=None), poll(200, C2=True)]
    assert report.from_polls(rows)["earned"] == []


def test_contract_wins_over_polls_and_ignores_events_before_load():
    rows = POLLS + [CONTRACT_ROW]
    block = report.from_journal_rows(rows, loaded_tick=250, ever=["C0"], final=["C0"])
    assert block["source"] == "contract" and block["earned"] == ["C5"]
    assert block["holds_now"] == ["C0", "C2"] and block["inherited"] == ["C0"]


def test_latch_is_the_labelled_last_resort_and_text_says_unknown():
    block = report.from_journal_rows([], ever=["C0", "C2"], final=None)
    assert block == {"source": "legacy_latch", "earned": ["C0", "C2"], "holds_now": None, "inherited": None}
    assert report.text(block) == "earned=C0,C2 holds_now=unknown inherited=unknown source=legacy_latch"


def write_world(tmp_path, rows):
    (tmp_path / "evidence").mkdir()
    (tmp_path / "evidence/clean-decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path


def test_scheduler_uses_contract_for_accepted_ever(tmp_path):
    latch = {"event": "acceptance", "tick": 100, "statuses": {"C0": "passed", "C2": "passed"}}
    result = scheduler.collect_result(write_world(tmp_path, [latch] + POLLS + [CONTRACT_ROW]), {"strict_target": "C2"})
    assert result["accepted_ever_basis"] == "earned_events" and result["accepted_ever"] == ["C2", "C5"]
    assert result["milestones"]["source"] == "contract" and result["milestones"]["holds_now"] == ["C0", "C2"]


def test_scheduler_labels_the_latch_fallback_and_still_reports_polls(tmp_path):
    latch = {"event": "acceptance", "tick": 100, "statuses": {"C0": "passed", "C2": "passed", "C5": "passed"}}
    result = scheduler.collect_result(write_world(tmp_path, [latch] + POLLS), {"strict_target": "C2"})
    assert result["accepted_ever_basis"] == "legacy_latch" and result["accepted_ever"] == ["C0", "C2", "C5"]
    assert {k: result["milestones"][k] for k in LEGACY_KEYS} == {
        "source": "legacy_polls", "earned": ["C2", "C5"], "holds_now": ["C0", "C2"], "inherited": ["C0"]}
    assert result["strict_reached"].keys() == {"C0", "C2", "C5"}  # old reach logic (incl. inherited C0) is unchanged


def test_runner_summary_line_names_evidence_and_labels_the_proxy(tmp_path):
    class Session:
        def acceptance(self):
            return {"C0": "passed", "C1": "failed"}
        acceptance_fresh = {"C0": "passed", "C1": "failed"}

        def close(self):
            pass

    lines = []
    summary = run(object(), object(), journal=tmp_path / "journal.jsonl", decisions=0, measure_ticks=600, seed=1,
                  run_id="r", session=Session(), emit=lines.append)
    line = next(x for x in lines if x.startswith("SUMMARY:"))
    assert " earned=" in line and " holds_now=" in line and " inherited=" in line and " proxy_phases=" in line
    assert " phases=" not in line and "phases_reached" in summary and summary["milestones"]["source"] == "contract"
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(line + "\n")
    parsed = summarize(tmp_path)  # the batch log parser reads the new names
    assert parsed["accepted_ever"] == ["C0"] and parsed["earned"] == [] and parsed["holds_now"] is None


def test_batch_parser_still_reads_the_old_summary_line(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs/laya-runner.log").write_text(
        "SUMMARY: stop=stalled decisions=3 failures=0 phases=C0,C1 accepted=C0 accepted_final=C0\n")
    parsed = summarize(tmp_path)
    assert parsed["phases"] == ["C0", "C1"] and parsed["accepted_final"] == ["C0"] and "earned" not in parsed


def test_experiment_score_counts_earned_and_flags_runs_without_the_block():
    block = {"source": "contract", "earned": ["C2"], "holds_now": [], "inherited": ["C0"]}
    rows = [{"summary": {"phases_reached": ["C0", "C1", "C2"], "milestones": block}},   # proxy and evidence agree
            {"summary": {"phases_reached": ["C0", "C1", "C2"], "milestones": {**block, "earned": []}}},  # proxy only
            {"summary": {"phases_reached": ["C0", "C1", "C2"], "accepted": ["C0"]}}]   # old journal
    score = experiment.score(rows, "C2")
    assert score["reached"] == 2 and score["proxy_phases_reached"] == 3 and score["proxy_fallback"] == 1
    assert score["accepted"] == 1 and score["held_at_end"] == 0


def test_ledger_reads_earned_events_and_falls_back_to_the_proxy(tmp_path):
    summary_row = {"event": "summary", "run_id": "r", "milestones": report.from_contract(CONTRACT_ROW["predicate_evidence"]["_contract"])}
    rows = [{"event": "phase_reached", "run_id": "r", "phase": "C1", "tick": 5},
            {**CONTRACT_ROW, "run_id": "r"}, summary_row]
    root = write_world(tmp_path, rows)
    entry = run_ledger.laya_entry({"run_id": "r", "root": str(root), "summary": {}})
    assert [m["id"] for m in entry["milestones"]] == ["C2", "C5"] and entry["milestones"][0]["tick"] == 200
    assert entry["milestone_source"] == "earned_evidence" and entry["holds_at_end"] == ["C0", "C2"]
    old = write_world(tmp_path / "old", [rows[0]]) if (tmp_path / "old").mkdir() is None else None
    legacy = run_ledger.laya_entry({"run_id": "r", "root": str(old), "summary": {}})
    assert legacy["milestone_source"] == "proxy_goal_ladder" and legacy["highest_milestone"] == "C1"


def test_curated_history_is_marked_stale_when_old_and_never_generated():
    old = (datetime.now(timezone.utc) - timedelta(days=18)).isoformat()
    status = laya_cohort_dashboard.curated_status({"updated_at": old})
    assert status["stale"] is True and status["generated"] is False and 17 < status["age_days"] < 19
    fresh = laya_cohort_dashboard.curated_status({"updated_at": datetime.now(timezone.utc).isoformat()})
    assert fresh["stale"] is False
    assert laya_cohort_dashboard.curated_status({})["stale"] is True


def test_ledger_replays_polls_for_journals_without_the_block(tmp_path):
    rows = [{"event": "phase_reached", "run_id": "r", "phase": "C1", "tick": 5}] + [{**p, "run_id": "r"} for p in POLLS]
    entry = run_ledger.laya_entry({"run_id": "r", "root": str(write_world(tmp_path, rows)), "summary": {}})
    assert [m["id"] for m in entry["milestones"]] == ["C2", "C5"] and entry["milestone_source"] == "earned_from_polls"
    assert entry["inherited"] == ["C0"] and entry["highest_milestone"] == "C5"


def test_experiment_report_rebuilds_the_block_for_old_index_rows(tmp_path):
    root = write_world(tmp_path, POLLS)
    row = {"root": str(root), "summary": {"phases_reached": ["C0", "C1", "C2", "C3"]}}
    rebuilt = experiment.with_milestones(row)
    assert rebuilt["summary"]["milestones"]["source"] == "legacy_polls"
    score = experiment.score([rebuilt], "C1")
    assert score["reached"] == 0 and score["proxy_phases_reached"] == 1 and score["proxy_fallback"] == 0
    assert experiment.with_milestones({"summary": {}}) == {"summary": {}}  # no root, no journal: unchanged


# -- observation gaps, event ticks and evidence timing (review R9, R10, R11) ---------------------------------------
@pytest.mark.parametrize("middle", [{}, {"C1": None}, {"C1": None, "C2": None}])
def test_a_missing_or_all_unknown_poll_fences_the_transition(middle):
    rows = [poll(1, C1=False), {"event": "strict_current", "tick": 2, "statuses": middle}, poll(3, C1=True)]
    block = report.from_polls(rows)
    assert block["earned"] == [] and block["events"] == [] and block["holds_now"] == ["C1"]


def test_a_tickless_poll_is_a_boundary_too():
    rows = [poll(1, C1=False), {"event": "strict_current", "tick": None, "statuses": {"C1": False}}, poll(3, C1=True)]
    assert report.from_polls(rows)["earned"] == []


def test_partial_unknowns_only_block_the_unknown_milestone():
    rows = [poll(1, C1=False, C2=None, C3=False), poll(2, C1=True, C2=True, C3=True)]
    block = report.from_polls(rows)
    assert block["earned"] == ["C1", "C3"] and [e["tick"] for e in block["events"]] == [2, 2]


def test_adjacent_known_failed_predecessor_is_credited_with_its_tick():
    block = report.from_polls([{**poll(1, C1=False), "loaded_tick": None}, {**poll(3, C1=True), "loaded_tick": None}])
    assert block["events"] == [{"id": "C1", "tick": 3, "from_tick": 1}] and block["origin_tick"] == 1
    assert block["origin_basis"] == "first_poll"


def test_duplicate_ticks_and_clock_regressions_credit_nothing():
    assert report.from_polls([poll(5, C1=False), poll(5, C1=True)])["earned"] == []
    assert report.from_polls([poll(9, C1=False), poll(4, C1=True)])["earned"] == []
    # After a regression the next advancing poll compares with the regressed one (as the accumulator does).
    block = report.from_polls([poll(9, C1=True), poll(4, C1=False), poll(6, C1=True)])
    assert block["earned"] == ["C1"] and block["events"][0]["from_tick"] == 4


def test_polls_and_contract_agree_on_a_gap_fixture():
    # The accumulator never credits across a failed poll; the replay of the same recorded stream must agree.
    from orchestrator.checkpoint_evidence import evidence_failure, evidence_success, new_evidence_state
    from orchestrator.checkpoint_milestones import FactorySnapshot
    state = new_evidence_state()
    before, after = FactorySnapshot(tick=1, base_valid=False), FactorySnapshot(tick=3, base_valid=True)
    evidence_success(state, before, [before])
    evidence_failure(state)
    evidence_success(state, before, [before, after])
    assert "C0" not in state["events"]
    rows = [poll(1, C0=False), {"event": "strict_current", "tick": 2, "statuses": {}}, poll(3, C0=True)]
    assert report.from_polls(rows)["earned"] == []


def test_contract_block_carries_event_ticks_and_the_load_origin():
    block = report.from_contract(contract([("C2", 200), ("C5", 300)], {"C2": "passed"}, {"C2": "failed"}), loaded_tick=100)
    assert [(e["id"], e["tick"]) for e in block["events"]] == [("C2", 200), ("C5", 300)]
    assert report.event_ticks(block, "C5") == (200, "loaded_tick") and report.event_ticks(block, "C3") == (None, None)
    assert report.event_ticks(report.from_contract(contract([("C2", 200)], {}, {})), "C2") == (None, None)  # origin unknown


def test_ledger_keeps_the_transition_tick_for_legacy_journals(tmp_path):
    rows = [{**poll(1, C1=False), "run_id": "r"}, {**poll(3, C1=True), "run_id": "r"}]
    entry = run_ledger.laya_entry({"run_id": "r", "root": str(write_world(tmp_path, rows)), "summary": {}})
    assert entry["milestones"] == [{"id": "C1", "tick": 3}]


def test_ledger_has_no_event_across_a_gap(tmp_path):
    rows = [{**poll(1, C1=False), "run_id": "r"}, {"event": "strict_current", "run_id": "r", "tick": 2, "statuses": {}},
            {**poll(3, C1=True), "run_id": "r"}]
    entry = run_ledger.laya_entry({"run_id": "r", "root": str(write_world(tmp_path, rows)), "summary": {}})
    assert entry["milestones"] == []


def test_experiment_speed_comes_from_the_evidence_event_not_the_proxy():
    block = report.from_contract(contract([("C1", 100)], {"C1": "passed"}, {}), loaded_tick=40)
    rows = [{"summary": {"milestones": block, "phase_ticks": {"C1": 10}}}]
    score = experiment.score(rows, "C1")
    assert score["median_ticks"] == 60 and score["timed"] == 1 and score["timed_of"] == 1
    assert score["timing_origins"] == ["loaded_tick"] and score["proxy_median_ticks"] == 10 and score["proxy_timed"] == 1


def test_experiment_leaves_missing_event_time_unknown_and_visible_in_the_denominator():
    timed = report.from_contract(contract([("C1", 100)], {}, {}), loaded_tick=0)
    untimed = report.from_contract(contract([("C1", 100)], {}, {}))  # no loaded tick: origin unknown
    rows = [{"summary": {"milestones": timed, "phase_ticks": {"C1": 10}}},
            {"summary": {"milestones": untimed, "phase_ticks": {"C1": 10}}}]
    score = experiment.score(rows, "C1")
    assert score["reached"] == 2 and (score["timed"], score["timed_of"]) == (1, 2) and score["median_ticks"] == 100
    only_untimed = experiment.score(rows[1:], "C1")
    assert only_untimed["median_ticks"] is None and only_untimed["proxy_median_ticks"] == 10


def test_experiment_proxy_only_runs_report_no_evidence_timing():
    rows = [{"summary": {"phases_reached": ["C1"], "phase_ticks": {"C1": 10}}}]
    score = experiment.score(rows, "C1")
    assert score["reached"] == 1 and score["median_ticks"] is None and score["timed_of"] == 0
    assert score["proxy_median_ticks"] == 10
