# Path: tests/test_laya_chain_facts.py | Purpose: Verify conservative consumer continuity, choices and shared-bill accounting.

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from training.laya_chain_facts import ConsumerObservation, bill_facts
from training.laya_ops_runner import add_consumer_options, compile_options


def world(tick=100, count=5):
    return {"tick": tick, "surface": "nauvis", "force": "player", "mods": {"base": "2"},
            "recipe_observations": {"chemical-science-pack": {"enabled": True,
                "ingredients": [{"name": "advanced-circuit", "amount": 3, "type": "item"},
                                {"name": "engine-unit", "amount": 2, "type": "item"},
                                {"name": "sulfur", "amount": 1, "type": "item"}],
                "products": [{"name": "chemical-science-pack", "type": "item", "amount": 2}],
                "machine_speeds": {"assembling-machine-2": 0.75}}},
            "machines": [{"name": "assembling-machine-2", "unit_number": 10,
                          "recipe": "chemical-science-pack", "products_finished": count}],
            "networks": {"1": {"items": {"advanced-circuit": 3, "engine-unit": 2, "sulfur": 1},
                                "reserved": {}}}}


def test_consumer_requires_same_machine_recipe_counter_and_positive_interval():
    observer = ConsumerObservation("advanced-circuit")
    assert observer.observe(world())["status"] == "unknown"
    observed = observer.observe(world(101, 7))
    assert observed["status"] == "observed"
    assert observed["progress"] == [{"unit_number": 10, "recipe": "chemical-science-pack", "baseline": 5,
                                     "observed": 7, "from_tick": 100, "tick": 101}]
    assert observer.observe(world(102, 7))["status"] == "unknown"


def test_survey_emits_machine_identity_and_counter_from_lua():
    from tests.test_laya_fluid_status import LUA_PRELUDE

    lua = shutil.which("lua")
    if not lua:
        pytest.skip("Lua runtime unavailable")
    source = Path("training/laya_saved_world.lua").read_text().rsplit("return facts", 1)[0]
    setup = "chemical.unit_number=123; chemical.products_finished=7\n"
    assertions = 'assert(facts.machines[1].unit_number==123); assert(facts.machines[1].products_finished==7)'
    result = subprocess.run([lua, "-"], input=LUA_PRELUDE + setup + source + assertions,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("change", ["identity", "recipe", "missing_counter", "reset", "disappear", "scope", "catalog", "tick"])
def test_consumer_does_not_latch_across_discontinuities(change):
    observer = ConsumerObservation("advanced-circuit")
    observer.observe(world())
    current = world(101, 6)
    if change == "identity":
        current["machines"][0]["unit_number"] = 11
    elif change == "recipe":
        current["machines"][0]["recipe"] = "other"
    elif change == "missing_counter":
        del current["machines"][0]["products_finished"]
    elif change == "reset":
        current["machines"][0]["products_finished"] = 1
    elif change == "disappear":
        current["machines"] = []
    elif change == "scope":
        current["surface"] = "other"
    elif change == "catalog":
        current["mods"]["base"] = "3"
    else:
        current["tick"] = 99
    assert observer.observe(current)["status"] == "unknown"
    assert observer.observe(world(102, 7))["status"] == "unknown"


def test_complete_bills_cannot_double_spend_and_unknown_is_not_free():
    bills = {"first": {"assembling-machine-1": 1, "long-handed-inserter": 2, "chest": 2},
             "second": {"assembling-machine-1": 1, "long-handed-inserter": 1, "chest": 2}}
    free = {"assembling-machine-1": 1, "long-handed-inserter": 2, "chest": 2}
    result = bill_facts(bills, free, complete=True)
    assert all(stage["funded_individually"] for stage in result["stages"].values())
    assert result["jointly_funded"] is False
    assert result["joint_shortfall"] == {"assembling-machine-1": 1, "long-handed-inserter": 1, "chest": 2}
    assert bill_facts(bills, free, complete=False)["jointly_funded"] is None


def test_consumer_choice_checks_free_inputs_and_supported_recipe():
    facts = world()
    results = [{"ok": True, "code": "producing", "operation": "mall_slot", "params": {"item": "inserter"}}]
    menu = {"options": [], "needs": []}
    option = add_consumer_options(menu, facts, results, {"projects": {}}, {})["options"][0]
    assert option["params"] == {"item": "chemical-science-pack", "stock_target": 1}
    assert option["consumer_input_bill"]["advanced-circuit"] == 3
    assert option["features"]["bill_known"] is False
    for mutation in ("reserved", "disabled", "fluid", "unsupported", "unknown", "no_slot"):
        changed, economics, history = copy.deepcopy(facts), {"projects": {}}, results
        recipe = changed["recipe_observations"]["chemical-science-pack"]
        if mutation == "reserved":
            economics["projects"] = {"other": {"reserved": {"advanced-circuit": 1}}}
        elif mutation == "disabled":
            recipe["enabled"] = False
        elif mutation == "fluid":
            recipe["ingredients"][0]["type"] = "fluid"
        elif mutation == "unsupported":
            recipe["ingredients"][0]["amount"] = 4
        elif mutation == "unknown":
            economics["projects"] = None
        else:
            history = []
        assert not add_consumer_options({"options": [], "needs": []}, changed, history, economics, {})["options"]


def test_consumer_option_uses_real_batch_dispatch_and_reports_prerequisite(monkeypatch):
    from training import laya_ops

    calls = []
    monkeypatch.setattr(laya_ops.ab, "produce_to_demand", lambda *args, **kwargs: calls.append((args, kwargs)))
    session = object.__new__(laya_ops.DeterministicSession)
    session.client, session.bridge, session.reference_point, session.emit = object(), object(), (0, 0), lambda _: None
    session._maintain_mall = lambda: None
    results = [{"ok": True, "code": "producing", "operation": "mall_slot", "params": {"item": "inserter"}}]
    option = add_consumer_options({"options": [], "needs": []}, world(), results, {"projects": {}}, {})["options"][0]
    receipt = session.run(option["family"], **option["params"])
    assert calls[0][0][4] == "chemical-science-pack"
    assert calls[0][1]["stock_target"] == 1
    assert receipt["code"] == "prerequisite_advanced" and not receipt["ok"]


def test_fluid_shortages_do_not_displace_solid_produce_options():
    board = {"needs": []}
    results = [{"ok": True, "operation": "mall_slot", "params": {"item": "inserter"}},
               {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
                "code": "material_shortage", "details": {"required": {"water": 1000, "steam": 900,
                    "crude-oil": 800, "petroleum-gas": 700, "iron-gear-wheel": 2}, "available": {}}}]
    ids = {option["option_id"] for option in compile_options(board, results, filter_fluid_shortages=True)["options"]}
    assert "op.produce.iron-gear-wheel" in ids
    assert "op.produce.water" not in ids
