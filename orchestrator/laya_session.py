# Path: orchestrator/laya_session.py | Purpose: Alias the harness branch's session module name to the single active session implementation.
"""Compatibility alias: the harness branch (6caac895) moved the selected-operation session here,
while Track A moved it to ``orchestrator.operation_session``. One implementation is kept
(operation_session); this name resolves to the same module object."""
import sys

from orchestrator import operation_session

sys.modules[__name__] = operation_session
