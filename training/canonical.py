# Path: training/canonical.py
# Purpose: Produce stable content hashes for immutable training artifacts.

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

_HASH_PREFIX = "sha256:"


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize JSON data deterministically and reject non-finite numbers."""
    try:
        text = json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def canonical_sha256(payload: Any) -> str:
    """Return a prefixed SHA-256 digest of canonical JSON data."""
    return _HASH_PREFIX + hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def scenario_hash(payload: Mapping) -> str:
    """Hash a scenario while excluding its self-referential hash field."""
    content = {key: value for key, value in payload.items() if key != "scenario_hash"}
    return canonical_sha256(content)


def plan_hash(payload: Mapping) -> str:
    """Bind a candidate identity to its complete deterministic BuildPlan."""
    return canonical_sha256(dict(payload))


def policy_hash(payload: Mapping) -> str:
    """Bind a policy identity to its immutable manifest or parameters."""
    return canonical_sha256(dict(payload))
