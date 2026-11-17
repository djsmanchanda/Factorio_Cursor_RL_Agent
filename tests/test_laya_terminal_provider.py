# Path: tests/test_laya_terminal_provider.py | Purpose: Protect terminal-only provider publishing for permanent cells.

from orchestrator.material_reservations import plan_material_bill
from orchestrator.stage_transport import _publish_output_chest
from planners.local_layout_planner import LocalLayoutPlanner


def _plan():
    return LocalLayoutPlanner().generate_line_layout(
        "copper-cable", 2, 0, 0, belt_type="transport-belt",
        inserter_type="inserter", feed_style="chest", terminal_collector=True,
    )


def test_terminal_only_publishes_one_provider_and_keeps_throughput_steel():
    plan = _plan()
    _publish_output_chest(plan, limit=1)
    bill = plan_material_bill(plan)
    assert bill.get("passive-provider-chest") == 1
    assert bill.get("steel-chest") == 7


def test_the_default_still_publishes_every_collector():
    plan = _plan()
    _publish_output_chest(plan)
    bill = plan_material_bill(plan)
    assert bill.get("passive-provider-chest") == 8
    assert bill.get("steel-chest") is None
