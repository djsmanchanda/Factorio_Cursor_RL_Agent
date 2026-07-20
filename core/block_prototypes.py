# Path: core/block_prototypes.py
# Purpose: Single source of truth mapping block types to their placeholder entity prototypes.

from __future__ import annotations

from typing import Dict

# Electric-only invariant (docs/20 §12): no fuel-burning placeholders.
BLOCK_PLACEHOLDER_PROTOTYPES: Dict[str, str] = {
    "circuits": "assembling-machine-1",
    "smelting": "electric-furnace",
    "science": "lab",
}

DEFAULT_PLACEHOLDER_PROTOTYPE = "assembling-machine-1"

# Tile footprints (square side) of the placeholder prototypes. Zone strides
# must exceed the footprint or adjacent ghosts overlap and cannot be revived.
PROTOTYPE_FOOTPRINTS: Dict[str, int] = {
    "assembling-machine-1": 3,
    "electric-furnace": 3,
    "lab": 3,
}

DEFAULT_PROTOTYPE_FOOTPRINT = 3


def placeholder_prototype(block_type: str) -> str:
    return BLOCK_PLACEHOLDER_PROTOTYPES.get(block_type, DEFAULT_PLACEHOLDER_PROTOTYPE)


def placeholder_stride(block_type: str, spacing: int = 1) -> int:
    prototype = placeholder_prototype(block_type)
    footprint = PROTOTYPE_FOOTPRINTS.get(prototype, DEFAULT_PROTOTYPE_FOOTPRINT)
    return footprint + spacing
