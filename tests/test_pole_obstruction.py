# Path: tests/test_pole_obstruction.py | Purpose: Protect rerouting own poles out of a plan's footprint: replacement first, links and dependents kept.

import math

import pytest

from orchestrator import pole_obstruction as po
from planners.infrastructure_geometry import footprint_tile_indices

CHEST_PLAN = {"phases": [{"name": "paired_mall_x", "actions": [
    {"action_type": "place_ghost", "entity": "passive-provider-chest", "position": {"x": 58.5, "y": 34.5}}]}]}
BIG = po.Pole("big-electric-pole", (58.0, 35.0), False, frozenset(footprint_tile_indices((58.0, 35.0), 2)),
              (("big-electric-pole", (40.0, 30.0)), ("medium-electric-pole", (56.5, 37.5))))


class Client:
    def command(self, text):
        return ""


def _install(monkeypatch, poles, consumers=()):
    calls = []
    monkeypatch.setattr(po, "_survey_poles", lambda *a: list(poles))
    monkeypatch.setattr(po, "_survey_consumers", lambda *a: list(consumers))
    monkeypatch.setattr(po.live_base, "occupied_tiles", lambda *a, **k: set())
    monkeypatch.setattr(po, "_connect", lambda *a: calls.append(("connect", a[3])))
    monkeypatch.setattr(po, "retire_entities_via_bots", lambda *a, **k: calls.append(("retire", a[4])))
    monkeypatch.setattr(po.live_base, "remove_ghost_at", lambda *a: calls.append(("unghost", a[4])))
    return calls


def test_a_linking_pole_is_replaced_before_it_is_recovered(monkeypatch):
    calls = _install(monkeypatch, [BIG])
    submit = lambda client, bridge, surface, plan, name, emit: calls.append(("submit", plan))
    moved = po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "paired_mall_x",
                                          lambda line: None, submit)
    assert moved == 1 and [c[0] for c in calls] == ["submit", "connect", "retire"]
    site = calls[1][1]
    assert not footprint_tile_indices(site, 2) & {(58, 34)}  # off the chest tile
    assert math.dist(site, (56.5, 37.5)) <= 9.0 and math.dist(site, (40.0, 30.0)) <= 32.0


def test_a_pole_linking_and_powering_nothing_is_just_recovered(monkeypatch):
    leaf = po.Pole("medium-electric-pole", (58.5, 34.5), True, frozenset({(58, 34)}),
                   (("medium-electric-pole", (56.5, 37.5)),))
    calls = _install(monkeypatch, [leaf])
    po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "paired_mall_x",
                                  lambda line: None, lambda *a: calls.append(("submit",)))
    assert calls == [("unghost", (58.5, 34.5))]


def test_dependents_exclude_consumers_another_pole_supplies():
    inserter = (57.15, 32.15, 57.85, 32.85)
    old = ("medium-electric-pole", (58.5, 34.5))
    assert po.dependents(old, [old], [inserter]) == [inserter]
    assert po.dependents(old, [old, ("medium-electric-pole", (56.5, 35.5))], [inserter]) == []


def test_no_connected_site_fails_loudly_instead_of_building_over(monkeypatch):
    stranded = po.Pole("medium-electric-pole", (58.5, 34.5), False, frozenset({(58, 34)}),
                       (("medium-electric-pole", (50.5, 34.5)), ("medium-electric-pole", (66.5, 34.5))))
    _install(monkeypatch, [stranded])
    monkeypatch.setattr(po.live_base, "occupied_tiles", lambda *a, **k: {
        (x, y) for x in range(40, 80) for y in range(20, 50) if (x, y) != (58, 34)})
    with pytest.raises(po.PoleObstruction):
        po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "paired_mall_x",
                                      lambda line: None, lambda *a: None)


def test_planned_poles_and_relocation_plans_are_left_alone(monkeypatch):
    calls = _install(monkeypatch, [BIG])
    own = {"phases": [{"name": "p", "actions": [
        {"action_type": "place_ghost", "entity": "big-electric-pole", "position": {"x": 58.0, "y": 35.0}}]}]}
    assert po.relocate_obstructing_poles(Client(), None, "nauvis", "player", own, "power_bridge",
                                         lambda line: None, lambda *a: None) == 0
    assert po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN,
                                         po.RELOCATION_PREFIX + "_x", lambda line: None, lambda *a: None) == 0
    assert calls == []


def test_sibling_in_flight_pole_is_preserved_with_an_unrelated_blocker(monkeypatch):
    sibling = po.Pole("small-electric-pole", (58.5, 34.5), True, frozenset({(58, 34)}), ())
    calls = _install(monkeypatch, [sibling, BIG])
    po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "growth",
        lambda _: None, lambda *a: None,
        own_action_positions={(sibling.name, *sibling.position)})
    assert not any(c[0] == "unghost" for c in calls)
    assert len([c for c in calls if c[0] == "retire"]) == 1


def test_ghost_cannot_replace_live_consumer_power_coverage(monkeypatch):
    old = po.Pole("medium-electric-pole", (58.5, 34.5), False, frozenset({(58, 34)}), ())
    ghost = po.Pole("medium-electric-pole", (56.5, 35.5), True, frozenset({(56, 35)}), ())
    calls = _install(monkeypatch, [old, ghost], [(57.15, 32.15, 57.85, 32.85)])
    po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "growth",
        lambda _: None, lambda *a: calls.append(("submit",)))
    assert [c[0] for c in calls] == ["submit", "connect", "retire"]


def test_incomplete_retirement_is_typed_and_keeps_replacement(monkeypatch):
    calls = _install(monkeypatch, [BIG])
    def fail(*_):
        raise po.RecoverableRetirementError("bots still working")
    monkeypatch.setattr(po, "retire_entities_via_bots", fail)
    with pytest.raises(po.PoleObstruction) as error:
        po.relocate_obstructing_poles(Client(), None, "nauvis", "player", CHEST_PLAN, "growth",
            lambda _: None, lambda *a: calls.append(("submit",)))
    assert error.value.details["reason"] == "retirement_incomplete"
    assert [c[0] for c in calls] == ["submit", "connect"]
