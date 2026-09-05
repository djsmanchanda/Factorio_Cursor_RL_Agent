# Path: tests/test_test_taxonomy.py | Purpose: Protect pytest marker and state-isolation rules.

from types import SimpleNamespace

from tests import conftest as taxonomy


def test_fixture_reads_are_not_source_tripwires():
    assert not taxonomy._source_tripwire_matches(
        "test_example.py",
        "tests/test_example.py::test_reads_fixture",
        "payload = fixture_path.read_text()",
    )


def test_function_local_source_inspection_is_a_source_tripwire():
    assert taxonomy._source_tripwire_matches(
        "test_example.py",
        "tests/test_example.py::test_checks_source",
        "source = inspect.getsource(target)",
    )


def test_module_level_source_snapshot_only_marks_consumers():
    assert taxonomy._source_tripwire_matches(
        "test_example.py",
        "tests/test_example.py::test_checks_snapshot",
        "assert 'guard' in _SOURCE",
        frozenset({"_SOURCE"}),
    )
    assert not taxonomy._source_tripwire_matches(
        "test_example.py",
        "tests/test_example.py::test_behavior",
        "assert execute() == 'guard'",
        frozenset({"_SOURCE"}),
    )


def test_explicit_lua_source_contract_is_a_source_tripwire():
    assert taxonomy._source_tripwire_matches(
        "test_stock_gating.py",
        "tests/test_stock_gating.py::test_the_executor_applies_the_gate",
        "lua = path.read_text()",
    )
    assert not taxonomy._source_tripwire_matches(
        "test_stock_gating.py",
        "tests/test_stock_gating.py::test_schema_fixture_round_trips",
        "schema = path.read_text()",
    )


def test_mutable_state_restore_preserves_container_identity():
    module = SimpleNamespace(cache={"dirty": 1}, seen={"dirty"}, order=["dirty"])
    original_ids = (id(module.cache), id(module.seen), id(module.order))

    taxonomy._restore_mutable_value(module, "cache", {"clean": 2})
    taxonomy._restore_mutable_value(module, "seen", {"clean"})
    taxonomy._restore_mutable_value(module, "order", ["clean"])

    assert (id(module.cache), id(module.seen), id(module.order)) == original_ids
    assert module.cache == {"clean": 2}
    assert module.seen == {"clean"}
    assert module.order == ["clean"]
