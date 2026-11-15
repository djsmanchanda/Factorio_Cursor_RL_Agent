# Path: tests/test_undefined_names.py | Purpose: Fail the fast gate when runtime code reads a name that no scope defines, the NameError class that reached a 150-run cohort (minimum_machines in _build_assembled_stage).
"""Standard-library undefined-name check for the runtime packages.

Uses ``symtable`` (the compiler's own scope analysis) so it needs no extra
package. A name is flagged when a scope reads it and neither that scope, an
enclosing function, the module, nor the builtins binds it.

What it catches: a function reading a name that only exists in a sibling
function, a typo, a deleted or renamed import or constant.
What it misses (pyflakes would flag some of these): use-before-assignment
inside one function (UnboundLocalError), module-level use before definition,
names bound only on one branch, attributes of modules, and any module that
uses ``from x import *`` (skipped, because its names cannot be known).
"""

import ast
import builtins
import symtable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKED_PACKAGES = ("orchestrator", "planners", "training", "tools", "core")

# Module attributes the interpreter supplies without any binding in the file.
_IMPLICIT_NAMES = frozenset(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__",
    "__loader__", "__builtins__", "__path__", "__class__",
    "__module__", "__qualname__", "__annotations__",
}


def _walk(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _walk(child)


def undefined_names(source: str, filename: str = "<string>") -> list[str]:
    """Return 'scope (line N): name' entries for names no scope defines."""
    tree = ast.parse(source, filename)
    if any(
        isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
        for node in ast.walk(tree)
    ):
        return []
    top = symtable.symtable(source, filename, "exec")
    tables = list(_walk(top))

    defined = set(_IMPLICIT_NAMES)
    for symbol in top.get_symbols():
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace():
            defined.add(symbol.get_name())
    # `global x; x = 1` inside a function defines x for the module.
    for table in tables[1:]:
        for symbol in table.get_symbols():
            if symbol.is_declared_global() and symbol.is_assigned():
                defined.add(symbol.get_name())

    found = []
    for table in tables:
        for symbol in table.get_symbols():
            if not symbol.is_referenced() or symbol.get_name() in defined:
                continue
            resolves_globally = (
                not symbol.is_assigned() and not symbol.is_imported()
                and not symbol.is_namespace()
            ) if table is top else symbol.is_global()
            if resolves_globally:
                found.append(
                    f"{table.get_name()} (line {table.get_lineno()}): "
                    f"{symbol.get_name()}"
                )
    return sorted(set(found))


def test_checker_flags_a_name_read_from_a_sibling_function() -> None:
    # The shape of the minimum_machines incident: the callee reads a name that
    # is only a parameter of its caller.
    source = (
        "def build(machine_count=1):\n"
        "    return max(1, int(minimum_machines))\n"
        "def ensure(minimum_machines):\n"
        "    return build()\n"
    )
    assert undefined_names(source) == ["build (line 1): minimum_machines"]


def test_checker_accepts_names_bound_by_any_legitimate_route() -> None:
    source = (
        "import os\n"
        "from typing import Callable\n"
        "LIMIT = 3\n"
        "def f(a, *rest, key=None, **kw):\n"
        "    total = 0\n"
        "    while True:\n"
        "        if total >= LIMIT:\n"
        "            return remaining, os.sep, rest, kw, key, a\n"
        "        remaining = total\n"
        "        total += 1\n"
        "def g():\n"
        "    global STATE\n"
        "    STATE = 1\n"
        "def h():\n"
        "    inner = 2\n"
        "    return [inner + x for x in range(3)], lambda y: y + inner, STATE\n"
        "class C:\n"
        "    size = 1\n"
        "    def m(self):\n"
        "        return __class__, self.size, len([e for e in (1, 2)])\n"
        "try:\n"
        "    pass\n"
        "except Exception as exc:\n"
        "    print(exc)\n"
    )
    assert undefined_names(source) == []


def test_checker_flags_an_undefined_module_level_name() -> None:
    assert undefined_names("value = missing_constant + 1\n") == [
        "top (line 0): missing_constant"
    ]


def _python_files() -> list[Path]:
    return sorted(
        path
        for package in CHECKED_PACKAGES
        for path in (REPO_ROOT / package).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def test_runtime_packages_have_no_undefined_names() -> None:
    problems = []
    for path in _python_files():
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            pytest.fail(f"{relative} is not valid UTF-8: {exc}")
        problems.extend(
            f"{relative}: {entry}" for entry in undefined_names(source, relative)
        )
    assert not problems, "undefined names (would raise NameError):\n" + "\n".join(problems)
