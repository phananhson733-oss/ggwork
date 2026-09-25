"""The start-up self-check of both cron services (plan D5, TR-13; design 3.1; counterexample 15).

Before a collector takes its lease or sends anything, it checks, and refuses with exit 2 (errors.Refused) on a mismatch:
- the collection contract: PICK_OBS_EXPECTED_COLLECTOR equals versions.COLLECTOR_VERSION;
- the migration head: the database's ggwp_alembic_version names a revision this image's own migration chain holds,
  at or after MIN_MIGRATION_HEAD (0007). A newer head the image knows passes, so another session's migration does not
  stop the crons once they are redeployed with it; a head the image does not know means the image is older than the
  database and is refused (the runbook: every new migration in production redeploys the gateway and both crons);
- the role, on PostgreSQL: current_user equals PICK_OBS_EXPECTED_ROLE (pick_observer in production, TR-12);
- the package: a digest of the installed ggwork_pick files goes into the report S6 reads. A non-editable wheel cannot
  point __file__ at the managed snapshot, so the digest replaces design 3.1's __file__ check: compare it with
  package_digest() run on the deployed commit's source.

The chain is read from this package's migrations/versions files with ast, never through alembic: a cron process never
imports alembic (TR-01). A database the check cannot reach or query is StateUnavailable (exit 3), like a runtime row
that cannot be read (D34), and keeps the SQLSTATE: 42501 is a missing grant (regrant). A wait that runs out (a migration
holding the version table) is db.StepTimedOut, exit 1. The check only reads, in a read-only transaction
(test_write_paths).

No observe SQL names a schema: on PostgreSQL the tables are found through the role's search_path, which TR-12's
bootstrap-observer.sql sets to deerflow for pick_observer. A wrong search_path looks like a database without the
migrations, so that refusal names current_schema() (a schema name, nothing else) and points at the role.
"""

import ast
import hashlib
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ggwork_pick.observe.db import ObsDatabase, open_database
from ggwork_pick.observe.errors import Refused, StateUnavailable
from ggwork_pick.observe.versions import COLLECTOR_VERSION, MIN_MIGRATION_HEAD

logger = logging.getLogger(__name__)

EXPECTED_COLLECTOR_VARIABLE = "PICK_OBS_EXPECTED_COLLECTOR"
EXPECTED_ROLE_VARIABLE = "PICK_OBS_EXPECTED_ROLE"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]  # the installed ggwork_pick
VERSIONS_DIR = PACKAGE_ROOT / "migrations" / "versions"
VERSION_TABLE = "ggwp_alembic_version"
MAX_SHOWN = 64  # a head or role read from the database is shown at most this long
_UNREADABLE = (DBAPIError, OSError, TimeoutError)


@dataclass(frozen=True)
class Expectations:
    collector: str
    role: str | None  # required on PostgreSQL; SQLite has no roles


def expectations_from(environ: Mapping[str, str] | None = None) -> Expectations:
    env = os.environ if environ is None else environ
    collector = env.get(EXPECTED_COLLECTOR_VARIABLE, "").strip()
    if not collector:
        raise Refused(f"缺少 {EXPECTED_COLLECTOR_VARIABLE}（本镜像的采集合同版本，见手册）")
    return Expectations(collector, env.get(EXPECTED_ROLE_VARIABLE, "").strip() or None)


@dataclass(frozen=True)
class MigrationChain:
    """Revision -> its parents (none for the first, two for a merge), as this image's migration files declare them."""

    parents: Mapping[str, tuple[str, ...]]

    def knows(self, revision: str) -> bool:
        return revision in self.parents

    def at_or_after(self, revision: str, floor: str) -> bool:
        """Whether `floor` is `revision` or one of its ancestors."""
        seen, pending = set(), [revision]
        while pending:
            current = pending.pop()
            if current == floor:
                return True
            if current not in seen:
                seen.add(current)
                pending.extend(self.parents.get(current, ()))
        return False


def _declared(path: Path) -> dict[str, object]:
    """The module-level `revision` and `down_revision` of a migration file, read without running it."""
    found = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else getattr(node, "target", None)
        value = getattr(node, "value", None)
        if isinstance(target, ast.Name) and target.id in ("revision", "down_revision") and value is not None:
            found[target.id] = ast.literal_eval(value)
    return found


def migration_chain(directory: Path = VERSIONS_DIR) -> MigrationChain:
    parents = {}
    for path in sorted(directory.glob("*.py")):
        declared = _declared(path)
        if isinstance(declared.get("revision"), str):
            down = declared.get("down_revision")
            parents[declared["revision"]] = () if down is None else ((down,) if isinstance(down, str) else tuple(down))
    return MigrationChain(MappingProxyType(parents))


def package_digest(root: Path = PACKAGE_ROOT) -> str:
    """sha256 over every file of the package (relative path, size, bytes), bytecode and dot files left out."""
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and not path.name.startswith(".") and path.suffix not in (".pyc", ".pyo")
    )
    for path in files:
        data = path.read_bytes()
        digest.update(f"{path.relative_to(root).as_posix()}\0{len(data)}\0".encode())
        digest.update(data)
    return f"sha256:{digest.hexdigest()}"


def _shown(value: object) -> str:
    """A head or role from the database, safe for one log line."""
    text_value = "".join(char if char.isprintable() else "?" for char in str(value))
    return repr(text_value[:MAX_SHOWN])


@dataclass(frozen=True)
class SelfCheckReport:
    collector_version: str
    migration_head: str
    role: str | None
    package_digest: str
    package_root: str

    def line(self) -> str:
        """The line S6 reads in the service log: no DSN, no secret."""
        return (
            f"[pick-obs] selfcheck ok: collector={self.collector_version} head={self.migration_head} "
            f"role={self.role or '-'} package={self.package_digest} at={self.package_root}"
        )


async def _has_version_table(conn, postgres: bool) -> bool:
    if postgres:  # through search_path, like the queries after it
        return (await conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": VERSION_TABLE})).scalar_one()
    found = await conn.execute(text("SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = :t"), {"t": VERSION_TABLE})
    return found.scalar_one() > 0


@dataclass(frozen=True)
class _Found:
    role: str | None  # current_user; None on SQLite
    schema: str | None  # current_schema(), where an unqualified name resolves; None when search_path names none
    heads: tuple[str, ...]


async def _read_database(db: ObsDatabase) -> _Found:
    """What the check compares, read in one read-only transaction; StateUnavailable when the database cannot be read."""
    postgres = db.dialect == "postgresql"
    try:
        async with db.transaction(read_only=True) as conn:
            role, schema = (await conn.execute(text("SELECT current_user, current_schema()"))).one() if postgres else (None, None)
            if not await _has_version_table(conn, postgres):
                return _Found(role, schema, ())
            heads = tuple(row[0] for row in (await conn.execute(text(f"SELECT version_num FROM {VERSION_TABLE}"))).all())
    except _UNREADABLE as exc:
        raise StateUnavailable("自检读不了数据库（连不上、没有授权或查询失败），当天不跑") from exc
    return _Found(role, schema, heads)


def _missing_head(postgres: bool, schema: str | None) -> str:
    missing = f"库里没有迁移头（{VERSION_TABLE} 不存在或为空）：库还没迁移到 {MIN_MIGRATION_HEAD}"
    if not postgres:
        return missing
    where = f"当前 schema 是 {_shown(schema)}" if schema is not None else "search_path 里没有一个存在的 schema"
    return f"{missing}，或者连接角色的 search_path 不对（{where}；生产应是 deerflow，由 TR-12 的 bootstrap-observer.sql 为 pick_observer 设置）"


def _check_head(heads: tuple[str, ...], chain: MigrationChain, missing: str) -> str:
    if not heads:
        raise Refused(missing)
    if len(heads) > 1:
        raise Refused(f"库里有 {len(heads)} 个迁移头：本扩展的迁移链只有一条线")
    head = heads[0]
    if not chain.knows(MIN_MIGRATION_HEAD):
        raise Refused(f"本镜像的迁移链里没有 {MIN_MIGRATION_HEAD}：镜像打包不完整")
    if not chain.knows(head):
        raise Refused(f"库的迁移头 {_shown(head)} 不在本镜像的迁移链里：镜像比库旧，按手册重新部署两个 cron")
    if not chain.at_or_after(head, MIN_MIGRATION_HEAD):
        raise Refused(f"库的迁移头 {_shown(head)} 早于 {MIN_MIGRATION_HEAD}：观测表还不在")
    return head


async def run_selfcheck(db: ObsDatabase, expected: Expectations, *, chain: MigrationChain | None = None) -> SelfCheckReport:
    """The check, in the order that needs the least: the contract (no database), then one read-only transaction."""
    if expected.collector != COLLECTOR_VERSION:
        raise Refused(f"{EXPECTED_COLLECTOR_VARIABLE} 与本镜像的采集合同版本不符：镜像不是预期的那一版")
    postgres = db.dialect == "postgresql"
    if postgres and expected.role is None:
        raise Refused(f"缺少 {EXPECTED_ROLE_VARIABLE}（预期的数据库角色，生产是 pick_observer）")
    found = await _read_database(db)
    head = _check_head(found.heads, chain or migration_chain(), _missing_head(postgres, found.schema))
    if postgres and found.role != expected.role:
        raise Refused(f"连接的角色 {_shown(found.role)} 不是 {EXPECTED_ROLE_VARIABLE} 要求的 {_shown(expected.role)}")
    report = SelfCheckReport(COLLECTOR_VERSION, head, found.role, package_digest(), str(PACKAGE_ROOT))
    logger.info(report.line())
    return report


async def selfcheck_only(channel: str, *, environ: Mapping[str, str] | None = None) -> SelfCheckReport:
    """`--selfcheck-only` (TR-14, TR-21; S6): the check on the channel's database, and nothing else."""
    expected = expectations_from(environ)
    db = open_database(channel, environ)
    try:
        return await run_selfcheck(db, expected)
    finally:
        await db.dispose()
