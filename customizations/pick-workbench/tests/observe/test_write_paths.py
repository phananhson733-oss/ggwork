"""TR-13: the collectors write only through LeasedWriter (plan TR-13, critique A-7; counterexample 10).

An import graph over the ggwork_pick sources: from every module under observe/trends and observe/gsc, follow the
first-party imports; nothing reached may import a database driver, an engine factory, the async engine API, the host's
engine or observe.db, except through the sanctioned doors (lease: the leased step; store: TR-20's writers, which take
a leased step; selfcheck: read only). The doors themselves are where the database is, so the walk stops at them.

The runtime half is test_lease.py: a step checks the lease under the row lock, and a finished step's handle is dead.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
PACKAGE = SOURCE / "ggwork_pick"
COLLECTOR_PACKAGES = ("ggwork_pick.observe.trends", "ggwork_pick.observe.gsc")
DOORS = frozenset({"ggwork_pick.observe.lease", "ggwork_pick.observe.store", "ggwork_pick.observe.selfcheck"})
FORBIDDEN_MODULES = (
    "ggwork_pick.observe.db",
    "sqlalchemy.ext.asyncio",
    "sqlalchemy.engine",
    "sqlalchemy.orm",
    "sqlalchemy.pool",
    "asyncpg",
    "aiosqlite",
    "sqlite3",
    "psycopg",
    "psycopg2",
    "deerflow.persistence",
)
ENGINE_FACTORIES = frozenset({"create_engine", "create_async_engine", "engine_from_config", "async_engine_from_config", "create_mock_engine"})


def _module_name(path: Path, root: Path) -> str:
    parts = path.relative_to(root.parent).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def package_sources(root: Path = PACKAGE) -> dict[str, str]:
    return {_module_name(path, root): path.read_text(encoding="utf-8") for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts}


def _resolve(module: str, node: ast.ImportFrom, is_package: bool) -> str:
    if not node.level:
        return node.module or ""
    base = module.split(".") if is_package else module.split(".")[:-1]
    base = base[: len(base) - (node.level - 1)]
    return ".".join([*base, node.module] if node.module else base)


def _node_imports(module: str, node: ast.AST, is_package: bool, known: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """(modules imported, engine factories named) by one node."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}, set()
    if isinstance(node, ast.ImportFrom):
        base = _resolve(module, node, is_package)
        submodules = {f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in known}
        return {base} | submodules, {alias.name for alias in node.names if alias.name in ENGINE_FACTORIES}
    named = node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)
    return set(), ({named} if named in ENGINE_FACTORIES else set())


def imports_of(module: str, source: str, known: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """The modules `module` imports (a `from package import name` counts as package.name when that is a module) and
    the engine factories it names, by name or as an attribute."""
    is_package = any(name.startswith(module + ".") for name in known)
    found = [_node_imports(module, node, is_package, known) for node in ast.walk(ast.parse(source))]
    return set().union(*(modules for modules, _ in found)), set().union(*(factories for _, factories in found))


def _forbidden(name: str) -> bool:
    return any(name == banned or name.startswith(banned + ".") for banned in FORBIDDEN_MODULES)


def violations(sources: Mapping[str, str], collectors: tuple[str, ...] = COLLECTOR_PACKAGES) -> dict[str, list[str]]:
    """For each collector module, what its import closure (stopping at the doors) reaches that it must not."""
    graph = {name: imports_of(name, text, sources) for name, text in sources.items()}
    found = {}
    for start in (name for name in sources if name.startswith(collectors)):
        seen, pending, bad = set(), [start], []
        while pending:
            current = pending.pop()
            if current in seen or current in DOORS:
                continue
            seen.add(current)
            imported, factories = graph.get(current, (set(), set()))
            bad += [f"{current} -> {name}" for name in sorted(imported) if _forbidden(name)]
            bad += [f"{current} names {name}" for name in sorted(factories)]
            pending += [name for name in imported if name in sources]
        if bad:
            found[start] = bad
    return found


def test_collectors_write_only_via_lease():
    """[counterexample 10] No collector module reaches the database except through LeasedWriter (or TR-20's store)."""
    sources = package_sources()
    collectors = [name for name in sources if name.startswith(COLLECTOR_PACKAGES)]
    assert len(collectors) > 10  # the walk has something to walk
    assert violations(sources) == {}
    # The doors are real: the lease module is where observe.db is imported.
    assert "ggwork_pick.observe.db" in imports_of("ggwork_pick.observe.lease", sources["ggwork_pick.observe.lease"], sources)[0]


BYPASSES = {
    "direct_db": "from ggwork_pick.observe import db\n",
    "db_by_relative_import": "from .. import db\n",
    "async_engine_api": "from sqlalchemy.ext.asyncio import AsyncSession\n",
    "engine_factory_by_name": "from sqlalchemy import create_engine\n",
    "engine_factory_by_attribute": "import sqlalchemy as sa\nENGINE = sa.create_async_engine\n",
    "driver": "import asyncpg\n",
    "host_engine": "from deerflow.persistence.engine import get_session_factory\n",
    "through_a_helper": "from ggwork_pick.observe import helper\n",
}


@pytest.mark.parametrize("bypass", sorted(BYPASSES))
def test_the_walk_catches_a_bypass(bypass):
    """The checker is not vacuous: each way around the lease is caught, including one hidden behind a helper."""
    sources = {
        "ggwork_pick.observe.trends.sneaky": BYPASSES[bypass],
        "ggwork_pick.observe.trends": "",
        "ggwork_pick.observe.helper": "from sqlalchemy.ext.asyncio import create_async_engine\n",
        "ggwork_pick.observe.db": "",
        "ggwork_pick.observe": "",
    }
    assert "ggwork_pick.observe.trends.sneaky" in violations(sources)


def test_the_doors_are_not_walked_into():
    sources = {
        "ggwork_pick.observe.trends.run": "from ggwork_pick.observe.lease import LeasedWriter\nfrom ggwork_pick.observe import store\n",
        "ggwork_pick.observe.trends": "",
        "ggwork_pick.observe.lease": "from ggwork_pick.observe.db import ObsDatabase\n",
        "ggwork_pick.observe.store": "from sqlalchemy.ext.asyncio import AsyncConnection\n",
        "ggwork_pick.observe.db": "",
        "ggwork_pick.observe": "",
    }
    assert violations(sources) == {}


# ---- the new modules start light in a cron process (TR-01's rule) -------------------------------------------------

ROOT = SOURCE.parents[1]
HEAVY = ("deerflow.runtime", "deerflow.config.app_config", "fastapi", "alembic", "langgraph", "dotenv")
GATEWAY = ("ggwork_pick.context", "ggwork_pick.routes", "ggwork_pick.service", "ggwork_pick.repository")


@pytest.mark.parametrize(
    "module", ["ggwork_pick.observe.db", "ggwork_pick.observe.lease", "ggwork_pick.observe.selfcheck", "ggwork_pick.observe.admin.cmd_reset_disable"]
)
def test_tr13_modules_import_light(module):
    paths = [str(SOURCE), str(ROOT / "backend/packages/extension-api"), str(ROOT / "backend/packages/harness")]
    code = f"import importlib, json, sys\nsys.path[:0] = {json.dumps(paths)}\nimportlib.import_module({module!r})\nprint(json.dumps(sorted(sys.modules)))\n"
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    assert [name for name in loaded if any(name == heavy or name.startswith(heavy + ".") for heavy in HEAVY)] == []
    assert [name for name in loaded if name in GATEWAY] == []
