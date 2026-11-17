# Path: tests/test_checkpoint_milestones.py
# Purpose: Verify structured, versioned, monotonic checkpoint predicates.
from orchestrator.checkpoint_milestones import (
    C6_V2,
    CheckpointEvaluator,
    FactorySnapshot,
    MilestoneDefinition,
    PredicateStatus,
    _c5,
    default_milestones,
    evaluate_milestone,
)


def _snapshot(**overrides):
    value = {
        "tick": 0,
        "base_valid": True,
        "starters": {
            "iron": {"present": True, "healthy": True, "producing": True},
            "copper": {"present": True, "healthy": True, "producing": True},
            "stone": {"present": True, "healthy": True, "producing": True},
        },
        "mall_assemblers": [
            {"recipe": "iron-gear-wheel", "working": True},
            {"recipe": "copper-cable", "working": True},
            {"recipe": "electronic-circuit", "working": True},
            {"recipe": "transport-belt", "working": True},
        ],
    }
    value.update(overrides)
    return value


def test_default_registry_is_ordered_and_versioned_with_advanced_circuit_milestone():
    milestones = default_milestones()
    assert [item.checkpoint_id for item in milestones] == ["C0", "C1", "C2", "C3", "C4", "C5", "C6a", "C6", "C7", "C8", "C9"]
    assert [item.predicate_version for item in milestones] == [f"c{i}-v1" for i in range(5)] + [
        "c5-v4", "c6a-v1", "c6-v3", "c7-v1", "c8-v1", "c9-v1"]
    assert milestones[7].name == "advanced_circuit_consumption"


def test_missing_telemetry_is_unknown_not_failed():
    result = evaluate_milestone("C1", {"tick": 1})
    assert result.status is PredicateStatus.UNKNOWN
    assert "iron_starter_ready" in result.missing


def test_c1_requires_four_working_mall_cells_and_required_recipes():
    result = evaluate_milestone("C1", _snapshot())
    assert result.passed
    broken = _snapshot(mall_assemblers=[{"recipe": "iron-gear-wheel", "working": True}] * 4)
    assert evaluate_milestone("C1", broken).failed


def test_c2_requires_six_machine_foundations_and_absent_starters():
    value = _snapshot(
        foundations={
            "iron": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
            "copper": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
        },
        starters={
            "iron": {"present": False}, "copper": {"present": False},
        },
    )
    assert evaluate_milestone("C2", value).passed
    value["foundations"]["iron"]["machine_count"] = 5
    assert evaluate_milestone("C2", value).failed


def test_foundation_requires_measured_output_not_only_running_machines():
    value = _snapshot(
        foundations={
            "iron": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 0},
            "copper": {"machine_count": 6, "healthy": True, "producing": True, "output_count": 1},
        },
        starters={"iron": {"present": False}, "copper": {"present": False}},
    )
    assert evaluate_milestone("C2", value).failed


def test_c4_requires_new_plastic_production_and_delivery():
    baseline = {"tick": 100, "production": {"plastic-bar": {"produced": 2}}, "providers": {"plastic-bar": {"delivered": 1}}}
    current = {"tick": 200, "production": {"plastic-bar": {"produced": 3}}, "providers": {"plastic-bar": {"delivered": 2}}}
    assert evaluate_milestone("C4", current, baseline=baseline).passed
    assert evaluate_milestone("C4", current).unknown


C5_V1 = (MilestoneDefinition("C5", "stable_plastic", "c5-v1", _c5),)


def test_c5_requires_120_seconds_and_advancing_plastic_evidence():
    def sample(tick, craft, delivered):
        return {"tick": tick, "production": {"plastic-bar": {"craft_count": craft}}, "providers": {"plastic-bar": {"delivered": delivered}}}

    history = [sample(0, 1, 1), sample(3600, 2, 2), sample(7200, 3, 3)]
    assert evaluate_milestone("C5", history[-1], history=history[:-1], milestones=C5_V1).passed
    assert evaluate_milestone("C5", sample(6000, 3, 3), history=history[:-1], milestones=C5_V1).failed


def test_c5_ignores_preproduction_history_from_c0_lane():
    def sample(tick, craft, delivered):
        return {"tick": tick, "production": {"plastic-bar": {"craft_count": craft}}, "providers": {"plastic-bar": {"delivered": delivered}}}

    history = [
        {"tick": 0},
        sample(100, 0, 0),
        sample(200, 1, 1),
        sample(3800, 2, 2),
    ]
    result = evaluate_milestone("C5", sample(7400, 3, 3), history=history, milestones=C5_V1)
    assert result.passed
    assert result.evidence["ignored_preproduction_samples"] == 2


def test_c6_proves_advanced_circuit_production_and_new_owned_consumer_output():
    baseline = {
        "production": {"advanced-circuit": {"produced": 1}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 0}},
    }
    current = {
        "production": {"advanced-circuit": {"produced": 2}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 1}},
    }
    assert evaluate_milestone("C6", current, baseline=baseline, milestones=(C6_V2,)).passed


def test_c6_v2_rejects_a_logistics_chest_as_the_production_consumer():
    baseline = {
        "production": {"advanced-circuit": {"produced": 1}},
        "recipes": {"passive-provider-chest": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 0}},
    }
    current = {
        "production": {"advanced-circuit": {"produced": 2}},
        "recipes": {"passive-provider-chest": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 1}},
    }
    assert not evaluate_milestone("C6", current, baseline=baseline, milestones=(C6_V2,)).passed


def test_c6_v2_accepts_a_production_consumer():
    baseline = {
        "production": {"advanced-circuit": {"produced": 1}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 0}},
    }
    current = {
        "production": {"advanced-circuit": {"produced": 2}},
        "recipes": {"processing-unit": {"owned": True, "ingredients": ["advanced-circuit"], "output_count": 1}},
    }
    assert evaluate_milestone("C6", current, baseline=baseline, milestones=(C6_V2,)).passed


def test_evaluator_keeps_passed_checkpoint_monotonic():
    evaluator = CheckpointEvaluator()
    assert evaluator.evaluate("C0", _snapshot()).passed
    later = evaluator.evaluate("C0", {"tick": 4})
    assert later.passed
    assert later.evidence["monotonic_reached"] is True


def test_c1_evidence_counts_mall_statuses_and_reads_idle_producers_as_failed():
    idle = [{"recipe": "iron-gear-wheel", "working": False, "status": "output_full"},
            {"recipe": "copper-cable", "working": False, "status": "no_ingredients"},
            {"recipe": "electronic-circuit", "working": True, "status": "working"},
            {"recipe": "transport-belt", "working": False}]
    starters = {"iron": {"present": True, "healthy": True, "producing": True},
                "copper": {"present": True, "healthy": True, "producing": False},
                "stone": {"present": False}}
    result = evaluate_milestone("C1", _snapshot(mall_assemblers=idle, starters=starters))
    assert result.failed
    assert result.evidence["mall_status_counts"] == {"no_ingredients": 1, "output_full": 1, "unreported": 1, "working": 1}
    assert result.evidence["recipes_present"] == {"iron-gear-wheel": True, "copper-cable": True}
    assert result.evidence["checks"]["iron_gear_producer_working"] is False
    assert result.evidence["starter_blockers"] == {"iron": "ready", "copper": "not_producing", "stone": "absent"}


def test_c1_idle_survey_now_reads_producers_false_without_changing_status():
    # c1-v1 change: an observed survey with no working machine used to report the producer checks unknown;
    # they are now measured False. The count check already failed it, so the status is unchanged.
    idle = [{"recipe": "iron-gear-wheel", "working": False, "status": "no_power"}] * 4
    result = evaluate_milestone("C1", _snapshot(mall_assemblers=idle))
    assert result.failed and result.evidence["checks"]["copper_cable_producer_working"] is False
    empty = evaluate_milestone("C1", _snapshot(mall_assemblers=[]))
    assert empty.unknown and empty.evidence["checks"]["iron_gear_producer_working"] is None
    assert empty.evidence["mall_status_counts"] == {}


def _science(level=0, recyclers=0, labs=2, **packs):
    counts = {pack: {"produced": 0, "consumed": 0} for pack in (
        "automation-science-pack", "logistic-science-pack", "chemical-science-pack", "production-science-pack")}
    for key, (produced, consumed) in packs.items():
        counts[key.replace("_", "-")] = {"produced": produced, "consumed": consumed}
    return {"tick": 1, "science": {"packs": counts, "labs": labs, "recyclers": recyclers,
                                   "mining_productivity_completed_level": level}}


def test_c7_needs_labs_to_consume_chemical_packs_made_this_run():
    baseline = _science(chemical_science_pack=(10, 0))
    stored = evaluate_milestone("C7", _science(chemical_science_pack=(10, 4)), baseline=baseline)
    assert stored.failed  # inherited stock consumed, none made
    made = evaluate_milestone("C7", _science(chemical_science_pack=(15, 4)), baseline=baseline)
    assert made.passed and made.predicate_version == "c7-v1"
    assert evaluate_milestone("C7", _science(labs=0, chemical_science_pack=(15, 4)), baseline=baseline).failed


def test_c7_c8_c9_are_unknown_without_trustworthy_science_telemetry():
    baseline = _science()
    recycler = _science(recyclers=1, production_science_pack=(5, 5))
    assert evaluate_milestone("C8", recycler, baseline=baseline).unknown
    unavailable = {"tick": 2, "science": {"unavailable_reason": "science_read_failed: ValueError"}}
    for checkpoint in ("C7", "C8", "C9"):
        result = evaluate_milestone(checkpoint, unavailable, baseline=baseline)
        assert result.unknown and result.evidence["reason"] == "science_read_failed: ValueError"
        assert evaluate_milestone(checkpoint, baseline).unknown  # no baseline


def test_c9_needs_level_four_and_every_pack_made_and_consumed_this_run():
    earned = dict(automation_science_pack=(9, 9), logistic_science_pack=(9, 9),
                  chemical_science_pack=(9, 9), production_science_pack=(9, 9))
    baseline = _science(level=2)
    assert evaluate_milestone("C9", _science(level=4, **earned), baseline=baseline).passed
    assert evaluate_milestone("C9", _science(level=3, **earned), baseline=baseline).failed
    no_purple = {**earned, "production_science_pack": (0, 0)}
    assert evaluate_milestone("C9", _science(level=4, **no_purple), baseline=baseline).failed


def _ac(tick, produced, machines):
    return FactorySnapshot.from_mapping({"tick": tick, "production": {"advanced-circuit": {"produced": produced}},
                                         "ac_consumers": [{"unit": u, "recipe": r, "count": c} for u, r, c in machines]})


def test_c6_v3_does_not_count_a_relabelled_machines_lifetime_counter():
    # C89 C0-smallcell-s83343: a mall cell switched to electric-furnace arrives with 2294 lifetime crafts.
    baseline = _ac(1, 0, [])
    relabel = _ac(2, 411, [(7, "electric-furnace", 2294)])
    result = evaluate_milestone("C6", relabel, baseline=baseline, history=[baseline, relabel])
    assert result.predicate_version == "c6-v3" and result.failed
    assert result.evidence["machine_consumption"] == {}
    later = _ac(3, 413, [(7, "electric-furnace", 2295)])
    passed = evaluate_milestone("C6", later, baseline=baseline, history=[baseline, relabel, later])
    assert passed.passed and passed.evidence["machine_consumption"] == {"electric-furnace": 1}


def test_c6_v3_ignores_chests_and_recipe_switches_between_samples():
    baseline = _ac(1, 0, [(3, "requester-chest", 0), (4, "productivity-module", 0)])
    switched = _ac(2, 9, [(3, "requester-chest", 5), (4, "processing-unit", 40)])
    result = evaluate_milestone("C6", switched, baseline=baseline, history=[baseline, switched])
    assert result.failed and result.evidence["machine_consumption"] == {}


def test_c6_v3_consumer_leg_is_unknown_without_machine_telemetry():
    baseline = _ac(1, 0, [])
    unreadable = FactorySnapshot.from_mapping({"tick": 2, "production": {"advanced-circuit": {"produced": 5}}})
    assert evaluate_milestone("C6", unreadable, baseline=baseline, history=[baseline, unreadable]).unknown


def _item_sample(tick, produced, *, scope='["nauvis",1,"player",1]', generation=0, consumers=()):
    return FactorySnapshot.from_mapping({"tick": tick, "production": {"advanced-circuit": {
        "produced": produced, "counter_basis": "force-surface-item-input-v1", "counter_scope": scope,
        "counter_generation": generation, "sample_tick": tick}},
        "ac_consumers": [{"unit": 7, "recipe": "bulk-inserter", "count": count} for count in consumers]})


def test_item_totals_do_not_credit_recipe_retarget_or_erase_output_on_removal():
    # The replay reassigns a 131-lifetime-craft assembler to AC while actual AC remains 297.
    # A genuine +2 consumer event must not make that relabel into AC production.
    baseline = _item_sample(1, 297, consumers=[10])
    retarget = _item_sample(2, 297, consumers=[12])
    result = evaluate_milestone('C6', retarget, baseline=baseline, history=[baseline, retarget])
    assert result.failed and result.evidence['production_delta'] == 0
    assert result.evidence['machine_consumption'] == {'bulk-inserter': 2}
    actual_output = _item_sample(3, 298, consumers=[13])
    assert evaluate_milestone('C6', actual_output, baseline=baseline, history=[baseline, retarget, actual_output]).passed
    # Item statistics retain output even when the AC recipe is absent or assigned again.
    no_recipe = _item_sample(4, 298)
    assert evaluate_milestone('C6', no_recipe, baseline=baseline).evidence['production_delta'] == 1
    assert evaluate_milestone('C6', _item_sample(5, 298), baseline=baseline).evidence['production_delta'] == 1


def test_item_counter_refuses_scope_reset_clock_and_legacy_comparisons():
    from orchestrator.checkpoint_milestones import _new_output
    baseline = _item_sample(10, 297)
    for current in (_item_sample(20, 300, scope='other-surface'), _item_sample(20, 300, generation=1),
                    _item_sample(20, 1), _item_sample(9, 300), _item_sample(10, 300)):
        value, evidence = _new_output(current, baseline, 'advanced-circuit')
        assert value is None and evidence['production_delta'] is None
        assert evidence['production_counter']['unavailable_reason']
    legacy = _ac(1, 0, [])
    for current, before in ((baseline, legacy), (legacy, baseline)):
        assert _new_output(current, before, 'advanced-circuit')[0] is None


def test_observed_item_counter_reset_generation_survives_history_eviction():
    from orchestrator.checkpoint_milestones import _new_output, track_item_production
    from tools.laya_strict_sidecar import Measurement
    measurement = Measurement({})
    baseline = _item_sample(10, 297)
    measurement.add(baseline)
    measurement.add(_item_sample(20, 1))
    for tick in range(21, 50):
        measurement.add(_item_sample(tick, 400 + tick))
    current = measurement.history[-1]
    assert len(measurement.history) == 20
    assert current.production['advanced-circuit'].counter_generation == 1
    assert _new_output(current, measurement.baseline, 'advanced-circuit')[0] is None
    rolled_back = track_item_production(_item_sample(5, 1000), current)
    assert rolled_back.production['advanced-circuit'].counter_generation == 2
