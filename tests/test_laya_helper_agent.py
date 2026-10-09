# Path: tests/test_laya_helper_agent.py | Purpose: Protect the post-run Laya Helper: run selection, bounded packet, read-only prompt and completion line.

import json
import stat
from pathlib import Path

import pytest

import tools.laya_helper_agent as helper


def write_run(root, run_id, decisions=2):
    (root / "logs").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "logs/laya-runner.log").write_text(
        f"RUN START: ts=2026-10-03T20:00:00+05:30 run_id=other\n+1s DECISION 0\n+2s RUN END\n"
        f"RUN START: ts=2026-10-03T21:00:00+05:30 run_id={run_id}\n+1s DECISION 0 phase=C0\n"
        "+1s   STATUS: same\n+5s   STATUS: same\n+9s PHASE REACHED: C0 iron_copper_starter\n"
        "+9s ELAPSED: wall_seconds=1312 game_seconds=78540\n+9s RUN END\n")
    rows = [{"event": "decision", "run_id": run_id, "index": i, "status": "observed", "phase": "C0",
             "choice": {"need_id": "item:iron-plate"}} for i in range(decisions)]
    rows.append({"event": "summary", "run_id": run_id, "stop": "stopped", "decisions": decisions})
    (root / "evidence/clean-decisions.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def setup(tmp_path, monkeypatch):
    for name, value in {"RUN_INDEX": tmp_path / "runs.jsonl", "REPORT_ROOT": tmp_path / "reports",
                        "DATA_ROOT": tmp_path / "data", "REPO_ROOT": tmp_path}.items():
        monkeypatch.setattr(helper, name, value)
    det = tmp_path / "det.log"
    det.write_text("RUN START: ts=x\n+1s CHEMICAL LADDER: plastic-bar waits\n+9s STUCK: no progress\n+9s RUN END\n")
    config = tmp_path / "laya_helper.json"
    config.write_text(json.dumps({"model": "m", "variant": "v", "deterministic_reference": {
        "label": "ref", "log": str(det), "findings": "f.md", "note": "reference note"}}))
    monkeypatch.setattr(helper, "CONFIG", config)
    runs = []
    for run_id, commit, ended in [("old", "aaa", "1"), ("same-commit", "bbb", "2"), ("now", "bbb", "3")]:
        write_run(tmp_path / run_id, run_id)
        runs.append({"run_id": run_id, "root": str(tmp_path / run_id), "commit": commit, "ended": ended})
    (tmp_path / "runs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in runs))
    return runs


def test_previous_run_is_the_latest_from_an_earlier_commit(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    assert helper.previous_run(runs[2], runs)["run_id"] == "old"
    assert helper.previous_run(runs[0], runs) is None


def test_packet_holds_both_laya_runs_the_reference_and_the_workflow_note(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    lines = helper.run_log_slice(tmp_path / "now/logs/laya-runner.log", "now")
    assert lines[0].startswith("RUN START") and "run_id=now" in lines[0] and lines[-1].endswith("RUN END")
    assert helper.compact_log(lines).count("STATUS: same") == 1  # Repeats collapse.
    long = [f"+{i}s DECISION {i} " + "x" * 200 for i in range(2000)] + ["+9s SAFE STOP: done"]
    assert len(helper.compact_log(long, budget=5000)) < 7000 and "SAFE STOP" in helper.compact_log(long, budget=5000)
    packet = helper.build_packet(runs[2], runs[0], helper.load_config())
    assert "different workflows" in packet and "run_id=now" in packet and "run_id=old" in packet
    assert "CHEMICAL LADDER" in packet and "reference note" in packet



_FAKE_WRITE_FINDINGS = r"""python3 - "$last" <<'FAKEPY'
import re, sys
from pathlib import Path
found = re.search(r"Write (.*?) with these sections:", sys.argv[1])
if found:
    report = Path(found.group(1))
    report.write_text(report.read_text() + "\n## Outcome\nObserved terminal evidence.\n")
FAKEPY
"""

def test_review_writes_findings_through_one_read_only_opencode_session(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho '{\"sessionID\": \"ses_1\"}'\nfor a; do last=$a; done\n"
                    "echo \"$last\" > \"$(dirname \"$0\")/prompt.txt\"\n")
    fake.write_text(fake.read_text() + _FAKE_WRITE_FINDINGS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(helper, "OPENCODE", [str(fake)])
    report = helper.review("now")
    assert report == tmp_path / "reports/now/findings.md" and report.is_file()
    message = (tmp_path / "prompt.txt").read_text()
    assert "read-only" in message and "context management" in message and "fine-tuning" in message
    assert "do not edit code" in message


def test_live_observer_checkpoints_then_reviews_in_the_same_session(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho '{\"sessionID\": \"ses_live\"}'\nfor a; do last=$a; done\n"
                    "echo '---CALL---' >> \"$(dirname \"$0\")/calls.txt\"\necho \"$@\" >> \"$(dirname \"$0\")/calls.txt\"\n")
    fake.write_text(fake.read_text() + _FAKE_WRITE_FINDINGS)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(helper, "OPENCODE", [str(fake)])
    root = tmp_path / "now"  # RUN END already logged and no runner PID record: one checkpoint, then the review.
    report = helper.observe("now", root / "logs/laya-runner.log", root, interval=1)
    calls = (tmp_path / "calls.txt").read_text().split("---CALL---")[1:]
    assert report.is_file() and len(calls) == 2
    assert "live observer" in calls[0] and "--session ses_live" in calls[1] and "Live observations" in calls[1]
    assert (tmp_path / "data/now/checkpoint-001.md").is_file()


def test_journal_summary_accepts_ops_decision_rows_without_status(tmp_path):
    # The ops runner records decisions with a summary but no status; the final review must not fail on them.
    journal = tmp_path / "clean-decisions.jsonl"
    rows = [{"event": "decision", "run_id": "r", "index": "0", "summary": "extend copper-plate",
             "choice": {"need_id": "item:copper-plate", "option_id": "op.extend.copper-plate", "probability": 0.7}},
            {"event": "acceptance", "run_id": "r", "at": "end", "statuses": {"C2": "passed"}}]
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    summary = helper.journal_summary(journal, "r")
    assert summary["decisions"] == 1 and summary["top_choices"] == {"extend copper-plate": 1}


def acceptance_summary(**overrides):
    """A real ops-run shape: latched statuses plus fresh per-checkpoint results."""
    summary = {
        "decisions": 34, "failures": 0, "phases_reached": ["C0", "C1"], "stop": "stalled",
        "acceptance": {"C0": "passed", "C1": "passed", "C2": "failed", "C3": "failed",
                       "C4": "failed", "C5": "unknown", "C6": "failed"},
        "accepted": ["C0", "C1"],
        "phase_ticks": {"C0": 12509, "C1": 53279},
        "acceptance_details": {
            "version": "laya-acceptance-v1", "path": "/does/not/exist.json", "tick": 78000, "tick_end": 78002,
            "results": {
                "C0": {"predicate_version": "c0-v1", "status": "passed", "latched_status": "passed",
                       "checks": {"base_valid": True}, "missing": []},
                "C1": {"predicate_version": "c1-v2", "status": "failed", "latched_status": "passed",
                       "checks": {"at_least_four_working_mall_assemblers": False},
                       "working_mall_count": 4, "missing": []},
                "C2": {"predicate_version": "c2-v1", "status": "failed", "latched_status": "failed",
                       "checks": {"iron_foundation_ready": False}, "missing": []},
            },
            "predicate_comparisons": {
                "C1": {"c1-v1": {"predicate_version": "c1-v1", "status": "failed",
                                  "checks": {"at_least_four_working_mall_assemblers": False},
                                  "evidence": {"working_mall_count": 2}}},
            },
        },
    }
    summary.update(overrides)
    return summary


def test_packet_reports_latched_accepted_beside_current_c1(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    runs[2]["summary"] = acceptance_summary()
    packet = helper.build_packet(runs[2], runs[0], helper.load_config())
    assert "ACCEPTED (latched passes, monotonic): C0, C1" in packet
    assert "C1 current=failed latched=passed predicate=c1-v2 accepted=yes" in packet
    assert "C0 current=passed latched=passed predicate=c0-v1 accepted=yes" in packet
    assert "C2 current=failed latched=failed predicate=c2-v1 accepted=no" in packet
    # The retired c1-v1 predicate stays visible without replacing the active c1-v2.
    assert "C1 c1-v1 current=failed" in packet and "working_mall_count=2" in packet
    assert "C1 current=failed latched=passed predicate=c1-v2 accepted=yes missing=[] working_mall_count=4" in packet
    assert "predicate=c1-v2" in packet


def test_packet_labels_game_time_separately_from_wall_time(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    packet = helper.build_packet(runs[2], runs[0], helper.load_config())
    assert "wall_seconds=1312" in packet
    assert "game_seconds=78540" in packet
    assert "Factorio ticks/60" in packet
    assert "Never compare a game-time figure to the wall run length" in packet


def test_previous_run_uses_provided_comparator_root_outside_isolated_index(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside-tree/prior-pair"
    write_run(outside, "prior-pair")
    record = {"run_id": "prior-pair", "root": str(outside), "commit": "ccc9999", "mode": "live",
              "started": "2026-10-09T20:00:00+05:30", "ended": "2.5", "kind": "laya",
              "experiment": "live-pair-20261009", "comparable_window": "seed-50616-live-pair", "summary": {}}
    indexed_copy = {**record, "root": str(tmp_path / "indexed-copy"), "commit": "wrong-index-root"}
    runs.append(indexed_copy)
    deterministic = {"run_id": "prior-deterministic", "root": str(outside), "commit": "ddd9999",
                     "ended": "9", "kind": "deterministic", "summary": {}}
    previous = helper.previous_run(runs[2], runs, comparators=[record, deterministic])
    assert previous["run_id"] == "prior-pair"  # A deterministic comparator is not a Laya previous run.
    assert previous["root"] == str(outside)
    declared_window = helper.previous_run(runs[2], runs)
    assert declared_window["run_id"] == "prior-pair"
    assert declared_window["root"] == str(tmp_path / "indexed-copy")
    packet = helper.build_packet(runs[2], previous, helper.load_config(), comparators=[record, deterministic])
    assert "run_id=prior-pair" in packet and str(outside) in packet
    assert "run_id=prior-deterministic" in packet


def test_latched_only_summary_keeps_current_unknown(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    runs[2]["summary"] = {"acceptance": {"C1": "passed"}, "accepted": ["C1"]}
    vector = helper.acceptance_vector(runs[2])
    assert vector["C1"]["current"] == "unknown"
    assert vector["C1"]["latched"] == "passed"
    assert vector["C1"]["accepted"] is True


def test_explicit_deterministic_comparator_supplies_packet_log(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    explicit_log = tmp_path / "deterministic-explicit.log"
    explicit_log.write_text("EXPLICIT COMPARATOR LOG\n")
    comparator = {"run_id": "det-20261010", "kind": "deterministic", "root": str(tmp_path / "det-root"),
                  "log": str(explicit_log), "findings": "exact-findings.md", "note": "matched paired run"}
    packet = helper.build_packet(runs[2], runs[0], helper.load_config(), comparators=[comparator])
    assert "EXPLICIT COMPARATOR LOG" in packet
    assert str(explicit_log) in packet
    assert "reference note" not in packet
    assert "exact-findings.md" in packet


def test_helper_records_completion_and_failure_states(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    def wrote_report(config, *args, **kwargs):
        report = Path(config["report_path"])
        report.write_text(report.read_text() + "\n## Outcome\nTerminal review.\n")
        return "ses"
    monkeypatch.setattr(helper, "_ask", wrote_report)
    helper.review("now")
    complete = json.loads((tmp_path / "data/now/helper-state.json").read_text())
    assert complete["status"] == "complete" and complete["run_id"] == "now"
    assert complete["report"].endswith("findings.md")

    def boom(*a, **k):
        raise RuntimeError("opencode failed")

    monkeypatch.setattr(helper, "_ask", boom)
    with pytest.raises(RuntimeError):
        helper.review("now")
    failed = json.loads((tmp_path / "data/now/helper-state.json").read_text())
    assert failed["status"] == "failed" and "opencode failed" in failed["error"]


def test_prompt_forbids_reinterpreting_accepted_as_proxies(tmp_path, monkeypatch):
    runs = setup(tmp_path, monkeypatch)
    runs[2]["summary"] = acceptance_summary()
    config = helper.load_config()
    report = tmp_path / "reports/now/findings.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    message = helper.prompt(runs[2], helper.previous_run(runs[2], runs), tmp_path / "packet.md", report, "commits")
    assert "latched" in message and "c1-v1" in message and "c1-v2" in message
    assert "game_seconds" in message and "wall_seconds" in message
    assert "proxy" in message


def test_helper_cli_can_write_reports_outside_frozen_code(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    report_root = tmp_path / 'campaign-reports'
    data_root = tmp_path / 'campaign-helper-state'
    config = tmp_path / 'campaign-config.json'
    config.write_text(helper.CONFIG.read_text())
    seen = {}
    def review(run_id, **kwargs):
        seen.update(config=helper.CONFIG, reports=helper.REPORT_ROOT, data=helper.DATA_ROOT)
        report, _ = helper._paths(run_id)
        helper._header(report, run_id, 'Outside frozen code')
        return report
    monkeypatch.setattr(helper, 'REPO_ROOT', tmp_path / 'frozen-code')
    monkeypatch.setattr(helper, 'final_review', review)
    assert helper.main(['--run-id', 'now', '--config', str(config), '--report-root', str(report_root),
                        '--data-root', str(data_root)]) == 0
    assert seen == {'config': config, 'reports': report_root, 'data': data_root}
    assert (report_root / 'now/findings.md').is_file()


def test_muse_permissions_allow_only_its_findings_file(tmp_path, monkeypatch):
    from subprocess import CompletedProcess
    report = tmp_path / 'reports/now/findings.md'
    seen = {}
    def execute(command, **kwargs):
        seen.update(kwargs)
        return CompletedProcess(command, 0, '{"sessionID":"ses-readonly"}\n', '')
    monkeypatch.setattr(helper.subprocess, 'run', execute)
    work = tmp_path / 'work'
    work.mkdir()
    config = {'model': 'opencode-go/muse-spark-1.3-contributor', 'variant': 'xhigh', 'report_path': str(report)}
    helper._ask(config, work, 'Read the packet and write findings', None, 'readonly', 'continue')
    permissions = json.loads(seen['env']['OPENCODE_CONFIG_CONTENT'])['permission']
    assert permissions['bash'] == 'deny' and permissions['task'] == 'deny'
    assert permissions['edit']['*'] == 'deny'
    assert permissions['edit'][str(report)] == 'allow'


def test_comparator_world_roots_resolve_their_own_run_records(tmp_path):
    root = tmp_path / 'previous-code/data/laya-saved-world/prior'
    root.mkdir(parents=True)
    record = {'run_id': 'prior-s50616', 'root': str(root), 'commit': 'frozen-prior',
              'ended': '2026-10-10T04:00:00+05:30', 'summary': {'accepted': ['C0','C1']}}
    (root.parent / 'laya-runs.jsonl').write_text(json.dumps(record) + '\n')
    assert helper.load_comparators([root]) == [record]


def test_header_only_report_is_not_marked_complete(tmp_path, monkeypatch):
    import pytest
    setup(tmp_path, monkeypatch)
    monkeypatch.setattr(helper, '_ask', lambda *args, **kwargs: 'ses-no-report')
    with pytest.raises(RuntimeError, match='findings document was not updated'):
        helper.final_review('now')
    state = json.loads((helper.DATA_ROOT / 'now/helper-state.json').read_text())
    assert state['status'] == 'failed'
