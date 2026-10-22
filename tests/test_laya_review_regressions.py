# Path: tests/test_laya_review_regressions.py | Purpose: Protect measured plateaus, in-flight loan inputs and scheduler persistence from the October 22 review.
from types import SimpleNamespace

import pytest

from orchestrator.operation_observation import item_service_observation
from tools.laya_host_scheduler import DEFAULTS, choose


def test_stocked_plateau_and_renewed_demand_remain_different_observations():
    before = {"surface": "nauvis", "force": "player", "mods": {"base": "2.1.23"}, "tick": 600,
              "production": {"plastic-bar": 100}, "consumption": {"plastic-bar": 0},
              "networks": {"1": {"items": {"plastic-bar": 4800}}}}
    plateau = dict(before, tick=1200)
    evidence = item_service_observation(plateau, before, "plastic-bar")
    assert evidence["status"] == "stocked_plateau" and evidence["production_rate_per_second"] == 0
    assert evidence["sustained_service"] is None
    renewed = dict(plateau, tick=1800, production={"plastic-bar": 120}, consumption={"plastic-bar": 15})
    evidence = item_service_observation(renewed, plateau, "plastic-bar")
    assert evidence["status"] == "producing"
    assert evidence["production_rate_per_second"] == 2 and evidence["consumption_rate_per_second"] == 1.5
    assert item_service_observation(dict(renewed, force="other"), plateau, "plastic-bar")["status"] == "unknown"
    assert item_service_observation(before, renewed, "plastic-bar")["status"] == "unknown"


def _metrics(**kwargs):
    return {"active_slots": 20, "queued": 100, "ups_samples": 20, "ups_p95": 110, "ups_p98": 105,
            "total_ups": 2200, "temp_c": 90, "available_gb": 10, "unstable": [],
            "power_profile": "performance", **kwargs}


def test_transient_heat_does_not_drain_but_sustained_heat_does_and_hard_heat_bypasses_settling():
    config = dict(DEFAULTS, step=1, temp_limit_c=93, temp_hard_c=97, window_seconds=60)
    state = {"target": 20, "mode": "performance"}
    for now in (100, 160):
        target, reason, state = choose(config, state, _metrics(temp_c=94), now=now)
        assert (target, reason) == (20, "thermal_pending")
    target, reason, state = choose(config, state, _metrics(temp_c=94), now=220)
    assert (target, reason) == (19, "thermal")
    assert choose(config, state, _metrics(temp_c=94, active_slots=19), now=280)[:2] == (19, "settling")
    assert choose(config, state, _metrics(temp_c=98, active_slots=19), now=281)[:2] == (17, "thermal_hard")


def test_a_slow_world_does_not_create_a_per_minute_drain_cascade():
    config = dict(DEFAULTS, step=1, window_seconds=60)
    state = {"target": 20, "mode": "performance"}
    assert choose(config, state, _metrics(ups_p95=90), now=100)[:2] == (20, "unhealthy_pending")
    assert choose(config, state, _metrics(ups_p95=90), now=160)[:2] == (19, "unhealthy")
    for now in (220, 280, 340):
        assert choose(config, state, _metrics(ups_p95=90, active_slots=19), now=now)[:2] == (19, "settling")
    # Critical memory pressure still lowers admission immediately during that hold.
    assert choose(config, state, _metrics(active_slots=19, available_gb=1), now=341)[:2] == (18, "memory")
