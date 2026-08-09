# Path: training/research/controller.py
# Purpose: Ask a local LLM for bounded policy changes only after measured plateaus.

from __future__ import annotations

from typing import Mapping, Sequence

from training.research.proposals import PolicyProposal, validate_proposal


SYSTEM_PROMPT = """You are a Factorio policy research assistant. Propose exactly one bounded
configuration experiment. You may change only keys listed in allowed_changes. Never propose
source-code, planner, geometry, reward, evaluator, holdout, credential, or deployment changes.
Return one JSON object with hypothesis, changes, expected_effect, and stop_condition."""


def has_plateau(scores: Sequence[float], window: int = 5, minimum_delta: float = 0.001) -> bool:
    if window < 2 or len(scores) < window:
        return False
    recent = [float(value) for value in scores[-window:]]
    return max(recent) - min(recent) <= minimum_delta


def build_research_packet(
    policy_config: Mapping, experiment_summaries: Sequence[Mapping],
    failure_counts: Mapping[str, int], allowed_changes: Mapping,
) -> dict:
    """Bound prompt size and exclude raw paths, credentials, and unrestricted logs."""
    summaries = [dict(summary) for summary in experiment_summaries[-10:]]
    failures = sorted(failure_counts.items(), key=lambda item: (-item[1], item[0]))[:10]
    return {
        "policy_config": dict(policy_config),
        "recent_experiments": summaries,
        "failure_counts": dict(failures),
        "allowed_changes": dict(allowed_changes),
    }


class AutoresearchController:
    def __init__(self, client) -> None:
        self.client = client

    def propose(
        self, scores: Sequence[float], packet: Mapping,
        *, window: int = 5, minimum_delta: float = 0.001,
    ) -> PolicyProposal | None:
        if not has_plateau(scores, window, minimum_delta):
            return None
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": __import__("json").dumps(packet, sort_keys=True)},
        ]
        return validate_proposal(self.client.complete_json(messages))
