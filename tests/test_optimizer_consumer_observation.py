# Path: tests/test_optimizer_consumer_observation.py | Purpose: Keep consumer probes in the bound disposable scope.
from dataclasses import replace

import pytest

from tests.test_optimizer_consumer_window import report
from tests.test_optimizer_saved_world import world
from tools import optimizer_consumer_observation as probe


@pytest.fixture
def prepared(world, monkeypatch):
    adapter, bound, ledger = world
    monkeypatch.setattr(probe, "owned_root", lambda root: root.resolve())
    monkeypatch.setattr(probe, "verify_instance", lambda _root, **_kwargs: {"runtime": "disposable_saved_world"})
    calls = []
    class Bridge:
        def optimizer_consumer_observation(self, **kwargs):
            calls.append(kwargs)
            return report(100, 7, "probe")
    adapter.bridge = Bridge()
    return adapter, bound, ledger, calls


def observe(prepared):
    adapter, bound, _ledger, _calls = prepared
    return probe.observe_bound_consumer(adapter, bound, entity="chemical-plant",
                                        position={"x": 1.5, "y": 2.5}, unit_number=42)


def test_observation_uses_bound_disposable_scope_without_claiming_commission(prepared):
    adapter, bound, _ledger, calls = prepared
    result = observe(prepared)
    assert calls == [{"surface": "nauvis", "force": "player", "entity": "chemical-plant",
                      "position": {"x": 1.5, "y": 2.5}, "unit_number": 42}]
    assert result["binding"] == bound.plan.binding.to_dict() and result["report"]["tick"] == 100
    assert result["ownership_claimed"] is result["commissioned"] is result["dispatch_authorized"] is False


@pytest.mark.parametrize("change", ["manifest", "symlink", "binding", "runtime"])
def test_wrong_scope_or_instance_fails_before_consumer_probe(prepared, monkeypatch, change, tmp_path):
    adapter, bound, ledger, calls = prepared
    if change == "manifest":
        (adapter.root / "manifest.json").write_text("changed")
    elif change == "symlink":
        manifest = adapter.root / "manifest.json"
        target = tmp_path / "elsewhere.json"
        target.write_bytes(manifest.read_bytes())
        manifest.unlink()
        manifest.symlink_to(target)
    elif change == "binding":
        bound = replace(bound, canonical_binding=bound.canonical_binding + b" ")
    else:
        def fail(_root, **_kwargs): raise ValueError("instance mismatch")
        monkeypatch.setattr(probe, "verify_instance", fail)
    with pytest.raises(ValueError):
        observe((adapter, bound, ledger, calls))
    assert calls == []
