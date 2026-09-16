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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


PREDICATE_SCHEMA_VERSION = 1
PLASTIC_ITEM = "plastic-bar"
ADVANCED_CIRCUIT_ITEM = "advanced-circuit"
IRON_GEAR_RECIPE = "iron-gear-wheel"
COPPER_CABLE_RECIPE = "copper-cable"


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
        )


@dataclass(frozen=True)
class FoundationEvidence:
    """Health and output evidence for a persistent resource foundation."""

    name: str
    machine_count: int | None = None
    healthy: bool | None = None
    producing: bool | None = None
    output_count: int | None = None

    @classmethod
    def from_mapping(cls, name: str, value: Mapping[str, Any] | Any) -> "FoundationEvidence":
        if not isinstance(value, Mapping):
            return cls(name=name, machine_count=_as_int(value))
        return cls(
            name=str(value.get("name", name)),
            machine_count=_as_int(value.get("machine_count", value.get("machines"))),
            healthy=_as_bool(value.get("healthy", value.get("valid"))),
            producing=_as_bool(value.get("producing", value.get("active"))),
            output_count=_item_counter(value, "output_count", "output", "produced"),
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

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> "CounterEvidence":
        if not isinstance(value, Mapping):
            return cls(produced=_as_int(value), delivered=_as_int(value))
        return cls(
            produced=_item_counter(value, "produced", "output_count", "output", "production"),
            delivered=_item_counter(value, "delivered", "delivered_count", "provider_count"),
            craft_count=_item_counter(value, "craft_count", "crafts", "products_finished"),
            available=_item_counter(value, "available", "count", "inventory"),
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


def _c1(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    working = [machine for machine in snapshot.mall_assemblers if machine.working is True]
    has_recipe = {recipe: any(machine.recipe == recipe for machine in working) for recipe in (IRON_GEAR_RECIPE, COPPER_CABLE_RECIPE)}
    count_ok: bool | None = len(working) >= 4 if snapshot.mall_assemblers else None
    checks = {
        "iron_starter_ready": _starter_ready(snapshot, "iron"),
        "copper_starter_ready": _starter_ready(snapshot, "copper"),
        "stone_starter_ready": _starter_ready(snapshot, "stone"),
        "at_least_four_working_mall_assemblers": count_ok,
        "iron_gear_producer_working": has_recipe[IRON_GEAR_RECIPE] if working else None,
        "copper_cable_producer_working": has_recipe[COPPER_CABLE_RECIPE] if working else None,
    }
    return _all_checks("C1", "c1-v1", checks, {"working_mall_count": len(working), "recipes": has_recipe})


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
    return _all_checks("C2", "c2-v1", checks, {})


def _c3(snapshot: FactorySnapshot, _baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    checks = {"stone_foundation_ready": _foundation_ready(snapshot, "stone"), "stone_starter_absent": _starter_absent(snapshot, "stone")}
    return _all_checks("C3", "c3-v1", checks, {})


def _new_output(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, item: str) -> tuple[bool | None, dict[str, Any]]:
    if baseline is None:
        return None, {"baseline": "missing"}
    current = snapshot.production.get(item)
    before = baseline.production.get(item)
    delta = _counter_delta(current, before)
    return (None if delta is None else delta > 0), {"production_delta": delta}


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


def _c6(snapshot: FactorySnapshot, baseline: FactorySnapshot | None, _history: Sequence[FactorySnapshot]) -> CheckpointResult:
    produced, production_evidence = _new_output(snapshot, baseline, ADVANCED_CIRCUIT_ITEM)
    consumer_checks: dict[str, bool | None] = {}
    consumer_deltas: dict[str, int | None] = {}
    for name, recipe in snapshot.recipes.items():
        if recipe.owned is not True or ADVANCED_CIRCUIT_ITEM not in recipe.ingredients:
            continue
        before = None if baseline is None else baseline.recipes.get(name)
        delta = None if before is None else (None if recipe.progression() is None or before.progression() is None else recipe.progression() - before.progression())
        consumer_deltas[name] = delta
        consumer_checks[name] = None if delta is None else delta > 0
    if not consumer_checks:
        consumer = None if baseline is None else False
    else:
        consumer = True if any(value is True for value in consumer_checks.values()) else (None if any(value is None for value in consumer_checks.values()) else False)
    return _all_checks("C6", "c6-v1", {"advanced_circuits_newly_produced": produced, "advanced_circuit_consumer_output": consumer}, {**production_evidence, "consumer_deltas": consumer_deltas})


DEFAULT_MILESTONES: tuple[MilestoneDefinition, ...] = (
    MilestoneDefinition("C0", "base", "c0-v1", _c0),
    MilestoneDefinition("C1", "starter_mall", "c1-v1", _c1),
    MilestoneDefinition("C2", "iron_copper_rollout", "c2-v1", _c2),
    MilestoneDefinition("C3", "stone_rollout", "c3-v1", _c3),
    MilestoneDefinition("C4", "first_plastic", "c4-v1", _c4),
    MilestoneDefinition("C5", "stable_plastic", "c5-v1", _c5),
    MilestoneDefinition("C6", "advanced_circuit_consumption", "c6-v1", _c6),
)


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
