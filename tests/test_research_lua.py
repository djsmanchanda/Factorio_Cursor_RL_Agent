# Path: tests/test_research_lua.py
# Purpose: Protect live research option discovery and repeatable-level gating.

from pathlib import Path


MODULE = Path(__file__).resolve().parents[1] / "factorio_mod" / "research.lua"


def test_research_mod_registers_open_options_and_remembers_explicit_target() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'commands.add_command("research_options"' in source
    assert "research_targets()[force.name] = requested_name" in source
    assert 'current_target = remembered' in source
    assert '"queued-next"' in source
    assert 'local active_technology = active and force.technologies[active.name]' in source


def test_research_mod_allows_only_the_immediate_repeatable_level() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert 'requested_level == technology.level + 1' in source
    assert 'return false, "future"' in source
    assert 'if state == "future" then' in source
