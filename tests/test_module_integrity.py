# Path: tests/test_module_integrity.py
# Purpose: Catch names a module uses but never defines or imports, which unit tests miss when the code path only runs live.

from __future__ import annotations

import ast
import builtins
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    "core", "helper_agent", "orchestrator", "planners", "tools", "training",
)
MODULES = sorted(
    path
    for package in PACKAGES
    for path in (ROOT / package).rglob("*.py")
    if "__pycache__" not in path.parts
)
TEST_MODULES = sorted(
    path for path in (ROOT / "tests").rglob("*.py")
    if "__pycache__" not in path.parts
)


def _defined_names(tree: ast.Module) -> set[str]:
    """Every name bound at module scope, plus builtins."""
    names: set[str] = set(dir(builtins))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, (ast.comprehension,)):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
    return names


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_module_calls_only_names_it_can_resolve(path: Path) -> None:
    """A call to a function that no longer exists is a NameError that only
    surfaces when that exact line runs -- which, for the live-execution paths
    in this project, means mid-build against a real game. Refactors that delete
    a helper while leaving its call sites should fail here instead.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined = _defined_names(tree)
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    missing = sorted(called - defined)
    assert not missing, f"{path.relative_to(ROOT)} calls undefined name(s): {missing}"


def test_tests_rely_on_configured_pythonpath_instead_of_mutating_sys_path() -> None:
    forbidden = tuple("sys.path." + action for action in ("insert", "append"))
    offenders = [
        path.relative_to(ROOT)
        for path in TEST_MODULES
        if any(
            token in path.read_text(encoding="utf-8")
            for token in forbidden
        )
    ]
    assert not offenders, f"test modules mutate sys.path: {offenders}"
