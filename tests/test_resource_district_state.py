# Path: tests/test_resource_district_state.py
# Purpose: Specify persistent resource-district identity, reservations, and fail-closed recovery.

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from orchestrator.resource_district import (
    complete_pending_revision,
    district_state_path,
    DistrictStateError,
    load_district_state,
    OwnedPlacement,
    ResourceDistrictState,
    create_initial_envelope,
    reconcile_exact,
    save_district_state,
)
from planners.plan_validation import occupied_tile_indices
from planners.smelter_block import generate_managed_refinery_plan


def _district(
    episode_id: str = "episode-district-a",
    *,
    belt_capacity: float = 45.0,
) -> ResourceDistrictState:
    return create_initial_envelope(
        episode_id=episode_id,
        surface="nauvis",
        force="player",
        ore="iron-ore",
        recipe="iron-plate",
        root=(4.5, 0.5),
        output=(0.5, 0.5),
        expansion_direction="east",
        initial_drills=6,
        longitudinal_drill_limit=20,
        maximum_drills=50,
        parallel_band_pitch=8.0,
        refinery_origin=(80.0, -16.0),
        refinery_variant="standard",
        refinery_generation=1,
        belt_tier="transport-belt",
        belt_capacity_items_per_second=belt_capacity,
        drill_items_per_second=0.5,
    )


def _primary(state: ResourceDistrictState):
    return next(band for band in state.collector_bands if band.order == 0)


def test_district_id_is_deterministic_and_episode_scoped() -> None:
    first = _district("episode-one")
    retry = _district("episode-one")
    fresh = _district("episode-two")

    assert first.district_id == retry.district_id
    assert first.district_id != fresh.district_id
    assert first.episode_id == "episode-one"


def test_state_has_a_canonical_json_round_trip() -> None:
    original = _district()

    encoded = original.to_json()
    restored = ResourceDistrictState.from_json(encoded)

    assert restored == original
    assert restored.to_json() == encoded
    assert json.loads(encoded)["version"] == 1


def test_phase_one_reserves_the_complete_growth_envelope() -> None:
    state = _district()
    primary = _primary(state)

    assert len(primary.column_xs) == 10
    assert len(primary.built_column_xs) == 3
    assert primary.pending_column_xs == ()
    assert len(set(primary.column_xs) - set(primary.built_column_xs)) == 7

    parallel = sorted(
        (band for band in state.collector_bands if band.order),
        key=lambda band: band.order,
    )
    assert {band.order for band in parallel} >= {-1, 1}
    assert all(
        band.belt_y == primary.belt_y + band.order * state.parallel_band_pitch
        for band in parallel
    )

    assert {
        "longitudinal", "parallel_bands", "manifold", "egress", "service",
        "refinery",
    } <= set(state.reservations)
    assert all(state.reservations[name] for name in (
        "longitudinal", "parallel_bands", "manifold", "egress", "service",
    ))

    maximum_refinery = generate_managed_refinery_plan(
        "iron-plate",
        48,
        origin_x=state.refinery.origin[0],
        origin_y=state.refinery.origin[1],
        variant=state.refinery.variant,
    )
    expected_tiles = frozenset(occupied_tile_indices([("generation-1", maximum_refinery)]))
    assert state.refinery.generation == 1
    assert state.refinery.maximum_furnaces == 48
    assert state.refinery.maximum_footprint_tiles == expected_tiles
    assert expected_tiles <= state.reservations["refinery"]


def test_recovery_requires_persisted_state() -> None:
    with pytest.raises(DistrictStateError, match="persisted.*required"):
        reconcile_exact(
            None,
            episode_id="episode-district-a",
            observed=(),
        )


def test_recovery_rejects_an_episode_or_exact_geometry_mismatch() -> None:
    state = _district("episode-one")
    built = OwnedPlacement(
        action_id="mine:drill:000",
        entity="electric-mining-drill",
        position=(4.5, -1.5),
        direction="south",
        status="built",
        revision=1,
    )
    persisted = replace(state, revision=1, owned_placements=(built,))

    with pytest.raises(DistrictStateError, match="episode"):
        reconcile_exact(persisted, episode_id="episode-two", observed=(built,))

    rotated = replace(built, direction="west")
    with pytest.raises(DistrictStateError, match="owned placement.*mismatch"):
        reconcile_exact(persisted, episode_id="episode-one", observed=(rotated,))


def test_recovery_preserves_exact_built_and_ghost_ownership_for_pending_revision() -> None:
    state = _district()
    built = OwnedPlacement(
        action_id="mine:drill:000",
        entity="electric-mining-drill",
        position=(4.5, -1.5),
        direction="south",
        status="built",
        revision=1,
    )
    ghost = OwnedPlacement(
        action_id="mine:drill:006",
        entity="electric-mining-drill",
        position=(13.5, 2.5),
        direction="north",
        status="ghost",
        revision=2,
    )
    persisted = replace(
        state,
        revision=1,
        pending_revision=2,
        pending_plan_id="district-phase-20-r2",
        owned_placements=(built, ghost),
    )

    recovered = reconcile_exact(
        persisted,
        episode_id=state.episode_id,
        observed=(ghost, built),
    )

    assert recovered.revision == 1
    assert recovered.pending_revision == 2
    assert recovered.pending_plan_id == "district-phase-20-r2"
    assert recovered.owned_placements == (built, ghost)


def test_recovery_accepts_only_forward_ghost_to_built_transition() -> None:
    state = _district()
    ghost = OwnedPlacement(
        action_id="mine:drill:006",
        entity="electric-mining-drill",
        position=(13.5, 2.5),
        direction="north",
        status="ghost",
        revision=1,
    )
    persisted = replace(
        state,
        pending_revision=1,
        pending_plan_id="district-phase-20-r1",
        owned_placements=(ghost,),
    )
    built = replace(ghost, status="built")

    recovered = reconcile_exact(
        persisted, episode_id=state.episode_id, observed=(built,),
    )
    completed = complete_pending_revision(recovered)

    assert completed.revision == 1
    assert completed.pending_revision is None
    assert completed.pending_plan_id is None
    assert completed.owned_placements == (built,)

    with pytest.raises(DistrictStateError, match="status.*regressed"):
        reconcile_exact(
            replace(persisted, revision=1, pending_revision=None,
                    pending_plan_id=None, owned_placements=(built,)),
            episode_id=state.episode_id,
            observed=(ghost,),
        )


def test_episode_scoped_state_write_is_atomic_and_round_trips(tmp_path) -> None:
    state = _district("episode-persisted")

    save_district_state(tmp_path, state)

    path = district_state_path(
        tmp_path, episode_id=state.episode_id, surface=state.surface,
        force=state.force, ore=state.ore,
    )
    assert path.is_file()
    assert not list(path.parent.glob("*.tmp"))
    assert load_district_state(
        tmp_path, episode_id=state.episode_id, surface=state.surface,
        force=state.force, ore=state.ore,
    ) == state
    assert load_district_state(
        tmp_path, episode_id="another-episode", surface=state.surface,
        force=state.force, ore=state.ore,
    ) is None


@pytest.mark.parametrize("field", [
    "parallel_band_pitch",
    "belt_capacity_items_per_second",
    "drill_items_per_second",
])
def test_non_finite_persisted_numeric_state_is_rejected(field: str) -> None:
    payload = json.loads(_district().to_json())
    payload[field] = float("inf")

    with pytest.raises(DistrictStateError, match="finite"):
        ResourceDistrictState.from_json(json.dumps(payload))
