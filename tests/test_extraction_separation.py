# Path: tests/test_extraction_separation.py
# Purpose: Prove real-base extraction keeps furnace production off ore and out of the drill row.

from __future__ import annotations

import pytest

from orchestrator import autonomous_builder, extraction_state, live_base, resource_patches
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
    smelter_count_for_drills,
    smelter_search_anchors,
)
from planners.local_layout_planner import LocalLayoutPlanner
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

    assert output == (7.5, 18.5)
    assert _entities(plan).count("electric-mining-drill") == 6
    assert "electric-furnace" not in _entities(plan)
    assert "steel-chest" in _entities(plan)
    assert "inserter" in _entities(plan)
    assert "fast-inserter" not in _entities(plan)

def test_furnaces_use_force_productivity_instead_of_copying_drill_count() -> None:
    assert smelter_count_for_drills("iron-plate", 2, 0.0) == 2
    assert smelter_count_for_drills("iron-plate", 5, 0.0) == 4
    assert smelter_count_for_drills("iron-plate", 2, 0.40) == 3


def test_exact_smelter_bounds_include_west_feed_and_substation() -> None:
    bounds, feed = _smelter_geometry(
        "iron-plate", 2, "fast-transport-belt", "fast-inserter"
    )

    assert bounds == Rect(-5.0, -2.0, 8.0, 7.0)
    assert feed == (-1.5, -1.5)


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


def test_pending_smelter_probe_is_ore_and_pending_specific() -> None:
    completed_iron = _StateRcon(["0"])
    pending_copper = _StateRcon(["1"])

    assert not extraction_state.pending_plate_smelter(
        completed_iron, "nauvis", "player", "copper-ore", (0.0, 0.0)
    )
    assert extraction_state.pending_plate_smelter(
        pending_copper, "nauvis", "player", "copper-ore", (0.0, 0.0)
    )
    assert "name=='copper-ore'" in completed_iron.commands[0]
    # Completeness is ghost-ness and nothing else. A furnace has no settable
    # recipe -- it auto-selects one from the first ore inserted -- so probing
    # get_recipe() reported every row that had not been fed yet as pending
    # forever, and plan_local_extraction refused to continue before reaching the
    # remediation that would have unstarved it. Asserting the probe is ABSENT is
    # the regression guard against reintroducing that deadlock.
    assert "type=='entity-ghost'" in completed_iron.commands[0]
    assert "get_recipe" not in completed_iron.commands[0]

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

    assert planned.smelter_origin == (85.0, 82.0)
    assert clear_calls[1][1] == {
        "max_radius": 60.0,
        "avoid_resources": True,
        "resource_clearance": 5.0,
    }
    layout = LocalLayoutPlanner().generate_line_layout(
        "iron-plate", 2, *planned.smelter_origin,
        belt_type="fast-transport-belt", inserter_type="fast-inserter",
        feed_style="chest", terminal_collector=True,
    )
    retained = [
        action for action in actions(layout)
        if action["entity"] != "electric-energy-interface"
    ]
    for action in retained:
        size = ENTITY_FOOTPRINTS.get(action["entity"], 1)
        x, y = action["position"]["x"], action["position"]["y"]
        assert 80.0 <= x - size / 2 and x + size / 2 <= 93.0
        assert 80.0 <= y - size / 2 and y + size / 2 <= 89.0


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


def test_real_builder_submits_ore_only_then_calls_separate_smelter(
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
        lambda _client, _bridge, _surface, plan, _name, _emit: submitted.append(plan),
    )
    monkeypatch.setattr(autonomous_builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(autonomous_builder, "_diagnose_machines", lambda *_a, **_k: [])

    def build_conversion(*args, **kwargs):
        conversion["ingredient_sources"] = args[5]
        conversion["placement_origin"] = kwargs["placement_origin"]
        conversion["machine_count"] = kwargs["machine_count"]
        conversion["max_belt_route_tiles"] = kwargs["max_belt_route_tiles"]
        return (120.5, 90.5)

    monkeypatch.setattr(autonomous_builder, "build_conversion_stage", build_conversion)

    output = autonomous_builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate",
        (0.0, 0.0), lambda _message: None,
    )

    assert output == (120.5, 90.5)
    assert len(submitted) == 1
    assert "electric-furnace" not in _entities(submitted[0])
    assert "passive-provider-chest" in _entities(submitted[0])
    assert conversion == {
        "ingredient_sources": {"iron-ore": (7.5, 18.5)},
        "placement_origin": (85.0, 82.0),
        "machine_count": 2,
        "max_belt_route_tiles": 300,
    }



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
        autonomous_builder, "build_conversion_stage", lambda *_a, **_k: (1.0, 2.0),
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