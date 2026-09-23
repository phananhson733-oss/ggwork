"""What SQLite accepts and PostgreSQL refuses (plan 6.7), one test per row of the audit table.

Tests take pick_db_url or app_client and run on both dialects; the PostgreSQL half skips when
PICK_TEST_PG_URL is unset. Migrations 0001-0004 on PostgreSQL are covered by test_pg_migrations.
"""

import asyncio
import json
import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pg
import pytest
from engines import host_engine
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

NUL = chr(0)
# What Python's JSON decoder makes of a "\ud800" escape: asyncpg, psycopg and sqlite3 encode strictly and cannot send it.
LONE_SURROGATE = chr(0xD800)
UNSTORABLE = pytest.mark.parametrize("bad", [NUL, LONE_SURROGATE], ids=["nul", "lone-surrogate"])
ALICE = {"test-owner": "alice"}
EXTENSION = Path(__file__).resolve().parents[1] / "ggwork_pick"


async def _open(url, tmp_path):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    return engine, service, PickRepository(service.session_factory, "alice")


async def _result(service):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
    return repo, await SelectionService(repo).query({}, thread_id="t", run_id="r", call_id="c")


def _json(payload) -> dict:
    # json.dumps escapes both characters, the way a browser sends them; FastAPI's decoder turns them back into characters.
    return {"content": json.dumps(payload), "headers": {**ALICE, "content-type": "application/json"}}


# ---- text columns: NUL and lone surrogates ----


@UNSTORABLE
@pytest.mark.asyncio
async def test_unstorable_text_in_a_request_body_is_a_422_and_writes_nothing(app_client, bad):
    client, service = app_client
    repo, result = await _result(service)
    item = result["items"][0]["item_id"]
    save = {"request_id": "save-1", "result_id": result["id"], "item_ids": [item]}
    for payload in ({**save, "note": f"a{bad}b"}, {**save, "request_id": f"save{bad}"}, {**save, "item_ids": [item, f"x{bad}"]}):
        response = await client.post("/api/pick/selections", **_json(payload))
        assert response.status_code == 422, (payload, response.status_code)
    assert await repo.selections() == []
    assert await repo.command_receipt("save-1") is None
    assert (await client.post("/api/pick/selections", **_json(save))).status_code == 200
    selection = (await repo.selections())[0]
    edit = {"request_id": "edit-1", "expected_version": selection["version"], "note": f"a{bad}b"}
    assert (await client.patch(f"/api/pick/selections/{selection['id']}", **_json(edit))).status_code == 422
    assert (await repo.selections())[0]["note"] == ""


@pytest.mark.asyncio
async def test_nul_in_a_path_or_query_parameter_is_a_422(app_client):
    # Percent-decoding replaces invalid UTF-8, so only NUL can reach a parameter; PostgreSQL rejects it even in a WHERE.
    client, service = app_client
    await _result(service)
    for url in ("/api/pick/results/%00", "/api/pick/results?thread_id=%00", "/api/pick/answer-checks?thread_id=t%00", "/api/pick/commands/%00"):
        assert (await client.get(url, headers=ALICE)).status_code == 422, url
    edit = {"request_id": "edit-1", "expected_version": 1, "note": "x"}
    assert (await client.patch("/api/pick/selections/%00", **_json(edit))).status_code == 422
    assert (await client.get("/api/pick/results/missing", headers=ALICE)).status_code == 404


@UNSTORABLE
@pytest.mark.asyncio
async def test_unstorable_text_in_an_import_is_refused_before_the_database(app_client, bad):
    client, service = app_client
    for field in ("title", "source_id"):
        rows = [{"source": "synthetic", "source_id": "1", "language": "en", "title": "Example", field: f"a{bad}b"}]
        files = [("files", ("c.json", json.dumps(rows), "application/json"))]
        response = await client.post("/api/pick/imports", headers=ALICE, data={"kind": "catalog"}, files=files)
        assert response.status_code == 422, (field, response.status_code)
    if bad == NUL:
        # A multipart form cannot carry a lone surrogate; NUL in the source reference is a knowledge refusal (400).
        files = [("files", ("a.md", "# A", "text/markdown"))]
        response = await client.post("/api/pick/imports", headers=ALICE, data={"kind": "knowledge", "source_ref": f"ref{bad}"}, files=files)
        assert response.status_code == 400
    assert (await client.get("/api/pick/imports", headers=ALICE)).json()["batches"] == []

    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    importer = Importer(PickRepository(service.session_factory, "alice"), service.data_dir)
    with pytest.raises(ValueError, match="NUL"):
        await importer.knowledge(b"# A", f"a{bad}.md", "ref")


@pytest.mark.asyncio
async def test_model_conditions_refuse_unstorable_text():
    from pydantic import ValidationError

    from ggwork_pick.contracts import PickConditions

    for bad in (NUL, LONE_SURROGATE):
        for filters in ({"query": f"a{bad}"}, {"tags": ["ok", f"t{bad}"]}, {"posted_account": bad}):
            with pytest.raises(ValidationError, match="NUL"):
                PickConditions.model_validate(filters)
    # Paired surrogates are one ordinary character once decoded.
    assert PickConditions.model_validate(json.loads('{"query": "\\ud83c\\udfac"}')).query == chr(0x1F3AC)


# ---- JSON columns ----


@pytest.mark.asyncio
async def test_json_columns_keep_escaped_nul(pick_db_url, tmp_path):
    # Written past the repository, which replaces NUL in text that skipped StrictInput (test_storable_text):
    # this is what the column itself accepts.
    from ggwork_pick.models import candidate_sets, import_batches
    from ggwork_pick.repository import stamp

    engine, _, repo = await _open(pick_db_url, tmp_path)
    try:
        odd = f"a{NUL}b"
        batch = dict(id="batch-1", owner_id="alice", kind="catalog", content_hash="h", raw_blob_path="/x", status="published", created_at=stamp())
        record = dict(
            id="result-1",
            owner_id="alice",
            thread_id="t",
            run_id="r",
            tool_call_id="c",
            catalog_batch_id="batch-1",
            rule_version="v",
            ranking_version="v",
            conditions_json={"query": odd},
            ordered_items_json=[{"title": odd}],
            created_at=stamp(),
        )
        async with engine.begin() as conn:
            await conn.execute(insert(import_batches).values(**batch, validation_json={"scope": odd}))
            await conn.execute(insert(candidate_sets).values(**record))
        stored = await repo.result("result-1")
        assert stored["conditions_json"] == {"query": odd}
        assert stored["ordered_items_json"] == [{"title": odd}]
        assert (await repo.batch_info("batch-1"))["scope"] == odd
        if engine.dialect.name == "postgresql":
            async with engine.connect() as conn:
                types = await conn.execute(
                    text(
                        "select table_name, column_name, data_type from information_schema.columns"
                        " where table_schema = current_schema() and table_name like 'ggwp%' and column_name like '%json'"
                    )
                )
                assert {row.data_type for row in types} == {"json"}
                # jsonb refuses the same escape: the reason these columns stay json.
                with pytest.raises(DBAPIError, match="cannot be converted to text"):
                    await conn.execute(text("select cast(:v as jsonb)"), {"v": json.dumps(f"a{NUL}")})
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_lone_surrogate_cannot_be_stored_in_a_json_column(pick_db_url, tmp_path):
    # The host's serializer keeps it verbatim (ensure_ascii=False), and every driver encodes parameters as
    # strict UTF-8; SQLAlchemy's default serializer would have escaped it and hidden this.
    from ggwork_pick.models import answer_checks
    from ggwork_pick.repository import stamp

    engine, _, _ = await _open(pick_db_url, tmp_path)
    try:
        row = dict(id="check-1", owner_id="alice", thread_id="t", run_id="r", message_id=None, created_at=stamp())
        async with engine.connect() as conn:
            for notes in ([f"a{LONE_SURROGATE}b"], {f"k{LONE_SURROGATE}": "v"}):
                with pytest.raises((UnicodeEncodeError, DBAPIError), match="surrogates not allowed"):
                    await conn.execute(insert(answer_checks).values(**row, notes_json=notes))
            await conn.execute(insert(answer_checks).values(**row, notes_json=[f"a{chr(0xFFFD)}b"]))
            assert (await conn.execute(select(answer_checks.c.notes_json))).scalar_one() == [f"a{chr(0xFFFD)}b"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_engine_that_serializes_json_differently_fails_the_suite(pick_db_url):
    engine = create_async_engine(pick_db_url)
    try:
        with pytest.raises(AssertionError, match="host_engine"):
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))
    finally:
        await engine.dispose()


# ---- session settings on pooled connections ----


@pytest.mark.asyncio
async def test_transaction_settings_do_not_follow_a_pooled_connection(pg_db_url, tmp_path):
    from ggwork_pick.selection import SelectionService

    engine = host_engine(pg_db_url, pool_size=1, max_overflow=0)
    try:
        async with engine.begin() as conn:
            pid = (await conn.execute(text("select pg_backend_pid()"))).scalar_one()
            await conn.execute(text("SET LOCAL lock_timeout = '5s'"))
            await conn.execute(text("SET LOCAL search_path = public"))
            assert (await conn.execute(text("show lock_timeout"))).scalar_one() == "5s"
        # The extension's own write path: an advisory transaction lock around the save.
        from ggwork_pick.imports import Importer
        from ggwork_pick.repository import PickRepository

        repo = PickRepository(async_sessionmaker(engine, expire_on_commit=False), "alice")
        await Importer(repo, tmp_path).catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
        result = await SelectionService(repo).query({}, thread_id="t", run_id="r", call_id="c")
        await repo.save_selection("save-1", result["id"], [result["items"][0]["item_id"]])
        async with engine.connect() as conn:
            assert (await conn.execute(text("select pg_backend_pid()"))).scalar_one() == pid
            assert (await conn.execute(text("show lock_timeout"))).scalar_one() == "0"
            assert (await conn.execute(text("show search_path"))).scalar_one() == pg.SCHEMA
            assert (await conn.execute(text("select count(*) from pg_locks where locktype = 'advisory'"))).scalar_one() == 0
    finally:
        await engine.dispose()


def test_extension_sql_files_only_set_local():
    # Statements sent through SQLAlchemy are checked at run time by the conftest guard; SQL files may go through a bare driver.
    session_set = re.compile(r"(?i)^\s*(SET\s+(?!LOCAL\b)|RESET\b)")
    offenders = [
        f"{path.relative_to(EXTENSION)}:{number}"
        for path in sorted(EXTENSION.rglob("*.sql"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if session_set.search(line)
    ]
    assert offenders == []


@pytest.mark.asyncio
async def test_a_session_level_setting_fails_the_suite(pick_db_url):
    engine = host_engine(pick_db_url)
    try:
        async with engine.connect() as conn:
            with pytest.raises(AssertionError, match="SET LOCAL"):
                await conn.execute(text("SET lock_timeout = '5s'"))
    finally:
        await engine.dispose()


# ---- the per-owner write lock ----


@pytest.mark.asyncio
async def test_concurrent_saves_of_one_drama_leave_one_selection(pick_db_url, tmp_path):
    from ggwork_pick.selection import SelectionService

    engine, service, repo = await _open(pick_db_url, tmp_path)
    try:
        _, result = await _result(service)
        item = result["items"][0]["item_id"]
        receipts = await asyncio.gather(*(repo.save_selection(f"save-{i}", result["id"], [item]) for i in range(5)))
        assert sorted(receipt["saved"][0]["status"] for receipt in receipts) == ["created"] + ["existing"] * 4
        assert len(await repo.selections()) == 1
        assert (await SelectionService(repo).query({}, thread_id="t", run_id="r2", call_id="c2"))["items"] == []
    finally:
        await engine.dispose()


# ---- ISO timestamps compared as strings ----


def _moments() -> list[datetime]:
    base = datetime(2026, 9, 23, 10, 0, 0, tzinfo=UTC)
    return [
        base,
        base + timedelta(microseconds=1),
        base + timedelta(microseconds=999_999),
        base + timedelta(seconds=1),
        base - timedelta(microseconds=1),
        base + timedelta(hours=13, microseconds=500_000),
        base + timedelta(days=1),
        base.replace(month=10, day=1),
        base.replace(year=2027, month=1, day=1),
    ]


async def _sorted_in_database(conn, values: list[str], collate: str) -> list[str]:
    await conn.execute(text("delete from stamps"))
    await conn.execute(text("insert into stamps (v) values (:v)"), [{"v": v} for v in random.Random(7).sample(values, len(values))])
    return list((await conn.execute(text(f"select v from stamps order by v{collate}"))).scalars())


@pytest.mark.asyncio
async def test_stored_timestamps_sort_in_time_order_under_each_collation(pick_db_url):
    from ggwork_pick.repository import stamp

    stamps = [stamp(moment) for moment in _moments()]
    assert all(len(value) == len(stamps[0]) for value in stamps)
    expected = sorted(stamps, key=datetime.fromisoformat)
    engine = host_engine(pick_db_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("create temporary table stamps (v varchar(40))"))
            collations = [""]
            if engine.dialect.name == "postgresql":
                # Supabase sorts text with en_US.UTF-8 (glibc); ICU en-US stands in for a linguistic collation here.
                available = set((await conn.execute(text("select collname from pg_collation where collname in ('C', 'en-US-x-icu')"))).scalars())
                collations += [f' collate "{name}"' for name in sorted(available)]
                if "en-US-x-icu" in available:
                    # Plain isoformat() drops ".000000"; that second then sorts after its own fractions.
                    plain = [moment.isoformat() for moment in _moments()]
                    assert await _sorted_in_database(conn, plain, ' collate "en-US-x-icu"') != sorted(plain, key=datetime.fromisoformat)
            for collate in collations:
                assert await _sorted_in_database(conn, stamps, collate) == expected, collate
    finally:
        await engine.dispose()


def test_stored_timestamps_come_from_one_utc_clock():
    # repository.stamp() is the one writer: UTC, six fractional digits.
    clock = re.compile(r"datetime\.now\((?!UTC\))|\butcnow\(|\bdate\.today\(|\.isoformat\(\)")
    offenders = [
        f"{path.relative_to(EXTENSION)}:{number}"
        for path in sorted(EXTENSION.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), 1)
        if clock.search(line)
    ]
    assert offenders == []


# ---- text ordering in the database ----


def _catalog(source_ids) -> bytes:
    rows = [
        {
            "source": "synthetic",
            "source_id": source_id,
            "language": "en",
            "title": f"Drama {source_id}",
            "signals": [{"kind": "kd", "source_ref": "r", "observed_at": "2026-09-20"}],
        }
        for source_id in source_ids
    ]
    return json.dumps(rows, ensure_ascii=False).encode()


# Case, punctuation and accents order differently under a linguistic collation than by code point.
SOURCE_IDS = ["b", "B", "a-b", "a_b", "ab", "Ab", chr(0xC4) + "b", "a b", "z", "A"]


def test_candidate_order_does_not_depend_on_the_row_order_from_the_database():
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.imports import parse_catalog
    from ggwork_pick.selection import matching_rows

    rows = parse_catalog(_catalog(SOURCE_IDS), "json", keep_original=False)
    conditions = PickConditions()
    expected = matching_rows(rows, conditions, set())
    assert [row["identity"] for row in expected] == sorted(row["identity"] for row in rows)
    for seed in range(5):
        assert matching_rows(random.Random(seed).sample(rows, len(rows)), conditions, set()) == expected


@pytest.mark.asyncio
async def test_candidate_order_is_the_same_on_both_dialects(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.selection import SelectionService

    engine, service, repo = await _open(pick_db_url, tmp_path)
    try:
        await Importer(repo, service.data_dir).catalog(_catalog(SOURCE_IDS), "json")
        result = await SelectionService(repo).query({"limit": 20}, thread_id="t", run_id="r", call_id="c")
        identities = [item["identity"] for item in result["items"]]
        assert identities == sorted(identities) and len(identities) == len(SOURCE_IDS)
    finally:
        await engine.dispose()
