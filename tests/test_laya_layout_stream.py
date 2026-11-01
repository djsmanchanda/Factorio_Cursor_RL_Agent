# Path: tests/test_laya_layout_stream.py | Purpose: Verify durable single-writer layout submission and recovery.
import json

import pytest

from training.laya_layout_stream import LayoutStreamError, _locked, recover, submit


class Bridge:
    def __init__(self, root, *, fail=False):
        self.root = root
        self.fail = fail
        self.calls = []
        self.receipts = {}

    def build_layout_correlated(self, authorization, plan, *, request_id, stream_id, sequence, timeout):
        state = json.loads((self.root / "evidence/layout-stream.json").read_text())
        assert state["pending"]["request_id"] == request_id
        assert (self.root / "prepared.json").exists()
        self.calls.append((request_id, sequence))
        self.receipts[request_id] = {"ok": True, "request_id": request_id}
        if self.fail:
            raise TimeoutError("response lost")
        return self.receipts[request_id]

    def read_layout_receipt(self, authorization, plan, *, request_id, stream_id, sequence):
        return self.receipts[request_id]


def prepared(root):
    return lambda request: (root / "prepared.json").write_text(json.dumps(request))


def test_submissions_are_journaled_before_command_and_sequences_advance(tmp_path):
    bridge = Bridge(tmp_path)
    for i in range(1, 131):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{i:032x}.json",
               prepared=prepared(tmp_path))
    state = json.loads((tmp_path / "evidence/layout-stream.json").read_text())
    assert state["last_sequence"] == 130 and state["pending"] is None
    assert [sequence for _, sequence in bridge.calls] == list(range(1, 131))
    assert len({request for request, _ in bridge.calls}) == 130


def test_lost_response_blocks_new_work_until_receipt_is_read_without_retry(tmp_path):
    bridge = Bridge(tmp_path, fail=True)
    with pytest.raises(TimeoutError):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'a' * 32}.json",
               prepared=prepared(tmp_path))
    with pytest.raises(LayoutStreamError, match="unknown outcome"):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'b' * 32}.json",
               prepared=prepared(tmp_path))
    pending, report = recover(tmp_path, bridge)
    assert pending["sequence"] == 1 and report["ok"] is True and len(bridge.calls) == 1
    state = json.loads((tmp_path / "evidence/layout-stream.json").read_text())
    assert state["last_sequence"] == 1 and state["pending"] is None


def test_recovery_does_not_advance_if_transaction_cannot_be_saved(tmp_path):
    bridge = Bridge(tmp_path, fail=True)
    with pytest.raises(TimeoutError):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'a' * 32}.json",
               prepared=prepared(tmp_path))
    def fail(_request, _report):
        raise RuntimeError("cannot save recovered transaction")
    with pytest.raises(RuntimeError, match="cannot save"):
        recover(tmp_path, bridge, resolved=fail)
    state = json.loads((tmp_path / "evidence/layout-stream.json").read_text())
    assert state["last_sequence"] == 0 and state["pending"] is not None
    recover(tmp_path, bridge)
    assert len(bridge.calls) == 1


def test_failed_preparation_stays_pending_and_lock_excludes_other_writers(tmp_path):
    bridge = Bridge(tmp_path)
    with _locked(tmp_path):
        with pytest.raises(LayoutStreamError, match="owns this stream"):
            submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'a' * 32}.json",
                   prepared=prepared(tmp_path))
    def fail(_request):
        raise RuntimeError("cannot persist transaction")
    with pytest.raises(RuntimeError, match="cannot persist"):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'a' * 32}.json", prepared=fail)
    assert bridge.calls == []
    with pytest.raises(LayoutStreamError, match="unknown outcome"):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'b' * 32}.json",
               prepared=prepared(tmp_path))


def test_invalid_plan_is_rejected_before_creating_pending_request(tmp_path):
    bridge = Bridge(tmp_path)
    with pytest.raises(ValueError, match="unsupported action"):
        submit(tmp_path, bridge, {}, {"phases": [{"actions": [{"action_type": "unknown"}]}]},
               transaction=f"transaction-{'a' * 32}.json", prepared=prepared(tmp_path))
    assert not (tmp_path / "evidence/layout-stream.json").exists() and bridge.calls == []


@pytest.mark.parametrize("kind", ["symlink", "duplicate_fields", "lock_symlink"])
def test_unsafe_journal_files_fail_before_submission(tmp_path, kind):
    bridge = Bridge(tmp_path)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    path = evidence / "layout-stream.json"
    if kind == "symlink":
        path.symlink_to(tmp_path / "missing")
    elif kind == "lock_symlink":
        (evidence / ".layout-stream.lock").symlink_to(tmp_path / "missing")
    else:
        path.write_text('{"version":1,"version":1,"stream_id":"aaaaaaaaaaaaaaaa","last_sequence":0,"pending":null}')
    with pytest.raises(LayoutStreamError):
        submit(tmp_path, bridge, {}, {"phases": []}, transaction=f"transaction-{'a' * 32}.json",
               prepared=prepared(tmp_path))
    assert bridge.calls == []
