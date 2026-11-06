# Path: tests/test_laya_cable_capacity.py | Purpose: Protect the C6 copper-cable capacity option and its input-starvation gate.

from training.laya_clean_runner import Settings
from training.laya_ops_runner import add_cable_capacity_option

PHASE = {"phase_id": "C6"}
STARVED = {"needs": [{"item": "advanced-circuit", "condition": "input_starved"}]}
FACTS = {"recipes": {"copper-cable": {"ingredients": []}}}


def test_the_cable_option_is_off_by_default():
    assert Settings().cable_capacity is False


def test_cable_capacity_is_offered_only_when_ac_is_input_starved():
    menu = add_cable_capacity_option({"needs": [], "options": []}, STARVED, PHASE, FACTS)
    option = next(o for o in menu["options"] if o["option_id"] == "op.production_service.copper-cable")
    assert option["params"]["item"] == "copper-cable"
    assert option["params"]["upgrade_bootstrap"] is True
    assert option["params"]["minimum_machines"] == 6
    assert add_cable_capacity_option(
        {"needs": [], "options": []},
        {"needs": [{"item": "advanced-circuit", "condition": "short"}]}, PHASE, FACTS)["options"] == []
    assert add_cable_capacity_option(
        {"needs": [], "options": []}, STARVED, {"phase_id": "C5"}, FACTS)["options"] == []
