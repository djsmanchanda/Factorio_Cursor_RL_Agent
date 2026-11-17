# Path: tests/test_laya_blocker_unchanged.py | Purpose: Protect the blocker_unchanged option fact (a refused/deferred option whose blocker has not changed) and that the control arm's choices do not depend on it.

import json

from training.laya_guided_policy import GuidedPolicy
from training.laya_ops_checkpoint import decode, encode
from training.laya_ops_runner import blocker_unchanged, compile_options, run

BOARD = {"needs": [{"item": "iron-plate", "condition": "unbuilt", "goal_required_per_s": 0.5},
                   {"item": "copper-plate", "condition": "unbuilt", "goal_required_per_s": 0.5}]}
KEY = ("produce", "plastic-bar")
DEFERRED = {"ok": False, "operation": "produce", "params": {"item": "plastic-bar"}, "code": "chemical_capability_batch",
            "details": {"required_stock": 100, "rung": "pipe", "target": "plastic-bar"},
            "blocker_stock": {"pipe": 4, "plastic-bar": 0}}
WAIT = {"ok": True, "operation": "wait", "params": {"ticks": 300}}


def test_the_fact_holds_while_the_named_stock_and_the_world_are_unchanged():
    assert blocker_unchanged([DEFERRED, WAIT], KEY, {"pipe": 4})
    assert blocker_unchanged([DEFERRED], KEY, {"pipe": 2})  # consumed, not gained: still blocked
    assert not blocker_unchanged([], KEY, {})  # never tried


def test_the_fact_clears_when_the_blocker_changes():
    assert not blocker_unchanged([DEFERRED], KEY, {"pipe": 5})  # a named item gained stock
    other = {"ok": True, "operation": "mall_slot", "params": {"item": "pipe"}}
    assert not blocker_unchanged([DEFERRED, other], KEY, {"pipe": 4})  # something else succeeded since
    built = {"ok": False, "operation": "produce", "params": {"item": "pipe"}, "code": "prerequisite_advanced"}
    assert not blocker_unchanged([DEFERRED, built], KEY, {"pipe": 4})  # something was put into the world


def test_the_fact_clears_when_the_option_succeeds():
    done = {"ok": True, "operation": "produce", "params": {"item": "plastic-bar"}}
    assert not blocker_unchanged([DEFERRED, done], KEY, {"pipe": 4})


def test_restart_keeps_what_the_checkpoint_keeps_and_drops_what_it_cannot_rederive():
    # Exact ops recovery restores ``results`` through the controller encoding: the fact survives.
    restored = decode(json.loads(json.dumps(encode([DEFERRED, WAIT]))))
    assert blocker_unchanged(restored, KEY, {"pipe": 4})
    # A failure recorded before the signature existed cannot be re-derived: not marked.
    legacy = {k: v for k, v in DEFERRED.items() if k != "blocker_stock"}
    assert not blocker_unchanged([legacy], KEY, {"pipe": 4})


def test_menu_options_carry_the_fact():
    shortage = {"ok": False, "operation": "foundation", "params": {"recipe": "iron-plate"},
                "code": "material_shortage", "details": {"required": {"transport-belt": 10}},
                "blocker_stock": {"transport-belt": 3}}
    results = [{"ok": True, "operation": "starter", "params": {"recipe": p}} for p in ("iron-plate", "copper-plate")]
    options = {o["option_id"]: o for o in compile_options(BOARD, results + [shortage], stock={"transport-belt": 3})["options"]}
    assert options["op.foundation.iron-plate"]["features"]["blocker_unchanged"] is True
    assert options["op.foundation.copper-plate"]["features"]["blocker_unchanged"] is False


class World:
    root = None

    def __init__(self):
        self.tick = 1000

    def query(self):
        return {"tick": self.tick, "production": {}, "consumption": {}, "machines": [], "networks": {}, "ports": [],
                "resources": [], "recipes": {}}

    def advance(self, ticks):
        self.tick += ticks


class RefusingSession:
    """Starters succeed; every foundation is refused the same way (an unchanged blocker)."""

    def run(self, operation, **params):
        if operation == "foundation":
            return {"ok": False, "operation": operation, "params": params, "code": "district_expansion_unbuildable",
                    "details": {"cause": "no_second_site"}}
        return {"ok": True, "operation": operation, "params": params}

    def close(self):
        pass


def _choices(tmp_path, name, policy):
    journal = tmp_path / name / "journal.jsonl"
    run(World(), policy, journal=journal, decisions=40, measure_ticks=600, seed=7, run_id="t",
        session=RefusingSession())
    rows = [json.loads(line) for line in (journal.parent / "decision-states.jsonl").read_text().splitlines()]
    marked = sum(any(o["option_id"] == r["choice"]["option_id"] and o["features"].get("blocker_unchanged")
                     for o in r["options"]) for r in rows)
    return [(r["choice"]["option_id"], r["choice"]["probability"]) for r in rows], marked


def test_control_arm_choices_are_identical_without_the_fact(tmp_path, monkeypatch):
    import training.laya_ops_runner as runner
    with_fact, marked = _choices(tmp_path, "with", GuidedPolicy(7))
    assert marked  # the fact was set on chosen options in this run
    monkeypatch.setattr(runner, "blocker_unchanged", lambda *args: False)
    without_fact, _ = _choices(tmp_path, "without", GuidedPolicy(7))
    assert with_fact == without_fact


def test_the_variant_down_weights_marked_options_only():
    from training.laya_clean_runner import ROOT
    from training.laya_guided_policy import load_preferences
    from training.laya_variants import apply, load_variant
    settings, policy, _ = apply(load_variant(ROOT / "docs/rl/laya_variants/ops-blocker-unchanged.json"))
    prefs = load_preferences(ROOT / settings.catalog)
    control = load_preferences(ROOT / "docs/rl/laya_ops_guidelines.json")
    assert prefs["mission.ops-history-no-repeat"] == [{"when": {"blocker_unchanged": True}, "weight": 0.05}]
    assert "mission.ops-history-no-repeat" not in control  # the control catalog never reads the fact
    assert {k: v for k, v in prefs.items() if k != "mission.ops-history-no-repeat"} == control and not policy
