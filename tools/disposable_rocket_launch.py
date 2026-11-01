# Path: tools/disposable_rocket_launch.py | Purpose: Journal one explicitly selected disposable silo launch and its observation.
"""Opt-in mechanics operation; it does not authenticate a save or grant real-base authority.

The caller must exclude other runners and arbitrary RCON commands throughout.
The layout lock coordinates cooperating writers only. An interrupted attempt or
observation close requires review and is never automatically resent.
"""

from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid

from jsonschema import Draft7Validator

from core.rocket_launch_evidence import evaluate_launch_session
from orchestrator.game_bridge import ROCKET_LAUNCH_EVENT_SUBDIR
from tools.rocket_launch_evidence import collect_launch_session
from training.laya_layout_stream import _load as load_stream, _locked as lock_stream, atomic_json
from training.laya_saved_world import SavedWorldAdapter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "training/laya_launch_rocket.lua"


class DisposableLaunchError(RuntimeError):
    pass


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError("non-finite value")
    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)


def _read(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("not a regular file")
            data = handle.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError("record exceeds limit")
        value = _json(data)
        if not isinstance(value, dict):
            raise ValueError("object required")
        return value
    except (OSError, ValueError, UnicodeError) as error:
        raise DisposableLaunchError(f"unreadable launch evidence: {Path(path).name}") from error


def _target(target):
    if (not isinstance(target, dict) or set(target) != {"name", "unit_number", "position"}
            or not isinstance(target["name"], str) or not target["name"]
            or type(target["unit_number"]) is not int or target["unit_number"] <= 0
            or not isinstance(target["position"], dict) or set(target["position"]) != {"x", "y"}
            or any(type(value) not in (int, float) or abs(value) > 1_000_000 or not math.isfinite(value)
                   for value in target["position"].values())):
        raise ValueError("target must identify one silo by name, unit number and finite position")
    return json.loads(json.dumps(target, allow_nan=False))


def _adapter(adapter):
    if type(adapter) is not SavedWorldAdapter or adapter.runtime != "disposable_saved_world":
        raise DisposableLaunchError("the disposable saved-world adapter is required")
    if (adapter.surface, adapter.force) != ("nauvis", "player"):
        raise DisposableLaunchError("the saved-world scope must be explicit")
    SavedWorldAdapter._check_instance(adapter)


def _schema(name, value):
    schema = json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not Draft7Validator(schema).is_valid(value):
        raise DisposableLaunchError("launch observation does not match its contract")


def _armed_start(start, *, tick, surface, force):
    _schema("rocket_launch_observation.schema.json", start)
    if (start.get("kind") != "rocket_launch_scope" or start.get("ok") is not True
            or start.get("surface") != surface or start.get("force") != force
            or start.get("enabled") is not True or start.get("session_started") is not True
            or type(start.get("tick")) is not int or start["tick"] != tick
            or type(start.get("scope_since_tick")) is not int or start["scope_since_tick"] != tick
            or type(start.get("recorded_launches")) is not int or start["recorded_launches"] != 0
            or not isinstance(start.get("session_id"), str) or start["session_id"] == "unidentified"
            or not isinstance(start.get("request_id"), str)):
        raise DisposableLaunchError("fresh same-tick launch observation could not be armed")


def _clear_evidence(evidence):
    if any(evidence.glob("disposable-rocket-launch-*.json")):
        raise DisposableLaunchError("previous launch attempt requires review")
    for pattern, accepted in (("transaction-*.json", {"observed", "rejected"}),
                              ("optimizer-stock-refresh-*.json", {"applied"})):
        for path in evidence.glob(pattern):
            if _read(path).get("status") not in accepted:
                raise DisposableLaunchError(f"unresolved evidence prevents launch: {path.name}")


def disposable_launch_options(adapter, *, tick):
    """Offer each identity-readable, engine-ready silo in the current paused sample.

    The report is a runtime observation, not proof of earned construction,
    cargo, destination, successful launch, or authority outside this copy.
    """
    _adapter(adapter)
    if type(tick) is not int or tick < 0:
        raise ValueError("a non-negative observation tick is required")
    if any((adapter.root / "evidence").glob("disposable-rocket-launch-*.json")):
        return []
    report = adapter.bridge.observe_rocket_silos(adapter.surface, adapter.force)
    _schema("rocket_silo_observation.schema.json", report)
    if (report.get("ok") is not True or report.get("surface") != adapter.surface
            or report.get("force") != adapter.force or type(report.get("tick")) is not int
            or report["tick"] != tick or type(report.get("silo_count")) is not int
            or report["silo_count"] != len(report["silos"])
            or report["requested"]["unit_number"].get("state") != "absent"):
        raise DisposableLaunchError("silo survey differs from the paused-world sample")
    options, seen = [], set()
    for silo in report["silos"]:
        unit, position, name = silo["unit_number"], silo["position"], silo.get("prototype_name", {})
        if unit.get("state") != "available":
            continue
        number = unit.get("value")
        if type(number) is not int or number <= 0 or number in seen:
            raise DisposableLaunchError("silo survey has an invalid or duplicate unit identity")
        seen.add(number)
        if (position.get("state") != "available" or name.get("state") != "available"
                or silo["status"]["rocket_silo"] != {"state": "available", "value": "rocket_ready"}):
            continue
        target = _target({"name": name.get("value"), "unit_number": number, "position": position.get("value")})
        identity = {"surface": adapter.surface, "force": adapter.force, "tick": tick, "target": target}
        digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":"),
                                           ensure_ascii=True, allow_nan=False).encode("ascii")).hexdigest()
        options.append({"option_id": "launch." + digest, "family": "launch", "launch_target": target,
                        "observed_tick": tick, "summary": f"Launch ready silo {number} at {target['position']}"})
    return options


def execute_selected_disposable_launch(adapter, *, selected, presented, measure_ticks):
    """Execute a single selected, still-current launch option and observe its window."""
    from training.laya_saved_world import measured_outcome

    if (not isinstance(selected, str) or not isinstance(presented, list)
            or type(measure_ticks) is not int or not 1 <= measure_ticks <= 18000):
        raise ValueError("selected option, presented menu and bounded measurement are required")
    matches = [candidate for candidate in presented if isinstance(candidate, dict)
               and candidate.get("option_id") == selected]
    if len(matches) != 1 or matches[0].get("family") != "launch":
        raise DisposableLaunchError("launch selection must resolve to one presented option")
    _adapter(adapter)
    before = SavedWorldAdapter.query(adapter)
    current = disposable_launch_options(adapter, tick=before.get("tick"))
    if matches[0] not in current:
        raise DisposableLaunchError("selected silo is no longer the same ready option")
    order = order_disposable_rocket(adapter, target=matches[0]["launch_target"])
    if order["status"] != "ordered":
        close_disposable_launch_observation(adapter, Path(order["receipt_path"]))
        raise DisposableLaunchError("the engine did not accept the launch order")
    adapter.advance(measure_ticks)
    after = SavedWorldAdapter.query(adapter)
    closed = close_disposable_launch_observation(adapter, Path(order["receipt_path"]))
    observed = closed["launch_evidence"]["status"] == "matching_event_found"
    return {"status": "launch_observed" if observed else "launch_event_not_observed",
            "before": before, "after": after, "measurement": measured_outcome(before, after),
            "launch_receipt": closed}


def order_disposable_rocket(adapter, *, target):
    """Order exactly one selected silo launch and journal before any mutation.

    Fresh telemetry is armed and persisted first. A true engine response means
    only that launch was ordered; ascent is observed separately. A false result,
    timeout, crash or malformed reply is never automatically retried.
    """
    target = _target(target)
    _adapter(adapter)
    source = SOURCE.read_text(encoding="utf-8")
    if adapter.code_hashes.get(str(SOURCE.relative_to(ROOT))) != hashlib.sha256(source.encode("utf-8")).hexdigest():
        raise DisposableLaunchError("launch script changed since the adapter started")
    with lock_stream(adapter.root) as stream_path:
        if load_stream(stream_path)["pending"] is not None:
            raise DisposableLaunchError("pending layout submission prevents launch")
        evidence = stream_path.parent
        _clear_evidence(evidence)
        _adapter(adapter)
        facts = SavedWorldAdapter.query(adapter)
        tick = facts.get("tick") if isinstance(facts, dict) else None
        if type(tick) is not int or tick < 0:
            raise DisposableLaunchError("paused-world tick is unavailable")
        silo = adapter.bridge.observe_rocket_silos(adapter.surface, adapter.force, unit_number=target["unit_number"])
        _schema("rocket_silo_observation.schema.json", silo)
        if (silo.get("ok") is not True or silo.get("surface") != adapter.surface or silo.get("force") != adapter.force
                or type(silo.get("tick")) is not int or silo["tick"] != tick
                or type(silo.get("silo_count")) is not int or silo["silo_count"] != 1
                or silo["requested"]["unit_number"] != {"state": "available", "value": target["unit_number"]}
                or type(silo["requested"]["unit_number"].get("value")) is not int
                or silo["silos"][0]["unit_number"] != {"state": "available", "value": target["unit_number"]}
                or type(silo["silos"][0]["unit_number"].get("value")) is not int
                or ("prototype_name" in silo["silos"][0] and silo["silos"][0]["prototype_name"]
                    != {"state": "available", "value": target["name"]})
                or silo["silos"][0]["position"] != {"state": "available", "value": target["position"]}):
            raise DisposableLaunchError("silo observation differs from the selected target or tick")
        request_id = uuid.uuid4().hex
        path = evidence / f"disposable-rocket-launch-{request_id}.json"
        if path.exists() or path.is_symlink():
            raise DisposableLaunchError("launch attempt identity already exists")
        receipt = {"version": "disposable-rocket-launch-v1", "request_id": request_id,
                   "status": "arming", "surface": adapter.surface, "force": adapter.force,
                   "target": target, "tick": tick, "silo_observation": silo}
        atomic_json(path, receipt)
        start = _read(adapter.bridge.rocket_launch_observation(adapter.surface, adapter.force, enabled=True))
        _armed_start(start, tick=tick, surface=adapter.surface, force=adapter.force)
        receipt.update(status="ordering", observation_start=start)
        atomic_json(path, receipt)
        request = {"request_id": request_id, "surface": adapter.surface, "force": adapter.force,
                   "expected_tick": tick, **target}
        encoded = json.dumps(request, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        delimiter = "="
        while f"]{delimiter}]" in encoded:
            delimiter += "="
        program = " ".join(line.split("--", 1)[0] for line in source.splitlines())
        command = (f"/sc local request=helpers.json_to_table([{delimiter}[{encoded}]{delimiter}]); "
                   f"local result=(function() {program} end)(); rcon.print(helpers.table_to_json(result))")
        _adapter(adapter)
        response = adapter.bridge.command(command)
        try:
            result = _json(response)
        except (ValueError, TypeError) as error:
            raise DisposableLaunchError("launch order outcome is unknown") from error
        expected = {"version": "disposable-rocket-order-v1", "request_id": request_id, "tick": tick,
                    "surface": adapter.surface, "force": adapter.force, "unit_number": target["unit_number"]}
        if (not isinstance(result, dict) or set(result) != {*expected, "ordered"}
                or any(type(result.get(key)) is not type(value) or result.get(key) != value
                       for key, value in expected.items()) or type(result.get("ordered")) is not bool):
            raise DisposableLaunchError("launch order response does not match the attempt")
        receipt.update(status="ordered" if result["ordered"] else "not_ready", order_result=result)
        atomic_json(path, receipt)
        return {"receipt_path": str(path), **receipt}


def close_disposable_launch_observation(adapter, receipt_path):
    """Close one ordered session and reduce its event files; never resend a launch."""
    _adapter(adapter)
    path = Path(receipt_path)
    if (path.parent != adapter.root / "evidence"
            or not re.fullmatch(r"disposable-rocket-launch-[0-9a-f]{32}\.json", path.name)):
        raise ValueError("launch receipt must belong to the saved-world evidence directory")
    with lock_stream(adapter.root) as stream_path:
        if load_stream(stream_path)["pending"] is not None:
            raise DisposableLaunchError("pending layout submission prevents launch observation close")
        receipt = _read(path)
        if (receipt.get("version") != "disposable-rocket-launch-v1"
                or path.stem != "disposable-rocket-launch-" + str(receipt.get("request_id"))
                or receipt.get("status") not in {"ordered", "not_ready"}
                or (receipt.get("surface"), receipt.get("force")) != (adapter.surface, adapter.force)):
            raise DisposableLaunchError("launch attempt is not ready for observation close")
        target = _target(receipt.get("target"))
        start = receipt.get("observation_start")
        _armed_start(start, tick=receipt.get("tick"), surface=adapter.surface, force=adapter.force)
        receipt["status"] = "closing"
        atomic_json(path, receipt)
        _adapter(adapter)
        end = _read(adapter.bridge.rocket_launch_observation(adapter.surface, adapter.force, enabled=False))
        event_directory = adapter.bridge.script_output / ROCKET_LAUNCH_EVENT_SUBDIR
        if end.get("recorded_launches") == 0 and not event_directory.exists() and not event_directory.is_symlink():
            evidence = evaluate_launch_session(start, end, [], target_silo=target)
        else:
            evidence = collect_launch_session(event_directory, start=start, end=end, target_silo=target)
        receipt.update(status="closed", observation_end=end, launch_evidence=asdict(evidence))
        atomic_json(path, receipt)
        return {"receipt_path": str(path), **receipt}
