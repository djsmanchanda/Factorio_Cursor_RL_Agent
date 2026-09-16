# Path: tests/test_deterministic_fleet.py | Purpose: Verify deterministic fleet scheduling and evidence ranking.

from tools.deterministic_fleet import (
    CheckpointTimeline,
    FleetScheduler,
    LaneKind,
    LaneStatus,
    SuiteInputSnapshot,
    TimingHistory,
    last_frontier_results,
    rank_commits,
    summarize_commit,
)


def snapshot(suite_id: str, commit: str, *, order: int = 1, frontier: str = "C4") -> SuiteInputSnapshot:
    return SuiteInputSnapshot(
        suite_id=suite_id,
        commit_sha=commit,
        registry_version="timeline-v1",
        mission_id="science-v1",
        factorio_version="2.0",
        checkpoint_generations={f"C{i}": f"save-{suite_id}-C{i}" for i in range(5)},
        frontier_checkpoint=frontier,
        enqueue_order=order,
    )


def start_all(scheduler: FleetScheduler, count: int) -> list:
    started = []
    for _ in range(count):
        lane = scheduler.next_to_start()
        assert lane is not None
        started.append(lane)
    return started


def test_suite_pins_inputs_and_newest_commit_gets_c0_and_frontier_first():
    timeline = CheckpointTimeline(("C0", "C1", "C2", "C3", "C4"))
    scheduler = FleetScheduler(timeline)
    old = snapshot("old", "11111111old", order=1)
    new = snapshot("new", "22222222new", order=2)
    scheduler.enqueue_suite(old)
    scheduler.enqueue_suite(new)

    first, second = start_all(scheduler, 2)
    assert (first.snapshot.suite_id, first.origin_checkpoint) == ("new", "C0")
    assert (second.snapshot.suite_id, second.origin_checkpoint) == ("new", "C4")
    assert old.checkpoint_generations["C0"] == "save-old-C0"
    assert new.checkpoint_generations["C0"] == "save-new-C0"


def test_older_queue_precedes_newest_middle_until_anti_stall_reservation():
    timeline = CheckpointTimeline(("C0", "C1", "C2", "C3", "C4"))
    scheduler = FleetScheduler(timeline)
    old = snapshot("old", "oldsha", order=1)
    new = snapshot("new", "newsha", order=2)
    scheduler.enqueue_suite(old)
    scheduler.enqueue_suite(new)
    start_all(scheduler, 2)  # newest C0/C4

    # The first old queued lane gets served before newest middle work.
    assert scheduler.next_to_start().snapshot.suite_id == "old"
    started = []
    for _ in range(4):
        started.append(scheduler.next_to_start())
    # Five ordinary starts since the last newest-middle dispatch reserve one.
    assert any(lane.snapshot.suite_id == "new" and lane.kind is LaneKind.MIDDLE for lane in started)


def test_verification_waiting_five_slot_opportunities_is_promoted():
    timeline = CheckpointTimeline(("C0", "C1", "C2"))
    scheduler = FleetScheduler(timeline)
    suite = snapshot("suite", "abcdef012345", order=1, frontier="C2")
    verification = scheduler.enqueue_verification(suite, "C1")
    for index in range(5):
        newer = snapshot(
            f"newer-{index}", f"{index + 1:08x}", order=index + 2,
            frontier="C2",
        )
        scheduler.enqueue_lane(newer, "C0", kind=LaneKind.ENDPOINT)
        selected = scheduler.next_to_start()
        assert selected is not None
        scheduler.complete_lane(selected.lane_id)
    assert verification.waiting_opportunities >= 5
    latest = snapshot("latest", "99999999", order=99, frontier="C2")
    scheduler.enqueue_lane(latest, "C0", kind=LaneKind.ENDPOINT)
    selected = scheduler.next_to_start()
    assert selected is verification


def test_unaged_verification_follows_newest_endpoints_before_old_backlog():
    timeline = CheckpointTimeline(("C0", "C1", "C2"))
    scheduler = FleetScheduler(timeline)
    old = snapshot("old", "11111111", order=1, frontier="C2")
    new = snapshot("new", "22222222", order=2, frontier="C2")
    scheduler.enqueue_suite(old)
    scheduler.enqueue_suite(new)
    verification = scheduler.enqueue_verification(old, "C1")
    assert scheduler.next_to_start().origin_checkpoint == "C0"
    assert scheduler.next_to_start().origin_checkpoint == "C2"
    assert scheduler.next_to_start() is verification


def test_eight_active_cap_and_only_double_ticked_middle_is_reclaimable():
    timeline = CheckpointTimeline(("C0", "C1", "C2", "C3"))
    scheduler = FleetScheduler(timeline, max_active=8)
    suite = snapshot("suite", "sha", order=1, frontier="C3")
    lanes = scheduler.enqueue_suite(suite)
    for index in range(2, 6):
        scheduler.enqueue_lane(
            snapshot(f"suite-{index}", f"sha-{index}", order=index, frontier="C3"),
            "C1",
        )
    started = start_all(scheduler, 8)
    assert len(started) == 8
    assert scheduler.next_to_start() is None
    middle = next(lane for lane in started if lane.kind is LaneKind.MIDDLE)
    assert not middle.reclaimable
    scheduler.record_checkpoint(middle.lane_id, "C2")
    assert middle.reclaimable is False
    scheduler.record_checkpoint(middle.lane_id, "C3")
    assert middle.reclaimable is True
    assert all(lane.kind is not LaneKind.ENDPOINT for lane in scheduler.reclaimable_lanes()) or middle in scheduler.reclaimable_lanes()
    assert scheduler.release_reclaimable(middle.lane_id).status is LaneStatus.PREEMPTED


def test_progress_and_later_failure_preserve_ticks_and_last_log_lines():
    timeline = CheckpointTimeline(("C0", "C1", "C2", "C3"))
    scheduler = FleetScheduler(timeline)
    lane = scheduler.enqueue_lane(
        snapshot("suite", "deadbeef", order=1, frontier="C3"), "C1"
    )
    assert scheduler.next_to_start() is lane
    scheduler.record_checkpoint(lane.lane_id, "C2")
    scheduler.record_checkpoint(lane.lane_id, "C3", slow_warning=True, warning_reason="slow")
    result = scheduler.record_failure(
        lane.lane_id,
        "power stalled",
        failure_checkpoint="C3",
        log_lines=(f"line-{i}" for i in range(150)),
    )
    assert result.status is LaneStatus.FAILED
    assert result.progress.passed_checkpoints == ("C2", "C3")
    assert result.progress.tick_count == 2
    assert result.progress.slow_warning is True
    assert result.progress.log_tail == tuple(f"line-{i}" for i in range(50, 150))


def test_failure_outcomes_keep_infrastructure_distinct_from_regression():
    timeline = CheckpointTimeline(("C0", "C1"))
    scheduler = FleetScheduler(timeline)
    lane = scheduler.enqueue_lane(snapshot("suite", "deadbeef", frontier="C1"), "C0")
    scheduler.next_to_start()
    result = scheduler.record_failure(
        lane.lane_id, "port collision", functional=False,
        outcome=LaneStatus.INFRASTRUCTURE_ERROR,
    )
    assert result.status is LaneStatus.INFRASTRUCTURE_ERROR
    assert result.progress.functional_failure is False


def test_timing_history_uses_latest_twenty_and_2_5x_after_three_samples():
    history = TimingHistory()
    for value in range(1, 24):
        history.record_success("pred-v1", "2.0", value)
    assert len(history.samples("pred-v1", "2.0")) == 20
    assert history.samples("pred-v1", "2.0")[0] == 4
    assessment = history.assess("pred-v1", "2.0", 100, work_waiting=True)
    assert assessment.sample_count == 20
    assert assessment.warning is True
    assert assessment.stop_if_waiting is True
    fresh = TimingHistory()
    assert fresh.assess("pred-v1", "2.0", 100).warning is False


def test_stability_first_ranking_beats_faster_run_with_intermediate_failure():
    timeline = CheckpointTimeline(("C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8"))
    scheduler = FleetScheduler(timeline)
    stable = scheduler.enqueue_lane(snapshot("stable", "bbbbbbbb", order=2), "C0")
    unstable = scheduler.enqueue_lane(snapshot("unstable", "aaaaaaaa", order=1), "C0")
    for lane in (stable, unstable):
        scheduler.next_to_start()
        for checkpoint in timeline.checkpoint_ids[1:]:
            scheduler.record_checkpoint(lane.lane_id, checkpoint)
        scheduler.complete_lane(lane.lane_id, elapsed_seconds=200 if lane is stable else 100)
    # Give the faster commit a separate run with an earlier functional failure.
    bad = scheduler.enqueue_lane(snapshot("unstable-2", "aaaaaaaa", order=3), "C2")
    scheduler.next_to_start()
    scheduler.record_failure(bad.lane_id, "crash", failure_checkpoint="C4", elapsed_seconds=50)
    summaries = [
        summarize_commit("bbbbbbbb", [stable], timeline),
        summarize_commit("aaaaaaaa", [unstable, bad], timeline),
    ]
    assert rank_commits(summaries)[0].commit_sha == "bbbbbbbb"


def test_last_frontier_projection_is_latest_ten_with_log_reason_and_star():
    timeline = CheckpointTimeline(("C0", "C1", "C2"))
    scheduler = FleetScheduler(timeline)
    lanes = []
    for index in range(12):
        lane = scheduler.enqueue_lane(
            snapshot(f"s{index}", f"commit{index:08d}", order=index + 1, frontier="C2"),
            "C2",
        )
        scheduler.next_to_start()
        scheduler.record_failure(lane.lane_id, "stalled", failure_checkpoint="C2", log_lines=(str(index),))
        lanes.append(lane)
    projection = last_frontier_results(lanes, "C2")
    assert len(projection) == 10
    assert projection[0].commit_code == "commit00"
    assert projection[0].failure_reason == "stalled"
    assert projection[0].starred is True
