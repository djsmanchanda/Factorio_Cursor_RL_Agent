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


def test_catalog_replacement_keeps_the_previous_file_readable(tmp_path, monkeypatch):
    from training import laya_variants as variants
    monkeypatch.setattr(variants, "ROOT", tmp_path)
    monkeypatch.setattr(variants, "GENERATED", tmp_path / "generated")
    base = tmp_path / "base.json"
    base.write_text(json.dumps({"rules": [{"rule_id": "rule", "note": "old"}]}))
    variant = {"variant_id": "test", "catalog": "base.json", "catalog_patch": {"rule": {"weight": 2}}}
    target = tmp_path / variants._catalog(variant)
    old = target.read_text()
    base.write_text(json.dumps({"rules": [{"rule_id": "rule", "note": "new"}]}))
    replace = variants.os.replace

    def checked_replace(source, destination):
        assert target.read_text() == old
        assert json.loads(source.read_text())["rules"][0]["note"] == "new"
        replace(source, destination)

    monkeypatch.setattr(variants.os, "replace", checked_replace)
    variants._catalog(variant)
    assert json.loads(target.read_text())["rules"][0]["note"] == "new"
    assert list((tmp_path / "generated").iterdir()) == [target]


@pytest.mark.parametrize("bad", [{"settings": {"no_such_knob": 1}}, {"catalog_patch": {"mission.nope": {}}}, {"extra": 1}])
def test_bad_variants_fail_closed(tmp_path, bad):
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"version": "1.0.0", "variant_id": "bad", **bad}))
    with pytest.raises(VariantError):
        apply(load_variant(path))
