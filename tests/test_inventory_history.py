# Path: tests/test_inventory_history.py
# Purpose: Prove inventory charts reset at runner boundaries and retain only five runs.

from pathlib import Path

from tools.inventory_history import InventoryHistory, MAX_RETAINED_RUNS


def _report(tick: int, **items: int) -> dict:
    return {"ok": True, "tick": tick, "total_items": items}


def _start(log: Path, timestamp: str, target: str) -> None:
    with log.open("a", encoding="utf-8") as handle:
        handle.write(f"RUN START: ts={timestamp} command=research target={target}\n")


def test_inventory_history_starts_a_fresh_series_for_each_runner_run(tmp_path: Path) -> None:
    log = tmp_path / "autonomous-run.log"
    history = InventoryHistory(tmp_path / "inventory-history.json", log)
    _start(log, "2026-09-08T10:00:00+00:00", "one")

    history.record(_report(100, **{"iron-plate": 4}))
    history.record(_report(200, **{"iron-plate": 7}))
    _start(log, "2026-09-08T11:00:00+00:00", "two")
    history.record(_report(100, **{"iron-plate": 1}))

    runs = history.view()["runs"]
    assert [run["id"] for run in runs] == [
        "2026-09-08T10:00:00+00:00", "2026-09-08T11:00:00+00:00",
    ]
    assert [sample["items"]["iron-plate"] for sample in runs[0]["samples"]] == [4, 7]
    assert [sample["items"]["iron-plate"] for sample in runs[1]["samples"]] == [1]


def test_inventory_history_keeps_all_items_at_each_distinct_tick_and_only_five_runs(
    tmp_path: Path,
) -> None:
    log = tmp_path / "autonomous-run.log"
    history = InventoryHistory(tmp_path / "inventory-history.json", log)
    for index in range(MAX_RETAINED_RUNS + 1):
        _start(log, f"2026-09-08T{index:02d}:00:00+00:00", str(index))
        history.record(_report(100, **{"iron-plate": index, "copper-plate": index + 1}))
        history.record(_report(100, **{"iron-plate": 999}))

    runs = history.view()["runs"]
    assert len(runs) == MAX_RETAINED_RUNS
    assert runs[0]["label"].endswith("target=1")
    assert runs[-1]["label"].endswith(f"target={MAX_RETAINED_RUNS}")
    assert len(runs[-1]["samples"]) == 1
    assert runs[-1]["samples"][0]["items"] == {
        "iron-plate": MAX_RETAINED_RUNS,
        "copper-plate": MAX_RETAINED_RUNS + 1,
    }
