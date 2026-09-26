# Path: tests/test_watch_laya_run.py
# Purpose: Verify the live Laya journal feed renders decisions, options and termination.

from __future__ import annotations

from tests.test_laya_runner import Bridge, Predictor, _runner
from tools.watch_laya_run import main
from training.scenarios.mining_delivery import generate_mining_delivery_scenario


def test_watch_renders_decisions_with_options_and_termination(tmp_path, capsys) -> None:
    scenario = generate_mining_delivery_scenario(11)

    def choose(kind, ids, _state):
        return {"choose_operation": "operation.mining_delivery", "wait_or_act": "wait.5s"}.get(kind, ids[-1])

    _runner(tmp_path, Bridge(scenario), Predictor(choose), scenario).run()
    assert main(["--once", str(tmp_path / "runs" / "runner-test.jsonl")]) == 0

    output = capsys.readouterr().out
    assert "-> choose_operation" in output and "chose operation.mining_delivery" in output
    assert "*" in output and "wait.1s" in output
    assert "== terminated: completed" in output
