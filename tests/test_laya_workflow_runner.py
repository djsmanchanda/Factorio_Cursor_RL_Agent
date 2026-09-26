# Path: tests/test_laya_workflow_runner.py
# Purpose: Verify serve_goal scheduling over measured branches in the connected-workflow runner.

from __future__ import annotations

import json

from tests.test_laya_runner import LARGE_BUDGET, MODEL_ONLY_REF, Predictor
from training.laya_exploration import ExplorationDirector, ExplorationGate
from training.laya_runner import LayaPrimaryRunner, LayaRunnerConfig
from training.scenarios.connected_workflow import generate_connected_workflow_scenario

_RATES = {"iron": ("iron-plate", 0.011), "gears": ("iron-gear-wheel", 0.004), "belts": ("transport-belt", 0.006)}


class WorkflowBridge:
    """Built branches produce at a fixed rate; a window is sustained after 600 ticks."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.tick = 1_000
        self.built: dict[str, int] = {}
        self.recycled = 0

    def provision(self, _episode_id, _scenario):
        return {"started_tick": 1_000}

    def execute(self, _episode_id, _authorization, plan):
        branch = plan["phases"][0]["name"].rsplit(".", 1)[-1]
        self.built[branch] = self.tick
        self.tick += 1
        return {"succeeded_placements": len(plan["phases"][0]["actions"]), "failed_placements": 0}

    def _window(self, branch):
        item, rate = _RATES[branch]
        if branch not in self.built:
            return item, {"available": True, "window_rate_per_tick": 0.0, "window_ticks": 0, "cumulative_items": 0}
        age = self.tick - self.built[branch]
        return item, {"available": True, "window_rate_per_tick": rate,
                      "window_ticks": min(600, age), "cumulative_items": int(age * rate)}

    def observe(self, _episode_id):
        items = dict(self._window(branch) for branch in _RATES)
        belts_age = self.tick - self.built["belts"] if "belts" in self.built else 0
        completed = belts_age >= 1_800
        environment, objective = self.scenario["environment"], self.scenario["objective"]
        return {
            "status": "completed" if completed else "running",
            "scenario_id": self.scenario["scenario_id"],
            "surface": environment["surface_name"], "force": environment["force_name"],
            "tick": self.tick, "started_tick": 1_000, "elapsed_ticks": self.tick - 1_000,
            "objective": {"target_rate_per_tick": objective["target_rate_per_tick"],
                          "sustain_ticks": objective["sustain_ticks"], "stage_index": 1,
                          "destination_fixture_ids": ["belt-sink"]},
            "metrics": {
                "rate_per_tick": 0.006 if belts_age else 0.0, "sustained_ticks": min(belts_age, 1_800),
                "delivered_items": int(belts_age * 0.006), "resource_remaining": 0,
                "electric_pole_count": 0, "occupied_footprint_tiles": 0, "power_connected": True,
                "built_entities": {}, "item_production": {"available": True, "items": items},
                "live_entities": {"medium-electric-pole": 4} if "construction" in self.built else {},
            },
            "failure": {"kind": "none", "reason": ""},
        }

    def recycle(self, _episode_id):
        self.recycled += 1
        return {"status": "completed"}


def test_serve_goal_gates_on_measured_branches_and_offers_independent_work(tmp_path) -> None:
    scenario = generate_connected_workflow_scenario(3)
    bridge = WorkflowBridge(scenario)
    offered = []

    def choose(kind, ids, _state):
        assert kind == "serve_goal"
        offered.append(ids)
        if "serve.iron" in ids:
            return "serve.iron"
        builds = [option for option in ids if option.startswith("serve.")]
        return builds[0] if builds else "wait.15s"

    gate = ExplorationGate(seed=1, epsilon=0.0)

    def sleep(seconds):
        bridge.tick += int(seconds * 60)

    result = LayaPrimaryRunner(
        bridge, scenario, ExplorationDirector(Predictor(choose), gate, budget_profile=LARGE_BUDGET),
        LayaRunnerConfig(worker_id="worker-test-1", run_id="workflow-test", evidence_dir=tmp_path,
                         max_decisions=40, max_no_progress=12),
        model_ref=MODEL_ONLY_REF, exploration=gate, sleeper=sleep,
    ).run()

    assert result["status"] == "completed"
    assert {"serve.iron", "serve.construction"} <= set(offered[0])
    assert not {"serve.gears", "serve.belts"} & set(offered[0])
    # While iron is built but not yet sustained, gears stay blocked and the
    # independent construction branch is the useful alternative to waiting.
    assert "serve.construction" in offered[1] and "serve.gears" not in offered[1]
    first_gears = next(index for index, ids in enumerate(offered) if "serve.gears" in ids)
    assert first_gears > 1
    events = [json.loads(line) for line in (tmp_path / "runs" / "workflow-test.jsonl").read_text().splitlines()]
    assert sum(event["type"] == "work_graph" for event in events) == len(offered)
    final = next(event for event in events if event["type"] == "termination")["work_graph"]
    assert sorted(branch["branch_id"] for branch in final["closed"]) == ["belts", "construction", "gears", "iron"]


def test_live_entity_counts_enter_supply_only_for_counted_branches() -> None:
    # Live: every built entity type became a supply item and overflowed 1,024 tokens.
    from training.candidates.connected_workflow import branch_specs
    from training.laya_lab_workflow import observe_lab_workflow

    scenario = generate_connected_workflow_scenario(3)
    bridge = WorkflowBridge(scenario)
    bridge.built["construction"] = bridge.tick
    report = bridge.observe("episode")
    report["metrics"]["live_entities"].update({"electric-furnace": 1, "fast-inserter": 7, "iron-chest": 2})
    graph, supply = observe_lab_workflow(report, branch_specs(scenario))
    assert set(supply["items"]) == {"iron-plate", "iron-gear-wheel", "transport-belt", "medium-electric-pole"}
    assert "construction" in {branch.branch_id for branch in graph.branches if branch.status == "closed"}
