# Path: tests/test_checkpoint_observer.py | Purpose: Live structured checkpoint telemetry contracts.
from __future__ import annotations

from types import SimpleNamespace

from orchestrator import live_base
from orchestrator.bootstrap_district import BootstrapDistrictLedger
from orchestrator.checkpoint_observer import LiveCheckpointObservationAdapter


class _Client:
    def command(self, text: str) -> str:
        if "f.item_production_statistics" in text:
            if "plastic-bar" in text:
                return "5"
            if "advanced-circuit" in text:
                return "7"
        if "electric-furnace" in text:
            return "iron-ore||working|2;copper-ore||working|3;stone||working|4"
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


def test_live_adapter_keeps_foundation_counts_and_output_structured(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "game_tick", lambda client: 1)
    monkeypatch.setattr(live_base, "direct_plate_starter", lambda *args: None)
    monkeypatch.setattr(live_base, "find_line", lambda *args, **kwargs: None)
    monkeypatch.setattr(live_base, "available_items", lambda *args: {})
    snapshot = LiveCheckpointObservationAdapter(_Client()).observe().snapshot

    assert snapshot.starters["iron"].present is False
    assert snapshot.foundations["iron"].machine_count == 1
    assert snapshot.foundations["iron"].output_count == 2
    assert snapshot.foundations["copper"].machine_count == 1
    assert snapshot.foundations["stone"].machine_count == 1


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
