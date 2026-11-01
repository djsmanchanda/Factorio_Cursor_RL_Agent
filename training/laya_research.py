# Path: training/laya_research.py | Purpose: Versioned earned research acceptance and measured science-demand curriculum.
# Ported from local laya/release 5a5ed28:training/laya_research.py. finite_material_progress omitted
# until training.laya_ops_runner provides material_results/_demand_bills.
from __future__ import annotations
import copy
import math

VERSION = "laya-research-consumption-v2"
SUPPORT = ("iron-plate", "copper-plate", "steel-plate", "electronic-circuit", "advanced-circuit", "plastic-bar")
# The independent /export_recipe_catalog document. Older exports are still
# accepted, but a bare name->recipe mapping is never completeness evidence.
_CATALOG_VERSIONS = frozenset({"1.0.0", "1.1.0", "1.2.0", "1.3.0"})
# The installed-consumer census is a separate same-sample document. Its
# contract is documented on _installed_consumers; the version is explicit so a
# changed shape cannot be mistaken for complete evidence.
_INVENTORY_VERSIONS = frozenset({"1.0.0"})
_CONSUMER_KINDS = frozenset({"none", "recipe", "science"})
# Interval-wide earned-provenance receipt. This document is caller-provided;
# nothing in this module independently observes the interval or verifies the
# receipt's truth. It is accepted only as an explicit, internally consistent
# contract, never as a boolean trust flag.
_PROVENANCE_VERSIONS = frozenset({"1.0.0"})
_PROVENANCE_SCOPES = frozenset({"force-surface"})


def _stock(facts):
    total = {}
    for network in (facts.get("networks") or {}).values():
        for item, count in network.get("items", {}).items():
            total[item] = total.get(item, 0) + count
    return total


def _catalog_recipes(raw):
    """Index either the export's recipe list or a name-keyed recipe mapping."""
    recipes = {}
    if isinstance(raw, dict):
        for name, recipe in raw.items():
            if not isinstance(name, str) or not name or not isinstance(recipe, dict):
                raise ValueError("complete consumption recipe catalog unavailable")
            recipes[name] = recipe
        return recipes
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("complete consumption recipe catalog unavailable")
            name = entry.get("name")
            if not isinstance(name, str) or not name or name in recipes:
                raise ValueError("complete consumption recipe catalog unavailable")
            recipes[name] = entry
        return recipes
    raise ValueError("complete consumption recipe catalog unavailable")


def _catalog_evidence(facts):
    """Return explicit catalog metadata plus the recipe entries it describes."""
    catalog = facts.get("recipe_catalog")
    observations = facts.get("recipe_observations")
    if isinstance(catalog, dict) and "recipes" in catalog:
        return catalog, _catalog_recipes(catalog["recipes"])
    if isinstance(catalog, dict):
        # A metadata-only envelope may describe the legacy observation map, but
        # the map alone is never evidence that it is the complete catalog.
        if not isinstance(observations, dict):
            raise ValueError("complete consumption recipe catalog unavailable")
        return catalog, _catalog_recipes(observations)
    if isinstance(observations, dict) and "recipes" in observations:
        return observations, _catalog_recipes(observations["recipes"])
    raise ValueError("complete consumption recipe catalog unavailable")


def _complete_recipes(facts):
    """Require same-sample identity/count/scope evidence for the whole catalog.

    Integration requirement: ``facts["recipe_catalog"]`` (or an equally shaped
    ``facts["recipe_observations"]``) must be the untouched full export document
    plus its declared ``recipe_count`` (the full export's count, never a filtered
    subset's length); the export may also be carried as metadata around the
    legacy ``recipe_observations`` map. A filtered map is not completeness
    evidence. These checks reject missing, partial and internally inconsistent
    evidence; they do not prove runtime provenance, which stays a caller duty.
    """
    catalog, recipes = _catalog_evidence(facts)
    version = catalog.get("version")
    if version not in _CATALOG_VERSIONS:
        raise ValueError("recipe catalog identity unavailable")
    if version == "1.3.0":
        mods = catalog.get("active_mods")
        count = catalog.get("active_mods_count")
        if (not isinstance(mods, dict) or not mods or type(count) is not int
                or count != len(mods) or any(
                    not isinstance(name, str) or not name
                    or not isinstance(value, str) or not value
                    for name, value in mods.items())):
            raise ValueError("recipe catalog mod identity unavailable")
    if "complete" in catalog and catalog["complete"] is not True:
        raise ValueError("recipe catalog incomplete")
    if catalog.get("force") != facts.get("force"):
        raise ValueError("recipe catalog scope mismatch")
    tick = catalog.get("tick")
    if type(tick) is not int or tick != facts.get("tick"):
        raise ValueError("recipe catalog tick mismatch")
    surface = catalog.get("surface")
    if surface is not None and surface != facts.get("surface"):
        raise ValueError("recipe catalog scope mismatch")
    count = catalog.get("recipe_count", catalog.get("count"))
    if type(count) is not int or count < 0:
        raise ValueError("recipe catalog count unavailable")
    if count != len(recipes):
        raise ValueError("recipe catalog count inconsistent")
    if not recipes:
        raise ValueError("complete consumption recipe catalog unavailable")
    return recipes, catalog


def _ingredient_names(recipe):
    ingredients = recipe.get("ingredients")
    if isinstance(ingredients, dict):
        return set(ingredients)
    if isinstance(ingredients, list):
        names = set()
        for part in ingredients:
            if not isinstance(part, dict):
                raise ValueError("consumption recipe catalog malformed")
            name = part.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("consumption recipe catalog malformed")
            names.add(name)
        return names
    raise ValueError("consumption recipe catalog malformed")


def _installed_consumers(facts, lab_total):
    """Require a complete, same-sample census of every installed entity.

    Integration contract: ``facts["infrastructure_inventory"]`` is produced by
    the world survey at the same surface/force/tick as the research and science
    samples. It must be the complete installed-entity census, not a filtered or
    inferred list::

        {"version": "1.0.0", "force": <force>, "surface": <surface>,
         "tick": <facts tick>, "complete": true,
         "entity_count": <sum of counts>,
         "counts": {<prototype name>: <int count > 0>, ...},
         "entities": {<prototype name>: {
             "consumer": "none" | "recipe" | "science",
             "crafting_categories": [<category>, ...],
             "ingredient_count": <int >= 0, 0 means unlimited>,
             "fixed_recipe": <recipe name or null>}, ...}}

    ``counts`` and ``entities`` must name exactly the same installed prototypes.
    ``recipe`` entities can run matching force recipes (or their fixed recipe),
    ``science`` entities consume science packs outside the recipe system and are
    accepted only for ``lab``, and ``none`` entities cannot consume items.
    Missing scope, an incomplete flag, a count/name mismatch, a malformed or
    unknown consumer class, or an unclassifiable recipe consumer is rejected.
    The legacy ``infrastructure_counts`` map is never completeness evidence on
    its own; when both documents are present they must agree exactly. These
    checks reject missing, partial and internally inconsistent evidence; they
    do not prove runtime provenance, which stays a caller duty.
    """
    raw = facts.get("infrastructure_inventory")
    if not isinstance(raw, dict):
        raise ValueError("installed consumer inventory unavailable")
    if raw.get("version") not in _INVENTORY_VERSIONS:
        raise ValueError("installed consumer inventory identity unavailable")
    if raw.get("force") != facts.get("force"):
        raise ValueError("installed consumer inventory scope mismatch")
    tick = raw.get("tick")
    if type(tick) is not int or tick != facts.get("tick"):
        raise ValueError("installed consumer inventory scope mismatch")
    surface = raw.get("surface")
    if not isinstance(surface, str) or not surface or surface != facts.get("surface"):
        raise ValueError("installed consumer inventory scope mismatch")
    if raw.get("complete") is not True:
        raise ValueError("installed consumer inventory incomplete")
    counts, capabilities = raw.get("counts"), raw.get("entities")
    if not isinstance(counts, dict) or not isinstance(capabilities, dict):
        raise ValueError("installed consumer inventory malformed")
    if set(counts) != set(capabilities):
        raise ValueError("installed consumer inventory malformed")
    total = 0
    for name, count in counts.items():
        if (not isinstance(name, str) or not name or type(count) is not int or count <= 0):
            raise ValueError("installed consumer inventory malformed")
        total += count
        capability = capabilities[name]
        if not isinstance(capability, dict):
            raise ValueError("installed consumer inventory malformed")
        kind = capability.get("consumer")
        if kind not in _CONSUMER_KINDS:
            raise ValueError("installed consumer inventory malformed")
        categories = capability.get("crafting_categories")
        if categories is None:
            categories = []
        if (not isinstance(categories, list)
                or any(not isinstance(category, str) or not category for category in categories)
                or len(set(categories)) != len(categories)):
            raise ValueError("installed consumer inventory malformed")
        fixed = capability.get("fixed_recipe")
        if fixed is not None and (not isinstance(fixed, str) or not fixed):
            raise ValueError("installed consumer inventory malformed")
        if kind == "none":
            if categories or fixed is not None:
                raise ValueError("installed consumer inventory malformed")
        elif kind == "science":
            if name != "lab":
                raise ValueError("alternate pack consumer present")
            if categories or fixed is not None:
                raise ValueError("installed consumer inventory malformed")
        else:
            limit = capability.get("ingredient_count")
            if type(limit) is not int or limit < 0:
                raise ValueError("installed consumer inventory malformed")
            if not categories and fixed is None:
                raise ValueError("alternate pack consumer applicability unavailable")
    entity_count = raw.get("entity_count")
    if type(entity_count) is not int or entity_count != total:
        raise ValueError("installed consumer inventory inconsistent")
    if counts.get("lab", 0) != lab_total:
        raise ValueError("installed consumer inventory inconsistent")
    legacy = facts.get("infrastructure_counts")
    if legacy is not None and legacy != counts:
        raise ValueError("installed consumer inventory inconsistent")
    return {name: capabilities[name] for name in counts}


def _recipe_categories(recipe):
    categories = set()
    raw = recipe.get("categories")
    if raw is not None:
        if not isinstance(raw, list) or any(not isinstance(category, str) or not category for category in raw):
            raise ValueError("consumption recipe catalog malformed")
        categories.update(raw)
    category = recipe.get("category")
    if category is not None:
        if not isinstance(category, str) or not category:
            raise ValueError("consumption recipe catalog malformed")
        categories.add(category)
    return categories


def _alternate_pack_consumer(facts, recipes, packs, lab_total):
    """Fail closed unless every installed consumer of the packs is a lab.

    Disabled recipes stay candidates: an existing machine that was configured
    while the recipe was enabled may still execute it, so absence of a live
    unlock is not evidence that pack consumption is lab-only. A recipe whose
    machine applicability cannot be established from the census is rejected
    rather than assumed impossible.
    """
    installed = _installed_consumers(facts, lab_total)
    pack_names = set(packs)
    for name, recipe in recipes.items():
        enabled = recipe.get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("consumption recipe catalog malformed")
        ingredients = _ingredient_names(recipe)
        if not ingredients & pack_names:
            continue
        if (recipe.get("category") == "unavailable" and
                "recipe category unavailable or malformed" in str(recipe.get("unsupported_reason", ""))):
            raise ValueError("alternate pack consumer applicability unavailable")
        categories = _recipe_categories(recipe)
        fixed = [entity for entity, capability in installed.items()
                 if capability.get("fixed_recipe") == name]
        candidates = set(fixed)
        for entity, capability in installed.items():
            if capability.get("consumer") != "recipe" or entity in candidates:
                continue
            if not categories or not categories & set(capability.get("crafting_categories") or []):
                continue
            limit = capability.get("ingredient_count", 0)
            if limit == 0 or len(ingredients) <= limit:
                candidates.add(entity)
        if candidates:
            raise ValueError("alternate pack consumer present")
        if not categories and not fixed:
            raise ValueError("alternate pack consumer applicability unavailable")


def _sample(observation, facts):
    if (not observation.get("available") or observation["tick"] != facts["tick"]
            or (facts.get("surface"), facts.get("force")) != ("nauvis", "player") or not facts.get("mods")):
        raise ValueError("fresh scoped research evidence unavailable")
    tech = observation["technology"]
    packs = tech["science_packs"]
    # Do not attribute pack statistics to labs if another installed machine
    # can consume the same packs. Both the full recipe catalog and the full
    # installed-entity census must be complete and from this same sample.
    # That census only describes this endpoint: it cannot rule out a consumer
    # that operated between samples. Earned completion therefore additionally
    # requires the interval receipt checked by _provenance_proof.
    recipes, _ = _complete_recipes(facts)
    _alternate_pack_consumer(facts, recipes, packs, observation["labs"]["total"])
    produced = {p: facts["production"][p] for p in packs}
    consumed = {p: facts["consumption"][p] for p in packs}
    if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in [*produced.values(), *consumed.values()]):
        raise ValueError("pack statistics unavailable")
    return {"tick": facts["tick"], "mods": facts["mods"], "packs": packs,
            "produced": produced, "consumed": consumed, "stock": _stock(facts),
            "lab_inventory": dict(observation["labs"]["input_inventory"]),
            "level": tech.get("current_level"), "canonical": tech["canonical_name"],
            "current": observation.get("current"), "progress": observation["progress"],
            "completed": tech["target_completed"], "units": tech.get("research_unit_count"),
            "labs": observation["labs"]["total"]}


def _provenance_proof(facts, initial, sample):
    """Check a caller-provided, interval-wide conservation receipt.

    Contract for ``facts["research_provenance"]``::

        {"version": "1.0.0", "force": <force>, "surface": <surface>,
         "scope": "force-surface",
         "start_tick": <initial sample tick>, "end_tick": <completing tick>,
         "packs": {<pack>: {
             "start_stock": <network stock at start>,
             "end_stock": <network stock at end>,
             "start_lab_input": <lab input inventory at start>,
             "end_lab_input": <lab input inventory at end>,
             "produced": <force production over interval>,
             "consumed": <force consumption over interval>}, ...},
         "non_lab_consumers": [],
         "coverage": {"scope": "force-surface",
                      "from_tick": <start_tick>, "to_tick": <end_tick>,
                      "complete": true,
                      "source": <caller label for how the receipt was produced>}}

    The receipt must be scoped to the same force/surface and cover exactly the
    initial-to-completing interval. Its pack values are bound to the samples:
    stocks and lab inputs must equal the observations, and the counter deltas
    must equal the sampled production/consumption deltas. Conservation is
    checked over network stock plus lab input inventory; ``non_lab_consumers``
    must be an explicit empty list, which is the caller's interval-wide claim
    that no other item sink existed. The combined stock may not fall, so
    pre-existing packs or a pre-filled lab buffer cannot be what funded the
    completion. ``coverage`` must explicitly cover the same interval; its
    ``source`` is a caller label, not a runtime capability this module provides.

    This module does no independent interval observation and cannot verify the
    receipt's truth: the result is exactly as trustworthy as the caller that
    produced it. Missing, malformed, mismatched, non-conserving, drawdown or
    consumer-positive receipts return a typed status and never earn.
    """
    raw = facts.get("research_provenance")
    if raw is None:
        return False, "missing"
    if not isinstance(raw, dict):
        return False, "invalid"
    if raw.get("version") not in _PROVENANCE_VERSIONS:
        return False, "invalid"
    if (raw.get("force") != facts.get("force") or raw.get("surface") != facts.get("surface")
            or raw.get("scope") not in _PROVENANCE_SCOPES):
        return False, "scope_mismatch"
    start_tick, end_tick = raw.get("start_tick"), raw.get("end_tick")
    if type(start_tick) is not int or type(end_tick) is not int:
        return False, "interval_mismatch"
    if start_tick != initial["tick"] or end_tick != sample["tick"] or end_tick <= start_tick:
        return False, "interval_mismatch"
    packs = raw.get("packs")
    if not isinstance(packs, dict) or set(packs) != set(sample["packs"]):
        return False, "sample_mismatch"
    consumers = raw.get("non_lab_consumers")
    if not isinstance(consumers, list) or any(not isinstance(name, str) or not name for name in consumers):
        return False, "invalid"
    if consumers:
        return False, "transient_consumer"
    coverage = raw.get("coverage")
    if not isinstance(coverage, dict):
        return False, "coverage_incomplete"
    if (coverage.get("scope") not in _PROVENANCE_SCOPES
            or coverage.get("from_tick") != start_tick
            or coverage.get("to_tick") != end_tick
            or coverage.get("complete") is not True
            or not isinstance(coverage.get("source"), str)
            or not coverage.get("source")):
        return False, "coverage_incomplete"
    for pack in sample["packs"]:
        entry = packs[pack]
        if not isinstance(entry, dict):
            return False, "invalid"
        values = {}
        for name in ("start_stock", "end_stock", "start_lab_input", "end_lab_input",
                     "produced", "consumed"):
            value = entry.get(name)
            if (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                return False, "invalid"
            values[name] = float(value)
        bound = {
            "start_stock": initial["stock"].get(pack, 0),
            "end_stock": sample["stock"].get(pack, 0),
            "start_lab_input": initial["lab_inventory"].get(pack, 0),
            "end_lab_input": sample["lab_inventory"].get(pack, 0),
            "produced": sample["produced"][pack] - initial["produced"][pack],
            "consumed": sample["consumed"][pack] - initial["consumed"][pack],
        }
        if any(not math.isclose(values[name], float(expected), rel_tol=0.0, abs_tol=1e-9)
               for name, expected in bound.items()):
            return False, "sample_mismatch"
        start_total = values["start_stock"] + values["start_lab_input"]
        end_total = values["end_stock"] + values["end_lab_input"]
        if not math.isclose(start_total + values["produced"] - values["consumed"],
                            end_total, rel_tol=0.0, abs_tol=1e-6):
            return False, "conservation_violation"
        if end_total + 1e-9 < start_total:
            return False, "stock_drawdown"
    return True, "receipt_consistent"



def _completion_demand(initial, sample):
    """Packs the completed target level still needed after the initial sample.

    The estimate is the declared unit count times the declared pack amounts for
    the fraction of the level that was still unfinished at the initial sample.
    It assumes no lab productivity or consumption bonus and is not a measured
    ratio. ``None`` means the survey cannot support a quantitative claim, so
    earned completion must be withheld rather than guessed.
    """
    progress = initial.get("progress")
    same_level = initial.get("level") == sample.get("level")
    if same_level and initial.get("current") == initial.get("canonical"):
        # The target level was already being researched; its declared demand
        # comes from the initial sample, of which this level is still current.
        unit_count, amounts = initial.get("units"), initial.get("packs")
        if (type(progress) not in (int, float) or not math.isfinite(progress)
                or not 0 <= progress <= 1):
            return None
        fraction = 1.0 - progress
    else:
        # A different level was current initially, so the completed level began
        # after the initial sample. Use that completed level's declared demand
        # from this sample; never reuse the previous level's unit count.
        unit_count, amounts = sample.get("units"), sample.get("packs")
        fraction = 1.0
    if (type(unit_count) not in (int, float) or not math.isfinite(unit_count)
            or unit_count <= 0):
        return None
    if not isinstance(amounts, dict) or not amounts:
        return None
    demand = {}
    for pack, amount in amounts.items():
        if (type(amount) not in (int, float) or not math.isfinite(amount)
                or amount <= 0):
            return None
        demand[pack] = fraction * unit_count * amount
    return demand


class ResearchObjective:
    def __init__(self, requested="mining-productivity-4", *, progressive=True, sustain_ticks=18000, state=None):
        if type(sustain_ticks) is not int or sustain_ticks < 300:
            raise ValueError("research sustain window must be >=300 ticks")
        self.state = copy.deepcopy(state) if state is not None else {
            "version": VERSION, "requested": requested, "progressive": progressive,
            "sustain_ticks": sustain_ticks, "target": 0.25, "achieved": 0.0,
            "initial": None, "previous": None, "window": [], "rungs": [], "earned": False,
            "available": False, "research_units": 0.0}
        if (self.state.get("version") != VERSION or self.state.get("requested") != requested
                or self.state.get("progressive") != progressive or self.state.get("sustain_ticks") != sustain_ticks):
            raise ValueError("incompatible research objective state")

    def snapshot(self):
        return copy.deepcopy(self.state)

    def brief(self):
        return copy.deepcopy({k: v for k, v in self.state.items() if k not in {"window", "previous", "initial"}})

    def targets(self, observation):
        return {p: self.state["target"] * coefficient
                for p, coefficient in observation.get("technology", {}).get("science_packs", {}).items()}

    def observe(self, observation, facts, *, service_ok=False, clean=True):
        s = self.state
        s["advanced"] = False
        s["progressed"] = False
        s.pop("provenance_status", None)
        try:
            sample = _sample(observation, facts)
            if not service_ok or not clean:
                # Invalid service/clean evidence must revoke an earlier reward,
                # including a repeat at the same tick.
                s["earned"] = False
                s["reason"] = "invalid research service or clean evidence"
            initial, previous = s["initial"], s["previous"]
            if initial is None:
                s["initial"] = initial = sample
                s["ineligible_start"] = sample["completed"]
            if any(sample[k] != initial[k] for k in ("mods", "packs", "canonical")):
                raise ValueError("research provenance changed")
            if previous:
                if (sample["tick"] < previous["tick"] or any(sample[part][p] < previous[part][p]
                        for part in ("produced", "consumed") for p in sample["packs"])):
                    raise ValueError("research tick/counter regressed")
                if sample["tick"] == previous["tick"]:
                    return self.snapshot()
                before, now = previous["level"], sample["level"]
                if before is not None and now is not None and now < before:
                    raise ValueError("research level regressed")
                units = 0.0
                if previous["current"] == sample["canonical"] and previous["units"]:
                    if before == now:
                        end = 1.0 if sample["completed"] else sample["progress"] if sample["current"] == sample["canonical"] else previous["progress"]
                        units = max(0, end - previous["progress"]) * previous["units"]
                    elif before is not None and now == before + 1:
                        units = (1 - previous["progress"]) * previous["units"]
                        if sample["current"] == sample["canonical"] and sample["units"]:
                            units += sample["progress"] * sample["units"]
                s["research_units"] += units
                s["progressed"] = units > 0
            s["previous"] = sample
            s["available"] = True
            s["reason"] = "observing"
            # A positive counter delta is not proof: a nearly complete level and
            # presupplied packs can complete after one consumed pack. Counter
            # coverage is necessary but not sufficient, because it cannot show
            # which stock funded the labs; earned completion also needs the
            # interval conservation receipt below.
            demand = _completion_demand(initial, sample) if sample["completed"] else None
            supplied = {p: sample["produced"][p] - initial["produced"][p] for p in sample["packs"]}
            consumed_since = {p: sample["consumed"][p] - initial["consumed"][p] for p in sample["packs"]}
            quantified = (demand is not None
                          and all(demand[p] > 0 for p in sample["packs"])
                          and all(supplied[p] + 1e-9 >= demand[p]
                                  and consumed_since[p] + 1e-9 >= demand[p]
                                  for p in sample["packs"]))
            # Counter deltas alone are not provenance: pre-existing packs can be
            # consumed while newly produced packs are stored or exported, and a
            # consumer can act between endpoint censuses. Earn only with an
            # explicit, interval-scoped conservation receipt (see
            # _provenance_proof). The receipt is caller-provided, not verified.
            provenance_ok, provenance_status = (False, "missing")
            if sample["completed"] and demand is not None:
                provenance_ok, provenance_status = _provenance_proof(facts, initial, sample)
            # Completion still requires valid service/clean evidence: hard invalid
            # evidence at the completing sample cannot be rewarded.
            s["earned"] = bool(service_ok and clean and not s["ineligible_start"]
                               and sample["completed"] and sample["labs"] > 0
                               and sample["tick"] > initial["tick"] and quantified
                               and provenance_ok and s["research_units"] > 0)
            if s["earned"]:
                s["reason"] = "requested_level_completed_with_earned_supply"
                s["required_packs"] = demand
                s["provenance_status"] = provenance_status
                s.pop("earned_shortfall", None)
                return self.snapshot()
            if sample["completed"] and service_ok and clean and not s["ineligible_start"]:
                s["required_packs"] = demand
                s["earned_shortfall"] = (None if demand is None else
                                         {p: max(0.0, demand[p] - supplied[p]) for p in sample["packs"]})
                s["provenance_status"] = provenance_status
                if demand is None:
                    s["reason"] = "requested_level_completed_without_quantifiable_supply"
                elif not quantified:
                    s["reason"] = "requested_level_completed_without_quantified_factory_supply"
                elif provenance_status == "missing":
                    s["reason"] = "requested_level_completed_without_provenance_evidence"
                else:
                    s["reason"] = "requested_level_completed_with_invalid_provenance_evidence"
            window = s["window"]
            if not service_ok or not clean or not sample["labs"] or s["ineligible_start"]:
                window.clear()
                return self.snapshot()
            if window:
                base = window[0]["sample"]
                if (any(sample["stock"].get(p, 0) < base["stock"].get(p, 0) for p in (*SUPPORT, *sample["packs"]))
                        or any(sample["lab_inventory"].get(p, 0) < base["lab_inventory"].get(p, 0) for p in sample["packs"])):
                    window.clear()
            window.append({"sample": sample, "units": s["research_units"]})
            while len(window) > 1 and sample["tick"] - window[1]["sample"]["tick"] >= s["sustain_ticks"]:
                window.pop(0)
            base = window[0]["sample"]
            seconds = (sample["tick"] - base["tick"]) / 60
            if seconds * 60 < s["sustain_ticks"]:
                return self.snapshot()
            rates = {part: {p: (sample[part][p] - base[part][p]) / seconds for p in sample["packs"]}
                     for part in ("produced", "consumed")}
            delivered = {p: rates["consumed"][p] + (sample["lab_inventory"].get(p, 0) - base["lab_inventory"].get(p, 0)) / seconds
                         for p in sample["packs"]}
            units_rate = (s["research_units"] - window[0]["units"]) / seconds
            s["rates"] = {**rates, "delivered_to_labs": delivered, "research_units": units_rate}
            target = s["target"]
            if (units_rate + 1e-9 >= target and all(rates[k][p] + 1e-9 >= target * amount
                    for k in rates for p, amount in sample["packs"].items())
                    and all(delivered[p] + 1e-9 >= target * amount for p, amount in sample["packs"].items())):
                s["achieved"] = max(s["achieved"], target)
                if not s["rungs"] or s["rungs"][-1]["rate"] != target:
                    s["rungs"].append({"rate": target, "tick": sample["tick"]})
                if s["progressive"]:
                    s["target"] *= 2
                    s["advanced"] = True
                    window.clear()
            return self.snapshot()
        except (KeyError, TypeError, ValueError) as error:
            s.update(available=False, earned=False, reason=str(error), window=[])
            return self.snapshot()


def add_research_options(menu, board, observation, facts, objective):
    """Expose independent mechanics and measured capacity facts without choosing one."""
    if not observation.get("available"):
        return menu
    from planners.research_labs import logistic_lab_row
    from orchestrator.material_reservations import plan_material_bill
    options = list(menu["options"])
    packs = observation["technology"]["science_packs"]
    current_goal = bool(set(packs) & set(board["targets"]))
    labs = observation["labs"]["total"]
    capacity = observation.get("capacity")
    wanted = 1 if not labs else labs
    if capacity:
        wanted = max(1, math.ceil(objective.state["target"] * capacity["unit_energy"] /
                                 (60 * capacity["lab_speed"] * (1 + capacity["speed_bonus"]))))
    if labs < wanted or not observation["labs"]["working"]:
        count = min(256, max(labs + 1, min(labs + 6, wanted)))
        options.append({"option_id": f"op.research_labs.{count}", "family": "research_labs",
                        "need_id": "research:labs", "product": "lab",
                        "summary": f"fund and supply owned labs through {count} (currently {labs})",
                        "params": {"count": count, "packs": packs},
                        "bill": plan_material_bill(logistic_lab_row(min(6, count - labs), packs)),
                        "features": {"goal_need": current_goal, "lab_capacity_needed": wanted}})
    # Repair/reconfigure existing owned packets without inventing a fresh site.
    if labs:
        options.append({"option_id": "op.research_labs.reconcile", "family": "research_labs", "product": "lab",
                        "need_id": "research:labs", "summary": "reconcile funded lab power and pack requests",
                        "params": {"count": labs, "packs": packs}, "features": {"goal_need": current_goal}})
    for candidate in observation.get("selection_candidates", []):
        target = candidate["technology"]
        canonical = candidate["canonical_name"]
        if observation.get("current") == canonical or canonical in observation["queue"]:
            continue
        replaces = bool(observation.get("current") or observation["queue"])
        options.append({"option_id": f"op.research_select.{target}", "family": "research_select", "product": target,
                        "need_id": "research:selection", "summary": f"select {target}" + (" replacing observed queue" if replaces else ""),
                        "params": {"technology": target, "expected_queue": observation["queue"],
                                   "expected_current": observation.get("current"), "replace_queue": replaces},
                        "features": {"goal_need": current_goal, "replaces_queue": replaces}})
    for need in board["needs"]:
        item = need["item"]
        present = sum(need.get("machines", {}).values())
        if not present or need.get("condition") != "under_capacity" or not need.get("machines_needed"):
            continue
        count = min(256, max(present + 1, min(need["machines_needed"].values())))
        if count <= present:
            continue
        options.append({"option_id": f"op.scale_production.{item}.{count}", "family": "scale_production", "product": item,
                        "need_id": need["need_id"], "summary": f"scale {item} capacity through {count} machines",
                        "params": {"item": item, "machines": count,
                                   "stock_target": max(1, math.ceil(need["required_per_s"] * 300))},
                        "features": {"goal_need": item in board["targets"], "in_bottleneck": True,
                                     "required_per_s": need["required_per_s"], "capacity_per_s": need["capacity_per_s"]}})
    return {**menu, "options": options}
