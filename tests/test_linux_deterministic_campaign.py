# Path: tests/test_linux_deterministic_campaign.py
# Purpose: Verify the bounded one-call deterministic campaign lifecycle wrapper.

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "scripts" / "manage_linux_deterministic_campaign.sh"
RUNNER_MANAGER = ROOT / "scripts" / "manage_linux_deterministic_runner.sh"


def test_campaign_manager_has_valid_shell_syntax() -> None:
    subprocess.run(["bash", "-n", str(MANAGER)], check=True)


def test_fresh_dry_run_prints_the_six_ordered_verified_steps(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"save")
    result = subprocess.run(
        [
            "bash",
            str(MANAGER),
            "fresh",
            "--source-save",
            str(source),
            "--root",
            str(tmp_path / "state"),
            "--runtime-root",
            str(tmp_path / "runtime"),
            "--gui-mods",
            str(tmp_path / "gui-mods"),
            "--python",
            "/usr/bin/python3",
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    lines = result.stdout.splitlines()
    assert len(lines) == 6
    assert "manage_linux_deterministic_runner.sh stop" in lines[0]
    assert "manage_linux_deterministic_server.sh stop" in lines[1]
    assert "manage_linux_deterministic_server.sh deploy-if-required" in lines[2]
    assert "manage_linux_deterministic_server.sh reset" in lines[3]
    assert str(source) in lines[3]
    assert "manage_linux_deterministic_server.sh start" in lines[4]
    assert "manage_linux_deterministic_runner.sh start" in lines[5]
    assert "--episode-manifest" in lines[5]


def test_cycle_is_a_compatibility_alias_for_fresh(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(b"save")
    fresh = subprocess.run(
        ["bash", str(MANAGER), "fresh", "--source-save", str(source),
         "--episode-id", "episode-test", "--dry-run"],
        check=True, capture_output=True, text=True,
    )
    cycle = subprocess.run(
        ["bash", str(MANAGER), "cycle", "--source-save", str(source),
         "--episode-id", "episode-test", "--dry-run"],
        check=True, capture_output=True, text=True,
    )
    assert fresh.stdout == cycle.stdout


def test_cycle_requires_an_existing_source_save(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            "bash",
            str(MANAGER),
            "cycle",
            "--source-save",
            str(tmp_path / "missing.zip"),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "source save is missing" in result.stderr


def test_runner_resume_omits_the_optional_fresh_episode_manifest() -> None:
    source = RUNNER_MANAGER.read_text(encoding="utf-8")
    assert 'EPISODE_MANIFEST=""' in source
    assert 'manifest_args=(--episode-manifest "$EPISODE_MANIFEST")' in source
    assert '"${manifest_args[@]}"' in source
    assert '--episode-manifest "$EPISODE_MANIFEST" \\\n      --reference-point' not in source


def test_fresh_retries_the_same_runner_episode_before_failing() -> None:
    source = MANAGER.read_text(encoding="utf-8")

    assert 'start_runner_with_retry()' in source
    assert 'for attempt in 1 2 3; do' in source
    assert 'runner start attempt $attempt failed; retrying same episode' in source
