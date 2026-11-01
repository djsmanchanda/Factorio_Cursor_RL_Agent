# Path: core/optimizer_construction_state.py | Purpose: Reduce explicit construction receipts to typed offline facts without touching a runtime.
"""Pure, offline construction/commission receipt reducer.

The reducer is a deterministic state machine over caller-supplied facts. It
never contacts Factorio, RCON, a save, or the filesystem, chooses no strategy,
and grants no execution authority. Instance identity, manifest digest, plan
hash, observed entity sets and throughput evidence are assertions: callers must
independently verify the instance and observation provenance before using a
state as evidence.

Order receipts acknowledge a submission only. They never prove construction,
never credit an existing entity to the transaction, and never release
reservations. Completion requires an observation that explicitly asserts
transaction ownership and a complete scan matching the immutable plan entity
set, including each explicitly planned machine recipe. Other entity settings
are not checked. Commissioning requires a separate caller-asserted time window over one
scoped consumer's cumulative production or delivery counter. The reducer
computes an exact rate in the plan's declared unit and compares only that rate
with the plan's designated throughput target; stock counts, order state and bare
presence are not production evidence. Supported units distinguish items and
fluid, per tick or per second. Per-second rates use the documented Factorio
rate of 60 ticks per second. Counters are restricted to integer observations;
fractional fluid counters require a future explicit precision contract.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from fractions import Fraction
import re

NON_CLAIMS = (
    "offline pure reducer; no network, RCON, save, filesystem, or runtime mutation",
    "scope, manifest digest, plan hash, entity observations, and throughput evidence "
    "are caller assertions; callers must verify instance and observation provenance",
    "an order receipt is not construction evidence and never releases reservations",
    "observed completion matches structural identity and explicitly planned recipes; "
    "other entity settings and production remain unverified",
    "commissioning requires caller-asserted start/end ticks and a nondecreasing scoped "
    "consumer counter; adapters must verify the window against actual runtime statistics",
    "only the exact counter-advance rate in the plan's declared item or fluid "
    "per-tick or per-second unit (60 ticks per second) is compared with "
    "the plan target; no stock, order, scalar, or presence proxy is accepted",
    "no strategy, priority, affordability policy, or execution authorization is chosen",
)

_SHA256 = re.compile(r"[0-9a-f]{64}")

ITEMS_PER_TICK = "items/tick"
ITEMS_PER_SECOND = "items/second"
FLUID_PER_TICK = "fluid/tick"
FLUID_PER_SECOND = "fluid/second"
THROUGHPUT_UNITS = (ITEMS_PER_TICK, ITEMS_PER_SECOND, FLUID_PER_TICK, FLUID_PER_SECOND)
FACTORIO_TICKS_PER_SECOND = 60


class ConstructionReason(str, Enum):
    """Typed fail-closed reason codes for construction receipt reduction."""

    INVALID_SCOPE = "invalid_scope"
    INVALID_PLAN = "invalid_plan"
    INVALID_RECEIPT = "invalid_receipt"
    INVALID_STATE = "invalid_state"
    SCOPE_MISMATCH = "scope_mismatch"
    PROJECT_MISMATCH = "project_mismatch"
    TRANSACTION_MISMATCH = "transaction_mismatch"
    PLAN_HASH_MISMATCH = "plan_hash_mismatch"
    DUPLICATE_RECEIPT_CONFLICT = "duplicate_receipt_conflict"
    SEQUENCE_DECREASING = "sequence_decreasing"
    UNKNOWN_ENTITY = "unknown_entity"
    FUNDING_OUTSIDE_PLAN = "funding_outside_plan"
    FUNDING_EXCEEDS_REQUIRED = "funding_exceeds_required"
    RELEASE_OUTSIDE_PLAN = "release_outside_plan"
    RELEASE_EXCEEDS_FUNDING = "release_exceeds_funding"
    PREMATURE_RELEASE = "premature_release"
    PREMATURE_COMMISSION = "premature_commission"
    MEASUREMENT_MISMATCH = "measurement_mismatch"
    INVALID_INTERVAL = "invalid_interval"
    COUNTER_NOT_ADVANCING = "counter_not_advancing"
    COMMISSION_TARGET_NOT_MET = "commission_target_not_met"
    ALREADY_COMMISSIONED = "already_commissioned"
    COMPLETION_REGRESSION = "completion_regression"
    FAILURE_AFTER_COMMISSION = "failure_after_commission"
    ALREADY_FAILED = "already_failed"
    FUNDING_AFTER_COMMISSION = "funding_after_commission"


class ConstructionStateError(ValueError):
    """A receipt, plan, or prior state contradicts the construction contract."""

    def __init__(self, reason: ConstructionReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.code = reason.value


def _sha256(
    value: object, label: str,
    reason: ConstructionReason = ConstructionReason.INVALID_SCOPE,
) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ConstructionStateError(reason, f"{label} must be lowercase SHA-256")
    return value


def _coord(value: object, label: str) -> Fraction:
    if isinstance(value, bool):
        raise ConstructionStateError(ConstructionReason.INVALID_PLAN, f"{label} must be numeric")
    try:
        result = Fraction(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
        raise ConstructionStateError(
            ConstructionReason.INVALID_PLAN, f"{label} must be a finite number"
        ) from error
    return result


def _amounts(
    value: object, label: str,
    reason: ConstructionReason = ConstructionReason.INVALID_PLAN,
) -> tuple[tuple[str, int], ...]:
    if not isinstance(value, Mapping):
        raise ConstructionStateError(
            reason, f"{label} must be an item-count mapping"
        )
    result: dict[str, int] = {}
    for raw_item, raw_count in value.items():
        if not isinstance(raw_item, str) or not raw_item:
            raise ConstructionStateError(
                reason, f"{label} item names must be non-empty"
            )
        if isinstance(raw_count, bool):
            raise ConstructionStateError(
                reason, f"{label} counts must be positive integers"
            )
        try:
            count = int(raw_count)
        except (TypeError, ValueError) as error:
            raise ConstructionStateError(
                reason, f"{label} counts must be positive integers"
            ) from error
        if count != raw_count or count <= 0:
            raise ConstructionStateError(
                reason, f"{label} counts must be positive integers"
            )
        result[raw_item] = count
    return tuple(sorted(result.items()))


def _fraction(value: object, label: str, *, reason: ConstructionReason) -> Fraction:
    if isinstance(value, bool):
        raise ConstructionStateError(reason, f"{label} must be a finite non-negative number")
    try:
        result = Fraction(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError, OverflowError) as error:
        raise ConstructionStateError(
            reason, f"{label} must be a finite non-negative number"
        ) from error
    if result < 0:
        raise ConstructionStateError(
            reason, f"{label} must be a finite non-negative number"
        )
    return result


def _integer(
    value: object, label: str, *, reason: ConstructionReason,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConstructionStateError(reason, f"{label} must be an int")
    return value


def _throughput_unit(
    value: object, label: str, *, reason: ConstructionReason,
) -> str:
    if not isinstance(value, str) or value not in THROUGHPUT_UNITS:
        raise ConstructionStateError(
            reason,
            f"{label} must be one of {', '.join(THROUGHPUT_UNITS)}",
        )
    return value


def _entity_key(entity: "EntityRef") -> tuple[object, ...]:
    return (entity.entity, entity.x, entity.y, entity.direction, entity.quality, entity.recipe or "")


def _entities(value: object, label: str, *, reason: ConstructionReason) -> tuple["EntityRef", ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise ConstructionStateError(reason, f"{label} must be entity identities")
    result: list[EntityRef] = []
    for item in value:
        if not isinstance(item, EntityRef):
            raise ConstructionStateError(reason, f"{label} must contain EntityRef values")
        if item in result:
            raise ConstructionStateError(reason, f"{label} contains a duplicate entity")
        result.append(item)
    return tuple(sorted(result, key=_entity_key))


def _merge_entities(
    current: tuple["EntityRef", ...], incoming: Iterable["EntityRef"],
) -> tuple["EntityRef", ...]:
    merged = list(current)
    for entity in incoming:
        if entity not in merged:
            merged.append(entity)
    return tuple(sorted(merged, key=_entity_key))


def _with_note(notes: tuple[str, ...], *incoming: str) -> tuple[str, ...]:
    result = list(notes)
    for note in incoming:
        if note and note not in result:
            result.append(note)
    return tuple(result)


@dataclass(frozen=True)
class EntityRef:
    """Structural identity and planned recipe; None means no recipe assertion.

    Observers must supply the planned recipe where one is specified, and use
    None when the plan does not specify one. None is not evidence of the
    runtime entity's actual recipe.
    """

    entity: str
    x: Fraction
    y: Fraction
    direction: int = 0
    quality: str = "normal"
    recipe: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entity, str) or not self.entity:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "entity prototype must be non-empty"
            )
        object.__setattr__(self, "x", _coord(self.x, "entity x"))
        object.__setattr__(self, "y", _coord(self.y, "entity y"))
        if isinstance(self.direction, bool) or not isinstance(self.direction, int):
            raise ConstructionStateError(ConstructionReason.INVALID_PLAN, "direction must be an int")
        if self.direction < 0 or self.direction > 15:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "direction must be 0..15"
            )
        if not isinstance(self.quality, str) or not self.quality:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "quality must be non-empty"
            )
        if self.recipe is not None and (not isinstance(self.recipe, str) or not self.recipe):
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "recipe must be non-empty when specified"
            )

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "x": str(self.x),
            "y": str(self.y),
            "direction": self.direction,
            "quality": self.quality,
            "recipe": self.recipe,
        }


@dataclass(frozen=True)
class ConstructionScope:
    """Explicit runtime identity: instance, manifest, surface, and force."""

    instance_id: str
    manifest_digest: str
    surface: str
    force: str

    def __post_init__(self) -> None:
        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise ConstructionStateError(
                ConstructionReason.INVALID_SCOPE, "instance_id must be non-empty"
            )
        _sha256(self.manifest_digest, "manifest_digest")
        if not isinstance(self.surface, str) or not self.surface:
            raise ConstructionStateError(
                ConstructionReason.INVALID_SCOPE, "surface must be non-empty"
            )
        if not isinstance(self.force, str) or not self.force:
            raise ConstructionStateError(
                ConstructionReason.INVALID_SCOPE, "force must be non-empty"
            )

    def to_dict(self) -> dict:
        return {
            "instance_id": self.instance_id,
            "manifest_digest": self.manifest_digest,
            "surface": self.surface,
            "force": self.force,
        }


@dataclass(frozen=True)
class ConstructionBinding:
    """Immutable project, transaction, plan, and scope identity for receipts."""

    scope: ConstructionScope
    project_id: str
    transaction_id: str
    plan_hash: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ConstructionScope):
            raise ConstructionStateError(
                ConstructionReason.INVALID_SCOPE, "binding requires a ConstructionScope"
            )
        for label, value in (
            ("project_id", self.project_id),
            ("transaction_id", self.transaction_id),
        ):
            if not isinstance(value, str) or not value:
                raise ConstructionStateError(
                    ConstructionReason.INVALID_PLAN, f"{label} must be non-empty"
                )
        _sha256(
            self.plan_hash, "plan_hash", ConstructionReason.INVALID_PLAN,
        )

    def to_dict(self) -> dict:
        return {
            "scope": self.scope.to_dict(),
            "project_id": self.project_id,
            "transaction_id": self.transaction_id,
            "plan_hash": self.plan_hash,
        }


@dataclass(frozen=True)
class ConstructionPlan:
    """Immutable construction contract: entities, bill, and throughput target."""

    binding: ConstructionBinding
    required_entities: tuple[EntityRef, ...]
    required_materials: Mapping[str, int]
    throughput_output: str
    throughput_target: Fraction
    throughput_unit: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, ConstructionBinding):
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "plan requires a ConstructionBinding"
            )
        object.__setattr__(
            self, "required_entities",
            _entities(
                self.required_entities, "required_entities",
                reason=ConstructionReason.INVALID_PLAN,
            ),
        )
        if not self.required_entities:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "plan requires at least one entity"
            )
        object.__setattr__(
            self, "required_materials",
            _amounts(self.required_materials, "required_materials"),
        )
        if not isinstance(self.throughput_output, str) or not self.throughput_output:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "throughput_output must be non-empty"
            )
        object.__setattr__(
            self, "throughput_unit",
            _throughput_unit(
                self.throughput_unit, "throughput_unit",
                reason=ConstructionReason.INVALID_PLAN,
            ),
        )
        target = _fraction(
            self.throughput_target, "throughput_target",
            reason=ConstructionReason.INVALID_PLAN,
        )
        if target <= 0:
            raise ConstructionStateError(
                ConstructionReason.INVALID_PLAN, "throughput_target must be positive"
            )
        object.__setattr__(self, "throughput_target", target)

    def to_dict(self) -> dict:
        return {
            "binding": self.binding.to_dict(),
            "required_entities": [entity.to_dict() for entity in self.required_entities],
            "required_materials": dict(self.required_materials),
            "throughput_output": self.throughput_output,
            "throughput_target": str(self.throughput_target),
            "throughput_unit": self.throughput_unit,
        }


@dataclass(frozen=True)
class OrderAction:
    """A submission was acknowledged; this is not construction evidence."""

    entities: tuple[EntityRef, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "entities",
            _entities(self.entities, "order entities", reason=ConstructionReason.INVALID_RECEIPT),
        )
        if not self.entities:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "order requires at least one entity"
            )


@dataclass(frozen=True)
class ObservationAction:
    """An independently asserted entity observation in the plan's region."""

    entities: tuple[EntityRef, ...]
    complete: bool
    transaction_owned: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "entities",
            _entities(
                self.entities, "observed entities",
                reason=ConstructionReason.INVALID_RECEIPT,
            ),
        )
        if not isinstance(self.complete, bool):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "observation complete must be bool"
            )
        if not isinstance(self.transaction_owned, bool):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT,
                "observation transaction_owned must be bool",
            )


@dataclass(frozen=True)
class FundingAction:
    """Explicit construction funding committed to this project's bill."""

    committed: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "committed",
            _amounts(
                self.committed, "committed funding",
                ConstructionReason.INVALID_RECEIPT,
            ),
        )
        if not self.committed:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "funding requires a positive amount"
            )


@dataclass(frozen=True)
class ReleaseAction:
    """Explicit return of previously funded stock; never implicit on order."""

    released: Mapping[str, int]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "released",
            _amounts(
                self.released, "released funding",
                ConstructionReason.INVALID_RECEIPT,
            ),
        )
        if not self.released:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "release requires a positive amount"
            )


@dataclass(frozen=True)
class ThroughputEvidence:
    """Caller-asserted production/delivery counter window for one output.

    The reducer computes the exact rate in the evidence's declared unit and
    compares only that rate with the plan's target. Per-tick units use
    ``(end_count - start_count) / (end_tick - start_tick)``; per-second units
    multiply that rate by 60. Item and fluid units are not interchangeable.
    Counters are cumulative integers for one scoped consumer and must advance.
    ``evidence_id`` names the observation, and ``binding`` must match the plan's
    instance, surface, force, project, transaction and plan hash. This class does
    not prove that the window happened: an adapter must verify the start/end
    ticks, consumer scope and counter values against actual runtime statistics.
    """

    evidence_id: str
    binding: ConstructionBinding
    output: str
    unit: str
    consumer: str
    start_tick: int
    end_tick: int
    start_count: int
    end_count: int

    def __post_init__(self) -> None:
        for label, value in (
            ("evidence_id", self.evidence_id),
            ("output", self.output),
            ("consumer", self.consumer),
        ):
            if not isinstance(value, str) or not value:
                raise ConstructionStateError(
                    ConstructionReason.INVALID_RECEIPT, f"{label} must be non-empty"
                )
        object.__setattr__(
            self, "unit",
            _throughput_unit(
                self.unit, "unit", reason=ConstructionReason.INVALID_RECEIPT,
            ),
        )
        if not isinstance(self.binding, ConstructionBinding):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT,
                "throughput evidence requires a ConstructionBinding",
            )
        start_tick = _integer(
            self.start_tick, "start_tick", reason=ConstructionReason.INVALID_INTERVAL,
        )
        end_tick = _integer(
            self.end_tick, "end_tick", reason=ConstructionReason.INVALID_INTERVAL,
        )
        if start_tick < 0 or end_tick <= start_tick:
            raise ConstructionStateError(
                ConstructionReason.INVALID_INTERVAL,
                "throughput window requires 0 <= start_tick < end_tick",
            )
        object.__setattr__(self, "start_tick", start_tick)
        object.__setattr__(self, "end_tick", end_tick)
        start_count = _integer(
            self.start_count, "start_count",
            reason=ConstructionReason.COUNTER_NOT_ADVANCING,
        )
        end_count = _integer(
            self.end_count, "end_count",
            reason=ConstructionReason.COUNTER_NOT_ADVANCING,
        )
        if start_count < 0 or end_count <= start_count:
            raise ConstructionStateError(
                ConstructionReason.COUNTER_NOT_ADVANCING,
                "throughput counters must be non-negative and strictly advance",
            )
        object.__setattr__(self, "start_count", start_count)
        object.__setattr__(self, "end_count", end_count)

    @property
    def elapsed_ticks(self) -> int:
        return self.end_tick - self.start_tick

    @property
    def counter_advance(self) -> int:
        return self.end_count - self.start_count

    @property
    def rate(self) -> Fraction:
        """Exact counter advance per unit in the evidence's declared unit.

        Per-tick units use ``advance / ticks``. Per-second units use
        ``60 * advance / ticks`` because Factorio runs at 60 ticks per second.
        The unit is validated at construction, so this is never a per-tick value
        mislabeled as per-second or the reverse.
        """
        if self.unit in (ITEMS_PER_SECOND, FLUID_PER_SECOND):
            return Fraction(
                FACTORIO_TICKS_PER_SECOND * self.counter_advance,
                self.elapsed_ticks,
            )
        return Fraction(self.counter_advance, self.elapsed_ticks)


@dataclass(frozen=True)
class FailureAction:
    """The transaction is terminally failed for the stated reason."""

    code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "failure code must be non-empty"
            )
        if not isinstance(self.detail, str):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "failure detail must be a string"
            )


ConstructionAction = (
    OrderAction | ObservationAction | FundingAction | ReleaseAction
    | ThroughputEvidence | FailureAction
)


@dataclass(frozen=True)
class ConstructionReceipt:
    """One ordered, idempotent construction receipt."""

    receipt_id: str
    sequence: int
    binding: ConstructionBinding
    action: ConstructionAction

    def __post_init__(self) -> None:
        if not isinstance(self.receipt_id, str) or not self.receipt_id:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "receipt_id must be non-empty"
            )
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "receipt sequence must be an int"
            )
        if self.sequence < 1:
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "receipt sequence must be positive"
            )
        if not isinstance(self.binding, ConstructionBinding):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "receipt requires a ConstructionBinding"
            )
        if not isinstance(
            self.action,
            (OrderAction, ObservationAction, FundingAction, ReleaseAction,
             ThroughputEvidence, FailureAction),
        ):
            raise ConstructionStateError(
                ConstructionReason.INVALID_RECEIPT, "unknown receipt action"
            )


class ConstructionPhase(str, Enum):
    """Typed construction fact emitted by the reducer."""

    WAIT = "wait"
    PARTIAL = "partial"
    BUILT = "built"
    COMMISSIONED = "commissioned"
    FAILED = "failed"


@dataclass(frozen=True)
class ThroughputFact:
    """Recorded time-window evidence with its exact computed rate."""

    output: str
    unit: str
    consumer: str
    start_tick: int
    end_tick: int
    start_count: int
    end_count: int
    elapsed_ticks: int
    counter_advance: int
    rate: Fraction
    evidence_id: str
    receipt_id: str
    sequence: int


@dataclass(frozen=True)
class FailureFact:
    """Terminal failure evidence with the receipt that asserted it."""

    code: str
    detail: str
    receipt_id: str
    sequence: int


@dataclass(frozen=True)
class ConstructionState:
    """Complete replayable facts for one construction transaction."""

    binding: ConstructionBinding
    required_entities: tuple[EntityRef, ...]
    required_materials: tuple[tuple[str, int], ...]
    throughput_output: str
    throughput_target: Fraction
    throughput_unit: str
    phase: ConstructionPhase = ConstructionPhase.WAIT
    ordered_entities: tuple[EntityRef, ...] = ()
    observed_entities: tuple[EntityRef, ...] = ()
    unverified_observed_entities: tuple[EntityRef, ...] = ()
    foreign_observed_entities: tuple[EntityRef, ...] = ()
    observation_complete: bool = False
    funded: tuple[tuple[str, int], ...] = ()
    released: tuple[tuple[str, int], ...] = ()
    throughput_fact: ThroughputFact | None = None
    failure: FailureFact | None = None
    last_sequence: int = 0
    receipts: tuple[ConstructionReceipt, ...] = ()
    notes: tuple[str, ...] = NON_CLAIMS

    @property
    def order_acknowledged(self) -> bool:
        return bool(self.ordered_entities)

    @property
    def construction_observed(self) -> bool:
        return self.phase in (ConstructionPhase.BUILT, ConstructionPhase.COMMISSIONED)

    @property
    def commissioned(self) -> bool:
        return self.phase is ConstructionPhase.COMMISSIONED

    @property
    def failed(self) -> bool:
        return self.phase is ConstructionPhase.FAILED

    @property
    def unresolved_funding(self) -> tuple[tuple[str, int], ...]:
        """Required materials not yet explicitly committed; never inferred."""
        funded = dict(self.funded)
        return tuple(
            (item, required - funded.get(item, 0))
            for item, required in self.required_materials
            if required > funded.get(item, 0)
        )

    @property
    def held_reservations(self) -> tuple[tuple[str, int], ...]:
        """Committed funding minus explicit releases; order never changes this."""
        released = dict(self.released)
        return tuple(
            (item, committed - released.get(item, 0))
            for item, committed in self.funded
            if committed > released.get(item, 0)
        )

    def facts(self) -> dict:
        """Typed facts with boundary flags kept explicit."""
        return {
            "construction": self.phase.value,
            "order_acknowledged": self.order_acknowledged,
            "order_is_completion": False,
            "observed_completion": self.construction_observed,
            "measured_production": self.throughput_fact is not None,
            "throughput_rate": (
                None if self.throughput_fact is None
                else str(self.throughput_fact.rate)
            ),
            "commission_met": self.commissioned,
            "unresolved_funding": dict(self.unresolved_funding),
            "held_reservations": dict(self.held_reservations),
        }

    def to_dict(self) -> dict:
        return {
            "binding": self.binding.to_dict(),
            "phase": self.phase.value,
            "required_entities": [entity.to_dict() for entity in self.required_entities],
            "required_materials": dict(self.required_materials),
            "throughput_output": self.throughput_output,
            "throughput_target": str(self.throughput_target),
            "throughput_unit": self.throughput_unit,
            "ordered_entities": [entity.to_dict() for entity in self.ordered_entities],
            "observed_entities": [entity.to_dict() for entity in self.observed_entities],
            "unverified_observed_entities": [
                entity.to_dict() for entity in self.unverified_observed_entities
            ],
            "foreign_observed_entities": [
                entity.to_dict() for entity in self.foreign_observed_entities
            ],
            "observation_complete": self.observation_complete,
            "funded": dict(self.funded),
            "released": dict(self.released),
            "unresolved_funding": dict(self.unresolved_funding),
            "held_reservations": dict(self.held_reservations),
            "throughput": (
                None if self.throughput_fact is None else {
                    "output": self.throughput_fact.output,
                    "unit": self.throughput_fact.unit,
                    "consumer": self.throughput_fact.consumer,
                    "start_tick": self.throughput_fact.start_tick,
                    "end_tick": self.throughput_fact.end_tick,
                    "start_count": self.throughput_fact.start_count,
                    "end_count": self.throughput_fact.end_count,
                    "elapsed_ticks": self.throughput_fact.elapsed_ticks,
                    "counter_advance": self.throughput_fact.counter_advance,
                    "rate": str(self.throughput_fact.rate),
                    "evidence_id": self.throughput_fact.evidence_id,
                    "receipt_id": self.throughput_fact.receipt_id,
                    "sequence": self.throughput_fact.sequence,
                }
            ),
            "failure": (
                None if self.failure is None else {
                    "code": self.failure.code,
                    "detail": self.failure.detail,
                    "receipt_id": self.failure.receipt_id,
                    "sequence": self.failure.sequence,
                }
            ),
            "last_sequence": self.last_sequence,
            "receipt_ids": [receipt.receipt_id for receipt in self.receipts],
            "facts": self.facts(),
            "notes": list(self.notes),
        }


def initial_construction_state(plan: ConstructionPlan) -> ConstructionState:
    """Start a reducer state from an immutable construction plan."""
    if not isinstance(plan, ConstructionPlan):
        raise ConstructionStateError(
            ConstructionReason.INVALID_PLAN, "plan must be a ConstructionPlan"
        )
    return ConstructionState(
        binding=plan.binding,
        required_entities=plan.required_entities,
        required_materials=tuple(plan.required_materials),
        throughput_output=plan.throughput_output,
        throughput_target=plan.throughput_target,
        throughput_unit=plan.throughput_unit,
    )


def _check_binding_identity(
    expected: ConstructionBinding, actual: ConstructionBinding, label: str,
) -> None:
    for field, left, right in (
        ("instance_id", expected.scope.instance_id, actual.scope.instance_id),
        ("manifest_digest", expected.scope.manifest_digest, actual.scope.manifest_digest),
        ("surface", expected.scope.surface, actual.scope.surface),
        ("force", expected.scope.force, actual.scope.force),
    ):
        if left != right:
            raise ConstructionStateError(
                ConstructionReason.SCOPE_MISMATCH,
                f"{label} scope {field} does not match the plan",
            )
    if expected.project_id != actual.project_id:
        raise ConstructionStateError(
            ConstructionReason.PROJECT_MISMATCH,
            f"{label} belongs to another project",
        )
    if expected.transaction_id != actual.transaction_id:
        raise ConstructionStateError(
            ConstructionReason.TRANSACTION_MISMATCH,
            f"{label} belongs to another transaction",
        )
    if expected.plan_hash != actual.plan_hash:
        raise ConstructionStateError(
            ConstructionReason.PLAN_HASH_MISMATCH,
            f"{label} belongs to another plan",
        )


def _check_binding(plan: ConstructionPlan, receipt: ConstructionReceipt) -> None:
    _check_binding_identity(
        plan.binding, receipt.binding, f"receipt {receipt.receipt_id!r}",
    )


def _replay_state(
    plan: ConstructionPlan, receipts: tuple[ConstructionReceipt, ...],
) -> ConstructionState:
    """Reduce prior receipt history without re-validating the prior state."""
    state = initial_construction_state(plan)
    for receipt in receipts:
        state = _apply_receipt(plan, state, receipt)
    return state


def _check_state(plan: ConstructionPlan, state: ConstructionState) -> None:
    if not isinstance(plan, ConstructionPlan):
        raise ConstructionStateError(
            ConstructionReason.INVALID_PLAN, "plan must be a ConstructionPlan"
        )
    if not isinstance(state, ConstructionState):
        raise ConstructionStateError(
            ConstructionReason.INVALID_STATE, "prior state must be a ConstructionState"
        )
    if (
        state.binding != plan.binding
        or state.required_entities != plan.required_entities
        or state.required_materials != tuple(plan.required_materials)
        or state.throughput_output != plan.throughput_output
        or state.throughput_target != plan.throughput_target
        or state.throughput_unit != plan.throughput_unit
    ):
        raise ConstructionStateError(
            ConstructionReason.INVALID_STATE,
            "prior state does not belong to this immutable plan",
        )
    if not isinstance(state.receipts, tuple) or not all(
        isinstance(receipt, ConstructionReceipt) for receipt in state.receipts
    ):
        raise ConstructionStateError(
            ConstructionReason.INVALID_STATE,
            "prior state receipt history must be ConstructionReceipt values",
        )
    try:
        replayed = _replay_state(plan, state.receipts)
    except ConstructionStateError as error:
        raise ConstructionStateError(
            ConstructionReason.INVALID_STATE,
            f"prior state receipt history is not reducible: {error}",
        ) from error
    if replayed != state:
        raise ConstructionStateError(
            ConstructionReason.INVALID_STATE,
            "prior state contradicts deterministic replay of its receipt history",
        )


def _apply_order(
    plan: ConstructionPlan, state: ConstructionState, action: OrderAction,
) -> ConstructionState:
    known = set(plan.required_entities)
    unknown = [entity for entity in action.entities if entity not in known]
    if unknown:
        raise ConstructionStateError(
            ConstructionReason.UNKNOWN_ENTITY,
            "order contains an entity outside the immutable plan",
        )
    return replace(
        state,
        ordered_entities=_merge_entities(state.ordered_entities, action.entities),
        notes=_with_note(
            state.notes,
            "order acknowledged: submission is not construction evidence "
            "and does not release reservations",
        ),
    )


def _apply_observation(
    plan: ConstructionPlan, state: ConstructionState, action: ObservationAction,
) -> ConstructionState:
    known = set(plan.required_entities)
    credited = tuple(entity for entity in action.entities if entity in known)
    foreign = tuple(entity for entity in action.entities if entity not in known)
    if not action.transaction_owned:
        return replace(
            state,
            unverified_observed_entities=_merge_entities(
                state.unverified_observed_entities, action.entities,
            ),
            notes=_with_note(
                state.notes,
                "observation did not assert transaction ownership; "
                "no entity was credited to this transaction",
            ),
        )

    observed = _merge_entities(state.observed_entities, credited)
    complete_match = action.complete and known <= set(credited)
    if state.construction_observed:
        if not (known <= set(credited)):
            raise ConstructionStateError(
                ConstructionReason.COMPLETION_REGRESSION,
                "observation contradicts a previously observed complete entity set",
            )
        return replace(
            state,
            observed_entities=observed,
            foreign_observed_entities=_merge_entities(
                state.foreign_observed_entities, foreign,
            ),
            observation_complete=state.observation_complete or action.complete,
        )

    if state.failed:
        raise ConstructionStateError(
            ConstructionReason.ALREADY_FAILED, "failed construction cannot observe progress"
        )
    if complete_match:
        phase = ConstructionPhase.BUILT
        note = "observed completion matches the immutable plan entity set"
    elif action.complete:
        phase = (
            ConstructionPhase.PARTIAL if observed else ConstructionPhase.WAIT
        )
        note = (
            "complete observation is missing required entities; "
            "construction is not built"
        )
    else:
        phase = ConstructionPhase.PARTIAL if observed else ConstructionPhase.WAIT
        note = ""
    return replace(
        state,
        phase=phase,
        observed_entities=observed,
        foreign_observed_entities=_merge_entities(
            state.foreign_observed_entities, foreign,
        ),
        observation_complete=action.complete,
        notes=_with_note(state.notes, note),
    )


def _apply_funding(
    plan: ConstructionPlan, state: ConstructionState, action: FundingAction,
) -> ConstructionState:
    if state.commissioned:
        raise ConstructionStateError(
            ConstructionReason.FUNDING_AFTER_COMMISSION,
            "commissioned construction cannot receive new funding",
        )
    required = dict(plan.required_materials)
    funded = dict(state.funded)
    for item, count in action.committed:
        if item not in required:
            raise ConstructionStateError(
                ConstructionReason.FUNDING_OUTSIDE_PLAN,
                f"funding item {item!r} is outside the immutable plan bill",
            )
        updated = funded.get(item, 0) + count
        if updated > required[item]:
            raise ConstructionStateError(
                ConstructionReason.FUNDING_EXCEEDS_REQUIRED,
                f"funding for {item!r} exceeds the immutable plan bill",
            )
        funded[item] = updated
    return replace(state, funded=tuple(sorted(funded.items())))


def _apply_release(
    plan: ConstructionPlan, state: ConstructionState, action: ReleaseAction,
) -> ConstructionState:
    if state.phase not in (ConstructionPhase.COMMISSIONED, ConstructionPhase.FAILED):
        raise ConstructionStateError(
            ConstructionReason.PREMATURE_RELEASE,
            "reservations may be released only after a terminal "
            "commissioned or failed outcome",
        )
    required = dict(plan.required_materials)
    funded = dict(state.funded)
    released = dict(state.released)
    for item, count in action.released:
        if item not in required:
            raise ConstructionStateError(
                ConstructionReason.RELEASE_OUTSIDE_PLAN,
                f"release item {item!r} is outside the immutable plan bill",
            )
        held = funded.get(item, 0) - released.get(item, 0)
        if count > held:
            raise ConstructionStateError(
                ConstructionReason.RELEASE_EXCEEDS_FUNDING,
                f"release for {item!r} exceeds its held reservation",
            )
        released[item] = released.get(item, 0) + count
    return replace(state, released=tuple(sorted(released.items())))


def _apply_throughput(
    plan: ConstructionPlan, state: ConstructionState, action: ThroughputEvidence,
    receipt: ConstructionReceipt,
) -> ConstructionState:
    if state.phase not in (ConstructionPhase.BUILT, ConstructionPhase.COMMISSIONED):
        raise ConstructionStateError(
            ConstructionReason.PREMATURE_COMMISSION,
            "commissioning requires independently observed construction completion",
        )
    _check_binding_identity(
        plan.binding, action.binding, f"evidence {action.evidence_id!r}",
    )
    if action.output != plan.throughput_output or action.unit != plan.throughput_unit:
        raise ConstructionStateError(
            ConstructionReason.MEASUREMENT_MISMATCH,
            "throughput evidence output or unit does not match the immutable plan target",
        )
    if state.commissioned:
        raise ConstructionStateError(
            ConstructionReason.ALREADY_COMMISSIONED,
            "construction is already commissioned",
        )
    fact = ThroughputFact(
        output=action.output,
        unit=action.unit,
        consumer=action.consumer,
        start_tick=action.start_tick,
        end_tick=action.end_tick,
        start_count=action.start_count,
        end_count=action.end_count,
        elapsed_ticks=action.elapsed_ticks,
        counter_advance=action.counter_advance,
        rate=action.rate,
        evidence_id=action.evidence_id,
        receipt_id=receipt.receipt_id,
        sequence=receipt.sequence,
    )
    commissioned = action.rate >= plan.throughput_target
    note = (
        "calculated throughput rate meets the plan target"
        if commissioned else
        "calculated throughput rate is below the plan target; project remains built"
    )
    return replace(
        state,
        phase=ConstructionPhase.COMMISSIONED if commissioned else ConstructionPhase.BUILT,
        throughput_fact=fact,
        notes=_with_note(state.notes, note),
    )


def _apply_failure(
    state: ConstructionState, action: FailureAction, receipt: ConstructionReceipt,
) -> ConstructionState:
    if state.commissioned:
        raise ConstructionStateError(
            ConstructionReason.FAILURE_AFTER_COMMISSION,
            "commissioned construction cannot be marked failed",
        )
    if state.failed:
        raise ConstructionStateError(
            ConstructionReason.ALREADY_FAILED, "construction is already failed"
        )
    return replace(
        state,
        phase=ConstructionPhase.FAILED,
        failure=FailureFact(
            code=action.code,
            detail=action.detail,
            receipt_id=receipt.receipt_id,
            sequence=receipt.sequence,
        ),
        notes=_with_note(state.notes, f"construction failed: {action.code}"),
    )


def _apply_receipt(
    plan: ConstructionPlan, state: ConstructionState, receipt: ConstructionReceipt,
) -> ConstructionState:
    """Apply one receipt to a state already known to belong to the plan."""
    if not isinstance(receipt, ConstructionReceipt):
        raise ConstructionStateError(
            ConstructionReason.INVALID_RECEIPT, "receipt must be a ConstructionReceipt"
        )

    for existing in state.receipts:
        if existing.receipt_id == receipt.receipt_id:
            if existing == receipt:
                return state
            raise ConstructionStateError(
                ConstructionReason.DUPLICATE_RECEIPT_CONFLICT,
                f"receipt {receipt.receipt_id!r} was replayed with different content",
            )
    if receipt.sequence <= state.last_sequence:
        raise ConstructionStateError(
            ConstructionReason.SEQUENCE_DECREASING,
            f"receipt sequence {receipt.sequence} is not after {state.last_sequence}",
        )
    _check_binding(plan, receipt)

    action = receipt.action
    if isinstance(action, FailureAction):
        updated = _apply_failure(state, action, receipt)
    elif state.failed and not isinstance(action, ReleaseAction):
        raise ConstructionStateError(
            ConstructionReason.ALREADY_FAILED,
            "failed construction accepts only explicit release evidence",
        )
    elif isinstance(action, OrderAction):
        updated = _apply_order(plan, state, action)
    elif isinstance(action, ObservationAction):
        updated = _apply_observation(plan, state, action)
    elif isinstance(action, FundingAction):
        updated = _apply_funding(plan, state, action)
    elif isinstance(action, ReleaseAction):
        updated = _apply_release(plan, state, action)
    elif isinstance(action, ThroughputEvidence):
        updated = _apply_throughput(plan, state, action, receipt)
    else:  # pragma: no cover - constructor rejects unknown actions
        raise ConstructionStateError(
            ConstructionReason.INVALID_RECEIPT, "unknown receipt action"
        )
    return replace(
        updated,
        last_sequence=receipt.sequence,
        receipts=state.receipts + (receipt,),
    )


def apply_construction_receipt(
    plan: ConstructionPlan, state: ConstructionState, receipt: ConstructionReceipt,
) -> ConstructionState:
    """Apply one ordered receipt idempotently and return a new immutable state."""
    if not isinstance(plan, ConstructionPlan):
        raise ConstructionStateError(
            ConstructionReason.INVALID_PLAN, "plan must be a ConstructionPlan"
        )
    _check_state(plan, state)
    return _apply_receipt(plan, state, receipt)


def reduce_construction(
    plan: ConstructionPlan, receipts: Iterable[ConstructionReceipt],
) -> ConstructionState:
    """Replay an ordered receipt stream from the plan's initial state."""
    state = initial_construction_state(plan)
    for receipt in receipts:
        state = _apply_receipt(plan, state, receipt)
    return state


def resume_construction(
    plan: ConstructionPlan, state: ConstructionState,
    receipts: Iterable[ConstructionReceipt],
) -> ConstructionState:
    """Continue a partial construction from a previously reduced state."""
    _check_state(plan, state)
    for receipt in receipts:
        state = _apply_receipt(plan, state, receipt)
    return state
