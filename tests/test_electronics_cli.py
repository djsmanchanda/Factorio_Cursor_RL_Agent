# Path: tests/test_electronics_cli.py
# Purpose: Verify electronics CLIs fail closed and gate the scripted legacy path explicitly.

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORLD = _REPO_ROOT / "tests" / "fixtures" / "electronics_world_spec.json"


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=_REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_advanced_circuit_cli_requires_schema_loaded_world_spec() -> None:
    missing = _run("tools/build_advanced_circuits.py")
    assert missing.returncode == 2
    assert "--world-spec" in missing.stderr

    planned = _run("tools/build_advanced_circuits.py", "--world-spec", str(_WORLD))
    assert planned.returncode == 0
    assert "live execution: disabled" in planned.stdout


def test_processing_cli_requires_world_spec_or_explicit_legacy_opt_in() -> None:
    missing = _run("tools/build_processing_units.py", "--plan-only")
    assert missing.returncode == 2
    assert "--legacy-fluid-only" in missing.stderr

    planned = _run(
        "tools/build_processing_units.py", "--plan-only", "--world-spec", str(_WORLD),
    )
    assert planned.returncode == 0
    assert "live execution: disabled" in planned.stdout

    legacy = _run("tools/build_processing_units.py", "--plan-only", "--legacy-fluid-only")
    assert legacy.returncode == 0

def test_legacy_mining_builders_require_explicit_existing_resource_acknowledgement() -> None:
    line = _run(
        "tools/build_line.py", "--script-output", "unused", "--rcon-password", "unused", "--mine",
    )
    assert line.returncode == 2
    assert "requires a surveyed resource source" in line.stderr

    science = _run("tools/build_science_chain.py", "--plan-only")
    assert science.returncode == 2
    assert "--legacy-existing-resources" in science.stderr

    acknowledged = _run(
        "tools/build_science_chain.py", "--plan-only", "--legacy-existing-resources",
    )
    assert acknowledged.returncode == 0