# Path: tests/test_run_laya_workflow_batch.py
# Purpose: Protect stepped-mode instance pause/unpause and its one-slot-per-instance guard.

from __future__ import annotations

import json

import pytest

import tools.run_laya_workflow_batch as batch


class FakeRcon:
    """Records /sc scripts sent to one instance; answers rcon.print(...) calls."""

    def __init__(self, *, closes: list[str] | None = None):
        self.commands: list[str] = []
        self._closes = closes

    def command(self, text: str) -> str:
        self.commands.append(text)
        if "game.tick_paused" in text and "rcon.print(game.tick_paused)" in text:
            return "true" if "= true" in text else "false"
        if "game.speed" in text and "rcon.print(game.speed)" in text:
            return "8.0"
        return ""

    def close(self) -> None:
        if self._closes is not None:
            self._closes.append("closed")


def test_set_stepped_mode_pauses_and_unpauses_with_speed_reset(monkeypatch) -> None:
    created: list[FakeRcon] = []

    def fake_client(host, port, password, timeout=15.0):
        rcon = FakeRcon()
        created.append(rcon)
        return rcon

    monkeypatch.setattr(batch, "RconClient", fake_client)

    paused = batch.set_stepped_mode("127.0.0.1", 27015, "pw", True)
    assert paused == "true"
    assert "game.tick_paused = true" in created[0].commands[0]

    resumed = batch.set_stepped_mode("127.0.0.1", 27015, "pw", False)
    assert resumed == "false"
    # Unpausing also restores game.speed, matching the normal-mode cleanup contract.
    assert "game.tick_paused = false" in created[1].commands[0]
    assert "game.speed = 1" in created[1].commands[0]


def test_set_stepped_all_touches_each_instance_once(monkeypatch) -> None:
    calls: list[tuple[str, int, str, bool]] = []

    def fake_set_stepped_mode(host, port, password, enabled, speed=1.0):
        calls.append((host, port, password, enabled))
        return "true" if enabled else "false"

    monkeypatch.setattr(batch, "set_stepped_mode", fake_set_stepped_mode)

    class Slot:
        def __init__(self, instance_id):
            self.instance_id = instance_id
            self.host = "127.0.0.1"
            self.rcon_port = 27015

    slots = [(Slot("inst-a"), "pw"), (Slot("inst-a"), "pw"), (Slot("inst-b"), "pw")]

    seen = batch.set_stepped_all(slots, True)

    assert seen == {"inst-a": "true", "inst-b": "true"}
    assert len(calls) == 2  # one RCON round trip per distinct instance, not per slot


def _write_worker_config(tmp_path, *, slots_per_instance: int):
    script_output = tmp_path / "script-output"
    script_output.mkdir()
    (tmp_path / "rcon-password").write_text("secret", encoding="utf-8")
    workers = [{
        "worker_id": f"worker-{index}", "instance_id": "factorio-training-linux-99",
        "host": "127.0.0.1", "game_port": 34197, "rcon_port": 27099,
        "script_output": str(script_output),
    } for index in range(slots_per_instance)]
    path = tmp_path / "workers.json"
    path.write_text(json.dumps({"workers": workers}), encoding="utf-8")
    return path


def test_stepped_flag_requires_exactly_one_slot_per_instance(tmp_path, capsys) -> None:
    workers = _write_worker_config(tmp_path, slots_per_instance=2)

    with pytest.raises(SystemExit):
        batch.main([
            "--workers", str(workers), "--instance", "factorio-training-linux-99",
            "--slots", "2", "--stepped", "--seed-start", "1", "--episodes", "1",
            "--run-prefix", "test-stepped-guard",
        ])

    assert "exactly --slots 1" in capsys.readouterr().err
