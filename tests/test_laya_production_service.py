# Path: tests/test_laya_production_service.py | Purpose: Protect the PM line recipe, the C6/S1 service option and its sizing.

from planners.recipe_data import LINE_RECIPES
from orchestrator.operation_session import PRODUCING_OPERATIONS
from training.laya_clean_runner import Settings
from training.laya_ops_runner import add_c6_consumer_options

FACTS = {"recipe_observations": {"productivity-module": {"enabled": True, "products": [{"name": "productivity-module"}]}}}


def test_productivity_module_has_a_line_recipe():
    spec = LINE_RECIPES["productivity-module"]
    assert spec["machine"] == "assembling-machine-2"
    assert spec["ingredients"] == ["advanced-circuit", "electronic-circuit"]
    assert spec["amounts"] == [5, 5] and spec["craft_time"] == 15.0


def test_the_service_is_enabled_on_this_branch_and_a_producing_operation():
    assert Settings().production_service is True
    assert "production_service" in PRODUCING_OPERATIONS


def test_c6_and_s1_offer_the_sized_consumer_service_once():
    for phase in ({"phase_id": "C6"}, {"phase_id": "S1"}):
        menu = add_c6_consumer_options({"needs": [], "options": []}, phase, FACTS)
        option = next(o for o in menu["options"] if o["option_id"] == "op.production_service.productivity-module")
        assert option["params"] == {"item": "productivity-module", "stock_target": 10, "minimum_machines": 2}
        assert add_c6_consumer_options(menu, phase, FACTS)["options"].count(option) == 1


def test_no_service_before_c6():
    assert add_c6_consumer_options({"needs": [], "options": []}, {"phase_id": "C5"}, FACTS)["options"] == []
