# Path: training/laya_ops.py | Purpose: Preserve the original session module identity for existing consumers.
"""Compatibility alias for the active deterministic session implementation."""
import sys
from orchestrator import laya_session

sys.modules[__name__] = laya_session
