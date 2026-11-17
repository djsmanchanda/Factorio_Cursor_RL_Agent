# Path: orchestrator/checkpoint_milestones.py
# Purpose: Evaluate versioned, structured factory checkpoint predicates.
"""Structured checkpoint predicates for deterministic run comparisons.

The module deliberately knows nothing about RCON, runner logs, or Factorio's
Lua objects.  An adapter turns live telemetry into :class:`FactorySnapshot`;
predicates then return a three-valued result (passed, failed, or unknown) and
the evidence that led to it.  This keeps missing telemetry distinct from a
measured negative result and makes predicates replayable in tests.
"""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


PREDICATE_SCHEMA_VERSION = 1
PLASTIC_ITEM = "plastic-bar"
ADVANCED_CIRCUIT_ITEM = "advanced-circuit"
ITEM_PRODUCTION_BASIS = "force-surface-item-input-v1"
PRODUCTION_PROVENANCE_FIELDS = frozenset({"counter_basis", "counter_scope", "counter_generation", "sample_tick"})
IRON_GEAR_RECIPE = "iron-gear-wheel"
COPPER_CABLE_RECIPE = "copper-cable"
CHEMICAL_SCIENCE_ITEM = "chemical-science-pack"
PRODUCTION_SCIENCE_ITEM = "production-science-pack"
# Space Age removes mining-productivity-4 and makes mining-productivity-3 repeatable with red,
# green, blue and purple packs; "mining-productivity-4" is its fourth completed level.
MINING_PRODUCTIVITY_LEVEL = 4
MINING_PRODUCTIVITY_PACKS = ("automation-science-pack", "logistic-science-pack",
                             CHEMICAL_SCIENCE_ITEM, PRODUCTION_SCIENCE_ITEM)


class PredicateStatus(str, Enum):
    """The result of a structured predicate."""

    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


def _as_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _item_counter(value: Any, *names: str) -> int | None:
    """Read a counter from a scalar or a telemetry mapping."""
    if isinstance(value, Mapping):
        for name in names:
            if name in value:
                return _as_int(value[name])
        return None
    return _as_int(value)


@dataclass(frozen=True)
class MachineEvidence:
    """One mall or production machine after normalization."""

    recipe: str | None = None
    healthy: bool | None = None
    producing: bool | None = None
    working: bool | None = None
    craft_count: int | None = None
    output_count: int | None = None
    name: str | None = None
    status: str | None = None  # raw entity status, e.g. "working" or "output_full"; measurement only

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> "MachineEvidence":
        if not isinstance(value, Mapping):
            return cls(working=_as_bool(value))
        working = _as_bool(value.get("working"))
        healthy = _as_bool(value.get("healthy", value.get("valid")))
        producing = _as_bool(value.get("producing", value.get("active")))
        if working is None and healthy is not None and producing is not None:
            working = healthy and producing
        return cls(
            recipe=value.get("recipe", value.get("recipe_name")),
            healthy=healthy,
            producing=producing,
            working=working,
            craft_count=_item_counter(value, "craft_count", "crafts", "products_finished"),
            output_count=_item_counter(value, "output_count", "output", "produced"),
            name=value.get("name"),
            status=None if value.get("status") is None else str(value.get("status")),
        )


@dataclass(frozen=True)
class FoundationEvidence:
    """Health and output evidence for a persistent resource foundation."""

    name: str
    machine_count: int | None = None
    healthy: bool | None = None
    producing: bool | None = None
    output_count: int | None = None
    working_count: int | None = None
    identity_source: str | None = None
    counter_basis: str | None = None
    district_id: str | None = None
    district_revision: int | None = None
    machines: tuple[Mapping[str, Any], ...] = ()
    # Why the census could not measure this foundation (counts are then unknown).
    census_error: str | None = None

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any] | Any) -> "FoundationEvidence":
        if not isinstance(value, Mapping):
            return cls(name=name, machine_count=_as_int(value))
        # Legacy snapshots use a scalar `machines` as the count alias.
        machines = value.get("machines")
        rows = machines if isinstance(machines, (list, tuple)) else ()
        return cls(
            name=str(value.get("name", name)),
            machine_count=_as_int(value.get("machine_count", value.get("machines"))),
            healthy=_as_bool(value.get("healthy", value.get("valid"))),
            producing=_as_bool(value.get("producing", value.get("active"))),
            output_count=_item_counter(value, "output_count", "output", "produced"),
            working_count=_as_int(value.get("working_count")),
            identity_source=value.get("identity_source"),
            counter_basis=value.get("counter_basis"),
            district_id=value.get("district_id"),
            district_revision=_as_int(value.get("district_revision")),
            machines=tuple(dict(machine) for machine in rows if isinstance(machine, Mapping)),
            census_error=value.get("census_error"),
        )


@dataclass(frozen=True)
class StarterEvidence:
    """Presence and health of one temporary plate/stone starter."""

    resource: str
    present: bool | None = None
    healthy: bool | None = None
    producing: bool | None = None
    output_count: int | None = None

    @classmethod
    def from_mapping(cls, resource: str, value: Mapping[str, Any] | Any) -> "StarterEvidence":
        if not isinstance(value, Mapping):
            return cls(resource=resource, present=_as_bool(value))
        return cls(
            resource=str(value.get("resource", resource)),
            present=_as_bool(value.get("present", value.get("exists"))),
            healthy=_as_bool(value.get("healthy", value.get("valid"))),
            producing=_as_bool(value.get("producing", value.get("active"))),
            output_count=_item_counter(value, "output_count", "output", "produced"),
        )


@dataclass(frozen=True)
class CounterEvidence:
    """A cumulative production/provider counter."""

    produced: int | None = None
    delivered: int | None = None
    craft_count: int | None = None
    available: int | None = None
    counter_basis: str | None = None
    counter_scope: str | None = None
    counter_generation: int | None = None
    sample_tick: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> "CounterEvidence":
        if not isinstance(value, Mapping):
            return cls(produced=_as_int(value), delivered=_as_int(value))
        scoped = any(value.get(name) is not None for name in PRODUCTION_PROVENANCE_FIELDS)
        produced = (value.get("produced") if type(value.get("produced")) is int else None) if scoped else (
            _item_counter(value, "produced", "output_count", "output", "production"))
        return cls(
            produced=produced,
            delivered=_item_counter(value, "delivered", "delivered_count", "provider_count"),
            craft_count=_item_counter(value, "craft_count", "crafts", "products_finished"),
            available=_item_counter(value, "available", "count", "inventory"),
            counter_basis=value.get("counter_basis"),
            counter_scope=value.get("counter_scope"),
            counter_generation=value.get("counter_generation"),
            sample_tick=value.get("sample_tick"),
        )

    def progression(self) -> int | None:
        for value in (self.craft_count, self.produced, self.delivered):
            if value is not None:
                return value
        return None

    def delivery_evidence(self) -> int | None:
        for value in (self.delivered, self.available, self.produced):
            if value is not None:
                return value
        return None


@dataclass(frozen=True)
class RecipeEvidence:
    """Live recipe catalog entry and its cumulative output counter."""

    name: str
    owned: bool | None = None
    ingredients: tuple[str, ...] = ()
    output_count: int | None = None
    craft_count: int | None = None
    produced: int | None = None

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any] | Any) -> "RecipeEvidence":
        if not isinstance(value, Mapping):
            return cls(name=name, owned=_as_bool(value))
        raw_ingredients = value.get("ingredients", value.get("ingredient_names", ()))
        names: list[str] = []
        if isinstance(raw_ingredients, Mapping):
            names.extend(str(item) for item in raw_ingredients)
        elif isinstance(raw_ingredients, Sequence) and not isinstance(raw_ingredients, (str, bytes)):
            for item in raw_ingredients:
                if isinstance(item, Mapping):
                    ingredient = item.get("name", item.get("item"))
                    if ingredient is not None:
                        names.append(str(ingredient))
                else:
                    names.append(str(item))
        return cls(
            name=str(value.get("name", name)),
            owned=_as_bool(value.get("owned", value.get("available"))),
            ingredients=tuple(names),
            output_count=_item_counter(value, "output_count", "output", "produced"),
            craft_count=_item_counter(value, "craft_count", "crafts", "products_finished"),
            produced=_item_counter(value, "produced", "output_count", "output"),
        )

    def progression(self) -> int | None:
        for value in (self.craft_count, self.output_count, self.produced):
            if value is not None:
                return value
        return None


@dataclass(frozen=True)
class FactorySnapshot:
    """Normalized, serializable observation used by checkpoint predicates.

    ``from_mapping`` accepts the deliberately boring shape emitted by a future
    RCON adapter.  The canonical dataclass makes predicate inputs independent
    of Lua table details and allows fixtures to be JSON-like dictionaries.
    """

    tick: int | None = None
    base_valid: bool | None = None
    mall_assemblers: tuple[MachineEvidence, ...] = ()
    foundations: Mapping[str, FoundationEvidence] = field(default_factory=dict)
    starters: Mapping[str, StarterEvidence] = field(default_factory=dict)
    production: Mapping[str, CounterEvidence] = field(default_factory=dict)
    providers: Mapping[str, CounterEvidence] = field(default_factory=dict)
    recipes: Mapping[str, RecipeEvidence] = field(default_factory=dict)
    # Force-wide science telemetry for C7-C9: per-pack produced/consumed counters, lab and
    # recycler counts and the completed mining-productivity level, or {"unavailable_reason": ...}.
    science: Mapping[str, Any] | None = None
    # Per-machine advanced-circuit consumers for c6-v3: [{"unit", "recipe", "count"}] from
    # products_finished, or {"unavailable_reason": ...}. A machine's counter is lifetime, so only
    # increases between consecutive samples on the same recipe count as consumption.
    ac_consumers: Any = None
    # Plastic telemetry for c5-v2 and c6a-v1, or None when not observed (older snapshots):
    # {"produced": force plastic-bar production (bars), "declared_target": {"rate", "tick", "source"} or None,
    #  "capacity": {"machines", "crafting_speed", "products_per_craft", "energy", "rate"},
    #  "consumers": [{"unit", "recipe", "count"}] or None}. A runner declares the target in the game;
    # the predicate never invents one.
    plastic: Any = None
    # Not an observation: C5's fold state over the history trimmed before this sample, set only on the
    # oldest sample of a bounded history (``checkpoint_evidence.bounded_history``; see ``c5_ledger``).
    c5_ledger: Any = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "FactorySnapshot":
        foundations_raw = value.get("foundations", {})
        starters_raw = value.get("starters", {})
        production_raw = value.get("production", {})
        providers_raw = value.get("providers", {})
        recipes_raw = value.get("recipes", value.get("recipe_catalog", {}))
        foundations = {
            str(name): FoundationEvidence.from_mapping(str(name), evidence)
            for name, evidence in (foundations_raw.items() if isinstance(foundations_raw, Mapping) else ())
        }
        starters = {
            str(name): StarterEvidence.from_mapping(str(name), evidence)
            for name, evidence in (starters_raw.items() if isinstance(starters_raw, Mapping) else ())
        }
        production = {
            str(name): CounterEvidence.from_mapping(evidence)
            for name, evidence in (production_raw.items() if isinstance(production_raw, Mapping) else ())
        }
        providers = {
            str(name): CounterEvidence.from_mapping(evidence)
            for name, evidence in (providers_raw.items() if isinstance(providers_raw, Mapping) else ())
        }
        recipes = {
            str(name): RecipeEvidence.from_mapping(str(name), evidence)
            for name, evidence in (recipes_raw.items() if isinstance(recipes_raw, Mapping) else ())
        }
        mall_raw = value.get("mall_assemblers", value.get("mall", ()))
        mall = tuple(MachineEvidence.from_mapping(item) for item in mall_raw) if isinstance(mall_raw, Sequence) and not isinstance(mall_raw, (str, bytes)) else ()
        return cls(
            tick=_as_int(value.get("tick")),
            base_valid=_as_bool(value.get("base_valid", value.get("valid_base"))),
            mall_assemblers=mall,
            foundations=foundations,
            starters=starters,
            production=production,
            providers=providers,
            recipes=recipes,
            science=value.get("science") if isinstance(value.get("science"), Mapping) else None,
            ac_consumers=value.get("ac_consumers"),
            plastic=value.get("plastic") if isinstance(value.get("plastic"), Mapping) else None,
            c5_ledger=value.get("c5_ledger") if isinstance(value.get("c5_ledger"), Mapping) else None,
        )


@dataclass(frozen=True)
class CheckpointResult:
    checkpoint_id: str
    predicate_version: str
    status: PredicateStatus
    evidence: Mapping[str, Any]
    missing: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return self.status is PredicateStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.status is PredicateStatus.FAILED

    @property
    def unknown(self) -> bool:
        return self.status is PredicateStatus.UNKNOWN

    def as_mapping(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "predicate_version": self.predicate_version,
            "status": self.status.value,
            "evidence": dict(self.evidence),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class MilestoneDefinition:
    checkpoint_id: str
    name: str
    predicate_version: str
    evaluate: Callable[[FactorySnapshot, FactorySnapshot | None, Sequence[FactorySnapshot]], CheckpointResult]


def _result(checkpoint: str, version: str, status: PredicateStatus, evidence: Mapping[str, Any], missing: Iterable[str] = ()) -> CheckpointResult:
    return CheckpointResult(checkpoint, version, status, dict(evidence), tuple(missing))


def _all_checks(checkpoint: str, version: str, checks: Mapping[str, bool | None], evidence: Mapping[str, Any]) -> CheckpointResult:
    missing = tuple(name for name, value in checks.items() if value is None)
    status = PredicateStatus.FAILED if any(value is False for value in checks.values()) else (
        PredicateStatus.UNKNOWN if missing else PredicateStatus.PASSED
    )
    return _result(checkpoint, version, status, {"checks": dict(checks), **dict(evidence)}, missing)


def _counter_delta(current: CounterEvidence | None, baseline: CounterEvidence | None) -> int | None:
    if current is None or baseline is None:
        return None
    now, then = current.progression(), baseline.progression()
    return None if now is None or then is None else now - then


def _delivery_delta(current: CounterEvidence | None, baseline: CounterEvidence | None) -> int | None:
    if current is None or baseline is None:
        return None
    now, then = current.delivery_evidence(), baseline.delivery_evidence()
    return None if now is None or then is None else now - then


def _c0(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    return _all_checks("C0", "c0-v1", {"base_valid": snapshot.base_valid}, {"base_valid": snapshot.base_valid})


def _starter_ready(snapshot: FactorySnapshot, name: str) -> bool | None:
    starter = snapshot.starters.get(name)
    if starter is None:
        return None
    if starter.present is False or starter.healthy is False or starter.producing is False:
        return False
    if starter.present is None or starter.healthy is None or starter.producing is None:
        return None
    return True


def starter_blocker(snapshot: FactorySnapshot, name: str) -> str:
    """First failing starter fact, for evidence only; readiness is still decided by _starter_ready."""
    starter = snapshot.starters.get(name)
    if starter is None:
        return "unobserved"
    for fact, label in (("present", "absent"), ("healthy", "unhealthy"), ("producing", "not_producing")):
        if getattr(starter, fact) is False:
            return label
    return "ready" if _starter_ready(snapshot, name) else "unknown"


def _c1(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    observed = snapshot.mall_assemblers
    working = [machine for machine in observed if machine.working is True]
    recipes = (IRON_GEAR_RECIPE, COPPER_CABLE_RECIPE)
    has_recipe = {recipe: any(machine.recipe == recipe for machine in working) for recipe in recipes}
    count_ok: bool | None = len(working) >= 4 if observed else None
    # Producer checks read every observed machine: with no working machine a present-but-idle producer is a
    # measured False, and only an empty survey is unknown. Status cannot change: an observed survey with no
    # working machine already fails the count check.
    checks = {
        "iron_starter_ready": _starter_ready(snapshot, "iron"),
        "copper_starter_ready": _starter_ready(snapshot, "copper"),
        "stone_starter_ready": _starter_ready(snapshot, "stone"),
        "at_least_four_working_mall_assemblers": count_ok,
        "iron_gear_producer_working": has_recipe[IRON_GEAR_RECIPE] if observed else None,
        "copper_cable_producer_working": has_recipe[COPPER_CABLE_RECIPE] if observed else None,
    }
    status_counts: dict[str, int] = {}
    for machine in observed:
        key = machine.status or "unreported"
        status_counts[key] = status_counts.get(key, 0) + 1
    evidence = {
        "working_mall_count": len(working),
        "observed_mall_count": len(observed),
        "recipes": has_recipe,
        "recipes_present": {recipe: any(machine.recipe == recipe for machine in observed) for recipe in recipes},
        "mall_status_counts": dict(sorted(status_counts.items())),
        "starter_blockers": {name: starter_blocker(snapshot, name) for name in ("iron", "copper", "stone")},
    }
    return _all_checks("C1", "c1-v1", checks, evidence)


def _foundation_ready(snapshot: FactorySnapshot, name: str) -> bool | None:
    foundation = snapshot.foundations.get(name)
    if foundation is None:
        return None
    values = (
        foundation.machine_count, foundation.healthy,
        foundation.producing, foundation.output_count,
    )
    if any(value is False for value in values) or (foundation.machine_count is not None and foundation.machine_count < 6):
        return False
    if foundation.output_count is not None and foundation.output_count <= 0:
        return False
    if any(value is None for value in values):
        return None
    return True


def _starter_absent(snapshot: FactorySnapshot, name: str) -> bool | None:
    starter = snapshot.starters.get(name)
    return None if starter is None else starter.present is False


def _c2(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    checks = {
        "iron_foundation_ready": _foundation_ready(snapshot, "iron"),
        "copper_foundation_ready": _foundation_ready(snapshot, "copper"),
        "iron_starter_absent": _starter_absent(snapshot, "iron"),
        "copper_starter_absent": _starter_absent(snapshot, "copper"),
    }
    return _all_checks("C2", "c2-v1", checks, _foundation_evidence(snapshot, "iron", "copper"))


def _c3(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    checks = {"stone_foundation_ready": _foundation_ready(snapshot, "stone"), "stone_starter_absent": _starter_absent(snapshot, "stone")}
    return _all_checks("C3", "c3-v1", checks, _foundation_evidence(snapshot, "stone"))


def _foundation_evidence(snapshot: FactorySnapshot, *names: str) -> dict[str, Any]:
    """Keep the census that produced a receipt, including on a failed check."""
    return {"tick": snapshot.tick, "foundations": {
        name: asdict(snapshot.foundations[name]) if name in snapshot.foundations else None
        for name in names
    }}


def _item_counter_provenance(counter: CounterEvidence) -> bool:
    return (counter.counter_basis == ITEM_PRODUCTION_BASIS
            and isinstance(counter.counter_scope, str) and bool(counter.counter_scope)
            and type(counter.counter_generation) is int and counter.counter_generation >= 0
            and type(counter.sample_tick) is int and counter.sample_tick >= 0)


def _scoped_item_counter(counter: CounterEvidence) -> bool:
    return (_item_counter_provenance(counter)
            and type(counter.produced) is int and counter.produced >= 0
            and counter.craft_count is None)


def track_item_production(snapshot: FactorySnapshot, previous: FactorySnapshot | None) -> FactorySnapshot:
    """Carry sampled counter generations in snapshots, including across restart/history eviction.

    This detects observed regressions, scope changes and clock rollback. A statistics reset that
    happens and overtakes the previous count between samples remains unobservable; no continuity
    claim is made. Unavailable intervals carry provenance without a count. Legacy-only counters
    stay untouched; a legacy sample after scoped telemetry is an unavailable interval, not a rebase.
    """
    production = dict(snapshot.production)
    previous_production = {} if previous is None else previous.production
    for item in production.keys() | previous_production.keys():
        current, prior = production.get(item), previous_production.get(item)
        if prior is None or not _item_counter_provenance(prior):
            continue
        if current is None or not _scoped_item_counter(current):
            # Retain the boundary, never the last count. One unavailable interval starts a new
            # generation; repeating that interval cannot forget it, even after history eviction.
            production[item] = CounterEvidence(
                counter_basis=prior.counter_basis, counter_scope=prior.counter_scope,
                counter_generation=prior.counter_generation + int(prior.produced is not None),
                sample_tick=prior.sample_tick)
            continue
        boundary = (current.counter_scope != prior.counter_scope
                    or current.sample_tick < prior.sample_tick
                    or (prior.produced is not None and (current.produced < prior.produced
                        or (current.sample_tick == prior.sample_tick and current.produced != prior.produced))))
        production[item] = replace(current, counter_generation=max(
            current.counter_generation, prior.counter_generation + int(boundary)))
    return replace(snapshot, production=production)


def _new_output(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, item: str) -> tuple[bool | None, dict[str, Any]]:
    if baseline is None:
        return None, {"baseline": "missing"}
    current = snapshot.production.get(item)
    before = baseline.production.get(item)
    # Old fixtures/reports retain their explicit legacy interpretation. Never difference a new
    # force/surface item total against an old sum of recipe-labelled machine lifetime counters.
    scoped = any(getattr(counter, name) is not None
                 for counter in (current, before) if counter is not None
                 for name in PRODUCTION_PROVENANCE_FIELDS)
    provenance = {}
    if scoped:
        reason = None
        if current is None or before is None or not all(_scoped_item_counter(c) for c in (current, before)):
            reason = "missing_or_legacy_counter_provenance"
        elif current.counter_scope != before.counter_scope:
            reason = "counter_scope_changed"
        elif current.counter_generation != before.counter_generation:
            reason = "counter_generation_changed"
        elif current.sample_tick < before.sample_tick or current.produced < before.produced:
            reason = "counter_regressed"
        elif current.sample_tick == before.sample_tick and current.produced != before.produced:
            reason = "same_tick_counter_changed"
        provenance = {"production_counter": {
            "basis": None if current is None else current.counter_basis,
            "scope": None if current is None else current.counter_scope,
            "generation": None if current is None else current.counter_generation,
            "baseline_tick": None if before is None else before.sample_tick,
            "sample_tick": None if current is None else current.sample_tick,
            "unavailable_reason": reason,
        }}
        if reason is not None:
            return None, {"production_delta": None, **provenance}
    delta = _counter_delta(current, before)
    return (None if delta is None else delta > 0), {"production_delta": delta, **provenance}


def _new_delivery(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, item: str) -> tuple[bool | None, dict[str, Any]]:
    if baseline is None:
        return None, {"baseline": "missing"}
    current = snapshot.providers.get(item)
    before = baseline.providers.get(item)
    delta = _delivery_delta(current, before)
    return (None if delta is None else delta > 0), {"delivery_delta": delta}


def _c4(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    produced, production_evidence = _new_output(snapshot, baseline, PLASTIC_ITEM)
    delivered, delivery_evidence = _new_delivery(snapshot, baseline, PLASTIC_ITEM)
    return _all_checks("C4", "c4-v1", {"plastic_newly_produced": produced, "plastic_newly_delivered": delivered}, {**production_evidence, **delivery_evidence})


def _plastic_sample(snapshot: FactorySnapshot) -> tuple[int | None, int | None]:
    production = snapshot.production.get(PLASTIC_ITEM)
    provider = snapshot.providers.get(PLASTIC_ITEM)
    return (None if production is None else production.progression(), None if provider is None else provider.delivery_evidence())


def _c5(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, history: Sequence[FactorySnapshot]) -> CheckpointResult:
    all_samples = tuple(history) + ((snapshot,) if not history or history[-1] is not snapshot else ())
    all_values = [_plastic_sample(sample) for sample in all_samples]
    # A C0-origin lane naturally contains many observations before plastic
    # exists.  Those samples must not poison the later 120-second stability
    # window.  Start the cohort at the first measured positive production and
    # provider sample, then require every subsequent observation to remain
    # structured and monotonic.
    start = next((
        index for index, (sample, (craft, delivered)) in enumerate(zip(all_samples, all_values))
        if sample.tick is not None
        and craft is not None and craft > 0
        and delivered is not None and delivered > 0
    ), None)
    samples = () if start is None else all_samples[start:]
    values = () if start is None else tuple(all_values[start:])
    ticks = [sample.tick for sample in samples]
    complete = bool(values) and all(
        tick is not None and craft is not None and delivered is not None
        for tick, (craft, delivered) in zip(ticks, values)
    )
    duration = None if not complete else int(ticks[-1]) - int(ticks[0])
    advancing = (
        all(
            values[index][0] > values[index - 1][0]
            and values[index][1] >= values[index - 1][1]
            for index in range(1, len(values))
        )
        if complete and len(values) > 1 else None
    )
    checks = {
        "window_at_least_120_seconds": None if duration is None else duration >= 120 * 60,
        "plastic_craft_counter_advances": advancing,
        "plastic_provider_evidence_present": None if not complete else all(delivered > 0 for _, delivered in values),
    }
    return _all_checks("C5", "c5-v1", checks, {
        "sample_count": len(samples),
        "ignored_preproduction_samples": 0 if start is None else start,
        "duration_ticks": duration,
        "samples": [
            {"tick": sample.tick, "craft": craft, "delivered": delivered}
            for sample, (craft, delivered) in zip(samples, values)
        ],
    })


#: C5 (c5-v4): skip this long after plastic production first starts, then measure over at least this long;
#: pass at this share of the declared planned rate (owner decision, 16 November 2026).
#: c5-v4 (17 November) is c5-v3 as a fold whose bounded history is exact; it differs from c5-v3 only when the
#: game clock goes backwards, where c5-v3 measured windows across the jump and c5-v4 starts none across it.
#: c5-v3 corrected two c5-v2 defects seen live on 17 November: production "started" at the first sample with a
#: positive lifetime counter (a save's history made that the first sample, with no plants), and every window
#: ending before the runner declared its target left the status unknown, so C5 never read failed and a
#: failed-to-passed transition was never credited.
C5_SETTLE_TICKS = 30 * 60
C5_WINDOW_TICKS = 30 * 60
C5_TARGET_SHARE = 0.75


def _plastic_fact(sample: FactorySnapshot, key: str) -> Any:
    return sample.plastic.get(key) if isinstance(sample.plastic, Mapping) else None


def _plastic_produced(sample: FactorySnapshot) -> int | None:
    return _as_int(_plastic_fact(sample, "produced"))


def _declared_target(sample: FactorySnapshot) -> dict[str, Any] | None:
    """The runner's declared planned plastic rate (bars/s) recorded in the game, or None."""
    target = _plastic_fact(sample, "declared_target")
    if not isinstance(target, Mapping):
        return None
    rate = target.get("rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not rate > 0:
        return None
    return {"rate": float(rate), "tick": _as_int(target.get("tick")), "source": target.get("source")}


def _ratio(value: float | None, reference: Any) -> float | None:
    if value is None or isinstance(reference, bool) or not isinstance(reference, (int, float)) or reference <= 0:
        return None
    return round(value / reference, 4)


# C5 is a left fold over the observation sequence. Folding the trimmed prefix of a bounded history into a
# small ledger, and continuing the fold over the retained samples, gives exactly the full-history result.
# The state is JSON (lists, not tuples) so a restart bundle carries it unchanged.
_C5_EMPTY_LEDGER: dict[str, Any] = {
    "last": None,            # [tick, produced] of the newest usable sample: the first rise is production start
    "start": None,           # tick of the first rise
    "first_target": None,    # first target declared by a usable sample; judges windows without their own
    "latest_target": None,   # newest declared target, from any sample
    "begins": [],            # [tick, produced] settled samples that can still begin a window, oldest first
    "windows": 0,            # windows formed so far
    "last_window": None,     # newest window
    "passing": None,         # first passing window
    "pending": [],           # windows formed before any declaration, each faster than all before it
}


def _c5_rate_passes(rate: float, target: Mapping[str, Any]) -> bool:
    return rate >= C5_TARGET_SHARE * target["rate"]


def _c5_fold(state: dict[str, Any], sample: FactorySnapshot) -> None:
    """Advance C5's state by one observation.

    A window ends at each settled sample and starts at the newest earlier settled sample at least
    C5_WINDOW_TICKS before it; a later endpoint can only start at that sample or a newer one, so older
    begins are dropped. No window spans a clock regression (a reloaded save): its begins are dropped too.
    Before the first declaration, only windows faster than every earlier one are kept:
    the first window to pass the first declared target is always one of them.
    """
    target = _declared_target(sample)
    if target is not None:
        state["latest_target"] = target
    tick, produced = sample.tick, _plastic_produced(sample)
    if tick is None or produced is None:
        return
    if state["first_target"] is None and target is not None:
        state["first_target"] = target
        if state["passing"] is None:
            state["passing"] = next((w for w in state["pending"] if _c5_rate_passes(w["rate"], target)), None)
        state["pending"] = []
    last, state["last"] = state["last"], [tick, produced]
    if last is not None and tick < last[0]:
        state["begins"] = []
    if state["start"] is None:
        if last is not None and produced > last[1]:
            state["start"] = tick
        return
    if tick < state["start"] + C5_SETTLE_TICKS:
        return
    begins = state["begins"]
    index = next((i for i in range(len(begins) - 1, -1, -1) if begins[i][0] <= tick - C5_WINDOW_TICKS), None)
    if index is not None:
        begin = begins[index]
        capacity = _plastic_fact(sample, "capacity")
        window = {"from_tick": begin[0], "tick": tick, "produced_delta": produced - begin[1],
                  "rate": (produced - begin[1]) * 60 / (tick - begin[0]), "target": target,
                  "capacity": dict(capacity) if isinstance(capacity, Mapping) else None}
        state["windows"] += 1
        state["last_window"] = window
        judge = target or state["first_target"]
        if judge is None:
            if not state["pending"] or window["rate"] > state["pending"][-1]["rate"]:
                state["pending"].append(window)
        elif state["passing"] is None and _c5_rate_passes(window["rate"], judge):
            state["passing"] = window
        del begins[:index]
    # Of samples sharing a tick, only the newest can begin a later window.
    if begins and begins[-1][0] == tick:
        begins[-1] = [tick, produced]
    else:
        begins.append([tick, produced])


def c5_ledger(samples: Sequence[FactorySnapshot]) -> dict[str, Any]:
    """C5's state after ``samples``, continuing the ledger carried by the first one (a copy, never shared)."""
    first = samples[0].c5_ledger if samples else None
    state = copy.deepcopy(dict(first) if isinstance(first, Mapping) else _C5_EMPTY_LEDGER)
    for sample in samples:
        _c5_fold(state, sample)
    return state


def c5_ledger_or_none(state: Mapping[str, Any]) -> dict[str, Any] | None:
    """``state`` to carry on a trimmed history, or None while C5 has seen no plastic counter."""
    return None if state == _C5_EMPTY_LEDGER else dict(state)


def validate_c5_ledger(value: Any) -> dict[str, Any]:
    """A ledger read from a restart bundle: the expected keys and value types, or ValueError."""
    def pair(item):
        return isinstance(item, list) and len(item) == 2 and all(type(v) is int for v in item)

    def target(item):
        return item is None or (isinstance(item, dict) and set(item) == {"rate", "tick", "source"}
                                and _declared_target(FactorySnapshot(plastic={"declared_target": item})) == item)

    def window(item):
        return (isinstance(item, dict) and set(item) == {"from_tick", "tick", "produced_delta", "rate", "target", "capacity"}
                and all(type(item[k]) is int for k in ("from_tick", "tick", "produced_delta"))
                and type(item["rate"]) in (int, float) and target(item["target"])
                and (item["capacity"] is None or isinstance(item["capacity"], dict)))

    if not (isinstance(value, dict) and set(value) == set(_C5_EMPTY_LEDGER)
            and (value["last"] is None or pair(value["last"]))
            and (value["start"] is None or type(value["start"]) is int)
            and target(value["first_target"]) and target(value["latest_target"])
            and isinstance(value["begins"], list) and all(pair(item) for item in value["begins"])
            and type(value["windows"]) is int and value["windows"] >= 0
            and (value["last_window"] is None or window(value["last_window"]))
            and (value["passing"] is None or window(value["passing"]))
            and isinstance(value["pending"], list) and all(window(item) for item in value["pending"])):
        raise ValueError("Invalid C5 ledger")
    return value


def _c5_window_evidence(window: Mapping[str, Any], first_target: Mapping[str, Any] | None) -> dict[str, Any]:
    """A window as C5 evidence; one that ended before any declaration is judged against the first one."""
    target = window["target"] or first_target
    capacity = window["capacity"]
    rate = window["rate"]
    return {
        "from_tick": window["from_tick"], "tick": window["tick"], "window_ticks": window["tick"] - window["from_tick"],
        "produced_delta": window["produced_delta"], "measured_rate": round(rate, 4),
        "planned_target": target, "target_declared_after_window": window["target"] is None and first_target is not None,
        "installed_capacity": capacity,
        "ratio_to_target": None if target is None else _ratio(rate, target["rate"]),
        "ratio_to_capacity": _ratio(rate, capacity.get("rate") if isinstance(capacity, Mapping) else None),
        "passes": None if target is None else _c5_rate_passes(rate, target),
    }


def _c5_planned_rate(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, history: Sequence[FactorySnapshot]) -> CheckpointResult:
    """Plastic made at >= 75% of the runner's declared planned rate over a settled window of >= 30 s.

    Historical: the first passing window is the result for the rest of the run, so a full output chest
    (production falls to zero) does not retract it. Without a declared target the result is unknown.
    The history's first sample may carry the ledger of samples trimmed before it; the result is then the
    one the untrimmed history gives.
    """
    samples = tuple(history) + ((snapshot,) if not history or history[-1] is not snapshot else ())
    state = c5_ledger(samples)
    start, passing, windows = state["start"], state["passing"], state["windows"]
    shown = passing or state["last_window"]
    declared = state["latest_target"]
    evidence = {
        "first_production_tick": start,
        "settle_ticks": C5_SETTLE_TICKS, "min_window_ticks": C5_WINDOW_TICKS, "target_share": C5_TARGET_SHARE,
        "window_count": windows, "sample_count": len(samples),
        "latest_declared_target": declared,
        "window": None if shown is None else _c5_window_evidence(shown, state["first_target"]),
        "historical": passing is not None,
    }
    produced_now = _plastic_produced(snapshot)
    # A window without a declared target cannot be judged; it might have passed.
    unjudged = declared is None or (windows > 0 and state["first_target"] is None)
    if passing is not None:
        status, missing = PredicateStatus.PASSED, ()
    elif unjudged:
        status, missing = PredicateStatus.UNKNOWN, ("planned_target",)
    elif produced_now is None:
        status, missing = PredicateStatus.UNKNOWN, ("plastic_production_counter",)
    else:
        status, missing = PredicateStatus.FAILED, ()
    checks = {
        "planned_target_declared": None if declared is None else True,
        "plastic_production_started": None if produced_now is None and start is None else start is not None,
        "settled_window_at_least_30_seconds": windows > 0,
        "rate_at_least_75_percent_of_target": (True if passing is not None else
                                                None if unjudged or not windows else False),
    }
    return _result("C5", "c5-v4", status, {"checks": checks, **evidence}, missing)


def _plastic_consumers(samples: Sequence[FactorySnapshot]) -> dict[str, int] | None:
    """Plastic crafts per recipe by non-chest machines, counted as c6-v3 counts advanced-circuit consumers."""
    rows = [_plastic_fact(s, "consumers") for s in samples]
    rows = [r for r in rows if isinstance(r, (list, tuple))]
    if not rows:
        return None
    return _machine_consumption([FactorySnapshot(ac_consumers=r) for r in rows])


def _c6a(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, history: Sequence[FactorySnapshot]) -> CheckpointResult:
    """Plastic used: a non-chest machine whose recipe takes plastic-bar crafted after the baseline."""
    samples = list(history) if history and history[-1] is snapshot else [*history, snapshot]
    observed = isinstance(_plastic_fact(snapshot, "consumers"), (list, tuple))
    consumed = None if baseline is None or not observed else _plastic_consumers(samples)
    window = sum(isinstance(_plastic_fact(s, "consumers"), (list, tuple)) for s in samples)
    return _all_checks("C6a", "c6a-v1", {
        "plastic_consumer_crafted": None if consumed is None else any(v > 0 for v in consumed.values()),
    }, {"machine_consumption": consumed, "window_samples": window})


#: A logistics chest only moves stock; it is not a production consumer. c6-v1 accepted any owned
#: recipe with advanced circuits among its ingredients, so 83/84 C55 and 56/56 C51 passes were chests.
C6_LOGISTICS_CHESTS = frozenset({
    "wooden-chest", "iron-chest", "steel-chest",
    "passive-provider-chest", "active-provider-chest", "storage-chest",
    "buffer-chest", "requester-chest",
})


def _consumer_leg(snapshot: FactorySnapshot, baseline: FactorySnapshot | None,
                  *, exclude: frozenset[str] = frozenset()) -> tuple[bool | None, dict[str, int | None]]:
    """Whether a non-excluded owned recipe consumed advanced circuits, and each recipe's delta."""
    consumer_checks: dict[str, bool | None] = {}
    consumer_deltas: dict[str, int | None] = {}
    for name, recipe in snapshot.recipes.items():
        if recipe.owned is not True or ADVANCED_CIRCUIT_ITEM not in recipe.ingredients or name in exclude:
            continue
        before = None if baseline is None else baseline.recipes.get(name)
        delta = None if before is None else (None if recipe.progression() is None or before.progression() is None else recipe.progression() - before.progression())
        consumer_deltas[name] = delta
        consumer_checks[name] = None if delta is None else delta > 0
    if not consumer_checks:
        return (None if baseline is None else False), consumer_deltas
    return (True if any(value is True for value in consumer_checks.values())
            else (None if any(value is None for value in consumer_checks.values()) else False)), consumer_deltas


def _c6(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    produced, production_evidence = _new_output(snapshot, baseline, ADVANCED_CIRCUIT_ITEM)
    consumer, consumer_deltas = _consumer_leg(snapshot, baseline)
    return _all_checks("C6", "c6-v1", {"advanced_circuits_newly_produced": produced, "advanced_circuit_consumer_output": consumer}, {**production_evidence, "consumer_deltas": consumer_deltas})


def _c6_v2(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    """C6 with a production consumer: logistics chests do not satisfy the consumer leg."""
    produced, production_evidence = _new_output(snapshot, baseline, ADVANCED_CIRCUIT_ITEM)
    consumer, consumer_deltas = _consumer_leg(snapshot, baseline, exclude=C6_LOGISTICS_CHESTS)
    return _all_checks("C6", "c6-v2", {"advanced_circuits_newly_produced": produced, "advanced_circuit_consumer_output": consumer},
                       {**production_evidence, "consumer_deltas": consumer_deltas, "excluded_logistics_chests": sorted(C6_LOGISTICS_CHESTS)})


def _machine_consumption(samples: Sequence[FactorySnapshot]) -> dict[str, int] | None:
    """Crafts per recipe, counted only between consecutive samples where a machine kept that recipe.

    A mall cell re-recipe'd to a consumer recipe brings its lifetime products_finished with it
    (C72, C86c, C89 s83343: 0 -> 2294 furnaces with 411 circuits ever made); the first sample on a
    recipe is therefore a baseline, never consumption.
    """
    rows = [s.ac_consumers for s in samples]
    if not rows or not all(isinstance(r, (list, tuple)) for r in rows):
        return None
    per_recipe: dict[str, int] = {}
    for before, after in zip(rows, rows[1:]):
        previous = {(m.get("unit"), m.get("recipe")): m.get("count") for m in before if isinstance(m, Mapping)}
        for machine in after:
            if not isinstance(machine, Mapping) or machine.get("recipe") in C6_LOGISTICS_CHESTS:
                continue
            then, now = previous.get((machine.get("unit"), machine.get("recipe"))), machine.get("count")
            if isinstance(then, int) and isinstance(now, int) and now > then:
                per_recipe[machine["recipe"]] = per_recipe.get(machine["recipe"], 0) + now - then
    return per_recipe


def _c6_v3(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, history: Sequence[FactorySnapshot]) -> CheckpointResult:
    """C6 counted per machine: new advanced circuits, and a non-chest machine crafted with them."""
    produced, production_evidence = _new_output(snapshot, baseline, ADVANCED_CIRCUIT_ITEM)
    samples = list(history) if history and history[-1] is snapshot else [*history, snapshot]
    samples = [s for s in samples if s.ac_consumers is not None]
    consumed = None if baseline is None or snapshot.ac_consumers is None else _machine_consumption(samples)
    legacy, legacy_deltas = _consumer_leg(snapshot, baseline, exclude=C6_LOGISTICS_CHESTS)
    return _all_checks("C6", "c6-v3", {
        "advanced_circuits_newly_produced": produced,
        "advanced_circuit_consumer_output": None if consumed is None else any(v > 0 for v in consumed.values()),
    }, {**production_evidence, "machine_consumption": consumed, "window_samples": len(samples),
        "c6_v2_consumer_output": legacy, "consumer_deltas": legacy_deltas})


C6_V2 = MilestoneDefinition("C6", "advanced_circuit_consumption", "c6-v2", _c6_v2)


def _science_inputs(snapshot: FactorySnapshot, baseline: FactorySnapshot | None) -> tuple[Mapping, Mapping] | str:
    """Both science observations, or the reason C7-C9 must stay unknown."""
    if baseline is None:
        return "baseline_missing"
    for observed in (snapshot.science, baseline.science):
        if not isinstance(observed, Mapping):
            return "science_telemetry_missing"
        if observed.get("unavailable_reason"):
            return str(observed["unavailable_reason"])
    # Labs are the only vanilla pack consumer; a recycler would make consumption ambiguous.
    if snapshot.science.get("recyclers") != 0:
        return "alternate_pack_consumer_present"
    return snapshot.science, baseline.science


def _pack_delta(current: Mapping, before: Mapping, pack: str, part: str) -> int | None:
    now = _as_int(((current.get("packs") or {}).get(pack) or {}).get(part))
    then = _as_int(((before.get("packs") or {}).get(pack) or {}).get(part))
    return None if now is None or then is None else now - then


def _lab_research(checkpoint: str, version: str, pack: str, snapshot: FactorySnapshot,
                  baseline: FactorySnapshot | None) -> CheckpointResult:
    """Labs consumed a pack the force also made since the baseline: research, not stored packs."""
    inputs = _science_inputs(snapshot, baseline)
    if isinstance(inputs, str):
        return _all_checks(checkpoint, version, {"labs_consume_newly_made_packs": None}, {"reason": inputs})
    current, before = inputs
    produced, consumed = (_pack_delta(current, before, pack, part) for part in ("produced", "consumed"))
    labs = _as_int(current.get("labs"))
    return _all_checks(checkpoint, version, {
        f"{pack}_newly_produced": None if produced is None else produced > 0,
        f"{pack}_consumed_by_labs": None if consumed is None or labs is None else consumed > 0 and labs > 0,
    }, {"pack": pack, "production_delta": produced, "consumption_delta": consumed, "labs": labs})


def _c7(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    return _lab_research("C7", "c7-v1", CHEMICAL_SCIENCE_ITEM, snapshot, baseline)


def _c8(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    return _lab_research("C8", "c8-v1", PRODUCTION_SCIENCE_ITEM, snapshot, baseline)


def _c9(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    """mining-productivity-4 completed, with every one of its packs made and consumed in this run."""
    inputs = _science_inputs(snapshot, baseline)
    if isinstance(inputs, str):
        return _all_checks("C9", "c9-v1", {"mining_productivity_level_completed": None}, {"reason": inputs})
    current, before = inputs
    level = _as_int(current.get("mining_productivity_completed_level"))
    deltas = {pack: {part: _pack_delta(current, before, pack, part) for part in ("produced", "consumed")}
              for pack in MINING_PRODUCTIVITY_PACKS}
    values = [delta for pack in deltas.values() for delta in pack.values()]
    earned = None if any(delta is None for delta in values) else all(delta > 0 for delta in values)
    return _all_checks("C9", "c9-v1", {
        "mining_productivity_level_completed": None if level is None else level >= MINING_PRODUCTIVITY_LEVEL,
        "packs_made_and_consumed_this_run": earned,
    }, {"completed_level": level, "target_level": MINING_PRODUCTIVITY_LEVEL,
        "baseline_level": _as_int(before.get("mining_productivity_completed_level")), "pack_deltas": deltas})


DEFAULT_MILESTONES: tuple[MilestoneDefinition, ...] = (
    MilestoneDefinition("C0", "base", "c0-v1", _c0),
    MilestoneDefinition("C1", "starter_mall", "c1-v1", _c1),
    MilestoneDefinition("C2", "iron_copper_rollout", "c2-v1", _c2),
    MilestoneDefinition("C3", "stone_rollout", "c3-v1", _c3),
    MilestoneDefinition("C4", "first_plastic", "c4-v1", _c4),
    MilestoneDefinition("C5", "plastic_at_planned_rate", "c5-v4", _c5_planned_rate),
    MilestoneDefinition("C6a", "plastic_consumption", "c6a-v1", _c6a),
    MilestoneDefinition("C6", "advanced_circuit_consumption", "c6-v3", _c6_v3),
    MilestoneDefinition("C7", "chemical_science_research", "c7-v1", _c7),
    MilestoneDefinition("C8", "production_science_research", "c8-v1", _c8),
    MilestoneDefinition("C9", "mining_productivity_4", "c9-v1", _c9),
)

#: Ordered strict checkpoint IDs; CLI choices and reports derive from this, never from a fixed range.
MILESTONE_IDS: tuple[str, ...] = tuple(item.checkpoint_id for item in DEFAULT_MILESTONES)

#: Milestones added after evidence was already being recorded; older journals, evidence states and
#: restart bundles may lack them, which means "not measured then", never failed.
LATER_MILESTONE_IDS: frozenset[str] = frozenset({"C6a"})

#: A restart bundle that reached a milestone under one of these older versions is not refused: the
#: milestone is dropped from ``reached`` and re-earned under the current version (recorded as such).
REEARNED_VERSIONS: Mapping[str, frozenset[str]] = {"C5": frozenset({"c5-v1", "c5-v2", "c5-v3"})}


def default_milestones() -> tuple[MilestoneDefinition, ...]:
    """Return the ordered defaults; callers may insert sub-checkpoints."""
    return DEFAULT_MILESTONES


def evaluate_milestone(
    checkpoint_id: str,
    snapshot: FactorySnapshot | Mapping[str, Any],
    *,
    baseline: FactorySnapshot | Mapping[str, Any] | None = None,
    history: Sequence[FactorySnapshot | Mapping[str, Any]] = (),
    milestones: Sequence[MilestoneDefinition] = DEFAULT_MILESTONES,
) -> CheckpointResult:
    """Evaluate one definition without interpreting logs or side effects."""
    current = snapshot if isinstance(snapshot, FactorySnapshot) else FactorySnapshot.from_mapping(snapshot)
    prior = None if baseline is None else (baseline if isinstance(baseline, FactorySnapshot) else FactorySnapshot.from_mapping(baseline))
    samples = tuple(item if isinstance(item, FactorySnapshot) else FactorySnapshot.from_mapping(item) for item in history)
    for definition in milestones:
        if definition.checkpoint_id == checkpoint_id:
            return definition.evaluate(current, prior, samples)
    raise KeyError(f"unknown checkpoint {checkpoint_id!r}")


class CheckpointEvaluator:
    """Monotonic evaluator state for one run.

    Once a checkpoint has passed, later incomplete telemetry cannot retract it.
    The stateless :func:`evaluate_milestone` remains available for replay and
    comparison tools.
    """

    def __init__(self, milestones: Sequence[MilestoneDefinition] = DEFAULT_MILESTONES) -> None:
        self.milestones = tuple(milestones)
        self.reached: set[str] = set()

    def evaluate(
        self,
        checkpoint_id: str,
        snapshot: FactorySnapshot | Mapping[str, Any],
        *,
        baseline: FactorySnapshot | Mapping[str, Any] | None = None,
        history: Sequence[FactorySnapshot | Mapping[str, Any]] = (),
    ) -> CheckpointResult:
        if checkpoint_id in self.reached:
            definition = next(item for item in self.milestones if item.checkpoint_id == checkpoint_id)
            return _result(checkpoint_id, definition.predicate_version, PredicateStatus.PASSED, {"monotonic_reached": True})
        result = evaluate_milestone(checkpoint_id, snapshot, baseline=baseline, history=history, milestones=self.milestones)
        if result.passed:
            self.reached.add(checkpoint_id)
        return result
