# Path: tests/test_science_status.py
# Purpose: Pin ScienceStatus v1's strict read-only report and bridge contract.

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator

from orchestrator.game_bridge import SCIENCE_REPORT_SUBDIR, GameBridge
from tools.science_status import main, science_status_errors

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas" / "science_status.schema.json"


def _success_report() -> dict:
    return {
        "schema_version": "1.0.0",
        "ok": True,
        "tick": 123456,
        "surface": "nauvis",
        "force": "player",
        "research": {
            "current": "automation",
            "progress": 0.42,
            "queue": ["automation", "logistics"],
            "current_science_packs": {"automation-science-pack": 1},
            "current_research_unit_count": 10,
        },
        "labs": {
            "total": 2,
            "working": 1,
            "status_counts": {"no_power": 1, "working": 1},
            "input_inventory": {"automation-science-pack": 7},
            "entries": [
                {
                    "unit_number": 41,
                    "position": {"x": 10.5, "y": -2.5},
                    "status": "working",
                    "input_inventory": {"automation-science-pack": 3},
                },
                {
                    "unit_number": 43,
                    "position": {"x": 14.5, "y": -2.5},
                    "status": "no_power",
                    "input_inventory": {"automation-science-pack": 4},
                },
            ],
        },
    }


def _failure_report() -> dict:
    return {
        "schema_version": "1.0.0",
        "ok": False,
        "tick": 123456,
        "surface": "",
        "force": "",
        "error": "Science status payload must include surface and force",
    }


def test_science_status_schema_accepts_success_and_error_reports() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    validator = Draft7Validator(schema)

    assert list(validator.iter_errors(_success_report())) == []
    assert list(validator.iter_errors(_failure_report())) == []

    no_research = _success_report()
    no_research["research"] = {
        "current": None,
        "progress": 0,
        "queue": [],
        "current_science_packs": {},
        "current_research_unit_count": 0,
    }
    assert list(validator.iter_errors(no_research)) == []


def test_science_status_schema_rejects_zero_item_counts_and_error_state_leaks() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    report = _success_report()
    report["labs"]["input_inventory"] = {"automation-science-pack": 0}
    assert list(validator.iter_errors(report))

    failure = _failure_report()
    failure["labs"] = _success_report()["labs"]
    assert list(validator.iter_errors(failure))


def test_inspector_rejects_non_deterministic_lab_entry_order() -> None:
    report = _success_report()
    report["labs"]["entries"].reverse()

    assert science_status_errors(report, SCHEMA_PATH) == [
        "labs.entries must be strictly sorted by unit_number"
    ]


def test_science_status_bridge_sends_one_explicit_scoped_command() -> None:
    bridge = GameBridge.__new__(GameBridge)
    calls: list[tuple[str, Path, float]] = []

    def collect(command: str, subdir: Path, timeout: float) -> Path:
        calls.append((command, subdir, timeout))
        return Path("science_status.json")

    bridge._run_and_collect = collect  # type: ignore[method-assign]

    assert bridge.science_status("nauvis", "player") == Path("science_status.json")
    assert calls == [
        (
            '/science_status {"surface":"nauvis","force":"player"}',
            SCIENCE_REPORT_SUBDIR,
            60.0,
        )
    ]


@pytest.mark.parametrize("surface, force", [("", "player"), ("nauvis", ""), (None, "player")])
def test_science_status_bridge_rejects_invalid_targets_before_collection(
    surface: str | None, force: str,
) -> None:
    bridge = GameBridge.__new__(GameBridge)
    bridge._run_and_collect = lambda *_args: pytest.fail("invalid targets must not collect")  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="non-empty string"):
        bridge.science_status(surface, force)  # type: ignore[arg-type]


def test_science_status_inspector_prints_identity_and_aggregates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "science_status.json"
    report_path.write_text(json.dumps(_success_report()), encoding="utf-8")

    assert main([str(report_path)]) == 0

    output = capsys.readouterr().out
    assert "Science Status" in output
    assert "Tick: 123456" in output
    assert "Surface: nauvis" in output
    assert "Force: player" in output
    assert "Labs: 1 working / 2 total" in output
    assert "automation-science-pack: 7" in output


def test_science_status_inspector_fails_closed_for_aggregate_mismatch(tmp_path: Path) -> None:
    report = copy.deepcopy(_success_report())
    report["labs"]["working"] = 2
    report_path = tmp_path / "science_status.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with pytest.raises(SystemExit, match="1"):
        main([str(report_path)])
