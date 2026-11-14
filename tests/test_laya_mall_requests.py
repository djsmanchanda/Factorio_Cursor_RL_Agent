# Path: tests/test_laya_mall_requests.py | Purpose: Verify request ages and single-use stock accounting drive automatic mall work.
import pytest

from training.laya_mall_requests import observe

pytestmark = pytest.mark.fast


def request(identity="chest:1:pipe", item="pipe", missing=81, network="1", **extra):
    return {"request_id": identity, "item": item, "missing": missing, "network": network, **extra}


def world(tick, *requests, items=None, reserved=None):
    return {"tick": tick, "pending_requests": list(requests),
            "networks": {"1": {"items": items or {}, "reserved": reserved or {}}}}


def test_continuous_request_crosses_twenty_seconds_survives_restart_and_quantity_changes(tmp_path):
    assert observe(tmp_path, world(100, request()))["demand"] == {}
    assert observe(tmp_path, world(1299, request(missing=50)))["demand"] == {}
    snapshot = observe(tmp_path, world(1300, request(missing=49)))
    assert snapshot["demand"] == {"pipe": 49}
    assert snapshot["pending"][0]["age_ticks"] == 1200
    assert observe(tmp_path, world(1400, request(missing=1)))["demand"] == {"pipe": 1}


@pytest.mark.parametrize("reset", ["fulfilled", "removed", "replaced", "rewound"])
def test_resolved_replaced_and_rewound_requests_restart_the_clock(tmp_path, reset):
    observe(tmp_path, world(100, request()))
    assert observe(tmp_path, world(1300, request()))["demand"]
    if reset == "fulfilled":
        observe(tmp_path, world(1400, request(missing=0)))
    elif reset == "removed":
        observe(tmp_path, world(1400))
    elif reset == "replaced":
        assert observe(tmp_path, world(1400, request("chest:2:pipe")))["demand"] == {}
        return
    else:
        assert observe(tmp_path, world(10, request()))["demand"] == {}
        return
    assert observe(tmp_path, world(1500, request()))["demand"] == {}


def test_available_stock_is_shared_once_after_construction_and_never_across_networks(tmp_path):
    requests = (request("a", missing=10), request("b", missing=10), request("c", missing=7, network="2"))
    observe(tmp_path, world(0, *requests))
    snapshot = observe(tmp_path, world(1200, *requests, items={"pipe": 30}, reserved={"pipe": 15}))
    assert snapshot["demand"] == {"pipe": 12}  # 5 in network 1 plus 7 isolated from that stock.
    assert [r["production_shortfall"] for r in snapshot["pending"]] == [0, 5, 7]
    assert observe(tmp_path, world(1300, request(missing=5), items={"pipe": 50}))["demand"] == {}


def test_oldest_request_is_scheduled_first_without_faking_quality_support(tmp_path):
    observe(tmp_path, world(0, request("old", item="splitter")))
    observe(tmp_path, world(100, request("old", item="splitter"), request("new", item="pipe"),
                                 request("quality", normal_eligible=False)))
    snapshot = observe(tmp_path, world(1300, request("old", item="splitter"), request("new", item="pipe"),
                                         request("quality", normal_eligible=False)))
    assert snapshot["priority"] == ["splitter", "pipe"]
    assert snapshot["pending"][-1]["status"] == "unsupported_quality"


def test_incoming_pickups_still_in_provider_are_not_spent_twice(tmp_path):
    observe(tmp_path, world(0, request(missing=80)))  # Survey removed 20 assigned deliveries from 100 requested.
    facts = world(1200, request(missing=80), items={"pipe": 20})
    facts["networks"]["1"]["logistic_pickups"] = {"pipe": 20}
    assert observe(tmp_path, facts)["demand"] == {"pipe": 80}
