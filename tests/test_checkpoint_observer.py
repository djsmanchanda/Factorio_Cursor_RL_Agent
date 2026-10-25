# Path: tests/test_checkpoint_observer.py | Purpose: Live structured checkpoint telemetry contracts.
from __future__ import annotations

from types import SimpleNamespace
from dataclasses import asdict
import json
import shutil
import subprocess

import pytest

from orchestrator import live_base
from orchestrator.bootstrap_district import BootstrapDistrictLedger, REQUIRED_RESERVATION_ROLES
from orchestrator.checkpoint_milestones import FoundationEvidence, evaluate_milestone
from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter, ObservationUnsupported


class _Client:
    def command(self, text: str) -> str:
        if "f.item_production_statistics" in text:
            if "plastic-bar" in text:
                return "5"
            if "advanced-circuit" in text:
                return "7"
        if "electric-furnace" in text:
            return "1|101|working|2;2|102|working|3;3|103|working|4"
        return ""


def test_live_adapter_builds_structured_snapshot_without_logs(monkeypatch) -> None:
    starter = SimpleNamespace(
        drill_position=(10.0, 10.0), output_direction="north", pole_side=1,
        additional_drill_positions=(),
    )

    def fake_starter(client, surface, force, recipe, ore, near):
        return starter if ore == "iron-ore" else None

    def fake_health(client, surface, positions):
        return ({position: "working" for position in positions}, {position: 2.0 for position in positions})

    def fake_line(client, surface, force, recipe, machine, **kwargs):
        if recipe == "iron-gear-wheel":
            return SimpleNamespace(
                machine_positions=((1.0, 1.0), (2.0, 2.0)), produced_count=9,
            )
        if recipe == "advanced-circuit":
            return SimpleNamespace(machine_positions=((3.0, 3.0),), produced_count=7)
        return None

    monkeypatch.setattr(live_base, "game_tick", lambda client: 120)
    monkeypatch.setattr(live_base, "direct_plate_starter", fake_starter)
    monkeypatch.setattr(live_base, "machine_health", fake_health)
    monkeypatch.setattr(live_base, "find_line", fake_line)
    monkeypatch.setattr(live_base, "available_items", lambda client, surface, force: {"plastic-bar": 2})

    snapshot = LiveCheckpointObservationAdapter(
        _Client(), surface="nauvis", force="player",
    ).observe().snapshot

    assert snapshot.tick == 120
    assert snapshot.base_valid is True
    assert snapshot.starters["iron"].present is True
    assert snapshot.starters["copper"].present is False
    assert snapshot.production["plastic-bar"].produced == 0
    assert snapshot.providers["plastic-bar"].available == 2
    assert snapshot.providers["plastic-bar"].delivered is None
    assert snapshot.recipes["processing-unit"].owned is False


def _district(ledger, recipe, ore, positions):
    ledger.record_pioneer(recipe, ore, [{
        "action_type": "place_ghost", "entity": "electric-furnace",
        "position": {"x": -10, "y": -10},
    }])
    return ledger.provision(
        recipe, reservations={role: frozenset({(i, 0)}) for i, role in enumerate(REQUIRED_RESERVATION_ROLES)},
        replacement_origin=positions[0], replacement_provider=(0, 30),
        replacement_furnaces=len(positions), replacement_actions=[{
            "action_type": "place_ghost", "entity": "electric-furnace",
            "position": {"x": x, "y": y},
        } for x, y in positions] + [{
            "action_type": "place_ghost", "entity": "passive-provider-chest",
            "position": {"x": 0, "y": 30},
        }],
    )


@pytest.fixture
def ledger(tmp_path):
    return BootstrapDistrictLedger(
        tmp_path / "script-output", episode_id="episode-1", surface="nauvis",
        force="player", bootstrap_profile="reduced-v1",
    )


def test_live_adapter_keeps_foundation_counts_and_output_structured(monkeypatch, ledger) -> None:
    monkeypatch.setattr(live_base, "game_tick", lambda client: 1)
    monkeypatch.setattr(live_base, "direct_plate_starter", lambda *args: None)
    monkeypatch.setattr(live_base, "find_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_base, "available_items", lambda *args: {})
    for i, (name, recipe, ore) in enumerate(LiveCheckpointObservationAdapter._STARTERS):
        _district(ledger, recipe, ore, [(i, 0)])
        ledger.mark_validating(recipe, measured_output_count=1)
        ledger.mark_retiring(recipe)
        ledger.mark_released(recipe)
    bridge = SimpleNamespace(episode_id=ledger.episode_id, script_output=ledger.root.parents[2] / "script-output")
    observation = LiveCheckpointObservationAdapter(_Client(), bridge).observe()
    snapshot = observation.snapshot

    assert snapshot.starters["iron"].present is False
    assert snapshot.foundations["iron"].machine_count == 1
    assert snapshot.foundations["iron"].output_count == 2
    assert snapshot.foundations["copper"].machine_count == 1
    assert snapshot.foundations["stone"].machine_count == 1
    assert observation.raw["foundations"]["stone"]["machines"][0]["unit_number"] == 103
    # Laya journals serialize the normalized dataclass, not only raw telemetry.
    evidence = asdict(snapshot.foundations["stone"])
    assert evidence["machines"][0]["unit_number"] == 103
    assert evidence["counter_basis"] == "live-entity-products-finished"
    receipt = evaluate_milestone("C3", snapshot).as_mapping()
    assert receipt["evidence"]["foundations"]["stone"] == evidence


def test_partial_starter_retirement_is_not_reported_absent(tmp_path, monkeypatch) -> None:
    script_output = tmp_path / "script-output"
    script_output.mkdir()
    ledger = BootstrapDistrictLedger(
        script_output, episode_id="episode-1", surface="nauvis", force="player",
        bootstrap_profile="reduced-v1",
    )
    ledger.record_pioneer("iron-plate", "iron-ore", [{
        "action_type": "place_ghost", "entity": "electric-mining-drill",
        "position": {"x": 1, "y": 2},
    }])
    monkeypatch.setattr(live_base, "game_tick", lambda client: 1)
    monkeypatch.setattr(live_base, "direct_plate_starter", lambda *args: None)
    monkeypatch.setattr(live_base, "find_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_base, "available_items", lambda *args: {})
    bridge = SimpleNamespace(episode_id="episode-1", script_output=script_output)

    snapshot = LiveCheckpointObservationAdapter(_Client(), bridge=bridge).observe().snapshot

    assert snapshot.starters["iron"].present is True
    assert snapshot.starters["copper"].present is False


def _lua_value(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ",".join(f"[{json.dumps(k)}]={_lua_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "{" + ",".join(_lua_value(v) for v in value) + "}"
    return json.dumps(value)


class _LuaClient:
    """Execute the actual survey against a small Factorio entity fixture."""

    def __init__(self, entities):
        self.entities = entities

    def command(self, text):
        lua = shutil.which("lua")
        if lua is None:
            pytest.skip("Lua is required for executable survey regressions")
        setup = "local entities=" + _lua_value(self.entities) + "\n" + """
            defines={entity_status={working=1,no_ingredients=2,no_power=3}}
            for _,e in ipairs(entities) do
                e.to_be_deconstructed=function() return e.deconstruct or false end
                e.get_inventory=function() error('census must not depend on inventory') end
            end
            local surface={find_entities_filtered=function(filter)
                -- Factorio 2.1 rejects a position table with extra fields
                -- (live C31: 'position: too many arguments').
                if filter.position[3] ~= nil then error('position: too many arguments') end
                local out={}
                for _,e in ipairs(entities) do
                    if e.surface=='nauvis' and e.force==filter.force and e.name==filter.name
                       and math.abs(e.position.x-filter.position[1])<=filter.radius
                       and math.abs(e.position.y-filter.position[2])<=filter.radius then
                        out[#out+1]=e
                    end
                end
                return out
            end}
            game={surfaces={nauvis=surface},forces={player='player'}}
            rcon={print=print}
        """
        return subprocess.run(
            [lua, "-"], input=setup + text.removeprefix("/sc "), text=True,
            capture_output=True, check=True, timeout=5,
        ).stdout


def _furnaces():
    return [{
        "name": "electric-furnace", "type": "furnace", "force": "player", "surface": "nauvis",
        "position": {"x": i * 3 + 0.5, "y": 0.5}, "unit_number": i + 1,
        "status": 1, "products_finished": (i + 1) * 10,
        "input": "stone", "output": "stone-brick",
    } for i in range(6)]


def _stone_district(ledger, entities):
    state = _district(ledger, "stone-brick", "stone", [
        (e["position"]["x"], e["position"]["y"]) for e in entities
    ])
    return {state.recipe: state}


@pytest.mark.lua
def test_owned_empty_furnaces_keep_counts_and_counters_across_observer_restart(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    before = LiveCheckpointObservationAdapter()._foundation_survey(_LuaClient(entities), districts)["stone"]
    for entity in entities:
        entity.update(input="", output="", status=2)
    # A new adapter and ledger load have no in-memory attribution cache.
    after = LiveCheckpointObservationAdapter()._foundation_survey(
        _LuaClient(entities), {s.recipe: s for s in ledger.states()},
    )["stone"]
    assert before["machine_count"] == after["machine_count"] == 6
    assert before["output_count"] == after["output_count"] == 210
    assert [m["unit_number"] for m in before["machines"]] == [m["unit_number"] for m in after["machines"]]
    assert before["working_count"] == 6 and after["working_count"] == 0


@pytest.mark.lua
def test_missing_ghost_and_foreign_furnaces_cannot_fill_owned_sites(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    removed = entities.pop()
    entities.extend([
        {**removed, "name": "entity-ghost", "type": "entity-ghost", "unit_number": 20},
        {**removed, "force": "enemy", "unit_number": 21},
        {**removed, "surface": "other", "unit_number": 22},
        {**removed, "position": {"x": removed["position"]["x"] + 0.2, "y": 0.5}, "unit_number": 23},
        {**removed, "position": {"x": -10, "y": -10}, "unit_number": 24},
    ])
    adapter = LiveCheckpointObservationAdapter()
    before = adapter._foundation_survey(_LuaClient(entities), districts)["stone"]
    assert before["machine_count"] == 5
    assert before["output_count"] == 150
    assert evaluate_milestone("C3", {
        "foundations": {"stone": before}, "starters": {"stone": {"present": False}},
    }).failed
    entities.append({**removed, "unit_number": 99, "products_finished": 0})
    after = adapter._foundation_survey(_LuaClient(entities), districts)["stone"]
    assert after["machine_count"] == 6 and after["output_count"] == 150
    assert {m["unit_number"] for m in after["machines"]} == {1, 2, 3, 4, 5, 99}


@pytest.mark.lua
def test_uncrafted_and_deconstructing_furnaces_count_without_proving_readiness(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    for entity in entities:
        entity.update(input="", output="", products_finished=0, status=2)
    adapter = LiveCheckpointObservationAdapter()
    result = adapter._foundation_survey(_LuaClient(entities), districts)["stone"]
    assert result["machine_count"] == 6 and result["output_count"] == 0
    assert result["producing"] is False
    for entity in entities:
        entity.update(products_finished=10, deconstruct=True)
    result = adapter._foundation_survey(_LuaClient(entities), districts)["stone"]
    assert result["machine_count"] == 6 and result["healthy"] is False
    assert evaluate_milestone("C3", {
        "foundations": {"stone": result}, "starters": {"stone": {"present": False}},
    }).failed


def test_missing_ownership_is_unknown_and_empty_ledger_is_measured_absence():
    adapter = LiveCheckpointObservationAdapter()
    unknown = adapter._foundation_survey(None, None)
    assert unknown["stone"]["machine_count"] is None
    assert evaluate_milestone("C3", {
        "foundations": unknown, "starters": {"stone": {"present": False}},
    }).unknown
    absent = adapter._foundation_survey(None, {})
    assert absent["stone"]["machine_count"] == 0
    assert absent["stone"]["identity_source"] == "no-district"
    assert unknown["stone"]["counter_basis"] is None


def test_absent_ledger_directory_stays_unknown_on_resume(monkeypatch, ledger):
    monkeypatch.setattr(live_base, "game_tick", lambda client: 1)
    monkeypatch.setattr(live_base, "direct_plate_starter", lambda *args: None)
    monkeypatch.setattr(live_base, "find_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_base, "available_items", lambda *args: {})
    bridge = SimpleNamespace(episode_id=ledger.episode_id, script_output=ledger.root.parents[2] / "script-output")
    adapter = LiveCheckpointObservationAdapter(_Client(), bridge)
    assert not ledger.root.exists()
    snapshot = adapter.observe().snapshot
    assert snapshot.foundations["stone"].machine_count is None
    assert evaluate_milestone("C3", snapshot).unknown
    ledger.root.mkdir(parents=True)
    snapshot = adapter.observe().snapshot
    assert snapshot.foundations["stone"].machine_count == 0
    assert evaluate_milestone("C3", snapshot).failed


def test_legacy_foundation_counts_still_normalize_without_entity_rows():
    evidence = FoundationEvidence.from_mapping("stone", {"machines": 6, "output_count": 10})
    assert evidence.machine_count == 6 and evidence.output_count == 10
    assert evidence.machines == ()


@pytest.mark.parametrize("last_row,count,passed", [
    ("6|106|no_ingredients|10", 6, True),
    ("6||missing|", 5, False),
])
def test_foundation_receipt_uses_live_entities_not_planned_count(ledger, last_row, count, passed):
    districts = _stone_district(ledger, _furnaces())
    raw = ";".join([f"{i}|{100+i}|no_ingredients|10" for i in range(1, 6)] + [last_row])
    foundations = LiveCheckpointObservationAdapter()._foundation_survey(
        SimpleNamespace(command=lambda _: raw), districts,
    )
    assert foundations["stone"]["machine_count"] == count
    assert foundations["stone"]["output_count"] == count * 10
    assert foundations["stone"]["working_count"] == 0
    result = evaluate_milestone("C3", {
        "foundations": foundations, "starters": {"stone": {"present": False}},
    })
    assert result.passed is passed


@pytest.mark.parametrize("raw", ["", "1|1|working|1", "1|1|working|1;1|2|working|2", "1|1|working|1;2|1|working|2"])
def test_truncated_or_duplicate_foundation_telemetry_is_unsupported(ledger, raw):
    districts = _stone_district(ledger, _furnaces()[:2])
    client = SimpleNamespace(command=lambda _: raw)
    with pytest.raises(ObservationUnsupported, match="malformed"):
        LiveCheckpointObservationAdapter()._foundation_survey(client, districts)


@pytest.mark.lua
def test_unavailable_entity_counter_stays_unknown(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    entities[-1]["products_finished"] = None
    result = LiveCheckpointObservationAdapter()._foundation_survey(_LuaClient(entities), districts)
    assert result["stone"]["machine_count"] == 6
    assert result["stone"]["output_count"] is None
    assert evaluate_milestone("C3", {
        "foundations": result, "starters": {"stone": {"present": False}},
    }).unknown


@pytest.mark.lua
def test_neighboring_district_cannot_supply_missing_stone_furnaces(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    copper = _district(ledger, "copper-plate", "copper-ore", [(30.5, 0.5)])
    districts[copper.recipe] = copper
    entities[-1] = {**entities[-1], "position": {"x": 30.5, "y": 0.5}, "input": "", "output": ""}
    result = LiveCheckpointObservationAdapter()._foundation_survey(_LuaClient(entities), districts)
    assert result["stone"]["machine_count"] == 5
    assert result["copper"]["machine_count"] == 1
    assert result["copper"]["output_count"] == 60


def test_overlapping_district_ownership_is_unsupported(ledger):
    entities = _furnaces()
    districts = _stone_district(ledger, entities)
    copper = _district(ledger, "copper-plate", "copper-ore", [(0.5, 0.5)])
    districts[copper.recipe] = copper
    with pytest.raises(ObservationUnsupported, match="duplicate furnace sites"):
        LiveCheckpointObservationAdapter()._foundation_survey(None, districts)


# Exact live C31 response: the survey Lua passed {x, y, name} as a position.
_LIVE_POSITION_ERROR = "Cannot execute command. Error: position: too many arguments."


def test_failed_census_leaves_foundations_unknown_with_the_raw_response(ledger):
    districts = _stone_district(ledger, _furnaces()[:2])
    client = SimpleNamespace(command=lambda _: _LIVE_POSITION_ERROR)
    foundations = LiveCheckpointObservationAdapter()._foundations_or_unknown(client, districts)
    stone = FoundationEvidence.from_mapping("stone", foundations["stone"])
    assert stone.machine_count is None and stone.identity_source == "census-failed"
    assert "too many arguments" in stone.census_error
    result = evaluate_milestone("C3", {"foundations": foundations, "starters": {"stone": {"present": False}}})
    assert result.status.value == "unknown"


@pytest.mark.parametrize("runtime,interface", [("deterministic", "factorio_cursor_rl_agent"),
                                                 ("laya", "factorio_laya_agent")])
def test_startup_sampler_uses_runtime_interface_and_scope(runtime, interface):
    from orchestrator.game_bridge import GameBridge
    from tools.runtime_namespace import resolve
    bridge = GameBridge.__new__(GameBridge)
    bridge._namespace = resolve(runtime)
    bridge.episode_id = "logical-episode"
    watch = {"surface": "nauvis", "force": "player", "run_id": bridge.episode_id, "samples": {}}
    commands = []
    client = SimpleNamespace(command=lambda command: (commands.append(command), json.dumps(watch))[1])
    observer = LiveCheckpointObservationAdapter(client, bridge)
    assert observer._plastic_startup(client, arm=True)["samples"] == []
    assert f'remote.call("{interface}","production_startup_arm"' in commands[-1]
    assert observer._plastic_startup(client)["run_id"] == bridge.episode_id
    assert f'remote.call("{interface}","production_startup_read"' in commands[-1]
    watch["run_id"] = "different-episode"
    assert observer._plastic_startup(client) is None


def test_missing_startup_sampler_does_not_invent_a_late_window():
    from tools.rcon_client import RconError
    def unavailable(command):
        assert "production_startup_read" in command and "production_startup_arm" not in command
        raise RconError("interface unavailable")
    bridge = SimpleNamespace(episode_id="resumed-episode", remote_interface="factorio_laya_agent")
    client = SimpleNamespace(command=unavailable)
    assert LiveCheckpointObservationAdapter(client, bridge)._plastic_startup(client) is None



@pytest.mark.lua
def test_missing_watch_command_prints_null_without_serializing_lua_nil():
    lua = shutil.which("lua") or shutil.which("lua5.4")
    if lua is None:
        pytest.skip("Lua is required for the sampler command wrapper")
    commands = []
    client = SimpleNamespace(command=lambda text: (commands.append(text), "null")[1])
    bridge = SimpleNamespace(episode_id="legacy", remote_interface="factorio_laya_agent")
    assert LiveCheckpointObservationAdapter(client, bridge)._plastic_startup(client) is None
    setup = """
      remote={interfaces={factorio_laya_agent={production_startup_read=true}},call=function() return nil end}
      helpers={table_to_json=function(value) error('nil must not be serialized') end}
      rcon={print=print}
    """
    result = subprocess.run([lua, "-"], input=setup+commands[0].removeprefix("/sc "),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "null"
