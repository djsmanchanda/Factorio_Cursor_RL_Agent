# Path: tests/test_chemical_packets.py
# Purpose: Protect chemical packet partitioning and its construction metadata.

from copy import deepcopy

from orchestrator.stage_chemical import _split_plan_power


def test_power_partition_preserves_reservations_and_non_entity_actions() -> None:
    pole = {"action_type": "place_ghost", "entity": "medium-electric-pole"}
    machine = {"action_type": "place_ghost", "entity": "chemical-plant"}
    landfill = {"action_type": "place_tile_ghost", "tile": "landfill"}
    wire = {"action_type": "connect_wire"}
    plan = {
        "surface": "nauvis", "force": "player", "reserved_tiles": [[3, 7]],
        "phases": [
            {"name": "mixed", "metadata": {"atomic": True},
             "actions": [landfill, pole, machine, wire]},
            {"name": "empty", "actions": []},
        ],
    }
    original = deepcopy(plan)

    power, construction = _split_plan_power(plan)

    for packet, actions in ((power, [pole]), (construction, [landfill, machine, wire])):
        assert packet == {
            **plan,
            "phases": [{**plan["phases"][0], "actions": actions}],
        }
    assert plan == original
