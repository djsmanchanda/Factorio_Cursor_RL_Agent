# Path: tests/test_laya_zero_provider_wiring.py | Purpose: Catch a zero_provider that never reaches the cell builder.

import inspect

import orchestrator.autonomous_builder as builder


def test_zero_provider_is_threaded_to_every_stage_of_the_chain():
    for fn in (builder.ensure_produced, builder._build_assembled_stage,
               builder.build_conversion_stage):
        assert "zero_provider" in inspect.signature(fn).parameters, fn.__name__
    source = inspect.getsource(builder.ensure_produced)
    assert "zero_provider=zero_provider" in source
    assert "_build_assembled_stage(" in source
    stage_source = inspect.getsource(builder._build_assembled_stage)
    assert "zero_provider=zero_provider" in stage_source
