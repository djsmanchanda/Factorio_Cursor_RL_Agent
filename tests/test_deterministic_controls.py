# Path: tests/test_deterministic_controls.py
# Purpose: Bound deterministic passes/submissions and verify episode/repair gates.

import inspect
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator import mall_builder
from orchestrator.controller_budget import (
    BudgetExhausted,
    begin_run_budget,
    consume_diagnosis,
    consume_remediation,
    consume_plan_submission,
    consume_wait,
    end_run_budget,
)
from orchestrator.stage_services import StuckError, _submit
from tools.autonomous_run import (
    REPO_ROOT, _directory_hash, _patch_episode_manifest, _validate_episode_manifest,
)


def test_every_control_pass_is_counted_before_a_continue_branch() -> None:
    source = inspect.getsource(builder.run)
    assert "while budget.passes < max_iterations:" in source
    assert "budget.begin_pass()" in source
    assert source.index("while budget.passes < max_iterations:") < source.index(
        "budget.begin_pass()"
    )
    begin = begin_run_budget(2, plans_per_pass=3)
    try:
        begin.begin_pass()
        begin.begin_pass()
        with pytest.raises(BudgetExhausted, match="pass budget"):
            begin.begin_pass()
    finally:
        end_run_budget()


def test_plan_submissions_are_individually_bounded() -> None:
    begin_run_budget(1, plans_per_pass=2)
    try:
        consume_plan_submission("first")
        consume_plan_submission("second")
        with pytest.raises(BudgetExhausted, match="plan submission budget"):
            consume_plan_submission("third")
    finally:
        end_run_budget()


def test_remediation_wait_and_diagnosis_cycles_are_bounded() -> None:
    begin_run_budget(1, plans_per_pass=1)
    try:
        for index in range(4):
            consume_wait(f"wait{index}")
        with pytest.raises(BudgetExhausted, match="wait budget"):
            consume_wait("second")
    finally:
        end_run_budget()

    begin_run_budget(1, plans_per_pass=1)
    try:
        for index in range(8):
            consume_remediation(f"remediation{index}")
        with pytest.raises(BudgetExhausted, match="remediation budget"):
            consume_remediation("second")
    finally:
        end_run_budget()

    begin_run_budget(1, plans_per_pass=1)
    try:
        for index in range(8):
            consume_diagnosis(f"diagnosis{index}")
        with pytest.raises(BudgetExhausted, match="diagnosis budget"):
            consume_diagnosis("second")
    finally:
        end_run_budget()


def test_empty_plan_is_rejected_without_game_mutation(monkeypatch) -> None:
    def fail_build(*_args, **_kwargs):
        raise AssertionError("empty plan reached Factorio")

    monkeypatch.setattr("orchestrator.stage_services.clear_plan_clutter", lambda *_a: 0)
    monkeypatch.setattr("orchestrator.stage_services.assert_affordable", lambda *_a: None)
    bridge = SimpleNamespace(build_layout=fail_build)
    plan = {"phases": [{"name": "empty", "actions": []}]}
    with pytest.raises(StuckError, match="zero actions"):
        _submit(object(), bridge, "nauvis", plan, "empty", lambda _message: None)


def test_zero_placement_execution_is_not_treated_as_success(monkeypatch) -> None:
    monkeypatch.setattr("orchestrator.stage_services.clear_plan_clutter", lambda *_a: 0)
    monkeypatch.setattr("orchestrator.stage_services.assert_affordable", lambda *_a: None)
    report = {
        "ok": True, "attempted_placements": 0, "succeeded_placements": 0,
        "placed_ghosts": 0, "placed_entities": 0,
    }
    bridge = SimpleNamespace(build_layout=lambda *_a: report)
    monkeypatch.setattr("orchestrator.stage_services.load_json", lambda value: value)
    plan = {"phases": [{"name": "p", "actions": [
        {"action_type": "place_entity", "entity": "medium-electric-pole",
         "position": {"x": 0, "y": 0}},
    ]}]}
    with pytest.raises(StuckError, match="zero placements"):
        _submit(object(), bridge, "nauvis", plan, "empty", lambda _message: None)


def test_successful_plans_record_exact_pending_footprints(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr("orchestrator.stage_services.clear_plan_clutter", lambda *_a: 0)
    monkeypatch.setattr("orchestrator.stage_services.assert_affordable", lambda *_a: None)
    monkeypatch.setattr("orchestrator.stage_services.load_json", lambda value: value)
    report = {
        "ok": True, "attempted_placements": 1, "succeeded_placements": 1,
        "placed_ghosts": 1, "placed_entities": 0,
    }
    bridge = SimpleNamespace(build_layout=lambda *_a: report, script_output=tmp_path)
    plan = {"phases": [{"name": "p", "actions": [
        {"action_type": "place_ghost", "entity": "substation",
         "position": {"x": 10, "y": 10}},
    ]}]}
    _submit(object(), bridge, "nauvis", plan, "pending", lambda _message: None)
    reservations = (
        tmp_path / "logs" / "deterministic-plan-reservations.jsonl"
    ).read_text()
    assert '"name":"pending"' in reservations
    assert "[9,9]" in reservations and "[10,10]" in reservations


def test_full_output_and_intentional_gating_are_not_repaired(monkeypatch) -> None:
    existing = SimpleNamespace(
        machine_count=1,
        machine_positions=[(10.0, 10.0)],
        working_count=0,
        output_position=(14.0, 10.0),
    )
    plan = SimpleNamespace(existing=existing)

    def fail_repair(*_args, **_kwargs):
        raise AssertionError("healthy saturation entered structural repair")

    monkeypatch.setattr(builder, "bring_stage_up", fail_repair)
    monkeypatch.setattr(
        builder.live_base,
        "entity_statuses",
        lambda *_a: {(10.0, 10.0): "full_output"},
    )
    monkeypatch.setattr(
        builder.live_base,
        "nearest_container",
        lambda *_a, **_k: (13.0, 10.0),
    )
    result = builder._repair_stalled_line(
        object(), object(), "nauvis", "player", "pipe",
        (0.0, 0.0), lambda _message: None, plan, mall_provider=None,
        upgrade_bootstrap=False,
    )
    assert result == (13.0, 10.0)


@pytest.mark.parametrize("held,expected", [(-1, True), (0, False), (4, False)])
def test_only_missing_feed_chest_requires_rebuild(held: int, expected: bool) -> None:
    calls = {"locate": False}

    class FakeClient:
        pass

    monkey_local = SimpleNamespace()
    monkey_local.located = None
    original_locate = mall_builder.locate_mall_cell
    mall_builder.locate_mall_cell = lambda *_a: ((40, 40), "left")
    original_chest = mall_builder.live_base.chest_stored_items
    mall_builder.live_base.chest_stored_items = lambda *_a: held
    try:
        assert mall_builder.mall_cell_needs_rebuild(
            FakeClient(), "nauvis", "pipe", (46.5, 43.5), (3.0, -1.0),
        ) is expected
    finally:
        mall_builder.locate_mall_cell = original_locate
        mall_builder.live_base.chest_stored_items = original_chest
    del calls, monkey_local


def _manifest(tmp_path: Path, *, isolated_hash: str) -> Path:
    source = tmp_path / "source.zip"
    isolated = tmp_path / "isolated.zip"
    source.write_bytes(b"source")
    isolated.write_bytes(bytes.fromhex(isolated_hash) if False else b"isolated")
    import hashlib
    path = tmp_path / "episode.json"
    path.write_text(json.dumps({
        "episode_id": "episode-test",
        "target_technology": "mining-productivity-4",
        "surface": "nauvis",
        "force": "player",
        "source_save": str(source),
        "source_save_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "isolated_save": str(isolated),
        "isolated_save_sha256": hashlib.sha256(isolated.read_bytes()).hexdigest(),
        "baseline_world_fingerprint": f"sha256:{hashlib.sha256(isolated.read_bytes()).hexdigest()}",
        "repository_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True,
        ).strip(),
        "deployed_factorio_mod_sha256": _directory_hash(REPO_ROOT / "factorio_mod"),
        "deployed_factorio_training_lab_sha256": _directory_hash(REPO_ROOT / "factorio_training_lab"),
    }), encoding="utf-8")
    return path


def test_directory_hash_matches_coreutils_tree_contract(tmp_path: Path) -> None:
    root = tmp_path / "mod"
    (root / "nested").mkdir(parents=True)
    (root / "b.txt").write_bytes(b"second")
    (root / "a.txt").write_bytes(b"first")
    (root / "nested" / "c.txt").write_bytes(b"third")
    # Locale collation ignores punctuation, so these two names reorder
    # between C-byte order and UTF-8 collation; they guard the contract.
    (root / "data-updates.lua").write_bytes(b"updates")
    (root / "data.lua").write_bytes(b"data")
    expected = subprocess.check_output(
        [
            "bash", "-lc",
            "cd \"$1\" && find . -type f -print0 | LC_ALL=C sort -z | "
            "xargs -0 sha256sum | sha256sum | awk '{print $1}'",
            "bash", str(root),
        ],
        text=True,
    ).strip()
    assert _directory_hash(root) == expected


def test_deployed_mod_drift_fails_closed(tmp_path: Path) -> None:
    path = _manifest(tmp_path, isolated_hash="0" * 64)
    payload = json.loads(path.read_text())
    payload["deployed_factorio_mod_sha256"] = "0" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(StuckError, match="deployed mod hash differs"):
        _validate_episode_manifest(path)


def test_verified_manifest_returns_episode_identity_for_managed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    hashes = {
        "factorio_cursor_rl_agent": "a" * 64,
        "factorio_training_lab": "b" * 64,
    }
    path = _manifest(tmp_path, isolated_hash="0" * 64)
    payload = json.loads(path.read_text())

    payload["deployed_factorio_mod_sha256"] = hashes["factorio_cursor_rl_agent"]
    payload["deployed_factorio_training_lab_sha256"] = hashes[
        "factorio_training_lab"
    ]
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(
        "tools.autonomous_run._directory_hash", lambda root: hashes[root.name],
    )

    assert _validate_episode_manifest(path) == payload["episode_id"]
    assert _validate_episode_manifest(None) is None


def test_changed_source_save_fails_closed(tmp_path: Path) -> None:
    path = _manifest(tmp_path, isolated_hash="0" * 64)
    payload = json.loads(path.read_text())
    Path(payload["source_save"]).write_bytes(b"changed")
    with pytest.raises(StuckError, match="source-save SHA-256 changed"):
        _validate_episode_manifest(path)


def test_dirty_or_changed_isolated_save_fails_closed(tmp_path: Path) -> None:
    path = _manifest(tmp_path, isolated_hash="0" * 64)
    payload = json.loads(path.read_text())
    Path(payload["isolated_save"]).write_bytes(b"dirty")
    with pytest.raises(StuckError, match="isolated save does not match"):
        _validate_episode_manifest(path)


def test_baseline_verification_is_recorded_atomically(tmp_path: Path) -> None:
    path = _manifest(tmp_path, isolated_hash="0" * 64)
    _patch_episode_manifest(
        path, baseline_verified=True, initial_game_tick=1200,
        started_at="start", ended_at="end", termination_reason="completed",
    )
    payload = json.loads(path.read_text())
    assert payload["baseline_verified"] is True
    assert payload["initial_game_tick"] == 1200
    assert payload["termination_reason"] == "completed"
