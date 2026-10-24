# Path: tests/test_refinery_pole_clearance.py | Purpose: Route refinery pole conflicts through connected relocation before mine growth.
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder, pole_obstruction as po
from orchestrator.stage_services import StuckError


PLAN = {"phases": [{"name": "growth", "actions": [{
    "action_type": "place_ghost", "entity": "electric-furnace",
    "position": {"x": -39.5, "y": -13.5},
}]}]}
OWNER = ("big-electric-pole", -40.0, -14.0)
CLIENT = SimpleNamespace(command=lambda _: "")


def survey(monkeypatch, owned=True):
    owners = {(-41, -15): OWNER, (-41, -14): OWNER, (-40, -15): OWNER}
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners", lambda *_a: owners)
    monkeypatch.setattr(builder.live_base, "entity_signatures_at",
                        lambda *_a: {(-40.0, -14.0): {"name": OWNER[0]}} if owned else {})
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a: set())
    return owners


def test_recorded_big_pole_is_prepared_then_cleared_without_changing_geometry(monkeypatch):
    owners = survey(monkeypatch)
    preparation = builder._plate_expansion_foundation(CLIENT, "nauvis", "player", "iron-plate", PLAN)
    assert preparation["pole_plan"] == PLAN
    calls = []
    def relocate(*args):
        calls.append(args[4])
        owners.clear()
        return 1
    monkeypatch.setattr(po, "relocate_obstructing_poles", relocate)
    messages = []
    builder._place_plate_expansion_foundation(CLIENT, object(), "nauvis", "player", "iron-plate", preparation, messages.append)
    assert calls == [PLAN]
    assert "footprint rechecked" in messages[-1]


def test_foreign_force_or_unknown_pole_is_still_a_hard_refusal(monkeypatch):
    survey(monkeypatch, owned=False)
    with pytest.raises(StuckError, match="intersects real infrastructure"):
        builder._plate_expansion_foundation(CLIENT, "nauvis", "player", "iron-plate", PLAN)


def test_unfinished_relocation_cannot_release_mine_growth(monkeypatch):
    survey(monkeypatch)
    preparation = builder._plate_expansion_foundation(CLIENT, "nauvis", "player", "iron-plate", PLAN)
    monkeypatch.setattr(po, "relocate_obstructing_poles", lambda *_: 1)
    with pytest.raises(StuckError, match="intersects real infrastructure"):
        builder._place_plate_expansion_foundation(CLIENT, object(), "nauvis", "player", "iron-plate", preparation, print)


def test_no_safe_connected_site_remains_a_typed_refusal(monkeypatch):
    survey(monkeypatch)
    preparation = builder._plate_expansion_foundation(CLIENT, "nauvis", "player", "iron-plate", PLAN)
    def blocked(*_):
        raise po.PoleObstruction("no connected site", {"reason": "blocked"})
    monkeypatch.setattr(po, "relocate_obstructing_poles", blocked)
    with pytest.raises(StuckError, match="no connected site") as caught:
        builder._place_plate_expansion_foundation(CLIENT, object(), "nauvis", "player", "iron-plate", preparation, print)
    assert caught.value.code == "power_pole_obstruction"


def test_wire_failure_keeps_old_pole(monkeypatch):
    pole = po.Pole(OWNER[0], OWNER[1:], False, frozenset({(-41, -15)}),
                   (("big-electric-pole", (-60., -14.)), ("big-electric-pole", (-20., -14.))))
    monkeypatch.setattr(po, "_survey_poles", lambda *_: [pole])
    monkeypatch.setattr(po, "_survey_consumers", lambda *_: [])
    monkeypatch.setattr(po.live_base, "occupied_tiles", lambda *_: set())
    monkeypatch.setattr(po, "replacement_site", lambda *_: (-40., -18.))
    calls = []
    monkeypatch.setattr(po, "retire_entities_via_bots", lambda *_: calls.append("retired"))
    client = SimpleNamespace(command=lambda _: "wire_link_unverified")
    with pytest.raises(po.PoleObstruction, match="keeping the old pole"):
        po.relocate_obstructing_poles(client, object(), "nauvis", "player", PLAN, "growth", print,
                                     lambda *_: calls.append("replacement"))
    assert calls == ["replacement"]
