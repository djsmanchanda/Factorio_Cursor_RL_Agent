# Path: tests/test_optimizer_demand_to_ghost.py | Purpose: Offline integration
# proof from exact catalog bytes through demand, explicit machine facts, line
# geometry, a synthetic ghost artifact and a disposable reservation ledger.
#
# The tracked tests/fixtures/player_recipe_catalog.json bytes contain no
# "machines" records, so exact machine facts cannot be derived from them
# without inventing a catalog. This test instead uses the exact bytes of the
# local historical catalog export, which already contains both the chain and
# its machine records. Nothing below contacts a server, RCON, save, surface,
# stock, runtime or live force; every identity and item mapping is synthetic.

from __future__ import annotations

from fractions import Fraction
import hashlib
import json
from pathlib import Path

import pytest

from core.catalog_demand import mod_set_sha256, plan_catalog_demand
from core.fixed_recipe_machines import compute_fixed_recipe_machines
from core.science_line_candidate import (
    ScienceLineCandidateError,
    ScienceLineCandidateReason,
    build_science_line_candidate,
)
from core.optimizer_artifact_binding import bind_artifact_to_construction_plan
from core.optimizer_construction_state import ConstructionScope
from orchestrator.material_reservations import MaterialReservationLedger
from planners.ghost_line_artifact import (
    ARTIFACT_STATUS,
    ARTIFACT_VERSION,
    derive_ghost_artifact,
)
from planners.plan_validation import INFINITY_ENTITIES, assert_no_production_infinity
from training.isolation import assert_training_identity

_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "data/factorio-2.1.21/probes/deterministic/server/script-output"
    / "factorio_mod/recipe_catalogs/recipe_catalog_243688.json"
)
_FIXTURE_SHA256 = "9792354548cef192aac4c24dd76637d0c3a27cc787f87f537eca7acf915da71c"

_SCENARIO_ID = "optimizer-demand-to-ghost-001"
_SURFACE = f"training/{_SCENARIO_ID}"
_FORCE = f"training-{_SCENARIO_ID}"
_EPISODE_ID = f"synthetic-episode-{_SCENARIO_ID}"

# SYNTHETIC provenance: only the catalog hash, force and tick are observed from
# the stored export. The version, surface, command and mod set are test values,
# never claims about the export's real runtime or mod state.
_SYNTHETIC_SURFACE = "synthetic-offline-fixture-surface"
_SYNTHETIC_MODS = [{"name": "synthetic-offline-test-mod", "version": "0.0.0"}]
_SYNTHETIC_ITEM_MAPPING_PROVENANCE = (
    "SYNTHETIC TEST FIXTURE ONLY: hand-authored prototype-to-item mapping; "
    "never exported from, or verified against, a Factorio runtime"
)

_PREFERENCES = {
    "automation-science-pack": "automation-science-pack",
    "copper-plate": "copper-plate",
    "iron-gear-wheel": "iron-gear-wheel",
    "iron-plate": "iron-plate",
}
_BOUNDARIES = [{"name": "copper-ore", "cap": 5}, {"name": "iron-ore", "cap": 5}]
_MACHINES = {
    "automation-science-pack": "assembling-machine-2",
    "copper-plate": "electric-furnace",
    "iron-gear-wheel": "assembling-machine-2",
    "iron-plate": "electric-furnace",
}
_LINE_SPEC = {
    "recipe": "automation-science-pack",
    "machine_count": 7,
    "origin_x": -40,
    "origin_y": 24,
    "belt_type": "transport-belt",
    "inserter_type": "fast-inserter",
    "mining_feed": False,
    "feed_style": "chained",
    "direct_bus_ingredients": [],
    "chained_ingredients": [0, 1],
    "terminal_collector": True,
}


def _provenance(fixture_bytes: bytes, catalog: dict) -> dict:
    return {
        "catalog_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "game_version": "0.0.0",
        "force": catalog["force"],
        "surface": _SYNTHETIC_SURFACE,
        "export_tick": catalog["tick"],
        "export_command": "/synthetic-offline-test-export",
        "mods": [dict(mod) for mod in _SYNTHETIC_MODS],
        "mod_set_sha256": mod_set_sha256(_SYNTHETIC_MODS),
    }


def _ledger(tmp_path: Path, *, force: str = _FORCE) -> MaterialReservationLedger:
    return MaterialReservationLedger(
        tmp_path / "script-output" / "factorio_cursor_rl",
        episode_id=_EPISODE_ID,
        surface=_SURFACE,
        force=force,
    )


def _assert_non_authorizing(candidate) -> None:
    assert candidate.deployable is False
    assert candidate.player_force_build_claim is False


def test_exact_fixture_demand_reaches_ghost_and_held_reservation(tmp_path: Path) -> None:
    if not _FIXTURE.is_file():
        pytest.skip("local historical recipe export is unavailable")
    fixture_bytes = _FIXTURE.read_bytes()
    catalog = json.loads(fixture_bytes)

    assert hashlib.sha256(fixture_bytes).hexdigest() == _FIXTURE_SHA256
    assert catalog["force"] == "player"
    assert catalog["machines"], "exact fixture bytes must carry machine records"

    demand_result = plan_catalog_demand(
        fixture_bytes,
        provenance=_provenance(fixture_bytes, catalog),
        demand={"automation-science-pack": 1},
        boundaries=_BOUNDARIES,
        preferences=_PREFERENCES,
    )
    assert demand_result.balance.status == "unique"
    assert demand_result.balance.feasible is True
    assert demand_result.balance.shortages == ()
    assert demand_result.selected_recipes == (
        "automation-science-pack",
        "copper-plate",
        "iron-gear-wheel",
        "iron-plate",
    )
    assert dict(zip(demand_result.balance.recipe_names, demand_result.balance.rates)) == {
        "automation-science-pack": Fraction(1),
        "copper-plate": Fraction(1),
        "iron-gear-wheel": Fraction(1),
        "iron-plate": Fraction(2),
    }

    facts = {
        fact.recipe: fact
        for fact in compute_fixed_recipe_machines(
            catalog,
            balance=demand_result.balance,
            machine_by_recipe=_MACHINES,
        ).facts
    }
    pack = facts["automation-science-pack"]
    assert pack.required_crafts_per_second == Fraction(1)
    assert pack.exact_machines == Fraction(20, 3)
    assert pack.machine_count == 7
    assert pack.nominal_crafts_per_second == Fraction(21, 20)
    assert pack.crafting_speed == Fraction(3, 4)
    assert {name: fact.machine_count for name, fact in facts.items()} == {
        "automation-science-pack": 7,
        "copper-plate": 2,
        "iron-gear-wheel": 1,
        "iron-plate": 4,
    }

    candidate = build_science_line_candidate(
        demand_result,
        catalog,
        recipe="automation-science-pack",
        line_spec=_LINE_SPEC,
        machine_by_recipe=_MACHINES,
        catalog_bytes=fixture_bytes,
    )
    assert candidate.machine == "assembling-machine-2"
    assert candidate.requested_machine_count == 7
    assert candidate.required_machine_count == 7
    assert candidate.count_status == "adequate"
    assert candidate.machine_fact == pack
    assert candidate.geometry["ghost_entities"]["assembling-machine-2"] == 7
    _assert_non_authorizing(candidate)

    prototypes = set(candidate.ghost_entities) | set(candidate.direct_entities)
    artifact = derive_ghost_artifact(
        candidate.geometry["plan"],
        item_mapping={
            "provenance": _SYNTHETIC_ITEM_MAPPING_PROVENANCE,
            "entities": {name: name for name in sorted(prototypes)},
            "tiles": {},
        },
    )
    assert artifact["artifact_version"] == ARTIFACT_VERSION
    assert artifact["status"] == ARTIFACT_STATUS
    assert artifact["executable"] is False
    assert artifact["player_force_build_claim"] is False
    assert set(artifact["claims"].values()) == {False}
    assert artifact["counts"] == {
        "ghost_placements": 76,
        "direct_placements": 0,
        "removals": 0,
        "converted_direct_placements": 2,
    }
    assert artifact["declared_item_bill"] == {
        "assembling-machine-2": 7,
        "fast-inserter": 15,
        "medium-electric-pole": 8,
        "steel-chest": 1,
        "transport-belt": 45,
    }
    actions = [
        action
        for phase in artifact["plan"]["phases"]
        for action in phase["actions"]
    ]
    assert {action["action_type"] for action in actions} == {"place_ghost"}
    assert not any(action.get("entity") in INFINITY_ENTITIES for action in actions)
    assert_no_production_infinity([("synthetic-ghost-line", artifact["plan"])])

    assert_training_identity(_SURFACE, _FORCE)
    bill = dict(artifact["declared_item_bill"])
    short_stock = {**bill, "assembling-machine-2": 2}
    project_id = "synthetic_demand_to_ghost_commissioning"

    ledger = _ledger(tmp_path)
    short = ledger.declare(
        project_id,
        bill,
        short_stock,
        target_item="automation-science-pack",
        priority=100,
        hold_until_producing=True,
    )
    assert short.state == "supply_wait"
    assert short.reserved == {
        **{item: count for item, count in bill.items() if item != "assembling-machine-2"},
        "assembling-machine-2": 2,
    }
    assert short.hold_until_producing is True
    assert ledger.shortage_targets(project_id, short_stock) == {
        "assembling-machine-2": 7,
    }

    funded = _ledger(tmp_path).declare(
        project_id,
        bill,
        bill,
        target_item="automation-science-pack",
        priority=100,
        hold_until_producing=True,
    )
    assert funded.state == "ready"
    assert funded.reserved == bill
    assert funded.eta_seconds == {item: 0.0 for item in bill}

    funded_ledger = _ledger(tmp_path)
    funded_ledger.mark_constructing(project_id, bill)
    held = _ledger(tmp_path).projects[project_id]
    assert held.state == "constructing"
    assert held.hold_until_producing is True
    assert held.reserved == bill
    assert _ledger(tmp_path).project_is_active(project_id) is True
    assert _ledger(tmp_path).stock_targets() == bill

    undersized = build_science_line_candidate(
        demand_result,
        catalog,
        recipe="automation-science-pack",
        line_spec={**_LINE_SPEC, "machine_count": 3},
        machine_by_recipe=_MACHINES,
        catalog_bytes=fixture_bytes,
    )
    assert undersized.requested_machine_count == 3
    assert undersized.required_machine_count == 7
    assert undersized.count_status == "undersized"
    assert undersized.machine_shortfall == 4
    assert undersized.geometry["spec"]["machine_count"] == 3
    assert undersized.geometry["ghost_entities"]["assembling-machine-2"] == 3
    assert undersized.machine_fact.machine_count == 7
    _assert_non_authorizing(undersized)

    altered = json.loads(json.dumps(catalog))
    next(
        machine
        for machine in altered["machines"]
        if machine["name"] == "assembling-machine-2"
    )["crafting_speed"] = 1.5
    with pytest.raises(ScienceLineCandidateError) as mapping_mismatch:
        build_science_line_candidate(
            demand_result,
            altered,
            recipe="automation-science-pack",
            line_spec=_LINE_SPEC,
            machine_by_recipe=_MACHINES,
            catalog_bytes=fixture_bytes,
        )
    assert mapping_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "mapping differs" in str(mapping_mismatch.value)

    altered_bytes = json.dumps(
        altered, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    with pytest.raises(ScienceLineCandidateError) as hash_mismatch:
        build_science_line_candidate(
            demand_result,
            altered,
            recipe="automation-science-pack",
            line_spec=_LINE_SPEC,
            machine_by_recipe=_MACHINES,
            catalog_bytes=altered_bytes,
        )
    assert hash_mismatch.value.reason is ScienceLineCandidateReason.CATALOG_MISMATCH
    assert "hash" in str(hash_mismatch.value)

    shortage_result = plan_catalog_demand(
        fixture_bytes,
        provenance=_provenance(fixture_bytes, catalog),
        demand={"automation-science-pack": 1},
        boundaries=[{"name": "copper-ore", "cap": 0}, {"name": "iron-ore", "cap": 5}],
        preferences=_PREFERENCES,
    )
    assert shortage_result.balance.status == "unique"
    assert shortage_result.balance.feasible is False
    assert [
        (shortage.material, shortage.required, shortage.cap)
        for shortage in shortage_result.balance.shortages
    ] == [("copper-ore", Fraction(1), Fraction(0))]
    with pytest.raises(ScienceLineCandidateError) as shortage_candidate:
        build_science_line_candidate(
            shortage_result,
            catalog,
            recipe="automation-science-pack",
            line_spec=_LINE_SPEC,
            machine_by_recipe=_MACHINES,
            catalog_bytes=fixture_bytes,
        )
    assert shortage_candidate.value.reason is ScienceLineCandidateReason.UNSUPPORTED_BALANCE

    with pytest.raises(ScienceLineCandidateError) as wrong_machine:
        build_science_line_candidate(
            demand_result,
            catalog,
            recipe="automation-science-pack",
            line_spec=_LINE_SPEC,
            machine_by_recipe={
                **_MACHINES,
                "automation-science-pack": "assembling-machine-3",
            },
            catalog_bytes=fixture_bytes,
        )
    assert wrong_machine.value.reason is ScienceLineCandidateReason.MACHINE_GEOMETRY_MISMATCH


def test_requester_fed_red_block_is_bot_buildable_with_no_infinity(tmp_path: Path) -> None:
    """Early-world red block: AM1 + plain inserters, plates requested from the network."""
    if not _FIXTURE.is_file():
        pytest.skip("local historical recipe export is unavailable")
    fixture_bytes = _FIXTURE.read_bytes()
    catalog = json.loads(fixture_bytes)
    machines = {**_MACHINES, "automation-science-pack": "assembling-machine-1"}
    line_spec = {
        **_LINE_SPEC, "machine_count": 2, "machine": "assembling-machine-1",
        "inserter_type": "inserter", "feed_style": "requester", "chained_ingredients": [],
    }
    demand_result = plan_catalog_demand(
        fixture_bytes,
        provenance=_provenance(fixture_bytes, catalog),
        demand={"automation-science-pack": Fraction(1, 5)},
        boundaries=_BOUNDARIES,
        preferences=_PREFERENCES,
    )
    candidate = build_science_line_candidate(
        demand_result, catalog, recipe="automation-science-pack", line_spec=line_spec,
        machine_by_recipe=machines, catalog_bytes=fixture_bytes,
    )
    # 0.2 packs/s at AM1 speed 0.5 over a 5s recipe is exactly two machines.
    assert candidate.machine == "assembling-machine-1"
    assert candidate.required_machine_count == 2
    assert candidate.count_status == "adequate"
    assert candidate.has_direct_actions is False
    assert candidate.direct_entities == {}

    prototypes = set(candidate.ghost_entities)
    artifact = derive_ghost_artifact(
        candidate.geometry["plan"],
        item_mapping={
            "provenance": _SYNTHETIC_ITEM_MAPPING_PROVENANCE,
            "entities": {name: name for name in sorted(prototypes)},
            "tiles": {},
        },
    )
    actions = [a for phase in artifact["plan"]["phases"] for a in phase["actions"]]
    assert {a["action_type"] for a in actions} == {"place_ghost"}
    assert not any(a["entity"] in INFINITY_ENTITIES for a in actions)
    assert artifact["counts"]["converted_direct_placements"] == 0

    requesters = {
        a["logistic_requests"][0]["name"]: a["logistic_requests"][0]["count"]
        for a in actions if a["entity"] == "requester-chest"
    }
    assert set(requesters) == {"copper-plate", "iron-gear-wheel"}
    # 120s of one feeder's share of 0.2/s demand, above the 10-item floor.
    assert requesters == {"copper-plate": 24, "iron-gear-wheel": 24}
    assert all(
        len(a["logistic_requests"]) == 1 for a in actions if a["entity"] == "requester-chest"
    )
    outputs = [a for a in actions if a["entity"] == "passive-provider-chest"]
    assert len(outputs) == 1 and "logistic_requests" not in outputs[0]
    assert "steel-chest" not in artifact["declared_item_bill"]

    # 2 machines: 2 input + 2 output inserters, 2 feeder inserters, 1 collector.
    assert artifact["declared_item_bill"] == {
        "assembling-machine-1": 2,
        "inserter": 7,
        "medium-electric-pole": 4,
        "passive-provider-chest": 1,
        "requester-chest": 2,
        "transport-belt": 14,
    }

    # The requests survive into the hashed binding (EntityRef itself carries
    # identity only; the canonical artifact is what the binding digest covers).
    bound = bind_artifact_to_construction_plan(
        artifact,
        scope=ConstructionScope(
            instance_id="synthetic-requester-instance", manifest_digest="a" * 64,
            surface=_SURFACE, force=_FORCE,
        ),
        project_id="synthetic_requester_red_block", transaction_id="synthetic-transaction",
        throughput_output="automation-science-pack", throughput_target=Fraction(1, 5),
        throughput_unit="items/second",
    )
    bound_actions = [a for phase in bound.artifact["plan"]["phases"] for a in phase["actions"]]
    assert dict(bound.plan.required_materials) == artifact["declared_item_bill"]
    assert json.loads(bound.canonical_binding)["artifact"]["plan"] == artifact["plan"]
    assert {
        a["logistic_requests"][0]["name"] for a in bound_actions if a["entity"] == "requester-chest"
    } == {"copper-plate", "iron-gear-wheel"}
