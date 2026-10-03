# Path: tests/test_laya_knowledge.py
# Purpose: Verify current-document lineage and fail-closed guideline retrieval.

import json
from pathlib import Path

import pytest

from orchestrator.laya_knowledge import LayaKnowledgeError, load_guideline_context


ROOT = Path(__file__).resolve().parents[1]


def test_current_guidelines_retain_hard_rules_and_scope_soft_rules():
    context = load_guideline_context(ROOT, ["recovery"])
    ids = {rule["rule_id"] for rule in context["guidelines"]}
    assert "rule.repair-before-duplicate" in ids
    assert "rule.direct-transport" not in ids
    assert sum(rule["kind"] == "hard" for rule in context["guidelines"]) == 4
    assert context["provenance_ref"]["hash"].startswith("sha256:")
    assert context["provenance"]["source_documents"]
    assert len(load_guideline_context(ROOT, ["*"])["guidelines"]) == 8
    with pytest.raises(LayaKnowledgeError, match="hard guideline set"):
        load_guideline_context(ROOT, [], max_rules=3)


def test_document_edits_change_provenance_and_stale_passages_are_rejected(tmp_path):
    docs = tmp_path / "docs"
    (docs / "rl").mkdir(parents=True)
    source = docs / "rules.md"
    source.write_text("Stay isolated.\n")
    (docs / "rl/laya_guidelines.json").write_text(json.dumps({
        "version": "1.0.0", "rules": [{"rule_id": "scope", "kind": "hard",
        "source": "docs/rules.md", "quote": "Stay isolated."}],
    }))
    original = load_guideline_context(tmp_path, [])
    source.write_text("Stay isolated.\nAdditional context.\n")
    updated = load_guideline_context(tmp_path, [])
    assert updated["provenance_ref"] != original["provenance_ref"]
    source.write_text("The old instruction was removed.\n")
    with pytest.raises(LayaKnowledgeError, match="source passage changed"):
        load_guideline_context(tmp_path, [])


def test_mission_catalog_is_separate_and_scopes_the_users_transport_rule():
    mission = "docs/rl/laya_mission_guidelines.json"
    context = load_guideline_context(ROOT, ["mission.transport"], catalog=mission)
    ids = [rule["rule_id"] for rule in context["guidelines"]]
    assert ids == ["rule.plan-legality", "mission.bot-supply", "mission.belt-transfer"]
    # Every quoted passage still matches the strategy document.
    total = len(json.loads((ROOT / mission).read_text())["rules"])
    assert len(load_guideline_context(ROOT, ["*"], catalog=mission, max_rules=16)["guidelines"]) == min(16, total)
    # Existing runners' default catalog never sees mission rules.
    assert not any(r["rule_id"].startswith("mission.") for r in load_guideline_context(ROOT, ["*"])["guidelines"])
    with pytest.raises(LayaKnowledgeError, match="under docs/rl"):
        load_guideline_context(ROOT, ["*"], catalog="docs/system_invariants.md")
