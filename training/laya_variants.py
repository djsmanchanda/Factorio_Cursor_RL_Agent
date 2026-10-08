# Path: training/laya_variants.py
# Purpose: Variants for Laya experiments: small, recorded tweaks to the rules Laya reads, its context, the policy and the runtime.

"""Laya variants.

A variant is a JSON file (``docs/rl/laya_variants/<id>.json``)::

    {"version": "1.0.0", "variant_id": "goal-weight-5", "description": "...",
     "catalog": "docs/rl/laya_mission_guidelines.json",          # optional: another catalog (doc text tweaks)
     "catalog_patch": {"mission.phase-goals-first": {"prefer": [...]}},  # optional: preference tweaks
     "settings": {"guideline_limit": 12, "stall_decisions": 16},  # training.laya_clean_runner.Settings fields
     "policy": {"epsilon": 0.1},
     "runtime": {"speed": 4.0}}

A patched catalog is materialized under ``docs/rl/laya_variants/generated/``
(quotes are still verified against their source documents).  The variant's
content hash is recorded with every run so results stay attributable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from pathlib import Path
import tempfile

from training.laya_clean_runner import MISSION_CATALOG, ROOT, Settings

GENERATED = ROOT / "docs/rl/laya_variants/generated"
KEYS = {"version", "variant_id", "description", "catalog", "catalog_patch", "settings", "policy", "runtime"}


class VariantError(ValueError):
    pass


def baseline() -> dict:
    return {"version": "1.0.0", "variant_id": "baseline", "description": "Current workflow defaults."}


def load_variant(path: Path | str | None) -> dict:
    if path is None:
        return baseline()
    variant = json.loads(Path(path).read_text())
    unknown = set(variant) - KEYS
    if unknown or variant.get("version") != "1.0.0" or not variant.get("variant_id"):
        raise VariantError(f"bad variant {path}: unknown keys {sorted(unknown)} or missing version/variant_id")
    fields = {f.name for f in dataclasses.fields(Settings)}
    if set(variant.get("settings", {})) - fields:
        raise VariantError(f"unknown settings {sorted(set(variant['settings']) - fields)}")
    return variant


def variant_hash(variant: dict) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(variant, sort_keys=True).encode()).hexdigest()


def _catalog(variant: dict) -> str:
    """The catalog path Laya reads: the variant's own, patched when it carries preference tweaks."""
    base = variant.get("catalog", MISSION_CATALOG)
    patch = variant.get("catalog_patch")
    if not patch:
        return base
    catalog = json.loads((ROOT / base).read_text())
    ids = {rule["rule_id"] for rule in catalog["rules"]}
    if set(patch) - ids:
        raise VariantError(f"catalog_patch names unknown rules {sorted(set(patch) - ids)}")
    for rule in catalog["rules"]:
        rule.update(patch.get(rule["rule_id"], {}))
    GENERATED.mkdir(parents=True, exist_ok=True)
    target = GENERATED / f"{variant['variant_id']}-{variant_hash(variant)[7:19]}.json"
    # Multiple episode processes materialize the same variant at startup.
    # Readers must see a complete old or new catalog, never a truncated file.
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=GENERATED, delete=False) as out:
        temporary = Path(out.name)
        try:
            out.write(json.dumps(catalog, indent=2) + "\n")
            out.close()
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return str(target.relative_to(ROOT))


def apply(variant: dict) -> tuple[Settings, dict, dict]:
    """Settings for the runner, policy keyword arguments and runtime options."""
    settings = dataclasses.replace(Settings(), catalog=_catalog(variant), **variant.get("settings", {}))
    return settings, dict(variant.get("policy", {})), dict(variant.get("runtime", {}))
