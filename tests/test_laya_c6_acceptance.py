# Path: tests/test_laya_c6_acceptance.py | Purpose: Protect explicit Laya circuit choice/output evidence from stock, loan and counter confounds.
# Pure acceptance contracts intentionally use the taxonomy's contracts fallback.
import copy
import json

import pytest

from training.laya_phase_acceptance import ITEM, VERSION, LayaC6Acceptance, ops_phase_reached


def facts(tick=100, produced=9):
    return {"version": "saved-world-v1", "tick": tick, "surface": "nauvis", "force": "player",
            "mods": {"base": "2.1.22"}, "production": {ITEM: produced}, "consumption": {},
            "recipe_observations": {ITEM: {"enabled": True, "ingredients": [],
                                           "products": [{"name": ITEM, "type": "item", "amount": 1}]}},
            "networks": {}, "machines": [], "ports": [], "resources": [],
            "recipes": {ITEM: {"ingredients": [], "product_amount": 1, "energy_ticks": 360}}}


def choice(item=ITEM, family="produce"):
    return {"option_id": f"op.{family}.{item}", "family": family, "params": {"item": item},
            "product": item, "summary": f"Make {item}", "need_id": item, "features": {}}


def armed():
    c6 = LayaC6Acceptance("new-run")
    c6.choose(choice(), facts(), index=2)
    return c6


def test_only_real_post_choice_recipe_output_passes_and_survives_loan_restoration():
    c6 = armed()
    # Entity lifetime crafts, stocked circuits, consumption and a successful submission prove no output.
    sample = facts(110)
    sample.update(machines=[{"recipe": ITEM, "products_finished": 999}],
                  networks={"1": {"items": {ITEM: 999}}}, consumption={ITEM: 999})
    c6.outcome({"ok": True, "code": "submitted", "operation": "produce"})
    assert c6.observe(sample)["status"] == "failed"
    # Actual engine item production survives a complete loan and output consumed before sampling.
    sample = facts(120, 10)
    sample["machines"] = [{"recipe": "copper-cable", "products_finished": 1000}]
    evidence = c6.observe(sample)
    assert evidence["status"] == "passed"
    assert evidence["recipe_output_delta_items"] == 1
    assert evidence["choice"]["index"] == 2
    assert evidence["baseline"]["tick"] == 100 and evidence["observed"]["tick"] == 120
    assert evidence["baseline"]["units"] == "items"
    assert evidence["version"] == VERSION
    assert c6.observe(facts(130, 11))["observed"]["tick"] == 120  # achievement receipt remains immutable


def test_old_output_hidden_prerequisites_and_other_mall_choice_do_not_qualify():
    c6 = LayaC6Acceptance("new")
    assert c6.observe(facts(120, 1000))["status"] == "failed"
    c6.choose(choice("requester-chest"), facts(), index=0)
    assert c6.observe(facts(130, 1001))["status"] == "failed"
    c6 = armed()
    c6.choose(choice("requester-chest"), facts(), index=3)
    assert c6.observe(facts(130, 1001))["status"] == "failed"
    assert "baseline" not in c6.snapshot()
    assert LayaC6Acceptance("resumed").observe(facts(140, 1002))["status"] == "failed"


def test_wait_keeps_choice_open_and_choice_and_evidence_are_not_mutable_aliases():
    c6 = armed()
    c6.choose({"family": "wait"}, facts(), index=3)
    evidence = c6.snapshot()
    evidence["baseline"]["produced_items"] = 0
    assert c6.observe(facts(101))["status"] == "failed"
    assert c6.observe(facts(102, 10))["status"] == "passed"


@pytest.mark.parametrize("mutation", ["scope", "missing", "negative", "nan", "boolean", "catalog", "mods",
                                      "alternate", "disabled", "malformed", "tick_reset", "counter_reset"])
def test_invalid_or_ambiguous_measurements_stay_unknown_until_a_new_explicit_choice(mutation):
    c6 = armed()
    sample = facts(110, 10)
    if mutation == "scope": sample["force"] = "other"
    if mutation == "missing": sample["production"] = {}
    if mutation == "negative": sample["production"][ITEM] = -1
    if mutation == "nan": sample["production"][ITEM] = float("nan")
    if mutation == "boolean": sample["production"][ITEM] = True
    if mutation == "catalog": sample.pop("recipe_observations")
    if mutation == "mods": sample["mods"]["base"] = "2.1.23"
    if mutation == "alternate": sample["recipe_observations"]["recycling"] = copy.deepcopy(sample["recipe_observations"][ITEM])
    if mutation == "disabled": sample["recipe_observations"][ITEM]["enabled"] = False
    if mutation == "malformed": sample["recipe_observations"][ITEM]["products"] = ["broken"]
    if mutation == "tick_reset": sample["tick"] = 99
    if mutation == "counter_reset": sample["production"][ITEM] = 8
    assert c6.observe(sample)["status"] == "unknown"
    assert c6.observe(facts(120, 11))["status"] == "unknown"
    c6.choose(choice(), facts(120, 11), index=3)
    assert c6.observe(facts(121, 12))["status"] == "passed"


def test_missing_baseline_and_same_tick_never_prove_output():
    c6 = LayaC6Acceptance("new")
    baseline = facts()
    baseline["production"] = {}
    c6.choose(choice(), baseline, index=0)
    assert c6.observe(facts(110, 10))["status"] == "unknown"
    c6 = armed()
    assert c6.observe(facts(100, 10))["status"] == "failed"
    assert c6.observe(facts(101, 10))["status"] == "passed"


def test_ops_c6_uses_choice_output_instead_of_rate_proxy():
    from training.laya_mission import phase
    board = {"measured": False}
    assert not ops_phase_reached(phase("C6"), board, {})
    assert not ops_phase_reached(phase("C6"), board, {"status": "passed", "version": "old"})
    assert ops_phase_reached(phase("C6"), board, armed().observe(facts(110, 10)))


def test_runner_journals_fresh_choice_baseline_and_final_output_without_changing_strict_c6(tmp_path, monkeypatch):
    import training.laya_ops_runner as runner
    from training.laya_mission import phase

    selected = choice()
    monkeypatch.setattr(runner, "compile_options", lambda *a, **kw: {"options": [selected]})
    monkeypatch.setattr(runner, "current_phase", lambda reached: None if "C6" in reached else phase("C6"))

    class World:
        root = None
        tick = 100
        produced = 9

        def query(self):
            self.tick += 1
            # Before execution: output during policy deliberation is inherited by the fresh baseline.
            if self.tick == 102:
                self.produced += 5
            return facts(self.tick, self.produced)

    world = World()

    class Session:
        acceptance_fresh = {"C6": "failed"}

        def acceptance(self):
            return self.acceptance_fresh

        def run(self, family, **params):
            world.produced += 1
            return {"ok": False, "operation": family, "params": params, "code": "temporary_mall_batch"}

        def close(self):
            pass

    class Policy:
        authority = "test"

        def choose(self, state, menu):
            return {"option_id": selected["option_id"], "need_id": ITEM, "probability": 1,
                    "rules": [], "distribution": []}

    summary = runner.run(world, Policy(), journal=tmp_path / "journal.jsonl", status_path=tmp_path / "status.json", decisions=2,
                         measure_ticks=600, seed=1, run_id="fresh", session=Session())
    evidence = summary["laya_acceptance"]["C6"]
    assert evidence["status"] == "passed"
    assert evidence["baseline"]["tick"] == 102 and evidence["baseline"]["produced_items"] == 14
    assert evidence["recipe_output_delta_items"] == 1
    assert summary["phases_reached"] == ["C6"] and summary["accepted_final"] == []
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["laya_acceptance"]["C6"] == evidence
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    receipts = [r for r in rows if r.get("event") == "laya_acceptance"]
    assert [r["status"] for r in receipts] == ["failed", "failed", "passed"]
    assert receipts[-1]["choice"]["outcome"]["code"] == "temporary_mall_batch"


def test_slow_progressing_choice_has_no_arbitrary_expiry():
    assert armed().observe(facts(100 + 300_000, 10))["status"] == "passed"


def test_legacy_proxy_rate_and_deterministic_pass_cannot_supply_laya_choice_receipt():
    from training.laya_mission import phase, phase_reached
    # Use the same board shape as compile_status instead of manufacturing a success flag.
    from training.laya_factory_status import compile_status
    measurement = {"window_ticks": 600, "production": {ITEM: {"per_second": 1}}, "consumption": {}}
    board = compile_status(facts(), phase("C6")["targets"], measurement)
    assert phase_reached(board)
    assert not ops_phase_reached(phase("C6"), board, LayaC6Acceptance("new").snapshot())


def recycling_facts(tick=100, produced=9):
    sample = facts(tick, produced)
    sample["recipe_observations"]["scrap-recycling"] = {
        "enabled": True, "products": [{"name": ITEM, "type": "item", "amount_min": 0, "amount_max": 1}],
        "machine_speeds": {"recycler": .5}}
    sample["infrastructure_counts"] = {"assembling-machine-2": 1}
    return sample


def test_recycling_catalog_without_recycler_allows_recipe_attribution_after_restoration():
    c6 = LayaC6Acceptance("space-age")
    c6.choose(choice(), recycling_facts(), index=0)
    sample = recycling_facts(200, 10)
    sample["machines"] = [{"recipe": "copper-cable", "products_finished": 1000}]
    evidence = c6.observe(sample)
    assert evidence["status"] == "passed"
    assert evidence["observed"]["absent_alternate_machines"] == ["recycler"]
    assert evidence["baseline"]["alternate_recipes"] == ["scrap-recycling"]


@pytest.mark.parametrize("mutation", ["recycler", "other_capable_machine", "missing_counts", "bad_count", "missing_capability"])
def test_alternate_machine_presence_or_unknown_capability_invalidates_entire_choice_interval(mutation):
    baseline = recycling_facts()
    if mutation == "other_capable_machine":
        baseline["recipe_observations"]["scrap-recycling"]["machine_speeds"]["assembling-machine-1"] = .5
    c6 = LayaC6Acceptance("space-age")
    c6.choose(choice(), baseline, index=0)
    sample = copy.deepcopy(baseline)
    sample.update(tick=110, production={ITEM: 9})
    if mutation == "recycler": sample["infrastructure_counts"]["recycler"] = 1
    if mutation == "other_capable_machine": sample["infrastructure_counts"]["assembling-machine-1"] = 1
    if mutation == "missing_counts": sample.pop("infrastructure_counts")
    if mutation == "bad_count": sample["infrastructure_counts"]["recycler"] = None
    if mutation == "missing_capability": sample["recipe_observations"]["scrap-recycling"].pop("machine_speeds")
    assert c6.observe(sample)["status"] == "unknown"
    # Removing the machine or repairing telemetry cannot heal the ambiguous interval.
    assert c6.observe(recycling_facts(120, 10))["status"] == "unknown"
