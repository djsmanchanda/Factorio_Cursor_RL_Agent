# Path: tests/test_extraction_separation.py
# Purpose: Prove real-base extraction keeps furnace production off ore and out of the drill row.

from __future__ import annotations

import pytest

from orchestrator import autonomous_builder, extraction_state, live_base, resource_patches, stage_extraction
from orchestrator.extraction_state import (
    ExtractionEntity,
    ResourceMine,
    _classify_direct_mine,
)
from orchestrator.stage_extraction import (
    LOCAL_MODE_MAX_LINK_TILES,
    LocalExtractionPlan,
    _align_area_anchor,
    _smelter_geometry,
    direct_mine_plan,
    ore_reservation,
    plan_local_extraction,
    planned_smelter_count_for_drills,
    smelter_count_for_drills,
    smelter_search_anchors,
)
from planners.smelter_block import generate_managed_refinery_plan
from planners.plan_validation import ENTITY_FOOTPRINTS, actions
from planners.zoning_geometry import Rect


@pytest.fixture(autouse=True)
def _disable_live_retirement(monkeypatch) -> None:
    monkeypatch.setattr(
        autonomous_builder, "retire_depleted_mines", lambda *_args, **_kwargs: 0,
    )

def _entities(plan: dict) -> list[str]:
    return [
        action["entity"]
        for phase in plan["phases"]
        for action in phase["actions"]
    ]


def test_direct_mine_plan_contains_drills_and_egress_but_no_furnaces() -> None:
    plan, output = direct_mine_plan(
        (10, 20), 3,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert output == (7.5, 20.5)
    assert _entities(plan).count("electric-mining-drill") == 6
    assert "electric-furnace" not in _entities(plan)
    assert "steel-chest" not in _entities(plan)
    assert "inserter" not in _entities(plan)


def test_east_direct_mine_paves_a_bounded_straight_collector_continuation() -> None:
    plan, output = direct_mine_plan(
        (40.0, -1.0), 6, belt_type="fast-transport-belt",
        inserter_type="fast-inserter", output_side="east",
        continuation_tiles=6,
    )

    # Drill head is x=56.5; the returned haul head is exactly six tiles past
    # it, and the continuation is counted once rather than twice.
    assert output == (64.5, -0.5)
    geometry = plan["collector_geometry"]
    assert geometry["collector_head"] == output
    # The unbuilt westward drill corridor is reserved even though its ghosts
    # are intentionally not placed until the next six-drill checkpoint.
    assert (10, -5) in {tuple(tile) for tile in plan["reserved_tiles"]}
    belts = [
        action for action in plan["phases"][1]["actions"]
        if action.get("entity") == "fast-transport-belt"
    ]
    assert belts[-1]["position"] == {"x": output[0], "y": output[1]}


def test_parallel_row_expansion_declares_splitter_merge() -> None:
    from planners.resource_layouts import generate_parallel_mining_row_expansion

    plan = generate_parallel_mining_row_expansion(
        [44.5, 47.5, 50.5, 53.5, 56.5, 59.5], -1.5, merge_x=64.5,
    )
    actions = list(plan["phases"][1]["actions"])
    splitters = [action for action in actions if action["entity"] == "fast-splitter"]
    tunnels = [action for action in actions if action["entity"] == "fast-underground-belt"]
    removals = [action for action in actions if action["action_type"] == "remove_entity"]
    belts = [action for action in actions if action["entity"] == "fast-transport-belt"]
    assert len(splitters) == 1
    assert plan["atomic"] is True
    assert removals == [{
        "action_type": "remove_entity", "entity": "fast-transport-belt",
        "position": {"x": 64.5, "y": -1.5},
    }]
    assert {action["underground_type"] for action in tunnels} == {"input", "output"}
    assert all(action["position"]["x"] % 1 == 0.5 for action in belts)
    assert plan["parallel_merge"]["splitter"] == (
        splitters[0]["position"]["x"], splitters[0]["position"]["y"],
    )

def test_planned_metal_refinery_stays_proportional_to_drills() -> None:
    assert planned_smelter_count_for_drills("iron-plate", 6, 0.30) == 6
    assert planned_smelter_count_for_drills("copper-plate", 12, 0.30) == 12
    assert planned_smelter_count_for_drills("iron-plate", 20, 0.30) == 18


def test_furnaces_use_force_productivity_instead_of_copying_drill_count() -> None:
    assert smelter_count_for_drills("iron-plate", 2, 0.0) == 2
    assert smelter_count_for_drills("iron-plate", 5, 0.0) == 4
    assert smelter_count_for_drills("iron-plate", 2, 0.40) == 3


def test_stone_brick_furnaces_are_sized_on_two_stone_per_craft() -> None:
    # Six drills at +30% produce 3.9 stone/s; each electric furnace consumes
    # 1.25 stone/s for stone-brick, so four furnaces cover that row.
    assert smelter_count_for_drills("stone-brick", 6, 0.30) == 4

def test_exact_smelter_bounds_include_west_feed_and_substation() -> None:
    bounds, feed = _smelter_geometry(
        "iron-plate", 2, "fast-transport-belt", "fast-inserter"
    )

    assert bounds == Rect(-1.0, 0.0, 17.0, 16.0)
    assert feed == (-0.5, 0.5)


def test_smelter_search_alignment_produces_integer_line_origins() -> None:
    bounds = Rect(-5.0, -2.0, 8.0, 7.0)
    aligned = _align_area_anchor((-18.5, 10.5), bounds)

    assert aligned == (-19.0, 10.0)
    assert (aligned[0] - bounds.min_x).is_integer()
    assert (aligned[1] - bounds.min_y).is_integer()


def test_smelter_anchors_never_overlap_the_approximate_ore_apron() -> None:
    patch_min, patch_max = (0.0, 0.0), (40.0, 30.0)
    footprint = (13.0, 9.0)
    reserved_min, reserved_max = ore_reservation(patch_min, patch_max)
    reserved = Rect(*reserved_min, *reserved_max)

    first = smelter_search_anchors(
        patch_min, patch_max, footprint, reference_point=(-100.0, 0.0)
    )
    assert first == smelter_search_anchors(
        patch_min, patch_max, footprint, reference_point=(-100.0, 0.0)
    )
    for min_x, min_y in first:
        assert not Rect(
            min_x, min_y, min_x + footprint[0], min_y + footprint[1]
        ).overlaps(reserved)


class _AreaRcon:
    """Models the distinct queries find_clear_area makes, so a test can drive
    each independently: the region occupancy scan, the region resource
    prefilter, and the exact per-candidate reserved-patch check."""

    commands: list[str]

    def __init__(
        self, *, occupied: str = "", resources: str = "",
        patch_amount: int = resource_patches.MINIMUM_NEW_PATCH_RESOURCE,
    ) -> None:
        self.commands = []
        self.occupied = occupied
        self.resources = resources
        self.patch_amount = patch_amount

    def command(self, command: str) -> str:
        self.commands.append(command)
        if "collision_mask='water_tile'" in command:
            return self.occupied
        if "local grid={}" in command:  # nearest_patch's flood fill
            return f"80 80 80 80 81 81 {self.patch_amount}"
        if "seen[e.name]" in command:  # box_has_reserved_patch's name sample
            return "iron-ore,80.5,80.5" if self.resources else ""
        if "type='resource'" in command:  # region resource_tiles prefilter
            return self.resources
        return "0 0 0"

    def issued(self, marker: str) -> list[str]:
        return [command for command in self.commands if marker in command]


def test_clear_area_checks_an_ore_apron_beyond_the_actual_footprint() -> None:
    """The exact reserved-patch check must cover the apron, not just the
    footprint -- a furnace row flush against ore still blocks the drills."""
    client = _AreaRcon(resources="80,80")

    live_base.find_clear_area(
        client, "nauvis", (80.0, 80.0), 13.0, 9.0,
        max_radius=0.0, avoid_resources=True, resource_clearance=5.0,
    )

    exact_check = next(iter(client.issued("seen[e.name]")))
    assert "area={{75.0,75.0},{98.0,94.0}}" in exact_check


def test_clear_area_rejects_resources_found_only_inside_the_apron() -> None:
    client = _AreaRcon(resources="75,75")  # apron-only: outside the 80..93 footprint

    assert live_base.find_clear_area(
        client, "nauvis", (80.0, 80.0), 13.0, 9.0,
        max_radius=0.0, avoid_resources=True, resource_clearance=5.0,
    ) is None


def test_clear_area_keeps_land_whose_patch_is_too_depleted_to_reserve() -> None:
    """Ore alone does not reserve land -- only ore still worth mining does."""
    client = _AreaRcon(
        resources="75,75",
        patch_amount=resource_patches.MINIMUM_NEW_PATCH_RESOURCE - 1,
    )

    assert live_base.find_clear_area(
        client, "nauvis", (80.0, 80.0), 13.0, 9.0,
        max_radius=0.0, avoid_resources=True, resource_clearance=5.0,
    ) == (80.0, 80.0)


def test_clear_area_skips_the_patch_probe_for_ore_free_candidates() -> None:
    """The expensive per-candidate patch flood must not run where the region
    scan already proved there is no resource at all."""
    client = _AreaRcon(resources="")

    assert live_base.find_clear_area(
        client, "nauvis", (80.0, 80.0), 13.0, 9.0,
        max_radius=0.0, avoid_resources=True, resource_clearance=5.0,
    ) == (80.0, 80.0)
    assert client.issued("seen[e.name]") == []
    assert client.issued("local grid={}") == []


def test_clear_area_scans_each_region_once_regardless_of_candidate_count() -> None:
    """Siting cost must not scale with the number of positions probed."""
    client = _AreaRcon(occupied="80,80")  # forces the first candidates to be rejected

    live_base.find_clear_area(
        client, "nauvis", (80.0, 80.0), 13.0, 9.0,
        max_radius=60.0, avoid_resources=True, resource_clearance=5.0,
    )

    assert len(client.issued("collision_mask='water_tile'")) == 1
    assert len(client.issued("out[#out+1]=math.floor(e.position.x)")) == 1


class _StateRcon:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return next(self.replies)


def _mine_entities(*, pending_drill: bool = False, pending_belt: bool = False):
    entities = [
        ExtractionEntity("drill", (11.5, 18.5), True),
        ExtractionEntity("drill", (14.5, 18.5), not pending_drill),
        ExtractionEntity("chest", (18.5, 20.5), True),
        ExtractionEntity("inserter", (17.5, 20.5), True),
    ]
    entities.extend(
        ExtractionEntity("belt", (x + 0.5, 20.5), not (pending_belt and x == 13))
        for x in range(11, 17)
    )
    return entities


def test_direct_mine_classifier_requires_every_drill_and_belt_built() -> None:
    assert _classify_direct_mine(
        _mine_entities(), (0.0, 0.0)
    ) == ResourceMine((18.5, 20.5), 2)
    assert _classify_direct_mine(
        _mine_entities(pending_drill=True), (0.0, 0.0)
    ) == ResourceMine((18.5, 20.5), 2, pending=True)
    assert _classify_direct_mine(
        _mine_entities(pending_belt=True), (0.0, 0.0)
    ) == ResourceMine((18.5, 20.5), 2, pending=True)


def test_direct_mine_classifier_recognizes_side_tapped_output() -> None:
    entities = [
        ExtractionEntity("chest", (7.5, 18.5), True),
        ExtractionEntity("inserter", (7.5, 19.5), True),
        *(ExtractionEntity("belt", (x + 0.5, 20.5), True) for x in range(7, 15)),
        ExtractionEntity("drill", (11.5, 18.5), True),
        ExtractionEntity("drill", (14.5, 18.5), True),
    ]

    assert _classify_direct_mine(entities, (0.0, 0.0)) == ResourceMine(
        (7.5, 18.5), 2, expansion_step=1, row_capacity=12, belt_y=20.5,
    )

def test_direct_mine_classifier_recognizes_belt_only_output() -> None:
    entities = [
        *(ExtractionEntity("drill", (x, 18.5), True) for x in (11.5, 14.5)),
        *(ExtractionEntity("drill", (x, 22.5), True) for x in (11.5, 14.5)),
        *(ExtractionEntity("belt", (x + 0.5, 20.5), True) for x in range(7, 17)),
    ]

    assert _classify_direct_mine(entities, (0.0, 0.0)) == ResourceMine(
        (7.5, 20.5), 2, expansion_step=1, row_capacity=12, belt_y=20.5,
        first_column_x=11.5, haul_head=(16.5, 20.5), growth_direction=-1,
    )


def test_direct_mine_classifier_preserves_straight_collector_continuation() -> None:
    entities = [
        *(ExtractionEntity("drill", (x, 18.5), True) for x in (11.5, 14.5)),
        *(ExtractionEntity("drill", (x, 22.5), True) for x in (11.5, 14.5)),
        *(ExtractionEntity("belt", (x + 0.5, 20.5), True) for x in range(7, 25)),
    ]

    mine = _classify_direct_mine(entities, (0.0, 0.0))

    assert mine is not None
    assert mine.haul_head == (24.5, 20.5)


def test_direct_mine_classifier_marks_belt_only_ghosts_pending() -> None:
    entities = [
        ExtractionEntity("drill", (11.5, 18.5), True),
        ExtractionEntity("drill", (14.5, 18.5), False),
        *(ExtractionEntity("belt", (x + 0.5, 20.5), x != 10) for x in range(7, 17)),
    ]

    assert _classify_direct_mine(entities, (0.0, 0.0)).pending

def test_direct_mine_classifier_fails_closed_on_orphan_drill_ghost() -> None:
    entities = [ExtractionEntity("drill", (11.5, 18.5), False)]

    assert _classify_direct_mine(entities, (0.0, 0.0)) == ResourceMine(
        (15.5, 20.5), 1, pending=True
    )


def test_extraction_state_reads_force_bonus_and_live_entity_records() -> None:
    raw = ";".join(
        f"{entity.kind},{entity.position[0]},{entity.position[1]},"
        f"{1 if entity.built else 0}"
        for entity in _mine_entities()
    )
    client = _StateRcon(["0.4", raw])

    assert extraction_state.mining_productivity_bonus(client, "player") == 0.4
    assert extraction_state.find_resource_mine(
        client, "nauvis", "player", "iron-ore", (0.0, 0.0)
    ) == ResourceMine((18.5, 20.5), 2)
    assert "mining_drill_productivity_bonus" in client.commands[0]
    assert "d.mining_target" in client.commands[1]
    assert "ghost_name=='electric-mining-drill'" in client.commands[1]
    assert "table.concat(out,';')" in client.commands[1]


def test_extraction_state_reconciles_pending_direct_mine_records() -> None:
    raw = "drill,11.5,18.5,0"
    client = _StateRcon([raw])

    assert extraction_state.find_resource_mine(
        client, "nauvis", "player", "iron-ore", (0.0, 0.0)
    ) == ResourceMine((15.5, 20.5), 1, pending=True)


def test_pending_smelter_probe_is_ghost_and_radius_specific() -> None:
    completed = _StateRcon(["0"])
    pending = _StateRcon(["1"])

    assert not extraction_state.pending_plate_smelter(
        completed, "nauvis", "player", "copper-ore", (0.0, 0.0)
    )
    assert extraction_state.pending_plate_smelter(
        pending, "nauvis", "player", "copper-ore", (0.0, 0.0)
    )
    # Completeness is ghost-ness and nothing else. A furnace has no settable
    # recipe -- it auto-selects one from the first ore inserted -- so probing
    # get_recipe() reported every row that had not been fed yet as pending
    # forever, and plan_local_extraction refused to continue before reaching the
    # remediation that would have unstarved it. Asserting the probe is ABSENT is
    # the regression guard against reintroducing that deadlock.
    assert "entity-ghost'" in completed.commands[0]
    assert "get_recipe" not in completed.commands[0]
    # The modular templates lay furnaces out in vertical columns, so the old
    # horizontal row walk never matched a pending modular system and the runner
    # opened a duplicate one. The probe is now row-shape-blind: any furnace
    # ghost within the pending radius of the mine's output counts.
    assert "ghost_name=='electric-furnace'" in completed.commands[0]
    assert str(extraction_state._PENDING_SMELTER_RADIUS) in completed.commands[0]


def test_a_pending_system_defers_the_mission_instead_of_ending_it(monkeypatch) -> None:
    """The 2026-08-21 landfill failure: a pending system was treated as stuck,
    the run ended, and the duplicate it opened died on its own preflight."""
    monkeypatch.setattr(live_base, "available_items", lambda *_args: {})

    def pending(*_args, **_kwargs):
        raise stage_extraction.PendingSystemDeferred(
            "A pending off-ore smelter already exists near (1.0, 2.0); "
            "refusing to submit a duplicate line"
        )

    monkeypatch.setattr(autonomous_builder, "plan_local_extraction", pending)
    with pytest.raises(autonomous_builder.ProductionPrerequisiteDeferred):
        autonomous_builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (0.0, 0.0), lambda _message: None,
        )


def test_plan_local_extraction_reports_a_pending_system_as_deferred(monkeypatch) -> None:
    _patch_and_rates(monkeypatch, existing=ResourceMine(
        output=(49.5, -65.5), drill_count=3,
    ))
    monkeypatch.setattr(
        extraction_state, "pending_plate_smelter", lambda *_args: True,
    )
    with pytest.raises(stage_extraction.PendingSystemDeferred):
        plan_local_extraction(
            None, "nauvis", "player", "iron-plate", (0.0, 0.0), 3,
            belt_type="transport-belt", inserter_type="inserter",
        )

def _patch_and_rates(monkeypatch, *, existing: ResourceMine | None = None) -> None:
    monkeypatch.setattr(
        extraction_state, "find_resource_mine", lambda *_args: existing,
    )
    monkeypatch.setattr(
        extraction_state, "find_resource_mines",
        lambda *_args: [existing] if existing is not None else [],
    )
    monkeypatch.setattr(
        extraction_state, "resource_drill_count",
        lambda *_args: existing.drill_count * 2 if existing is not None else 0,
    )
    monkeypatch.setattr(
        extraction_state, "mining_productivity_bonus", lambda *_args: 0.0,
    )
    monkeypatch.setattr(
        extraction_state, "pending_plate_smelter", lambda *_args: False,
    )
    monkeypatch.setattr(live_base, "area_clear", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        live_base, "drill_footprints_have_resource", lambda *_args: True,
    )
    monkeypatch.setattr(
        resource_patches, "patch_for_extraction",
        lambda *_args, **_kwargs: resource_patches.ResourcePatch(
            (5.0, 5.0), (0.0, 0.0), (40.0, 30.0), 500_000,
        ),
    )
    if existing is not None:
        monkeypatch.setattr(
            "orchestrator.stage_extraction.adjacent_mine_row_state",
            lambda *_args: "complete",
        )


def test_coal_direct_belt_endpoint_is_not_checked_as_a_logistic_chest(monkeypatch) -> None:
    from orchestrator import stage_chemical

    captured = {}
    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *_a, **_k: 0)
    monkeypatch.setattr(stage_chemical.extraction_state, "find_resource_mine", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical.resource_patches, "nearest_viable_patch",
        lambda *_a, **_k: resource_patches.ResourcePatch((10, 10), (0, 0), (20, 20), 500_000),
    )
    monkeypatch.setattr(
        stage_chemical, "_coal_compatible_mining_origins", lambda *_a: [(10, 10)],
    )
    monkeypatch.setattr(stage_chemical, "choose_mining_origin", lambda *_a, **_k: ((10, 10), 2))
    monkeypatch.setattr(stage_chemical, "direct_mine_plan", lambda *_a, **_k: ({"phases": []}, (7.5, 12.5)))
    monkeypatch.setattr(stage_chemical, "strip_local_power", lambda plan, **_k: plan)
    monkeypatch.setattr(stage_chemical, "_publish_output_chest", lambda _plan: None)
    monkeypatch.setattr(stage_chemical, "_submit", lambda *_a: None)
    monkeypatch.setattr(
        stage_chemical, "existing_mine_service_geometry",
        lambda *_a, **_k: ((0, 0), ((-1, -1), (1, 1)), (0, 0), []),
    )
    service_stage = lambda *_a, **kwargs: captured.update(kwargs)

    stage_chemical.ensure_coal_mine(object(), object(), "nauvis", "player", (0, 0), service_stage, lambda _m: None)
    assert captured["logistic_chest_positions"] == []

def test_planner_translates_checked_bounds_to_the_exact_line_origin(
    monkeypatch,
) -> None:
    _patch_and_rates(monkeypatch)
    clear_calls: list[tuple] = []

    def find_clear(*args, **kwargs):
        clear_calls.append((args, kwargs))
        return (0.0, 0.0) if len(clear_calls) == 1 else (80.0, 80.0)

    monkeypatch.setattr(live_base, "find_clear_area", find_clear)
    monkeypatch.setattr(
        "orchestrator.stage_extraction.choose_mining_origin",
        lambda *_args: ((10.0, 20.0), 2),
    )

    planned = plan_local_extraction(
        object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert planned.smelter_origin == (81.0, 80.0)
    assert clear_calls[1][1] == {
        "max_radius": 60.0,
        "avoid_resources": True,
        "resource_clearance": 5.0,
    }
    assert clear_calls[1][0][3:5] == (54.0, 25.0)
    layout = generate_managed_refinery_plan(
        "iron-plate", 48, origin_x=planned.smelter_origin[0],
        origin_y=planned.smelter_origin[1],
    )
    for action in actions(layout):
        size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
        x, y = action["position"]["x"], action["position"]["y"]
        assert 80.0 <= x - size / 2 and x + size / 2 <= 134.0
        assert 80.0 <= y - size / 2 and y + size / 2 <= 105.0


def test_planner_reuses_existing_direct_mine_on_retry(monkeypatch) -> None:
    _patch_and_rates(monkeypatch, existing=ResourceMine((18.5, 20.5), 2))
    monkeypatch.setattr(live_base, "find_clear_area", lambda *_a, **_k: (80.0, 80.0))

    planned = plan_local_extraction(
        object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert planned.build_plan is None
    assert planned.mine_origin is None
    assert planned.ore_output == (18.5, 20.5)


def test_full_straight_corridor_uses_parallel_splitter_band(monkeypatch) -> None:
    mine = ResourceMine(
        output=(36.5, -1.5), drill_count=3, row_capacity=3,
        belt_y=-1.5, first_column_x=46.5, haul_head=(78.5, -1.5),
        growth_direction=-1,
    )
    _patch_and_rates(monkeypatch, existing=mine)
    monkeypatch.setattr(live_base, "find_clear_area", lambda *_a, **_k: (80.0, 80.0))
    monkeypatch.setattr(live_base, "drill_siting_conflicts", lambda *_a: [])

    planned = plan_local_extraction(
        object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 3,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
        reuse_existing=False,
    )

    assert planned.expansion_positions == (
        (46.5, 4.5), (46.5, 8.5),
        (49.5, 4.5), (49.5, 8.5),
        (52.5, 4.5), (52.5, 8.5),
    )
    assert planned.ore_output == (78.5, -1.5)
    assert {phase["name"] for phase in planned.build_plan["phases"]} == {
        "parallel_mine_power", "parallel_mine_row",
    }


def test_planner_resumes_matching_mine_ghosts_instead_of_duplicating(
    monkeypatch,
) -> None:
    pending = ResourceMine((18.5, 20.5), 2, pending=True)
    _patch_and_rates(monkeypatch, existing=pending)
    monkeypatch.setattr(
        "orchestrator.stage_extraction.adjacent_mine_row_state",
        lambda *_args: "partial",
    )
    monkeypatch.setattr(live_base, "find_clear_area", lambda *_a, **_k: (80.0, 80.0))

    planned = plan_local_extraction(
        object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert planned.build_plan is None
    assert planned.ore_output == pending.output
    assert planned.drill_count == 4


def test_planner_services_non_pending_partial_mine_row(monkeypatch) -> None:
    existing = ResourceMine((18.5, 20.5), 2, pending=False)
    _patch_and_rates(monkeypatch, existing=existing)
    monkeypatch.setattr(
        "orchestrator.stage_extraction.adjacent_mine_row_state",
        lambda *_args: "partial",
    )
    monkeypatch.setattr(live_base, "find_clear_area", lambda *_a, **_k: (80.0, 80.0))

    planned = plan_local_extraction(
        object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )

    assert planned.drill_count == 2
    assert planned.ore_output == existing.output

def test_planner_refuses_duplicate_pending_smelter(monkeypatch) -> None:
    existing = ResourceMine((18.5, 20.5), 2)
    _patch_and_rates(monkeypatch, existing=existing)
    monkeypatch.setattr(
        extraction_state, "pending_plate_smelter", lambda *_args: True,
    )
    monkeypatch.setattr(
        live_base, "nearest_resource",
        lambda *_args: pytest.fail("pending smelter must stop before site search"),
    )

    with pytest.raises(ValueError, match="pending off-ore smelter"):
        plan_local_extraction(
            object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
            belt_type="fast-transport-belt", inserter_type="fast-inserter",
        )

def test_planner_fails_closed_beyond_local_mode_link_limit(monkeypatch) -> None:
    _patch_and_rates(monkeypatch, existing=ResourceMine((18.5, 20.5), 2))
    monkeypatch.setattr(
        live_base, "find_clear_area", lambda *_a, **_k: (1000.0, 1000.0),
    )

    with pytest.raises(ValueError, match="CityPlanner rail handoff"):
        plan_local_extraction(
            object(), "nauvis", "player", "iron-plate", (0.0, 0.0), 2,
            belt_type="fast-transport-belt", inserter_type="fast-inserter",
        )
    assert LOCAL_MODE_MAX_LINK_TILES == 300.0


def test_real_builder_submits_ore_then_calls_modular_refinery(
    monkeypatch,
) -> None:
    submitted: list[dict] = []
    conversion: dict = {}
    monkeypatch.setattr(live_base, "available_items", lambda *_args: {})
    mine_plan, ore_output = direct_mine_plan(
        (10.0, 20.0), 2,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )
    planned = LocalExtractionPlan(
        ore="iron-ore", mine_origin=(10.0, 20.0), drill_count=2,
        furnace_count=2, mining_productivity_bonus=0.0,
        smelter_origin=(85.0, 82.0), ore_output=ore_output,
        build_plan=mine_plan,
    )
    monkeypatch.setattr(
        autonomous_builder, "plan_local_extraction", lambda *_a, **_k: planned,
    )
    monkeypatch.setattr(
        autonomous_builder, "_submit",
        lambda _client, _bridge, _surface, plan, _name, _emit, **_k: submitted.append(plan),
    )
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(autonomous_builder, "_diagnose_machines", lambda *_a, **_k: [])

    def build_modular(*args, **_kwargs):
        conversion["recipe"] = args[4]
        conversion["extraction"] = args[5]
        conversion["ore_output"] = args[6]
        return (120.5, 90.5)

    monkeypatch.setattr(
        autonomous_builder, "_build_initial_plate_smelter", build_modular,
    )

    output = autonomous_builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate",
        (0.0, 0.0), lambda _message: None,
    )

    assert output == (120.5, 90.5)
    assert len(submitted) == 1
    assert "electric-furnace" not in _entities(submitted[0])
    assert "passive-provider-chest" not in _entities(submitted[0])
    assert conversion == {
        "recipe": "iron-plate", "extraction": planned,
        "ore_output": (7.5, 20.5),
    }


def test_new_mine_relocates_poles_off_collector_before_submission(
    monkeypatch,
) -> None:
    calls = []
    mine_plan, ore_output = direct_mine_plan(
        (10.0, 20.0), 3,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
    )
    extraction = LocalExtractionPlan(
        ore="iron-ore", mine_origin=(10.0, 20.0), drill_count=6,
        furnace_count=6, mining_productivity_bonus=0.0,
        smelter_origin=(85.0, 82.0), ore_output=ore_output,
        build_plan=mine_plan,
    )
    client = type("Client", (), {"command": lambda *_a: ""})()
    monkeypatch.setattr(
        autonomous_builder, "relocate_blocking_poles",
        lambda *_a: calls.append("relocate") or 1,
    )
    monkeypatch.setattr(
        autonomous_builder, "_submit",
        lambda *_a, **_k: calls.append("submit"),
    )
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(autonomous_builder, "_diagnose_machines", lambda *_a, **_k: [])

    autonomous_builder._place_new_mine(
        client, object(), "nauvis", "player", extraction, lambda _message: None,
    )

    assert calls[0] == "relocate"
    assert calls[-1] == "submit"


def test_over_limit_route_rejects_before_smelter_submission(monkeypatch) -> None:
    submitted = []
    monkeypatch.setattr(
        autonomous_builder, "preflight_ingredient_transport",
        lambda *_a, **_k: (_ for _ in ()).throw(
            ValueError("CityPlanner rail handoff is required")
        ),
    )
    monkeypatch.setattr(
        autonomous_builder, "_submit", lambda *_args: submitted.append(_args[3]),
    )
    # The stage reads the base's stock to pick an inserter tier it can actually
    # build; this test drives it with a bare object for a client.
    monkeypatch.setattr(live_base, "available_items", lambda *_a, **_k: {})

    with pytest.raises(ValueError, match="CityPlanner rail handoff"):
        autonomous_builder.build_conversion_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            {"iron-ore": (18.5, 20.5)}, (0.0, 0.0), lambda _message: None,
            placement_origin=(85.0, 82.0), machine_count=6,
            max_belt_route_tiles=300,
        )
    assert submitted == []


def test_find_line_includes_configured_machine_ghosts() -> None:
    client = _StateRcon(["2 0 86.5:85.5,89.5:85.5"])

    line = live_base.find_line(
        client, "nauvis", "player", "iron-plate", "electric-furnace"
    )

    assert line is not None and line.machine_count == 2 and line.working_count == 0
    assert "ghost_name=='electric-furnace'" in client.commands[0]


def test_find_line_parses_a_proven_machine_with_no_reported_position() -> None:
    client = _StateRcon(["1 0 0"])

    line = live_base.find_line(
        client, "nauvis", "player", "iron-plate", "electric-furnace"
    )

    assert line is not None
    assert line.machine_count == 1
    assert line.working_count == 0
    assert line.produced_count == 0
    assert line.machine_positions == ()


def test_intake_candidates_treat_ghost_corridor_belts_as_row_ends() -> None:
    """Live run of 2026-08-24 06:30: prebuilt ghost belts extended the mine
    row past its built head, so every side of the built end was occupied and
    the temporary smelter's intake had nowhere to go. The end scan must see
    ghost belts so the intake anchors past the corridor."""
    client = _StateRcon([""])

    live_base.intake_candidate_tiles(client, "nauvis", (89.5, -39.5))

    lua = client.commands[0]
    assert "type='entity-ghost'" in lua
    assert "string.sub(gn,-14)=='transport-belt'" in lua
    # Factorio 2.x directions are 16-valued: east=4, west=12 (matching the
    # drill drop-offset tables). Eastbound rows jam at their east end.
    assert "if minx_dir==4 then emit_end(maxx) " in lua
    assert "elseif minx_dir==12 then emit_end(minx) " in lua


def test_pending_smelter_is_repaired_instead_of_duplicate_mining(monkeypatch) -> None:
    existing = live_base.LineState(
        recipe="iron-plate", machine_count=2, working_count=0,
        output_position=(89.5, 85.5),
        machine_positions=((86.5, 85.5), (89.5, 85.5)),
    )
    monkeypatch.setattr(live_base, "find_line", lambda *_a: existing)
    monkeypatch.setattr(live_base, "nearest_container", lambda *_a, **_k: None)
    monkeypatch.setattr(live_base, "nearest_pole_on_other_network", lambda *_a: None)
    monkeypatch.setattr(live_base, "entity_statuses", lambda *_a: {})
    # Promotion now weighs the OUTSTANDING requirement against what the built
    # cells can clear, so the survey reads stock.
    monkeypatch.setattr(live_base, "available_items", lambda *_a, **_k: {})
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(
        autonomous_builder, "build_mining_stage",
        lambda *_a, **_k: pytest.fail("pending smelter must not duplicate mining"),
    )

    assert autonomous_builder.ensure_produced(
        object(), object(), "nauvis", "player", "iron-plate",
        (0.0, 0.0), lambda _message: None,
    ) is None

def test_real_builder_does_not_resubmit_a_reconciled_mine(monkeypatch) -> None:
    serviced = []
    monkeypatch.setattr(live_base, "available_items", lambda *_args: {})
    planned = LocalExtractionPlan(
        ore="iron-ore", mine_origin=None, drill_count=2, furnace_count=2,
        mining_productivity_bonus=0.0, smelter_origin=(85.0, 82.0),
        ore_output=(18.5, 20.5), build_plan=None,
    )
    monkeypatch.setattr(
        autonomous_builder, "plan_local_extraction", lambda *_a, **_k: planned,
    )
    monkeypatch.setattr(
        autonomous_builder, "_submit",
        lambda *_a, **_k: pytest.fail("reconciled mine must not be resubmitted"),
    )
    monkeypatch.setattr(
        autonomous_builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (1.0, 2.0),
    )
    monkeypatch.setattr(
        autonomous_builder, "bring_stage_up",
        lambda *_a, **_k: serviced.append((_a[4], _a[7], tuple(_a[8]))),
    )

    assert autonomous_builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate",
        (0.0, 0.0), lambda _message: None,
    ) == (1.0, 2.0)
    assert serviced == [(
        "existing mine for iron-ore", (5.5, 16.5),
        ((14.5, 18.5), (11.5, 18.5), (14.5, 22.5), (11.5, 22.5)),
    )]


def test_direct_belt_endpoint_is_not_checked_as_a_logistic_chest(monkeypatch) -> None:
    """A reconciled mine's belt endpoint must not create a fake coverage fault."""
    class _Client:
        def command(self, _text: str) -> str:
            return ""

    extraction = LocalExtractionPlan(
        ore="stone", mine_origin=None, drill_count=2, furnace_count=2,
        mining_productivity_bonus=0.0, smelter_origin=(3.0, -91.0),
        ore_output=(49.5, -65.5), build_plan=None,
        row_drill_count=2, expansion_step=-1, shared_belt_y=-65.5,
    )
    captured: dict = {}
    monkeypatch.setattr(
        live_base, "entity_at",
        lambda *_args, **_kwargs: {"name": "transport-belt", "type": "transport-belt"},
    )
    monkeypatch.setattr(
        autonomous_builder, "bring_stage_up",
        lambda *_args, **kwargs: captured.update(kwargs),
    )

    autonomous_builder._service_legacy_mine(
        _Client(), object(), "nauvis", "player", extraction, extraction.ore_output,
        lambda _message: None,
    )

    assert captured["logistic_chest_positions"] == []


def test_choose_mining_origin_probing_is_bounded(monkeypatch) -> None:
    """Live run 28 (2026-08-22): a patch saturated by our own drill rows made
    the origin scan probe 2226 staging boxes over ~4 silent RCON minutes
    before dying. Exhaustion must surface after a bounded budget."""
    probes = 0

    def never_clear(*_args):
        nonlocal probes
        probes += 1
        return False

    picked = stage_extraction.choose_mining_origin(
        (12.5, -1.5), (17.5, -26.5), (47.5, 23.5), 4,
        never_clear, lambda _centres: True,
    )

    assert picked is None
    assert probes <= 301


def test_choose_mining_origin_honors_bulk_resource_prefilter() -> None:
    clear_calls: list[tuple] = []

    def clear(minimum, maximum):
        clear_calls.append((minimum, maximum))
        return True

    picked = stage_extraction.choose_mining_origin(
        (0.0, 0.0), (-1.0, -1.0), (6.0, 2.0), 2,
        clear, lambda _centres: True,
        allowed_origins={(0.0, 0.0)},
    )

    assert picked == ((0.0, 0.0), 2)
    assert len(clear_calls) == 1


def test_coal_candidates_are_prefiltered_by_one_bulk_survey(monkeypatch) -> None:
    from orchestrator import stage_chemical

    coal_tiles = {(1, -2), (1, 3)}
    monkeypatch.setattr(
        resource_patches, "resource_tiles",
        lambda *_args, **_kwargs: coal_tiles,
    )

    origins = stage_chemical._coal_compatible_mining_origins(
        object(), "nauvis", (-5.0, -5.0), (5.0, 5.0), (0.0, 0.0),
    )

    assert (0.0, 0.0) in origins
    assert (4.0, 0.0) not in origins


def test_coal_exhaustion_reports_patch_and_candidate_evidence(monkeypatch) -> None:
    from orchestrator import stage_chemical

    monkeypatch.setattr(stage_chemical, "retire_depleted_mines", lambda *_a: 0)
    monkeypatch.setattr(
        stage_chemical.extraction_state, "find_resource_mine", lambda *_a: None,
    )
    monkeypatch.setattr(
        stage_chemical.resource_patches, "nearest_viable_patch",
        lambda *_a, **_k: resource_patches.ResourcePatch(
            (10, 10), (0, 0), (20, 20), 123456,
        ),
    )
    monkeypatch.setattr(
        stage_chemical, "_coal_compatible_mining_origins", lambda *_a: [],
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("empty prefilter must not invoke live siting")

    with pytest.raises(stage_chemical.StuckError, match="ore_compatible_candidates=0"):
        stage_chemical.ensure_coal_mine(
            object(), object(), "nauvis", "player", (0, 0),
            forbidden, lambda _message: None,
        )


def test_saturated_patch_defers_instead_of_killing_the_run(monkeypatch) -> None:
    """A patch whose strips are all owned by standing infrastructure is
    geography, not a bug: the fast-belt capacity gate must pause on
    PendingSystemDeferred instead of crashing the whole mission."""
    monkeypatch.setattr(live_base, "find_clear_area", lambda *_a, **_k: None)

    with pytest.raises(stage_extraction.PendingSystemDeferred):
        stage_extraction._new_direct_mine(
            object(), "nauvis", "iron-ore", (17.5, 3.5),
            (17.5, -26.5), (47.5, 23.5), 3,
            "fast-transport-belt", "fast-inserter", 40,
        )
