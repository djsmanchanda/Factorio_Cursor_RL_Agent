# Path: training/laya_ops.py | Purpose: Expose the shared selected-operation session without importing the strategic runner into training.
"""Compatibility entry point for disposable consumers of deterministic operations.

The orchestrator owns execution, ledgers and geometry; the training runner owns
choice. The session never calls the deterministic mission opener or run loop.
"""
from orchestrator.operation_session import DeterministicSession, receipt

__all__ = ["DeterministicSession", "receipt"]
