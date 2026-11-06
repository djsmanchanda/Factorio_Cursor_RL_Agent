# Path: tests/test_laya_service_hold.py | Purpose: Protect the construction-bill hold and its progress release.

from training.laya_ops_runner import add_cable_capacity_option, service_construction_blocks

PHASE = {"phase_id": "C6"}
BOARD = {"needs": [{"item": "advanced-circuit", "condition": "input_starved"}]}
FACTS = {"recipes": {"copper-cable": {}}}
SHORT = {"ok": False, "operation": "production_service", "code": "material_shortage",
         "params": {"item": "copper-cable", "stock_target": 40, "minimum_machines": 6,
                    "upgrade_bootstrap": True, "allow_promotion": True},
         "details": {"required": {"assembling-machine-1": 6, "passive-provider-chest": 3},
                     "available": {"assembling-machine-1": 2, "passive-provider-chest": 1}}}


def test_a_blocked_service_is_held_until_the_bill_progresses():
    assert service_construction_blocks([SHORT], {}) == {"copper-cable": {
        "assembling-machine-1": 4, "passive-provider-chest": 2}}
    assert service_construction_blocks([SHORT], {"assembling-machine-1": 3,
                                                 "passive-provider-chest": 1}) == {}
    assert service_construction_blocks([SHORT], {"assembling-machine-1": 6,
                                                 "passive-provider-chest": 3}) == {}


def test_the_blocked_cable_option_is_held_and_returns_on_progress():
    assert add_cable_capacity_option({"needs": [], "options": []}, BOARD, PHASE, FACTS, [SHORT], {})["options"] == []
    menu = add_cable_capacity_option({"needs": [], "options": []}, BOARD, PHASE, FACTS, [SHORT],
                                     {"assembling-machine-1": 3, "passive-provider-chest": 1})
    assert any(o["option_id"] == "op.production_service.copper-cable" for o in menu["options"])
