# Path: tests/test_inserter_throughput_reference.py
# Purpose: Keep the supplied Factorio 2.0.26 throughput evidence complete and explicitly separate from active conservative planner rates.

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE = (
    REPO_ROOT / "docs" / "reference"
    / "inserter_throughput_factorio_2_0_26.txt"
)


def test_the_complete_reference_keeps_each_transfer_geometry() -> None:
    text = REFERENCE.read_text(encoding="utf-8")

    for heading in (
        "Chest to chest",
        "Chest to belt",
        "Chest to splitter",
        "Belt to chest (perpendicular)",
    ):
        assert heading in text


def test_the_reference_keeps_every_inserter_tier() -> None:
    text = REFERENCE.read_text(encoding="utf-8")

    for tier in (
        "Burner inserter", "Inserter", "Long-handed inserter",
        "Fast inserter", "Bulk inserter", "Stack inserter",
    ):
        assert tier in text


def test_the_reference_records_its_version_and_experimental_status() -> None:
    text = REFERENCE.read_text(encoding="utf-8")

    assert "Factorio 2.0.26 experimental data" in text

