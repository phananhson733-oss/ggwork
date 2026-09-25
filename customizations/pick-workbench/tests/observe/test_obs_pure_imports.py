"""TR-10's six modules are pure functions both crons and the gateway import (plan section 5): light, and nothing that
reaches a database, the network or the system clock.

Each probe runs in a fresh interpreter, like TR-01's test_observe_import_is_light, because this process has long since
imported everything through the other tests.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
ROOT = Path(__file__).resolve().parents[4]
OBSERVE = SOURCE / "ggwork_pick" / "observe"
PURE = ("market_map", "link_rules", "eligibility", "wording", "status_rules", "leadtime", "instants")
HEAVY = ("deerflow", "fastapi", "alembic", "langgraph", "dotenv", "sqlalchemy", "httpx", "asyncpg")
ALLOWED_IMPORTS = {
    "dataclasses", "datetime", "json", "math", "re", "statistics", "types", "typing", "collections.abc",
    "ggwork_pick.observe.contract", "ggwork_pick.observe.contract_rows", "ggwork_pick.observe.contract_api",
    *(f"ggwork_pick.observe.{name}" for name in PURE),
}  # fmt: skip


def _probe(module: str) -> list[str]:
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE), str(ROOT / 'backend/packages/extension-api')])}\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("name", PURE)
def test_pure_module_imports_light(name):
    loaded = _probe(f"ggwork_pick.observe.{name}")
    assert sorted(m for m in loaded if any(m == heavy or m.startswith(heavy + ".") for heavy in HEAVY)) == []


@pytest.mark.parametrize("name", PURE)
def test_pure_module_imports_only_data(name):
    """Time is always an explicit input (D10): no clock module, no I/O, no async."""
    tree = ast.parse((OBSERVE / f"{name}.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert imported <= ALLOWED_IMPORTS, imported - ALLOWED_IMPORTS
    assert not [node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)]
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"now", "utcnow", "today"}  # the time module itself is not an allowed import
