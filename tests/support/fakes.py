# Path: tests/support/fakes.py
# Purpose: Reusable fake runtime boundaries for tests that must never contact Factorio.

from __future__ import annotations


class RecordingSeedBridge:
    """Record deterministic ore/water seed calls and synthesize mod reports."""

    def __init__(self) -> None:
        self.seed_calls: list[dict] = []

    def seed_ore_patches(
        self, payload: dict, timeout: float = 120.0,
    ) -> dict:
        self.seed_calls.append(payload)
        seeded_by_resource: dict[str, int] = {}
        seeded = 0
        for patch in payload["ore_patches"]:
            tiles = (
                (int(patch["x2"]) - int(patch["x1"]) + 1)
                * (int(patch["y2"]) - int(patch["y1"]) + 1)
            )
            seeded += tiles
            item = patch["item"]
            seeded_by_resource[item] = seeded_by_resource.get(item, 0) + tiles
        return {
            "tick": 0,
            "ok": True,
            "seeded_ore_tiles": seeded,
            "seeded_by_resource": seeded_by_resource,
        }

    def seed_water_lakes(
        self, payload: dict, timeout: float = 120.0,
    ) -> dict:
        self.seed_calls.append(payload)
        tiles = sum(
            (lake["x2"] - lake["x1"] + 1)
            * (lake["y2"] - lake["y1"] + 1)
            for lake in payload["water_lakes"]
        )
        return {"tick": 0, "ok": True, "seeded_water_tiles": tiles}
