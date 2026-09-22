# Path: orchestrator/laya_state.py
# Purpose: Validate Laya state evidence and render canonical storage envelopes.

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator


class LayaStateError(ValueError):
    """Raised when a director state cannot be safely rendered."""


@dataclass(frozen=True)
class LayaBudgetProfile:
    """A named context profile for one bounded Laya request.

    ``state_target_tokens`` and ``question_target_tokens`` are compacting
    targets for renderers.  The hard gate is always ``max_tokens`` over the
    complete request envelope, because a state that fits by itself can still
    overflow once instructions and option criteria are added.
    """

    profile_id: str
    max_tokens: int = 1024
    state_target_tokens: int = 768
    question_target_tokens: int = 256
    max_options: int = 20

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id is required")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if self.state_target_tokens < 1 or self.question_target_tokens < 1:
            raise ValueError("budget targets must be positive")
        if self.state_target_tokens + self.question_target_tokens > self.max_tokens:
            raise ValueError("budget targets cannot exceed max_tokens")
        if not 1 <= self.max_options <= 20:
            raise ValueError("max_options must be between 1 and 20")


@dataclass(frozen=True)
class LayaRequestUsage:
    """Measured size of a canonical evidence envelope, not model input."""

    profile_id: str
    token_count: int
    max_tokens: int
    tokenizer: str
    state_tokens: int
    question_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return self.max_tokens - self.token_count


LayaBudgetProfileLike = str | LayaBudgetProfile


# The compact profiles are opt-in except for the 1024-token director profile.
# Keeping the names stable lets dataset/runtime provenance record the profile
# without embedding renderer-specific constants in every caller.
BUDGET_PROFILES: dict[str, LayaBudgetProfile] = {
    "director-1024": LayaBudgetProfile(
        "director-1024", max_tokens=1024, state_target_tokens=768,
        question_target_tokens=256, max_options=20,
    ),
    "compact-768": LayaBudgetProfile(
        "compact-768", max_tokens=768, state_target_tokens=576,
        question_target_tokens=192, max_options=16,
    ),
    "compact-512": LayaBudgetProfile(
        "compact-512", max_tokens=512, state_target_tokens=384,
        question_target_tokens=128, max_options=12,
    ),
}
DEFAULT_BUDGET_PROFILE = "director-1024"


def get_budget_profile(profile: LayaBudgetProfileLike | None = None) -> LayaBudgetProfile:
    """Resolve a named or inline request budget profile."""
    if profile is None:
        return BUDGET_PROFILES[DEFAULT_BUDGET_PROFILE]
    if isinstance(profile, LayaBudgetProfile):
        return profile
    try:
        return BUDGET_PROFILES[profile]
    except KeyError as error:
        raise ValueError(
            f"unknown Laya budget profile {profile!r}; expected one of "
            + ", ".join(BUDGET_PROFILES)
        ) from error


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _ROOT / "schemas" / "laya_state.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft7Validator(_SCHEMA)


def canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def state_hash(payload: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def validate_state(payload: Mapping[str, Any]) -> None:
    errors = sorted(_VALIDATOR.iter_errors(dict(payload)), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}"
            for error in errors
        )
        raise LayaStateError(f"Laya state validation failed: {detail}")


def estimate_tokens(rendered: str) -> int:
    """Conservative tokenizer-free estimate used before the model tokenizer exists."""
    return max(1, (len(rendered) + 3) // 4)


def _tokenizer_count(rendered: str, tokenizer: Any) -> int:
    """Count tokens with a HuggingFace-like tokenizer without truncation."""
    try:
        if hasattr(tokenizer, "encode"):
            try:
                encoded = tokenizer.encode(
                    rendered, add_special_tokens=True, truncation=False,
                )
            except TypeError:
                # Small test/dummy tokenizers often expose only ``encode(text)``.
                encoded = tokenizer.encode(rendered)
        else:
            try:
                encoded = tokenizer(
                    rendered, add_special_tokens=True, truncation=False,
                )
            except TypeError:
                encoded = tokenizer(rendered)
        if isinstance(encoded, Mapping):
            encoded = encoded["input_ids"]
        # Batched tokenizer calls return [[...]].  This API counts one request,
        # so reject a multi-row result instead of measuring only the first row.
        if isinstance(encoded, (list, tuple)) and encoded and isinstance(encoded[0], (list, tuple)):
            if len(encoded) != 1:
                raise ValueError("tokenizer returned more than one request")
            encoded = encoded[0]
        count = len(encoded)
    except Exception as error:
        raise LayaStateError(f"Laya tokenizer could not count the request: {error}") from error
    if count < 1:
        raise LayaStateError("Laya tokenizer returned an empty request")
    return int(count)


def _count_tokens(rendered: str, tokenizer: Any | None) -> tuple[int, str]:
    if tokenizer is None:
        # JSON is ASCII in the normal renderer.  The two-token margin covers
        # common BOS/EOS wrappers while retaining the historical 4-char fallback.
        return estimate_tokens(rendered) + 2, "conservative-estimate"
    return _tokenizer_count(rendered, tokenizer), "tokenizer"


def _effective_max_tokens(max_tokens: int | None, profile: LayaBudgetProfileLike | None) -> tuple[int, LayaBudgetProfile]:
    resolved = get_budget_profile(profile)
    if max_tokens is None:
        return resolved.max_tokens, resolved
    if max_tokens < 1:
        raise ValueError("max_tokens must be positive")
    if profile is not None and max_tokens != resolved.max_tokens:
        raise ValueError("max_tokens conflicts with the selected Laya budget profile")
    return max_tokens, resolved


def _budget_error(kind: str, tokens: int, max_tokens: int, profile_id: str) -> LayaStateError:
    return LayaStateError(
        f"Laya {kind} requires {tokens} tokens; budget is {max_tokens} "
        f"(profile {profile_id!r}); compact the renderer explicitly"
    )


def _question_option_count(question: Any) -> int:
    if not isinstance(question, Mapping):
        raise LayaStateError("each Laya question must be an object")
    criteria = question.get("criteria")
    if isinstance(criteria, Mapping):
        return len(criteria)
    if isinstance(criteria, (list, tuple)):
        return len(criteria)
    if question.get("type") == "noul":
        return 2
    return 0


def render_state(
    payload: Mapping[str, Any], *, max_tokens: int | None = None,
    tokenizer: Any | None = None,
    budget_profile: LayaBudgetProfileLike | None = None,
) -> str:
    """Validate state and return compact JSON; never silently drops required facts."""
    # Preserve the historical ``render_state(payload)`` call contract while
    # allowing callers that already have a profile/tokenizer to use the same
    # exact counting path as full request rendering.
    limit, profile = _effective_max_tokens(max_tokens, budget_profile)
    validate_state(payload)
    rendered = canonical_json(payload)
    tokens, _ = _count_tokens(rendered, tokenizer)
    if tokens > limit:
        raise _budget_error("state", tokens, limit, profile.profile_id)
    return rendered


def measure_request(
    state: Mapping[str, Any],
    questions: Mapping[str, Any],
    *,
    max_tokens: int | None = None,
    tokenizer: Any | None = None,
    budget_profile: LayaBudgetProfileLike | None = None,
) -> tuple[str, LayaRequestUsage]:
    """Render and measure a canonical state-plus-question evidence envelope.

    This remains useful for storage and diagnostics. It is not the Laya model
    representation and must not gate inference; ``training.laya_sequence``
    builds and budgets that exact sequence. The envelope is never truncated.
    ``tokenizer`` may be a HuggingFace tokenizer or an ``encode``/callable test
    double. With no tokenizer, a conservative estimate is reported.
    """
    if not isinstance(questions, Mapping) or not questions:
        raise LayaStateError("Laya request must contain at least one question")
    limit, profile = _effective_max_tokens(max_tokens, budget_profile)
    for question_id, question in questions.items():
        option_count = _question_option_count(question)
        if option_count > profile.max_options:
            raise LayaStateError(
                f"question {question_id!r} has {option_count} options; "
                f"profile {profile.profile_id!r} allows {profile.max_options}"
            )
    validate_state(state)
    request = {"state": dict(state), "questions": dict(questions)}
    rendered = canonical_json(request)
    tokens, tokenizer_name = _count_tokens(rendered, tokenizer)
    if tokens > limit:
        raise _budget_error("request", tokens, limit, profile.profile_id)
    state_rendered = canonical_json(state)
    question_rendered = canonical_json(questions)
    state_tokens, _ = _count_tokens(state_rendered, tokenizer)
    question_tokens, _ = _count_tokens(question_rendered, tokenizer)
    return rendered, LayaRequestUsage(
        profile_id=profile.profile_id,
        token_count=tokens,
        max_tokens=limit,
        tokenizer=tokenizer_name,
        state_tokens=state_tokens,
        question_tokens=question_tokens,
    )


def render_request(
    state: Mapping[str, Any],
    questions: Mapping[str, Any],
    *,
    max_tokens: int | None = None,
    tokenizer: Any | None = None,
    budget_profile: LayaBudgetProfileLike | None = None,
) -> str:
    """Validate and render the canonical evidence envelope with all options."""
    rendered, _ = measure_request(
        state, questions, max_tokens=max_tokens, tokenizer=tokenizer,
        budget_profile=budget_profile,
    )
    return rendered


__all__ = [
    "BUDGET_PROFILES", "DEFAULT_BUDGET_PROFILE", "LayaBudgetProfile",
    "LayaRequestUsage", "LayaStateError", "canonical_json", "estimate_tokens",
    "get_budget_profile", "measure_request", "render_request", "render_state",
    "state_hash", "validate_state",
]
