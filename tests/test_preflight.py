# Path: tests/test_preflight.py
# Purpose: One focused test per preflight rejection, plus clean and skip behavior.

from __future__ import annotations

import pytest

from planners.preflight import assert_preflight, preflight


def _entity(entity, x, y, *, action_type="place_entity", direction=None, name="p"):
    action = {"action_type": action_type, "entity": entity, "position": {"x": x, "y": y}}
    if direction is not None:
        action["direction"] = direction
    return (name, {"phases": [{"name": "phase", "actions": [action]}]})


def _bundle(*plans):
    return list(plans)


# --- 1. duplicate tile -------------------------------------------------------

def test_duplicate_tile_is_caught():
    bundle = _bundle(
        _entity("transport-belt", 5.5, 5.5, direction="east", name="belt_a"),
        _entity("transport-belt", 5.5, 5.5, direction="north", name="belt_b"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    assert any(f["check"] == "duplicate_tile" for f in result["failures"])


# --- 2. footprint overlap -----------------------------------------------------

def test_footprint_overlap_is_caught():
    bundle = _bundle(
        _entity("assembling-machine-2", 10.5, 10.5, name="m1"),
        _entity("assembling-machine-2", 11.5, 10.5, name="m2"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    assert any(f["check"] == "footprint_overlap" for f in result["failures"])


# --- 3. power reach ------------------------------------------------------------

def test_power_reach_stranded_pole_is_caught():
    bundle = _bundle(
        _entity("electric-energy-interface", 0, 0, name="source"),
        _entity("big-electric-pole", 2, 0, name="root_pole"),
        _entity("substation", 500, 500, name="stranded_substation"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "power_reach")
    assert (500, 500) in failure["positions"]


def test_power_reach_uncovered_machine_is_caught():
    # Pole graph is fine, but the machine sits far outside any pole's supply area.
    bundle = _bundle(
        _entity("electric-energy-interface", 0, 0, name="source"),
        _entity("big-electric-pole", 2, 0, name="root_pole"),
        _entity("assembling-machine-2", 40.5, 40.5, name="stranded_machine"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "power_reach")
    assert "uncovered" in failure["detail"]


# --- 4. fluid mixing -----------------------------------------------------------

def test_fluid_mixing_is_caught():
    bundle = {
        "infrastructure": [],
        "plans": [_entity("pipe", 0.5, 0.5, name="pipe_stub")],
        "fluid_segments": [
            {"fluid": "water", "separated_by_pump": False, "tiles": [(0, 0)]},
            {"fluid": "crude-oil", "separated_by_pump": False, "tiles": [(1, 0)]},
        ],
    }
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "fluid_mixing")
    assert (0, 0) in failure["positions"] and (1, 0) in failure["positions"]


# --- 5. underground span -------------------------------------------------------

def test_underground_span_too_long_is_caught():
    bundle = _bundle(
        _entity("pipe-to-ground", 5.5, 0.5, direction="south", name="stub"),
        _entity("pipe-to-ground", 5.5, 15.5, direction="north", name="riser"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "underground_span")
    assert (5.5, 0.5) in failure["positions"] and (5.5, 15.5) in failure["positions"]


# --- 6. inserter sanity --------------------------------------------------------

def test_inserter_with_no_pickup_or_drop_is_caught():
    bundle = _bundle(_entity("fast-inserter", 5.5, 5.5, direction="north", name="dead_inserter"))
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "inserter_sanity")
    assert "never move anything" in failure["detail"]


# --- 7. roboport coverage -------------------------------------------------------

def test_roboport_coverage_gap_is_caught():
    # Construction radius only matters for ghosts (bot-built); a real gap must
    # be a place_ghost far outside the roboport's construction radius.
    bundle = _bundle(
        _entity("roboport", 0, 0, name="hub"),
        _entity("assembling-machine-2", 200.5, 200.5, action_type="place_ghost", name="far_machine"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "roboport_coverage")
    assert (200.5, 200.5) in failure["positions"]


def test_roboport_split_network_is_caught():
    bundle = _bundle(
        _entity("roboport", 0, 0, name="hub_a"),
        _entity("roboport", 200, 200, name="hub_b"),
    )
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "roboport_coverage")
    assert "separate logistic network" in failure["detail"]


# --- 8. seeded-water terrain --------------------------------------------------

def test_water_lake_overlap_is_caught() -> None:
    bundle = {
        "infrastructure": [_entity("medium-electric-pole", 17.5, 86.5, name="wet_pole")],
        "plans": [],
        "water_lake_tiles": [(17, 86)],
    }

    result = preflight(bundle)

    assert not result["ok"]
    assert any(failure["check"] == "water_lake_overlap" for failure in result["failures"])


def test_declared_shoreline_offshore_pump_is_allowed_at_water_edge() -> None:
    bundle = {
        "infrastructure": [_entity("offshore-pump", 20.5, 84.5, name="shore_pump")],
        "plans": [],
        "water_lake_tiles": [(20, 84)],
        "water_shoreline_pump_positions": [(20.5, 84.5)],
    }

    result = preflight(bundle)

    assert not any(failure["check"] == "water_lake_overlap" for failure in result["failures"])

# --- 8. electric-only ------------------------------------------------------------

def test_electric_only_rejects_burner_entities():
    bundle = _bundle(_entity("stone-furnace", 3.5, 3.5, name="burner"))
    result = preflight(bundle)
    assert not result["ok"]
    failure = next(f for f in result["failures"] if f["check"] == "electric_only")
    assert "stone-furnace" in failure["detail"]


# --- clean bundle + skip reporting ---------------------------------------------

def test_clean_bundle_passes_every_runnable_check():
    # source(size2)@(0,0) <-2 tiles-> pole(size2, supply 2)@(2,0): no footprint
    # overlap (distance 2 == sum of half-sizes) and the pole's supply area
    # (half 2) reaches the source. The pole's supply area also reaches the
    # roboport@(5,0) (distance 3 < half 1 + half 2 = 3 is false at the boundary,
    # so use distance 3 with pole half 1 -- overlap check uses <, and coverage
    # uses the pole's supply half of 2 against the roboport's half of 2, 3 < 4).
    bundle = _bundle(
        _entity("electric-energy-interface", 0, 0, name="source"),
        _entity("big-electric-pole", 2, 0, name="root_pole"),
        _entity("roboport", 5, 0, name="hub"),
    )
    result = preflight(bundle)
    assert result["failures"] == []
    assert result["ok"] is True
    # fluid_mixing (no fluid_segments) and underground_span (no pipe-to-ground)
    # cannot run over this bundle and must be reported skipped, not passed.
    skipped_checks = {s["check"] for s in result["skipped"]}
    assert "fluid_mixing" in skipped_checks
    assert "underground_span" in skipped_checks


def test_unrunnable_check_is_reported_skipped_not_passed():
    # No roboports anywhere in the bundle: roboport_coverage cannot run.
    bundle = _bundle(_entity("transport-belt", 0.5, 0.5, direction="east", name="belt"))
    result = preflight(bundle)
    skipped = {s["check"]: s["why"] for s in result["skipped"]}
    assert "roboport_coverage" in skipped
    assert "roboport_coverage" not in result["checked"]
    assert not any(f["check"] == "roboport_coverage" for f in result["failures"])


def test_assert_preflight_raises_readable_message_on_failure():
    bundle = _bundle(_entity("stone-furnace", 3.5, 3.5, name="burner"))
    with pytest.raises(ValueError, match="electric_only"):
        assert_preflight(bundle)


def test_assert_preflight_is_silent_on_a_clean_bundle():
    bundle = _bundle(
        _entity("electric-energy-interface", 0, 0, name="source"),
        _entity("big-electric-pole", 2, 0, name="root_pole"),
    )
    assert_preflight(bundle)  # must not raise


# --- real composed electronics bundle (M6: 40 plans about to be executed) -----
