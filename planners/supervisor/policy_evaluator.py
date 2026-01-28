# Path: planners/supervisor/policy_evaluator.py
# Purpose: Evaluate policies over metrics without taking actions.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from core.metrics import BotMetrics


@dataclass(frozen=True)
class PolicySignal:
    policy: str
    level: str
    metric: str
    value: float
    threshold: float
    recommended_direction: str


class PolicyEvaluator:
    """Read-only evaluator that emits policy signals from metrics."""

    def __init__(self, bot_thresholds_path: Optional[Path] = None):
        repo_root = Path(__file__).resolve().parents[2]
        self.bot_thresholds_path = bot_thresholds_path or (
            repo_root / "policies" / "bot_thresholds.json"
        )

    def load_bot_thresholds(self) -> Dict[str, float]:
        with self.bot_thresholds_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def evaluate_bot_policies(self, bot_metrics: BotMetrics) -> List[PolicySignal]:
        thresholds = self.load_bot_thresholds()
        signals: List[PolicySignal] = []

        warn_density = thresholds.get("warn_bot_density")
        max_density = thresholds.get("max_bot_density")

        if bot_metrics.bot_density_per_tile is not None and warn_density is not None and max_density is not None:
            density = bot_metrics.bot_density_per_tile
            if density >= max_density:
                level = "critical"
                threshold = max_density
            elif density >= warn_density:
                level = "warn"
                threshold = warn_density
            else:
                level = "ok"
                threshold = warn_density

            signals.append(
                PolicySignal(
                    policy="bot_saturation",
                    level=level,
                    metric="bot_density",
                    value=density,
                    threshold=threshold,
                    recommended_direction="reduce_bot_dependency" if level != "ok" else "maintain",
                )
            )

        max_construction = thresholds.get("max_active_construction_bots")
        if max_construction is not None:
            value = float(bot_metrics.active_construction_bots)
            level = "critical" if value >= max_construction else "ok"
            signals.append(
                PolicySignal(
                    policy="construction_bot_load",
                    level=level,
                    metric="active_construction_bots",
                    value=value,
                    threshold=float(max_construction),
                    recommended_direction="reduce_bot_dependency" if level != "ok" else "maintain",
                )
            )

        return signals

    def evaluate(self, bot_metrics: BotMetrics) -> List[PolicySignal]:
        signals = self.evaluate_bot_policies(bot_metrics)
        signals.sort(key=lambda signal: (signal.policy, signal.metric))
        return signals
