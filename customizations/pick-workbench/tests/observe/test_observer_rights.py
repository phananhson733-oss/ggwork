"""pick_observer as the cron services log in (plan TR-12; design 3.4; D3, D14, D15, D16).

The rights come from three places: bootstrap-observer.sql (or bootstrap.sql on a new project) for the database and the
two schemas, migration 0007 for the tables, and every mirror publish (or `observe.admin regrant`) for each version's
rs_ids. These tests log in as the observer on a stand-in for Supabase (supabase_stand_in.py), after the migrations ran
as deerflow_app the way production's do; the PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import io
import re

import pg
import pytest
import pytest_asyncio
from engines import host_engine
from mirror_pairs import NO_ACCEPT_EMPTY, building_version, now, open_service, stage_pair, version
from obs_schema import OBS_TABLES, filler, migration_module
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from supabase_stand_in import (
    OBSERVER,
    OBSERVER_ROLE_ENV,
    OBSERVER_RUNBOOK,
    P0_SCRIPT,
    bootstrap,
    expected_output,
    host_tables,
    migrate_as_app,
    stand_in_for,
)

OBSERVER_READS = ("ggwp_import_batches", "ggwp_drama_versions", "ggwp_alembic_version", "ggwp_obs_decisions")
OBSERVER_WRITES = tuple(sorted(OBS_TABLES - {"ggwp_obs_decisions"}))
CANDIDATE_COLUMNS = ("id", "trends_set_id", "gsc_set_id", "created_at")
DENIED = "permission denied"


@pytest_asyncio.fixture
async def observed(pg_cluster, tmp_path, monkeypatch):
    """A project the way production stands after S4: bootstrap, the whole chain as deerflow_app, the host's own tables."""
    with stand_in_for(pg_cluster, tmp_path, monkeypatch) as stand_in:
        bootstrap(stand_in)
        stand_in.set_passwords(stand_in.app, stand_in.reader, stand_in.observer)
        await migrate_as_app(stand_in, tmp_path / "data")
        host_tables(stand_in)
        yield stand_in


async def _refused(engine, sql: str, match: str = DENIED) -> None:
    async with engine.connect() as conn:
        with pytest.raises(DBAPIError, match=match):
            await conn.execute(text(sql))


async def _count(engine, sql: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(sql))).scalar_one()


async def _write_every_table(engine) -> None:
    """INSERT, UPDATE and DELETE on each of the twenty tables, the serial ids through their sequences; rolled back."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            for name in OBSERVER_WRITES:
                table, row = filler(name)
                if name != "ggwp_obs_runtime":  # its two rows come with 0007 (D34)
                    await conn.execute(table.insert(), row)
                column = next(iter(table.primary_key.columns))
                changed = await conn.execute(table.update().values({column.name: column}))
                removed = await conn.execute(table.delete())
                assert changed.rowcount == removed.rowcount >= 1, name
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_observer_rights(observed):
    engine = host_engine(observed.url_as(observed.observer))
    try:
        await _write_every_table(engine)
        for name in OBSERVER_READS:
            assert await _count(engine, f"select count(*) from {name}") >= 0
        await _refused(engine, "insert into ggwp_obs_decisions (id) values (1)")
        await _refused(engine, "delete from ggwp_import_batches")
        # Sessions, checkpoints, users, the selections and everything else of the workbench are out of reach.
        for name in ("ggwp_selections", "ggwp_selection_commands", "ggwp_knowledge_versions", "ggwp_sync_runs", "ggwp_answer_checks"):
            await _refused(engine, f"select count(*) from {name}")
        for name in ("users", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            await _refused(engine, f"select secret from deerflow.{name}")
        # pick_obs is the reader's (design 3.6), never the observer's.
        await _refused(engine, "select count(*) from pick_obs.sets", "permission denied for schema pick_obs")
        # D16: four columns of the candidate sets, enough to tell whether a set is still referenced.
        assert await _count(engine, f"select count(*) from (select {', '.join(CANDIDATE_COLUMNS)} from ggwp_candidate_sets) s") == 0
        await _refused(engine, "select owner_id from ggwp_candidate_sets")
        await _refused(engine, "select * from ggwp_candidate_sets")
        # D15: the mirror's versions and series, not its control rows.
        assert await _count(engine, "select count(*) from pick_mirror.versions") == 0
        assert await _count(engine, "select count(*) from pick_mirror.series") == 0
        await _refused(engine, "select * from pick_mirror.control")
        await _refused(engine, "select * from pick_mirror.series_state")
    finally:
        await engine.dispose()


async def _publish(engine, shared, importer, tag: str) -> str:
    """A paired publish through the repository, as the mirror run makes it: the version carries an rs_ids table."""
    staged = await stage_pair(importer, tag)
    version_id, schema = await building_version(engine)
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {schema}.rs_ids (id text NOT NULL, canonical_id text)"))
        await conn.execute(text(f"INSERT INTO {schema}.rs_ids VALUES ('rs-{tag}', NULL)"))
    await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), **NO_ACCEPT_EMPTY)
    return schema


@pytest.mark.asyncio
async def test_publish_grants_observer_rs_ids(observed, tmp_path):
    engine, _, shared, importer = await open_service(observed.url_as(observed.app), tmp_path / "files")
    observer = host_engine(observed.url_as(observed.observer))
    try:
        schema = await _publish(engine, shared, importer, "a")
        assert await _count(observer, f"select count(*) from {schema}.rs_ids") == 1
        # Nothing else of the version: its meta, and whatever the writer adds to a version later.
        await _refused(observer, f"select * from {schema}.meta")
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE TABLE {schema}.rs_later (x int)"))
        await _refused(observer, f"select * from {schema}.rs_later")
        # The reader keeps the whole version, as before (plan 5.2 step 1).
        reader = host_engine(observed.url_as(observed.reader))
        try:
            assert await _count(reader, f"select count(*) from {schema}.meta") == 0
        finally:
            await reader.dispose()
    finally:
        await observer.dispose()
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_version_without_rs_ids_still_publishes_and_regrant_reports_it(observed, tmp_path):
    """A missing rs_ids never fails a pair: a writer change that drops it leaves the version to the reader alone. But the
    observer cannot resolve that version's non-canonical ids (D15, D36), so regrant and --check both exit 1 over it: S4
    must not pass on "授权齐全" alone."""
    from ggwork_pick.observe.admin.cmd_regrant import regrant

    engine, _, shared, importer = await open_service(observed.url_as(observed.app), tmp_path / "files")
    try:
        staged = await stage_pair(importer, "a")
        version_id, schema = await building_version(engine)
        await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), **NO_ACCEPT_EMPTY)
        assert (await version(engine, version_id))["status"] == "published"
    finally:
        await engine.dispose()
    assert not observed.usage(observed.observer, schema)
    for check in (True, False):
        out = io.StringIO()
        assert await regrant(_dsn(observed), observed.observer, check=check, out=out) == 1, out.getvalue()
        assert f"已发布但没有 rs_ids 的镜像版本 1 个（观测侧解析不了它的非正典 id，要与维护镜像的会话核对）：{schema}" in out.getvalue()
    # Everything else is in place: the one line is what the exit status is about.
    assert "缺少授权" not in out.getvalue() and "越权" not in out.getvalue()
    assert not observed.usage(observed.observer, schema)


@pytest.fixture
def absent_observer(monkeypatch):
    monkeypatch.setenv(OBSERVER_ROLE_ENV, "pick_observer_absent_5d2e8a")


@pytest.mark.asyncio
async def test_publish_skips_a_missing_observer_role(absent_observer, pg_db_url, tmp_path):
    engine, _, shared, importer = await open_service(pg_db_url, tmp_path)
    try:
        schema = await _publish(engine, shared, importer, "a")
        version_id = int(schema.removeprefix("pickm_v"))
        assert (await version(engine, version_id))["status"] == "published"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["Pick_Observer", 'x"; DROP', "o" * 64])
@pytest.mark.asyncio
async def test_publish_refuses_a_malformed_observer_role_before_any_sql(role, pg_db_url, tmp_path, monkeypatch):
    monkeypatch.setenv(OBSERVER_ROLE_ENV, role)
    engine, _, shared, importer = await open_service(pg_db_url, tmp_path)
    try:
        staged = await stage_pair(importer, "a")
        version_id, schema = await building_version(engine)
        with pytest.raises(ValueError, match=OBSERVER_ROLE_ENV) as refused:
            await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), **NO_ACCEPT_EMPTY)
        assert role not in str(refused.value)
        assert (await version(engine, version_id))["status"] == "building"
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- regrant


def _observer_grants(stand_in, *schemas: str) -> set[tuple[str, str, str]]:
    """(kind, object, privilege) for every grant naming the observer in these schemas, the per-test suffix taken off."""
    relation = "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
    named = "aclexplode({}) x WHERE x.grantee = %(r)s::regrole AND n.nspname = ANY(%(s)s)"
    sql = (
        f"SELECT 'schema', n.nspname, x.privilege_type FROM pg_namespace n, {named.format('n.nspacl')}"
        f" UNION ALL SELECT CASE c.relkind WHEN 'S' THEN 'sequence' ELSE 'table' END, n.nspname || '.' || c.relname, x.privilege_type"
        f" {relation}, {named.format('c.relacl')}"
        f" UNION ALL SELECT 'column', n.nspname || '.' || c.relname || '.' || a.attname, x.privilege_type"
        f" {relation} JOIN pg_attribute a ON a.attrelid = c.oid, {named.format('a.attacl')}"
    )
    with stand_in.connect() as conn:
        return set(conn.execute(sql, {"r": stand_in.observer, "s": list(schemas)}).fetchall())


async def _production_without_observer(stand_in, tmp_path) -> tuple[list[str], list[str]]:
    """S3 before S2 on a project that published versions already: the observer arrives after 0007 skipped its grants."""
    bootstrap(stand_in, P0_SCRIPT)
    stand_in.set_passwords(stand_in.app, stand_in.reader)
    engine, _, shared, importer = await open_service(stand_in.url_as(stand_in.app), tmp_path / "files")
    try:
        published = [await _publish(engine, shared, importer, tag) for tag in ("a", "b")]
        _, building = await building_version(engine)
        failed_id, failed = await building_version(engine)
        async with engine.begin() as conn:
            for schema in (building, failed):
                await conn.execute(text(f"CREATE TABLE {schema}.rs_ids (id text NOT NULL)"))
            await conn.execute(text("UPDATE pick_mirror.versions SET status = 'failed' WHERE id = :id"), {"id": failed_id})
    finally:
        await engine.dispose()
    bootstrap(stand_in, OBSERVER)
    stand_in.set_passwords(stand_in.observer)
    return published, [building, failed]


def _dsn(stand_in, role: str | None = None) -> str:
    return stand_in.url_as(role or stand_in.app, driver="postgresql")


@pytest.mark.asyncio
async def test_regrant_covers_published_versions(pg_cluster, tmp_path, monkeypatch):
    from ggwork_pick.observe.admin.cmd_regrant import regrant

    (tmp_path / "late").mkdir()
    (tmp_path / "fresh").mkdir()
    with stand_in_for(pg_cluster, tmp_path / "late", monkeypatch) as late:
        published, unpublished = await _production_without_observer(late, tmp_path / "late")
        schemas = ("deerflow", "pick_mirror", "pick_obs", *published, *unpublished)
        # bootstrap-observer.sql's own three; the version table's SELECT, which the deploy guard needs before 0007 (D41).
        assert _observer_grants(late, *schemas) == {
            ("schema", "deerflow", "USAGE"),
            ("schema", "pick_mirror", "USAGE"),
            ("table", "deerflow.ggwp_alembic_version", "SELECT"),
        }
        checked = io.StringIO()
        assert await regrant(_dsn(late), late.observer, check=True, out=checked) == 1
        assert "缺少授权" in checked.getvalue() and published[0] in checked.getvalue()
        assert await regrant(_dsn(late), late.observer, out=io.StringIO()) == 0
        observer = host_engine(late.url_as(late.observer))
        try:
            for tag, schema in zip(("a", "b"), published):
                async with observer.connect() as conn:
                    assert (await conn.execute(text(f"select id from {schema}.rs_ids"))).scalars().all() == [f"rs-{tag}"]
            for schema in unpublished:
                await _refused(observer, f"select * from {schema}.rs_ids", f"permission denied for schema {schema}")
        finally:
            await observer.dispose()
        late_grants = _observer_grants(late, "deerflow", "pick_mirror", "pick_obs")
        again = io.StringIO()
        assert await regrant(_dsn(late), late.observer, check=True, out=again) == 0, again.getvalue()
        assert await regrant(_dsn(late), late.observer, out=io.StringIO()) == 0
        assert _observer_grants(late, *schemas) == late_grants | {
            grant for schema in published for grant in (("schema", schema, "USAGE"), ("table", f"{schema}.rs_ids", "SELECT"))
        }
    # Exactly what 0007 gives an observer that existed before it ran, table for table and column for column.
    with stand_in_for(pg_cluster, tmp_path / "fresh", monkeypatch) as fresh:
        bootstrap(fresh)
        fresh.set_passwords(fresh.app, fresh.reader, fresh.observer)
        await migrate_as_app(fresh, tmp_path / "fresh" / "data")
        assert _observer_grants(fresh, "deerflow", "pick_mirror", "pick_obs") == late_grants


@pytest.mark.asyncio
async def test_regrant_reports_privileges_beyond_the_list(observed):
    from ggwork_pick.observe.admin.cmd_regrant import regrant

    with observed.connect(observed.app) as conn:
        conn.execute(f"GRANT SELECT ON deerflow.ggwp_selections TO {observed.observer}")
        conn.execute(f"GRANT SELECT (owner_id) ON deerflow.ggwp_candidate_sets TO {observed.observer}")
        conn.execute(f"GRANT USAGE ON SCHEMA pick_obs TO {observed.observer}")
    for check in (True, False):
        out = io.StringIO()
        assert await regrant(_dsn(observed), observed.observer, check=check, out=out) == 1
        report = out.getvalue()
        assert "越权" in report and "deerflow.ggwp_selections" in report and "ggwp_candidate_sets.owner_id" in report and "pick_obs" in report
        assert "缺少授权" not in report


@pytest.mark.asyncio
async def test_regrant_refuses_what_it_cannot_do(observed, tmp_path):
    from ggwork_pick.observe.admin.cmd_regrant import regrant
    from ggwork_pick.observe.errors import Refused

    with pytest.raises(Refused, match="观测角色不存在"):
        await regrant(_dsn(observed), "pick_observer_absent_5d2e8a", out=io.StringIO())
    # The cron's own connection cannot grant: regrant runs as the tables' owner, deerflow_app.
    with pytest.raises(Refused, match="deerflow_app"):
        await regrant(_dsn(observed, observed.observer), observed.observer, out=io.StringIO())
    # The reader has no USAGE on deerflow, so its search_path resolves to nothing: said as such, not as "not the owner".
    with pytest.raises(Refused, match="search_path") as unusable:
        await regrant(_dsn(observed, observed.reader), observed.observer, out=io.StringIO())
    assert "deerflow_app" in str(unusable.value) and "不是工作台表的属主" not in str(unusable.value)
    with pytest.raises(ValueError, match=OBSERVER_ROLE_ENV):
        await regrant(_dsn(observed), 'x"; DROP', out=io.StringIO())


@pytest.mark.asyncio
async def test_regrant_refuses_a_table_it_does_not_own(observed):
    """The schema is deerflow_app's but one table is not: its GRANT would only warn, so regrant stops before any."""
    from ggwork_pick.observe.admin.cmd_regrant import regrant
    from ggwork_pick.observe.errors import Refused

    other = observed.role("other")
    observed.admin(f"CREATE ROLE {other} NOLOGIN")
    with observed.connect() as conn:
        conn.execute(f"ALTER TABLE deerflow.ggwp_obs_sets OWNER TO {other}")
    with pytest.raises(Refused, match="deerflow_app"):
        await regrant(_dsn(observed), observed.observer, out=io.StringIO())


@pytest.mark.asyncio
async def test_regrant_rolls_back_when_a_grant_does_not_take(observer_role, pg_db_url, monkeypatch):
    """A GRANT that only warned leaves a grant missing on read-back: nothing of the run stays, and it exits through the
    admin entry point's failure path."""
    from ggwork_pick.observe.admin import cmd_regrant
    from ggwork_pick.observe.errors import ObserveFailure

    async def one_grant_only(session, role, layout):
        await session.execute(text(f'GRANT SELECT ON ggwp_import_batches TO "{role}"'))

    monkeypatch.setattr(cmd_regrant, "grant_all", one_grant_only)
    dsn = pg_db_url.replace("postgresql+asyncpg://", "postgresql://")
    with pytest.raises(ObserveFailure, match="已回滚"):
        await cmd_regrant.regrant(dsn, observer_role, out=io.StringIO())
    engine = host_engine(pg_db_url)
    try:
        async with engine.connect() as conn:
            held = await conn.execute(text("select has_table_privilege(:r, 'ggwp_import_batches', 'SELECT')"), {"r": observer_role})
            assert held.scalar_one() is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_regrant_refuses_before_0007(pg_cluster, tmp_path, monkeypatch):
    from ggwork_pick.observe.admin.cmd_regrant import regrant
    from ggwork_pick.observe.errors import Refused

    with stand_in_for(pg_cluster, tmp_path, monkeypatch) as stand_in:
        bootstrap(stand_in)
        stand_in.set_passwords(stand_in.app, stand_in.reader, stand_in.observer)
        await migrate_as_app(stand_in, tmp_path, "0006")
        with pytest.raises(Refused, match="0007"):
            await regrant(_dsn(stand_in), stand_in.observer, out=io.StringIO())
        assert not stand_in.query("SELECT has_table_privilege(%s, 'deerflow.ggwp_import_batches', 'SELECT')", stand_in.observer)[0][0]


@pytest.mark.asyncio
async def test_observer_reads_current_rs_ids_readonly(observed_with_versions):
    """S4: the runbook's check, run by psql as the observer: the current version's rs_ids in a read-only transaction."""
    stand_in, current = observed_with_versions
    script = "\n".join(expected_output(OBSERVER_RUNBOOK, "以 observer 连接、只读事务读当前版本的 rs_ids")) + "\n"
    result = stand_in.run(script, role=stand_in.observer)
    assert result.returncode == 0, result.stdout
    assert "ERROR" not in result.stdout and "WARNING" not in result.stdout, result.stdout
    lines = result.stdout.splitlines()
    assert lines[0] == "BEGIN" and lines[-1] == "ROLLBACK", result.stdout
    assert re.search(rf"^ *{current} *\| *1 *$", result.stdout, re.M), result.stdout
    # The same transaction refuses a write: read-only, whatever the role may do otherwise.
    write = script.replace("ROLLBACK;", "INSERT INTO ggwp_obs_budget (channel, budget_day) VALUES ('gsc', '2026-09-25');\nROLLBACK;")
    refused = stand_in.run(write, role=stand_in.observer)
    assert "cannot execute INSERT in a read-only transaction" in refused.stdout, refused.stdout


@pytest_asyncio.fixture
async def observed_with_versions(observed, tmp_path):
    engine, _, shared, importer = await open_service(observed.url_as(observed.app), tmp_path / "files")
    try:
        schemas = [await _publish(engine, shared, importer, tag) for tag in ("a", "b")]
    finally:
        await engine.dispose()
    yield observed, schemas[-1]


def test_the_runbook_counts_what_the_lists_hold():
    """observer-role.md's table names how many tables the observer writes and how many sequences they draw ids from."""
    from ggwork_pick.observe import grants

    text = OBSERVER_RUNBOOK.read_text(encoding="utf-8")
    assert re.findall(r"其余 (\d+) 张 `ggwp_obs_\*`、`ggwp_gsc_\*`", text) == [str(len(grants.observer_writes()))]
    assert re.findall(r"这些表的 (\d+) 个自增序列", text) == [str(len(grants.serial_writes()))]
    assert len(grants.observer_writes()) == len(OBSERVER_WRITES)


def test_regrant_lists_are_0007s():
    """regrant restores what 0007 grants; the revision is frozen, so the lists are compared, not shared."""
    from ggwork_pick.observe import grants

    migration = migration_module()
    assert grants.OBSERVER_READS == migration.OBSERVER_READS == OBSERVER_READS
    assert grants.observer_writes() == tuple(sorted(migration.OBSERVER_WRITES)) == OBSERVER_WRITES
    assert grants.OBSERVER_CANDIDATE_COLUMNS == migration.OBSERVER_CANDIDATE_COLUMNS
    assert tuple(f"pick_mirror.{name}" for name in grants.OBSERVER_MIRROR) == migration.OBSERVER_MIRROR
    assert migration.DEFAULT_OBSERVER_ROLE == grants.DEFAULT_OBSERVER_ROLE and migration.OBSERVER_ROLE_ENV == OBSERVER_ROLE_ENV


# ---------------------------------------------------------------- the command line


@pytest.fixture
def observer_role(pg_cluster, monkeypatch):
    name = pg.unique_name("pick_observer")
    pg_cluster.create_role(name)
    monkeypatch.setenv(OBSERVER_ROLE_ENV, name)
    yield name
    pg_cluster.drop_role(name)


def test_regrant_command_line(observer_role, pg_db_url, monkeypatch, capsys):
    """Through the admin entry point as the gateway container runs it; the superuser stands in for deerflow_app."""
    from ggwork_pick.observe.admin.__main__ import main

    dsn = pg_db_url.replace("postgresql+asyncpg://", "postgresql://")
    monkeypatch.setenv("PICK_DATABASE_URL", dsn)
    monkeypatch.setenv("PGSSLMODE", "disable")
    assert main(["regrant", "--check"]) == 1
    assert main(["regrant"]) == 0
    assert main(["regrant", "--check"]) == 0
    out = capsys.readouterr()
    assert "regrant" in out.out and dsn not in out.out + out.err


@pytest.mark.parametrize(
    "environment, message",
    [
        ({"PGSSLMODE": "require"}, "PICK_DATABASE_URL"),
        ({"PICK_DATABASE_URL": "postgresql://observer:hunter2@db.example/pick"}, "PGSSLMODE"),
        ({"PICK_DATABASE_URL": "mysql://observer:hunter2@db.example/pick", "PGSSLMODE": "require"}, "PICK_DATABASE_URL"),
        ({"PICK_DATABASE_URL": "postgresql://observer:hunter2@db.example/pick", "PGSSLMODE": "require", OBSERVER_ROLE_ENV: "Bad Role"}, OBSERVER_ROLE_ENV),
    ],
)
def test_regrant_refuses_its_configuration_without_echoing_it(environment, message, monkeypatch, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    for name in ("PICK_DATABASE_URL", "PGSSLMODE", OBSERVER_ROLE_ENV):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    assert main(["regrant"]) == 2
    out = capsys.readouterr()
    assert message in out.err
    assert "hunter2" not in out.out + out.err and "Bad Role" not in out.out + out.err


def test_regrant_usage_errors_exit_2(capsys):
    from ggwork_pick.observe.admin.__main__ import main

    assert main(["regrant", "--grant-everything"]) == 2
    err = capsys.readouterr().err
    # The command's own usage line, not the entry point's unknown-command one; the argument is not echoed.
    assert "observe.admin regrant [-h] [--check]" in err and "--grant-everything" not in err
