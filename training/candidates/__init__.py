# Path: training/candidates/__init__.py
# Purpose: Expose deterministic candidate catalogs used by training policies.

from training.candidates.mining_delivery import mining_delivery_candidates

__all__ = ["mining_delivery_candidates", "furnace_refining_candidates"]

from training.candidates.furnace_refining import furnace_refining_candidates
