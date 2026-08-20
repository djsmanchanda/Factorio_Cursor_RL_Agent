# Path: tests/test_research_queue.py
# Purpose: Protect durable ordered research intent and progress semantics.

from pathlib import Path

import pytest

from orchestrator.research_queue import (
    ResearchQueueError,
    load_queue,
    merge_queue,
    new_queue,
    update_item,
    write_queue,
)


def test_queue_round_trip_is_atomic_and_preserves_scope(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "research-queue.json"
    payload = write_queue(path, new_queue(["automation", "logistics"]))

    assert load_queue(path) == payload
    assert [item["technology"] for item in payload["items"]] == ["automation", "logistics"]
    assert not list(path.parent.glob(".research-queue.json.*"))


def test_append_preserves_completed_progress_and_adds_pending_item(tmp_path: Path) -> None:
    path = tmp_path / "research-queue.json"
    write_queue(path, new_queue(["automation"]))
    update_item(path, "automation", "completed")

    payload = merge_queue(path, ["logistics"], mode="append")

    assert [(item["technology"], item["status"]) for item in payload["items"]] == [
        ("automation", "completed"), ("logistics", "pending"),
    ]


def test_replace_resets_existing_progress(tmp_path: Path) -> None:
    path = tmp_path / "research-queue.json"
    write_queue(path, new_queue(["automation"]))
    update_item(path, "automation", "completed")

    payload = merge_queue(path, ["logistics"], mode="replace")

    assert [(item["technology"], item["status"]) for item in payload["items"]] == [
        ("logistics", "pending"),
    ]


@pytest.mark.parametrize(
    ("technologies", "mode", "message"),
    [([], "replace", "at least one"), (["automation", "automation"], "replace", "duplicate"),
     (["Automation"], "replace", "invalid technology"), (["automation"], "unknown", "mode")],
)
def test_queue_rejects_invalid_requests(
    tmp_path: Path, technologies: list[str], mode: str, message: str,
) -> None:
    with pytest.raises(ResearchQueueError, match=message):
        merge_queue(tmp_path / "research-queue.json", technologies, mode=mode)


def test_append_rejects_duplicate_without_overwriting_queue(tmp_path: Path) -> None:
    path = tmp_path / "research-queue.json"
    original = write_queue(path, new_queue(["automation"]))

    with pytest.raises(ResearchQueueError, match="duplicate"):
        merge_queue(path, ["automation"], mode="append")

    assert load_queue(path)["items"] == original["items"]


def test_update_item_clears_old_error_on_retry(tmp_path: Path) -> None:
    path = tmp_path / "research-queue.json"
    payload = new_queue(["automation"])
    payload["items"][0]["status"] = "failed"
    payload["items"][0]["error"] = "temporary"
    write_queue(path, payload)

    updated = update_item(path, "automation", "running")

    assert updated["items"] == [{"technology": "automation", "status": "running"}]
    assert updated["last_error"] is None


def test_queue_rejects_unknown_contract_fields(tmp_path: Path) -> None:
    payload = new_queue(["automation"])
    payload["unexpected"] = True

    with pytest.raises(ResearchQueueError, match="unknown fields"):
        write_queue(tmp_path / "research-queue.json", payload)
