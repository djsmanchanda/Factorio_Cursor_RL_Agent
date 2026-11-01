# Path: tests/test_optimizer_research_capture.py | Purpose: Replay paused research catalog capture without Factorio.

import copy
import hashlib
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from core.catalog_demand import CatalogDemandError, CatalogDemandReason
from tests.test_catalog_demand import _catalog, _recipe
from tests.test_research_path_demand import catalog
from tools.optimizer_research_capture import (QUERY_SOURCE, ResearchCaptureError,
                                              capture_paused_research_supply)
from tools import optimizer_research_capture as module
from training.laya_saved_world import SavedWorldAdapter


@pytest.fixture
def setup_capture(tmp_path, monkeypatch):
    adapter = object.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    adapter.lua_source = QUERY_SOURCE.read_text()
    adapter.code_hashes = {"training/laya_saved_world.lua": hashlib.sha256(adapter.lua_source.encode()).hexdigest()}
    runtime = catalog()
    recipes = _catalog(_recipe("red", [("ore", 2)], [("red", 1)]), raw=("ore",), tick=12)
    recipes.update(version="1.3.0", active_mods={"base": "2.1.21"}, active_mods_count=1)
    facts = {"surface": "nauvis", "force": "player", "tick": 12, "mods": {"base": "2.1.21"}}
    snapshots = [copy.deepcopy(facts), copy.deepcopy(facts)]
    calls = []
    monkeypatch.setattr(SavedWorldAdapter, "query", lambda self: copy.deepcopy(snapshots.pop(0)))
    monkeypatch.setattr(SavedWorldAdapter, "_check_instance", lambda self: calls.append("identity"))
    def runtime_capture(surface, force):
        calls.append(("runtime", surface, force))
        return json.dumps(runtime).encode()
    def recipe_capture(force):
        calls.append(("recipe", force))
        return json.dumps(recipes).encode()
    adapter.bridge = SimpleNamespace(export_runtime_catalog=runtime_capture, export_recipe_catalog_capture=recipe_capture)
    return adapter, runtime, recipes, snapshots, calls


def capture(adapter):
    return capture_paused_research_supply(adapter, targets=["goal"], horizon_ticks=2100, game_version="2.1.21",
                                          boundaries=[{"name": "ore", "cap": 3}])


def test_same_tick_captures_flow_into_exact_balance(setup_capture):
    adapter, _, _, _, calls = setup_capture
    result = capture(adapter)
    assert result["tick"] == 12
    assert result["research_path"]["known_gross_science_bill"] == {"red": "35"}
    assert dict(result["demand"].demand)["red"] == 1
    assert result["demand"].balance.feasible is True
    assert result["runtime_catalog_sha256"] == hashlib.sha256(result["runtime_catalog"]).hexdigest()
    assert result["recipe_provenance"]["catalog_sha256"] == hashlib.sha256(result["recipe_catalog"]).hexdigest()
    assert result["dispatch_authorized"] is False
    assert calls == ["identity", ("runtime", "nauvis", "player"), "identity", ("recipe", "player")]


@pytest.mark.parametrize("mutation", ["before_tick", "after_tick", "after_mods", "runtime_tick", "recipe_tick",
                                      "recipe_mods", "recipe_force", "graph"])
def test_inconsistent_capture_cannot_become_a_plan(setup_capture, mutation):
    adapter, runtime, recipes, snapshots, _ = setup_capture
    if mutation == "before_tick": snapshots[0]["tick"] = True
    elif mutation == "after_tick": snapshots[1]["tick"] += 1
    elif mutation == "after_mods": snapshots[1]["mods"]["base"] = "different"
    elif mutation == "runtime_tick": runtime["tick"] += 1
    elif mutation == "recipe_tick": recipes["tick"] += 1
    elif mutation == "recipe_mods": recipes["active_mods"]["base"] = "different"
    elif mutation == "recipe_force": recipes["force"] = "other"
    else: runtime["technology_graph"]["complete"] = False
    with pytest.raises(ValueError):
        capture(adapter)


def test_locked_recipe_is_not_treated_as_currently_available(setup_capture):
    adapter, _, recipes, _, _ = setup_capture
    recipes["recipes"][0]["enabled"] = False
    with pytest.raises(CatalogDemandError) as error:
        capture(adapter)
    assert error.value.reason == CatalogDemandReason.DISABLED_PRODUCER


@pytest.mark.parametrize("mutation", ["source", "hash", "game_version", "subclass"])
def test_invalid_adapter_or_arguments_fail_before_capturing(setup_capture, mutation):
    adapter, _, _, _, calls = setup_capture
    game_version = "2.1.21"
    if mutation == "source": adapter.lua_source += "\n"
    elif mutation == "hash": adapter.code_hashes["training/laya_saved_world.lua"] = "0" * 64
    elif mutation == "game_version": game_version = "unknown"
    else:
        class Unpaused(SavedWorldAdapter):
            pass
        altered = object.__new__(Unpaused)
        altered.__dict__.update(adapter.__dict__)
        adapter = altered
    with pytest.raises(ResearchCaptureError):
        capture_paused_research_supply(adapter, targets=["goal"], horizon_ticks=2100,
                                       game_version=game_version, boundaries=[{"name": "ore", "cap": 3}])
    assert calls == []


def test_cli_help_is_available_without_a_server():
    result = subprocess.run([str(Path(".venv/bin/python")), str(module.ROOT / "tools/optimizer_research_capture.py"),
                             "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--horizon-ticks" in result.stdout


def test_cli_closes_adapter_and_prints_a_serializable_summary(setup_capture, monkeypatch, capsys):
    adapter, _, _, _, _ = setup_capture
    result = capture(adapter)
    closed = []
    monkeypatch.setattr(module, "SavedWorldAdapter", lambda _root: SimpleNamespace(close=lambda: closed.append(True)))
    monkeypatch.setattr(module, "capture_paused_research_supply", lambda *_args, **_kwargs: result)
    code = module.main(["--root", "unused", "--target", "goal", "--horizon-ticks", "2100",
                        "--game-version", "2.1.21"])
    output = json.loads(capsys.readouterr().out)
    assert code == 0 and closed == [True]
    assert output["demand"]["balance"]["feasible"] is True
    assert "runtime_catalog" not in output and "recipe_catalog" not in output
