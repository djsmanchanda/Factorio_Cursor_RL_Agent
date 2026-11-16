# Path: orchestrator/operation_facts.py | Purpose: Retain read-only decision evidence in the selected operation's durable invocation.
"""Facts explain an invocation; they never authorize dispatch or clear a fence."""
from contextlib import contextmanager
from contextvars import ContextVar


_sink = ContextVar("operation_fact_sink", default=None)


def enabled():
    return _sink.get() is not None


def record(kind, **facts):
    sink = _sink.get()
    if sink is not None:
        sink({"kind": kind, **facts})


@contextmanager
def capture(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


@contextmanager
def collect(kind):
    """Correlate one predicate's direct read while forwarding durable facts."""
    facts = []
    sink = _sink.get()
    def retain(fact):
        if fact["kind"] == kind:
            facts.append(fact)
        sink(fact)
    with capture(retain if sink is not None else None):
        yield facts
