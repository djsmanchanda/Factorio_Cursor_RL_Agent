# Path: tests/test_optimizer_construction_state.py
# Purpose: Verify the offline construction receipt reducer: replay idempotence,
# scope/plan binding, partial recovery, observed completion and commission guards.
#
# Every identity, entity, bill and measurement below is SYNTHETIC test data.
# Nothing here contacts Factorio, RCON, a save, a filesystem path or a live force.

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from core.optimizer_construction_state import (
    ConstructionBinding,
    ConstructionPhase,
    ConstructionPlan,
    ConstructionReason,
    ConstructionReceipt,
    ConstructionScope,
    ConstructionStateError,
    EntityRef,
    FailureAction,
    FLUID_PER_SECOND,
    FLUID_PER_TICK,
    FundingAction,
    ITEMS_PER_SECOND,
    ITEMS_PER_TICK,
    NON_CLAIMS,
    ObservationAction,
    OrderAction,
    ReleaseAction,
    ThroughputEvidence,
    apply_construction_receipt,
    initial_construction_state,
    reduce_construction,
    resume_construction,
)

pytestmark = pytest.mark.contracts

_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _scope(
    instance_id: str = "training-instance-a",
    manifest_digest: str = _DIGEST_A,
    surface: str = "training/synthetic-slice",
    force: str = "training-synthetic-force",
) -> ConstructionScope:
    return ConstructionScope(instance_id, manifest_digest, surface, force)


def _binding(
    scope: ConstructionScope | None = None,
    project_id: str = "project-1",
    transaction_id: str = "transaction-1",
    plan_hash: str = _DIGEST_B,
) -> ConstructionBinding:
    return ConstructionBinding(scope or _scope(), project_id, transaction_id, plan_hash)


def _belt() -> EntityRef:
    return EntityRef("transport-belt", 0, 0)


def _inserter() -> EntityRef:
    return EntityRef("inserter", 1, 0, 12)


def _plan(
    binding: ConstructionBinding | None = None,
    *,
    throughput_output: str = "iron-gear-wheel",
    throughput_target: Fraction = Fraction(1, 6),
    throughput_unit: str = "items/tick",
) -> ConstructionPlan:
    return ConstructionPlan(
        binding=binding or _binding(),
        required_entities=(_belt(), _inserter()),
        required_materials={"transport-belt": 2, "inserter": 1},
        throughput_output=throughput_output,
        throughput_target=throughput_target,
        throughput_unit=throughput_unit,
    )


def _receipt(
    receipt_id: str,
    sequence: int,
    action: object,
    binding: ConstructionBinding | None = None,
) -> ConstructionReceipt:
    return ConstructionReceipt(receipt_id, sequence, binding or _binding(), action)


def _order(receipt_id: str = "r-order", sequence: int = 1) -> ConstructionReceipt:
    return _receipt(receipt_id, sequence, OrderAction((_belt(), _inserter())))


def _observe(
    entities: tuple[EntityRef, ...],
    *,
    complete: bool,
    owned: bool = True,
    receipt_id: str = "r-observe",
    sequence: int = 2,
) -> ConstructionReceipt:
    return _receipt(
        receipt_id, sequence,
        ObservationAction(entities, complete=complete, transaction_owned=owned),
    )


def _fund(
    committed: dict[str, int],
    *,
    receipt_id: str = "r-fund",
    sequence: int = 2,
) -> ConstructionReceipt:
    return _receipt(receipt_id, sequence, FundingAction(committed))


def _throughput(
    *,
    evidence_id: str = "evidence-1",
    binding: ConstructionBinding | None = None,
    output: str = "iron-gear-wheel",
    unit: str = "items/tick",
    consumer: str = "consumer-1",
    start_tick: int = 6000,
    end_tick: int = 6060,
    start_count: int = 1000,
    end_count: int = 1010,
    receipt_id: str = "r-measure",
    sequence: int = 4,
) -> ConstructionReceipt:
    return _receipt(
        receipt_id, sequence,
        ThroughputEvidence(
            evidence_id=evidence_id,
            binding=binding or _binding(),
            output=output,
            unit=unit,
            consumer=consumer,
            start_tick=start_tick,
            end_tick=end_tick,
            start_count=start_count,
            end_count=end_count,
        ),
    )


def _built_state(plan: ConstructionPlan):
    return reduce_construction(
        plan,
        (
            _order(sequence=1),
            _fund({"transport-belt": 2, "inserter": 1}, sequence=2),
            _observe((_belt(), _inserter()), complete=True, sequence=3),
        ),
    )


def _raises(reason: ConstructionReason, plan: ConstructionPlan, state, receipt) -> None:
    with pytest.raises(ConstructionStateError) as caught:
        apply_construction_receipt(plan, state, receipt)
    assert caught.value.reason is reason
    assert caught.value.code == reason.value


# --- replay and idempotence -------------------------------------------------


def test_full_replay_is_deterministic_and_duplicate_receipt_is_idempotent() -> None:
    plan = _plan()
    receipts = (
        _order(sequence=1),
        _fund({"transport-belt": 2, "inserter": 1}, sequence=2),
        _observe((_belt(),), complete=False, receipt_id="r-part", sequence=3),
        _observe((_belt(), _inserter()), complete=True, receipt_id="r-done", sequence=4),
        _throughput(end_count=1012, sequence=5),
    )

    first = reduce_construction(plan, receipts)
    second = reduce_construction(plan, receipts)

    assert first == second
    assert first.phase is ConstructionPhase.COMMISSIONED
    assert [receipt.receipt_id for receipt in first.receipts] == [
        "r-order", "r-fund", "r-part", "r-done", "r-measure",
    ]

    replayed = apply_construction_receipt(plan, first, receipts[-1])
    assert replayed == first
    assert replayed.last_sequence == 5


def test_duplicate_receipt_ids_conflict_instead_of_overwriting() -> None:
    plan = _plan()
    state = apply_construction_receipt(plan, initial_construction_state(plan), _order())

    same_id_different_action = _receipt(
        "r-order", 1, OrderAction((_belt(),)),
    )
    _raises(
        ConstructionReason.DUPLICATE_RECEIPT_CONFLICT,
        plan, state, same_id_different_action,
    )

    same_id_later_sequence = _receipt(
        "r-order", 2, OrderAction((_belt(), _inserter())),
    )
    _raises(
        ConstructionReason.DUPLICATE_RECEIPT_CONFLICT,
        plan, state, same_id_later_sequence,
    )


@pytest.mark.parametrize("sequence", (1, 2))
def test_decreasing_or_repeated_sequence_is_rejected(sequence: int) -> None:
    plan = _plan()
    state = reduce_construction(
        plan, (_order(sequence=1), _receipt("r-second", 2, FundingAction({"inserter": 1}))),
    )
    _raises(
        ConstructionReason.SEQUENCE_DECREASING,
        plan, state,
        _receipt("r-third", sequence, FundingAction({"transport-belt": 1})),
    )


# --- scope and immutable plan binding --------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("instance_id", "other-instance", ConstructionReason.SCOPE_MISMATCH),
        ("manifest_digest", "c" * 64, ConstructionReason.SCOPE_MISMATCH),
        ("surface", "training/other-surface", ConstructionReason.SCOPE_MISMATCH),
        ("force", "other-force", ConstructionReason.SCOPE_MISMATCH),
        ("project_id", "other-project", ConstructionReason.PROJECT_MISMATCH),
        ("transaction_id", "other-transaction", ConstructionReason.TRANSACTION_MISMATCH),
        ("plan_hash", "d" * 64, ConstructionReason.PLAN_HASH_MISMATCH),
    ),
)
def test_receipts_must_match_explicit_scope_and_plan(
    field: str, value: str, reason: ConstructionReason,
) -> None:
    plan = _plan()
    wrong = dict(
        scope=_scope(), project_id="project-1",
        transaction_id="transaction-1", plan_hash=_DIGEST_B,
    )
    if field in {"instance_id", "manifest_digest", "surface", "force"}:
        scope_values = {
            "instance_id": "training-instance-a",
            "manifest_digest": _DIGEST_A,
            "surface": "training/synthetic-slice",
            "force": "training-synthetic-force",
        }
        scope_values[field] = value
        wrong["scope"] = _scope(**scope_values)
    else:
        wrong[field] = value
    receipt = _receipt(
        "r-wrong", 1, OrderAction((_belt(),)), binding=_binding(**wrong),
    )
    _raises(reason, plan, initial_construction_state(plan), receipt)


def test_invalid_scope_and_plan_identities_fail_closed() -> None:
    with pytest.raises(ConstructionStateError) as caught:
        _scope(manifest_digest="not-a-digest")
    assert caught.value.reason is ConstructionReason.INVALID_SCOPE

    with pytest.raises(ConstructionStateError) as caught:
        _binding(plan_hash="not-a-plan-hash")
    assert caught.value.reason is ConstructionReason.INVALID_PLAN

    with pytest.raises(ConstructionStateError) as caught:
        ConstructionPlan(
            binding=_binding(),
            required_entities=(),
            required_materials={},
            throughput_output="output",
            throughput_target=Fraction(1),
            throughput_unit="items/tick",
        )
    assert caught.value.reason is ConstructionReason.INVALID_PLAN


# --- order vs observed construction ---------------------------------------


def test_order_is_not_construction_and_never_releases_reservations() -> None:
    plan = _plan()
    state = reduce_construction(
        plan,
        (
            _order(sequence=1),
            _fund({"transport-belt": 2, "inserter": 1}, sequence=2),
        ),
    )

    assert state.phase is ConstructionPhase.WAIT
    assert state.order_acknowledged is True
    assert state.observed_entities == ()
    assert state.construction_observed is False
    assert state.facts()["order_is_completion"] is False
    assert dict(state.held_reservations) == {"transport-belt": 2, "inserter": 1}
    assert dict(state.unresolved_funding) == {}

    _raises(
        ConstructionReason.PREMATURE_RELEASE,
        plan, state,
        _receipt("r-release", 3, ReleaseAction({"inserter": 1})),
    )


def test_unowned_observation_never_credits_existing_entities() -> None:
    plan = _plan()
    state = reduce_construction(
        plan,
        (_observe((_belt(), _inserter()), complete=True, owned=False, sequence=1),),
    )

    assert state.phase is ConstructionPhase.WAIT
    assert state.observed_entities == ()
    assert state.construction_observed is False
    assert set(state.unverified_observed_entities) == {_belt(), _inserter()}
    assert any("transaction ownership" in note for note in state.notes)


def test_order_entity_outside_the_plan_is_rejected() -> None:
    plan = _plan()
    receipt = _receipt(
        "r-unknown", 1, OrderAction((EntityRef("assembling-machine-1", 5, 5),)),
    )
    _raises(
        ConstructionReason.UNKNOWN_ENTITY,
        plan, initial_construction_state(plan), receipt,
    )


def test_missing_entities_leave_partial_construction() -> None:
    plan = _plan()
    state = reduce_construction(
        plan,
        (
            _order(sequence=1),
            _observe((_belt(),), complete=True, receipt_id="r-short", sequence=2),
        ),
    )

    assert state.phase is ConstructionPhase.PARTIAL
    assert state.construction_observed is False
    assert state.observed_entities == (_belt(),)
    assert any("missing required entities" in note for note in state.notes)


def test_partial_construction_resumes_to_built_without_releasing_funding() -> None:
    plan = _plan()
    head = (
        _order(sequence=1),
        _fund({"transport-belt": 2, "inserter": 1}, sequence=2),
        _observe((_belt(),), complete=False, receipt_id="r-first", sequence=3),
    )
    partial = reduce_construction(plan, head)
    assert partial.phase is ConstructionPhase.PARTIAL
    assert dict(partial.held_reservations) == {"transport-belt": 2, "inserter": 1}

    tail = (_observe((_belt(), _inserter()), complete=True, receipt_id="r-second", sequence=4),)
    resumed = resume_construction(plan, partial, tail)
    replay = reduce_construction(plan, head + tail)

    assert resumed == replay
    assert resumed.phase is ConstructionPhase.BUILT
    assert set(resumed.observed_entities) == {_belt(), _inserter()}
    assert dict(resumed.held_reservations) == {"transport-belt": 2, "inserter": 1}


def test_observation_regressing_a_built_transaction_is_rejected() -> None:
    plan = _plan()
    built = _built_state(plan)
    _raises(
        ConstructionReason.COMPLETION_REGRESSION,
        plan, built,
        _observe((_belt(),), complete=True, receipt_id="r-regress", sequence=4),
    )


# --- commissioning from qualified throughput windows ----------------------


def test_commission_requires_observed_completion() -> None:
    plan = _plan()
    state = reduce_construction(plan, (_order(sequence=1),))
    _raises(
        ConstructionReason.PREMATURE_COMMISSION,
        plan, state,
        _throughput(sequence=2),
    )


def test_commission_output_and_unit_must_match_the_plan() -> None:
    plan = _plan()
    built = _built_state(plan)
    _raises(
        ConstructionReason.MEASUREMENT_MISMATCH,
        plan, built,
        _throughput(output="copper-plate", sequence=4),
    )
    _raises(
        ConstructionReason.MEASUREMENT_MISMATCH,
        plan, built,
        _throughput(unit="items/second", receipt_id="r-unit", sequence=4),
    )


@pytest.mark.parametrize("observed_recipe", [None, "basic-oil-processing"])
def test_machine_recipe_must_match_for_construction_observation(observed_recipe) -> None:
    machine = EntityRef("oil-refinery", 0, 0, recipe="advanced-oil-processing")
    plan = ConstructionPlan(
        binding=_binding(), required_entities=(machine,),
        required_materials={"oil-refinery": 1}, throughput_output="petroleum-gas",
        throughput_target=1, throughput_unit=FLUID_PER_SECOND,
    )
    incorrect = replace(machine, recipe=observed_recipe)
    receipt = _receipt("observe", 1, ObservationAction((incorrect,), complete=True,
                                                        transaction_owned=True))
    unmatched = apply_construction_receipt(plan, initial_construction_state(plan), receipt)
    assert unmatched.phase is ConstructionPhase.WAIT
    assert unmatched.foreign_observed_entities == (incorrect,)
    valid = _receipt("valid", 1, ObservationAction((machine,), complete=True,
                                                     transaction_owned=True))
    assert apply_construction_receipt(plan, initial_construction_state(plan), valid).phase is ConstructionPhase.BUILT


@pytest.mark.parametrize("recipe", ["", False, 42])
def test_invalid_machine_recipe_is_rejected(recipe) -> None:
    with pytest.raises(ConstructionStateError):
        EntityRef("oil-refinery", 0, 0, recipe=recipe)


def test_throughput_rate_is_the_exact_counter_advance_over_elapsed_ticks() -> None:
    plan = _plan()
    state = apply_construction_receipt(
        plan, _built_state(plan), _throughput(sequence=4),
    )

    assert state.phase is ConstructionPhase.COMMISSIONED
    fact = state.throughput_fact
    assert fact is not None
    assert fact.output == "iron-gear-wheel"
    assert fact.consumer == "consumer-1"
    assert fact.start_tick == 6000
    assert fact.end_tick == 6060
    assert fact.elapsed_ticks == 60
    assert fact.counter_advance == 10
    assert fact.rate == Fraction(1, 6)
    assert isinstance(fact.rate, Fraction)
    assert state.facts()["throughput_rate"] == "1/6"


@pytest.mark.parametrize(
    "unit", ("items", "items/minute", "", "items/second/consumer", 60),
)
def test_plan_rejects_unsupported_throughput_units(unit: object) -> None:
    with pytest.raises(ConstructionStateError) as caught:
        _plan(throughput_unit=unit)  # type: ignore[arg-type]
    assert caught.value.reason is ConstructionReason.INVALID_PLAN


@pytest.mark.parametrize(
    "unit", ("items", "items/minute", "", "items/second/consumer", 60),
)
def test_throughput_evidence_rejects_unsupported_units(unit: object) -> None:
    with pytest.raises(ConstructionStateError) as caught:
        _throughput(unit=unit)  # type: ignore[arg-type]
    assert caught.value.reason is ConstructionReason.INVALID_RECEIPT


def test_sixty_items_over_sixty_ticks_is_exactly_one_item_per_tick() -> None:
    plan = _plan(throughput_target=Fraction(1), throughput_unit=ITEMS_PER_TICK)
    built = _built_state(plan)

    below = apply_construction_receipt(
        plan, built,
        _throughput(
            unit=ITEMS_PER_TICK,
            start_tick=0, end_tick=60,
            start_count=0, end_count=59,
            sequence=4,
        ),
    )
    assert below.phase is ConstructionPhase.BUILT
    assert below.throughput_fact is not None
    assert below.throughput_fact.rate == Fraction(59, 60)
    assert below.facts()["commission_met"] is False

    met = apply_construction_receipt(
        plan, below,
        _throughput(
            evidence_id="evidence-2",
            unit=ITEMS_PER_TICK,
            start_tick=0, end_tick=60,
            start_count=0, end_count=60,
            receipt_id="r-measure-2",
            sequence=5,
        ),
    )
    assert met.phase is ConstructionPhase.COMMISSIONED
    assert met.throughput_fact is not None
    assert met.throughput_fact.rate == Fraction(1)
    assert met.facts()["throughput_rate"] == "1"


def test_sixty_items_over_sixty_ticks_is_exactly_sixty_items_per_second() -> None:
    plan = _plan(
        throughput_target=Fraction(60), throughput_unit=ITEMS_PER_SECOND,
    )
    built = _built_state(plan)

    below = apply_construction_receipt(
        plan, built,
        _throughput(
            unit=ITEMS_PER_SECOND,
            start_tick=0, end_tick=60,
            start_count=0, end_count=59,
            sequence=4,
        ),
    )
    assert below.phase is ConstructionPhase.BUILT
    assert below.throughput_fact is not None
    assert below.throughput_fact.rate == Fraction(59)
    assert below.facts()["commission_met"] is False

    met = apply_construction_receipt(
        plan, below,
        _throughput(
            evidence_id="evidence-2",
            unit=ITEMS_PER_SECOND,
            start_tick=0, end_tick=60,
            start_count=0, end_count=60,
            receipt_id="r-measure-2",
            sequence=5,
        ),
    )
    assert met.phase is ConstructionPhase.COMMISSIONED
    assert met.throughput_fact is not None
    assert met.throughput_fact.unit == ITEMS_PER_SECOND
    assert met.throughput_fact.rate == Fraction(60)
    assert met.facts()["throughput_rate"] == "60"


@pytest.mark.parametrize("unit, target", [(FLUID_PER_TICK, Fraction(13, 12)),
                                          (FLUID_PER_SECOND, Fraction(65))])
def test_fluid_counter_commission_requires_matching_exact_unit(unit, target) -> None:
    plan = _plan(throughput_output="petroleum-gas", throughput_target=target,
                 throughput_unit=unit)
    built = _built_state(plan)
    _raises(ConstructionReason.MEASUREMENT_MISMATCH, plan, built,
            _throughput(output="petroleum-gas", unit=ITEMS_PER_SECOND, sequence=4))
    commissioned = apply_construction_receipt(
        plan, built, _throughput(output="petroleum-gas", unit=unit, start_tick=10,
                                 end_tick=70, start_count=100, end_count=165),
    )
    assert commissioned.phase is ConstructionPhase.COMMISSIONED
    assert commissioned.throughput_fact.rate == target


def test_fluid_counters_do_not_silently_round_fractional_observations() -> None:
    with pytest.raises(ConstructionStateError) as error:
        _throughput(unit=FLUID_PER_SECOND, start_count=0, end_count=Fraction(3, 2))
    assert error.value.reason is ConstructionReason.COUNTER_NOT_ADVANCING


def test_per_second_conversion_is_exact_for_a_sub_second_window() -> None:
    second_plan = _plan(
        throughput_target=Fraction(1), throughput_unit=ITEMS_PER_SECOND,
    )
    per_second = apply_construction_receipt(
        second_plan, _built_state(second_plan),
        _throughput(
            unit=ITEMS_PER_SECOND,
            start_tick=0, end_tick=60,
            start_count=0, end_count=1,
            sequence=4,
        ),
    )
    assert per_second.phase is ConstructionPhase.COMMISSIONED
    assert per_second.throughput_fact is not None
    assert per_second.throughput_fact.rate == Fraction(1)

    tick_plan = _plan(
        throughput_target=Fraction(1, 60), throughput_unit=ITEMS_PER_TICK,
    )
    per_tick = apply_construction_receipt(
        tick_plan, _built_state(tick_plan),
        _throughput(
            unit=ITEMS_PER_TICK,
            start_tick=0, end_tick=60,
            start_count=0, end_count=1,
            sequence=4,
        ),
    )
    assert per_tick.phase is ConstructionPhase.COMMISSIONED
    assert per_tick.throughput_fact is not None
    assert per_tick.throughput_fact.rate == Fraction(1, 60)


def test_below_target_window_is_a_production_fact_without_commissioning() -> None:
    plan = _plan()
    state = apply_construction_receipt(
        plan, _built_state(plan),
        _throughput(end_count=1009, sequence=4),
    )

    assert state.phase is ConstructionPhase.BUILT
    assert state.throughput_fact is not None
    assert state.throughput_fact.rate == Fraction(9, 60)
    assert state.facts()["measured_production"] is True
    assert state.facts()["commission_met"] is False
    assert any("below the plan target" in note for note in state.notes)

    commissioned = apply_construction_receipt(
        plan, state,
        _throughput(
            evidence_id="evidence-2",
            receipt_id="r-measure-2",
            sequence=5,
        ),
    )
    assert commissioned.phase is ConstructionPhase.COMMISSIONED
    assert commissioned.facts()["commission_met"] is True


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"end_tick": 6000}, ConstructionReason.INVALID_INTERVAL),
        ({"end_tick": 5999}, ConstructionReason.INVALID_INTERVAL),
        ({"start_tick": -1}, ConstructionReason.INVALID_INTERVAL),
        ({"end_count": 1000}, ConstructionReason.COUNTER_NOT_ADVANCING),
        ({"end_count": 999}, ConstructionReason.COUNTER_NOT_ADVANCING),
        ({"start_count": -1}, ConstructionReason.COUNTER_NOT_ADVANCING),
    ),
)
def test_throughput_window_rejects_invalid_intervals_and_stalled_counters(
    kwargs: dict[str, int], reason: ConstructionReason,
) -> None:
    with pytest.raises(ConstructionStateError) as caught:
        _throughput(**kwargs)
    assert caught.value.reason is reason
    assert caught.value.code == reason.value


def test_arbitrary_scalar_stock_or_presence_action_is_not_a_receipt() -> None:
    for proxy in (Fraction(100), 100, {"stock": 100}, True, "present"):
        with pytest.raises(ConstructionStateError) as caught:
            _receipt("r-proxy", 4, proxy)
        assert caught.value.reason is ConstructionReason.INVALID_RECEIPT


def test_throughput_evidence_must_bind_to_the_plan_scope_and_transaction() -> None:
    plan = _plan()
    built = _built_state(plan)

    _raises(
        ConstructionReason.SCOPE_MISMATCH,
        plan, built,
        _throughput(
            binding=_binding(scope=_scope(instance_id="other-instance")),
            sequence=4,
        ),
    )
    _raises(
        ConstructionReason.TRANSACTION_MISMATCH,
        plan, built,
        _throughput(
            binding=_binding(transaction_id="transaction-2"),
            receipt_id="r-other-tx",
            sequence=4,
        ),
    )
    _raises(
        ConstructionReason.PLAN_HASH_MISMATCH,
        plan, built,
        _throughput(
            binding=_binding(plan_hash="e" * 64),
            receipt_id="r-other-plan",
            sequence=4,
        ),
    )


def test_commission_and_failure_are_mutually_terminal() -> None:
    plan = _plan()
    commissioned = apply_construction_receipt(
        plan, _built_state(plan),
        _throughput(end_count=1011, sequence=4),
    )
    assert commissioned.phase is ConstructionPhase.COMMISSIONED

    _raises(
        ConstructionReason.FAILURE_AFTER_COMMISSION,
        plan, commissioned,
        _receipt("r-fail", 5, FailureAction("late-failure")),
    )
    _raises(
        ConstructionReason.ALREADY_COMMISSIONED,
        plan, commissioned,
        _throughput(
            evidence_id="evidence-late",
            end_count=1099,
            receipt_id="r-measure-2",
            sequence=5,
        ),
    )


# --- funding, release and failure -----------------------------------------


def test_funding_is_explicit_and_bounded_by_the_plan_bill() -> None:
    plan = _plan()
    state = reduce_construction(
        plan, (_fund({"transport-belt": 1}, sequence=1),),
    )
    assert dict(state.unresolved_funding) == {"transport-belt": 1, "inserter": 1}
    assert dict(state.held_reservations) == {"transport-belt": 1}

    _raises(
        ConstructionReason.FUNDING_OUTSIDE_PLAN,
        plan, state,
        _fund({"iron-plate": 1}, receipt_id="r-outside", sequence=2),
    )
    _raises(
        ConstructionReason.FUNDING_EXCEEDS_REQUIRED,
        plan, state,
        _fund({"transport-belt": 2}, receipt_id="r-over", sequence=2),
    )


def test_release_is_explicit_and_only_after_a_terminal_outcome() -> None:
    plan = _plan()
    commissioned = apply_construction_receipt(
        plan, _built_state(plan), _throughput(sequence=4),
    )
    _raises(
        ConstructionReason.RELEASE_EXCEEDS_FUNDING,
        plan, commissioned,
        _receipt("r-over-release", 5, ReleaseAction({"inserter": 2})),
    )

    released = apply_construction_receipt(
        plan, commissioned,
        _receipt("r-release", 5, ReleaseAction({"inserter": 1})),
    )
    assert dict(released.held_reservations) == {"transport-belt": 2}
    assert dict(released.released) == {"inserter": 1}
    assert released.phase is ConstructionPhase.COMMISSIONED


def test_failed_construction_releases_nothing_implicitly_and_accepts_no_evidence() -> None:
    plan = _plan()
    failed = reduce_construction(
        plan,
        (
            _order(sequence=1),
            _fund({"transport-belt": 2}, sequence=2),
            _receipt("r-fail", 3, FailureAction("bot-starvation", "synthetic")),
        ),
    )
    assert failed.phase is ConstructionPhase.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "bot-starvation"
    assert dict(failed.held_reservations) == {"transport-belt": 2}

    _raises(
        ConstructionReason.ALREADY_FAILED,
        plan, failed,
        _fund({"inserter": 1}, receipt_id="r-late-fund", sequence=4),
    )

    released = apply_construction_receipt(
        plan, failed,
        _receipt("r-release", 4, ReleaseAction({"transport-belt": 2})),
    )
    assert dict(released.held_reservations) == {}


def test_state_serialization_keeps_order_completion_and_production_distinct() -> None:
    plan = _plan()
    partial = reduce_construction(plan, (_order(sequence=1),))
    payload = partial.to_dict()

    assert payload["phase"] == "wait"
    assert payload["facts"]["order_acknowledged"] is True
    assert payload["facts"]["order_is_completion"] is False
    assert payload["facts"]["observed_completion"] is False
    assert payload["facts"]["measured_production"] is False
    assert payload["unresolved_funding"] == {"transport-belt": 2, "inserter": 1}
    assert payload["binding"]["scope"]["manifest_digest"] == _DIGEST_A
    assert payload["binding"]["plan_hash"] == _DIGEST_B


def test_prior_state_cannot_be_reused_with_a_different_plan_sharing_the_binding() -> None:
    plan = _plan()
    state = reduce_construction(plan, (_order(sequence=1),))
    other_plan = ConstructionPlan(
        binding=_binding(),
        required_entities=(EntityRef("transport-belt", 9, 9),),
        required_materials={"transport-belt": 1},
        throughput_output="iron-gear-wheel",
        throughput_target=Fraction(1, 6),
        throughput_unit="items/tick",
    )

    _raises(
        ConstructionReason.INVALID_STATE,
        other_plan, state,
        _observe((EntityRef("transport-belt", 9, 9),), complete=True, sequence=2),
    )


def test_receipt_sequence_must_be_positive() -> None:
    plan = _plan()
    with pytest.raises(ConstructionStateError) as caught:
        _receipt("r-zero", 0, OrderAction((_belt(),)))
    assert caught.value.reason is ConstructionReason.INVALID_RECEIPT

    state = apply_construction_receipt(
        plan, initial_construction_state(plan), _order(sequence=1),
    )
    with pytest.raises(ConstructionStateError) as caught:
        apply_construction_receipt(
            plan, state,
            _receipt("r-negative", -1, FundingAction({"inserter": 1})),
        )
    assert caught.value.reason is ConstructionReason.INVALID_RECEIPT


def test_pre_existing_entities_are_reported_but_never_credited() -> None:
    plan = _plan()
    pre_existing = EntityRef("stone-furnace", 9, 9)
    state = reduce_construction(
        plan,
        (
            _order(sequence=1),
            _observe(
                (_belt(), _inserter(), pre_existing),
                complete=True, receipt_id="r-with-foreign", sequence=2,
            ),
        ),
    )

    assert state.phase is ConstructionPhase.BUILT
    assert set(state.observed_entities) == {_belt(), _inserter()}
    assert state.foreign_observed_entities == (pre_existing,)
    assert pre_existing not in state.required_entities


# --- prior state must replay from its receipt history ---------------------


@pytest.mark.parametrize(
    "phase", (ConstructionPhase.BUILT, ConstructionPhase.COMMISSIONED),
)
def test_prior_state_cannot_forge_construction_phase(phase: ConstructionPhase) -> None:
    plan = _plan()
    forged = replace(initial_construction_state(plan), phase=phase)
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _observe((_belt(), _inserter()), complete=True, sequence=1),
    )


def test_prior_state_cannot_forge_commissioning_on_a_built_replay() -> None:
    plan = _plan()
    forged = replace(_built_state(plan), phase=ConstructionPhase.COMMISSIONED)
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _receipt("r-release", 4, ReleaseAction({"inserter": 1})),
    )


def test_prior_state_cannot_forge_funding() -> None:
    plan = _plan()
    ordered = reduce_construction(plan, (_order(sequence=1),))
    forged = replace(ordered, funded=(("transport-belt", 2), ("inserter", 1)))
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _fund({"inserter": 1}, receipt_id="r-fund", sequence=2),
    )


def test_prior_state_cannot_forge_observations() -> None:
    plan = _plan()
    forged = replace(
        reduce_construction(plan, (_order(sequence=1),)),
        phase=ConstructionPhase.BUILT,
        observed_entities=plan.required_entities,
        observation_complete=True,
    )
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _observe((_belt(), _inserter()), complete=True, sequence=2),
    )


def test_prior_state_cannot_forge_an_observed_entity_subset() -> None:
    plan = _plan()
    forged = replace(_built_state(plan), observed_entities=(_belt(),))
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _observe((_belt(), _inserter()), complete=True, receipt_id="r-again", sequence=4),
    )


def test_prior_state_cannot_forge_sequence() -> None:
    plan = _plan()
    forged = replace(
        reduce_construction(plan, (_order(sequence=1),)), last_sequence=7,
    )
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _fund({"inserter": 1}, receipt_id="r-fund", sequence=2),
    )


def test_prior_state_cannot_drop_receipt_history() -> None:
    plan = _plan()
    forged = replace(_built_state(plan), receipts=())
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _observe((_belt(), _inserter()), complete=True, sequence=4),
    )


def test_prior_state_cannot_fabricate_extra_receipt_history() -> None:
    plan = _plan()
    built = _built_state(plan)
    extra = _observe(
        (_belt(), _inserter()), complete=True, receipt_id="r-extra", sequence=4,
    )
    forged = replace(built, receipts=built.receipts + (extra,))
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _fund({"inserter": 1}, receipt_id="r-later", sequence=5),
    )


def test_prior_state_cannot_reorder_receipt_history() -> None:
    plan = _plan()
    built = _built_state(plan)
    forged = replace(
        built,
        receipts=(built.receipts[1], built.receipts[0], built.receipts[2]),
    )
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _fund({"inserter": 1}, receipt_id="r-later", sequence=4),
    )


def test_prior_state_cannot_embed_foreign_receipt_history() -> None:
    plan = _plan()
    built = _built_state(plan)
    foreign = _receipt(
        "r-foreign", 4, OrderAction((_belt(),)),
        binding=_binding(transaction_id="transaction-2"),
    )
    forged = replace(built, receipts=built.receipts + (foreign,))
    _raises(
        ConstructionReason.INVALID_STATE, plan, forged,
        _fund({"inserter": 1}, receipt_id="r-later", sequence=5),
    )


def test_replay_preserves_caller_provenance_non_claims() -> None:
    plan = _plan()
    state = _built_state(plan)
    assert state.notes[: len(NON_CLAIMS)] == NON_CLAIMS
    _raises(
        ConstructionReason.INVALID_STATE, plan, replace(state, notes=()),
        _observe((_belt(), _inserter()), complete=True, receipt_id="r-extra", sequence=4),
    )
