# Path: tests/test_tier_one_bootstrap.py
# Purpose: Prove the reduced start builds on tier one and self-funds native upgrades.

from __future__ import annotations

from pathlib import Path

from orchestrator import autonomous_builder as builder
from orchestrator import live_base
from orchestrator.mall_builder import compact_mall_project_bill
from planners.mall_layout import generate_paired_mall_layout
from planners.recipe_data import LINE_RECIPES


def test_compact_cell_can_be_priced_and_built_from_tier_one() -> None:
    bill = compact_mall_project_bill(
        "iron-gear-wheel", machine_name="assembling-machine-1",
    )
    spec = LINE_RECIPES["iron-gear-wheel"]
    plan = generate_paired_mall_layout(
        "iron-gear-wheel", "assembling-machine-1",
        spec["ingredients"], spec["amounts"], (35, 31), "left",
        craft_time=spec["craft_time"],
    )
    entities = [
        action["entity"] for action in plan["phases"][0]["actions"]
    ]

    assert bill["assembling-machine-1"] == 1
    assert "assembling-machine-2" not in bill
    assert entities.count("inserter") == 2
    assert "fast-inserter" not in entities


def test_assembler_line_survey_accepts_mixed_native_tiers() -> None:
    class Client:
        command_text = ""

        def command(self, command: str) -> str:
            self.command_text = command
            return "2 1 1.5:2.5,7.5:2.5 9"

    client = Client()
    state = live_base.find_line(
        client, "nauvis", "player", "iron-gear-wheel",
        "assembling-machine-1",
    )

    assert state is not None and state.machine_count == 2
    assert all(tier in client.command_text for tier in live_base.ASSEMBLER_TIERS)


def test_bootstrap_mall_orders_only_self_funded_upgrade_surplus(monkeypatch) -> None:
    submitted: list[tuple[dict, dict]] = []

    class Bridge:
        def execute_upgrade_plan(self, authorization, plan, **_scope) -> Path:
            submitted.append((authorization, plan))
            return Path("upgrade-report.json")

    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"assembling-machine-2": builder.UPGRADE_RESERVE + 1},
    )
    monkeypatch.setattr(
        builder, "_production_started",
        lambda *_a: _a[-1] == "assembling-machine-2",
    )
    monkeypatch.setattr(
        builder, "mall_entity_positions",
        lambda *_a: ((36.5, 32.5), (47.5, 32.5))
        if _a[-1] == "assembling-machine-1" else (),
    )
    monkeypatch.setattr(
        builder, "load_json",
        lambda _path: {"actions": [{"status": "success"}]},
    )

    spent = builder._upgrade_bootstrap_mall(
        object(), Bridge(), "nauvis", "player", {}, (3.0, -1.0),
        lambda _message: None,
    )

    assert spent is True
    authorization, plan = submitted[0]
    assert authorization["scope_limits"]["max_count"] == 1
    assert plan["actions"] == [{
        "action": "entity_tier_upgrade",
        "from_name": "assembling-machine-1",
        "to_name": "assembling-machine-2",
        "position": {"x": 36.5, "y": 32.5},
        "block": "bootstrap-mall-assembling-machine-1-to-assembling-machine-2",
    }]
