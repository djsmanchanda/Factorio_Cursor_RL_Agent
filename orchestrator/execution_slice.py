# Path: orchestrator/execution_slice.py | Purpose: Let shared operations yield to independent queued work.
from contextlib import contextmanager
from contextvars import ContextVar

_COOPERATIVE = ContextVar('cooperative_execution', default=False)


def cooperative() -> bool:
    return _COOPERATIVE.get()


@contextmanager
def execution_slice():
    token = _COOPERATIVE.set(True)
    try:
        yield
    finally:
        _COOPERATIVE.reset(token)
