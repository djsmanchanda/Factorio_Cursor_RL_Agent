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
from orchestrator.stage_services import (
    StuckError, _ghost_materials, _ghostify_direct_infrastructure, _submit,
)
from orchestrator.parts_mall import MaterialShortage
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


def test_observed_progress_refunds_the_control_pass_limit() -> None:
    budget = begin_run_budget(2)
    try:
        budget.begin_pass()
        budget.credit_progress_pass()

        assert budget.passes == 0
        assert budget.progress_credits == 1
        budget.begin_pass()
        budget.begin_pass()
        with pytest.raises(BudgetExhausted, match="pass budget"):
            budget.begin_pass()
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


def test_submit_ghosts_and_bills_every_power_and_coverage_entity(
    monkeypatch,
) -> None:
    """The deterministic executor may not gift infrastructure to the base."""
    plan = {"surface": "nauvis", "force": "player", "phases": [{
        "name": "service",
        "actions": [
            {"action_type": "place_entity", "entity": entity,
             "position": {"x": index * 5.0, "y": 0.0}}
            for index, entity in enumerate((
                "small-electric-pole", "medium-electric-pole",
                "big-electric-pole", "substation", "roboport",
                "passive-provider-chest",
            ))
        ],
    }]}
    bills: list[dict[str, int]] = []
    submitted: list[dict] = []
    monkeypatch.setattr(
        "orchestrator.stage_services.clear_plan_clutter", lambda *_a: 0,
    )
    monkeypatch.setattr(
        "orchestrator.stage_services.assert_affordable",
        lambda _c, _s, _f, candidate, *_a, **_k:
            bills.append(_ghost_materials(candidate)),
    )
    monkeypatch.setattr(
        "orchestrator.stage_services.load_json", lambda value: value,
    )
    bridge = SimpleNamespace(build_layout=lambda _authorization, candidate: (
        submitted.append(candidate) or {
            "ok": True, "attempted_placements": 6,
            "succeeded_placements": 6, "placed_ghosts": 6,
            "placed_entities": 0,
        }
    ))

    _submit(object(), bridge, "nauvis", plan, "service", lambda _message: None)

    infrastructure = set((
        "small-electric-pole", "medium-electric-pole",
        "big-electric-pole", "substation", "roboport",
    ))
    actions = submitted[0]["phases"][0]["actions"]
    assert all(
        action["action_type"] == "place_ghost"
        for action in actions if action["entity"] in infrastructure
    )
    assert actions[-1]["action_type"] == "place_ghost"
    assert bills == [{entity: 1 for entity in sorted(infrastructure | {"passive-provider-chest"})}]


def test_infrastructure_ghostification_reports_only_former_direct_actions() -> None:
    plan = {"phases": [{"actions": [
        {"action_type": "place_entity", "entity": "substation",
         "position": {"x": 4, "y": 5}},
        {"action_type": "place_ghost", "entity": "roboport",
         "position": {"x": 40, "y": 5}},
    ]}]}

    converted = _ghostify_direct_infrastructure(plan)

    assert converted == (("substation", (4.0, 5.0)),)
    assert [
        action["action_type"] for action in plan["phases"][0]["actions"]
    ] == ["place_ghost", "place_ghost"]


def test_submit_waits_for_a_formerly_direct_pole_to_be_bot_built(
    monkeypatch,
) -> None:
    from orchestrator import stage_services

    plan = {"surface": "nauvis", "force": "player", "phases": [{
        "actions": [{"action_type": "place_entity",
                     "entity": "medium-electric-pole",
                     "position": {"x": 10.5, "y": 20.5}}],
    }]}
    observations = iter((
        {(10.5, 20.5): "entity-ghost"},
        {(10.5, 20.5): "medium-electric-pole"},
    ))
    waits: list[str] = []
    monkeypatch.setattr(stage_services, "clear_plan_clutter", lambda *_a: 0)
    monkeypatch.setattr(stage_services, "assert_affordable", lambda *_a, **_k: None)
    monkeypatch.setattr(stage_services, "load_json", lambda value: value)
    monkeypatch.setattr(
        stage_services.live_base, "entity_names_at", lambda *_a: next(observations),
    )
    monkeypatch.setattr(stage_services, "consume_wait", waits.append)
    monkeypatch.setattr(stage_services.time, "sleep", lambda _seconds: None)
    client = SimpleNamespace(command=lambda _command: "")
    bridge = SimpleNamespace(build_layout=lambda *_a: {
        "ok": True, "attempted_placements": 1, "succeeded_placements": 1,
        "placed_ghosts": 1, "placed_entities": 0,
    })

    _submit(client, bridge, "nauvis", plan, "pole", lambda _message: None)

    assert waits == ["bot_built_infrastructure"]


def test_infrastructure_wait_returns_an_unbacked_pole_bill_to_the_mall(
    monkeypatch,
) -> None:
    """A legal reachable ghost must not hold the controller for five minutes
    when the network has no item and no producer can replenish it."""
    from orchestrator import stage_services

    clock = [0.0]
    messages: list[str] = []
    monkeypatch.setattr(stage_services.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        stage_services.time, "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_names_at",
        lambda *_a: {(57.5, 46.5): "entity-ghost"},
    )
    monkeypatch.setattr(
        stage_services.live_base, "ghost_blockages", lambda *_a: [{
            "position": (57.5, 46.5),
            "entity": "medium-electric-pole",
            "reason": "missing_material:medium-electric-pole:1:0",
            "item": "medium-electric-pole",
            "required": 1,
            "network_item_count": 0,
            "network_id": 2,
            "construction_robots": 50,
            "available_construction_robots": 50,
            "can_revive": True,
        }],
    )
    monkeypatch.setattr(
        stage_services, "construction_supply_chain_is_scheduled",
        lambda *_a: False,
    )

    with pytest.raises(MaterialShortage) as caught:
        stage_services._await_bot_built_infrastructure(
            SimpleNamespace(command=lambda *_a: ""),
            "nauvis", "player",
            (("medium-electric-pole", (57.5, 46.5)),),
            messages.append, owner="power_bridge",
        )

    assert clock[0] == stage_services._INFRASTRUCTURE_DIAGNOSIS_SECONDS
    assert caught.value.required == {"medium-electric-pole": 1}
    assert caught.value.available == {"medium-electric-pole": 0}
    assert messages == [
        "  INFRASTRUCTURE SUPPLY WAIT: power_bridge has 1 pending "
        "medium-electric-pole ghost(s) with no live supply; returning the "
        "bill to the mall"
    ]


def test_recovered_post_starter_pole_shortage_builds_a_complete_stack(
    monkeypatch,
) -> None:
    observed_targets: list[int] = []
    monkeypatch.setattr(
        builder, "_metal_starter_transition_complete", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder, "ITEM_STACK_SIZES", {"medium-electric-pole": 50},
    )
    monkeypatch.setitem(
        builder.LINE_RECIPES, "medium-electric-pole",
        {"machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder, "_rationed_mall_batch",
        lambda _c, _b, _s, _f, _item, target, *_a, **_k:
            observed_targets.append(target) or True,
    )

    ready, output = builder._ensure_mall_item(
        object(), object(), "nauvis", "player", "medium-electric-pole", 1,
        {}, (0.0, 0.0), lambda _message: None, background=False,
    )

    assert (ready, output) == (False, None)
    assert observed_targets == [50]


def test_infrastructure_timeout_carries_owner_and_live_ghost_diagnostics(
    monkeypatch,
) -> None:
    from orchestrator import stage_services

    clock = [0.0]
    blockage = {
        "position": (4.5, 5.5),
        "entity": "medium-electric-pole",
        "reason": "no_available_construction_robots",
        "network_id": 7,
        "construction_robots": 10,
        "available_construction_robots": 0,
        "can_revive": True,
    }
    monkeypatch.setattr(stage_services, "_INFRASTRUCTURE_BUILD_SECONDS", 12.0)
    monkeypatch.setattr(stage_services.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        stage_services.time, "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    monkeypatch.setattr(
        stage_services.live_base, "entity_names_at",
        lambda *_a: {(4.5, 5.5): "entity-ghost"},
    )
    monkeypatch.setattr(
        stage_services.live_base, "ghost_blockages", lambda *_a: [blockage],
    )

    with pytest.raises(StuckError) as caught:
        stage_services._await_bot_built_infrastructure(
            SimpleNamespace(command=lambda *_a: ""),
            "nauvis", "player",
            (("medium-electric-pole", (4.5, 5.5)),),
            lambda _message: None, owner="mall_power",
        )

    assert caught.value.details["owner"] == "mall_power"
    assert caught.value.details["diagnostics"] == [blockage]


def test_removal_only_plans_are_not_churn(monkeypatch) -> None:
    """Live run of 2026-08-24 15:16: retiring a starved bootstrap cell is a
    pure remove_entity plan; the executor counts only place actions, so the
    zero-placement guard killed the run AFTER the removals had already run.
    Removal-only plans are governed by the report's ok status alone."""
    monkeypatch.setattr("orchestrator.stage_services.clear_plan_clutter", lambda *_a: 0)
    monkeypatch.setattr("orchestrator.stage_services.assert_affordable", lambda *_a: None)
    report = {
        "ok": True, "attempted_placements": 0, "succeeded_placements": 0,
        "placed_ghosts": 0, "placed_entities": 0, "removed_entities": 6,
    }
    bridge = SimpleNamespace(build_layout=lambda *_a: report)
    monkeypatch.setattr("orchestrator.stage_services.load_json", lambda value: value)
    plan = {"phases": [{"name": "retire", "actions": [
        {"action_type": "remove_entity", "entity": "stone-furnace",
         "position": {"x": 145.5, "y": -77.5}},
    ]}]}

    result = _submit(object(), bridge, "nauvis", plan, "retire", lambda _m: None)

    assert result["removed_entities"] == 6


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


def test_observed_progress_refunds_every_run_total_budget() -> None:
    """2026-09-04: a productive 3218s run died on the wait budget while
    chaining power to oil. Progress refunds one of each counter; idle
    counters floor at zero instead of going negative."""
    budget = begin_run_budget(100)
    try:
        budget.begin_pass()
        for _ in range(5):
            consume_wait("settle")
        consume_plan_submission("plan")
        consume_remediation("fix")
        consume_diagnosis("probe")
        assert budget.waits == 5

        budget.credit_progress_pass()

        assert budget.waits == 4
        assert budget.plans == 0
        assert budget.remediations == 0
        assert budget.diagnoses == 0
        assert budget.passes == 0
    finally:
        end_run_budget()


def test_unproductive_spinning_still_exhausts_every_budget() -> None:
    """Refunds only flow on progress: a pure spin still trips each bound."""
    budget = begin_run_budget(1, plans_per_pass=1)
    try:
        for index in range(4):
            consume_wait(f"wait{index}")
        with pytest.raises(BudgetExhausted, match="wait budget"):
            consume_wait("last")
    finally:
        end_run_budget()


def test_productive_pass_refunds_every_plan_spent_within_it() -> None:
    """2026-09-05: 2200s of fast-inserter/belt rotation submitted 2-4
    configure-only plans per productive pass and bled out against the old
    1-plan refund. Productive passes go plan-net-zero; banked unproductive
    spend is kept, not forgiven."""
    budget = begin_run_budget(100)
    try:
        # Banked unproductive spend first: no credit, it stays.
        budget.begin_pass()
        consume_plan_submission("stale-a")
        consume_plan_submission("stale-b")
        assert budget.plans == 2

        # A productive rotation pass spends several plans and is refunded
        # to the pass-start mark, not below it.
        budget.begin_pass()
        consume_plan_submission("borrow")
        consume_plan_submission("restore")
        consume_plan_submission("rebind")
        assert budget.plans == 5
        budget.credit_progress_pass()
        assert budget.plans == 2
    finally:
        end_run_budget()


def test_unproductive_plan_spend_still_exhausts_the_budget() -> None:
    """The pass-start refund must not launder pure spinning: passes without
    credit keep every submission until the bound fires."""
    budget = begin_run_budget(1, plans_per_pass=2)
    try:
        budget.begin_pass()
        consume_plan_submission("first")
        consume_plan_submission("second")
        with pytest.raises(BudgetExhausted, match="plan submission budget"):
            consume_plan_submission("third")
    finally:
        end_run_budget()


@pytest.mark.parametrize("entity", ["requester-chest", "passive-provider-chest", "steel-chest", "inserter", "fast-inserter", "assembling-machine-1", "pipe", "transport-belt"])
def test_all_legacy_construction_is_ghosted_and_billed(entity):
    plan = {"phases": [{"actions": [{"action_type": "place_entity", "entity": entity, "position": {"x": 10, "y": 20}}]}]}
    assert _ghostify_direct_infrastructure(plan) == ((entity, (10.0, 20.0)),)
    assert _ghost_materials(plan) == {entity: 1}


def test_sandbox_supply_cannot_enter_deterministic_submit():
    plan = {"phases": [{"actions": [{"action_type": "place_entity", "entity": "infinity-chest", "position": {"x": 0, "y": 0}}]}]}
    with pytest.raises(StuckError, match="sandbox supply"):
        _submit(object(), object(), "nauvis", plan, "free-input", lambda _: None)



def test_existing_direct_chest_is_configuration_without_new_material(monkeypatch):
    from orchestrator import stage_services as services
    plan = {"phases": [{"actions": [{"action_type": "place_entity", "entity": "requester-chest", "position": {"x": 1, "y": 2}}]}]}
    monkeypatch.setattr(services.live_base, 'entity_names_at', lambda *_: {(1, 2): 'requester-chest'})
    monkeypatch.setattr(services, 'clear_plan_clutter', lambda *_: None)
    bills = []
    monkeypatch.setattr(services, 'assert_affordable', lambda _c, _s, _f, p, *args: bills.append(_ghost_materials(p)))
    monkeypatch.setattr(services, 'load_json', lambda p: p)
    submitted = []
    bridge = SimpleNamespace(build_layout=lambda _a, p: submitted.append(p) or {'ok': True})
    _submit(SimpleNamespace(command=lambda _: ''), bridge, 'nauvis', plan, 'refresh', lambda _: None)
    assert bills == [{}]
    assert submitted[0]['phases'][0]['actions'][0]['action_type'] == 'configure_entity'
    assert plan['phases'][0]['actions'][0]['action_type'] == 'place_entity'



def test_new_chest_settings_wait_for_real_bot_construction(monkeypatch):
    from orchestrator import stage_services as services
    plan = {"phases": [{"actions": [{"action_type": "place_entity", "entity": "requester-chest", "position": {"x": 1, "y": 2}, "logistic_requests": [{"name": "iron-plate", "count": 5}]}]}]}
    monkeypatch.setattr(services, 'clear_plan_clutter', lambda *_: None)
    monkeypatch.setattr(services, 'assert_affordable', lambda *_a, **_k: None)
    monkeypatch.setattr(services, 'load_json', lambda p: p)
    sequence = []
    monkeypatch.setattr(services, '_await_bot_built_infrastructure', lambda *a, **k: sequence.append('wait') if a[3] else None)
    def build(_auth, p):
        sequence.append(p['phases'][0]['actions'][0])
        return {'ok': True, 'attempted_placements': 1, 'succeeded_placements': 1, 'placed_ghosts': 1, 'placed_entities': 0}
    _submit(object(), SimpleNamespace(build_layout=build), 'nauvis', plan, 'new-chest', lambda _: None)
    assert sequence[0]['action_type'] == 'place_ghost'
    assert 'logistic_requests' not in sequence[0]
    assert sequence[1] == 'wait'
    assert sequence[2]['action_type'] == 'configure_entity'
    assert sequence[2]['logistic_requests'] == [{'name': 'iron-plate', 'count': 5}]
