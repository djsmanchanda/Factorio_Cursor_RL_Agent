# Path: tests/test_laya_console.py
# Purpose: Verify Laya console status parsing, auto-resume derivation, and control safety checks.

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tools import laya_console_runtime as runtime
from tools.laya_console_runtime import OperationError, StartRequest
from tools.laya_console_server import LayaConsoleHandler


def _write_summary(run_dir: Path, lines: list[str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def isolated_data_dirs(tmp_path, monkeypatch):
    out_dir = tmp_path / "laya-pi"
    batches_dir = tmp_path / "batches"
    out_dir.mkdir()
    batches_dir.mkdir()
    monkeypatch.setattr(runtime, "OUT_DIR", out_dir)
    monkeypatch.setattr(runtime, "BATCHES_DIR", batches_dir)
    monkeypatch.setattr(runtime, "find_driver_processes", lambda processes=None: [])
    monkeypatch.setattr(runtime, "gpu_status", lambda: None)
    monkeypatch.setattr(runtime, "host_memory_status", lambda: None)
    return out_dir, batches_dir


START_LINE = "[2026-01-01 00:00:00] start: 6.0 h, code abc1234, instances 14 15 16, procs 2, speed 32"


def test_status_phase_labelling_and_stale_labeller(isolated_data_dirs, monkeypatch):
    out_dir, batches_dir = isolated_data_dirs
    run_dir = out_dir / "testrun"
    _write_summary(run_dir, [
        START_LINE,
        "[2026-01-01 00:01:00] t-r1: level 2 seeds 6001+2 continuation rollout",
    ])
    # One labeller process has written labels; the other is stale.
    (out_dir / "t-r1-p0.log").write_text("seed 6001 k0 faithful=True costs={}\n" * 3, encoding="utf-8")
    stale_log = out_dir / "t-r1-p1.log"
    stale_log.write_text("seed 6002 k0 faithful=True costs={}\n", encoding="utf-8")
    old_time = runtime.time.time() - 60 * 60
    import os
    os.utime(stale_log, (old_time, old_time))

    status = runtime.build_status("testrun")

    assert status["phase"]["phase"] == "labelling"
    assert status["phase"]["round"] == 1
    labellers = {row["process"]: row for row in status["labellers"]}
    assert labellers[0]["labels"] == 3
    assert labellers[0]["stale"] is False
    assert labellers[1]["labels"] == 1
    assert labellers[1]["stale"] is True


def test_status_phase_progression_through_round(isolated_data_dirs):
    out_dir, batches_dir = isolated_data_dirs
    run_dir = out_dir / "testrun"
    _write_summary(run_dir, [
        START_LINE,
        "[2026-01-01 00:01:00] t-r1: level 2 seeds 6001+2 continuation rollout",
    ])

    assert runtime.build_status("testrun")["phase"]["phase"] == "labelling"

    (out_dir / "t-r1-cases.jsonl").write_text("{}\n", encoding="utf-8")
    assert runtime.build_status("testrun")["phase"]["phase"] == "training features"

    (out_dir / "features-t-r1.json").write_text("{}\n", encoding="utf-8")
    assert runtime.build_status("testrun")["phase"]["phase"] == "evaluating"

    (batches_dir / "eval-t-r1-L2.jsonl").write_text("{}\n", encoding="utf-8")
    status = runtime.build_status("testrun")
    assert status["phase"]["phase"] == "done"
    assert status["phase"]["round"] == 1


def test_status_phase_finetune_and_finished(isolated_data_dirs):
    out_dir, batches_dir = isolated_data_dirs
    run_dir = out_dir / "testrun"
    _write_summary(run_dir, [
        START_LINE,
        "[2026-01-01 00:01:00] t-r2: level 1 seeds 5101+2 continuation rollout",
        "[2026-01-01 00:05:00] t-r2: done in 4 min, 300 min left",
        "[2026-01-01 00:05:00] laya r2: dataset from 1 case files",
    ])
    status = runtime.build_status("testrun")
    assert status["phase"] == {
        "phase": "fine-tuning laya", "round": None, "level": None,
        "detail": "training checkpoint for r2",
    }

    with (run_dir / "summary.log").open("a", encoding="utf-8") as handle:
        handle.write("[2026-01-01 00:10:00] laya r2: trained -> /ckpt/laya-testrun-r2\n")
    status = runtime.build_status("testrun")
    assert status["phase"]["phase"] == "evaluating"

    (batches_dir / "t-eval-laya-r2-L1.jsonl").write_text("{}\n", encoding="utf-8")
    (batches_dir / "t-eval-laya-r2-L2.jsonl").write_text("{}\n", encoding="utf-8")
    status = runtime.build_status("testrun")
    assert status["phase"]["phase"] == "done"  # round complete, no further round started yet

    with (run_dir / "summary.log").open("a", encoding="utf-8") as handle:
        handle.write("[2026-01-01 00:11:00] done; training workers stopped\n")
    status = runtime.build_status("testrun")
    assert status["finished"] is True
    assert status["phase"]["phase"] == "done"


def test_build_results_best_and_trend(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    run_dir = out_dir / "testrun"
    _write_summary(run_dir, [
        START_LINE,
        json.dumps({"batch": "t-eval-rollout-L1", "seeds": 32, "completed": 32,
                    "median_cost_ticks": 7000.0, "median_decisions": 18.0,
                    "mean_delta_vs_baseline": 0.0, "faster_than_baseline": 0}),
        json.dumps({"batch": "eval-t-r1-L1", "seeds": 32, "completed": 32,
                    "median_cost_ticks": 6500.0, "median_decisions": 12.0,
                    "mean_delta_vs_baseline": -500.0, "faster_than_baseline": 30}),
        json.dumps({"batch": "eval-t-r3-L1", "seeds": 32, "completed": 32,
                    "median_cost_ticks": 6000.0, "median_decisions": 11.0,
                    "mean_delta_vs_baseline": -1000.0, "faster_than_baseline": 32}),
        json.dumps({"batch": "t-eval-laya-r2-L1", "seeds": 32, "completed": 30,
                    "median_cost_ticks": 7200.0, "median_decisions": 20.0,
                    "mean_delta_vs_baseline": 200.0, "faster_than_baseline": 10}),
    ])

    results = runtime.build_results("testrun")

    assert results["rollout_baseline"][1]["median_cost_ticks"] == 7000.0
    assert results["best_features"][1]["batch"] == "eval-t-r3-L1"
    assert results["best_laya"][1]["batch"] == "t-eval-laya-r2-L1"
    rounds_seen = {point["round"] for point in results["trend"][1]}
    assert rounds_seen == {1, 2, 3}


def test_results_prefer_completion_and_reject_blocked_median_winner(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    run_dir = out_dir / "testrun"
    run_dir.mkdir()
    _write_summary(run_dir, [START_LINE, *[json.dumps(row) for row in [
        {"batch": "t-eval-rollout-v4-L1", "seeds": 32, "completed": 32, "median_cost_ticks": 6854},
        {"batch": "t-eval-laya-r26-L1", "seeds": 32, "completed": 32,
         "median_cost_ticks": 6167, "mean_cost_ticks": 6272, "status": "diagnostic_only"},
        {"batch": "t-eval-laya-r28-L1", "seeds": 32, "completed": 28,
         "median_cost_ticks": 6148, "mean_cost_ticks": 10596, "status": "blocked"},
    ]]])
    result = runtime.build_results("testrun")
    assert result["best_laya"][1]["batch"] == "t-eval-laya-r26-L1"
    assert result["rollout_baseline"][1]["batch"] == "t-eval-rollout-v4-L1"


def test_derive_autoresume_from_features_files(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    for round_num in range(1, 6):
        (out_dir / f"features-t-r{round_num}.json").write_text("{}", encoding="utf-8")

    derived = runtime.derive_autoresume(prefix="t", out_dir=out_dir, seeds_per_round=36)

    # Rounds 1..5: odd (1,3,5) are level 2, even (2,4) are level 1.
    assert derived["resume_round"] == 5
    assert derived["l1_seed"] == runtime.DEFAULT_L1_SEED + 2 * 36
    assert derived["l2_seed"] == runtime.DEFAULT_L2_SEED + 3 * 36
    assert derived["start_policy"] == str(out_dir / "features-t-r5.json")


def test_autoresume_uses_logged_levels_and_retains_accepted_policy(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    for n in range(1, 5):
        (out_dir / f"features-t-r{n}.json").write_text("{}")
    run = out_dir / "testrun"
    run.mkdir()
    (run / "summary.log").write_text("\n".join([
        "[2026-09-30 12:00:00] t-r1: level 2 seeds 6001+36 continuation rollout",
        "[2026-09-30 13:00:00] t-r2: level 1 seeds 5101+36 continuation rollout",
        "[2026-09-30 14:00:00] t-r3: level 3 seeds 8101+36 continuation rollout",
        "[2026-09-30 15:00:00] t-r4: level 2 seeds 6037+36 continuation features-t-r3.json",
        "[2026-09-30 16:00:00] t-r5: level 1 seeds 5137+36 continuation features-t-r3.json",
    ]))
    accepted = out_dir / "features-t-r3.json"
    (out_dir / "continuation-t.json").write_text(json.dumps({"policy": str(accepted), "round": 3}))
    derived = runtime.derive_autoresume(prefix="t", out_dir=out_dir)
    assert derived["resume_round"] == 5
    assert derived["start_policy"] == str(accepted)
    assert (derived["l1_seed"], derived["l2_seed"], derived["l3_seed"]) == (5173, 6073, 8137)


def test_level3_results_are_visible(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    run = out_dir / "testrun"
    run.mkdir()
    _write_summary(run, [START_LINE, json.dumps({"batch": "t-eval-rollout-v4-L3", "seeds": 32,
                                               "completed": 32, "median_cost_ticks": 6000}),
                         json.dumps({"batch": "eval-t-r3-L3", "seeds": 32,
                                     "completed": 32, "median_cost_ticks": 5900})])
    result = runtime.build_results("testrun")
    assert result["rollout_baseline"][3]["completed"] == 32
    assert result["best_features"][3]["batch"] == "eval-t-r3-L3"
    assert result["trend"][3][0]["round"] == 3


def test_derive_autoresume_with_no_history_defaults_to_rollout(isolated_data_dirs):
    out_dir, _ = isolated_data_dirs
    derived = runtime.derive_autoresume(prefix="t", out_dir=out_dir)
    assert derived == {
        "resume_round": 0, "start_policy": "rollout",
        "l1_seed": runtime.DEFAULT_L1_SEED, "l2_seed": runtime.DEFAULT_L2_SEED,
    }


def test_start_refuses_when_a_run_is_already_active(isolated_data_dirs, monkeypatch):
    monkeypatch.setattr(runtime, "find_driver_process", lambda processes=None: {"pid": 1234, "cmdline": []})

    def _reject_popen(*_args, **_kwargs):
        raise AssertionError("start_driver must not launch a process while one is active")

    monkeypatch.setattr(runtime.subprocess, "Popen", _reject_popen)

    with pytest.raises(OperationError, match="already active"):
        runtime.start_driver(StartRequest(hours=0.1, run_name="testrun"))


def test_stop_after_round_writes_stop_file_for_the_active_run(isolated_data_dirs, monkeypatch):
    out_dir, _ = isolated_data_dirs
    monkeypatch.setattr(runtime, "find_driver_processes", lambda processes=None: [{"pid": 999, "cmdline": []}])
    monkeypatch.setattr(runtime, "read_environ", lambda pid: {"LAYA_RUN_NAME": "testrun", "LAYA_PREFIX": "t"})

    result = runtime.request_stop_after_round()

    stop_file = out_dir / "testrun" / "stop-requested"
    assert stop_file.exists()
    assert result == {"run_name": "testrun", "stop_file": str(stop_file)}


def test_stop_after_round_rejects_mismatched_run_name(isolated_data_dirs, monkeypatch):
    monkeypatch.setattr(runtime, "find_driver_processes", lambda processes=None: [{"pid": 999, "cmdline": []}])
    monkeypatch.setattr(runtime, "read_environ", lambda pid: {"LAYA_RUN_NAME": "testrun"})

    with pytest.raises(OperationError, match="No active run named 'some-other-run'"):
        runtime.request_stop_after_round("some-other-run")


def test_stop_now_requires_run_name_when_multiple_runs_are_active(isolated_data_dirs, monkeypatch):
    monkeypatch.setattr(runtime, "find_driver_processes", lambda processes=None: [
        {"pid": 111, "cmdline": []}, {"pid": 222, "cmdline": []},
    ])
    monkeypatch.setattr(runtime, "read_environ", lambda pid: {"LAYA_RUN_NAME": "overnight" if pid == 111 else "console-test"})

    with pytest.raises(OperationError, match="Multiple Laya runs are active"):
        runtime.stop_now()


def test_stop_after_round_requires_an_active_run(isolated_data_dirs):
    with pytest.raises(OperationError, match="No Laya training run is active"):
        runtime.request_stop_after_round()


def _post(handler_path: str, headers: dict, body: bytes = b"{}") -> list[tuple[int, dict]]:
    handler = object.__new__(LayaConsoleHandler)
    handler.path = handler_path
    handler.headers = headers
    handler.action_token = "secret-token"
    handler.rfile = io.BytesIO(body)
    replies: list[tuple[int, dict]] = []
    handler._json = lambda code, payload: replies.append((code, payload))
    handler.do_POST()
    return replies


def test_post_actions_require_the_action_token():
    replies = _post("/api/actions/stop_now", headers={"Content-Length": "2"})
    assert replies == [(403, {"error": "Invalid console action token."})]


def test_post_actions_reject_wrong_token():
    replies = _post("/api/actions/stop_now", headers={"X-Action-Token": "wrong", "Content-Length": "2"})
    assert replies == [(403, {"error": "Invalid console action token."})]


def test_post_start_requires_explicit_confirmation():
    payload = json.dumps({"hours": 0.1}).encode()
    replies = _post(
        "/api/actions/start",
        headers={"X-Action-Token": "secret-token", "Content-Length": str(len(payload))},
        body=payload,
    )
    assert len(replies) == 1
    code, body = replies[0]
    assert code == 409
    assert "confirmation" in body["error"]
