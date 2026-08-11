# Path: training/observer_control.py
# Purpose: Restrict Observatory viewer hops to one configured loopback training worker.

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from tools.rcon_client import RconClient
from training.scheduler import WorkerSpec, load_worker_specs

_COMMAND_VERSION = "1.0.0"
_FOCUS_TOKEN = "FOCUS_TRAINING_OBSERVER"
_CLEANUP_TOKEN = "RECYCLE_STALE_TRAINING_SURFACES"


class ObserverControlError(RuntimeError):
    """A viewer request could not be safely completed on its training worker."""


class RconConnection(Protocol):
    def command(self, command: str) -> str: ...

    def close(self) -> None: ...


RconFactory = Callable[..., RconConnection]


@dataclass(frozen=True)
class TrainingSurfaceViewer:
    workers: dict[str, WorkerSpec]
    password: str
    observer_name: str
    rcon_factory: RconFactory = RconClient

    @classmethod
    def from_files(
        cls, worker_config: Path, secret_file: Path, observer_name: str,
        rcon_factory: RconFactory = RconClient,
    ) -> "TrainingSurfaceViewer":
        password = secret_file.read_text(encoding="utf-8").strip()
        if not password:
            raise ValueError("training observer RCON secret is empty")
        if not observer_name or len(observer_name) > 128:
            raise ValueError("training observer player name is invalid")
        workers = load_worker_specs(worker_config)
        return cls({worker.worker_id: worker for worker in workers}, password, observer_name, rcon_factory)

    def _worker(self, worker_id: str) -> WorkerSpec:
        if not isinstance(worker_id, str) or worker_id not in self.workers:
            raise ObserverControlError("unknown training worker")
        worker = self.workers[worker_id]
        if not worker.surface_prefix.startswith("training/"):
            raise ObserverControlError("configured worker is not training-only")
        return worker

    def focus(self, worker_id: str, episode_id: str) -> dict[str, str]:
        worker = self._worker(worker_id)
        if not isinstance(episode_id, str) or not episode_id or len(episode_id) > 128:
            raise ObserverControlError("invalid training episode")
        payload = {
            "version": _COMMAND_VERSION, "request_id": uuid.uuid4().hex,
            "episode_id": episode_id, "observer_name": self.observer_name,
            "confirmation_token": _FOCUS_TOKEN,
        }
        client = self.rcon_factory(worker.host, worker.rcon_port, self.password, timeout=15.0)
        try:
            response = client.command("/training_focus " + json.dumps(payload, separators=(",", ":")))
        finally:
            client.close()
        try:
            result = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ObserverControlError("training worker returned an invalid view response") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ObserverControlError(str(result.get("error", "training view request failed")))
        if result.get("episode_id") != episode_id:
            raise ObserverControlError("training worker returned a mismatched episode")
        surface = result.get("surface")
        if not isinstance(surface, str) or not surface.startswith(worker.surface_prefix):
            raise ObserverControlError("training worker returned a non-training surface")
        observer_name = result.get("observer_name")
        if observer_name != self.observer_name:
            raise ObserverControlError("training worker focused an unexpected observer")
        return {"episode_id": episode_id, "surface": surface, "observer_name": observer_name}

    def recycle_stale(self, worker_id: str) -> dict[str, object]:
        worker = self._worker(worker_id)
        payload = {
            "version": _COMMAND_VERSION, "request_id": uuid.uuid4().hex,
            "confirmation_token": _CLEANUP_TOKEN,
        }
        client = self.rcon_factory(worker.host, worker.rcon_port, self.password, timeout=15.0)
        try:
            response = client.command(
                "/training_cleanup_orphans " + json.dumps(payload, separators=(",", ":")),
            )
        finally:
            client.close()
        try:
            result = json.loads(response)
        except json.JSONDecodeError as exc:
            raise ObserverControlError("training worker returned an invalid cleanup response") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise ObserverControlError(str(result.get("error", "stale-surface cleanup failed")))
        for field in ("recycled", "pending", "connected", "refused"):
            values = result.get(field, [])
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not value.startswith(worker.surface_prefix)
                for value in values
            ):
                raise ObserverControlError("training worker returned a non-training surface")
        return result
