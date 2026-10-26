# Path: data/laya-orchestration/model-launch-20261026/autonomy12h-20261027/test_final_accounting.py | Purpose: CPU-only synthetic contracts for the guarded final accounting seam.
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SPEC = importlib.util.spec_from_file_location("final_accounting", Path(__file__).with_name("final_accounting.py"))
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)
HASH = "a" * 64


def ref(path="parent-evidence.json", **extra):
    return dict(path=path, sha256=HASH, **extra)


def binding():
    return {"predicate_source_sha256": HASH, "milestone_versions": dict(adapter.VERSIONS),
            "source_save_sha256": HASH, "mods_sha256": HASH, "freeze_sha256": HASH,
            "runtime_revision": "2511e4", "window": {"max_game_ticks": 540000, "wall_seconds": 7200},
            "baseline_sha256": HASH}


def dump(path, value):
    raw = (json.dumps(value, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()}


def observations(start="failed", end="failed", *, final=True, run="run-1"):
    """Shape from frozen OperationSession.acceptance/check_acceptance; passed checks are absent."""
    latched = {"C0", "C1"}
    records = []
    for at, value in (("start", start), ("end" if final else "decision 9", end)):
        fresh = dict({"C0": "passed", "C1": "passed"}, **{p: value for p in adapter.PHASES})
        latched.update(p for p in adapter.PHASES if value == "passed")
        statuses = {p: "passed" if p in latched else status for p, status in fresh.items()}
        checks = {p: {"failed": ["owned_output_present"], "unknown": [], "duration_ticks": 7200}
                  for p in adapter.PHASES if value != "passed"}
        records.append({"run_id": run, "event": "acceptance", "at": at,
                        "statuses": statuses, "fresh": fresh, "checks": checks})
    summary = {"run_id": run, "event": "summary", "stop": "window_timeout", "decisions": 0,
               "failures": 0, "accepted": sorted(latched), "accepted_ever": sorted(latched),
               "accepted_final": ["C0", "C1"] + (list(adapter.PHASES) if end == "passed" else []),
               "acceptance": records[-1]["statuses"],
               "codes": {}, "classifications": {}, "phases_reached": ["C1"],
               "laya_acceptance": {"C6": {"version": "laya-c6-choice-output-v2", "status": "failed"}}}
    return records, summary


class Fixture:
    """120 tiny planned rows, 8 short synthetic journals, no worlds or services."""
    def __init__(self, root):
        self.root = root
        self.source = str(root / adapter.BASE_UNIT.removesuffix(".service"))
        self.owners = {a: str(root / f"laya-model-scale15-{a}-2123-miniship-20261027") for a in adapter.ARMS}
        self.contract = {"predicate_source_sha256": HASH, "milestone_versions": adapter.VERSIONS,
                         "source_save_sha256": HASH, "freeze_sha256": HASH, "mods_sha256": HASH,
                         "runtime_revision": "2511e4", "window": {"max_game_ticks": 540000, "wall_seconds": 7200}}
        self.registries = {r: {"jobs": []} for r in [self.source, *self.owners.values()]}
        self.plan = {"version": "laya-model-capacity-transfer-v1", "source_registry": self.source,
                     "logical_planned_jobs": 120, "transferred_jobs": 113, "source_freeze_sha256": HASH,
                     "untouched_started_jobs": sorted(adapter.ORIGINAL), "assignments": []}
        self.health = {"version": "laya-model-cohort-host-health-v1", "checked_utc": "2026-10-27T00:00:00+00:00",
                       "logical_planned": 120, "logical_jobs": [], "source_administrative_transfers": 113,
                       "active_by_arm": {}, "running_jobs": [], "parse_errors": [], "host_occupied": 0,
                       "other_reserved_jobs": [], "units": {}, "issues": []}
        self.proof = {"version": "laya-final-accounting-closure-v1", "attempts": [],
                      "owned_reservations": [], "reservation_receipt": ref(), "units": {},
                      "predicate_contract": self.contract, "comparisons": [["guided", "kev"], ["kev", "vela"], ["vela", "jiwo"]],
                      "capacity_periods": [{"name": "original_target3", "start_utc": 0, "end_utc": 20000, "receipt": ref()}]}
        for unit in adapter.UNITS:
            raw = {"ActiveState": "inactive", "MainPID": "0", "Result": "success", "ExecMainStatus": "0"}
            self.health["units"][unit] = dict(raw)
            self.proof["units"][unit] = dict(raw, query_ok=True, cgroup_empty=True, processes_empty=True, receipt=ref())
        for arm in adapter.ARMS:
            for seed in adapter.SEEDS:
                jid = f"{arm}-s{seed}"
                row = {"id": jid, "arm": arm, "seed": seed, "status": "not_started_deadline",
                       "termination_cause": "cohort_deadline"}
                owner = self.source if jid in adapter.ORIGINAL else self.owners[arm]
                if jid in adapter.ORIGINAL or jid == "guided-s80301":
                    row.update(status="completed", world=str(root / (jid + "-world")), preparation_started=10000,
                               started=10001, runner_exited=10010, runner_returncode=0, cleanup_started=10011,
                               cleanup_completed=10012, admission_reserved=False, runner_stop="window_timeout")
                    self.add_attempt(row, owner)
                self.registries[owner]["jobs"].append(row)
                self.health["logical_jobs"].append(dict(row, registry=owner))
                if owner != self.source:
                    self.plan["assignments"].append(dict(id=jid, arm=arm, seed=seed, registry=owner, source=self.source))
                    self.registries[self.source]["jobs"].append(dict(id=jid, arm=arm, seed=seed,
                                                                   status="cancelled_queued", termination_cause="operator_cut"))

    def add_attempt(self, row, owner):
        jid, number = row["id"], row.get("attempt", 0)
        journal = self.root / f"{jid}-{number}-clean.jsonl"
        run = f"run-{jid}-{number}"
        records, summary = observations(run=run)
        journal.write_text("".join(json.dumps(r) + "\n" for r in [*records, summary]))
        self.proof["attempts"].append({
            "job_id": jid, "attempt": number, "registry": owner, "run_id": run, "job_sha256": adapter.digest(row),
            "start_receipt": ref(event="attempt_started", event_id=f"start:{jid}:{number}"),
            "terminal_receipt": ref(event="attempt_terminal", event_id=f"terminal:{jid}:{number}"),
            "release": {"runner_released": True, "factorio_released": True, "ports_released": True, "receipt": ref()},
            "journal": {"path": str(journal), "sha256": hashlib.sha256(journal.read_bytes()).hexdigest()},
            "provenance": dict(self.contract, baseline_sha256=HASH),
            "certification_receipts": [ref("carried-status-based-certification.json")]})

    def save(self):
        self.proof["registry_snapshots"] = {}
        self.health["registry_metadata"] = {}
        for number, (registry, data) in enumerate(self.registries.items()):
            pinned = dump(self.root / f"registry-{number}.json", data)
            self.proof["registry_snapshots"][registry] = pinned
            self.health["registry_metadata"][registry] = {"sha256": pinned["sha256"]}
        self.health["logical_counts"] = dict(adapter.Counter(j["status"] for j in self.health["logical_jobs"]))
        plan = dump(self.root / "transfer-plan.json", self.plan)
        health = dump(self.root / "host-health.json", self.health)
        self.proof.update(snapshot_sha256=health["sha256"], transfer_plan_sha256=plan["sha256"])
        proof = dump(self.root / "closure.json", self.proof)
        return Path(health["path"]), Path(plan["path"]), Path(proof["path"])

    def build(self):
        return adapter.build_report(*self.save())


class FinalAccountingTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(prefix="laya-final-fixture-")
        self.addCleanup(directory.cleanup)
        return Fixture(Path(directory.name))

    def test_guarded_full_denominators_and_admin_exclusion(self):
        fixture = self.fixture()
        report = fixture.build()
        self.assertEqual(report["closure_proof"], fixture.proof)
        self.assertEqual(report["overall"]["planned_jobs"], 120)
        self.assertEqual({a: r["planned_jobs"] for a, r in report["arms"].items()}, dict.fromkeys(adapter.ARMS, 30))
        self.assertEqual(report["ownership"]["original7"]["planned_jobs"], 7)
        self.assertEqual(report["ownership"]["transferred113"]["planned_jobs"], 113)
        for arm, n in (("guided", 1), ("kev", 2), ("vela", 2), ("jiwo", 2)):
            self.assertEqual(report["ownership_by_arm"][arm]["original7"]["planned_jobs"], n)
            self.assertEqual(report["ownership_by_arm"][arm]["transferred113"]["planned_jobs"], 30 - n)
        self.assertEqual(report["administrative_transfer_copies"]["additional_trials"], 0)
        cell = report["arms"]["guided"]["strict"]["C6_ever"]
        self.assertEqual((cell["planned"], cell["known"], cell["failed"], cell["unobserved"]), (30, 2, 2, 28))
        self.assertEqual(report["paired"]["kev_vs_guided"]["C6_final"]["observed_pairs"], 2)
        for pairs in report["paired"].values():
            self.assertEqual(set(pairs), {p + "_" + e for p in adapter.PHASES for e in ("ever", "final")})
            self.assertTrue(all(v["planned_pairs"] == 30 for v in pairs.values()))
        self.assertEqual(report["status"], "blocked")
        self.assertIsNone(report["adoption_verdict"])
        self.assertIsNone(report["faults"]["categories"]["safety_error"]["events"])
        self.assertIn("required_supplement:raw_join_certification", report["material_gaps"])

    def test_owner_and_administrative_copy_guards(self):
        fixture = self.fixture()
        job = next(j for j in fixture.health["logical_jobs"] if j["id"] == "guided-s80301")
        job["registry"] = fixture.source
        with self.assertRaisesRegex(adapter.GuardError, "owner mismatch"):
            fixture.build()
        fixture = self.fixture()
        admin = next(j for j in fixture.registries[fixture.source]["jobs"] if j["id"] == "guided-s80301")
        admin["started"] = 100
        with self.assertRaisesRegex(adapter.GuardError, "administrative copy"):
            fixture.build()

    def test_nonterminal_refused_before_any_journal_read(self):
        fixture = self.fixture()
        fixture.proof["attempts"][0]["journal"]["path"] = "/does-not-exist"
        fixture.health["logical_jobs"][0]["status"] = "running"
        owner = fixture.health["logical_jobs"][0]["registry"]
        fixture.registries[owner]["jobs"][0]["status"] = "running"
        with self.assertRaisesRegex(adapter.GuardError, "nonterminal"):
            fixture.build()

    def test_duplicate_ids_and_attempts_refused(self):
        fixture = self.fixture()
        fixture.health["logical_jobs"].append(copy.deepcopy(fixture.health["logical_jobs"][0]))
        with self.assertRaisesRegex(adapter.GuardError, "duplicate logical job"):
            fixture.build()
        fixture = self.fixture()
        fixture.proof["attempts"].append(copy.deepcopy(fixture.proof["attempts"][0]))
        with self.assertRaisesRegex(adapter.GuardError, "duplicate attempt proof"):
            fixture.build()

    def test_required_unit_reservation_and_cleanup_evidence(self):
        for missing in ("unit", "reservation", "cleanup", "terminal", "reserved"):
            with self.subTest(missing=missing):
                fixture = self.fixture()
                if missing == "unit":
                    fixture.proof["units"].pop(adapter.BASE_UNIT)
                elif missing == "reservation":
                    fixture.proof.pop("reservation_receipt")
                elif missing == "cleanup":
                    fixture.proof["attempts"][0]["release"].pop("ports_released")
                elif missing == "terminal":
                    fixture.proof["attempts"][0].pop("terminal_receipt")
                else:
                    row = fixture.health["logical_jobs"][0]
                    row["admission_reserved"] = True
                    fixture.registries[row["registry"]]["jobs"][0]["admission_reserved"] = True
                with self.assertRaises(adapter.GuardError):
                    fixture.build()

    def test_fresh_ever_final_and_status_only_disagreement(self):
        records, summary = observations(start="passed", end="failed")
        provenance = binding()
        projected = adapter.strict_projection(records, summary, provenance, provenance)
        self.assertEqual(projected["strict"]["C6"]["ever"], "passed")
        self.assertEqual(projected["strict"]["C6"]["final"], "failed")
        self.assertEqual(projected["strict"]["C6"]["version_basis"], "parent_source_binding")
        records[0]["fresh"]["C6"] = "failed"
        projected = adapter.strict_projection(records, summary, provenance, provenance)
        self.assertEqual(projected["strict"]["C6"]["ever"], "unknown")
        self.assertTrue(projected["strict"]["C6"]["crosscheck_errors"])

    def test_final_requires_end_and_observation_errors_are_unknown(self):
        records, summary = observations(start="failed", end="passed", final=False)
        bound = binding()
        projected = adapter.strict_projection(records, summary, bound, bound)
        self.assertEqual(projected["strict"]["C6"]["ever"], "passed")
        self.assertEqual(projected["strict"]["C6"]["final"], "unknown")
        self.assertTrue(projected["final_missing"])
        records[-1].update(at="end", statuses={"error": "observation unsupported"}, fresh=None, checks=None)
        summary.update(accepted=["C0", "C1"], accepted_ever=["C0", "C1"], accepted_final=None)
        projected = adapter.strict_projection(records, summary, bound, bound)
        self.assertEqual(projected["strict"]["C6"]["final"], "unknown")
        empty = adapter.strict_projection([], None, bound, bound)
        self.assertEqual(empty["strict"]["C6"]["final"], "unknown")

    def test_version_and_same_label_source_mismatch_never_pool(self):
        records, summary = observations(start="passed", end="passed")
        contract = binding()
        for mismatch in ("label", "source", "missing"):
            with self.subTest(mismatch=mismatch):
                provenance = copy.deepcopy(contract)
                if mismatch == "label":
                    provenance["milestone_versions"]["C6"] = "c6-v0"
                elif mismatch == "source":
                    provenance["predicate_source_sha256"] = "b" * 64
                else:
                    provenance.pop("predicate_source_sha256")
                projected = adapter.strict_projection(records, summary, provenance, contract)
                self.assertEqual(projected["strict"]["C6"]["ever"], "unknown" if mismatch == "missing" else "incompatible")
        fixture = self.fixture()
        fixture.proof["attempts"][0]["provenance"]["milestone_versions"] = dict(adapter.VERSIONS, C6="c6-v0")
        report = fixture.build()
        self.assertEqual(report["overall"]["strict"]["C6_ever"]["incompatible"], 1)

    def test_exact_pair_arithmetic_and_full_n(self):
        left = ["failed", "failed", "failed", "passed"] + ["unobserved"] * 20 + ["unknown"] * 5 + ["incompatible"]
        right = ["passed", "passed", "passed", "failed"] + ["passed"] * 26
        paired = adapter.paired_cells(left, right)
        self.assertEqual((paired["planned_pairs"], paired["observed_pairs"], paired["unknown_pairs"]), (30, 4, 26))
        self.assertEqual((paired["right_only"], paired["left_only"]), (3, 1))
        self.assertEqual(paired["exact_mcnemar_two_sided"], {"discordant_pairs": 4, "numerator": 5, "denominator": 8, "p": .625})
        self.assertEqual(sum(paired["cells_left_right"].values()), 30)
        self.assertEqual(adapter.paired_cells(["unknown"] * 30, ["unknown"] * 30)["exact_mcnemar_two_sided"]["p"], 1)

    def test_context_and_explicit_check_version_mismatches_are_visible(self):
        records, summary = observations(start="passed", end="failed")
        contract = binding()
        provenance = dict(contract, window={"max_game_ticks": 100, "wall_seconds": 10})
        projected = adapter.strict_projection(records, summary, provenance, contract)
        self.assertEqual(projected["strict"]["C6"]["ever"], "incompatible")
        self.assertIn("incompatible_context:window", projected["issues"])
        records[-1]["checks"]["C6"]["predicate_version"] = "c6-v0"
        projected = adapter.strict_projection(records, summary, contract, contract)
        self.assertEqual(projected["strict"]["C6"]["version_basis"], "incompatible")
        self.assertIn("C6:incompatible_predicate", projected["issues"])

    def test_wrong_seed_and_cached_summary_mismatch(self):
        fixture = self.fixture()
        job = fixture.health["logical_jobs"][0]
        fixture.registries[job["registry"]]["jobs"][0]["seed"] = job["seed"] = 80330
        with self.assertRaisesRegex(adapter.GuardError, "arm/seed mismatch"):
            fixture.build()
        fixture = self.fixture()
        job = fixture.health["logical_jobs"][0]
        row = fixture.registries[job["registry"]]["jobs"][0]
        row["accepted_ever"] = job["accepted_ever"] = ["C6"]
        fixture.proof["attempts"][0]["job_sha256"] = adapter.digest(row)
        report = fixture.build()
        projected = next(j for j in report["jobs"] if j["id"] == job["id"])
        self.assertEqual(projected["strict"]["C6"]["ever"], "unknown")
        self.assertTrue(any("snapshot_summary_disagrees" in gap for gap in report["material_gaps"]))

    def test_failed_unit_preserved_and_not_successful_closure(self):
        fixture = self.fixture()
        for mapping in (fixture.health["units"], fixture.proof["units"]):
            mapping[adapter.BASE_UNIT].update(ActiveState="failed", Result="exit-code", ExecMainStatus="1")
        report = fixture.build()
        self.assertIn("unit_non_success:" + adapter.BASE_UNIT, report["material_gaps"])
        self.assertEqual(report["health_context"]["units"][adapter.BASE_UNIT]["Result"], "exit-code")

    def test_retained_attempts_need_resolution_and_are_preserved(self):
        fixture = self.fixture()
        job = fixture.health["logical_jobs"][0]
        current = fixture.registries[job["registry"]]["jobs"][0]
        prior = copy.deepcopy(current)
        prior.update(attempt=1, world=prior["world"] + "-retained")
        current["previous_attempts"] = [prior]
        job["previous_attempts"] = [prior]
        fixture.add_attempt(prior, job["registry"])
        with self.assertRaisesRegex(adapter.GuardError, "ambiguous retained attempts"):
            fixture.build()
        fixture.proof["attempt_resolutions"] = {job["id"]: {
            "basis": "current_attempt_only", "reason": "synthetic parent reconciliation", "selected_attempt": 0}}
        report = fixture.build()
        result = next(j for j in report["jobs"] if j["id"] == job["id"])
        self.assertEqual(len(result["attempt_projections"]), 2)
        self.assertEqual(len(result["previous_attempts"]), 1)
        self.assertEqual(report["overall"]["planned_jobs"], 120)

    def test_narrow_stream_receipts_and_hash_guards(self):
        fixture = self.fixture()
        pinned = fixture.proof["attempts"][0]["journal"]
        records, summary = adapter.project_journal(pinned, fixture.proof["attempts"][0]["run_id"])
        self.assertEqual(records[0]["receipt"]["line"], 1)
        self.assertEqual(records[0]["receipt"]["offset"], 0)
        self.assertIsNotNone(records[0]["checks"])
        self.assertNotIn("decisions", summary)
        with self.assertRaisesRegex(adapter.GuardError, "run identity"):
            adapter.project_journal(pinned, "wrong-run")
        with self.assertRaisesRegex(adapter.GuardError, "hash mismatch"):
            adapter.project_journal(dict(pinned, sha256="b" * 64), fixture.proof["attempts"][0]["run_id"])

    def test_quiet_crossing_mixed_and_unknown_without_utc_mapping(self):
        periods = [("before_quiet", 0, 10), ("quiet_target4", 10, 20), ("restored15", 20, 30)]
        self.assertEqual(adapter.capacity_period({"preparation_started": 11, "cleanup_completed": 19}, periods), "quiet_target4")
        self.assertEqual(adapter.capacity_period({"preparation_started": 9, "cleanup_completed": 21}, periods), "mixed")
        self.assertEqual(adapter.capacity_period({"preparation_started": 9, "cleanup_completed": 21}, []), "period-unknown")

    def test_parent_fault_events_separate_and_duplicate_join_refused(self):
        fixture = self.fixture()
        parent = fixture.proof["attempts"][0]
        events = [dict(event_id=f"event-{i}", job_id=parent["job_id"], attempt=0, run_id=parent["run_id"], index=i,
                       category=c, code=c, classification="bug" if c == "typed_fault" else c, receipt=ref())
                  for i, c in enumerate(("typed_fault", "backend_error", "runner_failure", "safety_error"))]
        supplement = {"status": "verified", "receipt": ref(), "covered_attempts": [f"{p['job_id']}:{p['attempt']}" for p in fixture.proof["attempts"]],
                      "events": events}
        fixture.proof["supplements"] = {"faults": supplement}
        report = fixture.build()
        self.assertEqual(report["faults"]["categories"]["typed_fault"]["events"], 1)
        self.assertEqual(report["faults"]["categories"]["backend_error"]["affected_jobs"], 1)
        self.assertEqual(report["faults"]["categories"]["tool_error"]["events"], 0)
        supplement["events"].append(dict(events[0], event_id="duplicate-other-stream"))
        with self.assertRaisesRegex(adapter.GuardError, "duplicate joined fault"):
            fixture.build()

    def test_derived_only_output_and_immutable_input_binding(self):
        fixture = self.fixture()
        report = fixture.build()
        output = fixture.root / "derived"
        adapter.write_report(report, output)
        self.assertTrue((output / "report.json").is_file())
        with self.assertRaisesRegex(adapter.GuardError, "must be new"):
            adapter.write_report(report, output)
        with self.assertRaisesRegex(adapter.GuardError, "authoritative"):
            adapter.write_report(report, Path(fixture.source) / "unsafe-report")
        paths = fixture.save()
        paths[0].write_text("{}\n")
        with self.assertRaisesRegex(adapter.GuardError, "input hash mismatch"):
            adapter.build_report(*paths)


if __name__ == "__main__":
    unittest.main(verbosity=1)
