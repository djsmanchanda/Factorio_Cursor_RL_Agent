# Path: core/block_prototypes.py
# Purpose: Single source of truth mapping block types to their placeholder entity prototypes.

from __future__ import annotations

from typing import Dict

BLOCK_PLACEHOLDER_PROTOTYPES: Dict[str, str] = {
    "circuits": "assembling-machine-1",
    "smelting": "stone-furnace",
    "science": "lab",
}

DEFAULT_PLACEHOLDER_PROTOTYPE = "assembling-machine-1"


def placeholder_prototype(block_type: str) -> str:
    return BLOCK_PLACEHOLDER_PROTOTYPES.get(block_type, DEFAULT_PLACEHOLDER_PROTOTYPE)
