# Path: orchestrator/laya_knowledge.py
# Purpose: Retrieve reviewed, source-verified document guidelines for bounded Laya decisions.

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable


class LayaKnowledgeError(ValueError):
    """A guideline cannot be traced to the current authoritative document."""


def _hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _text(value: str) -> str:
    return " ".join(value.split())


def load_guideline_context(
    repo_root: str | Path, scopes: Iterable[str], *, max_rules: int = 8,
    catalog: str = "docs/rl/laya_guidelines.json",
) -> dict:
    """Select applicable reviewed passages; never turn historical code into rules.

    The catalog is an explicit source selection, not an unrestricted search over
    conflicting historical documents. All hard rules survive selection. Full
    source hashes stay in evidence; the model sees only the selected passages.
    """
    root = Path(repo_root).resolve()
    # Separate runners keep separate catalogs so one runner's rules never
    # change another runner's inputs or provenance.
    catalog_path = (root / catalog).resolve()
    if not catalog_path.is_relative_to(root / "docs/rl"):
        raise LayaKnowledgeError(f"guideline catalog must be under docs/rl: {catalog}")
    try:
        catalog_bytes = catalog_path.read_bytes()
        catalog = json.loads(catalog_bytes)
    except (OSError, ValueError) as error:
        raise LayaKnowledgeError(f"cannot read guideline catalog: {error}") from error
    if catalog.get("version") != "1.0.0" or not isinstance(catalog.get("rules"), list):
        raise LayaKnowledgeError("unsupported guideline catalog")
    if not 1 <= max_rules <= 16:
        raise LayaKnowledgeError("max_rules must be between 1 and 16")
    requested = set(scopes)
    hard, soft, sources, seen = [], [], {}, set()
    for raw in catalog["rules"]:
        if not isinstance(raw, dict) or raw.get("kind") not in {"hard", "soft"}:
            raise LayaKnowledgeError("malformed guideline rule")
        identifier = raw.get("rule_id")
        if not isinstance(identifier, str) or not identifier or identifier in seen:
            raise LayaKnowledgeError("guideline IDs must be unique and nonempty")
        seen.add(identifier)
        if raw["kind"] == "soft" and "*" not in requested and not (set(raw.get("scopes", [])) & (requested | {"*"})):
            continue
        relative = raw.get("source")
        quote = raw.get("quote")
        if not isinstance(relative, str) or not isinstance(quote, str):
            raise LayaKnowledgeError(f"missing source passage: {identifier}")
        path = (root / relative).resolve()
        docs = root / "docs"
        if not path.is_relative_to(docs) or path.is_relative_to(docs / "archive"):
            raise LayaKnowledgeError(f"source must be an active document: {relative}")
        try:
            document = path.read_bytes()
            normalized = _text(document.decode("utf-8-sig"))
        except (OSError, UnicodeError) as error:
            raise LayaKnowledgeError(f"cannot read {relative}: {error}") from error
        statement = _text(quote)
        if not statement or len(statement) > 512 or statement not in normalized:
            raise LayaKnowledgeError(f"source passage changed or is missing: {identifier} in {relative}")
        rule = {"rule_id": identifier, "kind": raw["kind"], "statement": statement}
        (hard if raw["kind"] == "hard" else soft).append(rule)
        sources[relative] = {"path": relative, "hash": _hash(document)}
    if not hard or len(hard) > max_rules:
        raise LayaKnowledgeError("the complete hard guideline set does not fit")
    rules = hard + soft[:max_rules - len(hard)]
    # Preserve which optional rules did not fit; never conceal context selection.
    selected = {rule["rule_id"] for rule in rules}
    provenance = {
        "catalog_hash": _hash(catalog_bytes),
        "source_documents": list(sources.values()),
        "selected_rule_ids": [rule["rule_id"] for rule in rules],
        "omitted_soft_rule_ids": [rule["rule_id"] for rule in soft if rule["rule_id"] not in selected],
    }
    digest = _hash(json.dumps({"rules": rules, "provenance": provenance}, sort_keys=True).encode())
    return {
        "guidelines": rules,
        "provenance_ref": {"id": "pack.laya-reviewed-documents", "version": "1.0.0", "hash": digest},
        "provenance": provenance,
    }
