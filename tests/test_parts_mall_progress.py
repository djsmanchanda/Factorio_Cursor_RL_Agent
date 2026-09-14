# Path: tests/test_parts_mall_progress.py
# Purpose: Prove a growing mall line is not mistaken for stalled capacity on a fixed timer.

from __future__ import annotations

import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import parts_mall  # noqa: E402


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_wait_for_stock_postpones_expansion_while_stock_keeps_growing(monkeypatch) -> None:
    clock = _Clock()
    expansions: list[float] = []
    monkeypatch.setattr(parts_mall.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(parts_mall.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        parts_mall.live_base,
        "available_items",
        lambda *_a, **_k: {"electric-mining-drill": min(4, int(clock.now / 30) + 1)},
    )

    ready = parts_mall.wait_for_stock(
        object(), "nauvis", "player", "electric-mining-drill", 4,
        lambda _message: None, poll_seconds=30.0, report_seconds=999.0,
        expansion_seconds=60.0,
        on_stalled=lambda: expansions.append(clock.now) is None or True,
    )

    assert ready is True
    assert expansions == []


def test_wait_for_stock_expands_after_a_full_window_without_progress(monkeypatch) -> None:
    clock = _Clock()
    expansions: list[float] = []
    monkeypatch.setattr(parts_mall.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(parts_mall.time, "sleep", clock.sleep)
    monkeypatch.setattr(
        parts_mall.live_base,
        "available_items",
        lambda *_a, **_k: {"electric-mining-drill": 1},
    )

    ready = parts_mall.wait_for_stock(
        object(), "nauvis", "player", "electric-mining-drill", 4,
        lambda _message: None, poll_seconds=30.0, report_seconds=999.0,
        expansion_seconds=60.0,
        on_stalled=lambda: expansions.append(clock.now) is not None,
    )

    assert ready is False
    assert expansions == [60.0]


def test_wait_for_stock_yields_after_one_successful_remediation(monkeypatch) -> None:
    clock = _Clock()
    expansions: list[float] = []
    monkeypatch.setattr(parts_mall.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(parts_mall.time, "sleep", clock.sleep)
    monkeypatch.setattr(parts_mall.live_base, "available_items", lambda *_a: {"inserter": 0})

    ready = parts_mall.wait_for_stock(
        object(), "nauvis", "player", "inserter", 1, lambda _message: None,
        poll_seconds=30.0, report_seconds=999.0, expansion_seconds=60.0,
        on_stalled=lambda: expansions.append(clock.now) is None or True,
    )

    assert ready is False
    assert expansions == [60.0]


def test_wait_for_stock_can_require_transferable_stock(monkeypatch) -> None:
    reads = iter(({"iron-stick": 12}, {"iron-stick": 0}, {"iron-stick": 4}))
    monkeypatch.setattr(parts_mall.time, "sleep", lambda _seconds: None)

    ready = parts_mall.wait_for_stock(
        object(), "nauvis", "player", "iron-stick", 4,
        lambda _message: None, stock_reader=lambda: next(reads),
    )

    assert ready is True


def test_mall_expansion_shortage_is_reported_before_the_pass_yields() -> None:
    source = inspect.getsource(builder._serve_mall_task)
    wait_clause = source[source.index("ready = wait_for_stock"):]

    assert "MALL EXPANSION DEMAND" in wait_clause
