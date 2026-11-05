# Path: tests/test_laya_production_service.py | Purpose: Protect the C6 production-service consumer option and its sizing.

from orchestrator.operation_session import PRODUCING_OPERATIONS
from training.laya_clean_runner import Settings
from training.laya_ops_runner import add_c6_consumer_options

MENU = {"needs": [], "options": []}
C6 = {"phase_id": "C6"}
FACTS = {"recipe_observations": {"productivity-module": {"enabled": True, "products": [{"name": "productivity-module"}]}}}


def test_the_service_is_off_by_default_and_a_producing_operation():
    assert Settings().production_service is False
    assert "production_service" in PRODUCING_OPERATIONS


def test_c6_offers_the_sized_consumer_service_once():
    menu = add_c6_consumer_options({"needs": [], "options": []}, C6, FACTS)
    option = next(o for o in menu["options"] if o["option_id"] == "op.production_service.productivity-module")
    assert option["params"] == {"item": "productivity-module", "stock_target": 10, "minimum_machines": 2}
    assert option["features"]["goal_need"] is True
    assert add_c6_consumer_options(menu, C6, FACTS)["options"].count(option) == 1


def test_no_service_before_c6_or_without_an_enabled_recipe():
    assert add_c6_consumer_options({"needs": [], "options": []}, {"phase_id": "C5"}, FACTS)["options"] == []
    facts = {"recipe_observations": {"productivity-module": {"enabled": False}}}
    assert add_c6_consumer_options({"needs": [], "options": []}, C6, facts)["options"] == []
