# Path: tests/test_laya_receipts.py | Purpose: The receipt cache parses each file once per change and keeps only slim fields.

import json
import os

import pytest

pytestmark = pytest.mark.fast


def test_receipts_parse_once_per_change_and_keep_slim_fields(tmp_path, monkeypatch):
    from training import laya_receipts
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    path = evidence / "transaction-a.json"
    path.write_text(json.dumps({"status": "observed", "candidate": {"family": "line"}, "before": {"tick": 5, "big": [0] * 9},
                                "submission": {"ok": True, "placement_failures": []}}))
    parsed = []
    real = laya_receipts.json.loads
    monkeypatch.setattr(laya_receipts.json, "loads", lambda text: parsed.append(1) or real(text))
    [(_, first)] = laya_receipts.receipts(tmp_path)
    laya_receipts.receipts(tmp_path)
    assert len(parsed) == 1
    assert first == {"status": "observed", "reason": None, "candidate": {"family": "line"},
                     "submission": {"ok": True, "error": None}, "before_tick": 5}
    path.write_text(json.dumps({"status": "rejected"}))
    os.utime(path, ns=(1, 1))  # A rewrite changes mtime/size; the cache must notice.
    [(_, second)] = laya_receipts.receipts(tmp_path)
    assert len(parsed) == 2 and second["status"] == "rejected"
