# Path: tests/test_observatory_control.py
# Purpose: Verify bounded Observatory training-control validation and HTTP wiring.

from __future__ import annotations

import json
import threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen

import pytest

from tools.training_observer import _handler
import training.observatory_control as observatory_control
from training.observatory_control import TrainingStartRequest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_training_control_form_exposes_explicit_runtime_settings() -> None:
    html = (REPO_ROOT / "tools" / "training_observer.html").read_text(encoding="utf-8")
    script = (REPO_ROOT / "tools" / "training_observer.js").read_text(encoding="utf-8")

    assert 'id="training-control-form"' in html
    assert 'id="training-server-count"' in html
    assert 'id="training-runner-limit"' in html
    assert 'id="training-initial-runners"' in html
    assert 'id="training-episodes-per-policy"' in html
    assert 'id="training-policy-rounds"' in html
    assert 'id="training-start-seed"' in html
    assert "fetch('/api/training/start'" in script
    assert "fetch('/api/training/stop'" in script

def test_training_start_request_validates_bounded_runtime_settings() -> None:
    request = TrainingStartRequest.from_payload({
        "server_count": 4, "runner_limit": 16, "initial_runners": 8,
        "episodes_per_policy": 64, "policy_rounds": 3, "start_seed": 1200,
    })

    assert request.server_count == 4
    assert request.runner_limit == 16
    assert request.initial_runners == 8
    assert request.episodes_per_policy == 64
    assert request.policy_rounds == 3
    assert request.start_seed == 1200


@pytest.mark.parametrize("field,value", [
    ("server_count", 0), ("server_count", 51),
    ("runner_limit", 0), ("runner_limit", 81),
    ("initial_runners", 17), ("episodes_per_policy", 0),
    ("policy_rounds", 0), ("start_seed", -1),
])
def test_training_start_request_rejects_out_of_bounds_values(field: str, value: int) -> None:
    payload = {"server_count": 4, "runner_limit": 16, "initial_runners": 8,
               "episodes_per_policy": 64, "policy_rounds": 3, "start_seed": 1200}
    payload[field] = value

    with pytest.raises(ValueError):
        TrainingStartRequest.from_payload(payload)


class FakeTrainingControl:
    def __init__(self) -> None:
        self.started: TrainingStartRequest | None = None
        self.stopped = False

    def snapshot(self) -> dict[str, object]:
        return {"status": "stopped", "message": "ready", "controller_alive": False}

    def start(self, request: TrainingStartRequest) -> dict[str, object]:
        self.started = request
        return {"status": "starting", "run_id": "training-observatory-test"}

    def stop(self) -> dict[str, object]:
        self.stopped = True
        return {"status": "stopping", "run_id": "training-observatory-test"}


class FakeProcess:
    pid = 4242

    def __init__(self) -> None:
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code

    def wait(self) -> int:
        self.return_code = 0
        return 0


def test_supervisor_starts_configured_workers_and_controller(monkeypatch, tmp_path) -> None:
    supervisor = observatory_control.TrainingSupervisor(tmp_path)
    request = TrainingStartRequest(2, 12, 4, 99, 24, 3)
    manage_calls: list[tuple[str, int, int]] = []
    fake_process = FakeProcess()
    popen_commands: list[list[str]] = []
    monkeypatch.setattr(supervisor, "_run_manage", lambda action, servers, runners: manage_calls.append((action, servers, runners)))
    monkeypatch.setattr(supervisor, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(observatory_control.subprocess, "Popen", lambda *args, **kwargs: (popen_commands.append(args[0]) or fake_process))

    result = supervisor.start(request)
    assert result["status"] == "starting"
    assert supervisor._operation is not None
    supervisor._operation.join(timeout=2)
    assert supervisor.snapshot()["status"] == "completed"
    assert manage_calls == [("configure", 2, 12), ("start", 2, 12)]
    assert "--episodes-per-policy" in popen_commands[0]
    assert popen_commands[0][popen_commands[0].index("--episodes-per-policy") + 1] == "24"
    assert "--policy-rounds" in popen_commands[0]
    assert popen_commands[0][popen_commands[0].index("--policy-rounds") + 1] == "3"


def test_linux_supervisor_uses_direct_worker_manager_and_runner(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(observatory_control.sys, "platform", "linux")
    supervisor = observatory_control.TrainingSupervisor(tmp_path)

    assert supervisor.worker_config.name == "training-workers-linux.json"
    assert supervisor.manage_script.name == "manage_linux_training_worker.sh"
    command = supervisor._runner_command(["--count", "1"])
    assert command[0] == observatory_control.sys.executable
    assert "--workers" in command
    assert command[command.index("--workers") + 1].endswith("training-workers-linux.json")

def test_training_control_endpoints_forward_only_validated_requests() -> None:
    control = FakeTrainingControl()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(None, None, control=control))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        start = Request(
            f"{base}/api/training/start", method="POST", data=json.dumps({
                "server_count": 2, "runner_limit": 12, "initial_runners": 4,
                "episodes_per_policy": 24, "policy_rounds": 3, "start_seed": 99,
            }).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        with urlopen(start, timeout=2) as response:
            assert response.status == 202
        assert control.started == TrainingStartRequest(2, 12, 4, 99, 24, 3)

        stop = Request(
            f"{base}/api/training/stop", method="POST", data=json.dumps({
                "confirmation": "STOP_TRAINING",
            }).encode("utf-8"), headers={"Content-Type": "application/json"},
        )
        with urlopen(stop, timeout=2) as response:
            assert response.status == 202
        assert control.stopped is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
