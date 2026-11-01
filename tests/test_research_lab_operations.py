# Path: tests/test_research_lab_operations.py | Purpose: Protect lab baseline evidence, recovery identity and funded configuration boundaries.
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

import orchestrator.research_operations as operation
from planners.research_labs import logistic_lab_row

PACK = "automation-science-pack"
SECOND_PACK = "chemical-science-pack"
NOW = 1000
FRESH_TICK = 900
TRANSACTION = "a" * 32


class FakeBridge:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def science_status(self, surface, force):
        self.calls += 1
        if isinstance(self.status, Exception):
            raise self.status
        return self.status


def science_status(
    tick=FRESH_TICK, *, total=0, surface="nauvis", force="player",
    ok=True, version="1.0.0",
):
    return {
        "schema_version": version,
        "ok": ok,
        "tick": tick,
        "surface": surface,
        "force": force,
        "labs": {"total": total, "working": total, "status_counts": {},
                 "input_inventory": {}, "entries": []},
    }


def prepare(tmp_path, monkeypatch, bridge):
    import orchestrator.research_operations as operation
    from orchestrator import autonomous_builder as ab
    from orchestrator.material_reservations import MaterialReservationLedger

    ledger = MaterialReservationLedger(
        tmp_path / "script-output", episode_id="labs", surface="nauvis", force="player")
    monkeypatch.setattr(ab, "_MATERIAL_RESERVATION_LEDGER", ledger)
    monkeypatch.setattr(operation, "load_json", lambda value: value)
    monkeypatch.setattr(ab.live_base, "game_tick", lambda client: NOW)
    calls = {"areas": [], "submitted": []}

    def find_clear_area(*args, **kwargs):
        calls["areas"].append((args, kwargs))
        return (20, 20)

    def submit(client, submitted_bridge, surface, plan, name, emit, **kwargs):
        calls["submitted"].append((name, copy.deepcopy(plan), kwargs))

    monkeypatch.setattr(ab.live_base, "find_clear_area", find_clear_area)
    monkeypatch.setattr(ab.live_base, "entity_signatures_at", lambda *a, **kw: {})
    monkeypatch.setattr(ab.live_base, "entity_unit_numbers_at", lambda *a, **kw: {})
    monkeypatch.setattr(ab, "_submit", submit)
    monkeypatch.setattr(
        "orchestrator.stage_services._await_bot_built_infrastructure", lambda *a, **kw: None)
    monkeypatch.setattr(ab, "extend_power", lambda *a, **kw: None)
    monkeypatch.setattr(ab, "ensure_logistic_coverage", lambda *a, **kw: None)
    session = SimpleNamespace(
        client=object(), bridge=bridge, reference_point=(0, 0), emit=lambda _: None)
    return operation, ledger, session, calls


def record_path(ledger):
    return ledger.path.with_suffix(".research-labs.json")


def lab_record(operation, *, packets=None, surface="nauvis", force="player",
               baseline_labs=2, baseline_tick=FRESH_TICK):
    return {
        "version": operation.LAB_RECORD_VERSION,
        "episode_id": "labs",
        "surface": surface,
        "force": force,
        "baseline_labs": baseline_labs,
        "baseline_tick": baseline_tick,
        "packets": [] if packets is None else packets,
    }


def lab_packet(count=2, *, packet_id="research_labs_0", complete=False,
               origin=(20, 20), surface="nauvis", force="player"):
    plan = logistic_lab_row(count, {PACK: 1}, origin=origin)
    plan.update(surface=surface, force=force)
    return {"count": count, "id": packet_id, "transaction_id": TRANSACTION, "complete": complete, "plan": plan}


def write_record(ledger, record):
    path = record_path(ledger)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, sort_keys=True))
    return path


def ownership_evidence(packet_id, actions, *, surface="nauvis", force="player",
                       tick=NOW, unit_base=700):
    records = []
    units = {}
    for index, action in enumerate(actions):
        position = (float(action["position"]["x"]), float(action["position"]["y"]))
        unit_number = unit_base + index
        units[position] = unit_number
        records.append({
            "entity": action["entity"],
            "position": {"x": position[0], "y": position[1]},
            "unit_number": unit_number,
            "tick": tick,
            "tags": {"research_lab_packet": packet_id, "research_lab_transaction": TRANSACTION},
        })
    evidence = {
        "version": operation.LAB_OWNERSHIP_EVIDENCE_VERSION,
        "source": operation.LAB_OWNERSHIP_EVIDENCE_SOURCE,
        "ok": True, "ownership_claimed": False, "dispatch_authorized": False,
        "transaction_id": TRANSACTION,
        "episode_id": "labs",
        "surface": surface,
        "force": force,
        "packet_id": packet_id,
        "tick": tick,
        "entities": records,
    }
    return evidence, units


def install_ownership(bridge, packet_id, actions, monkeypatch, **kwargs):
    import orchestrator.live_base as live_base
    evidence, units = ownership_evidence(packet_id, actions, **kwargs)
    def read_ownership(**request):
        result = copy.deepcopy(evidence)
        result["transaction_id"] = request["transaction_id"]
        for entry in result["entities"]:
            entry["tags"]["research_lab_transaction"] = request["transaction_id"]
        return result
    bridge.research_lab_ownership = read_ownership
    monkeypatch.setattr(
        live_base, "entity_unit_numbers_at", lambda *a, **kw: dict(units))
    return evidence, units


def test_build_labs_rejects_absent_science_status_before_planning(tmp_path, monkeypatch):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, object())

    with pytest.raises(ValueError, match="science_status"):
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert calls == {"areas": [], "submitted": []}
    assert not record_path(ledger).exists()


@pytest.mark.parametrize("status", [
    pytest.param(RuntimeError("report unavailable"), id="call-failed"),
    pytest.param(None, id="non-object"),
    pytest.param(science_status(ok=False), id="failed-report"),
    pytest.param(science_status(surface="vulcanus"), id="wrong-surface"),
    pytest.param(science_status(force="enemy"), id="wrong-force"),
    pytest.param(science_status(version="2.0.0"), id="wrong-schema"),
    pytest.param(science_status(tick=0), id="stale"),
    pytest.param(science_status(tick=NOW + 1), id="future"),
    pytest.param({**science_status(), "labs": None}, id="missing-labs"),
    pytest.param({**science_status(), "labs": {"total": -1}}, id="negative-labs"),
    pytest.param({**science_status(), "labs": {"total": True}}, id="bool-labs"),
    pytest.param({**science_status(), "labs": {"total": 1.5}}, id="float-labs"),
])
def test_build_labs_rejects_missing_or_stale_baseline_evidence(
    tmp_path, monkeypatch, status,
):
    operation, ledger, session, calls = prepare(
        tmp_path, monkeypatch, FakeBridge(status))

    with pytest.raises(ValueError):
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert calls == {"areas": [], "submitted": []}
    assert not record_path(ledger).exists()


def test_build_labs_persists_fresh_baseline_identity_and_funded_packet(
    tmp_path, monkeypatch,
):
    bridge = FakeBridge(science_status(total=2))
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, bridge)
    packs = {PACK: 1, SECOND_PACK: 2}
    install_ownership(
        bridge, "research_labs_0",
        logistic_lab_row(2, packs, origin=(21, 21))["phases"][0]["actions"],
        monkeypatch,
    )

    result = operation.build_labs(session, count=4, packs=packs)

    record = json.loads(record_path(ledger).read_text())
    assert bridge.calls == 1
    assert record["version"] == operation.LAB_RECORD_VERSION
    assert record["episode_id"] == "labs"
    assert record["surface"] == "nauvis"
    assert record["force"] == "player"
    assert record["baseline_labs"] == 2
    assert record["baseline_tick"] == FRESH_TICK
    assert [packet["count"] for packet in record["packets"]] == [2]
    assert result == {"ok": True, "code": "research_labs_submitted",
                      "target_count": 4, "committed": 2}
    assert len(calls["areas"]) == 1
    assert [entry[0] for entry in calls["submitted"]] == [
        "research_labs_0", "research_labs_0_requests"]
    build_name, build_plan, build_options = calls["submitted"][0]
    assert build_name == "research_labs_0"
    assert build_options["require_funded"] is True
    assert build_options["reservation_priority"] == 50
    assert sum(a["entity"] == "lab" for a in build_plan["phases"][0]["actions"]) == 2
    from orchestrator.extraction_transport import planned_footprint_tiles
    from orchestrator.material_reservations import plan_material_bill
    from orchestrator.stage_services import _ghost_materials
    from planners.plan_validation import validate_build_plan
    from planners.sandbox_infrastructure import build_layout_authorization
    original_plan = record["packets"][0]["plan"]
    validate_build_plan(build_plan)
    assert plan_material_bill(build_plan) == plan_material_bill(original_plan)
    assert _ghost_materials(build_plan) == plan_material_bill(build_plan)
    assert planned_footprint_tiles(build_plan) == planned_footprint_tiles(original_plan)
    assert build_layout_authorization([build_plan])["approved_actions"] == ["project_more_ghosts"]
    configure_plan = calls["submitted"][1][1]
    configuration = configure_plan["phases"][0]["actions"]
    assert all(
        action["action_type"] == "configure_entity_if_unit_number"
        and type(action["expected_unit_number"]) is int
        and action["entity"] == "requester-chest" for action in configuration)
    validate_build_plan(configure_plan)
    assert build_layout_authorization([configure_plan])["approved_actions"] == ["place_core_infrastructure"]
    missing_guard = copy.deepcopy(configure_plan)
    del missing_guard["phases"][0]["actions"][0]["expected_unit_number"]
    with pytest.raises(ValueError):
        validate_build_plan(missing_guard)
    assert {
        request["name"]
        for action in configuration
        for section in action["logistic_sections"]
        for request in section["requests"]
    } == set(packs)


def test_build_labs_accepts_evidence_observed_after_build_wait(tmp_path, monkeypatch):
    from orchestrator import live_base

    bridge = FakeBridge(science_status(total=2))
    operation, _ledger, session, calls = prepare(tmp_path, monkeypatch, bridge)
    clock = {"tick": NOW}
    monkeypatch.setattr(live_base, "game_tick", lambda client: clock["tick"])
    actions = logistic_lab_row(2, {PACK: 1}, origin=(21, 21))["phases"][0]["actions"]
    evidence, _units = install_ownership(bridge, "research_labs_0", actions, monkeypatch, tick=NOW + 8)

    def wait_for_bots(*args, **kwargs):
        clock["tick"] = NOW + 5

    def read_ownership(**_request):
        clock["tick"] = NOW + 10
        result = copy.deepcopy(evidence)
        result["transaction_id"] = _request["transaction_id"]
        for entry in result["entities"]:
            entry["tags"]["research_lab_transaction"] = _request["transaction_id"]
        return result

    monkeypatch.setattr("orchestrator.stage_services._await_bot_built_infrastructure", wait_for_bots)
    bridge.research_lab_ownership = read_ownership
    result = operation.build_labs(session, count=4, packs={PACK: 1})
    assert result["ok"] is True
    assert [name for name, _plan, _options in calls["submitted"]] == [
        "research_labs_0", "research_labs_0_requests"]


@pytest.mark.parametrize("tick", [FRESH_TICK - 1, NOW + 1])
def test_build_labs_rejects_ownership_outside_observed_ticks(tmp_path, monkeypatch, tick):
    bridge = FakeBridge(science_status(total=2))
    operation, _ledger, session, calls = prepare(tmp_path, monkeypatch, bridge)
    actions = logistic_lab_row(2, {PACK: 1}, origin=(21, 21))["phases"][0]["actions"]
    install_ownership(bridge, "research_labs_0", actions, monkeypatch, tick=tick)
    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})
    assert error.value.code == "research_lab_ownership_unknown"
    assert [name for name, _plan, _options in calls["submitted"]] == ["research_labs_0"]


def test_build_labs_rejects_unreadable_tick_after_ownership(tmp_path, monkeypatch):
    from orchestrator import live_base

    bridge = FakeBridge(science_status(total=2))
    operation, _ledger, session, calls = prepare(tmp_path, monkeypatch, bridge)
    actions = logistic_lab_row(2, {PACK: 1}, origin=(21, 21))["phases"][0]["actions"]
    install_ownership(bridge, "research_labs_0", actions, monkeypatch)
    ticks = iter([NOW, None])
    monkeypatch.setattr(live_base, "game_tick", lambda client: next(ticks))
    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})
    assert error.value.code == "research_lab_ownership_unknown"
    assert [name for name, _plan, _options in calls["submitted"]] == ["research_labs_0"]


def test_build_labs_recovers_owned_packet_without_new_baseline_or_site(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, SimpleNamespace())
    packet = lab_packet()
    write_record(ledger, lab_record(operation, packets=[packet]))
    install_ownership(
        session.bridge, packet["id"], packet["plan"]["phases"][0]["actions"],
        monkeypatch,
    )

    result = operation.build_labs(session, count=4, packs={PACK: 1})

    record = json.loads(record_path(ledger).read_text())
    assert result["committed"] == 2
    assert calls["areas"] == []
    assert [entry[0] for entry in calls["submitted"]] == [
        "research_labs_0", "research_labs_0_requests"]
    assert record["packets"][0]["complete"] is True
    assert record["packets"][0]["id"] == "research_labs_0"
    assert record["packets"][0]["transaction_id"] == TRANSACTION
    assert all(action["research_lab_ownership"]["research_lab_transaction"] == TRANSACTION
               for action in calls["submitted"][0][1]["phases"][0]["actions"])
    assert [
        (action["entity"], action["position"])
        for action in record["packets"][0]["plan"]["phases"][0]["actions"]
    ] == [
        (action["entity"], action["position"])
        for action in calls["submitted"][0][1]["phases"][0]["actions"]
    ]
    assert all(action["action_type"] == "place_ghost_no_reconcile"
               for action in calls["submitted"][0][1]["phases"][0]["actions"])
    assert all(
        "logistic_sections" not in action
        for action in calls["submitted"][0][1]["phases"][0]["actions"])
    assert all(
        action["action_type"] == "configure_entity_if_unit_number"
        for action in calls["submitted"][1][1]["phases"][0]["actions"])


def test_build_labs_rejects_malformed_packs_before_recovering_submission(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, object())
    write_record(ledger, lab_record(operation, packets=[lab_packet()]))

    with pytest.raises(ValueError):
        operation.build_labs(session, count=4, packs={PACK: True})

    assert calls == {"areas": [], "submitted": []}


def _invalid_records(operation):
    valid = lab_record(operation, packets=[lab_packet()])
    return [
        pytest.param([], id="not-an-object"),
        pytest.param({**valid, "version": 999}, id="wrong-version"),
        pytest.param({**valid, "version": 1}, id="legacy-version"),
        pytest.param({**valid, "episode_id": "other"}, id="wrong-episode"),
        pytest.param({**valid, "surface": "vulcanus"}, id="wrong-surface"),
        pytest.param({**valid, "force": "enemy"}, id="wrong-force"),
        pytest.param({k: v for k, v in valid.items() if k != "baseline_labs"},
                     id="missing-baseline"),
        pytest.param({**valid, "baseline_labs": -1}, id="negative-baseline"),
        pytest.param({**valid, "baseline_labs": True}, id="bool-baseline"),
        pytest.param({**valid, "baseline_labs": 1.5}, id="float-baseline"),
        pytest.param({k: v for k, v in valid.items() if k != "baseline_tick"},
                     id="missing-baseline-tick"),
        pytest.param({**valid, "baseline_tick": -1}, id="negative-baseline-tick"),
        pytest.param({**valid, "baseline_tick": True}, id="bool-baseline-tick"),
        pytest.param({k: v for k, v in valid.items() if k != "packets"},
                     id="missing-packets"),
        pytest.param({**valid, "packets": {}}, id="packets-not-list"),
        pytest.param({**valid, "packets": [None]}, id="packet-not-object"),
        pytest.param({**valid, "packets": [{k: v for k, v in lab_packet().items()
                                           if k != "transaction_id"}]}, id="missing-transaction"),
        pytest.param({**valid, "packets": [{**lab_packet(), "transaction_id": "A" * 32}]},
                     id="invalid-transaction"),
        pytest.param({**valid, "packets": [{**lab_packet(), "count": 0}]},
                     id="zero-count"),
        pytest.param({**valid, "packets": [{**lab_packet(), "count": 7}]},
                     id="oversize-count"),
        pytest.param({**valid, "packets": [{**lab_packet(), "count": True}]},
                     id="bool-count"),
        pytest.param({**valid, "packets": [{**lab_packet(), "id": ""}]},
                     id="blank-id"),
        pytest.param({**valid, "packets": [{**lab_packet(), "id": "research_labs_2"}]},
                     id="wrong-sequence"),
        pytest.param({**valid, "packets": [{**lab_packet(), "complete": "yes"}]},
                     id="non-bool-complete"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": None}]},
                     id="missing-plan"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"], "surface": "vulcanus"}}]}, id="plan-scope"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"], "phases": []}}]}, id="plan-no-phase"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"],
            "phases": [{**lab_packet()["plan"]["phases"][0], "name": "other"}]}}]},
            id="plan-identity"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"],
            "phases": [{**lab_packet()["plan"]["phases"][0], "actions": [
                {**lab_packet()["plan"]["phases"][0]["actions"][0],
                 "entity": "assembling-machine-1"}]}]}}]}, id="foreign-entity"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"],
            "phases": [{**lab_packet()["plan"]["phases"][0], "actions": [
                {**lab_packet()["plan"]["phases"][0]["actions"][0],
                 "action_type": "place_entity"}]}]}}]}, id="non-ghost"),
        pytest.param({**valid, "packets": [{**lab_packet(), "plan": {
            **lab_packet()["plan"],
            "phases": [{**lab_packet()["plan"]["phases"][0], "actions": [
                action for action in lab_packet()["plan"]["phases"][0]["actions"]
                if action["entity"] != "lab"]}]}}]}, id="count-mismatch"),
    ]


@pytest.mark.parametrize("record", _invalid_records(operation))
def test_build_labs_rejects_malformed_or_foreign_recovery_records(
    tmp_path, monkeypatch, record,
):
    operation, ledger, session, calls = prepare(
        tmp_path, monkeypatch, FakeBridge(science_status(total=2)))
    write_record(ledger, record)

    with pytest.raises(ValueError):
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert session.bridge.calls == 0
    assert calls == {"areas": [], "submitted": []}


def test_build_labs_rejects_overlapping_packets_and_multiple_unfinished_packets(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(
        tmp_path, monkeypatch, FakeBridge(science_status(total=2)))
    first = lab_packet(count=2, packet_id="research_labs_0", complete=True)
    overlapping = lab_packet(
        count=2, packet_id="research_labs_2", complete=False)
    write_record(ledger, lab_record(operation, packets=[first, overlapping]))
    with pytest.raises(ValueError, match="overlap"):
        operation.build_labs(session, count=6, packs={PACK: 1})
    assert calls == {"areas": [], "submitted": []}

    later = lab_packet(
        count=2, packet_id="research_labs_2", complete=False, origin=(40, 20))
    write_record(ledger, lab_record(
        operation, packets=[lab_packet(complete=False), later]))
    with pytest.raises(ValueError, match="unfinished"):
        operation.build_labs(session, count=6, packs={PACK: 1})
    assert calls == {"areas": [], "submitted": []}


def _chest_position(packet):
    action = next(
        action for action in packet["plan"]["phases"][0]["actions"]
        if action["entity"] == "requester-chest"
    )
    return (float(action["position"]["x"]), float(action["position"]["y"]))


def test_build_labs_refuses_preexisting_same_type_chest_without_ownership(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, object())
    packet = lab_packet()
    write_record(ledger, lab_record(operation, packets=[packet]))
    position = _chest_position(packet)
    monkeypatch.setattr(
        "orchestrator.live_base.entity_signatures_at",
        lambda *a, **kw: {position: {"name": "requester-chest", "direction": 0}},
    )

    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert error.value.code == "research_lab_ownership_unknown"
    assert error.value.details["gap"]
    assert calls == {"areas": [], "submitted": []}


@pytest.mark.parametrize("mutation", [
    "foreign_transaction", "foreign_tag", "old_event", "future_event", "authorization_claim",
])
def test_lab_ownership_rejects_forged_transaction_and_event_time(tmp_path, monkeypatch, mutation):
    bridge = SimpleNamespace()
    operation, _ledger, session, _calls = prepare(tmp_path, monkeypatch, bridge)
    actions = lab_packet()["plan"]["phases"][0]["actions"]
    evidence, _ = install_ownership(bridge, "research_labs_0", actions, monkeypatch)
    if mutation == "foreign_transaction":
        evidence["transaction_id"] = "b" * 32
    elif mutation == "foreign_tag":
        evidence["entities"][0]["tags"]["research_lab_transaction"] = "b" * 32
    elif mutation == "old_event":
        evidence["entities"][0]["tick"] = FRESH_TICK - 1
    elif mutation == "future_event":
        evidence["entities"][0]["tick"] = NOW + 1
    else:
        evidence["dispatch_authorized"] = True
    bridge.research_lab_ownership = lambda **_request: copy.deepcopy(evidence)
    targets = [(action["entity"], operation._lab_position(action)) for action in actions]
    with pytest.raises(operation.WorkStateSignal) as error:
        operation._require_lab_ownership(
            session, session.client, packet_id="research_labs_0", transaction_id=TRANSACTION,
            episode_id="labs", surface="nauvis", force="player", targets=targets,
            baseline_tick=FRESH_TICK,
        )
    assert error.value.code == "research_lab_ownership_unknown"


def test_build_labs_refuses_recreated_chest_whose_unit_number_changed(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, SimpleNamespace())
    packet = lab_packet()
    write_record(ledger, lab_record(operation, packets=[packet]))
    position = _chest_position(packet)
    monkeypatch.setattr(
        "orchestrator.live_base.entity_signatures_at",
        lambda *a, **kw: {position: {"name": "requester-chest", "direction": 0}},
    )
    _evidence, units = install_ownership(
        session.bridge, packet["id"], packet["plan"]["phases"][0]["actions"],
        monkeypatch,
    )
    import orchestrator.live_base as live_base
    replacement = {key: value + 1 for key, value in units.items()}
    monkeypatch.setattr(
        live_base, "entity_unit_numbers_at", lambda *a, **kw: replacement)

    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert error.value.code == "research_lab_ownership_unknown"
    assert "identity" in error.value.details["gap"]
    assert calls == {"areas": [], "submitted": []}


def test_build_labs_rejects_forged_recovery_sidecar_source(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(tmp_path, monkeypatch, SimpleNamespace())
    packet = lab_packet(complete=True)
    write_record(ledger, lab_record(operation, packets=[packet]))
    position = _chest_position(packet)
    monkeypatch.setattr(
        "orchestrator.live_base.entity_signatures_at",
        lambda *a, **kw: {position: {"name": "requester-chest", "direction": 0}},
    )
    evidence, _units = ownership_evidence(
        packet["id"], packet["plan"]["phases"][0]["actions"])
    evidence["source"] = "recovery_sidecar"
    session.bridge.research_lab_ownership = lambda **_request: copy.deepcopy(evidence)

    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert error.value.code == "research_lab_ownership_unknown"
    assert "robot-built" in error.value.details["gap"]
    assert calls == {"areas": [], "submitted": []}


def test_build_labs_does_not_configure_new_packet_without_runtime_ownership(
    tmp_path, monkeypatch,
):
    operation, ledger, session, calls = prepare(
        tmp_path, monkeypatch, FakeBridge(science_status(total=2)))

    with pytest.raises(operation.WorkStateSignal) as error:
        operation.build_labs(session, count=4, packs={PACK: 1})

    assert error.value.code == "research_lab_ownership_unknown"
    assert [entry[0] for entry in calls["submitted"]] == ["research_labs_0"]
    record = json.loads(record_path(ledger).read_text())
    assert record["packets"][0]["complete"] is False


@pytest.mark.parametrize("packs", [
    pytest.param(None, id="none"),
    pytest.param([], id="empty-list"),
    pytest.param({}, id="empty"),
    pytest.param({"": 1}, id="blank-name"),
    pytest.param({1: 1}, id="non-string-name"),
    pytest.param({PACK: True}, id="bool-coefficient"),
    pytest.param({PACK: 0}, id="zero-coefficient"),
    pytest.param({PACK: -1}, id="negative-coefficient"),
    pytest.param({PACK: float("nan")}, id="nan-coefficient"),
    pytest.param({PACK: float("inf")}, id="infinite-coefficient"),
])
def test_logistic_lab_row_rejects_malformed_pack_contract(packs):
    with pytest.raises(ValueError):
        logistic_lab_row(1, packs)


def test_logistic_lab_row_rejects_unusable_buffer():
    with pytest.raises(ValueError):
        logistic_lab_row(1, {PACK: 1}, buffer_units=0)
