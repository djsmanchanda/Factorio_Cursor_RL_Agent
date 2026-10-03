# Path: tests/test_laya_variants.py | Purpose: Protect Laya variants: patched catalogs still verify against the docs, settings are checked, the baseline is the default.

import json

import pytest

from orchestrator.laya_knowledge import load_guideline_context
from training.laya_clean_runner import MISSION_CATALOG, ROOT, Settings
from training.laya_guided_policy import load_preferences
from training.laya_mission import PHASES
from training.laya_variants import VariantError, apply, load_variant


def test_baseline_is_the_current_workflow():
    settings, policy, runtime = apply(load_variant(None))
    assert settings == Settings() and settings.catalog == MISSION_CATALOG and not policy and not runtime


def test_catalog_patch_changes_only_weights_and_still_verifies():
    variant = load_variant(ROOT / "docs/rl/laya_variants/goal-weight-10.json")
    settings, _, _ = apply(variant)
    prefs = load_preferences(ROOT / settings.catalog)
    assert prefs["mission.phase-goals-first"][0]["weight"] == 10.0
    assert prefs["mission.serve-bottleneck"] == load_preferences()["mission.serve-bottleneck"]
    load_guideline_context(ROOT, PHASES[0]["scopes"], catalog=settings.catalog, max_rules=32)  # quotes verified


@pytest.mark.parametrize("bad", [{"settings": {"no_such_knob": 1}}, {"catalog_patch": {"mission.nope": {}}}, {"extra": 1}])
def test_bad_variants_fail_closed(tmp_path, bad):
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"version": "1.0.0", "variant_id": "bad", **bad}))
    with pytest.raises(VariantError):
        apply(load_variant(path))
