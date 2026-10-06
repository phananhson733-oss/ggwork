"""TR-13: the collectors write only through LeasedWriter (plan TR-13, critique A-7; counterexample 10).

An import graph over the ggwork_pick sources: from every module under observe/trends and observe/gsc, follow the
first-party imports; nothing reached may import a database driver, an engine factory, the async engine API, the host's
engine or observe.db, except through the sanctioned doors (lease: the leased step; store: TR-20's writers, which take
a leased step; selfcheck: read only). The doors themselves are where the database is, so the walk stops at them; what
it checks instead is what a module takes from a door. Each door has a list of the names a collector may take, by
`from door import name`, as `door.name`, or with getattr: lease.py imports open_database and ObsDatabase to do its
work, and taking them from there would be observe.db by another name. Nor may a module reach a writer's or a step's
database handle (`._db`, `._engine`, `._conn`) or import anything by a name computed at run time.

The runtime half is test_lease.py: a step checks the lease under the row lock, and a finished step's handle is dead.
"""

import ast
import json
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
PACKAGE = SOURCE / "ggwork_pick"
COLLECTOR_PACKAGES = ("ggwork_pick.observe.trends", "ggwork_pick.observe.gsc")
LEASE_DOOR = "ggwork_pick.observe.lease"
SELFCHECK_DOOR = "ggwork_pick.observe.selfcheck"
STORE_DOOR = "ggwork_pick.observe.store"
# door -> what a collector may take from it. None of it opens a transaction outside a leased step.
DOORS = MappingProxyType(
    {
        LEASE_DOOR: frozenset(
            {
                "LeasedWriter",
                "LeasedStep",
                "ReadStep",
                "LeaseToken",
                "LeaseLost",
                "LeaseHeld",
                "DbStateStore",
                "CollectorSession",
                "collector_session",
                "status_reader",
                "stored_breaker",  # parses a runtime row a ReadStep read (trends preflight); opens nothing
                "StepLimits",
                "LEASE_SECONDS",
                "RENEW_SECONDS",
            }
        ),
        SELFCHECK_DOOR: frozenset({"selfcheck_only", "SelfCheckReport"}),
        # TR-20 registers each name it exports here; every store function takes a LeasedStep (or a ReadStep) first,
        # which test_store_takes_a_step checks.
        STORE_DOOR: frozenset(),
    }
)
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
DATABASE_HANDLES = frozenset({"_db", "_engine", "_conn"})  # LeasedWriter's database, ObsDatabase's engine, a step's connection
DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})
STEP_TYPES = frozenset({"LeasedStep", "ReadStep"})


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


def _dotted(node: ast.AST) -> str | None:
    """`a.b.c` for a chain of names and attributes, else None."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    return ".".join([node.id, *reversed(parts)]) if isinstance(node, ast.Name) else None


@dataclass(frozen=True)
class Facts:
    """What one module's source does that the walk judges."""

    modules: frozenset[str]  # imported (`from package import name` counts as package.name when that is a module)
    factories: frozenset[str]  # engine factories named, by name or as an attribute
    problems: tuple[str, ...]  # door names off the list, database handles, imports by computed name


def _bindings(module: str, tree: ast.AST, is_package: bool, known: Mapping[str, str]) -> dict[str, str]:
    """A local name -> the module it stands for, from every import in the module (wherever it sits)."""
    bound = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bound |= {alias.asname or alias.name.split(".")[0]: alias.name if alias.asname else alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(module, node, is_package)
            bound |= {
                alias.asname or alias.name: f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in known or f"{base}.{alias.name}" in DOORS
            }
    return bound


class _Reader(ast.NodeVisitor):
    def __init__(self, module: str, is_package: bool, known: Mapping[str, str], bound: Mapping[str, str]):
        self.module, self.is_package, self.known, self.bound = module, is_package, known, bound
        self.modules: set[str] = set()
        self.factories: set[str] = set()
        self.problems: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        self.modules |= {alias.name for alias in node.names}

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        base = _resolve(self.module, node, self.is_package)
        self.modules |= {base} | {f"{base}.{alias.name}" for alias in node.names if f"{base}.{alias.name}" in self.known}
        self.factories |= {alias.name for alias in node.names if alias.name in ENGINE_FACTORIES}
        for alias in node.names if base in DOORS else ():
            self._take(base, alias.name)

    def _take(self, door: str, name: str) -> None:
        if name not in DOORS[door]:
            self.problems.append(f"{self.module} takes {door}.{name}")

    def _door_of(self, node: ast.AST) -> str | None:
        dotted = _dotted(node)
        if dotted is None:
            return None
        head, _, rest = dotted.partition(".")
        target = self.bound.get(head)
        full = target if target is None or not rest else f"{target}.{rest}"
        return full if full in DOORS else None

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in ENGINE_FACTORIES:
            self.factories.add(node.attr)
        if node.attr in DATABASE_HANDLES and not (isinstance(node.value, ast.Name) and node.value.id in ("self", "cls")):
            self.problems.append(f"{self.module} reaches .{node.attr}")
        door = self._door_of(node.value)
        if door is not None:
            self._take(door, node.attr)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in ENGINE_FACTORIES:
            self.factories.add(node.id)

    def visit_Call(self, node: ast.Call) -> None:
        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
        if called in DYNAMIC_IMPORTS:
            self.problems.append(f"{self.module} imports by a computed name ({called})")
        if called == "getattr" and node.args and (door := self._door_of(node.args[0])) is not None:
            named = node.args[1] if len(node.args) > 1 else None
            self._take(door, named.value if isinstance(named, ast.Constant) and isinstance(named.value, str) else "<computed>")
        self.generic_visit(node)


def facts_of(module: str, source: str, known: Mapping[str, str]) -> Facts:
    is_package = any(name.startswith(module + ".") for name in known)
    tree = ast.parse(source)
    reader = _Reader(module, is_package, known, _bindings(module, tree, is_package, known))
    reader.visit(tree)
    return Facts(frozenset(reader.modules), frozenset(reader.factories), tuple(reader.problems))


def imports_of(module: str, source: str, known: Mapping[str, str]) -> tuple[set[str], set[str]]:
    """The modules `module` imports and the engine factories it names."""
    facts = facts_of(module, source, known)
    return set(facts.modules), set(facts.factories)


def _forbidden(name: str) -> bool:
    return any(name == banned or name.startswith(banned + ".") for banned in FORBIDDEN_MODULES)


def _judged(current: str, facts: Facts) -> list[str]:
    bad = [f"{current} -> {name}" for name in sorted(facts.modules) if _forbidden(name)]
    return bad + [f"{current} names {name}" for name in sorted(facts.factories)] + list(facts.problems)


def _walk(start: str, graph: Mapping[str, Facts], sources: Mapping[str, str]) -> list[str]:
    seen, pending, bad = set(), [start], []
    while pending:
        current = pending.pop()
        if current in seen or current in DOORS:
            continue
        seen.add(current)
        facts = graph.get(current, Facts(frozenset(), frozenset(), ()))
        bad += _judged(current, facts)
        pending += [name for name in facts.modules if name in sources]
    return bad


def is_collector(name: str, collectors: tuple[str, ...] = COLLECTOR_PACKAGES) -> bool:
    """A module of a collector package (the package itself or below it), not a sibling whose name starts the same."""
    return any(name == package or name.startswith(package + ".") for package in collectors)


def violations(sources: Mapping[str, str], collectors: tuple[str, ...] = COLLECTOR_PACKAGES) -> dict[str, list[str]]:
    """For each collector module, what its import closure (stopping at the doors) reaches that it must not."""
    graph = {name: facts_of(name, text, sources) for name, text in sources.items()}
    found = {start: _walk(start, graph, sources) for start in sources if is_collector(start, collectors)}
    return {start: bad for start, bad in found.items() if bad}


def _bound_by(node: ast.stmt) -> set[str]:
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return {node.name}
    if isinstance(node, ast.Assign | ast.AnnAssign):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return {target.id for target in targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.Import | ast.ImportFrom):
        return {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return set()


def top_level_names(source: str) -> set[str]:
    """What a module binds at its top level: defs, classes, assignments and imports."""
    return set().union(*(_bound_by(node) for node in ast.parse(source).body))


def _annotation_name(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.rsplit(".", 1)[-1]
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", None)


def store_signature_problems(source: str) -> list[str]:
    """Every public function of store.py takes a step first: a LeasedStep to write, a ReadStep to read."""
    problems = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and not node.name.startswith("_"):
            first = (node.args.posonlyargs + node.args.args)[:1]
            if not first or _annotation_name(first[0].annotation) not in STEP_TYPES:
                problems.append(f"store.{node.name} does not take a LeasedStep or ReadStep first")
    return problems


def test_collectors_write_only_via_lease():
    """[counterexample 10] No collector module reaches the database except through LeasedWriter (or TR-20's store)."""
    sources = package_sources()
    collectors = [name for name in sources if is_collector(name)]
    assert len(collectors) > 10  # the walk has something to walk
    assert violations(sources) == {}
    # The doors are real: the lease module is where observe.db is imported.
    assert "ggwork_pick.observe.db" in imports_of(LEASE_DOOR, sources[LEASE_DOOR], sources)[0]


def test_door_lists_name_what_the_doors_hold():
    """Each name a collector may take from a door is one the door binds; a stale list would hide nothing but mislead."""
    sources = package_sources()
    for door, allowed in DOORS.items():
        if door in sources:
            assert allowed <= top_level_names(sources[door]), (door, sorted(allowed - top_level_names(sources[door])))
    # What the list keeps out is there to be taken: lease.py and selfcheck.py hold observe.db's names.
    assert {"open_database", "ObsDatabase"} <= top_level_names(sources[LEASE_DOOR]) - DOORS[LEASE_DOOR]
    assert {"open_database", "ObsDatabase"} <= top_level_names(sources[SELFCHECK_DOOR]) - DOORS[SELFCHECK_DOOR]


def test_store_takes_a_step():
    """TR-20's store writes inside the caller's leased step; its checker is exercised here until store.py exists."""
    good = "async def publish_set(step: LeasedStep, rows):\n    pass\n\nasync def latest(step: 'ReadStep'):\n    pass\n\ndef _helper(x):\n    pass\n"
    assert store_signature_problems(good) == []
    bad = "async def publish_set(conn, rows):\n    pass\n\nasync def prune(*, step: LeasedStep):\n    pass\n"
    assert store_signature_problems(bad) == [
        "store.publish_set does not take a LeasedStep or ReadStep first",
        "store.prune does not take a LeasedStep or ReadStep first",
    ]
    sources = package_sources()
    if STORE_DOOR in sources:
        assert store_signature_problems(sources[STORE_DOOR]) == []


BYPASSES = {
    "direct_db": "from ggwork_pick.observe import db\n",
    "db_by_relative_import": "from .. import db\n",
    "async_engine_api": "from sqlalchemy.ext.asyncio import AsyncSession\n",
    "engine_factory_by_name": "from sqlalchemy import create_engine\n",
    "engine_factory_by_attribute": "import sqlalchemy as sa\nENGINE = sa.create_async_engine\n",
    "driver": "import asyncpg\n",
    "host_engine": "from deerflow.persistence.engine import get_session_factory\n",
    "through_a_helper": "from ggwork_pick.observe import helper\n",
    # observe.db's names, taken from a door that imports them
    "via_lease_reexport": "from ggwork_pick.observe.lease import open_database\n",
    "via_selfcheck_attribute": "from ggwork_pick.observe import selfcheck\nDB = selfcheck.ObsDatabase\n",
    "via_lease_module_alias": "import ggwork_pick.observe.lease as door\ndoor.open_database('trends')\n",
    "via_dotted_door": "import ggwork_pick.observe.lease\nggwork_pick.observe.lease.ObsDatabase\n",
    "via_door_getattr": "from ggwork_pick.observe import lease\nopen_db = getattr(lease, 'open_database')\n",
    "via_door_star": "from ggwork_pick.observe.lease import *\n",
    "via_door_internals": "from ggwork_pick.observe.lease import locked_runtime\n",
    "via_unregistered_store_name": "from ggwork_pick.observe.store import raw_connection\n",
    "via_helper_reexport": "from ggwork_pick.observe import relay\n",
    # a writer's or a step's own handle, and imports by computed name
    "writer_database_handle": "async def sneak(writer):\n    async with writer._db.transaction() as conn:\n        await conn.execute(None)\n",
    "step_connection_handle": "async def sneak(step):\n    await step._conn.commit()\n",
    "import_module": "import importlib\nDB = importlib.import_module('ggwork_pick.observe.' + 'db')\n",
    "dunder_import": "DB = __import__('asyncpg')\n",
}


def _bypass_sources(bypass: str) -> dict[str, str]:
    return {
        "ggwork_pick.observe.trends.sneaky": BYPASSES[bypass],
        "ggwork_pick.observe.trends": "",
        "ggwork_pick.observe.helper": "from sqlalchemy.ext.asyncio import create_async_engine\n",
        "ggwork_pick.observe.relay": "from ggwork_pick.observe.lease import ObsDatabase\n",
        "ggwork_pick.observe.lease": "from ggwork_pick.observe.db import ObsDatabase, open_database\n\nclass LeasedWriter: ...\n",
        "ggwork_pick.observe.selfcheck": "from ggwork_pick.observe.db import ObsDatabase, open_database\n",
        "ggwork_pick.observe.store": "",
        "ggwork_pick.observe.db": "",
        "ggwork_pick.observe": "",
    }


@pytest.mark.parametrize("bypass", sorted(BYPASSES))
def test_the_walk_catches_a_bypass(bypass):
    """The checker is not vacuous: each way around the lease is caught, including one hidden behind a helper."""
    assert "ggwork_pick.observe.trends.sneaky" in violations(_bypass_sources(bypass))


def test_a_sibling_sharing_the_name_is_not_a_collector():
    """A collector is a module of observe/trends or observe/gsc, not one whose name merely starts the same way: the
    gateway's trends table (observe/trends_table.py) reads through the repository like observe/status.py does."""
    reader = "from sqlalchemy.ext.asyncio import AsyncSession\n"
    sources = {**_bypass_sources("async_engine_api"), "ggwork_pick.observe.trends_table": reader, "ggwork_pick.observe.gsc_view": reader}
    assert sorted(violations(sources)) == ["ggwork_pick.observe.trends.sneaky"]
    assert is_collector("ggwork_pick.observe.trends") and is_collector("ggwork_pick.observe.gsc.client")
    assert not is_collector("ggwork_pick.observe.trends_table")


def test_the_doors_are_not_walked_into():
    """What the lists allow passes, however it is spelled; the doors' own imports are theirs."""
    run = (
        "from ggwork_pick.observe.lease import LeasedWriter, StepLimits\n"
        "from ggwork_pick.observe import lease, selfcheck, store\n"
        "import ggwork_pick.observe.lease as door\n"
        "WRITER = lease.LeasedWriter\nSESSION = getattr(door, 'collector_session')\nCHECK = selfcheck.selfcheck_only\n"
        "class Run:\n    def __init__(self, conn):\n        self._conn = conn\n"
    )
    sources = {
        "ggwork_pick.observe.trends.run": run,
        "ggwork_pick.observe.trends": "",
        "ggwork_pick.observe.lease": "from ggwork_pick.observe.db import ObsDatabase\n",
        "ggwork_pick.observe.selfcheck": "from ggwork_pick.observe.db import open_database\n",
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
