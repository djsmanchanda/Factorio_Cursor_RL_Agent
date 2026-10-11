# Path: tests/test_machine_input_inventory.py | Purpose: Protect actual assembler input accounting on Factorio 2.1.
from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder, live_base

pytestmark = pytest.mark.fast


class Factorio21InputClient:
    def command(self, lua):
        # 2.1.21 defines crafter_input=2; assembling_machine_input is absent.
        if "get_inventory(defines.inventory.crafter_input)" not in lua:
            return "Error: bad argument to get_inventory (inventory index expected, got nil)"
        assert "type='assembling-machine'" in lua
        return "iron-plate=3,electronic-circuit=4"


def test_machine_inputs_use_factorio21_inventory_and_preserve_real_counts():
    assert live_base.machine_input_contents(Factorio21InputClient(), "nauvis", (1.5, 2.5)) == {
        "iron-plate": 3, "electronic-circuit": 4}


def test_loan_includes_its_machine_inputs_without_borrowing_foreign_wip(monkeypatch):
    monkeypatch.setattr(live_base, "chest_contents", lambda client, surface, position: {"iron-plate": 2})
    loan = SimpleNamespace(requester_position=(4.5, 2.5), machine_position=(1.5, 2.5))
    held = autonomous_builder._loan_owned_inputs(Factorio21InputClient(), "nauvis", loan)
    assert held == {"iron-plate": 5, "electronic-circuit": 4}
