# Path: training/laya_segment_evidence.py | Purpose: Report fresh strict predicates and explicit segment stop semantics.
from __future__ import annotations

from dataclasses import asdict

from orchestrator.checkpoint_milestones import CheckpointEvaluator
from orchestrator.checkpoint_evidence import project_evidence, retained_samples, state_projection


def current_predicates(session, proxy_phase=None):
    """Never use the session's monotonic reached set as end-state evidence."""
    evaluator = CheckpointEvaluator()
    history = list(getattr(session, '_history', ()))
    baseline = getattr(session, '_baseline', None)
    statuses = {m.checkpoint_id: None for m in evaluator.milestones}
    evidence = {}
    state = getattr(session, "_evidence_state", None)
    if history and baseline is not None and (state is None or state["current_available"]):
        # Paused capture/reload may observe the same sample more than once.
        history = retained_samples(history)
        for milestone in evaluator.milestones:
            result = CheckpointEvaluator().evaluate(milestone.checkpoint_id, history[-1],
                                                   baseline=baseline, history=history)
            statuses[milestone.checkpoint_id] = (True if result.status.value == 'passed' else
                                                False if result.status.value == 'failed' else None)
            evidence[milestone.checkpoint_id] = asdict(result)
    evidence["_contract"] = (state_projection(baseline, history, state, proxy_phase) if state is not None
                             else project_evidence(baseline, history))
    evidence["_contract"]["proxy_phase"] = proxy_phase
    return statuses, evidence


def stop_classification(reason):
    return {'strict_target_reached': 'target_success', 'stalled': 'genuine_failure',
            'research_complete': 'target_success', 'wall_budget': 'wall_timeout',
            'safe_stop_after_repeated_failures': 'safe_stop', 'stopped': 'safe_stop',
            'phase_ladder_complete': 'capability_limit', 'window_timeout': 'tick_timeout',
            'decision_budget': 'decision_timeout', 'window_reached': 'safe_stop',
            'runner_error': 'infrastructure_failure'}.get(reason, 'unknown')
