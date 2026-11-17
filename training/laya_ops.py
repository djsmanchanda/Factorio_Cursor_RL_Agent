# Path: training/laya_ops.py | Purpose: Expose the shared selected-operation session without importing the strategic runner into training.
"""Compatibility entry point for disposable consumers of deterministic operations.

The orchestrator owns execution, ledgers and geometry; the training runner owns
choice. The session never calls the deterministic mission opener or run loop.
This name is an alias of ``orchestrator.operation_session`` (one module object), so
existing ``from training.laya_ops import DeterministicSession, receipt`` imports and
module-level monkeypatching (``laya_ops.ab``) reach the single implementation.
"""
import sys

from orchestrator import operation_session

sys.modules[__name__] = operation_session
