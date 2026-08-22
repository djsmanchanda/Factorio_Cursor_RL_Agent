# Path: tests/test_ghost_diagnostics.py
# Purpose: Prove stalled construction ghosts report actionable live causes and material demand.

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base  # noqa: E402


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.reply


def test_ghost_blockages_decodes_missing_material() -> None:
    client = _Client("77.5|-18.5|transport-belt|missing_material:transport-belt:4:0")

    result = live_base.ghost_blockages(
        client, "nauvis", "player", ((70.0, -25.0), (80.0, -10.0)),
    )

    assert result == [{
        "position": (77.5, -18.5), "entity": "transport-belt",
        "reason": "missing_material:transport-belt:4:0",
        "item": "transport-belt", "required": 4, "available": 0,
    }]
    assert "entity-ghost" in client.commands[0]
    assert "area={{70.0,-25.0},{80.0,-10.0}}" in client.commands[0]


def test_diagnosis_promotes_missing_ghost_material_to_mall_demand(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *_a: (0.0, 0.0))
    monkeypatch.setattr(live_base, "entity_status_name", lambda *_a: "working")
    monkeypatch.setattr(
        live_base, "ghost_blockages", lambda *_a: [{
            "position": (77.5, -18.5), "entity": "transport-belt",
            "reason": "missing_material:transport-belt:4:0",
            "item": "transport-belt", "required": 4, "available": 0,
        }],
    )

    issue = builder._diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0),
        [(11.0, 10.0)], area=((0.0, 0.0), (100.0, 100.0)),
    )

    assert issue == (
        "ghost transport-belt at (77.5, -18.5) needs 4 transport-belt, "
        "but its network has 0",
        "materials:transport-belt:4",
    )


def test_material_remedy_raises_shortage_when_base_lacks_item(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "available_items", lambda *_a: {})

    with pytest.raises(builder.MaterialShortage) as raised:
        builder._apply_remedy(
            None, None, "nauvis", "player", "conversion_stone-brick",
            "materials:stone-brick:8", "missing bricks", (0.0, 0.0),
            (0.0, 0.0), [], [], None, lambda _message: None,
        )

    assert raised.value.required == {"stone-brick": 8}

def test_coverage_remedy_targets_the_stranded_ghost_not_stage_origin(monkeypatch) -> None:
    targets = []
    monkeypatch.setattr(
        live_base, "ghost_blockages", lambda *_a: [{
            "position": (25.5, -49.5), "entity": "transport-belt",
            "reason": "out_of_construction_range",
        }],
    )
    monkeypatch.setattr(
        builder, "extend_roboport_coverage",
        lambda *_args, **_kwargs: targets.append(_args[4]) or True,
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "mining stage for coal",
        "coverage", "ghost transport-belt outside coverage", (10.0, -40.0),
        (10.0, -40.0), [], [], ((0.0, -60.0), (40.0, -20.0)),
        lambda _message: None,
    )

    assert acted
    assert targets == [(25.5, -49.5)]

def test_stage_power_repairs_stranded_machine_when_substation_is_powered(monkeypatch) -> None:
    calls = []

    def fake_extend(_client, _bridge, _surface, _force, position, _emit):
        calls.append(position)
        return position == (59.5, -63.5)

    monkeypatch.setattr(builder, "extend_power", fake_extend)
    monkeypatch.setattr(
        live_base, "entity_statuses",
        lambda *_a: {(53.5, -67.5): "working", (59.5, -63.5): "no_power"},
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "mining stage for stone",
        "stage_power", "1 machine(s) unpowered", (48.0, -70.0),
        (48.0, -70.0), [(53.5, -67.5), (59.5, -63.5)], [], None,
        lambda _message: None,
    )

    assert acted
    assert calls == [(48.0, -70.0), (59.5, -63.5)]

def test_material_remedy_waits_when_network_reserves_stock(monkeypatch) -> None:
    """Global stock can exist while another construction job reserves it."""
    messages: list[str] = []
    monkeypatch.setattr(
        live_base, "available_items", lambda *_a: {"transport-belt": 10},
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "conversion_automation-science-pack",
        "materials:transport-belt:1", "ghost belt has no network stock",
        (0.0, 0.0), (0.0, 0.0), [], [], None, messages.append,
    )

    assert acted
    assert any("temporarily unavailable" in message for message in messages)

def test_logistic_coverage_remedy_waits_out_the_roboport_charge(monkeypatch) -> None:
    """A just-connected roboport needs tens of seconds to charge before its
    logistic area exists; rounds that re-check instantly are six ways of doing
    nothing (live run of 2026-08-22 00:52 burned all six in ~2s and died)."""
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a: False)
    polls = iter([None, None, 4])
    slept: list[float] = []
    monkeypatch.setattr(
        builder.time, "sleep", lambda seconds: slept.append(seconds),
    )
    monkeypatch.setattr(
        live_base, "logistic_network_ids",
        lambda _c, _s, positions: {positions[0]: next(polls)},
    )
    # Keep the bound effectively unbounded for this scenario.
    monkeypatch.setattr(builder, "_LOGISTIC_CHARGE_WAIT_SECONDS", 90.0)
    monkeypatch.setattr(builder.time, "monotonic", lambda: 0.0)

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "logistic bootstrap for copper-plate",
        "logistic_coverage", "1 chest(s) outside every logistic area",
        (77.5, -39.5), (77.5, -39.5), [], [(77.5, -39.5)], None,
        lambda _message: None,
    )

    assert acted
    assert slept == [3.0, 3.0]

def test_logistic_coverage_remedy_reports_an_honest_noop_at_the_bound(monkeypatch) -> None:
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a: False)
    monkeypatch.setattr(
        live_base, "logistic_network_ids",
        lambda _c, _s, positions: {positions[0]: None},
    )
    ticks = iter(range(0, 400, 3))

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            return float(next(ticks))

        @staticmethod
        def sleep(_seconds: float) -> None:
            pass

    monkeypatch.setattr(builder, "time", FakeTime)

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "logistic bootstrap for copper-plate",
        "logistic_coverage", "1 chest(s) outside every logistic area",
        (77.5, -39.5), (77.5, -39.5), [], [(77.5, -39.5)], None,
        lambda _message: None,
    )

    assert not acted


def test_power_bridge_racing_a_concurrent_build_replans_once(monkeypatch) -> None:
    """A substation built by another stage between our survey and our bots'
    arrival is construction in flight, not a wall -- replan once on fresh
    ground (live run of 2026-08-22 02:46 ended the mission on this)."""
    from orchestrator import stage_services as ss

    surveys = {"n": 0}

    def fake_occupied(*_a, **_k):
        surveys["n"] += 1
        return set() if surveys["n"] == 1 else {(48, -70)}

    submits: list[str] = []

    def fake_submit(_c, _b, _s, plan, name, _emit):
        submits.append(name)
        if len(submits) == 1:
            raise ss.StuckError(
                "power_bridge: blocked by real infrastructure "
                "[{'position': {'x': 48, 'y': -70}, "
                "'reason': 'exact_position_occupied_by_different_entity'}]"
            )

    monkeypatch.setattr(ss, "_submit", fake_submit)
    monkeypatch.setattr(ss.live_base, "pole_network_id", lambda *_a: None)
    monkeypatch.setattr(
        ss.live_base, "nearest_powered_pole",
        lambda *_a, **_k: ((50.0, -5.0), "substation"),
    )
    monkeypatch.setattr(ss.live_base, "entity_at", lambda *_a: None)
    monkeypatch.setattr(ss.live_base, "occupied_tiles", fake_occupied)

    acted = ss.extend_power(
        object(), object(), "nauvis", "player", (45.0, -66.0),
        lambda _m: None,
    )

    assert acted
    assert submits.count("power_bridge") == 2


def test_remediation_extends_repeatedly_while_local_ghosts_fall(monkeypatch) -> None:
    """Live run 30 (2026-08-22): a ~250-tile oil pipeline built at cross-base
    robot-flight speed fell 15 -> 10 ghosts across two extensions and was
    killed anyway -- the old loop granted exactly one extension and judged
    progress by the WHOLE-SURFACE ghost count. Extensions must repeat while
    THIS stage's pending ghosts keep falling."""
    monkeypatch.setattr(builder, "extend_roboport_coverage", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_diagnose_blockage", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_apply_remedy", lambda *_a, **_k: False)
    readings = iter([4, 3, 3, 2, 2, 2])
    monkeypatch.setattr(
        builder, "_wait_for_ghosts",
        lambda *_a, **_k: next(readings),
    )
    emitted: list[str] = []

    with pytest.raises(builder.StuckError) as stuck:
        builder.bring_stage_up(
            None, None, "nauvis", "player", "pipeline",
            (0.0, 0.0), ((0.0, 0.0), (9.0, 9.0)), (0.0, 0.0), [],
            emitted.append, rounds=2, interval=0.0,
        )

    extensions = [e for e in emitted if "extending remediation" in e]
    assert len(extensions) == 2
    # The verdict must tell the truth about bot progress instead of claiming
    # nothing was built.
    assert "area ghosts 2 -> 2" in str(stuck.value)


def test_missing_feed_on_a_mall_cell_rebuilds_instead_of_dying(monkeypatch) -> None:
    """Live run 32 (2026-08-22): the strict transport repair raised
    'expected requester-chest ... found nothing' for a half-built paired cell
    and killed the mission. When the machine belongs to a declared mall half,
    the repair path must regenerate that cell in place and re-survey."""
    from orchestrator.mall_builder import _slot_position

    origin, side = (67, 53), "left"
    machine = _slot_position(origin, side)
    monkeypatch.setattr(
        builder, "extend_roboport_coverage", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "nearest_pole_on_other_network",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_statuses",
        lambda *_a, **_k: {tuple(machine)[:2]: "item_ingredient_shortage"},
    )
    monkeypatch.setattr(
        builder, "_existing_stage_chests", lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        builder, "bring_stage_up", lambda *_a, **_k: None,
    )
    rebuilt: list[str] = []
    monkeypatch.setattr(
        builder, "rebuild_incomplete_mall_cell",
        lambda _c, _b, _s, _f, recipe, position, _ref, emit, **_k:
            rebuilt.append((recipe, position)) or True,
    )
    raised = False

    def raising_repair(*_a, **_k):
        nonlocal raised
        raised = True
        raise builder.StuckError(
            "existing advanced-circuit feed for copper-cable expected "
            "requester-chest at (44.5, 50.5), found nothing"
        )

    monkeypatch.setattr(builder, "repair_existing_ingredient_transport", raising_repair)

    class _Existing:
        machine_count = 1
        working_count = 0
        machine_positions = [machine]
        output_position = machine

    plan = builder._LinePlan(
        existing=_Existing(),
        spec={"ingredients": [], "amounts": []},
        production_target=1,
        mall_storage_limit=0,
        fill_provider=False,
        demand=0.0,
        saturated=False,
        promoted_count=None,
        promote_to_line=False,
        at_size=True,
    )

    result = builder._repair_stalled_line(
        None, None, "nauvis", "player", "advanced-circuit",
        (35.0, 21.0), lambda _m: None, plan,
        mall_provider=None, upgrade_bootstrap=True,
    )

    assert raised, "the strict repair must have run before the fallback"
    assert rebuilt == [("advanced-circuit", machine)]
    assert result is None
