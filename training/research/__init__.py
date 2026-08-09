# Path: training/research/__init__.py
# Purpose: Expose bounded local-LLM research proposal contracts.

from training.research.controller import AutoresearchController, has_plateau
from training.research.proposals import PolicyProposal, apply_proposal, validate_proposal

__all__ = [
    "AutoresearchController", "PolicyProposal", "apply_proposal", "has_plateau",
    "validate_proposal",
]
