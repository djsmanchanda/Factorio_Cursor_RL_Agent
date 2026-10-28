# Path: training/laya_phase_acceptance.py | Purpose: Versioned Laya-only choice/output evidence, separate from deterministic checkpoints.
"""C6 is an achievement within one isolated, automated ops run, never a stock test.

The saved-world-v1 helper surveys the complete force recipe catalog and engine
item-production statistics. A sole feasible item-producing recipe permits
attribution even when a finite loan has already been restored. Alternatives
are excluded only with complete capability and full-surface infrastructure facts.
This contract assumes the disposable world has no concurrent player/script item
production; it does not establish a particular machine's output or item genealogy.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from collections.abc import Mapping

VERSION = "laya-c6-choice-output-v2"
ITEM = "advanced-circuit"
SOURCE = "force.get_item_production_statistics(surface).get_input_count"
MALL_OPERATIONS = {"produce", "mall_slot", "grow", "raise_stock_gate"}


def _sample(facts: Mapping) -> dict:
    if facts.get("version") != "saved-world-v1" or not facts.get("mods"):
        raise ValueError("missing observation provenance")
    if facts.get("surface") != "nauvis" or facts.get("force") != "player":
        raise ValueError("unsupported production scope")
    recipes = facts.get("recipe_observations")
    if not isinstance(recipes, Mapping) or not recipes:
        raise ValueError("complete recipe catalog unavailable")
    producers = []
    for name, recipe in recipes.items():
        if not isinstance(recipe, Mapping) or "products" not in recipe:
            raise ValueError("incomplete recipe catalog")
        products = recipe["products"] or []
        if not isinstance(products, list) or any(not isinstance(p, Mapping) for p in products):
            if products != []:
                raise ValueError("malformed recipe products")
        if any(p.get("type") == "item" and p.get("name") == ITEM for p in products):
            producers.append(name)
    if ITEM not in producers:
        raise ValueError("advanced-circuit output recipe unavailable")
    excluded_machines = set()
    alternatives = sorted(name for name in producers if name != ITEM)
    if alternatives:
        infrastructure = facts.get("infrastructure_counts")
        if not isinstance(infrastructure, Mapping):
            raise ValueError("complete infrastructure counts unavailable")
        if any(type(count) is not int or count < 0 for count in infrastructure.values()):
            raise ValueError("malformed infrastructure counts")
        for name in alternatives:
            capable = recipes[name].get("machine_speeds")
            if not isinstance(capable, Mapping) or not capable:
                raise ValueError("alternate recipe machine capability unavailable")
            if any(not isinstance(speed, (int, float)) or isinstance(speed, bool)
                   or not math.isfinite(speed) or speed <= 0 for speed in capable.values()):
                raise ValueError("alternate recipe machine capability malformed")
            if any(infrastructure.get(machine, 0) > 0 for machine in capable):
                raise ValueError("alternate circuit-producing machine present")
            excluded_machines.update(capable)
    if recipes[ITEM].get("enabled") is not True:
        raise ValueError("advanced-circuit recipe unavailable")
    tick, count = facts.get("tick"), (facts.get("production") or {}).get(ITEM)
    if type(tick) is not int or tick < 0:
        raise ValueError("invalid observation tick")
    if isinstance(count, bool) or not isinstance(count, (int, float)) or not math.isfinite(count) or count < 0:
        raise ValueError("advanced-circuit production counter unavailable")
    signature = hashlib.sha256(json.dumps({"recipes": recipes, "mods": facts["mods"]},
                                          sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"tick": tick, "produced_items": count, "surface": facts["surface"], "force": facts["force"],
            "catalog_mod_sha256": signature, "source": SOURCE, "units": "items", "recipe": ITEM,
            "attribution": "sole_feasible_recipe_in_isolated_automated_run",
            "alternate_recipes": alternatives, "absent_alternate_machines": sorted(excluded_machines)}


class LayaC6Acceptance:
    """Keep one active choice or successful receipt; never import evidence from earlier runs."""

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.evidence = {"version": VERSION, "run_id": run_id, "status": "failed", "reason": "no_explicit_choice"}

    @staticmethod
    def explicit_choice(chosen: Mapping) -> bool:
        return (chosen.get("family") in {"produce", "mall_slot", "grow"}
                and (chosen.get("params") or {}).get("item") == ITEM
                and chosen.get("product", ITEM) == ITEM)

    def choose(self, chosen: Mapping, facts: Mapping, *, index: int) -> None:
        if self.evidence["status"] == "passed":
            return
        # A hidden circuit prerequisite of another mall product cannot inherit this receipt.
        if not self.explicit_choice(chosen):
            if chosen.get("family") in MALL_OPERATIONS:
                self.evidence = {"version": VERSION, "run_id": self.run_id, "status": "failed",
                                 "reason": "superseded_by_other_mall_choice"}
            return
        choice = {"index": index, "option_id": chosen["option_id"], "family": chosen["family"],
                  "params": copy.deepcopy(chosen["params"]), "summary": chosen.get("summary")}
        self.evidence = {"version": VERSION, "run_id": self.run_id, "status": "unknown",
                         "choice": choice, "window": "current_run_until_superseding_mall_choice"}
        try:
            self.evidence["baseline"] = _sample(facts)
            self.evidence.update(status="failed", reason="no_post_choice_output")
        except (ValueError, TypeError, KeyError) as error:
            self.evidence["reason"] = str(error)

    def outcome(self, result: Mapping) -> None:
        if "choice" in self.evidence and "outcome" not in self.evidence["choice"]:
            self.evidence["choice"]["outcome"] = {k: result.get(k) for k in ("ok", "code", "operation")}

    def observe(self, facts: Mapping) -> dict:
        baseline = self.evidence.get("baseline")
        if baseline is None or self.evidence["status"] in {"passed", "unknown"}:
            return self.snapshot()
        try:
            observed = _sample(facts)
            self.evidence["observed"] = observed
            if any(observed[k] != baseline[k] for k in ("surface", "force", "catalog_mod_sha256", "source", "units")):
                raise ValueError("production scope/catalog changed")
            previous = self.evidence.get("last_tick", baseline["tick"])
            previous_count = self.evidence.get("last_count", baseline["produced_items"])
            if observed["tick"] < previous or observed["produced_items"] < previous_count:
                raise ValueError("production tick/counter regressed")
            self.evidence.update(last_tick=observed["tick"], last_count=observed["produced_items"])
            elapsed = observed["tick"] - baseline["tick"]
            delta = observed["produced_items"] - baseline["produced_items"]
            self.evidence["recipe_output_delta_items"] = delta
            if elapsed > 0 and delta > 0:
                self.evidence.update(status="passed", reason="explicit_choice_then_recipe_output")
        except (ValueError, TypeError, KeyError) as error:
            # An invalid interval cannot be healed by silently rebasing a later sample.
            self.evidence.update(status="unknown", reason=str(error))
        return self.snapshot()

    def snapshot(self) -> dict:
        return copy.deepcopy(self.evidence)


def ops_phase_reached(phase: Mapping, board: Mapping, c6: Mapping) -> bool:
    from training.laya_mission import phase_reached
    if phase["phase_id"] == "C6":
        return c6.get("version") == phase.get("ops_acceptance", VERSION) and c6.get("status") == "passed"
    return phase_reached(board)


CONSUMER_VERSION = "laya-c6-choice-output-v3"


def consumer_recipes(facts: Mapping) -> set[str]:
    """Observed recipe facts, without prescribing which consumer to choose."""
    return {name for name, recipe in (facts.get("recipe_observations") or {}).items()
            if any(p.get("type") == "item" and p.get("name") == ITEM
                   for p in recipe.get("ingredients", ()))}


class LayaC6ConsumerAcceptance:
    """V2 output evidence plus same-machine, same-recipe post-choice progression.

    Ownership is surface/force membership in the isolated automated run's survey.
    Samples live in the receipt so checkpoint recovery preserves the interval.
    """

    def __init__(self, run_id: str):
        self.run_id = run_id
        self.evidence = {"version": CONSUMER_VERSION, "run_id": run_id, "status": "failed",
                         "reason": "no_explicit_choice"}

    explicit_choice = staticmethod(LayaC6Acceptance.explicit_choice)

    def _output(self):
        output = LayaC6Acceptance(self.run_id)
        if "output" in self.evidence:
            output.evidence = copy.deepcopy(self.evidence["output"])
        return output

    def choose(self, chosen: Mapping, facts: Mapping, *, index: int) -> None:
        if self.evidence["status"] == "passed":
            return
        output = self._output()
        if not self.explicit_choice(chosen) and chosen.get("family") not in MALL_OPERATIONS:
            return
        # Consumer choices must retain the AC choice window they are fulfilling.
        if output.evidence.get("baseline") and (output.evidence["status"] == "passed"
                or (chosen.get("params") or {}).get("item") in consumer_recipes(facts)):
            return
        output.choose(chosen, facts, index=index)
        self.evidence = {"version": CONSUMER_VERSION, "run_id": self.run_id,
                         "status": output.evidence["status"], "reason": output.evidence.get("reason"),
                         "output": output.snapshot(), "consumer_samples": {}}

    def outcome(self, result: Mapping) -> None:
        output = self._output()
        output.outcome(result)
        self.evidence["output"] = output.snapshot()

    def observe(self, facts: Mapping) -> dict:
        if self.evidence["status"] in {"passed", "unknown"}:
            return self.snapshot()
        output = self._output()
        receipt = output.observe(facts)
        self.evidence["output"] = receipt
        baseline = receipt.get("baseline")
        if not baseline or receipt["status"] == "unknown":
            self.evidence.update(status=receipt["status"], reason=receipt.get("reason"))
            return self.snapshot()
        try:
            observed = _sample(facts)
            if any(observed[k] != baseline[k] for k in ("surface", "force", "catalog_mod_sha256")):
                raise ValueError("consumer scope/catalog changed")
            if observed["tick"] < self.evidence.get("consumer_last_tick", baseline["tick"]):
                raise ValueError("consumer observation tick regressed")
        except (ValueError, TypeError, KeyError) as error:
            self.evidence.update(status="unknown", reason=str(error))
            return self.snapshot()
        tick = observed["tick"]
        self.evidence["consumer_last_tick"] = tick
        previous = self.evidence.get("consumer_samples", {})
        current = {}
        consumers = consumer_recipes(facts)
        if type(tick) is int and tick > baseline["tick"]:
            for machine in facts.get("machines", ()):
                recipe, count = machine.get("recipe"), machine.get("products_finished")
                if recipe not in consumers or type(count) is not int or count < 0:
                    continue
                identity = (["unit", machine["unit_number"]] if machine.get("unit_number") is not None
                            else [machine.get("name"), machine.get("position")])
                key = json.dumps(identity, sort_keys=True)
                sample = {"recipe": recipe, "products_finished": count, "tick": tick}
                old = previous.get(key)
                if (old and old["recipe"] == recipe and tick > old["tick"]
                        and count > old["products_finished"]):
                    self.evidence["consumer_progression"] = {"identity": identity, "before": old, "after": sample}
                current[key] = sample
            self.evidence["consumer_samples"] = current
        passed = receipt["status"] == "passed" and bool(self.evidence.get("consumer_progression"))
        self.evidence.update(status="passed" if passed else "failed",
                             reason="choice_output_and_consumer_progression" if passed else "consumer_or_output_unmet")
        return self.snapshot()

    def snapshot(self) -> dict:
        return copy.deepcopy(self.evidence)


def consumer_goal(board: dict, phase: Mapping, evidence: Mapping, facts: Mapping) -> None:
    if phase.get("ops_acceptance") == CONSUMER_VERSION and not evidence.get("consumer_progression"):
        board["consumer_goal"] = {"ingredient": ITEM, "condition": "unmet",
                                  "recipes": sorted(consumer_recipes(facts))}
