# Path: tests/test_construction_packets.py | Purpose: Protect funded staged construction and restart reconciliation.
import pytest

from planners.construction_packets import release_packet
from training.laya_saved_world import ghost


def test_packet_release_honors_backlog_funding_and_exact_existing_targets():
    phases = [{"name": "coal", "actions": [ghost("transport-belt", x + .5, .5) for x in range(8)]},
              {"name": "pipe", "actions": [ghost("pipe", 10.5, .5)]}]
    targets = [{"entity": a["entity"], "position": a["position"], "state": "missing"}
               for phase in phases for a in phase["actions"]]
    targets[0]["state"], targets[1]["state"] = "built", "ghost"
    network = {"construction_robots": 5, "available_construction_robots": 4,
               "items": {"transport-belt": 5}, "reserved": {"transport-belt": 1}}
    packets, short = release_packet(phases, targets, network)
    assert [a["position"]["x"] for a in packets[0]["actions"]] == [2.5, 3.5, 4.5, 5.5]
    assert short == {"transport-belt": 2, "pipe": 1}
    network["reserved"]["transport-belt"] = 5
    assert release_packet(phases, targets, network)[0] == []
    with pytest.raises(ValueError, match="omitted"):
        release_packet(phases, targets[:-1], network)


def test_low_charge_and_busy_logistics_reduce_release_without_requiring_full_stock():
    actions = [ghost('pipe', x + .5, .5) for x in range(100)]
    phases = [{'name': 'pipeline', 'actions': actions}]
    targets = [{**a, 'state': 'missing'} for a in actions]
    network = {'items': {'pipe': 15}, 'construction_robots': 50, 'available_construction_robots': 50,
               'logistic_robots': 20, 'available_logistic_robots': 10}
    packet, short = release_packet(phases, targets, network, charge_fraction=.2)
    assert len(packet[0]['actions']) == 5
    assert short == {'pipe': 85}
    assert release_packet(phases, targets, network, charge_fraction=0)[0] == []


def test_shared_packet_endpoints_are_billed_once():
    a = ghost('pipe', .5, .5)
    phases = [{'name': 'source', 'actions': [a]}, {'name': 'link', 'actions': [a]}]
    packet, short = release_packet(phases, [{**a, 'state': 'missing'}],
                                   {'items': {}, 'construction_robots': 5})
    assert packet == [] and short == {'pipe': 1}
