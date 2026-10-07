# Path: tests/test_work_diagnosis.py
# Purpose: Protect scope, causal attribution, progress truth and offline-only reporting.

import copy
import json
from pathlib import Path

import pytest

from orchestrator.work_diagnosis import MAX_REPORT_CHARS, diagnose, render_markdown
from tools.diagnose_work import load_bundle, main

IDENTITY = {"episode_id": "episode-test", "surface": "nauvis", "force": "player", "source_id": "test-server"}


def bundle(*artifacts, focus="item:electric-mining-drill"):
    return {"version": 1, "identity": dict(IDENTITY), "objective": "Build drills", "focus": focus,
            "as_of_tick": 200, "max_age_ticks": 100, "artifacts": list(artifacts)}


def artifact(kind, payload, *, ref=None, tick=None):
    return {"kind": kind, "ref": ref or kind + ".json", "source_id": IDENTITY["source_id"],
            "observation_tick": tick, "payload": {"version": 1, **IDENTITY, **payload}}


def work(edges=None):
    return artifact("work", {"edges": edges or {"electric-mining-drill": {"electronic-circuit": "ingredient shortage"}}, "waits": {}})


def project(**overrides):
    return {"project_id": "drills", "target_item": "electric-mining-drill", "state": "supply_wait",
            "required": {"electronic-circuit": 3}, "reserved": {"electronic-circuit": 1},
            "source_producers": {"electronic-circuit": "producer:electronic-circuit"},
            "expected_rates": {"electronic-circuit": None}, "eta_seconds": {"electronic-circuit": None},
            "priority": 10, "sequence": 1, "hold_until_producing": False, "revision": 1, **overrides}


def loan():
    return artifact("loans", {"loans": [{"loan_id": "cell-left", "original_recipe": "electronic-circuit",
                                        "current_recipe": "transport-belt", "target_item": "transport-belt"}]})


def measurement(tick, value, *, node="item:electric-mining-drill", metric="items_delivered", counter="delivery-1"):
    return artifact("measurements", {"tick": tick, "measurements": [
        {"work_id": node, "metric": metric, "value": value, "counter_id": counter}]},
        ref=f"measurement-{tick}.json", tick=tick)


def test_traces_bill_to_displaced_cell_without_claiming_global_starvation():
    data = bundle(artifact("materials", {"projects": [project(), project(
        project_id="other", target_item="assembling-machine-1", required={"electronic-circuit": 8},
        reserved={"electronic-circuit": 8}, sequence=2)]}), loan())
    before = copy.deepcopy(data)
    report = diagnose(data)
    assert data == before
    assert report["status"] == "producer_displaced"
    assert any(e["parent"] == "project:drills" and e["child"] == "item:electronic-circuit" for e in report["edges"])
    shortage = next(f for f in report["findings"] if f["code"] == "material_wait")
    assert "short 2" in shortage["message"] and "other" in shortage["message"]
    displaced = next(f for f in report["findings"] if f["code"] == "producer_displaced")
    assert "Other deliverable producers" in displaced["message"]
    assert displaced["evidence"] == "loans.json#/loans/0"
    assert displaced["freshness"] == "undated"
    assert report["authority"] == "advisory_only"


@pytest.mark.parametrize("key,bad", [("episode_id", "other-run"), ("surface", "training"),
                                    ("force", "enemy"), ("source_id", "other-server")])
def test_mismatched_evidence_does_not_enter_graph(key, bad):
    wrong = loan()
    wrong["payload"][key] = bad
    report = diagnose(bundle(work(), wrong))
    assert report["inputs"][1]["status"] == "rejected"
    assert not any(f["code"] == "producer_displaced" for f in report["findings"])
    assert not any(e["child"].startswith("loan:") for e in report["edges"])


def test_wrapper_source_and_future_ticks_rejected():
    wrong = work()
    wrong["source_id"] = "other-server"
    future = measurement(201, 3)
    report = diagnose(bundle(wrong, future))
    assert all(i["status"] == "rejected" for i in report["inputs"])
    assert report["status"] == "unknown" and not report["progress"]


def test_native_work_scope_gaps_remain_explicit():
    journal = work()
    for key in ("surface", "force", "source_id"):
        journal["payload"].pop(key)
    report = diagnose(bundle(journal))
    assert report["inputs"][0]["status"] == "accepted"
    assert any("collector attribution" in u for u in report["unknowns"])


def test_recorded_cycle_is_not_a_deadlock_and_seeded_output_is_progress():
    cycle = work({"electric-mining-drill": {"electronic-circuit": "needs input"},
                  "electronic-circuit": {"electric-mining-drill": "needs capacity"}})
    report = diagnose(bundle(cycle))
    assert report["status"] == "recorded_dependency_cycle"
    assert report["cycles"] == [["item:electric-mining-drill", "item:electronic-circuit", "item:electric-mining-drill"]]
    assert "Verify" in report["next_observation"]
    report = diagnose(bundle(cycle, measurement(150, 1), measurement(200, 2)))
    assert report["status"] == "progressing" and report["cycles"]
    assert report["progress"][0]["delta"] == 1


def test_unrelated_and_prerequisite_progress_cannot_renew_focus_progress():
    report = diagnose(bundle(work(), measurement(150, 1, node="item:iron-plate"),
                             measurement(200, 900, node="item:iron-plate")))
    assert report["status"] != "progressing" and not report["progress"]
    report = diagnose(bundle(work(), measurement(150, 1, node="item:electronic-circuit"),
                             measurement(200, 2, node="item:electronic-circuit")))
    assert report["status"] != "progressing"
    assert report["progress"][0]["state"] == "advancing"


@pytest.mark.parametrize("first,second,expected", [(1, 1, "unchanged"), (3, 1, "counter_reset")])
def test_unchanged_or_reset_counters_do_not_prove_stall(first, second, expected):
    report = diagnose(bundle(measurement(150, first), measurement(200, second)))
    assert report["progress"][0]["state"] == expected
    assert report["status"] == "unknown"


@pytest.mark.parametrize("variant", ["stale", "duplicate_tick", "changed_counter", "mismatched_tick"])
def test_uncomparable_samples_do_not_prove_progress(variant):
    before, after = measurement(150, 1), measurement(200, 2)
    if variant == "stale":
        before = measurement(99, 1)
    elif variant == "duplicate_tick":
        before = measurement(200, 1)
        before["ref"] = "same-tick-other.json"
    elif variant == "changed_counter":
        after = measurement(200, 2, counter="replacement-generation")
    else:
        after["observation_tick"] = 199
    report = diagnose(bundle(before, after))
    assert report["status"] == "unknown" and not report["progress"]


def test_owned_ghost_completion_is_progress_but_stock_totals_are_not():
    report = diagnose(bundle(measurement(150, 9, metric="pending_ghosts"), measurement(200, 8, metric="pending_ghosts")))
    assert report["status"] == "progressing"
    report = diagnose(bundle(measurement(150, 9, metric="total_stock"), measurement(200, 99, metric="total_stock")))
    assert all(i["status"] == "rejected" for i in report["inputs"])


def test_consumed_constructing_and_terminal_bills_are_not_missing_supply():
    report = diagnose(bundle(artifact("materials", {"projects": [project(state="constructing", reserved={})]})))
    assert [f["code"] for f in report["findings"]] == ["construction_wait"]
    assert not any(e["child"] == "item:electronic-circuit" for e in report["edges"])
    for state in ("completed", "failed"):
        report = diagnose(bundle(artifact("materials", {"projects": [project(state=state)]})))
        assert not report["edges"] and not report["findings"]


def test_malformed_artifact_is_rejected_atomically_and_valid_evidence_survives():
    bad = artifact("materials", {"projects": [project(), project(project_id="bad", required={"x": -1})]})
    report = diagnose(bundle(bad, work(), loan()))
    assert report["inputs"][0]["status"] == "rejected"
    assert not any(e["parent"].startswith("project:") for e in report["edges"])
    assert report["status"] == "producer_displaced"
    invalid_district = artifact("district", {"recipe": "iron-plate"})
    assert diagnose(bundle(invalid_district))["inputs"][0]["status"] == "rejected"


def test_limits_and_omissions_keep_valid_bounded_deterministic_json():
    projects = [project(project_id=f"p{i}", required={"electronic-circuit": 3}, sequence=i + 1) for i in range(40)]
    data = bundle(artifact("materials", {"projects": projects}))
    report = diagnose(data)
    assert report["status"] == "material_wait"
    assert report["omitted"]
    assert len(json.dumps(report, sort_keys=True) + "\n") <= MAX_REPORT_CHARS
    assert len(render_markdown(report)) <= MAX_REPORT_CHARS
    assert diagnose(copy.deepcopy(data)) == report
    too_big = work({"electric-mining-drill": {f"item{i}": "needs" for i in range(97)}})
    assert diagnose(bundle(too_big))["inputs"][0]["status"] == "rejected"


def test_offline_cli_keeps_inputs_unchanged_and_refuses_overwrite(tmp_path, capsys):
    ledger = tmp_path / "work.json"
    ledger.write_text(json.dumps(work()["payload"]))
    descriptor = work()
    descriptor.pop("payload")
    descriptor["path"] = ledger.name
    descriptor["ref"] = "work.json"
    source = tmp_path / "bundle.json"
    source.write_text(json.dumps(bundle(descriptor)))
    original = {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert main(["--bundle", str(source), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["inputs"][0]["sha256"]) == 64
    assert {p: p.read_bytes() for p in tmp_path.iterdir()} == original
    with pytest.raises(SystemExit) as failure:
        main(["--bundle", str(source), "--output", str(ledger)])
    assert failure.value.code == 2 and ledger.read_bytes() == original[ledger]


def test_missing_corrupt_and_nonfinite_files_are_explicit_unknowns(tmp_path):
    for content in (None, "{broken", '{"value": NaN}'):
        ledger = tmp_path / "work.json"
        if content is not None:
            ledger.write_text(content)
        descriptor = work()
        descriptor.pop("payload")
        descriptor["path"] = ledger.name
        source = tmp_path / "bundle.json"
        source.write_text(json.dumps(bundle(descriptor)))
        report = diagnose(load_bundle(source))
        assert report["inputs"][0]["status"] == "rejected"
        assert "cannot read artifact" in report["unknowns"][0]


def test_conflicting_journal_snapshots_are_both_excluded():
    before, after = loan(), loan()
    after["ref"] = "new-loans.json"
    after["payload"]["loans"][0]["current_recipe"] = "electronic-circuit"
    report = diagnose(bundle(work(), before, after))
    assert all(i["status"] == "rejected" for i in report["inputs"][1:])
    assert not any(f["code"] == "producer_displaced" for f in report["findings"])


def test_district_lifecycle_uses_owned_intent_not_lifetime_output_readiness():
    from orchestrator.bootstrap_district import BootstrapDistrictState, REQUIRED_RESERVATION_ROLES
    action = {"action_type": "place_ghost", "entity": "electric-furnace", "position": {"x": 1.5, "y": 2.5}}
    state = BootstrapDistrictState(
        episode_id=IDENTITY["episode_id"], surface="nauvis", force="player", bootstrap_profile="reduced-v1",
        recipe="iron-plate", ore="iron-ore", district_id="district-iron", lifecycle_state="provisioning",
        pioneer_actions=(action,), reservations={role: frozenset({(0, 0)}) for role in REQUIRED_RESERVATION_ROLES},
        replacement_origin=(10, 10), replacement_provider=(20, 20), replacement_furnaces=6,
        replacement_actions=(action,), measured_output_count=900)
    report = diagnose(bundle(artifact("district", json.loads(state.to_json())), focus="item:iron-plate"))
    assert report["status"] == "recorded_wait"
    assert "owned replacement submission" in report["next_observation"]
    assert "historical measured output=900" in report["findings"][0]["message"]


def test_bundle_and_report_match_versioned_schemas():
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    root = Path(__file__).resolve().parents[1] / "schemas"
    name = "work_diagnosis_bundle.schema.json"
    input_schema = json.loads((root / name).read_text())
    output_schema = json.loads((root / "work_diagnosis.schema.json").read_text())
    registry = Registry().with_resource(name, Resource.from_contents(input_schema))
    data = bundle(work(), loan())
    Draft202012Validator(input_schema).validate(data)
    Draft202012Validator(output_schema, registry=registry).validate(diagnose(data))
