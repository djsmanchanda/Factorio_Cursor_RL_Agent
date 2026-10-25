# Path: training/laya_acceptance.py | Purpose: Compatibility exports for shared checkpoint evidence.
from orchestrator.checkpoint_evidence import (
    VERSION, acceptance_packet, summary_details, condition_counts,
)

__all__ = ["VERSION", "acceptance_packet", "summary_details", "condition_counts"]
