# Path: tests/test_layout_receipt_bridge.py | Purpose: Validate correlated layout responses without contacting Factorio.
import hashlib
import json

import pytest

from orchestrator.game_bridge import BridgeError, GameBridge, LAYOUT_REPORT_SUBDIR

AUTH = {"approved_actions": []}
PLAN = {"phases": [{"name": "test", "actions": [
    {"action_type": "place_ghost"}, {"action_type": "place_ghost_no_reconcile"}]}]}


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    monkeypatch.setattr("orchestrator.game_bridge.RconClient", lambda *_args, **_kwargs: object())
    return GameBridge(tmp_path, poll_interval=0)


def collect(bridge, monkeypatch, mutate=lambda report: None, *, stream_id=None, sequence=None):
    def command(text):
        body = json.loads(text.split(" ", 1)[1])
        canonical = json.dumps({"authorization": body["authorization"], "build_plan": body["build_plan"]},
                               sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        assert body["request_digest"] == hashlib.sha256(canonical.encode("ascii")).hexdigest()
        report = {"receipt_version": "layout-submission-receipt-v1", "request_id": body["request_id"],
                  "request_digest": body["request_digest"], "tick": 17, "ok": True,
                  "attempted_placements": 2, "succeeded_placements": 1, "already_present_placements": 1,
                  "failed_placements": 0, "placement_failures": []}
        if "stream_id" in body:
            report.update(receipt_version="layout-submission-receipt-v2",
                          stream_id=body["stream_id"], sequence=body["sequence"])
        mutate(report)
        directory = bridge.script_output / LAYOUT_REPORT_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"layout_{body['request_id']}.json").write_text(json.dumps(report))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    request_id = f"{stream_id}{sequence:016x}" if stream_id is not None else "a" * 32
    return bridge.build_layout_correlated(AUTH, PLAN, request_id=request_id, timeout=.1,
                                          stream_id=stream_id, sequence=sequence)


def test_receipt_is_correlated_and_counts_are_validated(bridge, monkeypatch):
    report = collect(bridge, monkeypatch)
    assert report["request_id"] == "a" * 32 and report["ok"] is True
    assert report["attempted_placements"] == 2


def test_expected_count_matches_executor_configuration_and_removal_semantics():
    plan = {"phases": [{"actions": [
        {"action_type": "place_ghost"}, {"action_type": "configure_entity_if_unit_number"},
        {"action_type": "configure_entity"}, {"action_type": "remove_entity"}]}]}
    _, _, expected, _ = GameBridge._layout_receipt_request({}, plan, "a" * 32)
    assert expected == 3


def test_stream_receipt_is_correlated_and_can_be_read(bridge, monkeypatch):
    stream_id, sequence = "c" * 16, 130
    report = collect(bridge, monkeypatch, stream_id=stream_id, sequence=sequence)
    assert report["stream_id"] == stream_id and report["sequence"] == sequence
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    assert bridge.read_layout_receipt(AUTH, PLAN, request_id=f"{stream_id}{sequence:016x}",
                                      stream_id=stream_id, sequence=sequence) == report


@pytest.mark.parametrize("stream_id,sequence,request_id", [
    ("c" * 16, True, "c" * 16 + "0" * 15 + "1"),
    ("c" * 16, 0, "c" * 32), ("c" * 16, 4294967296, "c" * 32),
    ("c" * 16, 1, "a" * 32), ("C" * 16, 1, "a" * 32),
    (None, 1, "a" * 32), ("c" * 16, None, "a" * 32),
])
def test_invalid_stream_identity_is_rejected_before_submission(bridge, monkeypatch, stream_id, sequence, request_id):
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(ValueError):
        bridge.build_layout_correlated(AUTH, PLAN, request_id=request_id, stream_id=stream_id, sequence=sequence)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(request_id="c" * 32),
    lambda r: r.update(request_digest="c" * 64),
    lambda r: r.update(receipt_version="other"),
    lambda r: r.update(tick=True),
    lambda r: r.update(ok="true"),
    lambda r: r.update(failed_placements=1),
    lambda r: r.update(attempted_placements=3),
    lambda r: r.update(attempted_placements=0, succeeded_placements=0, already_present_placements=0),
    lambda r: r.update(succeeded_placements=True),
    lambda r: r.update(placement_failures={}),
    lambda r: r.update(ok=False),
])
def test_mismatched_or_malformed_receipt_is_rejected(bridge, monkeypatch, mutate):
    with pytest.raises(BridgeError):
        collect(bridge, monkeypatch, mutate)


def test_failure_receipt_preserves_unknown_or_failed_result(bridge, monkeypatch):
    report = collect(bridge, monkeypatch, lambda r: r.update(ok=False, error="world check failed"))
    assert report["ok"] is False and report["error"] == "world check failed"


def test_existing_receipt_is_not_overwritten_or_resubmitted(bridge, monkeypatch):
    directory = bridge.script_output / LAYOUT_REPORT_SUBDIR
    directory.mkdir(parents=True)
    receipt = directory / f"layout_{'a' * 32}.json"
    receipt.write_text("{}")
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(BridgeError, match="already exists"):
        bridge.build_layout_correlated({}, {"phases": []}, request_id="a" * 32)
    assert receipt.read_text() == "{}"


def test_existing_receipt_can_be_read_without_resubmitting(bridge, monkeypatch):
    expected = collect(bridge, monkeypatch)
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    assert bridge.read_layout_receipt(AUTH, PLAN, request_id="a" * 32) == expected
    different = {"phases": [{"name": "test", "actions": [
        {"action_type": "place_entity"}, {"action_type": "place_ghost_no_reconcile"}]}]}
    with pytest.raises(BridgeError, match="correlation"):
        bridge.read_layout_receipt(AUTH, different, request_id="a" * 32)


def test_missing_or_symlinked_receipt_remains_unknown(bridge, monkeypatch, tmp_path):
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(BridgeError, match="outcome is unknown"):
        bridge.read_layout_receipt(AUTH, PLAN, request_id="a" * 32)
    directory = bridge.script_output / LAYOUT_REPORT_SUBDIR
    directory.mkdir(parents=True)
    target = tmp_path / "foreign.json"
    target.write_text("{}")
    (directory / f"layout_{'a' * 32}.json").symlink_to(target)
    with pytest.raises(BridgeError, match="outcome is unknown"):
        bridge.read_layout_receipt(AUTH, PLAN, request_id="a" * 32)


@pytest.mark.parametrize("request_id", ["../unsafe", "a" * 32 + "\n", "A" * 32])
def test_invalid_request_id_is_rejected_before_submission(bridge, monkeypatch, request_id):
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(ValueError):
        bridge.build_layout_correlated({}, {}, request_id=request_id)


TAGS = {"optimizer_episode": "episode", "optimizer_project": "project",
        "optimizer_transaction": "a" * 32, "optimizer_plan_hash": "b" * 64}
GHOST = {"action_type": "place_ghost_no_reconcile", "entity": "pipe", "position": {"x": 0.5, "y": 0.5}}
LAB_TAGS = {"research_lab_episode": "e", "research_lab_packet": "p", "research_lab_transaction": "a" * 32}


def shared_plan():
    return {"surface": "training/oil", "force": "training-oil", "atomic": True, "optimizer_ownership": dict(TAGS),
            "phases": [{"name": "one", "actions": [dict(GHOST)]},
                       {"name": "two", "actions": [{**GHOST, "position": {"x": 2.5, "y": 0.5}}]}]}


def test_shared_tags_add_protocol_and_keep_ghost_count(bridge, monkeypatch):
    sent = []
    def command(text):
        sent.append(json.loads(text.split(" ", 1)[1]))
        body = sent[-1]
        directory = bridge.script_output / LAYOUT_REPORT_SUBDIR
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"layout_{body['request_id']}.json").write_text(json.dumps({
            "receipt_version": "layout-submission-receipt-v1", "request_id": body["request_id"],
            "request_digest": body["request_digest"], "tick": 3, "ok": True, "attempted_placements": 2,
            "succeeded_placements": 2, "already_present_placements": 0, "failed_placements": 0,
            "placement_failures": []}))
        return ""
    monkeypatch.setattr(bridge, "command", command)
    report = bridge.build_layout_correlated(AUTH, shared_plan(), request_id="a" * 32, timeout=.1)
    assert report["ok"] is True and len(sent) == 1
    assert sent[0]["optimizer_tag_protocol"] == "optimizer-shared-tags-v1"
    assert sent[0]["build_plan"] == shared_plan()


def _set(path, value):
    def mutate(plan):
        *parents, last = path
        target = plan
        for key in parents:
            target = target[key]
        target[last] = value
    return mutate


def _drop(key):
    return lambda plan: plan.pop(key)


@pytest.mark.parametrize("mutate", [
    _set(("phases", 0, "actions", 0, "optimizer_ownership"), TAGS),
    _set(("phases", 0, "actions", 0, "research_lab_ownership"), LAB_TAGS),
    _set(("phases", 0, "actions", 0, "action_type"), "place_ghost"),
    _set(("phases", 0, "actions", 0, "action_type"), "configure_entity"),
    _set(("phases", 0, "actions", 0, "action_type"), "remove_entity"),
    _set(("phases", 0, "actions", 0, "action_type"), "place_entity"),
    _set(("phases", 0, "actions", 0, "action_type"), "place_tile_ghost"),
    _set(("atomic",), False), _set(("atomic",), 1), _drop("atomic"),
    _drop("surface"), _drop("force"), _set(("surface",), ""), _set(("force",), 7),
    _set(("phases",), []), _set(("phases", 1, "actions"), []), _set(("phases",), {"one": []}),
    _set(("phases", 0, "actions"), {"0": GHOST}),
    _set(("optimizer_ownership",), None), _set(("optimizer_ownership",), [TAGS]),
    _set(("optimizer_ownership",), "tags"),
    _set(("optimizer_ownership", "extra"), "x"),
    _set(("optimizer_ownership", "optimizer_plan_hash"), "B" * 64),
    _set(("optimizer_ownership", "optimizer_transaction"), "a" * 31 + "\n"),
    _set(("optimizer_ownership", "optimizer_episode"), 5),
    _set(("optimizer_ownership", "optimizer_project"), ""),
    lambda plan: plan["optimizer_ownership"].pop("optimizer_project"),
])
def test_malformed_shared_tag_plan_is_rejected_before_submission(bridge, monkeypatch, mutate):
    plan = shared_plan()
    mutate(plan)
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(ValueError, match="BuildPlan validation FAILED"):
        bridge.build_layout_correlated(AUTH, plan, request_id="a" * 32, timeout=.1)
    with pytest.raises(ValueError, match="BuildPlan validation FAILED"):
        bridge.read_layout_receipt(AUTH, plan, request_id="a" * 32)


def test_shared_tags_are_refused_on_uncorrelated_paths(bridge, monkeypatch):
    monkeypatch.setattr(bridge, "command", lambda _text: pytest.fail("unexpected submission"))
    with pytest.raises(ValueError, match="correlated"):
        bridge.build_layout(AUTH, shared_plan())
    with pytest.raises(ValueError, match="correlated"):
        bridge.execute_ghost_plan(AUTH, shared_plan())


@pytest.mark.parametrize("form", ["shared", "per_action"])
def test_optimizer_names_obey_lua_utf8_byte_limit_before_submission(form):
    plan = shared_plan()
    tags = plan["optimizer_ownership"]
    if form == "per_action":
        del plan["optimizer_ownership"]
        plan["phases"][0]["actions"][0]["optimizer_ownership"] = tags
    tags["optimizer_project"] = "é" * 64
    GameBridge._layout_receipt_request(AUTH, plan, "a" * 32)
    tags["optimizer_project"] = "é" * 65
    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        GameBridge._layout_receipt_request(AUTH, plan, "a" * 32)
    tags["optimizer_project"] = "\ud800"
    with pytest.raises(ValueError, match="valid UTF-8"):
        GameBridge._layout_receipt_request(AUTH, plan, "a" * 32)
