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

    # Bare --world-spec with neither --plan-only nor live credentials is a
    # live-mode attempt, and the CLI fails closed rather than silently
    # defaulting to a plan: it demands an explicit --script-output and
    # --rcon-password (or --plan-only) before it will do anything at all.
    bare = _run("tools/build_advanced_circuits.py", "--world-spec", str(_WORLD))
    assert bare.returncode == 2
    assert "--script-output" in bare.stderr
    assert "--rcon-password" in bare.stderr

    planned = _run(
        "tools/build_advanced_circuits.py", "--world-spec", str(_WORLD), "--plan-only",
    )
    assert planned.returncode == 0
    assert "mode: plan-only" in planned.stdout


def test_processing_cli_requires_world_spec_and_explicit_live_credentials() -> None:
    missing = _run("tools/build_processing_units.py", "--plan-only")
    assert missing.returncode == 2
    assert "--world-spec" in missing.stderr

    planned = _run(
        "tools/build_processing_units.py", "--plan-only", "--world-spec", str(_WORLD),
    )
    assert planned.returncode == 0
    assert "mode: plan-only" in planned.stdout

    # M6 removed the scripted legacy fluid-only bypass outright: there is no
    # opt-in flag left to gate, so the CLI must refuse to even recognize it
    # rather than silently accepting or reinterpreting it.
    legacy = _run(
        "tools/build_processing_units.py", "--plan-only", "--world-spec", str(_WORLD),
        "--legacy-fluid-only",
    )
    assert legacy.returncode == 2
    assert "--legacy-fluid-only" in legacy.stderr
    assert "unrecognized arguments" in legacy.stderr

    # Live mode (no --plan-only) fails closed without explicit credentials.
    bare_live = _run(
        "tools/build_processing_units.py", "--world-spec", str(_WORLD),
    )
    assert bare_live.returncode == 2
    assert "--script-output" in bare_live.stderr
    assert "--rcon-password" in bare_live.stderr

    # --existing-topology only accepts the three explicit reconciliation modes.
    bad_topology = _run(
        "tools/build_processing_units.py", "--plan-only", "--world-spec", str(_WORLD),
        "--existing-topology", "bogus",
    )
    assert bad_topology.returncode == 2
    assert "invalid choice: 'bogus'" in bad_topology.stderr
    assert "choose from" in bad_topology.stderr
    for mode in ("refuse", "reconcile", "reset"):
        assert mode in bad_topology.stderr

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
