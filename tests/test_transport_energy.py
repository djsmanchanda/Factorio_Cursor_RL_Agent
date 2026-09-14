# Path: tests/test_transport_energy.py | Purpose: Keep optional line promotion tied to finite electricity evidence and mandatory capacity.
import json

import pytest

from orchestrator.transport_energy import (
    TransportEnergy, estimate_transport_energy, observe_transport_energy,
    prefer_direct_transport,
)


def observations(distance=100):
    return dict(joules_per_tile=20, joules_per_tick=5, tiles_per_second=3,
                payload=4, machine_watts=150000, inserter_watts=13000,
                distances={"iron-plate": distance})


def test_small_nearby_demand_keeps_requesters_but_long_heavy_haul_prefers_belt():
    near = estimate_transport_energy(observations(10), ingredient_rates={"iron-plate": 1},
                                     added_machines=1, proposed_machines=2)
    far = estimate_transport_energy(observations(1000), ingredient_rates={"iron-plate": 10},
                                    added_machines=1, proposed_machines=2)
    assert prefer_direct_transport(near, demand=1, existing_capacity=12) is False
    assert prefer_direct_transport(far, demand=10, existing_capacity=12) is True


def test_transport_includes_return_trip_research_speed_and_payload():
    first = observations()
    value = estimate_transport_energy(first, ingredient_rates={"iron-plate": 4},
                                      added_machines=0, proposed_machines=1)
    assert value.robot_watts == 24000
    assert value.added_line_watts == 26000
    faster = {**first, "tiles_per_second": 6, "payload": 8}
    assert estimate_transport_energy(faster, ingredient_rates={"iron-plate": 4},
                                     added_machines=0, proposed_machines=1).robot_watts == 7000


def test_low_bot_cost_cannot_veto_capacity_shortage_and_unknown_keeps_fallback():
    assert prefer_direct_transport(TransportEnergy(1, 999999), demand=13, existing_capacity=12) is True
    assert prefer_direct_transport(None, demand=1, existing_capacity=12) is None
    assert prefer_direct_transport(TransportEnergy(1, 100), demand=0, existing_capacity=12) is None


@pytest.mark.parametrize("update", [{"payload": 0}, {"joules_per_tile": float('nan')},
                                   {"distances": {}}, {"machine_watts": None}])
def test_incomplete_or_invalid_observation_never_becomes_free_energy(update):
    assert estimate_transport_energy({**observations(), **update}, ingredient_rates={"iron-plate": 1},
                                     added_machines=1, proposed_machines=2) is None


def test_observer_consumes_report_and_unknown_report_keeps_fallback():
    class Client:
        def command(self, query):
            self.query = query
            return json.dumps(observations())
    client = Client()
    kwargs = dict(machine="assembling-machine-2", destination=(1.5, 4.5),
                  ingredient_rates={"iron-plate": 4}, added_machines=0, proposed_machines=1)
    result = observe_transport_energy(client, "nauvis", "player", **kwargs)
    assert result.robot_watts == 24000
    client.command = lambda _: '{}'
    assert observe_transport_energy(client, "nauvis", "player", **kwargs) is None


def test_energy_rejects_nonfinite_evidence():
    with pytest.raises(ValueError):
        TransportEnergy(float('inf'), 3)
