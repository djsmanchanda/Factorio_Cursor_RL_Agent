# Path: tests/test_laya_research.py | Purpose: Protect level-aware research completion, useful throughput ramp and funded operation boundaries.
# Ported from local laya/release 5a5ed28:tests/test_laya_research.py. Omitted tests that require the
# unported training.laya_mission.research_phase and the unported finite_material_progress ledger helpers.
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from training.laya_research import ResearchObjective
from orchestrator.research_operations import research_observation, select_research
from planners.research_labs import logistic_lab_row
from planners.plan_validation import validate_no_collisions
from orchestrator.work_state import WorkStateSignal

PACK = "automation-science-pack"
BASE_RECIPE = {"name": PACK, "enabled": True, "category": "chemistry",
               "ingredients": [{"name": "iron-plate", "type": "item", "amount": 1}],
               "products": [{"name": PACK, "type": "item", "amount": 1}], "supported": True}
CONSUMER_RECIPE = {"name": "science-recycling", "enabled": True, "category": "crafting",
                   "ingredients": [{"name": PACK, "type": "item", "amount": 1}],
                   "products": [{"name": "iron-plate", "type": "item", "amount": 1}], "supported": True}
MACHINE = {"name": "assembling-machine-1", "type": "assembling-machine",
           "ingredient_count": 2, "crafting_speed": 0.5, "categories": ["crafting"]}
LAB_CAPABILITY = {"consumer": "science"}
MACHINE_CAPABILITY = {"consumer": "recipe", "crafting_categories": ["crafting"],
                      "ingredient_count": 2, "fixed_recipe": None}


def test_unknown_consumer_category_fails_closed():
    from training.laya_research import _alternate_pack_consumer

    recipe = {**CONSUMER_RECIPE, "category": "unavailable", "categories": [],
              "supported": False, "unsupported_reason": "recipe category unavailable or malformed"}
    facts = {"tick": 100, "force": "player", "surface": "nauvis", "infrastructure_inventory": inventory_document(100)}
    with pytest.raises(ValueError, match="applicability unavailable"):
        _alternate_pack_consumer(facts, {recipe["name"]: recipe}, {PACK: 1}, 1)


def catalog(tick, *, recipes=None, count=None, version="1.2.0", force="player",
            surface="nauvis", machines=None):
    entries = list(recipes) if recipes is not None else [copy.deepcopy(BASE_RECIPE)]
    return {"version": version, "force": force, "tick": tick, "surface": surface,
            "recipes": entries, "recipe_count": len(entries) if count is None else count,
            "machines": list(machines) if machines is not None else []}


def inventory_document(tick, *, counts=None, entities=None, **overrides):
    counts = {"lab": 1} if counts is None else dict(counts)
    entities = {"lab": copy.deepcopy(LAB_CAPABILITY)} if entities is None else copy.deepcopy(entities)
    payload = {"version": "1.0.0", "force": "player", "tick": tick, "surface": "nauvis",
               "complete": True, "entity_count": sum(counts.values()),
               "counts": counts, "entities": entities}
    payload.update(overrides)
    return payload


def reports(tick=100, *, level=4, complete=False):
    tech = {"name": "mining-productivity-3", "canonical_name": "mining-productivity-3",
            "requested_name": "mining-productivity-4", "requested_level": 4, "current_level": level,
            "target_completed": complete, "state": "completed" if complete else "current",
            "researched": complete and level == 4, "enabled": True, "science_packs": {PACK: 1},
            "research_unit_count": 1000}
    science = json.loads((Path(__file__).parent / "fixtures/science_status/science_progressing_start.json").read_text())
    science["tick"] = tick
    science["research"].update(current="mining-productivity-3", progress=0, queue=["mining-productivity-3"])
    return {"research": {"ok": True, "force": "player", "tick": tick, "technology": tech,
                         "current_research": "mining-productivity-3", "research_progress": 0,
                         "research_queue": ["mining-productivity-3"]}, "science": science}


def provenance(start, end, *, made=0, consumed=0, stock=(0, 0), lab=(10, 10),
               non_lab_consumers=(), source="continuous-consumer-census"):
    start_stock, end_stock = stock
    start_lab, end_lab = lab
    return {"version": "1.0.0", "force": "player", "surface": "nauvis",
            "scope": "force-surface", "start_tick": start, "end_tick": end,
            "packs": {PACK: {"start_stock": start_stock, "end_stock": end_stock,
                              "start_lab_input": start_lab, "end_lab_input": end_lab,
                              "produced": made, "consumed": consumed}},
            "non_lab_consumers": list(non_lab_consumers),
            "coverage": {"scope": "force-surface", "from_tick": start, "to_tick": end,
                         "complete": True, "source": source}}


def pair(tick, *, made=0, consumed=0, progress=0, complete=False, inventory=10, recipe_catalog=None,
         proof=False):
    raw = reports(tick, complete=complete)
    raw["research"]["research_progress"] = progress
    raw["science"]["labs"]["input_inventory"][PACK] = inventory
    observed = research_observation(raw, tick=tick, surface="nauvis", force="player", requested="mining-productivity-4")
    facts = {"tick": tick, "surface": "nauvis", "force": "player", "mods": {"base": "2.1.23"},
             "production": {PACK: made}, "consumption": {PACK: consumed}, "networks": {},
             "infrastructure_counts": {"lab": 1},
             "infrastructure_inventory": inventory_document(tick),
             "recipe_catalog": recipe_catalog if recipe_catalog is not None else catalog(tick)}
    if proof:
        facts["research_provenance"] = provenance(0, tick, made=made, consumed=consumed,
                                                  lab=(10, inventory))
    return observed, facts


@pytest.mark.parametrize("mutation", ["stale", "force", "level", "identity"])
def test_research_report_fails_closed(mutation):
    raw = reports()
    if mutation == "stale": raw["research"]["tick"] = 0
    if mutation == "force": raw["research"]["force"] = "other"
    if mutation == "level": raw["research"]["technology"]["requested_level"] = 3
    if mutation == "identity": raw["research"]["technology"]["canonical_name"] = "unrelated"
    result = research_observation(raw, tick=500 if mutation == "stale" else 100,
                                  surface="nauvis", force="player", requested="mining-productivity-4")
    assert result["available"] is False


def test_science_production_alone_and_precompleted_start_cannot_earn_research():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(18000, made=10000, consumed=0), service_ok=True)
    assert not result["earned"] and result["target"] == 0.25
    precompleted = ResearchObjective()
    precompleted.observe(*pair(0, complete=True), service_ok=True)
    result = precompleted.observe(*pair(18000, made=100, consumed=100, complete=True), service_ok=True)
    assert not result["earned"] and result["ineligible_start"]


def test_incomplete_recipe_survey_is_not_acceptance_evidence():
    observed, facts = pair(0)
    facts["recipe_catalog"]["complete"] = False
    result = ResearchObjective().observe(observed, facts, service_ok=True)
    assert result["available"] is False
    assert result["reason"] == "recipe catalog incomplete"


def test_progressive_target_requires_useful_consumed_window_and_restores_exactly():
    objective = ResearchObjective(sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    restored = ResearchObjective(sustain_ticks=600, state=json.loads(json.dumps(objective.snapshot())))
    data = pair(600, made=3, consumed=3, progress=.003)
    result = objective.observe(*data, service_ok=True)
    assert result == restored.observe(*data, service_ok=True)
    assert result["target"] == .5 and result["achieved"] == .25 and result["advanced"]
    assert not result["earned"] and result["research_units"] == 3
    # Changing demand neither manufactures science nor increments useful research.
    repeat = objective.observe(*data, service_ok=True)
    assert repeat["research_units"] == 3 and len(repeat["rungs"]) == 1


@pytest.mark.parametrize("condition", ["inventory_depleted", "service_lost", "fault", "not_consumed", "alternate_consumer"])
def test_ramp_does_not_advance_from_depletion_faults_or_unconsumed_stock(condition):
    objective = ResearchObjective(sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    obs, facts = pair(600, made=3, consumed=3, progress=.003)
    if condition == "inventory_depleted": obs["labs"]["input_inventory"][PACK] = 0
    if condition == "not_consumed": facts["consumption"][PACK] = 0
    if condition == "alternate_consumer":
        facts["infrastructure_counts"]["science-eater"] = 1
        facts["infrastructure_inventory"] = inventory_document(
            600, counts={"lab": 1, "science-eater": 1},
            entities={"lab": LAB_CAPABILITY, "science-eater": {"consumer": "science"}})
    result = objective.observe(obs, facts, service_ok=condition != "service_lost", clean=condition != "fault")
    assert result["target"] == .25 and result["achieved"] == 0


def test_completed_requested_level_needs_fresh_factory_earned_supply():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, made=1000, consumed=1000, complete=True, proof=True), service_ok=True)
    assert result["earned"] and result["reason"] == "requested_level_completed_with_earned_supply"
    assert result["provenance_status"] == "receipt_consistent"
    assert result["requested"] == "mining-productivity-4"


def test_complete_scoped_conservation_receipt_earns_only_with_explicit_interval_proof():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    facts["research_provenance"] = provenance(0, 600, made=1000, consumed=1000)
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is True and result["earned"] is True
    assert result["provenance_status"] == "receipt_consistent"


def test_missing_interval_receipt_withholds_earned_with_typed_reason():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, made=1000, consumed=1000, complete=True), service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["reason"] == "requested_level_completed_without_provenance_evidence"
    assert result["provenance_status"] == "missing"


def test_caller_trust_booleans_and_bare_receipts_are_not_provenance():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    facts.update(earned=True, provenance_verified=True, no_non_lab_consumers=True)
    facts["research_provenance"] = True
    result = objective.observe(observed, facts, service_ok=True)
    assert result["earned"] is False and result["provenance_status"] == "invalid"
    assert result["reason"] == "requested_level_completed_with_invalid_provenance_evidence"


@pytest.mark.parametrize(
    "mutation,status",
    [("force", "scope_mismatch"), ("end_tick", "interval_mismatch"),
     ("coverage", "coverage_incomplete"), ("packs", "sample_mismatch"),
     ("consumer_type", "invalid")],
)
def test_malformed_interval_receipt_is_typed_and_withholds_earned(mutation, status):
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    receipt = provenance(0, 600, made=1000, consumed=1000)
    if mutation == "force":
        receipt["force"] = "other"
    elif mutation == "end_tick":
        receipt["end_tick"] = 599
    elif mutation == "coverage":
        receipt["coverage"]["complete"] = False
    elif mutation == "packs":
        receipt["packs"]["chemical-science-pack"] = copy.deepcopy(receipt["packs"][PACK])
    elif mutation == "consumer_type":
        receipt["non_lab_consumers"] = True
    facts["research_provenance"] = receipt
    result = objective.observe(observed, facts, service_ok=True)
    assert result["earned"] is False and result["provenance_status"] == status
    assert result["reason"] == "requested_level_completed_with_invalid_provenance_evidence"


def test_transient_consumer_between_endpoint_censuses_withholds_earned():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    # Endpoint census names only the lab, but the interval receipt reports a
    # consumer that operated between samples.
    facts["research_provenance"] = provenance(0, 600, made=1000, consumed=1000,
                                              non_lab_consumers=["assembling-machine-1"])
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["provenance_status"] == "transient_consumer"
    assert result["reason"] == "requested_level_completed_with_invalid_provenance_evidence"


@pytest.mark.parametrize("buffer", ["network_stock", "lab_input"])
def test_old_pack_drawdown_cannot_earn_even_when_counter_deltas_cover_demand(buffer):
    objective = ResearchObjective()
    first_observed, first_facts = pair(0, progress=.5)
    if buffer == "network_stock":
        first_facts["networks"] = {"old": {"items": {PACK: 1000}}}
    else:
        first_observed["labs"]["input_inventory"][PACK] = 1000
    objective.observe(first_observed, first_facts, service_ok=True)
    observed, facts = pair(600, made=500, consumed=1000, complete=True, inventory=10)
    if buffer == "network_stock":
        facts["networks"] = {"old": {"items": {PACK: 500}}}
        stock = (1000, 500)
        lab = (10, 10)
    else:
        observed["labs"]["input_inventory"][PACK] = 500
        stock = (0, 0)
        lab = (1000, 500)
    facts["research_provenance"] = provenance(0, 600, made=500, consumed=1000,
                                              stock=stock, lab=lab)
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["provenance_status"] == "stock_drawdown"
    assert result["reason"] == "requested_level_completed_with_invalid_provenance_evidence"


def test_nonempty_recipe_map_is_not_completeness_evidence():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    for mutation in ("missing", "flat_map"):
        observed, facts = pair(600, made=1000, consumed=1000, complete=True)
        facts.pop("recipe_catalog")
        if mutation == "flat_map":
            facts["recipe_observations"] = {
                PACK: {"enabled": True, "ingredients": [{"name": "iron-plate"}], "machine_speeds": {}}}
        result = objective.observe(observed, facts, service_ok=True)
        assert result["available"] is False and result["earned"] is False
        assert "catalog" in result["reason"]


def test_recipe_observations_envelope_still_supports_complete_evidence():
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, proof=True)
    facts["recipe_observations"] = facts.pop("recipe_catalog")
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    assert objective.observe(observed, facts, service_ok=True)["earned"] is True


def test_catalog_metadata_can_describe_the_legacy_recipe_map():
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, proof=True)
    facts["recipe_observations"] = {
        PACK: {"enabled": True, "ingredients": [{"name": "iron-plate"}], "machine_speeds": {}}}
    facts["recipe_catalog"] = {key: value for key, value in catalog(600).items() if key != "recipes"}
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    assert objective.observe(observed, facts, service_ok=True)["earned"] is True


def test_version_13_research_catalog_requires_mod_identity():
    from training.laya_research import _complete_recipes
    evidence = catalog(600, version="1.3.0")
    facts = {"force": "player", "surface": "nauvis", "tick": 600, "recipe_catalog": evidence}
    with pytest.raises(ValueError, match="mod identity"):
        _complete_recipes(facts)
    evidence.update(active_mods={"base": "2.1.21"}, active_mods_count=1)
    assert _complete_recipes(facts)[1] is evidence
    evidence["active_mods_count"] = True
    with pytest.raises(ValueError, match="mod identity"):
        _complete_recipes(facts)


@pytest.mark.parametrize(
    "mutation",
    ["version", "force", "tick", "surface", "count", "count_missing", "recipes_missing",
     "duplicate", "malformed", "empty"],
)
def test_incomplete_or_inconsistent_catalog_is_unavailable(mutation):
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    evidence = facts["recipe_catalog"]
    if mutation == "version":
        evidence["version"] = "9.9.9"
    elif mutation == "force":
        evidence["force"] = "other"
    elif mutation == "tick":
        evidence["tick"] = 599
    elif mutation == "surface":
        evidence["surface"] = "vulcanus"
    elif mutation == "count":
        evidence["recipe_count"] = 2
    elif mutation == "count_missing":
        evidence.pop("recipe_count")
    elif mutation == "recipes_missing":
        evidence.pop("recipes")
    elif mutation == "duplicate":
        evidence["recipes"] = [copy.deepcopy(BASE_RECIPE), copy.deepcopy(BASE_RECIPE)]
        evidence["recipe_count"] = 2
    elif mutation == "malformed":
        evidence["recipes"] = [{"name": PACK, "enabled": True}]
        evidence["recipe_count"] = 1
    elif mutation == "empty":
        evidence["recipes"] = []
        evidence["recipe_count"] = 0
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False


def test_partial_catalog_cannot_claim_a_full_count_or_earn():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    partial = catalog(600, recipes=[BASE_RECIPE], count=2, machines=[MACHINE])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=partial)
    facts["infrastructure_counts"]["assembling-machine-1"] = 1
    facts["infrastructure_inventory"] = inventory_document(
        600, counts={"lab": 1, "assembling-machine-1": 1},
        entities={"lab": LAB_CAPABILITY, "assembling-machine-1": MACHINE_CAPABILITY})
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "count inconsistent" in result["reason"]


def test_alternate_science_pack_consumer_blocks_earned_completion():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    full = catalog(600, recipes=[BASE_RECIPE, CONSUMER_RECIPE], machines=[MACHINE])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=full)
    facts["infrastructure_counts"]["assembling-machine-1"] = 1
    facts["infrastructure_inventory"] = inventory_document(
        600, counts={"lab": 1, "assembling-machine-1": 1},
        entities={"lab": LAB_CAPABILITY, "assembling-machine-1": MACHINE_CAPABILITY})
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "alternate pack consumer" in result["reason"]


def _assembler_facts(observed, facts, recipe_catalog):
    facts["recipe_catalog"] = recipe_catalog
    facts["infrastructure_counts"]["assembling-machine-1"] = 1
    facts["infrastructure_inventory"] = inventory_document(
        600, counts={"lab": 1, "assembling-machine-1": 1},
        entities={"lab": LAB_CAPABILITY, "assembling-machine-1": MACHINE_CAPABILITY})
    return observed, facts


def test_missing_installed_consumer_inventory_is_not_empty_evidence():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    facts.pop("infrastructure_inventory")
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "installed consumer inventory unavailable" in result["reason"]


def test_legacy_infrastructure_counts_alone_is_not_completeness_evidence():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    facts.pop("infrastructure_inventory")
    assert facts["infrastructure_counts"] == {"lab": 1}
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False


@pytest.mark.parametrize(
    "mutation",
    ["not_mapping", "version", "force", "tick", "surface", "complete", "entity_count",
     "count_type", "count_zero", "missing_capability", "extra_capability",
     "unknown_consumer", "recipe_without_applicability", "categories_malformed",
     "fixed_malformed", "legacy_mismatch", "lab_count_mismatch"],
)
def test_malformed_or_partial_installed_inventory_is_unavailable(mutation):
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    evidence = facts["infrastructure_inventory"]
    if mutation == "not_mapping":
        facts["infrastructure_inventory"] = []
    elif mutation == "version":
        evidence["version"] = "9.9.9"
    elif mutation == "force":
        evidence["force"] = "other"
    elif mutation == "tick":
        evidence["tick"] = 599
    elif mutation == "surface":
        evidence["surface"] = "vulcanus"
    elif mutation == "complete":
        evidence["complete"] = False
    elif mutation == "entity_count":
        evidence["entity_count"] = 2
    elif mutation == "count_type":
        evidence["counts"]["lab"] = "1"
    elif mutation == "count_zero":
        evidence["counts"]["lab"] = 0
        evidence["entity_count"] = 0
    elif mutation == "missing_capability":
        evidence["entities"].pop("lab")
    elif mutation == "extra_capability":
        evidence["entities"]["unlisted"] = {"consumer": "none"}
    elif mutation == "unknown_consumer":
        evidence["entities"]["lab"]["consumer"] = "maybe"
    elif mutation == "recipe_without_applicability":
        evidence["entities"]["lab"] = {"consumer": "recipe", "ingredient_count": 0,
                                       "crafting_categories": []}
    elif mutation == "categories_malformed":
        evidence["entities"]["lab"] = {"consumer": "none", "crafting_categories": "crafting"}
    elif mutation == "fixed_malformed":
        evidence["entities"]["lab"] = {"consumer": "none", "fixed_recipe": 5}
    elif mutation == "legacy_mismatch":
        facts["infrastructure_counts"] = {"lab": 2}
    elif mutation == "lab_count_mismatch":
        evidence["counts"]["lab"] = 2
        evidence["entity_count"] = 2
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "inventory" in result["reason"] or "applicability" in result["reason"]


def test_noncrafting_pack_consumer_outside_labs_blocks_attribution():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    facts["infrastructure_counts"]["science-eater"] = 1
    facts["infrastructure_inventory"] = inventory_document(
        600, counts={"lab": 1, "science-eater": 1},
        entities={"lab": LAB_CAPABILITY, "science-eater": {"consumer": "science"}})
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "alternate pack consumer present" in result["reason"]


def test_installed_recycler_running_a_pack_recycling_recipe_blocks():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    recycling = {**copy.deepcopy(CONSUMER_RECIPE), "category": "recycling"}
    full = catalog(600, recipes=[BASE_RECIPE, recycling])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=full)
    facts["infrastructure_counts"] = {"lab": 1, "recycler": 1}
    facts["infrastructure_inventory"] = inventory_document(
        600, counts={"lab": 1, "recycler": 1},
        entities={"lab": LAB_CAPABILITY,
                  "recycler": {"consumer": "recipe", "crafting_categories": ["recycling"],
                               "ingredient_count": 0, "fixed_recipe": None}})
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "alternate pack consumer present" in result["reason"]


def test_disabled_pack_consumer_on_installed_machine_still_blocks():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    disabled = {**copy.deepcopy(CONSUMER_RECIPE), "enabled": False}
    full = catalog(600, recipes=[BASE_RECIPE, disabled], machines=[MACHINE])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=full)
    observed, facts = _assembler_facts(observed, facts, full)
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "alternate pack consumer present" in result["reason"]


def test_disabled_pack_consumer_without_an_installed_machine_does_not_block():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    disabled = {**copy.deepcopy(CONSUMER_RECIPE), "enabled": False}
    full = catalog(600, recipes=[BASE_RECIPE, disabled], machines=[MACHINE])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=full, proof=True)
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is True and result["earned"] is True


def test_pack_consumer_with_unknown_machine_applicability_is_unavailable():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    unknown = {"name": "mystery-packs", "enabled": True,
               "ingredients": [{"name": PACK, "type": "item", "amount": 1}],
               "products": [{"name": "iron-plate", "type": "item", "amount": 1}]}
    full = catalog(600, recipes=[BASE_RECIPE, unknown])
    observed, facts = pair(600, made=1000, consumed=1000, complete=True, recipe_catalog=full)
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is False and result["earned"] is False
    assert "applicability unavailable" in result["reason"]


def test_completion_needs_quantified_factory_supply_not_just_positive_counters():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, made=1, consumed=1, complete=True), service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["reason"] == "requested_level_completed_without_quantified_factory_supply"
    assert result["required_packs"][PACK] == pytest.approx(1000)
    assert result["earned_shortfall"][PACK] == pytest.approx(999)


def test_nearly_complete_start_with_presupplied_packs_cannot_earn():
    objective = ResearchObjective()
    objective.observe(*pair(0, progress=.999, inventory=10), service_ok=True)
    result = objective.observe(
        *pair(600, made=0, consumed=1, progress=0, complete=True, inventory=0), service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["required_packs"][PACK] == pytest.approx(1)
    assert result["earned_shortfall"][PACK] == pytest.approx(1)


def test_completed_level_uses_its_own_declared_demand_after_level_change():
    objective = ResearchObjective()
    first_observed, first_facts = pair(0)
    first_observed["technology"].update(current_level=3, state="available",
                                        research_unit_count=5000)
    objective.observe(first_observed, first_facts, service_ok=True)
    result = objective.observe(*pair(600, made=1000, consumed=1000, complete=True, proof=True), service_ok=True)
    assert result["earned"] is True
    assert result["required_packs"][PACK] == pytest.approx(1000)


def test_partial_start_completion_uses_remaining_declared_demand():
    objective = ResearchObjective()
    objective.observe(*pair(0, progress=.5), service_ok=True)
    result = objective.observe(*pair(600, made=500, consumed=500, complete=True, proof=True), service_ok=True)
    assert result["earned"] is True
    assert result["required_packs"][PACK] == pytest.approx(500)


def test_completion_needs_matching_consumption_not_only_production():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, made=1000, consumed=999, complete=True), service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["reason"] == "requested_level_completed_without_quantified_factory_supply"


def test_completion_without_a_unit_count_withholds_earned_claim():
    objective = ResearchObjective()
    first_observed, first_facts = pair(0)
    first_observed["technology"].pop("research_unit_count")
    objective.observe(first_observed, first_facts, service_ok=True)
    observed, facts = pair(600, made=1000, consumed=1000, complete=True)
    observed["technology"].pop("research_unit_count")
    result = objective.observe(observed, facts, service_ok=True)
    assert result["available"] is True and result["earned"] is False
    assert result["reason"] == "requested_level_completed_without_quantifiable_supply"


@pytest.mark.parametrize("overrides", [{"service_ok": False}, {"clean": False}])
def test_hard_invalid_completion_evidence_cannot_earn(overrides):
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    kwargs = {"service_ok": True, "clean": True, **overrides}
    result = objective.observe(*pair(600, made=1000, consumed=1000, complete=True), **kwargs)
    assert result["earned"] is False


def test_hard_invalid_same_tick_evidence_revokes_earned_completion():
    objective = ResearchObjective()
    objective.observe(*pair(0), service_ok=True)
    assert objective.observe(*pair(600, made=1000, consumed=1000, complete=True, proof=True), service_ok=True)["earned"]
    result = objective.observe(*pair(600, made=1000, consumed=1000, complete=True, proof=True), service_ok=False)
    assert result["earned"] is False


def test_funded_lab_geometry_has_no_scaffolding_or_item_grants():
    plan = logistic_lab_row(6, {PACK: 1, "chemical-science-pack": 1})
    validate_no_collisions([("labs", plan)])
    actions = plan["phases"][0]["actions"]
    assert all(a["action_type"] == "place_ghost" for a in actions)
    assert sum(a["entity"] == "lab" for a in actions) == 6
    assert not any(a["entity"] in {"electric-energy-interface", "substation"} for a in actions)
    assert all(a["logistic_sections"][0]["requests"] for a in actions if a["entity"] == "requester-chest")


def test_research_selection_is_guarded_idempotent_and_checks_readback(monkeypatch):
    import orchestrator.research_operations as module
    monkeypatch.setattr(module, "load_json", lambda value: value)
    before = reports()["research"]
    before.update(current_research=None, research_queue=[])
    before["technology"]["state"] = "available"
    calls = []
    def select(technology, **kwargs):
        calls.append(technology)
        before.update(current_research="mining-productivity-3", research_queue=["mining-productivity-3"])
        return {"ok": True, "force": "player", "technology": technology, "canonical_name": "mining-productivity-3", "tick": 100}
    bridge = SimpleNamespace(research_status=lambda **_: copy.deepcopy(before), set_research=select)
    outcome = select_research(bridge, force="player", technology="mining-productivity-4", expected_current=None, expected_queue=[])
    assert outcome["verified"] and outcome["submitted"]
    result = select_research(bridge, force="player", technology="mining-productivity-4",
                             expected_current="mining-productivity-3", expected_queue=["mining-productivity-3"])
    assert result["idempotent"] and calls == ["mining-productivity-4"]
    with pytest.raises(WorkStateSignal) as error:
        select_research(bridge, force="player", technology="mining-productivity-4", expected_current=None, expected_queue=[])
    assert error.value.code == "research_queue_changed"


def test_repeatable_level_transition_counts_real_progress_across_reset():
    objective = ResearchObjective(sustain_ticks=600)
    observed, facts = pair(0, progress=.9)
    observed["technology"].update(requested_level=4, current_level=3, state="future")
    objective.observe(observed, facts, service_ok=True)
    after, facts = pair(600, progress=.01, made=100, consumed=100)
    after["technology"]["research_unit_count"] = 2000
    result = objective.observe(after, facts, service_ok=True)
    assert result["progressed"] and result["research_units"] == pytest.approx(120)
    assert not result["earned"]


def test_fixed_target_keeps_rate_while_recording_sustained_consumption():
    objective = ResearchObjective(progressive=False, sustain_ticks=600)
    objective.observe(*pair(0), service_ok=True)
    result = objective.observe(*pair(600, progress=.003, made=3, consumed=3), service_ok=True)
    assert result["target"] == .25 and result["achieved"] == .25 and not result["advanced"]


def test_lab_packet_retries_use_same_owned_plan_and_real_bill(tmp_path, monkeypatch):
    import orchestrator.research_operations as operation
    import orchestrator.live_base as live_base
    from orchestrator import autonomous_builder as ab
    from orchestrator.material_reservations import MaterialReservationLedger, plan_material_bill
    now = 1000
    status = {"schema_version": "1.0.0", "ok": True, "tick": 900,
              "surface": "nauvis", "force": "player",
              "labs": {"total": 0, "working": 0, "status_counts": {},
                       "input_inventory": {}, "entries": []}}
    actions = logistic_lab_row(1, {PACK: 1}, origin=(21, 21))["phases"][0]["actions"]
    units = {(float(a["position"]["x"]), float(a["position"]["y"])): 700 + i
             for i, a in enumerate(actions)}
    evidence = {"version": operation.LAB_OWNERSHIP_EVIDENCE_VERSION,
                "source": operation.LAB_OWNERSHIP_EVIDENCE_SOURCE,
                "episode_id": "labs", "surface": "nauvis", "force": "player",
                "packet_id": "research_labs_0", "tick": now,
                "ok": True, "ownership_claimed": False, "dispatch_authorized": False,
                "entities": [{"entity": a["entity"], "position": a["position"],
                              "unit_number": units[(float(a["position"]["x"]),
                                                    float(a["position"]["y"]))],
                              "tick": now,
                              "tags": {"research_lab_packet": "research_labs_0"}}
                             for a in actions]}
    def ownership(**request):
        report = copy.deepcopy(evidence)
        report["transaction_id"] = request["transaction_id"]
        for entry in report["entities"]:
            entry["tags"]["research_lab_transaction"] = request["transaction_id"]
        return report
    bridge = SimpleNamespace(
        science_status=lambda surface, force: copy.deepcopy(status),
        research_lab_ownership=ownership,
    )
    ledger=MaterialReservationLedger(tmp_path/'script-output',episode_id='labs',surface='nauvis',force='player')
    monkeypatch.setattr(operation, 'load_json', lambda value: value)
    monkeypatch.setattr(ab,'_MATERIAL_RESERVATION_LEDGER',ledger)
    monkeypatch.setattr(ab.live_base,'find_clear_area',lambda *a,**kw:(20,20))
    monkeypatch.setattr(live_base,'game_tick',lambda client: now)
    monkeypatch.setattr(live_base,'entity_signatures_at',lambda *a,**kw:{})
    monkeypatch.setattr(live_base,'entity_unit_numbers_at',lambda *a,**kw:dict(units))
    submitted=[]
    monkeypatch.setattr(ab,'_submit',lambda c,b,s,p,name,emit,**kw:submitted.append((name,copy.deepcopy(p))))
    monkeypatch.setattr('orchestrator.stage_services._await_bot_built_infrastructure',lambda *a,**kw:None)
    monkeypatch.setattr(ab,'extend_power',lambda *a,**kw:None)
    monkeypatch.setattr(ab,'ensure_logistic_coverage',lambda *a,**kw:None)
    session=SimpleNamespace(client=object(),bridge=bridge,reference_point=(0,0),emit=lambda _:None)
    first=operation.build_labs(session,count=1,packs={PACK:1})
    second=operation.build_labs(session,count=1,packs={PACK:1})
    assert first==second and len(submitted)==4
    assert submitted[:2]==submitted[2:]
    assert all(a["action_type"]=="configure_entity_if_unit_number" and a["expected_unit_number"]>0 for a in submitted[1][1]["phases"][0]["actions"])
    assert plan_material_bill(submitted[0][1])=={'lab':1,'requester-chest':1,'inserter':1,'medium-electric-pole':1}


def test_research_option_facts_follow_current_goal_and_unknown_capacity():
    from training.laya_research import add_research_options
    observed,facts=pair(100)
    menu=add_research_options({'options':[]},{'targets':{'iron-plate':.5},'needs':[]},observed,facts,ResearchObjective())
    assert all(not o['features']['goal_need'] for o in menu['options'])
    # Existing working labs with unknown capacity cannot justify unlimited growth.
    assert not any(o['family']=='research_labs' and o['option_id']!='op.research_labs.reconcile' for o in menu['options'])
