# Path: tools/runtime_namespace.py
# Purpose: Single source of truth mapping a runtime selection to one Factorio
# mod namespace (mod name, command prefix, remote interface, script-output root).

"""Two distinctly named runtime mods share one Lua source tree.

`factorio_cursor_rl_agent` (deterministic) keeps the original unprefixed
commands and the `factorio_mod/` script-output root. `factorio_laya_agent`
(Laya) is emitted from the same source with a `laya_` command prefix, its own
remote interface and a `factorio_laya_mod/` script-output root. Every server
enables both, so the client's enabled mod set is identical and joining either
server never prompts "sync mods with server".

This module is the Python half of that contract: callers pass a runtime name
(`deterministic` or `laya`) and resolve the command/output namespace without
hard-coding either name. It is also the documented API for the live-run-kit
stamping tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DETERMINISTIC_RUNTIME = "deterministic"
LAYA_RUNTIME = "laya"

# The logical report root the shared Lua modules hard-code. The deterministic
# runtime writes here verbatim; every other runtime rewrites the prefix.
DETERMINISTIC_OUTPUT_ROOT = "factorio_mod"


@dataclass(frozen=True)
class RuntimeNamespace:
    """One runtime's wire identity, mirrored by `factorio_mod/namespace.lua`."""

    runtime: str
    mod_name: str
    command_prefix: str
    remote_interface: str
    output_root: str
    setting_name: str
    default_active: bool

    def command(self, name: str) -> str:
        """Full console command for a mod-registered command base name."""
        base = name[1:] if name.startswith("/") else name
        return f"/{self.command_prefix}{base}"

    def output_subdir(self, leaf: str | Path) -> Path:
        """Absolute-relative script-output path under this runtime's root."""
        return Path(self.output_root) / Path(leaf)

    def remap_output_subdir(self, deterministic_subdir: str | Path) -> Path:
        """Map a shared `factorio_mod/...` path to this runtime's report root."""
        subdir = Path(deterministic_subdir)
        if self.output_root == DETERMINISTIC_OUTPUT_ROOT:
            return subdir
        try:
            relative = subdir.relative_to(DETERMINISTIC_OUTPUT_ROOT)
        except ValueError:
            return Path(self.output_root) / subdir
        return Path(self.output_root) / relative


RUNTIMES: dict[str, RuntimeNamespace] = {
    DETERMINISTIC_RUNTIME: RuntimeNamespace(
        runtime=DETERMINISTIC_RUNTIME,
        mod_name="factorio_cursor_rl_agent",
        command_prefix="",
        remote_interface="factorio_cursor_rl_agent",
        output_root=DETERMINISTIC_OUTPUT_ROOT,
        setting_name="factorio_cursor_rl_agent_active",
        default_active=True,
    ),
    LAYA_RUNTIME: RuntimeNamespace(
        runtime=LAYA_RUNTIME,
        mod_name="factorio_laya_agent",
        command_prefix="laya_",
        remote_interface="factorio_laya_agent",
        output_root="factorio_laya_mod",
        setting_name="factorio_laya_agent_active",
        default_active=False,
    ),
}

# Every server enables both runtime mods plus the isolated training lab.
RUNTIME_MOD_NAMES: tuple[str, ...] = tuple(ns.mod_name for ns in RUNTIMES.values())


def resolve(runtime: str) -> RuntimeNamespace:
    try:
        return RUNTIMES[runtime]
    except KeyError as error:
        raise ValueError(
            f"unknown runtime {runtime!r}; expected one of {sorted(RUNTIMES)}"
        ) from error


def by_mod_name(mod_name: str) -> RuntimeNamespace:
    for namespace in RUNTIMES.values():
        if namespace.mod_name == mod_name:
            return namespace
    raise ValueError(f"{mod_name!r} is not a runtime mod")


def command_for(runtime: str, name: str) -> str:
    return resolve(runtime).command(name)
