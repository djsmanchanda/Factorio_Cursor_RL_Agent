# Path: tests/test_laya_guidelines.py
# Purpose: Verify guideline compilation, provenance, and non-promotion defaults.

from __future__ import annotations

import pytest

from orchestrator.laya_guidelines import LayaGuidelineError, compile_guideline_pack


def _pack():
    return compile_guideline_pack(
        pack_id="pack.training",
        pack_version="1.0.0",
        task_scopes=["assemble"],
        goal={"statement": "Build safely", "acceptance_assertion": "Output is sustained"},
        hard_rules=[{"rule_id": "rule.isolation", "statement": "Stay isolated", "rationale": "Runtime boundary", "test_assertion": "Scope matches"}],
        soft_rules=[{"rule_id": "rule.direct", "statement": "Prefer direct transport", "rationale": "Less handling", "test_assertion": "Route cost is measured", "reward_weight": 0.5}],
        tool_inventory=[{"tool_id": "assemble.line", "registry_version": "1.0.0", "when_to_use": "When legal", "when_not_to_use": "When blocked", "failure_modes": ["collision"]}],
        source_documents=[{"path": "docs/system_invariants.md", "hash": "sha256:" + "0" * 64}],
    )


def test_guideline_pack_is_proposed_and_provenance_bound() -> None:
    pack = _pack()
    assert pack["promotion"] == {"status": "proposed", "evidence": None, "parent_pack": None}
    assert pack["provenance"]["pack_hash"].startswith("sha256:")
    assert pack["hard_rules"][0]["kind"] == "hard"
    assert pack["soft_rules"][0]["reward_weight"] == 0.5


def test_guideline_kind_mismatch_fails_closed() -> None:
    with pytest.raises(LayaGuidelineError, match="wrong kind"):
        compile_guideline_pack(
            pack_id="pack.bad", pack_version="1.0.0", task_scopes=["assemble"],
            goal={"statement": "x", "acceptance_assertion": "y"},
            hard_rules=[{"rule_id": "bad", "kind": "soft", "statement": "x", "rationale": "y", "test_assertion": "z"}],
            soft_rules=[], tool_inventory=[], source_documents=[{"path": "x", "hash": "sha256:" + "0" * 64}],
        )
