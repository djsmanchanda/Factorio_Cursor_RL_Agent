# Path: tests/test_laya_machine_count_scope.py | Purpose: Catch the NameError that crashed every smallcell PM attempt.

import inspect

import orchestrator.autonomous_builder as builder


def test_the_stage_builder_takes_machine_count_and_never_reads_minimum_machines():
    params = inspect.signature(builder._build_assembled_stage).parameters
    assert "machine_count" in params
    source = inspect.getsource(builder._build_assembled_stage)
    assert "int(minimum_machines)" not in source
    assert "machine_count=max(1, int(machine_count))" in source
