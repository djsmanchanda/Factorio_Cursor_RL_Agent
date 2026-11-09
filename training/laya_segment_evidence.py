# Path: training/laya_segment_evidence.py | Purpose: Report fresh strict predicates and explicit segment stop semantics.
from __future__ import annotations

from dataclasses import asdict

from orchestrator.checkpoint_milestones import CheckpointEvaluator


def current_predicates(session):
    """Never use the session's monotonic reached set as end-state evidence."""
    evaluator = CheckpointEvaluator()
    history = list(getattr(session, '_history', ()))
    baseline = getattr(session, '_baseline', None)
    statuses = {m.checkpoint_id: None for m in evaluator.milestones}
    evidence = {}
    if history and baseline is not None:
        # Paused capture/reload may observe the same sample more than once.
        history = [sample for i, sample in enumerate(history) if i == 0 or sample != history[i - 1]]
        for milestone in evaluator.milestones:
            result = CheckpointEvaluator().evaluate(milestone.checkpoint_id, history[-1],
                                                   baseline=baseline, history=history)
            statuses[milestone.checkpoint_id] = (True if result.status.value == 'passed' else
                                                False if result.status.value == 'failed' else None)
            evidence[milestone.checkpoint_id] = asdict(result)
    return statuses, evidence


def stop_classification(reason):
    return {'strict_target_reached': 'target_success', 'stalled': 'genuine_failure',
            'research_complete': 'target_success', 'wall_budget': 'wall_timeout',
            'safe_stop_after_repeated_failures': 'safe_stop', 'stopped': 'safe_stop',
            'phase_ladder_complete': 'capability_limit', 'window_timeout': 'tick_timeout',
            'decision_budget': 'decision_timeout', 'window_reached': 'safe_stop',
            'runner_error': 'infrastructure_failure', 'goal_completed': 'target_success',
            'accepted_window_reached': 'target_success', 'mission_complete': 'capability_limit',
            'proxy_mission_complete_without_acceptance': 'capability_limit'}.get(reason, 'unknown')
