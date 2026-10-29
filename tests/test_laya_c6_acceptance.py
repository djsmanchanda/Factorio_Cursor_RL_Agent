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
    assert ops_phase_reached(dict(phase("C6"), ops_acceptance=VERSION), board, armed().observe(facts(110, 10)))
    assert not ops_phase_reached(phase("C6"), board, armed().observe(facts(110, 10)))


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
            return consumer_facts(self.tick, self.produced, self.tick)

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

    summary = runner.run(world, Policy(), journal=tmp_path / "journal.jsonl", status_path=tmp_path / "status.json", decisions=3,
                         measure_ticks=600, seed=1, run_id="fresh", session=Session())
    evidence = summary["laya_acceptance"]["C6"]
    assert evidence["status"] == "passed"
    assert evidence["output"]["baseline"]["tick"] == 102 and evidence["output"]["baseline"]["produced_items"] == 14
    assert evidence["output"]["recipe_output_delta_items"] == 1
    assert evidence["consumer_progression"]["after"]["products_finished"] > evidence["consumer_progression"]["before"]["products_finished"]
    assert summary["phases_reached"] == ["C6"] and summary["accepted_final"] == []
    status = json.loads((tmp_path / "status.json").read_text())
    assert status["laya_acceptance"]["C6"]["output"] == evidence["output"]
    # The board is the last decision snapshot; final phase recognition is journaled before exit.
    assert status["laya_acceptance"]["C6"]["status"] == "failed"
    rows = [json.loads(line) for line in (tmp_path / "journal.jsonl").read_text().splitlines()]
    receipts = [r for r in rows if r.get("event") == "laya_acceptance"]
    assert [r["status"] for r in receipts] == ["failed", "failed", "failed", "failed", "passed"]
    assert receipts[-1]["output"]["choice"]["outcome"]["code"] == "temporary_mall_batch"


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


def consumer_facts(tick=100, produced=9, crafts=0, *, unit=1, recipe="consumer"):
    sample = facts(tick, produced)
    sample["recipe_observations"]["consumer"] = {
        "enabled": True, "ingredients": [{"name": ITEM, "type": "item", "amount": 1}],
        "products": [{"name": "consumer", "type": "item", "amount": 1}]}
    sample["recipes"]["consumer"] = {"ingredients": [{"name": ITEM, "amount": 1}],
                                       "product_amount": 1, "energy_ticks": 60}
    sample["machines"] = [{"name": "assembling-machine-2", "position": {"x": 0, "y": 0},
                            "recipe": recipe, "products_finished": crafts}]
    if unit is not None:
        sample["machines"][0]["unit_number"] = unit
    return sample


@pytest.mark.parametrize("unit", [1, None])
def test_v3_requires_two_post_choice_same_recipe_machine_observations(unit):
    from training.laya_phase_acceptance import LayaC6ConsumerAcceptance
    c6 = LayaC6ConsumerAcceptance("run")
    c6.choose(choice(), consumer_facts(crafts=999, unit=unit), index=0)
    assert c6.observe(consumer_facts(110, 10, 1000, unit=unit))["status"] == "failed"
    c6.choose(choice("consumer"), consumer_facts(110, 10, 1000, unit=unit), index=1)
    restored = LayaC6ConsumerAcceptance("run")
    restored.evidence = json.loads(json.dumps(c6.snapshot()))
    receipt = restored.observe(consumer_facts(120, 10, 1001, unit=unit))
    assert receipt["status"] == "passed"
    assert receipt["output"]["choice"]["params"]["item"] == ITEM


@pytest.mark.parametrize("confound", ["new", "retarget", "missing", "regressed", "absent"])
def test_v3_rejects_consumer_identity_recipe_and_counter_confounds(confound):
    from training.laya_phase_acceptance import LayaC6ConsumerAcceptance
    c6 = LayaC6ConsumerAcceptance("run")
    c6.choose(choice(), consumer_facts(), index=0)
    c6.observe(consumer_facts(110, 10, 5))
    middle = consumer_facts(120, 10, 6)
    if confound == "new":
        middle["machines"][0]["unit_number"] = 2
    elif confound == "retarget":
        middle["machines"][0]["recipe"] = ITEM
    elif confound == "missing":
        middle["machines"][0].pop("products_finished")
    elif confound == "regressed":
        middle["machines"][0]["products_finished"] = 0
    else:
        middle["machines"] = []
    assert c6.observe(middle)["status"] == "failed"
    # A return/new/retarget needs its own second same-recipe observation.
    if confound != "regressed":
        assert c6.observe(consumer_facts(130, 10, 7))["status"] == "failed"


def test_c6_consumer_goal_recovers_wait_only_menu_without_fixed_consumer(monkeypatch):
    from planners import recipe_data
    from training.laya_phase_acceptance import consumer_goal, CONSUMER_VERSION
    from training.laya_ops_runner import compile_options
    from training.laya_mission import phase
    board = {"needs": []}
    sample = consumer_facts()
    sample["recipe_observations"]["another"] = copy.deepcopy(sample["recipe_observations"]["consumer"])
    recipes = {i: {"ingredients": [{"name": ITEM, "amount": 1}], "product_amount": 1, "energy_ticks": 60}
               for i in ("consumer", "another")}
    for item in ("consumer", "another"):
        monkeypatch.setitem(recipe_data.LINE_RECIPES, item, {"ingredients": [ITEM], "amounts": [1]})
    results = [{"ok": True, "operation": "reserve_steel_seed", "params": {}}]
    assert all(o["family"] == "wait" for o in compile_options(board, results, recipes=recipes, stock={ITEM: 20})["options"])
    consumer_goal(board, phase("C6"), {"version": CONSUMER_VERSION}, sample)
    options = compile_options(board, results, recipes=recipes, stock={ITEM: 20})["options"]
    offered = {o["product"] for o in options if o["family"] == "produce" and o["features"]["goal_need"]}
    assert offered == {"consumer", "another"}
    assert not any(o["family"] == "produce" for o in compile_options(board, results, recipes=recipes, stock={})["options"])


def test_saved_world_optional_craft_counter_schema_is_backward_compatible():
    from training.laya_saved_world import FACTS_VALIDATOR
    sample = consumer_facts()
    sample.update(player_stock={}, pending_ghosts=0, poles=[], researched={})
    for recipe in sample["recipe_observations"].values():
        recipe.update(categories=["crafting"], machine_speeds={"assembling-machine-2": 1}, energy_ticks=60)
    machine = sample["machines"][0]
    machine.update(energy=0, status="working")
    assert not list(FACTS_VALIDATOR.iter_errors(sample))
    machine.pop("products_finished")
    machine.pop("unit_number")
    assert not list(FACTS_VALIDATOR.iter_errors(sample))
    machine["products_finished"] = -1
    assert list(FACTS_VALIDATOR.iter_errors(sample))


def test_batch_reports_v2_and_v3_receipts_as_observed_endpoints(tmp_path):
    from types import SimpleNamespace
    from tools.run_laya_batch import Batch
    from training.laya_phase_acceptance import CONSUMER_VERSION
    batch = object.__new__(Batch)
    batch.root = tmp_path
    batch.cfg = {"name": "consumer", "arms": [{"id": "v2"}, {"id": "v3"}]}
    batch.ledger = SimpleNamespace(events=[])
    batch.admission = {}
    batch.jobs = [{"arm": arm, "seed": 1, "status": "completed",
                   "laya_acceptance": {"C6": {"version": version, "status": "passed"}}}
                  for arm, version in (("v2", VERSION), ("v3", CONSUMER_VERSION))]
    batch.report()
    assert (tmp_path / "report.md").read_text().count("1/1 observed") == 2
    report = json.loads((tmp_path / "report.json").read_text())
    endpoint = report["paired"]["v3_vs_v2"]["versioned_endpoints"]["C6_v2"]
    assert endpoint == {"observed_pairs": 1, "new_only": 0, "old_only": 0}


@pytest.mark.parametrize("change", ["scope", "catalog", "tick"])
def test_v3_cannot_heal_invalid_consumer_interval_after_output_passes(change):
    from training.laya_phase_acceptance import LayaC6ConsumerAcceptance
    c6 = LayaC6ConsumerAcceptance("run")
    c6.choose(choice(), consumer_facts(), index=0)
    c6.observe(consumer_facts(110, 10, 1))
    sample = consumer_facts(120, 10, 2)
    if change == "scope":
        sample["force"] = "other"
    elif change == "catalog":
        sample["mods"]["base"] = "changed"
    else:
        sample["tick"] = 109
    assert c6.observe(sample)["status"] == "unknown"
    assert c6.observe(consumer_facts(130, 10, 3))["status"] == "unknown"


def test_consumer_progress_before_ac_output_survives_wait_choices():
    from training.laya_phase_acceptance import LayaC6ConsumerAcceptance
    c6 = LayaC6ConsumerAcceptance("run")
    c6.choose(choice(), consumer_facts(), index=0)
    c6.observe(consumer_facts(110, 9, 1))
    c6.choose({"family": "wait"}, consumer_facts(110, 9, 1), index=1)
    receipt = c6.observe(consumer_facts(120, 9, 2))
    assert receipt["status"] == "failed" and receipt["consumer_progression"]
    assert c6.observe(consumer_facts(130, 10, 2))["status"] == "passed"


def test_runner_requests_catalog_consumer_closure_and_projects_goal(tmp_path, monkeypatch):
    import training.laya_ops_runner as runner
    from planners import recipe_data
    from training.laya_mission import phase
    monkeypatch.setattr(runner, "current_phase", lambda reached: phase("C6"))
    monkeypatch.setitem(recipe_data.LINE_RECIPES, "consumer", {"ingredients": [ITEM], "amounts": [1]})

    class World:
        root = None
        queries = 0

        def query(self):
            self.queries += 1
            sample = consumer_facts()
            if "consumer" not in self.extra_recipes:
                sample["recipes"].pop("consumer")
            return sample

    class Policy:
        authority = "test"

        def choose(self, state, menu):
            assert state["observation_digest"]["factory_status"]["consumer_goal"]["recipes"] == ["consumer"]
            option = next(o for o in menu["options"] if o["option_id"] == "op.produce.consumer")
            assert option["features"]["goal_need"]
            return {"option_id": option["option_id"], "need_id": option["need_id"],
                    "probability": 1, "rules": [], "distribution": []}

    class Session:
        def run(self, operation, **params):
            return {"ok": True, "operation": operation, "params": params}

        def close(self):
            pass

    world = World()
    runner.run(world, Policy(), journal=tmp_path / "journal.jsonl", decisions=1,
               measure_ticks=600, seed=1, run_id="catalog", session=Session())
    assert "consumer" in world.extra_recipes


def test_consumer_goal_offers_only_consumers_the_builder_can_make(monkeypatch):
    # C46c: enabled, obtainable beacon and tank were offered and failed 115 times with "No recipe knowledge".
    from planners import recipe_data
    from training.laya_phase_acceptance import consumer_goal, CONSUMER_VERSION
    from training.laya_ops_runner import compile_options
    from training.laya_mission import phase
    board = {"needs": []}
    sample = consumer_facts()
    sample["recipe_observations"]["beacon"] = copy.deepcopy(sample["recipe_observations"]["consumer"])
    recipes = {i: {"ingredients": [{"name": ITEM, "amount": 1}], "product_amount": 1, "energy_ticks": 60}
               for i in ("consumer", "beacon")}
    monkeypatch.setitem(recipe_data.LINE_RECIPES, "consumer", {"ingredients": [ITEM], "amounts": [1]})
    monkeypatch.delitem(recipe_data.LINE_RECIPES, "beacon", raising=False)
    consumer_goal(board, phase("C6"), {"version": CONSUMER_VERSION}, sample)
    assert {"consumer", "beacon"} <= set(board["consumer_goal"]["recipes"])  # still observed as a fact
    options = compile_options(board, [{"ok": True, "operation": "reserve_steel_seed", "params": {}}],
                              recipes=recipes, stock={ITEM: 20})["options"]
    assert {o["product"] for o in options if o["family"] == "produce"} == {"consumer"}
