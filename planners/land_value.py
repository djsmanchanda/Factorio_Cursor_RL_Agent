# Path: planners/land_value.py
# Purpose: Deterministic intrinsic land-value scoring and late-game, shadow-only relocation decisions for production stages.

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

Point = tuple[float, float]

LATE_GAME_SCIENCE_RATE_PER_TICK = 100.0 / 60.0
MIN_RELOCATION_WINDOW_TICKS = 36_000


class StageKind(str, Enum):
    """The land-use role of a production stage."""

    MINING = "mining"
    SMELTING = "smelting"
    CONVERSION = "conversion"
    SCIENCE = "science"
    RESEARCH = "research"
    SPACEPORT = "spaceport"


BLOCK_CATEGORIES = {
    StageKind.MINING: "mining",
    StageKind.SMELTING: "smelting",
    StageKind.CONVERSION: "production",
    StageKind.SCIENCE: "science",
    StageKind.RESEARCH: "science",
    StageKind.SPACEPORT: "infrastructure",
}


class RelocationVerdict(str, Enum):
    """The only permitted migration outcomes; neither directly removes a block."""

    KEEP_IN_PLACE = "keep_in_place"
    BUILD_SHADOW_OUTWARD = "build_shadow_outward"


@dataclass(frozen=True)
class LandValuePolicy:
    """Prices central land according to a stage's strategic value.

    Intrinsic value is 1 at the base centre and falls linearly with Manhattan
    block distance until reaching 0 at ``central_radius_cells``. Placement
    penalties express how far a site's value is from a stage's preferred land
    value, in transport-tile-equivalent units.
    """

    base_center: Point
    block_pitch: float = 64.0
    central_radius_cells: float = 4.0
    penalty_weight_tiles: float = 128.0

    def __post_init__(self) -> None:
        if self.block_pitch <= 0:
            raise ValueError("block_pitch must be positive")
        if self.central_radius_cells <= 0:
            raise ValueError("central_radius_cells must be positive")
        if self.penalty_weight_tiles < 0:
            raise ValueError("penalty_weight_tiles cannot be negative")
        if not all(
            math.isfinite(value)
            for value in (
                *self.base_center,
                self.block_pitch,
                self.central_radius_cells,
                self.penalty_weight_tiles,
            )
        ):
            raise ValueError("land-value policy coordinates and weights must be finite")

    def block_distance(self, point: Point) -> float:
        """Manhattan distance from the base centre, measured in block pitches."""
        if not all(math.isfinite(value) for value in point):
            raise ValueError("point coordinates must be finite")
        return (
            abs(point[0] - self.base_center[0]) + abs(point[1] - self.base_center[1])
        ) / self.block_pitch

    def intrinsic_value(self, point: Point) -> float:
        """Return central land value in the closed interval 0..1."""
        distance = self.block_distance(point)
        return max(0.0, min(1.0, 1.0 - distance / self.central_radius_cells))

    def placement_penalty(
        self,
        kind: StageKind,
        point: Point,
        machine_count: int = 0,
    ) -> float:
        """Cost of assigning ``kind`` to ``point``.

        Mining prefers zero-value outskirts. Ordinary smelting prefers the
        outer edge, while a 20+ machine smelting district is treated as fully
        peripheral and receives stronger pressure away from central land.
        Conversion prefers middle-value land. Science, research, and the
        spaceport prefer progressively central, high-value land.
        """
        if machine_count < 0:
            raise ValueError("machine_count cannot be negative")
        value = self.intrinsic_value(point)
        preferred_value = {
            StageKind.MINING: 0.0,
            StageKind.SMELTING: 0.15,
            StageKind.CONVERSION: 0.55,
            StageKind.SCIENCE: 0.90,
            StageKind.RESEARCH: 1.0,
            StageKind.SPACEPORT: 0.95,
        }[kind]
        strength = 1.0
        if kind is StageKind.SMELTING and machine_count >= 20:
            preferred_value = 0.0
            strength = 1.25
        return self.penalty_weight_tiles * strength * abs(value - preferred_value)


@dataclass(frozen=True)
class DevelopmentMetrics:
    """Measured science maturity used to unlock late-game relocation."""

    required_sciences: tuple[str, ...]
    science_rates_per_tick: Mapping[str, float]
    measurement_window_ticks: int

    def __post_init__(self) -> None:
        if isinstance(self.measurement_window_ticks, bool) or not isinstance(
            self.measurement_window_ticks, int
        ):
            raise ValueError("measurement_window_ticks must be an integer")
        if self.measurement_window_ticks < 0:
            raise ValueError("measurement_window_ticks cannot be negative")
        object.__setattr__(self, "required_sciences", tuple(self.required_sciences))
        object.__setattr__(
            self,
            "science_rates_per_tick",
            MappingProxyType(dict(self.science_rates_per_tick)),
        )

    @property
    def relocation_ready(self) -> bool:
        """True only after every explicitly required science sustains 100/s."""
        if (
            not self.required_sciences
            or self.measurement_window_ticks < MIN_RELOCATION_WINDOW_TICKS
            or any(not science for science in self.required_sciences)
        ):
            return False
        for science in self.required_sciences:
            rate = self.science_rates_per_tick.get(science)
            try:
                measured_rate = float(rate) if rate is not None else math.nan
            except (TypeError, ValueError):
                return False
            if (
                not math.isfinite(measured_rate)
                or measured_rate < LATE_GAME_SCIENCE_RATE_PER_TICK
            ):
                return False
        return True


@dataclass(frozen=True)
class RelocationDecision:
    """A non-destructive decision to keep a stage or build its replacement."""

    kind: StageKind
    current_point: Point
    machine_count: int
    verdict: RelocationVerdict
    target_min_ring: int | None
    reason: str


def evaluate_relocation(
    kind: StageKind,
    current_point: Point,
    machine_count: int,
    policy: LandValuePolicy,
    development: DevelopmentMetrics,
    *,
    higher_value_waiting: bool,
    outer_site_available: bool,
) -> RelocationDecision:
    """Recommend a shadow replacement only for mature, central heavy industry.

    The old block remains in service. A caller may retire it only after the
    outward shadow block is built and independently proven healthy.
    """
    if machine_count < 0:
        raise ValueError("machine_count cannot be negative")
    distance = policy.block_distance(current_point)
    eligible_kind = kind is StageKind.MINING or (
        kind is StageKind.SMELTING and machine_count >= 20
    )
    if not development.relocation_ready:
        reason = "late-game science gate is not satisfied; keep the existing stage"
    elif not higher_value_waiting:
        reason = "no specific higher-value central block is waiting for this land"
    elif not eligible_kind:
        reason = "only mining and smelting districts with at least 20 machines relocate"
    elif distance >= policy.central_radius_cells:
        reason = "stage is already outside the central high-value land radius"
    elif not outer_site_available:
        reason = "no legal outward shadow site is available; keep the working stage"
    else:
        target_ring = math.ceil(policy.central_radius_cells)
        return RelocationDecision(
            kind=kind,
            current_point=current_point,
            machine_count=machine_count,
            verdict=RelocationVerdict.BUILD_SHADOW_OUTWARD,
            target_min_ring=target_ring,
            reason=(
                f"build and validate a shadow {kind.value} block at ring "
                f"{target_ring} or farther before retiring the current block"
            ),
        )
    return RelocationDecision(
        kind=kind,
        current_point=current_point,
        machine_count=machine_count,
        verdict=RelocationVerdict.KEEP_IN_PLACE,
        target_min_ring=None,
        reason=reason,
    )
