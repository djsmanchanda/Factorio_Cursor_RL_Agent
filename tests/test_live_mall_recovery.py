# Path: tests/test_live_mall_recovery.py
# Purpose: Prove live paired-mall cells are classified by their topology instead of mistaken for belt-fed lines.

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402


def test_science_call_keeps_starved_paired_mall_transport(monkeypatch) -> None:
    """The live fault: two valid mall gear assemblers were passed to line
    recovery, which rejected their six-tile spacing as invalid line geometry."""
    machines = ((50.5, 32.5), (56.5, 32.5))
    provider = (53.5, 31.5)
    existing = SimpleNamespace(
        machine_count=2,
        working_count=0,
        machine_positions=machines,
        output_position=provider,
    )
    plan = SimpleNamespace(
        existing=existing,
        at_size=True,
        promote_to_line=False,
    )
    monkeypatch.setattr(builder, "_plan_line", lambda *_a, **_k: plan)
    monkeypatch.setattr(builder, "_refresh_mall_cell", lambda *_a, **_k: provider)
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder.live_base, "nearest_pole_on_other_network", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "_existing_stage_chests", lambda *_a, **_k: [])
    monkeypatch.setattr(
        builder.live_base,
        "entity_statuses",
        lambda *_a, **_k: {position: "item_ingredient_shortage" for position in machines},
    )

    output = builder.ensure_produced(
        object(), object(), "nauvis", "player", "iron-gear-wheel",
        (3.0, -1.0), lambda _message: None, upgrade_bootstrap=True,
    )

    assert output == provider


def test_upgrade_call_still_detects_the_paired_mall_provider(monkeypatch) -> None:
    provider = (53.5, 31.5)
    existing = SimpleNamespace(machine_positions=((50.5, 32.5), (56.5, 32.5)))
    plan = SimpleNamespace(
        existing=existing, spec={}, production_target=1, mall_storage_limit=1,
        fill_provider=False,
    )
    monkeypatch.setattr(builder, "_paired_mall_provider", lambda *_a, **_k: provider)

    found = builder._refresh_mall_cell(
        object(), object(), "nauvis", "player", "iron-gear-wheel", plan,
        lambda _message: None, upgrade_bootstrap=True,
    )

    assert found == provider
