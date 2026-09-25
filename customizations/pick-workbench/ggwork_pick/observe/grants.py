"""pick_observer's grants in one list (plan TR-12; design 3.4; D3, D14, D15, D16): what migration 0007 gives it on the
ggwp tables and the mirror, and what every mirror publish gives it on a version. `observe.admin regrant` gives them back
and checks them against the catalog.

0007 keeps its own copy of the table lists, since a revision stays as it was written; tests/observe/test_observer_rights.py
keeps the two equal and checks that regrant ends where 0007 does. Objects are named unquoted (schema.table,
schema.table.column), the way the catalog spells them; statements quote them. Nothing here commits: the caller owns the
transaction, and runs it as the tables' owner, deerflow_app.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from typing import NamedTuple

from sqlalchemy import Integer, text

from ggwork_pick.mirror.publish import DEFAULT_OBSERVER_ROLE, OBSERVER_ROLE_ENV, check_schema_name, grant_observer
from ggwork_pick.models import metadata
from ggwork_pick.observe.errors import Refused

# 0007's lists (TR-12): reads, the four columns of the candidate sets (D16), the mirror's two tables (D15). Every other
# ggwp_obs_* and ggwp_gsc_* table the observer writes, ggwp_obs_legacy included (D14).
OBSERVER_READS = ("ggwp_import_batches", "ggwp_drama_versions", "ggwp_alembic_version", "ggwp_obs_decisions")
OBSERVER_CANDIDATE_COLUMNS = ("id", "trends_set_id", "gsc_set_id", "created_at")
OBSERVER_MIRROR = ("versions", "series")
CANDIDATE_SETS = "ggwp_candidate_sets"
WRITE_PREFIXES = ("ggwp_obs_", "ggwp_gsc_")
TABLE_WRITES = ("SELECT", "INSERT", "UPDATE", "DELETE")
SEQUENCE_USE = ("USAGE", "SELECT")
MIRROR_SCHEMA = "pick_mirror"
NOT_OWNER = "当前连接的角色不是工作台表的属主，授不了权：regrant 要在 gateway 容器里用 deerflow_app 的连接（gateway 自己的 PICK_DATABASE_URL）执行"
NO_ROLE = f"观测角色不存在（取自 {OBSERVER_ROLE_ENV}，没设时是 {DEFAULT_OBSERVER_ROLE}）：先执行 bootstrap-observer.sql"
NO_TABLES = "库里缺少观测表或镜像表（迁移 0007 还没执行）：先部署带 0007 的 gateway，再 regrant"

_SEQUENCES = """SELECT q.spelled, n.nspname || '.' || c.relname AS plain
  FROM (SELECT pg_get_serial_sequence(t, 'id') AS spelled FROM unnest(CAST(:tables AS text[])) AS t) q
  JOIN pg_class c ON c.oid = CAST(q.spelled AS regclass) JOIN pg_namespace n ON n.oid = c.relnamespace
 ORDER BY 2"""
_VERSIONS = """SELECT schema_name, to_regclass(quote_ident(schema_name) || '.rs_ids') IS NOT NULL AS has_rs_ids
  FROM pick_mirror.versions WHERE status = 'published' ORDER BY id"""
_OWNED = """SELECT count(*) FILTER (WHERE to_regclass(t) IS NULL) AS missing,
       coalesce(bool_and(pg_has_role(current_user, c.relowner, 'USAGE')), true) AS owner
  FROM unnest(CAST(:tables AS text[])) AS t LEFT JOIN pg_class c ON c.oid = to_regclass(t)"""
_WHO = """SELECT current_user = :r AS itself, current_schema() AS schema,
       coalesce(bool_and(pg_has_role(current_user, n.nspowner, 'USAGE')), false) AS owner
  FROM pg_namespace n WHERE n.nspname IN (current_schema(), 'pick_mirror')"""
# Every grant naming the role, anywhere in this database: the schemas, relations (tables, views, sequences) and columns.
_HELD = """SELECT 'schema', n.nspname, x.privilege_type FROM pg_namespace n, aclexplode(n.nspacl) x WHERE x.grantee = CAST(:r AS regrole)
UNION ALL
SELECT CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'table' END, n.nspname || '.' || c.relname, x.privilege_type
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(c.relacl) x WHERE x.grantee = CAST(:r AS regrole)
UNION ALL
SELECT 'column', n.nspname || '.' || c.relname || '.' || a.attname, x.privilege_type
  FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace, aclexplode(a.attacl) x
 WHERE x.grantee = CAST(:r AS regrole)"""


def observer_writes() -> tuple[str, ...]:
    """Every ggwp_obs_* and ggwp_gsc_* table of the models but the decisions, which the observer only reads (D12, D24)."""
    return tuple(sorted(name for name in metadata.tables if name.startswith(WRITE_PREFIXES) and name not in OBSERVER_READS))


def serial_writes() -> tuple[str, ...]:
    """The written tables whose integer id comes from a sequence the observer needs for INSERT (0007's SERIAL_TABLES)."""
    return tuple(name for name in observer_writes() if isinstance(getattr(metadata.tables[name].c.get("id"), "type", None), Integer))


class Grant(NamedTuple):
    kind: str  # schema, table, column or sequence
    target: str
    privilege: str


@dataclass(frozen=True)
class Layout:
    """Where the grants go in this database: the ggwp schema, its serial sequences and the published mirror versions."""

    schema: str
    sequences: tuple[tuple[str, str], ...]  # (as GRANT spells it, as the catalog does)
    versions: tuple[str, ...]  # published, with an rs_ids
    bare_versions: tuple[str, ...]  # published, without one: nothing to grant there

    def expected(self) -> frozenset[Grant]:
        schema = self.schema
        return frozenset(
            (
                Grant("schema", schema, "USAGE"),
                Grant("schema", MIRROR_SCHEMA, "USAGE"),
                *(Grant("table", f"{schema}.{name}", "SELECT") for name in OBSERVER_READS),
                *(Grant("table", f"{schema}.{name}", privilege) for name in observer_writes() for privilege in TABLE_WRITES),
                *(Grant("column", f"{schema}.{CANDIDATE_SETS}.{column}", "SELECT") for column in OBSERVER_CANDIDATE_COLUMNS),
                *(Grant("sequence", plain, privilege) for _, plain in self.sequences for privilege in SEQUENCE_USE),
                *(Grant("table", f"{MIRROR_SCHEMA}.{name}", "SELECT") for name in OBSERVER_MIRROR),
                *(grant for version in self.versions for grant in (Grant("schema", version, "USAGE"), Grant("table", f"{version}.rs_ids", "SELECT"))),
            )
        )


def _quote(session, name: str) -> str:
    return session.bind.dialect.identifier_preparer.quote(name)


def _qualified(session, schema: str, names: Iterable[str]) -> list[str]:
    return [f"{_quote(session, schema)}.{_quote(session, name)}" for name in names]


async def refuse_unless_ready(session, role: str) -> str:
    """The ggwp schema, once the role exists, this connection owns the tables, and 0007 has run; Refused otherwise."""
    if (await session.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role})).first() is None:
        raise Refused(NO_ROLE)
    who = (await session.execute(text(_WHO), {"r": role})).one()
    if who.itself or who.schema is None or not who.owner:
        raise Refused(NOT_OWNER)
    tables = [*_qualified(session, who.schema, (*OBSERVER_READS, CANDIDATE_SETS, *observer_writes())), *_qualified(session, MIRROR_SCHEMA, OBSERVER_MIRROR)]
    owned = (await session.execute(text(_OWNED), {"tables": tables})).one()
    if owned.missing:
        raise Refused(NO_TABLES)
    if not owned.owner:
        raise Refused(NOT_OWNER)
    return who.schema


async def read_layout(session, schema: str) -> Layout:
    serial = _qualified(session, schema, serial_writes())
    sequences = tuple((row.spelled, row.plain) for row in await session.execute(text(_SEQUENCES), {"tables": serial}))
    rows = [(row.schema_name, row.has_rs_ids) for row in await session.execute(text(_VERSIONS))]
    versions = tuple(check_schema_name(name) for name, has_rs_ids in rows if has_rs_ids)
    bare = tuple(check_schema_name(name) for name, has_rs_ids in rows if not has_rs_ids)
    return Layout(schema=schema, sequences=sequences, versions=versions, bare_versions=bare)


async def grant_all(session, role: str, layout: Layout) -> None:
    """Every grant of the layout, 0007's statements and then the publish's per version; GRANT again changes nothing."""
    grantee, schema = _quote(session, role), _quote(session, layout.schema)

    def tables(names: Iterable[str]) -> str:
        return ", ".join(_qualified(session, layout.schema, names))

    statements = [
        f"GRANT USAGE ON SCHEMA {schema}, {MIRROR_SCHEMA} TO {grantee}",
        f"GRANT SELECT ON {tables(OBSERVER_READS)} TO {grantee}",
        f"GRANT SELECT ({', '.join(OBSERVER_CANDIDATE_COLUMNS)}) ON {schema}.{CANDIDATE_SETS} TO {grantee}",
        f"GRANT {', '.join(TABLE_WRITES)} ON {tables(observer_writes())} TO {grantee}",
        f"GRANT SELECT ON {', '.join(f'{MIRROR_SCHEMA}.{name}' for name in OBSERVER_MIRROR)} TO {grantee}",
    ]
    if layout.sequences:
        statements.append(f"GRANT {', '.join(SEQUENCE_USE)} ON SEQUENCE {', '.join(spelled for spelled, _ in layout.sequences)} TO {grantee}")
    for statement in statements:
        await session.execute(text(statement))
    for version in layout.versions:
        await grant_observer(session, version, role)


async def held(session, role: str) -> frozenset[Grant]:
    return frozenset(Grant(*row) for row in await session.execute(text(_HELD), {"r": role}))


def describe(grants: Iterable[Grant]) -> list[str]:
    """One line per object, its privileges after it, in a stable order."""
    grouped = groupby(sorted(grants), key=lambda grant: (grant.kind, grant.target))
    return [f"  {kind} {target}：{'、'.join(grant.privilege for grant in group)}" for (kind, target), group in grouped]
