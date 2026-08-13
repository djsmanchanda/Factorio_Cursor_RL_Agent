# Path: tests/test_alternating_scheduler.py
# Purpose: Prove ABAB policy cohorts reuse released training slots safely.

from __future__ import annotations

import pytest

from training.alternating import AlternatingFamily, AlternatingPolicyScheduler


def families() -> tuple[AlternatingFamily, AlternatingFamily]:
    return (
        AlternatingFamily("ore-production", ({"scenario_id": "ore-1"},)),
        AlternatingFamily("furnace-refinery", ({"scenario_id": "refinery-1"},)),
    )


def test_each_family_cohort_has_the_requested_terminal_attempt_count() -> None:
    scheduler = AlternatingPolicyScheduler(families(), episodes_per_policy=5)
    first = scheduler.claim(["w1", "w2", "w3", "w4", "w5"])
    assert len(first) == 5
    assert {job.family for job in first} == {"ore-production"}
    for job in first:
        scheduler.complete(job.worker_id)
    second = scheduler.claim(["w1", "w2", "w3", "w4", "w5"])
    assert len(second) == 5
    assert {job.family for job in second} == {"furnace-refinery"}
    assert {job.policy_index for job in second} == {0}


def test_next_family_uses_slots_as_soon_as_previous_queue_is_assigned() -> None:
    scheduler = AlternatingPolicyScheduler(families(), episodes_per_policy=2)
    first = scheduler.claim(["w1", "w2"])
    assert {job.family for job in first} == {"ore-production"}
    # A is still running, but its queue is exhausted.  The first released slot
    # is immediately reused by B rather than waiting for all A jobs to drain.
    scheduler.complete("w1")
    next_job = scheduler.claim(["w1"])
    assert len(next_job) == 1
    assert next_job[0].family == "furnace-refinery"
    assert scheduler.status().active == 2


def test_policy_index_increments_after_a_full_ab_pair() -> None:
    scheduler = AlternatingPolicyScheduler(families(), episodes_per_policy=1)
    for worker in ("w1", "w2"):
        job = scheduler.claim([worker])[0]
        scheduler.complete(worker)
    assert scheduler.family == "ore-production"
    assert scheduler.policy_index == 1


def test_duplicate_or_busy_workers_do_not_receive_two_jobs() -> None:
    scheduler = AlternatingPolicyScheduler(families(), episodes_per_policy=2)
    assigned = scheduler.claim(["w1", "w1", "w2"])
    assert [job.worker_id for job in assigned] == ["w1", "w2"]
    assert scheduler.claim(["w1"]) == ()


@pytest.mark.parametrize("bad", [0, -1])
def test_episode_target_must_be_positive(bad: int) -> None:
    with pytest.raises(ValueError, match="episodes_per_policy"):
        AlternatingPolicyScheduler(families(), episodes_per_policy=bad)

