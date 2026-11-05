# Path: tests/test_laya_service_hold.py | Purpose: Protect the construction-bill hold on the production service.

from training.laya_ops_runner import add_c6_consumer_options, service_construction_blocks

FACTS = {"recipe_observations": {"productivity-module": {"enabled": True}}}
C6 = {"phase_id": "C6"}
SHORT = {"ok": False, "operation": "production_service", "code": "material_shortage",
         "params": {"item": "productivity-module", "stock_target": 10, "minimum_machines": 2},
         "details": {"required": {"assembling-machine-2": 4, "passive-provider-chest": 11},
                     "available": {"assembling-machine-2": 2, "passive-provider-chest": 5}}}


def test_a_service_short_of_its_bill_is_blocked_until_the_items_are_stocked():
    assert service_construction_blocks([SHORT], {}) == {"productivity-module": {
        "assembling-machine-2": 2, "passive-provider-chest": 6}}
    assert service_construction_blocks([SHORT], {"assembling-machine-2": 4,
                                                 "passive-provider-chest": 11}) == {}


def test_the_blocked_service_option_is_held_and_returns_when_stocked():
    menu = add_c6_consumer_options({"needs": [], "options": []}, C6, FACTS, [SHORT], {})
    assert menu["options"] == []
    menu = add_c6_consumer_options({"needs": [], "options": []}, C6, FACTS, [SHORT],
                                   {"assembling-machine-2": 4, "passive-provider-chest": 11})
    assert any(o["option_id"] == "op.production_service.productivity-module" for o in menu["options"])
