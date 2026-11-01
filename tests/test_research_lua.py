# Path: tests/test_research_lua.py
# Purpose: Protect live research option discovery and repeatable-level gating.

from pathlib import Path

import pytest


MODULE = Path(__file__).resolve().parents[1] / "factorio_mod" / "research.lua"


def test_research_mod_registers_open_options_and_remembers_explicit_target() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'commands.add_command("research_options"' in source
    assert "research_targets()[force.name] = requested_name" in source
    assert 'current_target = remembered' in source
    assert '"queued-next"' in source
    assert 'local active_technology = active and force.technologies[active.name]' in source
    assert 'if not technology.researched then' in source


def test_research_mod_allows_only_the_immediate_repeatable_level() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'requested_level == technology.level + 1' in source
    assert 'return false, "future"' in source
    assert 'if state == "future" then' in source


def test_research_status_report_is_named_by_validated_request_id() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'value:match("^[%w_-]+$")' in source
    assert 'if payload.request_id ~= nil and not valid_request_id(payload.request_id)' in source
    assert 'request_id = request_id' in source
    assert 'local suffix = status.request_id or tostring(game.tick)' in source
    assert 'research_status_" .. suffix .. ".json"' in source


def test_research_setter_report_is_correlated_by_validated_request_id() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'report.request_id = request_id' in source
    assert 'local suffix = request_id or tostring(game.tick)' in source
    assert 'helpers.write_file("factorio_mod/research_reports/research_" .. suffix .. ".json"' in source


def test_research_setter_checks_the_observation_guard_before_replacing() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert "assert_research_guard(force, guard)" in source
    assert source.index("assert_research_guard(force, guard)") < source.index(
        "force.research_queue = {}"
    )
    assert 'error("Research queue changed since observation' in source
    assert 'error("Research queue replacement requires replace_queue")' in source


try:
    import lupa
except ImportError:  # pragma: no cover - the source tripwire above still runs
    lupa = None


def _to_lua(runtime, value):
    if isinstance(value, dict):
        table = runtime.table()
        for key, item in value.items():
            table[key] = _to_lua(runtime, item)
        return table
    if isinstance(value, list):
        table = runtime.table()
        for index, item in enumerate(value, 1):
            table[index] = _to_lua(runtime, item)
        return table
    return value


def _lua_runtime():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(
        f'package.path = "{MODULE.parent.as_posix()}/?.lua;" .. package.path'
    )
    runtime.execute("""
      storage = {}
      game = {tick = 100, forces = {}}
      commands = {handlers = {}}
      function commands.add_command(name, description, callback)
        commands.handlers[name] = callback
      end
      helpers = {}
      pending_payload = nil
      last_report = nil
      last_path = nil
      written_paths = {}
      function helpers.json_to_table(_text) return pending_payload end
      function helpers.table_to_json(value) last_report = value; return "{}" end
      function helpers.write_file(path, _json, _append)
        last_path = path
        written_paths[#written_paths + 1] = path
      end
      function build_force(queue_names)
        local technologies = {}
        local function technology(name)
          local tech = {name=name, researched=false, enabled=true, level=1,
                        research_unit_count=0, research_unit_ingredients={}}
          tech.prototype = {level=1, max_level=1}
          technologies[name] = tech
          return tech
        end
        technology("automation")
        technology("logistics")
        local force = {name="player", technologies=technologies, research_queue={},
                       research_progress=0}
        function force.add_research(name)
          local target = technologies[name]
          if not target then return false end
          force.research_queue[#force.research_queue + 1] = target
          return true
        end
        for _, name in ipairs(queue_names) do
          force.research_queue[#force.research_queue + 1] = technologies[name]
        end
        return force
      end
    """)
    runtime.execute(f'dofile("{MODULE.as_posix()}")')
    return runtime


def _install_force(runtime, queue: list[str]) -> None:
    runtime.globals().queue_names = _to_lua(runtime, queue)
    runtime.execute("game.forces.player = build_force(queue_names)")


def _queue_names(runtime) -> str:
    return runtime.eval(
        "(function() local names={} for _,t in ipairs(game.forces.player.research_queue) "
        "do names[#names+1]=t.name end return table.concat(names, ',') end)()"
    )


def _select_with_payload(runtime, payload: dict):
    runtime.globals().pending_payload = _to_lua(runtime, payload)
    runtime.globals().commands.handlers["set_research"](runtime.table(parameter="{}"))
    return runtime.globals().last_report


def test_lua_setter_rejects_an_intervening_queue_change_without_mutation() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, ["logistics"])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "expected_queue": [],
        "replace_queue": False,
    })

    assert report.ok is False
    assert "queue changed since observation" in report.error
    assert _queue_names(runtime) == "logistics"
    assert runtime.eval("storage.research_targets == nil") is True


def test_lua_setter_requires_the_replacement_flag_after_a_matching_guard() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, ["logistics"])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "expected_queue": ["logistics"],
        "replace_queue": False,
    })

    assert report.ok is False
    assert "replacement requires replace_queue" in report.error
    assert _queue_names(runtime) == "logistics"


def test_lua_setter_replaces_only_when_the_guard_and_flag_allow_it() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, ["logistics"])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "expected_queue": ["logistics"],
        "replace_queue": True,
    })

    assert report.ok is True
    assert report.idempotent is False
    assert _queue_names(runtime) == "automation"


def test_lua_setter_without_a_guard_keeps_the_legacy_replacement_behavior() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, ["logistics"])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
    })

    assert report.ok is True
    assert _queue_names(runtime) == "automation"


def test_lua_setter_names_guarded_response_by_request_id() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, [])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "expected_queue": [],
        "request_id": "request42",
    })

    assert report.ok is True
    assert report.request_id == "request42"
    assert runtime.globals().last_path == (
        "factorio_mod/research_reports/research_request42.json"
    )


def test_lua_setter_keeps_same_tick_responses_distinct() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, [])

    _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "request_id": "requestA",
    })
    _select_with_payload(runtime, {
        "technology": "logistics",
        "force": "player",
        "request_id": "requestB",
    })

    assert runtime.eval("table.concat(written_paths, ',')") == (
        "factorio_mod/research_reports/research_requestA.json,"
        "factorio_mod/research_reports/research_requestB.json"
    )


def test_lua_setter_without_request_id_keeps_the_legacy_tick_filename() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, [])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
    })

    assert report.ok is True
    assert runtime.eval("last_report.request_id == nil") is True
    assert runtime.globals().last_path == (
        "factorio_mod/research_reports/research_100.json"
    )


def test_lua_setter_rejects_unsafe_request_id() -> None:
    runtime = _lua_runtime()
    _install_force(runtime, [])

    report = _select_with_payload(runtime, {
        "technology": "automation",
        "force": "player",
        "request_id": "../escape",
    })

    assert report.ok is False
    assert "filename-safe" in report.error
