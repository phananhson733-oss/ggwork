"""Migration 0007's pick_obs schema on PostgreSQL (plan TR-11; design 3.4, 3.6; D14, D15, D16): the eight views serve the
contract's rows (ggwork_pick/observe/contract_views.py) to the board reader and nobody else, and the observer gets TR-12's
table grants. Skips when PICK_TEST_PG_URL is unset; the tables themselves are test_0007.py.
"""

import json
import logging
from pathlib import Path

import pg
import pytest
import sqlalchemy as sa
from engines import host_engine
from obs_schema import (
    OBS_TABLES,
    STAMP,
    assert_0007_in_place,
    filler,
    migration_module,
    scalar,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from ggwork_pick.observe.contract_views import VIEW_COLUMNS, VIEW_NAMES, VIEW_ROW_MODELS

OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
# The observer reads these and writes every other ggwp_obs_* and ggwp_gsc_* table (TR-12; D14, D16).
OBSERVER_READS = ("ggwp_import_batches", "ggwp_drama_versions", "ggwp_alembic_version", "ggwp_obs_decisions")
OBSERVER_WRITES = tuple(sorted(OBS_TABLES - {"ggwp_obs_decisions"}))
OBSERVER_CANDIDATE_COLUMNS = ("id", "trends_set_id", "gsc_set_id", "created_at")
OBSERVER_NEVER = ("ggwp_selections", "ggwp_selection_commands", "ggwp_knowledge_versions", "ggwp_sync_runs", "ggwp_answer_checks")
# Tables whose rows carry an autoincrement id (a sequence on PostgreSQL the observer needs for INSERT).
SERIAL_TABLES = (
    "ggwp_obs_watch", "ggwp_obs_requests", "ggwp_obs_raw", "ggwp_obs_discoveries", "ggwp_obs_identity_alias", "ggwp_gsc_slices",
    "ggwp_obs_states", "ggwp_obs_milestones", "ggwp_obs_alerts", "ggwp_obs_links", "ggwp_gsc_vchecks", "ggwp_gsc_totals",
)  # fmt: skip


def _load_views() -> list[dict]:
    return json.loads((FIXTURES / "views.json").read_text(encoding="utf-8"))["valid"]


# view -> (table, the view's columns renamed from the table's) for the views that project one table.
PROJECTIONS = {
    "sets": ("ggwp_obs_sets", {"set_id": "id", "frozen_inputs": "frozen_inputs_json", "summary": "summary_json"}),
    "states": (
        "ggwp_obs_states",
        {
            "row_id": "id",
            "labels": "labels_json",
            "flags": "flags_json",
            "metrics": "metrics_json",
            "quality_note": "quality_note_json",
            "paste_row": "paste_row_json",
        },
    ),
    "links": ("ggwp_obs_links", {}),
    "discoveries": ("ggwp_obs_discoveries", {"discovery_id": "id"}),
    "alias_queue": ("ggwp_obs_identity_alias", {"alias_id": "id", "evidence": "evidence_json"}),
    "alerts": ("ggwp_obs_alerts", {"evidence": "evidence_json"}),
    "run_status": ("ggwp_obs_batches", {"batch_id": "id", "status_codes": "status_codes_json"}),
}


def _source_rows(view: str, row: dict) -> list[tuple[sa.Table, dict]]:
    """Table rows from which the view shows exactly this row."""
    if view == "confirm_queue":
        return _confirm_rows(row)
    name, renamed = PROJECTIONS[view]
    values = {renamed.get(key, key): value for key, value in row.items()}
    if view == "states" and row["channel"] == "trends":
        values["normalized_title"] = row["title"].lower()
    return [filler(name, **values)]


def _confirm_rows(row: dict) -> list[tuple[sa.Table, dict]]:
    published = {"id": row["set_id"], "channel": "trends", "mode": row["mode"], "status": "published", "published_at": row["since"]}
    state = {key: row[key] for key in ("identity", "title", "normalized_title", "language", "state", "confirmation", "id_evidence", "set_id", "mode")} | {
        "channel": "trends",
        "theater": row["platform"],
        "scope": row["geo"],
        "created_at": row["since"],
        "correspondence": "unconfirmed",
    }
    return [filler("ggwp_obs_sets", **published), filler("ggwp_obs_states", **_trends_row(state))]


def _trends_row(values: dict) -> dict:
    return {
        "window_kind": "H", "labels_json": [], "tier": "A", "ambiguity": "clear", "flags_json": [], "carried_over": False, "stale": False,
        "window_end": STAMP, "latest_block_end": STAMP, "metrics_json": {}, "created_at": STAMP,
    } | values  # fmt: skip


async def _read_as(engine, role: str, rows: list[tuple[sa.Table, dict]], query: str) -> list[dict]:
    """Insert the rows, read as the role in the same transaction, roll everything back."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        try:
            for table, row in rows:
                await conn.execute(table.insert(), row)
            await conn.execute(text(f'SET LOCAL ROLE "{role}"'))
            return [dict(found) for found in (await conn.execute(text(query))).mappings()]
        finally:
            await transaction.rollback()


def _decoded(view: str, row: dict) -> dict:
    kinds = {column.name: column.type for column in VIEW_COLUMNS[view]}
    return {key: json.loads(value) if kinds[key] == "json" and isinstance(value, str) else value for key, value in row.items()}


@pytest.mark.parametrize("case", _load_views(), ids=lambda case: case["name"])
@pytest.mark.asyncio
async def test_views_serve_the_contract_rows_to_the_reader(pg_reader_role, empty_pg_url, tmp_path, case):
    await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    try:
        found = await _read_as(engine, pg_reader_role, _source_rows(case["view"], case["row"]), f"select * from pick_obs.{case['view']}")
    finally:
        await engine.dispose()
    assert [_decoded(case["view"], row) for row in found] == [case["row"]]
    VIEW_ROW_MODELS[case["view"]].model_validate(case["row"])


def _alias(pair: str, version: int, status: str) -> tuple[sa.Table, dict]:
    old, new = f'["kalostv","{pair}","en"]', f'["kalostv","{pair}-2","en"]'
    return filler("ggwp_obs_identity_alias", alias_version=version, old_identity=old, new_identity=new, status=status, created_at=STAMP)


@pytest.mark.asyncio
async def test_alias_queue_shows_the_latest_suggestion_of_each_pair(pg_reader_role, empty_pg_url, tmp_path):
    await pg.migrate(empty_pg_url, tmp_path)
    rows = [_alias("a", 1, "suggested"), _alias("a", 2, "confirmed"), _alias("b", 1, "suggested"), _alias("c", 2, "auto"), _alias("d", 3, "rejected")]
    rows += [_alias("e", 1, "rejected"), _alias("e", 4, "suggested")]
    engine = host_engine(empty_pg_url)
    try:
        found = await _read_as(engine, pg_reader_role, rows, "select old_identity, alias_version, status from pick_obs.alias_queue order by old_identity")
    finally:
        await engine.dispose()
    assert [tuple(row.values()) for row in found] == [('["kalostv","b","en"]', 1, "suggested"), ('["kalostv","e","en"]', 4, "suggested")]


def _set(set_id: str, published_at: str, *, mode: str = "live", channel: str = "trends", status: str = "published") -> tuple[sa.Table, dict]:
    return filler("ggwp_obs_sets", id=set_id, channel=channel, mode=mode, status=status, published_at=published_at)


def _state(set_id: str, identity: str, created_at: str, *, state: str = "rising", mode: str = "live", correspondence: str = "unconfirmed") -> tuple:
    values = {"set_id": set_id, "identity": identity, "title": identity.upper(), "normalized_title": identity, "language": "en", "theater": "ReelShort"}
    values |= {"scope": "US", "state": state, "confirmation": "first", "id_evidence": "medium", "mode": mode, "channel": "trends"}
    return filler("ggwp_obs_states", **_trends_row(values | {"created_at": created_at, "correspondence": correspondence}))


def _at(day: int) -> str:
    return f"2026-09-{day:02d}T02:00:00.000000+00:00"


@pytest.mark.asyncio
async def test_confirm_queue_reads_the_latest_published_trends_set_of_each_mode(pg_reader_role, empty_pg_url, tmp_path):
    rows = [_set("live-1", _at(23)), _set("live-2", _at(24)), _set("live-3", _at(25), status="pruned"), _set("shadow-1", _at(24), mode="shadow")]
    rows += [_set("gsc-1", _at(25), channel="gsc")]
    rows += [_state("live-1", "x", _at(23)), _state("live-1", "gone", _at(23))]
    rows += [_state("live-2", "x", _at(24)), _state("live-2", "w", _at(24), state="emerging"), _state("live-2", "z", _at(24), correspondence="confirmed")]
    rows += [_state("live-2", "y", _at(24), state="flat"), _state("live-2", "c", _at(24), state="cooling"), _state("shadow-1", "x", _at(24), mode="shadow")]
    await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    try:
        query = "select identity, set_id, mode, state, since from pick_obs.confirm_queue order by mode, identity"
        found = await _read_as(engine, pg_reader_role, rows, query)
    finally:
        await engine.dispose()
    # since is when the identity first entered the queue, even in a set the queue no longer reads.
    assert [tuple(row.values()) for row in found] == [
        ("w", "live-2", "live", "emerging", _at(24)),
        ("x", "live-2", "live", "rising", _at(23)),
        ("x", "shadow-1", "shadow", "rising", _at(24)),
    ]


async def _privilege(engine, check: str, *args) -> bool:
    params = {f"a{i}": arg for i, arg in enumerate(args)}
    return await scalar(engine, f"select {check}({', '.join(':' + key for key in params)})", **params)


@pytest.mark.asyncio
async def test_reader_reads_views_not_tables(pg_reader_role, empty_pg_url, tmp_path):
    await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    try:
        assert await _privilege(engine, "has_schema_privilege", pg_reader_role, "pick_obs", "USAGE")
        for view in VIEW_NAMES:
            assert await _privilege(engine, "has_table_privilege", pg_reader_role, f"pick_obs.{view}", "SELECT"), view
            # A one-table view is updatable in PostgreSQL: only the missing grant keeps the reader from writing through it.
            assert not await _privilege(engine, "has_table_privilege", pg_reader_role, f"pick_obs.{view}", "INSERT, UPDATE, DELETE, TRUNCATE"), view
        assert not await _privilege(engine, "has_schema_privilege", pg_reader_role, pg.SCHEMA, "USAGE")
        for table in sorted(OBS_TABLES):
            assert not await _privilege(engine, "has_table_privilege", pg_reader_role, f"{pg.SCHEMA}.{table}", "SELECT"), table
        assert await _read_as(engine, pg_reader_role, [], "select count(*) as n from pick_obs.sets") == [{"n": 0}]
        with pytest.raises(DBAPIError, match="permission denied"):
            await _read_as(engine, pg_reader_role, [], f"select count(*) from {pg.SCHEMA}.ggwp_obs_sets")
    finally:
        await engine.dispose()


@pytest.fixture
def api_roles(pg_cluster):
    """Supabase's anon and authenticated, cluster-wide under their real names: 0007 revokes from exactly these."""
    created = []
    for role in ("anon", "authenticated"):
        try:
            pg_cluster._execute(f"CREATE ROLE {role} NOLOGIN")
            created.append(role)
        except Exception:  # already there from another run: leave it for whoever made it
            pass
    yield ("anon", "authenticated")
    for role in created:
        pg_cluster.drop_role(role)


@pytest.mark.asyncio
async def test_no_anon_usage_pick_obs(api_roles, pg_reader_role, empty_pg_url, tmp_path):
    # api_roles comes first so it is dropped after this database and the default privileges that name it here.
    engine = host_engine(empty_pg_url)
    grantees = "PUBLIC, anon, authenticated"
    try:
        # As if the schema came from elsewhere with Supabase's generous defaults for anything created in it.
        async with engine.begin() as conn:
            await conn.execute(text("create schema pick_obs"))
            await conn.execute(text(f"grant usage on schema pick_obs to {grantees}"))
            await conn.execute(text(f"alter default privileges in schema pick_obs grant select, insert on tables to {grantees}"))
        await pg.migrate(empty_pg_url, tmp_path)
        for role in ("public", *api_roles):
            assert not await _privilege(engine, "has_schema_privilege", role, "pick_obs", "USAGE"), role
            for view in VIEW_NAMES:
                assert not await _privilege(engine, "has_table_privilege", role, f"pick_obs.{view}", "SELECT, INSERT"), (role, view)
        assert await _privilege(engine, "has_table_privilege", pg_reader_role, "pick_obs.sets", "SELECT")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_grant_skipped_without_role(empty_pg_url, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(pg.READER_ROLE_ENV, "pick_board_reader_absent_4f1c9e")
    monkeypatch.setenv(OBSERVER_ROLE_ENV, "pick_observer_absent_4f1c9e")
    with caplog.at_level(logging.WARNING, logger="ggwork_pick.migrations"):
        await pg.migrate(empty_pg_url, tmp_path)
    skipped = [record.getMessage() for record in caplog.records if record.getMessage().startswith("[pick-obs] ")]
    assert sorted(env for env in (pg.READER_ROLE_ENV, OBSERVER_ROLE_ENV) for message in skipped if env in message) == sorted(
        (pg.READER_ROLE_ENV, OBSERVER_ROLE_ENV)
    )
    assert len(skipped) == 2
    # Neither name is in the message: the log line says which variable to look at, not what it holds.
    assert not any("absent_4f1c9e" in message for message in skipped)
    engine = host_engine(empty_pg_url)
    try:
        await assert_0007_in_place(engine)
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["Pick_Observer", "1observer", "pick-observer", 'observer"; drop schema deerflow cascade; --', "o" * 64])
@pytest.mark.asyncio
async def test_a_malformed_observer_role_name_fails_the_whole_migration(empty_pg_url, tmp_path, monkeypatch, role):
    monkeypatch.setenv(OBSERVER_ROLE_ENV, role)
    with pytest.raises(ValueError, match=OBSERVER_ROLE_ENV) as failure:
        await pg.migrate(empty_pg_url, tmp_path)
    assert role not in str(failure.value)
    engine = host_engine(empty_pg_url)
    try:
        # One transaction from 0001 to 0007: nothing of it stays behind.
        assert await scalar(engine, "select count(*) from pg_tables where tablename like 'ggwp%'") == 0
        assert await scalar(engine, "select count(*) from pg_namespace where nspname in ('pick_mirror', 'pick_obs')") == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sqlite_gets_the_tables_but_never_reads_the_role_variables(tmp_path, monkeypatch):
    # Like 0006: SQLite has no roles, so 0007 returns before it reads either name there.
    monkeypatch.setenv(pg.READER_ROLE_ENV, "Not A Role")
    monkeypatch.setenv(OBSERVER_ROLE_ENV, "Not A Role")
    url = f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    await pg.migrate(url, tmp_path)
    engine = host_engine(url)
    try:
        await assert_0007_in_place(engine)
    finally:
        await engine.dispose()
    with pytest.raises(ValueError, match=OBSERVER_ROLE_ENV):
        migration_module()._observer_role()


@pytest.fixture(params=["plain", "keyword"])
def observer_role(request, pg_cluster, monkeypatch):
    # A reserved word passes the name check; unquoted, the grants would fail or name something else.
    name = pg.unique_name("pick_observer") if request.param == "plain" else "window"
    pg_cluster._execute(f'CREATE ROLE "{name}" NOLOGIN')
    monkeypatch.setenv(OBSERVER_ROLE_ENV, name)
    yield name
    pg_cluster._execute(f'DROP ROLE IF EXISTS "{name}"')


@pytest.mark.asyncio
async def test_observer_gets_tr12s_table_grants(observer_role, empty_pg_url, tmp_path):
    # observer_role comes first so it is dropped after this database and the grants it holds here.
    await pg.migrate(empty_pg_url, tmp_path)
    engine = host_engine(empty_pg_url)
    schema, role = pg.SCHEMA, observer_role

    async def table(name: str, privilege: str) -> bool:
        return await _privilege(engine, "has_table_privilege", role, f"{schema}.{name}", privilege)

    try:
        assert await _privilege(engine, "has_schema_privilege", role, schema, "USAGE")
        for name in OBSERVER_READS:
            assert await table(name, "SELECT") and not await table(name, "INSERT, UPDATE, DELETE, TRUNCATE"), name
        for name in OBSERVER_WRITES:
            assert await table(name, "SELECT") and await table(name, "INSERT") and await table(name, "UPDATE") and await table(name, "DELETE"), name
            assert not await table(name, "TRUNCATE"), name
        for name in SERIAL_TABLES:
            sequence = await scalar(engine, "select pg_get_serial_sequence(:t, 'id')", t=f"{schema}.{name}")
            assert await _privilege(engine, "has_sequence_privilege", role, sequence, "USAGE"), name
        # D16: four columns of the candidate sets, enough to tell whether a set is still referenced; nothing else of them.
        candidates = f"{schema}.ggwp_candidate_sets"
        assert not await table("ggwp_candidate_sets", "SELECT")
        for column in ("id", "trends_set_id", "gsc_set_id", "created_at", "owner_id", "ordered_items_json", "conditions_json"):
            assert await _privilege(engine, "has_column_privilege", role, candidates, column, "SELECT") == (column in OBSERVER_CANDIDATE_COLUMNS)
        for name in OBSERVER_NEVER:
            assert not await table(name, "SELECT"), name
        # D15: the mirror's versions and series (rs_ids per version comes with publish and regrant, TR-12).
        assert await _privilege(engine, "has_schema_privilege", role, "pick_mirror", "USAGE")
        for name, readable in {"versions": True, "series": True, "series_state": False, "control": False}.items():
            assert await _privilege(engine, "has_table_privilege", role, f"pick_mirror.{name}", "SELECT") == readable, name
        assert not await _privilege(engine, "has_schema_privilege", role, "pick_obs", "USAGE")
    finally:
        await engine.dispose()
