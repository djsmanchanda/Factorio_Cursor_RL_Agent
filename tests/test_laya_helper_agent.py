# Path: tests/test_laya_helper_agent.py | Purpose: Protect the post-run Laya Helper: run selection, bounded packet, read-only prompt and completion line.

import json
import stat

import tools.laya_helper_agent as helper


def write_run(root, run_id, decisions=2):
    (root / "logs").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "logs/laya-runner.log").write_text(
        f"RUN START: ts=2026-10-03T20:00:00+05:30 run_id=other\n+1s DECISION 0\n+2s RUN END\n"
        f"RUN START: ts=2026-10-03T21:00:00+05:30 run_id={run_id}\n+1s DECISION 0 phase=C0\n"
        "+1s   STATUS: same\n+5s   STATUS: same\n+9s PHASE REACHED: C0 iron_copper_starter\n+9s RUN END\n")
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


def test_review_writes_findings_through_one_read_only_opencode_session(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho '{\"sessionID\": \"ses_1\"}'\nfor a; do last=$a; done\n"
                    "echo \"$last\" > \"$(dirname \"$0\")/prompt.txt\"\necho \"$@\" > \"$(dirname \"$0\")/argv.txt\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(helper, "OPENCODE", [str(fake)])
    report = helper.review("now")
    assert report == tmp_path / "reports/now/findings.md" and report.is_file()
    message = (tmp_path / "prompt.txt").read_text()
    assert "read-only" in message and "context management" in message and "fine-tuning" in message
    assert "do not edit code" in message
    argv = (tmp_path / "argv.txt").read_text()  # OpenCode v2: variant inside the model id, no --dir/--variant flags.
    assert "--model m#v" in argv and "--dir" not in argv and "--variant" not in argv


def test_live_observer_checkpoints_then_reviews_in_the_same_session(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    fake = tmp_path / "opencode"
    fake.write_text("#!/bin/sh\necho '{\"sessionID\": \"ses_live\"}'\nfor a; do last=$a; done\n"
                    "echo '---CALL---' >> \"$(dirname \"$0\")/calls.txt\"\necho \"$@\" >> \"$(dirname \"$0\")/calls.txt\"\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(helper, "OPENCODE", [str(fake)])
    root = tmp_path / "now"  # RUN END already logged and no runner PID record: one checkpoint, then the review.
    report = helper.observe("now", root / "logs/laya-runner.log", root, interval=1)
    calls = (tmp_path / "calls.txt").read_text().split("---CALL---")[1:]
    assert report.is_file() and len(calls) == 2
    assert "live observer" in calls[0] and "--session ses_live" in calls[1] and "Live observations" in calls[1]
    assert (tmp_path / "data/now/checkpoint-001.md").is_file()


def test_journal_summary_accepts_ops_rows_without_status(tmp_path):
    journal = tmp_path / "clean-decisions.jsonl"
    rows = [{"event": "decision", "run_id": "r", "index": 0, "phase": "C1", "choice": {"need_id": "wait"}},
            {"event": "phase_reached", "run_id": "r", "phase": "C1"}]
    journal.write_text("".join(json.dumps(r) + "\n" for r in rows))
    summary = helper.journal_summary(journal, "r")
    assert summary["decisions"] == 1 and summary["statuses"] == {} and summary["phases_reached"] == ["C1"]
