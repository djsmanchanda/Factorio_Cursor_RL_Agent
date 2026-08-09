# Path: training/isolation.py
# Purpose: Enforce training identity and dependency firewalls without touching the live runtime.

from __future__ import annotations

import ast
import re
from pathlib import Path

_TRAINING_SURFACE = re.compile(r"^training/[a-z0-9][a-z0-9-]{2,79}$")
_TRAINING_FORCE = re.compile(r"^training-[a-z0-9][a-z0-9-]{2,79}$")
_ACTIVE_FORBIDDEN = ("training", "experimental")
_TRAINING_FORBIDDEN = (
    "experimental",
    "orchestrator.autonomous_builder",
    "orchestrator.stage_",
)


def assert_training_identity(surface_name: str, force_name: str) -> None:
    """Reject any environment that is not an exactly paired training identity."""
    if not isinstance(surface_name, str) or not _TRAINING_SURFACE.fullmatch(surface_name):
        raise ValueError("surface_name must use the strict training/<scenario-id> identity")
    if not isinstance(force_name, str) or not _TRAINING_FORCE.fullmatch(force_name):
        raise ValueError("force_name must use the strict training-<scenario-id> identity")
    if force_name != "training-" + surface_name.removeprefix("training/"):
        raise ValueError("training surface and force identities must share one scenario id")


def _matches_prefix(module_name: str, prefix: str) -> bool:
    if prefix.endswith("_"):
        return module_name.startswith(prefix)
    return module_name == prefix or module_name.startswith(prefix + ".")


def assert_runtime_import_allowed(importer_layer: str, imported_module: str) -> None:
    """Fail closed when a runtime layer attempts a forbidden dependency."""
    if importer_layer not in {"active", "training"}:
        raise ValueError(f"unknown importer layer: {importer_layer}")
    forbidden = _ACTIVE_FORBIDDEN if importer_layer == "active" else _TRAINING_FORBIDDEN
    if any(_matches_prefix(imported_module, prefix) for prefix in forbidden):
        raise ImportError(f"{importer_layer} runtime cannot import {imported_module}")


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return modules


def _active_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for folder in ("core", "planners", "orchestrator"):
        files.extend((repo_root / folder).rglob("*.py"))
    entrypoint = repo_root / "tools" / "autonomous_run.py"
    if entrypoint.exists():
        files.append(entrypoint)
    return sorted(set(files))


def find_import_violations(repo_root: Path) -> list[str]:
    """Return stable, human-readable dependency firewall violations."""
    root = Path(repo_root)
    groups = (("active", _active_python_files(root)), ("training", sorted((root / "training").rglob("*.py"))))
    violations: list[str] = []
    for layer, paths in groups:
        for path in paths:
            for module_name in _imported_modules(path):
                try:
                    assert_runtime_import_allowed(layer, module_name)
                except ImportError as exc:
                    relative = path.relative_to(root).as_posix()
                    violations.append(f"{relative}: {exc}")
    return sorted(set(violations))


def assert_import_firewall(repo_root: Path) -> None:
    """Raise when active and training package dependency directions drift."""
    violations = find_import_violations(repo_root)
    if violations:
        raise ImportError("import firewall violations: " + "; ".join(violations))
