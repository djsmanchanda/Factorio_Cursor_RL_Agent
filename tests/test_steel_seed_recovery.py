# Path: tests/test_steel_seed_recovery.py | Purpose: Protect exhausted-source detection, additive routing, identity-bound restart and honest delivery evidence.
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as ab, live_base, operation_facts
from orchestrator import steel_seed_recovery as recovery
from planners.plan_validation import entity_footprint_tiles, validate_build_plan, validate_no_collisions
from planners.steel_bootstrap import (
    VECTORS, mined_seed_interfaces, steel_seed, steel_seed_intakes,
    steel_seed_source_taps, steel_seed_through_handoff,
)

pytestmark = [pytest.mark.deterministic, pytest.mark.fast]


def seed():
    return {**steel_seed((18.5, 4.5), "east", mined=True), "surface": "nauvis", "force": "player"}


def health(plan):
    rows = []
    for i, action in enumerate(plan["phases"][0]["actions"]):
        rows.append({"expected": {"name": action["entity"], "position": action["position"]},
            "name": action["entity"], "position": action["position"], "found": True, "unit": 100 + i,
            "reads": {"recipe": "absent", "lifetime_crafts": "value", "contents": "value",
                "mining_area": "value", "resources_in_mining_area": "value", "mining_target": "absent"},
            "lifetime_crafts": 740, "contents": [{"name": "steel-plate", "count": 419}],
            "status": "no_minable_resources", "resources_in_mining_area": {},
            "mining_area": {"left_top": {"x": 16, "y": 2}, "right_bottom": {"x": 21, "y": 7}}})
    return {"tick": 659619, "surface": "nauvis", "force": "player", "entities": rows}


@pytest.fixture
def world(monkeypatch):
    plan = seed()
    observed = health(plan)
    source = {"action_type": "place_ghost", "entity": "transport-belt",
              "position": {"x": 5.5, "y": 10.5}, "direction": "east"}
    source_row = {"expected": {"name": source["entity"], "position": source["position"]},
        "found": True, "unit": 999, "type": "transport-belt", "direction": 4,
        "reads": {"belt_contents": "value"}, "belt_contents": [{"name": "iron-plate", "count": 4}]}

    def observe(_c, _s, _f, p):
        return copy.deepcopy(observed if len(p["phases"][0]["actions"]) == 8 else {
            "tick": observed["tick"], "entities": [source_row]})

    monkeypatch.setattr(live_base, "plan_entity_health", observe)
    monkeypatch.setattr(live_base, "entity_at", lambda _c, _s, p: {
        "name": "transport-belt", "type": "transport-belt", "force": "player"} if p == (5.5, 10.5) else None)
    monkeypatch.setattr(live_base, "transport_belt_direction_at", lambda *_: "east")
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"transport-belt": 1000, "underground-belt": 100})
    monkeypatch.setattr(live_base, "transferable_items", lambda *args: live_base.available_items(*args))
    blocked = {tile for a in plan["phases"][0]["actions"] for tile in entity_footprint_tiles(a)} | {(5, 10)}
    monkeypatch.setattr(live_base, "occupied_tiles", lambda *_: set(blocked))
    monkeypatch.setattr(recovery, "_through_belt_source", lambda *_: (5.5, 10.5))
    control = SimpleNamespace(built=True)
    def construction(_c, _s, _f, p):
        required = {}
        for a in p["phases"][0]["actions"]:
            required[a["entity"]] = required.get(a["entity"], 0) + 1
        return {"required": required, "installed": required if control.built else {},
            "remaining": {} if control.built else required}
    monkeypatch.setattr(live_base, "plan_construction_status", construction)
    monkeypatch.setattr(ab, "extend_power", lambda *_a, **_k: True)
    return SimpleNamespace(plan=plan, observed=observed, source=source, source_row=source_row, blocked=blocked, control=control)


@pytest.mark.parametrize("direction", VECTORS)
@pytest.mark.parametrize("side", [-1, 1])
def test_all_seed_intakes_drop_inside_furnace_and_are_powered(direction, side):
    plan = steel_seed((0.5, 0.5), direction, mined=True, pole_side=side)
    interfaces = mined_seed_interfaces(plan)
    assert steel_seed_intakes(plan)
    furnace_tiles = entity_footprint_tiles(interfaces["furnace"])
    for endpoint, heading, action in steel_seed_intakes(plan):
        x, y = action["position"].values()
        dx, dy = VECTORS[action["direction"]]
        assert endpoint == (x + dx, y + dy)
        assert (int((x - dx) // 1), int((y - dy) // 1)) in furnace_tiles
        assert any(abs(x - pole["position"]["x"]) <= 3 and abs(y - pole["position"]["y"]) <= 3
                   for pole in interfaces["poles"])
        assert VECTORS[heading] == (-dx, -dy)


def test_empty_native_census_builds_real_shared_route_without_new_production_or_removals(world):
    before = copy.deepcopy(world.plan["phases"])
    facts = []
    with operation_facts.capture(facts.append):
        record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    repair = record["plan"]
    validate_build_plan(repair)
    validate_no_collisions([("seed", world.plan), ("route", repair)])
    actions = repair["phases"][0]["actions"]
    assert all(a["action_type"] == "place_ghost" for a in actions)
    assert {a["entity"] for a in actions} <= {"transport-belt", "underground-belt", "inserter"}
    assert sum(a["entity"] == "inserter" for a in actions) == 2
    assert all(a["position"] != world.source["position"] for a in actions)
    assert record["source_unit"] == 999 and not record["delivery_observed"]
    assert world.plan["phases"] == before
    assert any(f["kind"] == "steel_seed_source_viability" and f["state"] == "exhausted" for f in facts)


@pytest.mark.parametrize("change", ["working", "resources", "target", "read_error", "wrong_recipe", "wrong_scope"])
def test_only_proven_exhaustion_can_plan_a_refeed(world, change):
    drill = world.observed["entities"][0]
    if change == "working": drill["status"] = "working"
    if change == "resources": drill["resources_in_mining_area"] = [{"name": "iron-ore", "amount": 10}]
    if change == "target": drill["reads"]["mining_target"] = "value"
    if change == "read_error": drill["reads"]["resources_in_mining_area"] = "error"
    if change == "wrong_recipe":
        furnace = recovery._row(world.observed, mined_seed_interfaces(world.plan)["furnace"])
        furnace.update(recipe="copper-plate")
        furnace["reads"]["recipe"] = "value"
    if change == "wrong_scope": world.plan["force"] = "enemy"
    if change in {"read_error", "wrong_recipe", "wrong_scope"}:
        with pytest.raises(recovery.SteelSeedRecoveryWait):
            recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    else:
        assert recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5)) is None
    assert "source_recovery" not in world.plan


@pytest.mark.parametrize("defect", ["mixed", "empty", "missing", "heading", "error", "blocked"])
def test_source_and_intake_fail_closed(world, defect):
    if defect == "mixed": world.source_row["belt_contents"].append({"name": "copper-plate", "count": 1})
    if defect == "empty": world.source_row["belt_contents"] = {}
    if defect == "missing": world.source_row["found"] = False
    if defect == "heading": world.source_row["direction"] = 12
    if defect == "error": world.source_row["reads"]["belt_contents"] = "error"
    if defect == "blocked":
        for point, _, action in steel_seed_intakes(world.plan):
            world.blocked.update(entity_footprint_tiles(action))
    with pytest.raises(recovery.SteelSeedRecoveryWait):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    assert "source_recovery" not in world.plan


def test_restart_reuses_same_plan_and_rejects_replaced_entities(world, tmp_path, monkeypatch):
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    path = tmp_path / "episode.steel-seed.json"
    recovery.save_seed(path, world.plan)
    restored = json.loads(path.read_text())
    monkeypatch.setattr(recovery, "_plan_belt_transport", lambda *_a, **_k: pytest.fail("retry must not replan"))
    world.source_row["belt_contents"] = {}  # A temporarily empty terminal is not proof of depletion.
    same = recovery.prepare_seed_refeed(object(), "nauvis", "player", restored, (5.5, 10.5))
    assert same["plan"] == record["plan"] and not same["delivery_observed"]
    world.observed["entities"][0]["unit"] += 1000
    with pytest.raises(recovery.SteelSeedRecoveryWait, match="ownership"):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", restored, (5.5, 10.5))


def test_crafts_and_new_exact_provider_stock_are_separate_from_submission(world):
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    recovery.note_seed_refeed_submission(object(), "nauvis", "player", world.plan)
    recovery.observe_seed_refeed(object(), "nauvis", "player", world.plan)
    interfaces = mined_seed_interfaces(world.plan)
    furnace = recovery._row(world.observed, interfaces["furnace"])
    provider = recovery._row(world.observed, interfaces["provider"])
    furnace["lifetime_crafts"] += 1
    world.observed["tick"] += 120
    recovery.update_seed_refeed(world.plan, world.observed)
    assert not record["delivery_observed"]  # Old stock is insufficient.
    provider["contents"][0]["count"] += 1
    recovery.update_seed_refeed(world.plan, world.observed)
    assert record["delivery_observed"]
    furnace["lifetime_crafts"] = 0
    with pytest.raises(recovery.SteelSeedRecoveryWait, match="reset"):
        recovery.update_seed_refeed(world.plan, world.observed)


def test_builder_persists_funded_transaction_before_yield_and_resumes_it(world, tmp_path, monkeypatch):
    path = tmp_path / "episode.steel-seed.json"
    submitted = []
    def submit(_c, _b, _s, plan, name, _emit, **kwargs):
        assert json.loads(path.read_text())["source_recovery"]["plan"] == plan
        assert name == "steel_seed_refeed" and kwargs["require_funded"]
        submitted.append(copy.deepcopy(plan))
        if len(submitted) == 1:
            raise ab.MaterialShortage(name, {"transport-belt": 100}, {})
    monkeypatch.setattr(ab, "_submit", submit)
    state = SimpleNamespace(reservations={})
    with pytest.raises(ab.MaterialShortage):
        ab._recover_steel_seed_source(object(), object(), "nauvis", "player", path,
            world.plan, (5.5, 10.5), state, lambda _: None)
    restored = json.loads(path.read_text())
    ab._recover_steel_seed_source(object(), object(), "nauvis", "player", path,
        restored, (5.5, 10.5), state, lambda _: None)
    assert submitted[0] == submitted[1]


def test_belt_seed_and_unfinished_seed_keep_existing_construction_path(world):
    assert recovery.prepare_seed_refeed(object(), "nauvis", "player", steel_seed((0.5, 0.5), "east"), (0, 0)) is None
    world.observed["entities"][0]["found"] = False
    assert recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5)) is None


def test_saved_exhausted_seed_reaches_refeed_then_original_furnace(world, tmp_path, monkeypatch):
    path = tmp_path / "materials.steel-seed.json"
    path.write_text(json.dumps(world.plan))
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=tmp_path / "materials.json"))
    monkeypatch.setattr(ab, "_bootstrap_state", lambda *_: SimpleNamespace(lifecycle_state="released", reservations={}))
    monkeypatch.setattr(ab, "_adopt_legacy_steel_seed_reservation", lambda *_: None)
    monkeypatch.setattr(ab, "_transferable_or_available_stock", lambda *_: {})
    monkeypatch.setattr(ab, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(ab, "_ensure_power_anchor_on_generated_network", lambda *_: None)
    submissions = []
    monkeypatch.setattr(ab, "_submit", lambda _c, _b, _s, p, name, *_a, **_k: submissions.append((name, copy.deepcopy(p))))
    def craft(*_args, **_kwargs):
        world.observed["tick"] += 120
        interfaces = mined_seed_interfaces(world.plan)
        recovery._row(world.observed, interfaces["furnace"])["lifetime_crafts"] += 1
        recovery._row(world.observed, interfaces["provider"])["contents"][0]["count"] += 1
        return []
    monkeypatch.setattr(ab, "_diagnose_machines", craft)
    assert ab._build_compact_steel_seed(object(), object(), "nauvis", "player", (5.5, 10.5), (0, 0), lambda _: None) == (28.5, 4.5)
    assert [name for name, _ in submissions] == ["steel_seed_refeed", "conversion_steel-plate"]
    saved = json.loads(path.read_text())
    assert saved["phases"] == world.plan["phases"]
    assert saved["source_recovery"]["submission"] is not None
    assert saved["source_recovery"]["built_baseline"] is not None
    assert not saved["source_recovery"]["delivery_observed"]  # First built observation is a baseline.
    assert ab._build_compact_steel_seed(object(), object(), "nauvis", "player", (5.5, 10.5), (0, 0), lambda _: None) == (28.5, 4.5)
    assert json.loads(path.read_text())["source_recovery"]["delivery_observed"]


@pytest.mark.parametrize("field,value", [("version", True), ("delivery_observed", "yes"),
                                       ("baseline", {}), ("source_unit", None)])
def test_malformed_recovery_metadata_cannot_resume(world, field, value):
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    record[field] = value
    with pytest.raises(recovery.SteelSeedRecoveryWait):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))


def test_recovery_ownership_travels_in_existing_version4_seed_sidecar(world, tmp_path):
    from orchestrator.checkpoint_milestones import CheckpointEvaluator, FactorySnapshot
    from orchestrator.checkpoint_restart_state import export_restart_state, import_restart_state, sidecar_registry
    source = tmp_path / "source" / "server" / "script-output"
    source.mkdir(parents=True)
    recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    seed_path = sidecar_registry(source, "episode")["steel_seed"]
    seed_path.parent.mkdir(parents=True)
    recovery.save_seed(seed_path, world.plan)
    baseline = FactorySnapshot(tick=659619)
    bundle = export_restart_state(source, "episode", "matching-mods", baseline, [baseline], CheckpointEvaluator())
    target = tmp_path / "target" / "server" / "script-output"
    import_restart_state(json.loads(json.dumps(bundle)), target, episode_id="episode", mod_scope="matching-mods")
    restored = json.loads(sidecar_registry(target, "episode")["steel_seed"].read_text())
    assert restored == world.plan
    assert recovery.prepare_seed_refeed(object(), "nauvis", "player", restored, (5.5, 10.5))["plan"] == world.plan["source_recovery"]["plan"]


@pytest.mark.parametrize("direction", VECTORS)
@pytest.mark.parametrize("side", [-1, 1])
def test_every_selected_intake_has_its_funded_pickup_belt(world, direction, side):
    p = {**steel_seed((18.5, 4.5), direction, mined=True, pole_side=side), "surface": "nauvis", "force": "player"}
    world.plan.clear(); world.plan.update(p)
    world.observed.clear(); world.observed.update(health(p))
    world.blocked.clear()
    world.blocked.update({t for a in p["phases"][0]["actions"] for t in entity_footprint_tiles(a)} | {(5, 10)})
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    actions = record["plan"]["phases"][0]["actions"]
    intake = actions[-1]
    dx, dy = VECTORS[intake["direction"]]
    pickup = {"x": intake["position"]["x"] + dx, "y": intake["position"]["y"] + dy}
    assert any(a["entity"] == "transport-belt" and a["position"] == pickup for a in actions)


def test_one_blocked_approach_does_not_skip_other_legal_intakes(world):
    world.blocked.add((30, 3))
    assert recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))


def test_failed_funding_cannot_credit_buffered_old_production(world, tmp_path, monkeypatch):
    path = tmp_path / "seed.json"
    flags = []
    def unfunded(*_args, **_kwargs):
        flags.append(json.loads(path.read_text())["source_recovery"]["delivery_observed"])
        raise ab.MaterialShortage("steel_seed_refeed", {"transport-belt": 100}, {})
    monkeypatch.setattr(ab, "_submit", unfunded)
    for attempt in range(2):
        if attempt:
            world.plan = json.loads(path.read_text())
            world.observed["tick"] += 120
            interfaces = mined_seed_interfaces(world.plan)
            recovery._row(world.observed, interfaces["furnace"])["lifetime_crafts"] += 1
            recovery._row(world.observed, interfaces["provider"])["contents"][0]["count"] += 1
        with pytest.raises(ab.MaterialShortage):
            ab._recover_steel_seed_source(object(), object(), "nauvis", "player", path,
                world.plan, (5.5, 10.5), SimpleNamespace(reservations={}), lambda _: None)
    assert flags == [False, False]
    assert not json.loads(path.read_text())["source_recovery"]["delivery_observed"]


@pytest.mark.parametrize("transport_mode", ["belt", "logistic"])
def test_completed_refeed_preserves_terminal_for_unchanged_promotion(world, tmp_path, monkeypatch, transport_mode):
    from orchestrator import stage_transport
    from planners import smelter_block
    if transport_mode == "logistic":
        for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east"):
            world.blocked.update(tile for a in actions for tile in entity_footprint_tiles(a))
        monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1})
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    actions = record["plan"]["phases"][0]["actions"]
    entities = {(5.5, 10.5): {"entity": "transport-belt", "direction": "east"}}
    for a in [*world.plan["phases"][0]["actions"], *actions]:
        entities[(a["position"]["x"], a["position"]["y"])] = a
    monkeypatch.setattr(live_base, "entity_at", lambda _c, _s, p: {
        "name": entities[p]["entity"], "type": "transport-belt" if entities[p]["entity"] == "transport-belt" else "inserter",
        "force": "player"} if p in entities else None)
    monkeypatch.setattr(live_base, "transport_belt_direction_at", lambda _c, _s, p: entities[p]["direction"])
    monkeypatch.setattr(stage_transport, "_through_belt_source", lambda *_: (5.5, 10.5))
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=tmp_path / "materials.json"))
    monkeypatch.setattr(ab, "_bootstrap_state", lambda *_: SimpleNamespace(replacement_provider=(5.5, 10.5)))
    (tmp_path / "materials.steel-seed.json").write_text(json.dumps(world.plan))
    class ReachedPromotionPlanner(Exception): pass
    def reached(*_args, **_kwargs): raise ReachedPromotionPlanner()
    monkeypatch.setattr(smelter_block, "generate_managed_refinery_plan", reached)
    with pytest.raises(ReachedPromotionPlanner):
        ab._promote_compact_steel(object(), object(), "nauvis", "player", (0, 0), lambda _: None)


def test_pending_ghosts_and_buffered_output_cannot_establish_recovery(world):
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))
    recovery.note_seed_refeed_submission(object(), "nauvis", "player", world.plan)
    world.control.built = False
    interfaces = mined_seed_interfaces(world.plan)
    def output():
        world.observed["tick"] += 120
        recovery._row(world.observed, interfaces["furnace"])["lifetime_crafts"] += 1
        recovery._row(world.observed, interfaces["provider"])["contents"][0]["count"] += 1
    output()
    recovery.observe_seed_refeed(object(), "nauvis", "player", world.plan)
    assert record["built_baseline"] is None and not record["delivery_observed"]
    world.control.built = True
    recovery.observe_seed_refeed(object(), "nauvis", "player", world.plan)
    assert record["built_baseline"] is not None and not record["delivery_observed"]
    output()
    recovery.observe_seed_refeed(object(), "nauvis", "player", world.plan)
    assert record["delivery_observed"]


def test_other_lifecycle_failures_are_not_swallowed_by_candidate_search(world, monkeypatch):
    def failure(*_a, **_k):
        raise ab.ProductionPrerequisiteDeferred("unrelated", code="other_wait")
    monkeypatch.setattr(recovery, "_plan_belt_transport", failure)
    with pytest.raises(ab.ProductionPrerequisiteDeferred, match="unrelated"):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))


def test_source_pickup_uses_funded_shared_power_connection(world, tmp_path, monkeypatch):
    path = tmp_path / "seed.json"
    monkeypatch.setattr(ab, "_submit", lambda *_a, **_k: None)
    power = []
    def unpowered(*args, **kwargs):
        power.append((args[4], kwargs["reserved_tiles"]))
        return False
    monkeypatch.setattr(ab, "extend_power", unpowered)
    with pytest.raises(ab.ProductionPrerequisiteDeferred) as result:
        ab._recover_steel_seed_source(object(), object(), "nauvis", "player", path, world.plan,
            (5.5, 10.5), SimpleNamespace(reservations={}), lambda _: None)
    assert result.value.code == "steel_seed_source_power_wait"
    record = json.loads(path.read_text())["source_recovery"]
    assert record["submission"] is not None and not record["delivery_observed"]
    assert power and (6, 10) in power[0][1]  # Keep the through terminal clear even during power work.


@pytest.mark.parametrize("obstacle", ["reserved", "occupied", "unroutable"])
def test_reserved_or_blocked_source_can_use_funded_single_furnace_logistics(world, monkeypatch, obstacle):
    tap_tiles = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                 for action in actions for tile in entity_footprint_tiles(action)}
    reserved = tap_tiles if obstacle == "reserved" else set()
    if obstacle == "occupied":
        world.blocked.update(tap_tiles)
    if obstacle == "unroutable":
        def no_route(*_a, **_k):
            raise ab.StuckError("no clear source corridor")
        monkeypatch.setattr(recovery, "_plan_belt_transport", no_route)
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1, "inserter": 1})
    facts = []
    with operation_facts.capture(facts.append):
        record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan,
                                              (5.5, 10.5), reserved_tiles=reserved)
    assert record["transport_mode"] == "logistic"
    actions = record["plan"]["phases"][0]["actions"]
    assert [a["entity"] for a in actions] == ["inserter", "requester-chest"]
    assert actions[1]["logistic_request"] == {"name": "iron-plate", "count": 100}
    footprint = {tile for a in actions for tile in entity_footprint_tiles(a)}
    assert not footprint & (reserved | world.blocked | steel_seed_through_handoff((5.5, 10.5), "east"))
    validate_no_collisions([("seed", world.plan), ("feed", record["plan"])])
    assert any(f["kind"] == "steel_seed_refeed_options" and not f["belt_plans"] for f in facts)
    # The funded two-entity selection, not a fresh choice, survives a restart.
    restored = json.loads(json.dumps(world.plan))
    monkeypatch.setattr(live_base, "available_items", lambda *_: {})
    assert recovery.prepare_seed_refeed(object(), "nauvis", "player", restored,
                                        (5.5, 10.5))["plan"] == record["plan"]


@pytest.mark.parametrize("unavailable", ["chest", "capacity", "intake"])
def test_logistic_alternative_preserves_stock_rate_and_intake_legality(world, monkeypatch, unavailable):
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    monkeypatch.setattr(live_base, "available_items", lambda *_: {} if unavailable == "chest" else {"requester-chest": 1})
    if unavailable == "capacity":
        monkeypatch.setattr(recovery, "_transport_mode", lambda *_: "belt")
    if unavailable == "intake":
        for _, _, inserter in steel_seed_intakes(world.plan):
            reserved.update(entity_footprint_tiles(inserter))
    with pytest.raises(recovery.SteelSeedRecoveryWait, match="source pickup"):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan,
                                      (5.5, 10.5), reserved_tiles=reserved)
    assert "source_recovery" not in world.plan


def test_logistic_refeed_uses_shared_coverage_and_same_funding_witness(world, tmp_path, monkeypatch):
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1})
    path = tmp_path / "seed.json"
    submitted, coverage = [], []
    def submit(_c, _b, _s, plan, _name, _emit, **kwargs):
        assert kwargs["require_funded"]
        assert json.loads(path.read_text())["source_recovery"]["plan"] == plan
        submitted.append(plan)
    monkeypatch.setattr(ab, "_submit", submit)
    monkeypatch.setattr(ab, "ensure_logistic_coverage", lambda *_a, **_k: coverage.append((_a[4], _k)))
    ab._recover_steel_seed_source(object(), object(), "nauvis", "player", path, world.plan,
        (5.5, 10.5), SimpleNamespace(reservations={"refinery_growth": reserved}), lambda _: None)
    record = json.loads(path.read_text())["source_recovery"]
    requester = submitted[0]["phases"][0]["actions"][1]["position"]
    assert coverage and (requester["x"], requester["y"]) in coverage[0][0]
    assert reserved <= coverage[0][1]["reserved_tiles"]
    assert record["submission"] is not None and record["built_baseline"] is None
    assert not record["delivery_observed"]


def test_persisted_logistic_feed_cannot_change_ingredient_or_furnace_intake(world, monkeypatch):
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1})
    record = recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan,
                                          (5.5, 10.5), reserved_tiles=reserved)
    record["plan"]["phases"][0]["actions"][1]["logistic_request"]["name"] = "copper-plate"
    record["plan_sha256"] = recovery._hash(record["plan"])
    with pytest.raises(recovery.SteelSeedRecoveryWait, match="furnace intake"):
        recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5))


@pytest.mark.parametrize("commitment", ["requester_wip", "foreign_reservation", "foreign_ghost"])
def test_logistic_feed_requires_allocatable_chest_not_committed_stock(world, tmp_path, monkeypatch, commitment):
    from orchestrator import construction_coverage
    from orchestrator.material_reservations import MaterialReservationLedger
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    stock = {"requester-chest": 1, "inserter": 1}
    monkeypatch.setattr(live_base, "available_items", lambda *_: stock)
    monkeypatch.setattr(live_base, "transferable_items", lambda *_: {} if commitment == "requester_wip" else stock)
    ledger = MaterialReservationLedger(tmp_path, episode_id="stock-test", surface="nauvis", force="player")
    monkeypatch.setattr(construction_coverage, "active_material_ledger", lambda: ledger)
    client = object()
    if commitment == "foreign_reservation":
        ledger.declare("foreign", {"requester-chest": 1}, stock, priority=100)
    if commitment == "foreign_ghost":
        client = SimpleNamespace(command=lambda *_: "")
        world.control.built = False
        monkeypatch.setattr(live_base, "pending_construction_items", lambda *_a, **_k: {"requester-chest": 1})
    with pytest.raises(recovery.SteelSeedRecoveryWait):
        recovery.prepare_seed_refeed(client, "nauvis", "player", world.plan, (5.5, 10.5), reserved_tiles=reserved)
    assert "source_recovery" not in world.plan


def test_persisted_stock_only_feed_never_requests_chest_manufacture(world, tmp_path, monkeypatch):
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1})
    recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5), reserved_tiles=reserved)
    def lost_chest(*_a, **_k):
        raise ab.MaterialShortage("steel_seed_refeed", {"requester-chest": 1}, {})
    monkeypatch.setattr(ab, "_submit", lost_chest)
    with pytest.raises(ab.ProductionPrerequisiteDeferred) as blocked:
        ab._recover_steel_seed_source(object(), object(), "nauvis", "player", tmp_path / "seed.json",
            json.loads(json.dumps(world.plan)), (5.5, 10.5), SimpleNamespace(reservations={}), lambda _: None)
    assert blocked.value.code == "steel_seed_source_stock_wait"


@pytest.mark.parametrize("delivery_proven", [False, True])
def test_replacement_delivery_retires_owned_logistic_feed_with_seed(world, tmp_path, monkeypatch, delivery_proven):
    reserved = {tile for _, _, actions in steel_seed_source_taps((5.5, 10.5), "east")
                for action in actions for tile in entity_footprint_tiles(action)}
    monkeypatch.setattr(live_base, "available_items", lambda *_: {"requester-chest": 1})
    recovery.prepare_seed_refeed(object(), "nauvis", "player", world.plan, (5.5, 10.5), reserved_tiles=reserved)
    (tmp_path / "materials.steel-seed.json").write_text(json.dumps(world.plan))
    replacement = {"surface": "nauvis", "force": "player", "phases": [{"name": "replacement", "actions": [
        {"action_type": "place_ghost", "entity": "electric-furnace", "position": {"x": 100.5, "y": 100.5}}]}]}
    path = tmp_path / "materials.steel-district.json"
    path.write_text(json.dumps({"plan": replacement, "provider": [105.5, 100.5],
                               "power": [103.5, 100.5], "delivery_observed": delivery_proven}))
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", SimpleNamespace(path=tmp_path / "materials.json"))
    monkeypatch.setattr(ab, "_submit", lambda *_a, **_k: None)
    monkeypatch.setattr(live_base, "machine_health", lambda *_: ({}, {}))
    monkeypatch.setattr(ab, "_ensure_power_anchor_on_generated_network", lambda *_: None)
    monkeypatch.setattr(ab, "_retire_unused_starter_power_branch", lambda *_: None)
    monkeypatch.setattr(ab, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(ab, "_diagnose_machines", lambda *_a, **_k: [])
    removals = []
    monkeypatch.setattr(ab, "retire_entities_via_bots", lambda _c, _b, _s, _f, p, *_: removals.extend(p["phases"][0]["actions"]))
    if not delivery_proven:
        with pytest.raises(ab.ProductionPrerequisiteDeferred, match="first delivery"):
            ab._promote_compact_steel(object(), object(), "nauvis", "player", (0, 0), lambda _: None)
        assert not removals and not json.loads(path.read_text()).get("seed_retired")
        return
    assert ab._promote_compact_steel(object(), object(), "nauvis", "player", (0, 0), lambda _: None) == (105.5, 100.5)
    targets = {(a["entity"], tuple(a["position"].values())) for a in removals}
    for action in world.plan["source_recovery"]["plan"]["phases"][0]["actions"]:
        assert (action["entity"], tuple(action["position"].values())) in targets
    assert ("transport-belt", (5.5, 10.5)) not in targets
    assert json.loads(path.read_text())["seed_retired"]
