# Path: training/laya_policy.py
# Purpose: Adapt a typed Laya predictor to the training policy interface without deterministic fallback.

from __future__ import annotations

import math

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from orchestrator.laya_state import (
    DEFAULT_BUDGET_PROFILE,
    LayaBudgetProfileLike,
    get_budget_profile,
    validate_state,
)
from training.laya_guidance import LayaGuidanceStore


class LayaPredictor(Protocol):
    def predict(self, state: Mapping[str, Any], questions: Mapping[str, Any]) -> Mapping[str, Any]: ...


class LayaPolicyError(RuntimeError):
    """Base error for unavailable, invalid, or unsafe model decisions."""


class LayaEscalation(LayaPolicyError):
    """The director must observe again or escalate; no hidden baseline is used."""


StateBuilder = Callable[[Mapping[str, Any], Sequence[Mapping[str, Any]]], Mapping[str, Any]]


@dataclass
class LayaPolicy:
    """Select a candidate through Laya's typed choice output.

    The state builder owns the complete versioned Laya state.  This class only
    builds a dynamic option question, validates the answer, and returns the
    exact candidate whose identity Laya selected.
    """

    agent: LayaPredictor
    state_builder: StateBuilder
    policy_id: str = "laya-director-local-v1"
    model_id: str = "convaiinnovations/laya"
    model_hash: str = "sha256:" + ("0" * 64)
    confidence_threshold: float = 0.70
    max_options: int = 20
    guidance_store: LayaGuidanceStore | None = None
    budget_profile: LayaBudgetProfileLike = DEFAULT_BUDGET_PROFILE
    request_tokenizer: Any | None = None

    def __post_init__(self) -> None:
        if not self.policy_id or not self.model_id:
            raise ValueError("Laya policy and model IDs are required")
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0, 1]")
        if not 1 <= self.max_options <= 20:
            raise ValueError("max_options must be between 1 and 20")
        get_budget_profile(self.budget_profile)

    def select(
        self,
        candidates: Sequence[Mapping[str, Any]],
        observation: Mapping[str, Any],
        seed: int | None = None,
    ) -> Mapping[str, Any]:
        del seed
        if not candidates:
            raise LayaEscalation("Laya received no candidates")
        profile = get_budget_profile(self.budget_profile)
        if len(candidates) > min(self.max_options, profile.max_options):
            raise LayaEscalation("candidate set exceeds the Laya option budget")
        ids = [str(candidate.get("action_id", "")) for candidate in candidates]
        if any(not identifier for identifier in ids) or len(ids) != len(set(ids)):
            raise LayaPolicyError("candidate action IDs must be unique and non-empty")

        state = dict(self.state_builder(observation, candidates))
        if self.guidance_store is not None:
            state = self.guidance_store.apply(state)
        state_options = state.get("options") or []
        state_option_ids = [str(option.get("option_id", "")) for option in state_options]
        if state_option_ids != ids:
            raise LayaPolicyError("state option identities must match candidates in order")
        questions = {
            "select_candidate": {
                "type": "choice",
                "instructions": "Choose the next validated operation candidate.",
                "criteria": {
                    identifier: str(option.get("summary") or identifier)
                    for identifier, option in zip(ids, state_options)
                },
            }
        }
        try:
            validate_state(state)
        except Exception as error:
            raise LayaEscalation(f"Laya decision state is invalid: {error}") from error
        # The predictor owns exact sequence construction and its lossless
        # token gate. Measuring a different JSON envelope here previously
        # rejected valid inputs while the model builder truncated other ones.
        try:
            result = self.agent.predict(state, questions)
        except Exception as error:  # model/runtime errors must become explicit escalation
            raise LayaEscalation(f"Laya inference failed: {error}") from error
        answer = ((result.get("answers") or {}).get("select_candidate") or {})
        selected = answer.get("choice", answer.get("selected_label"))
        try:
            confidence = float(answer.get("confidence", 0.0))
        except (TypeError, ValueError) as error:
            raise LayaEscalation("Laya confidence must be a finite probability") from error
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise LayaEscalation("Laya confidence must be a finite probability")
        if selected not in ids:
            raise LayaEscalation("Laya returned an option outside the presented candidate set")
        if confidence < self.confidence_threshold:
            raise LayaEscalation(
                f"Laya confidence {confidence:.3f} is below {self.confidence_threshold:.3f}"
            )
        return next(candidate for candidate in candidates if candidate.get("action_id") == selected)

    def to_dict(self) -> dict:
        return {
            "version": "1.0.0",
            "algorithm": "laya_director",
            "policy_id": self.policy_id,
            "model_id": self.model_id,
            "model_hash": self.model_hash,
            "confidence_threshold": self.confidence_threshold,
            "max_options": self.max_options,
            "budget_profile": get_budget_profile(self.budget_profile).profile_id,
        }


__all__ = ["LayaEscalation", "LayaPolicy", "LayaPolicyError", "LayaPredictor"]
