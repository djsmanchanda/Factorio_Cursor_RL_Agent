# Path: training/laya_ops.py | Purpose: Compatibility entry point for shared selected operations.
from orchestrator.operation_session import DeterministicSession, receipt

__all__ = ["DeterministicSession", "receipt"]


def __getattr__(name):
    # Older disposable consumers reference the session's diagnostic helpers here.
    from orchestrator import operation_session
    return getattr(operation_session, name)
