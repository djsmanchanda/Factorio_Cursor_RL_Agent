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
    # Merge note (O1, 2026-11-15): the harness scheduler (4818e4a7, later than this Oct 22 test)
    # lets a sustained UPS breach drain through settling (test_ceiling_is_not_a_target_and_ups_still_drains).
    # The protection kept here: at most one step per `unhealthy_samples` (2) windows, never per minute.
    assert choose(config, state, _metrics(ups_p95=90, active_slots=19), now=220)[:2] == (19, "unhealthy_pending")
    assert choose(config, state, _metrics(ups_p95=90, active_slots=19), now=280)[:2] == (18, "unhealthy")
    assert choose(config, state, _metrics(ups_p95=90, active_slots=18), now=340)[:2] == (18, "unhealthy_pending")
    # Critical memory pressure still lowers admission immediately.
    assert choose(config, state, _metrics(active_slots=18, available_gb=1), now=341)[:2] == (17, "memory")


@pytest.mark.parametrize("launcher", ["run_laya_mixed", "run_laya_clean"])
def test_failed_saved_world_prepare_leaves_its_cause_in_the_job_log(launcher, tmp_path, monkeypatch, capfd):
    # ab-60 lost the cause of two prepare failures because the child's stderr was captured and dropped.
    import importlib
    import subprocess
    module = importlib.import_module(f"tools.{launcher}")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/run_laya_saved_world.py").write_text(
        "import sys\nprint('partial result')\nprint('prepare failed: rcon never ready', file=sys.stderr)\nsys.exit(1)\n")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        module.saved_world("prepare", tmp_path / "world")
    assert "prepare failed: rcon never ready" in capfd.readouterr().err
