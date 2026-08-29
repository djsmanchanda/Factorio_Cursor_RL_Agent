# Path: tests/test_mission_state.py
# Purpose: Verify bootstrap identity, cross-controller mission state, and typed blocker telemetry.

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

from jsonschema import Draft7Validator

from orchestrator.mission_state import MissionStateLedger
from orchestrator.stage_services import StuckError
from orchestrator.work_state import WORK_CLASSIFICATIONS, WORK_STATES
from tools.autonomous_run import _bootstrap_profile


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "schemas" / "deterministic_mission_state.schema.json").read_text(
        encoding="utf-8"
    )
)


def _ledger(tmp_path: Path) -> MissionStateLedger:
    return MissionStateLedger(
        tmp_path / "mission.json",
        tmp_path / "blockers.jsonl",
        episode_id="episode-1",
        bootstrap_profile="reduced-v1",
        command="research",
        target="mining-productivity-4",
        surface="nauvis",
        force="player",
        repository_revision="abc123",
        save_provenance={"baseline_world_fingerprint": "sha256:save"},
    )


def test_mission_ledger_spans_pack_controllers_and_validates(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.transition("research_preflight", current_target="mining-productivity-4")
    ledger.controller_started("automation-science-pack")
    ledger.controller_finished("automation-science-pack", "completed")
    ledger.controller_started("logistic-science-pack")
    ledger.controller_finished("logistic-science-pack", "completed")
    ledger.finish("completed")

    payload = json.loads(ledger.path.read_text(encoding="utf-8"))

    assert payload["bootstrap_profile"] == "reduced-v1"
    assert payload["status"] == "completed"
    assert [item["target"] for item in payload["controllers"]] == [
        "automation-science-pack", "logistic-science-pack",
    ]
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))


def test_restart_preserves_history_and_increments_attempt(tmp_path: Path) -> None:
    first = _ledger(tmp_path)
    first.controller_started("automation-science-pack")
    first.controller_finished("automation-science-pack", "failed")
    first.finish("stuck")

    resumed = _ledger(tmp_path)
    payload = json.loads(resumed.path.read_text(encoding="utf-8"))

    assert payload["attempt"] == 2
    assert payload["status"] == "running"
    assert payload["controllers"][0]["status"] == "failed"
    assert len([event for event in payload["events"] if event["event"] == "mission_started"]) == 2


def test_typed_blocker_is_in_snapshot_and_append_only_stream(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.controller_started("automation-science-pack")
    error = StuckError(
        "foundation bill cannot be funded",
        code="foundation_supply_shortage",
        classification="intended_difficulty",
        state="supply_wait",
        details={"missing": {"splitter": 4}},
    )

    blocker = ledger.record_blocker(error)
    ledger.controller_finished("automation-science-pack", "failed")
    ledger.finish("stuck")

    payload = json.loads(ledger.path.read_text(encoding="utf-8"))
    stream = [
        json.loads(line)
        for line in ledger.blocker_events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert blocker["classification"] == "intended_difficulty"
    assert blocker["state"] == "supply_wait"
    assert blocker["target"] == "automation-science-pack"
    assert blocker["details"]["missing"] == {"splitter": 4}
    assert payload["blockers"] == stream
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))


def test_untyped_stuck_defaults_to_bug_instead_of_claiming_difficulty(tmp_path: Path) -> None:
    blocker = _ledger(tmp_path).record_blocker(StuckError("legacy failure"))

    assert blocker["code"] == "untyped_stuck"
    assert blocker["classification"] == "bug"
    assert blocker["state"] == "failed"


def test_mission_schema_uses_the_canonical_work_taxonomy() -> None:
    blocker = SCHEMA["definitions"]["blocker"]["properties"]

    assert set(blocker["state"]["enum"]) == WORK_STATES
    assert set(blocker["classification"]["enum"]) == WORK_CLASSIFICATIONS


def test_bootstrap_profile_prefers_cli_then_manifest_then_reduced_default(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "episode.json"
    manifest.write_text(json.dumps({"bootstrap_profile": "supplied-v1"}), encoding="utf-8")

    assert _bootstrap_profile(Namespace(
        bootstrap_profile="reduced-v1", episode_manifest=manifest,
    )) == "reduced-v1"
    assert _bootstrap_profile(Namespace(
        bootstrap_profile=None, episode_manifest=manifest,
    )) == "supplied-v1"
    assert _bootstrap_profile(Namespace(
        bootstrap_profile=None, episode_manifest=None,
    )) == "reduced-v1"


def test_runner_records_controller_failure_as_structured_blocker(
    tmp_path: Path, monkeypatch,
) -> None:
    import tools.autonomous_run as autonomous_run

    def fail(*_args, **_kwargs):
        raise StuckError(
            "construction reserve is empty",
            code="construction_supply_shortage",
            classification="intended_difficulty",
            state="supply_wait",
            details={"item": "transport-belt"},
        )

    monkeypatch.setattr(autonomous_run, "run", fail)
    mission_path = tmp_path / "mission.json"
    blocker_path = tmp_path / "blockers.jsonl"
    result = autonomous_run.main([
        "produce", "automation-science-pack",
        "--rcon-password", "test",
        "--script-output", str(tmp_path / "script-output"),
        "--log-file", str(tmp_path / "run.log"),
        "--mission-state-file", str(mission_path),
        "--blocker-events-file", str(blocker_path),
        "--bootstrap-profile", "reduced-v1",
        "--no-helper-agent-review",
    ])

    payload = json.loads(mission_path.read_text(encoding="utf-8"))
    assert result == 2
    assert payload["status"] == "stuck"
    assert payload["controllers"][-1]["status"] == "failed"
    assert payload["blockers"][-1]["code"] == "construction_supply_shortage"
    assert payload["blockers"][-1]["classification"] == "intended_difficulty"
    assert len(blocker_path.read_text(encoding="utf-8").splitlines()) == 1
    assert not list(Draft7Validator(SCHEMA).iter_errors(payload))
