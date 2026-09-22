# Path: tests/test_laya_failure.py
# Purpose: Verify bounded failure replay packets and the offline proposal ledger.

from __future__ import annotations

import json
from pathlib import Path

import pytest

from training.laya_failure import (
    FailureCause,
    FailurePacket,
    ImprovementLedger,
    ImprovementProposal,
    LayaFailureError,
    LayaLedgerError,
)


def _packet() -> FailurePacket:
    return FailurePacket.create(
        cause=FailureCause.POLICY,
        failure_kind="timeout",
        summary="Selected a route that never delivered to the sink.",
        objective={"goal_id": "goal-iron", "gap": 30.0, "target": 60.0},
        replay_request={
            "scenario": {"family": "mining_delivery"},
            "seed": 17,
            "initial_state": {"surface": "training/17"},
            "decision_context": {"decision_kind": "select_route", "selected_option": "route-b"},
            "variations": [{"variation_id": "baseline"}, {"variation_id": "shorter-route"}],
            "limits": {"max_attempts": 4, "max_ticks": 1200},
        },
        provenance={
            "run_id": "run-1", "episode_id": "episode-1", "trajectory_id": "trajectory-1",
            "scenario_id": "scenario-1", "scenario_seed": 17,
            "guideline_pack": {"id": "pack", "version": "1.0.0"},
            "tool_registry": {"id": "tools", "version": "1.0.0"},
            "model": {"id": "laya", "version": "adapter-1"},
        },
        evidence={"decision_trace": [{"kind": "select_route"}], "execution": {"status": "failed"}, "outcome": {"delivered": 0}},
        packet_id="failure-1",
    )


def test_failure_packet_is_disposable_bounded_and_provenance_carrying() -> None:
    packet = _packet()
    record = packet.to_dict()
    assert record["cause"] == "policy"
    assert record["replay_request"]["runtime"] == "disposable_training"
    assert record["provenance"]["source_hash"].startswith("sha256:")
    assert FailurePacket.from_mapping(json.loads(packet.serialized())).packet_hash == packet.packet_hash


def test_failure_packet_rejects_real_runtime_and_oversized_evidence() -> None:
    with pytest.raises(LayaFailureError, match="disposable_training"):
        FailurePacket.create(
            cause="tool", failure_kind="execution", summary="bad", objective={"goal": "x"},
            replay_request={"runtime": "real_nauvis"},
        )
    with pytest.raises(LayaFailureError, match="4096"):
        FailurePacket.create(
            cause="observation", failure_kind="unknown", summary="bad", objective={"goal": "x"},
            replay_request={"decision_context": {"raw": "x" * 4097}},
        )


def test_proposal_is_derived_without_applying_the_change(tmp_path: Path) -> None:
    proposal = ImprovementProposal.create(
        packet=_packet(), summary="Add route context to the observation.",
        rationale="The selected route was legal but the observation omitted the blockage.",
        target="laya_state.observation_digest", description="Expose top route blockages.",
        required_tests=["test_route_blockage_digest"], kind=FailureCause.OBSERVATION,
    )
    assert proposal.cause is FailureCause.OBSERVATION
    assert proposal.to_dict()["status"] == "proposed"
    assert proposal.to_dict()["evidence"]["packet_hash"].startswith("sha256:")

    ledger = ImprovementLedger(tmp_path / "improvement-proposals.jsonl")
    stored = ledger.append(proposal)
    assert stored["proposal_id"] == proposal.proposal_id
    assert ledger.read(packet_id="failure-1")[0]["cause"] == "observation"
    with pytest.raises(LayaLedgerError, match="duplicate"):
        ledger.append(proposal)


def test_ledger_rejects_corrupt_records_and_invalid_cause(tmp_path: Path) -> None:
    path = tmp_path / "proposals.jsonl"
    ledger = ImprovementLedger(path)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("not-json\n")
    with pytest.raises(LayaLedgerError, match="line 1"):
        ledger.read()
    with pytest.raises(LayaFailureError, match="unknown failure cause"):
        FailurePacket.create(
            cause="secret", failure_kind="unknown", summary="bad", objective={"goal": "x"}, replay_request={}
        )
