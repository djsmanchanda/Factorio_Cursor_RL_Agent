# Path: tests/test_checkpoint_milestones.py
# Purpose: Verify structured, versioned, monotonic checkpoint predicates.
from orchestrator.checkpoint_milestones import (
    C1_V1,
    C1_V2,
    C5_V1,
    CheckpointEvaluator,
    FactorySnapshot,
    PredicateStatus,
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
    assert [item.checkpoint_id for item in milestones] == ["C0", "C1", "C2", "C3", "C4", "C5", "C6"]
    versions = {item.checkpoint_id: item.predicate_version for item in milestones}
    assert versions["C1"] == "c1-v2"
    assert versions["C5"] == "c5-v2"
    assert all(version.endswith("-v1") for key, version in versions.items() if key not in {"C1", "C5"})
    assert milestones[-1].name == "advanced_circuit_consumption"


def test_missing_telemetry_is_unknown_not_failed():
    result = evaluate_milestone("C1", {"tick": 1})
    assert result.status is PredicateStatus.UNKNOWN
    assert "iron_starter_ready" in result.missing


def test_c1_requires_four_working_mall_cells_and_required_recipes():
    result = evaluate_milestone("C1", _snapshot())
    assert result.passed
    broken = _snapshot(mall_assemblers=[{"recipe": "iron-gear-wheel", "working": True}] * 4)
    assert evaluate_milestone("C1", broken).failed


def _mall(craft_counts, status, working=False):
    recipes = ("iron-gear-wheel", "copper-cable", "electronic-circuit", "transport-belt")
    return [
        {
            "recipe": recipe, "working": working, "status": status,
            "position": {"x": index, "y": 0}, "craft_count": craft,
        }
        for index, (recipe, craft) in enumerate(zip(recipes, craft_counts))
    ]


def test_c1_v2_counts_assembler_idle_on_full_output_that_v1_does_not():
    mall = [
        {"recipe": "iron-gear-wheel", "working": True},
        {"recipe": "copper-cable", "working": True},
        {"recipe": "electronic-circuit", "working": True},
        {"recipe": "iron-gear-wheel", "working": False, "status": "full_output"},
    ]
    snapshot = _snapshot(mall_assemblers=mall)
    assert evaluate_milestone("C1", snapshot, milestones=(C1_V1,)).failed
    assert evaluate_milestone("C1", snapshot, milestones=(C1_V2,)).passed


def test_c1_counts_genuinely_stopped_mall_assemblers_under_neither_version():
    history = [_snapshot(tick=0, mall_assemblers=_mall((5000, 5000, 5000, 5000), "no_power"))]
    snapshot = _snapshot(tick=7200, mall_assemblers=_mall((5000, 5000, 5000, 5000), "no_power"))
    for milestone in (C1_V1, C1_V2):
        assert evaluate_milestone("C1", snapshot, history=history, milestones=(milestone,)).failed


def test_c1_v2_counts_craft_advance_inside_window_only():
    inside = _snapshot(
        tick=7200,
        mall_assemblers=_mall((2000, 2000, 2000, 2000), "waiting_for_source_items"),
    )
    history = [_snapshot(tick=0, mall_assemblers=_mall((1000, 1000, 1000, 1000), "waiting_for_source_items"))]
    assert evaluate_milestone("C1", inside, history=history, milestones=(C1_V2,)).passed
    assert evaluate_milestone("C1", inside, history=history, milestones=(C1_V1,)).failed

    stale_history = [
        _snapshot(tick=0, mall_assemblers=_mall((1000, 1000, 1000, 1000), "waiting_for_source_items")),
        _snapshot(tick=1000, mall_assemblers=_mall((2000, 2000, 2000, 2000), "waiting_for_source_items")),
    ]
    stale = _snapshot(
        tick=14400,
        mall_assemblers=_mall((2000, 2000, 2000, 2000), "waiting_for_source_items"),
    )
    assert evaluate_milestone("C1", stale, history=stale_history, milestones=(C1_V2,)).failed


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


def test_c5_requires_120_seconds_and_advancing_plastic_evidence():
    def sample(tick, craft, delivered):
        return {"tick": tick, "production": {"plastic-bar": {"craft_count": craft}}, "providers": {"plastic-bar": {"delivered": delivered}}}

    history = [sample(0, 1, 1), sample(3600, 2, 2), sample(7200, 3, 3)]
    assert evaluate_milestone("C5", history[-1], history=history[:-1], milestones=(C5_V1,)).passed
    assert evaluate_milestone("C5", sample(6000, 3, 3), history=history[:-1], milestones=(C5_V1,)).failed


def test_c5_ignores_preproduction_history_from_c0_lane():
    def sample(tick, craft, delivered):
        return {"tick": tick, "production": {"plastic-bar": {"craft_count": craft}}, "providers": {"plastic-bar": {"delivered": delivered}}}

    history = [
        {"tick": 0},
        sample(100, 0, 0),
        sample(200, 1, 1),
        sample(3800, 2, 2),
    ]
    result = evaluate_milestone("C5", sample(7400, 3, 3), history=history, milestones=(C5_V1,))
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
    assert evaluate_milestone("C6", current, baseline=baseline).passed


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
