# Path: orchestrator/execution_slice.py | Purpose: Let shared operations yield to independent queued work.
from contextlib import contextmanager
from contextvars import ContextVar

_COOPERATIVE = ContextVar('cooperative_execution', default=False)
_CHECKPOINT = ContextVar('execution_checkpoint', default=None)
_PERSIST = ContextVar('execution_persist', default=None)
_RUNTIME = ContextVar('execution_runtime', default=None)


def cooperative() -> bool:
    return _COOPERATIVE.get()


def checkpoint() -> dict | None:
    return _CHECKPOINT.get()


def runtime_checkpoint() -> dict | None:
    return _RUNTIME.get()


def persist_checkpoint():
    save = _PERSIST.get()
    if save is not None:
        save()


@contextmanager
def execution_slice(*, checkpoint=None, persist=None, runtime=None):
    token = _COOPERATIVE.set(True)
    runtime_token = _RUNTIME.set(runtime if runtime is not None else _RUNTIME.get())
    state_token = _CHECKPOINT.set(checkpoint)
    persist_token = _PERSIST.set(persist)
    try:
        yield
    finally:
        _RUNTIME.reset(runtime_token)
        _PERSIST.reset(persist_token)
        _CHECKPOINT.reset(state_token)
        _COOPERATIVE.reset(token)
