# Path: tests/conftest.py
# Purpose: Share expensive, immutable planner fixtures across behavioral test modules.

from pathlib import Path

import pytest

from planners.electronics_block import build_electronics_block
from planners.electronics_world import load_electronics_world_spec

_FIXTURE = Path(__file__).parent / "fixtures" / "electronics_world_spec.json"


@pytest.fixture(scope="session")
def electronics_world():
    return load_electronics_world_spec(_FIXTURE)


@pytest.fixture(scope="session")
def processing_bundle(electronics_world):
    return build_electronics_block(include_processing=True, world=electronics_world)
