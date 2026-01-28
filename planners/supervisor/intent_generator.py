# Path: planners/supervisor/intent_generator.py
# Purpose: Translate policy signals into high-level intents without actions.

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from planners.supervisor.policy_evaluator import PolicySignal


@dataclass(frozen=True)
class Intent:
    intent: str
    scope: str
    urgency: str
    blocked_by: List[str]
    notes: str


def _urgency_for_level(level: str) -> str:
    if level == "critical":
        return "high"
    if level == "warn":
        return "medium"
    return "low"


def generate_intents(signals: Iterable[PolicySignal]) -> List[Intent]:
    intents: List[Intent] = []

    for signal in signals:
        if signal.policy == "bot_saturation" and signal.level in {"warn", "critical"}:
            intents.append(
                Intent(
                    intent="reduce_bot_dependency",
                    scope="local",
                    urgency=_urgency_for_level(signal.level),
                    blocked_by=[],
                    notes=f"Bot density {signal.level}: {signal.value:.6f} vs {signal.threshold:.6f}",
                )
            )

        if signal.policy == "construction_bot_load" and signal.level == "critical":
            intents.append(
                Intent(
                    intent="reduce_bot_dependency",
                    scope="local",
                    urgency=_urgency_for_level(signal.level),
                    blocked_by=[],
                    notes=f"Active construction bots critical: {signal.value:.0f} vs {signal.threshold:.0f}",
                )
            )

    return intents
