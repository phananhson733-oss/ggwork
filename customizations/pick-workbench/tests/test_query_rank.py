"""Synthetic PostgreSQL rank parity at the caller-owned connection boundary.

Expected identities/periods below are frozen examples of the existing board SQL
(queries-rank.ts / rs-queries.ts); no production catalog is read.
"""

import importlib
import json
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from mirror.mirror_rows import MANIFEST, synthetic_row
from sqlalchemy.engine import make_url

from ggwork_pick.completion_contracts import CommonQuery, QueryPeriod
from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS


@pytest_asyncio.fixture
async def rank_db(empty_pg_url):
    import asyncpg

    conn = await asyncpg.connect(make_url(empty_pg_url).set(drivername="postgresql").render_as_string(hide_password=False))
    ddl = Path(__file__).resolve().parents[1] / "ggwork_pick/mirror/ddl.sql"
    await conn.execute("CREATE SCHEMA pickm_v000001")
    await conn.execute(ddl.read_text(encoding="utf-8").replace("__SCHEMA__", "pickm_v000001"))
    async with conn.transaction():
        await conn.execute("SET LOCAL search_path TO pickm_v000001")
        yield conn
    await conn.close()


async def put(conn, table, **values):
    row = synthetic_row(table, 1, nulls=True, **values)
    cols = RESOURCE_COLUMNS[table]
    args = [row[c.name] for c in cols]
    args = [datetime.fromisoformat(v.replace("Z", "+00:00")) if c.type == "ts" and v else json.dumps(v) if c.type == "json" else v for c, v in zip(cols, args)]
    await conn.execute(f"INSERT INTO {table} ({','.join(c.name for c in cols)}) VALUES ({','.join(f'${i}' for i in range(1, len(cols) + 1))})", *args)


async def query(conn, rank, **kwargs):
    module = importlib.import_module("ggwork_pick.query_rank")
    return await module.query_rank(
        conn, CommonQuery(domain="rankings", scope="full_catalog", rank=rank, **kwargs), rules={}, meta={"as_of": "2026-10-08T12:00:00Z"}
    )


@pytest.mark.asyncio
async def test_daily_history_retains_delisted_rows_exact_period_and_counts(rank_db):
    for key, title, off in [("a", "Alpha", None), ("b", "Beta", "2026-10-02"), ("c", "Gamma", None)]:
        await put(rank_db, "catalog_rows", row_key=key, title=title, off_on=off, imported_at="2026-10-08T00:00:00Z")
    for key, history in [("a", [["2026-10-01", 2], ["2026-10-07", 1]]), ("b", [["2026-10-01", 1, "historical"]]), ("c", [["2026-10-07", 2]])]:
        await put(rank_db, "catalog_signals", row_key=key, kind="kd", ord=0, payload={"h": history})
    result = await query(rank_db, "kd", period=QueryPeriod(kind="daily", value="2026-10-01"), limit=1)
    assert result.row_keys == ["b"]
    assert (result.total, result.matched, result.has_more) == (2, 2, True)
    assert result.actual_period == QueryPeriod(kind="daily", value="2026-10-01")
    assert result.period_options == {"days": ["2026-10-07", "2026-10-01"], "weeks": [], "resolution": "exact"}
    assert result.rank_rows[0]["day_rank"] == 1
    assert result.rank_rows[0]["day_note"] == "historical"
    assert result.facets["ranks"]["kd"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "keys", "resolution", "actual"),
    [
        ({"legacy_week_label": "10.1–10.7"}, ["b", "a"], "ambiguous", "2026-10-01"),
        ({"period": QueryPeriod(kind="weekly", value="2025-10-01")}, ["old"], "exact", "2025-10-01"),
        ({"legacy_week_label": "unique"}, ["unique"], "label", "2026-09-01"),
        ({"legacy_week_label": "missing"}, ["b", "a"], "missing", "2026-10-01"),
    ],
)
async def test_week_start_identity_duplicate_signal_and_label_resolution(rank_db, kwargs, keys, resolution, actual):
    for key, history, weeks, ord_ in [
        ("a", [["2026-10-01", "10.1–10.7"]], 2, 0),
        ("a", [["2026-10-01", "10.1–10.7"]], 3, 1),
        ("b", [["2026-10-01", "10.1–10.7"]], 4, 0),
        ("old", [["2025-10-01", "10.1–10.7"]], 100, 0),
        ("unique", [["2026-09-01", "unique"]], 10, 0),
    ]:
        if ord_ == 0:
            await put(rank_db, "catalog_rows", row_key=key, title=key, imported_at="2026-10-08T00:00:00Z")
        await put(rank_db, "catalog_signals", row_key=key, kind="kw", ord=ord_, payload={"h": history, "weeks": weeks})
    result = await query(rank_db, "kw", **kwargs)
    assert result.row_keys == keys
    assert result.matched == len(keys)
    assert result.actual_period == QueryPeriod(kind="weekly", value=actual)
    assert result.period_options["resolution"] == resolution
    if actual == "2026-10-01":
        assert result.rank_rows[1]["signal"]["ord"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["sm", "mg", "smd", "fh", "sh", "gh", "gn", "ghh", "dbn"])
async def test_static_rank_distinct_grades_null_and_title_ties(rank_db, kind):
    for key, grade, evidence, listed in [
        ("a", "A", "2026-10-01", "2026-10-01"),
        ("b", "SS", "2026-10-07", None),
        ("c", "SS", None, "2026-10-02"),
        ("d", "other", None, None),
    ]:
        await put(rank_db, "catalog_rows", row_key=key, title="same", listed_on=listed, imported_at="2026-10-08T00:00:00Z")
        await put(rank_db, "catalog_signals", row_key=key, kind=kind, ord=0, grade=grade, evidence_on=evidence, payload={})
        await put(rank_db, "catalog_signals", row_key=key, kind=kind, ord=1, grade="D", evidence_on=None, payload={})
    result = await query(rank_db, kind)
    assert result.row_keys == (["c", "b", "a", "d"] if kind in ("sm", "mg") else ["b", "a", "c", "d"])
    assert result.total == 4
    assert result.actual_period == QueryPeriod()
    if kind in ("sm", "mg"):
        filtered = await query(rank_db, kind, grade="SS")
        assert filtered.row_keys == ["c", "b"]
        assert (filtered.total, filtered.matched) == (4, 2)
        assert filtered.facets["grades"] == {"SS": 2, "A": 1, "D": 4}


async def seed_rs(conn):
    for id_, rr, promoters, click, gsc, bill, bill_rank, baseline, signal, locale, publish in [
        ("a", 0.3, 3, 2, 10, 1, 2, 0.2, True, "en", "2026-10-01T23:59:00Z"),
        ("b", 0.4, 4, 4, 5, 2, 1, 0.3, True, "en", "2026-09-08T23:59:00Z"),
        ("c", 9.0, 0, 0, 0, 0, None, None, False, "fr", None),
    ]:
        await put(
            conn,
            "rs_rows",
            row_key=f"reelshort-{id_}",
            drama_id=id_,
            title=f"Title {id_}",
            locale=locale,
            rr=rr,
            promoters_cnt=promoters,
            clicks7=click,
            search_impressions=gsc,
            bill_orders=bill,
            bill_rank=bill_rank,
            rr1=baseline,
            rr7=baseline,
            p1=1 if baseline is not None else None,
            p7=2 if baseline is not None else None,
            s1_rr=baseline,
            s7_rr=baseline,
            s1_p=1 if baseline is not None else None,
            s7_p=2 if baseline is not None else None,
            has_signal=signal,
            publish_at=publish,
        )
    await put(conn, "rs_ids", id="sibling", canonical_id="a")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sort", "keys"),
    [
        ("rr", ["c", "b", "a"]),
        ("d1", ["a", "b", "c"]),
        ("d7", ["a", "b", "c"]),
        ("dp1", ["b", "a", "c"]),
        ("dp7", ["b", "a", "c"]),
        ("promoters", ["b", "a", "c"]),
        ("publish", ["a", "b", "c"]),
        ("bill", ["b", "a", "c"]),
        ("eff", ["a", "b", "c"]),
        ("gsc", ["a", "b", "c"]),
        ("clicks", ["b", "a", "c"]),
    ],
)
async def test_rs_sort_numeric_ties_nulls_and_deterministic_identity(rank_db, sort, keys):
    await seed_rs(rank_db)
    result = await query(rank_db, "rs_rr", rs_sort=sort, limit=2)
    assert result.row_keys == [f"reelshort-{k}" for k in keys[:2]]
    assert (result.total, result.matched, result.legacy_total, result.has_more) == (3, 3, 3, True)
    assert result.effective_sort == sort


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "sort", "keys"),
    [
        ("rs_growth", "d7", ["a", "b"]),
        ("rs_cand", "rr", ["b", "a"]),
        ("rs_pc", "promoters", ["b", "a"]),
        ("rs_clk", "clicks", ["b", "a"]),
        ("rs_gsc", "gsc", ["a", "b"]),
        ("rs_bill", "bill", ["b", "a"]),
    ],
)
async def test_rs_declared_rank_membership_and_forced_sort(rank_db, kind, sort, keys):
    await seed_rs(rank_db)
    result = await query(rank_db, kind)
    assert result.row_keys == [f"reelshort-{k}" for k in keys]
    assert result.effective_sort == sort
    assert result.matched == 2
    assert result.legacy_total == (None if kind == "rs_growth" else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "keys"),
    [
        ({"rs_locale": "fr"}, ["c"]),
        ({"rs_bucket": "0-7"}, ["a"]),
        ({"rs_bucket": "8-30"}, ["b"]),
        ({"rs_bucket": "31-90"}, []),
        ({"query": "sibling"}, ["a"]),
        ({"query": "Title b"}, ["b"]),
        ({"query": "' OR true --"}, []),
    ],
)
async def test_rs_bound_filters_use_pinned_utc_day_and_sibling_identity(rank_db, kwargs, keys):
    await seed_rs(rank_db)
    result = await query(rank_db, "rs_rr", **kwargs)
    assert result.row_keys == [f"reelshort-{k}" for k in keys]
    assert result.total == 3
    assert result.matched == len(keys)


@pytest.mark.asyncio
async def test_ledger_preserves_unmapped_titles_and_full_totals_under_pagination(rank_db):
    await put(rank_db, "rs_ids", id="a", title="Canonical title", locale="en", canonical_id="a")
    for day, book, orders, sources, clicks in [("2026-10-07", "a", 2, 3, 4), ("2026-10-07", "gone", 3, 2, 0), ("2026-10-01", "a", 1, 1, 1)]:
        await put(
            rank_db,
            "rs_bill_orders",
            bill_date=day,
            book_id=book,
            promotion_type="test",
            canonical_id="a" if book == "a" else None,
            book_title="Removed title",
            order_cnt=orders,
            source_rows=sources,
            same_day_clicks=clicks,
        )
    result = await query(rank_db, "rs_ledger", limit=1)
    assert (result.total, result.matched, result.has_more) == (3, 3, True)
    assert result.bill_rows == [
        {
            "bill_date": "2026-10-07",
            "book_id": "gone",
            "promotion_type": "test",
            "canonical_id": None,
            "title": "Removed title",
            "locale": "",
            "order_cnt": 3,
            "source_rows": 2,
            "same_day_clicks": 0,
        }
    ]
    assert result.bill_totals == {"rows": 6, "merged_rows": 3, "orders": 6, "merged_with_clicks": 2, "rows_with_clicks": 4}
    assert result.row_keys == []  # An unmapped ledger record is not a selectable drama.
    second = await query(rank_db, "rs_ledger", limit=1, offset=1)
    assert second.bill_rows[0]["title"] == "Canonical title"
    assert second.row_keys == ["reelshort-a"]


@pytest.mark.asyncio
async def test_all_rank_facets_are_global_meta_counts_not_filtered_page_counts(rank_db):
    from ggwork_pick.query_rank import query_rank

    await seed_rs(rank_db)
    meta = {
        "rsCounts": {"all": 3, "cand": 2, "growthD1": 1, "growthD7": 2, "growthDp1": 3, "growthDp7": 4, "pc": 2, "clk": 2, "gsc": 2, "bill": 2, "ledger": 6}
    }
    req = CommonQuery(domain="rankings", scope="full_catalog", rank="rs_rr", rs_locale="fr", rs_sort="dp1")
    result = await query_rank(rank_db, req, rules={}, meta=meta)
    assert result.facets["ranks"] == {"rs_rr": 3, "rs_cand": 2, "rs_growth": 3, "rs_pc": 2, "rs_clk": 2, "rs_gsc": 2, "rs_bill": 2, "rs_ledger": 6}


@pytest.mark.asyncio
async def test_growth_cap_is_explicit_and_full_match_count_is_not_lost(rank_db):
    for n in range(55):
        await put(rank_db, "rs_rows", row_key=f"reelshort-{n:02}", drama_id=f"{n:02}", rr=100.0, s7_rr=float(n), rr7=float(n))
    result = await query(rank_db, "rs_growth", limit=200)
    assert result.row_keys == [f"reelshort-{n:02}" for n in range(50)]
    assert (result.total, result.matched, result.rank_limit, result.legacy_total) == (55, 55, 50, None)
    assert result.has_more is False  # No further page inside the top-50 rank.
    page = await query(rank_db, "rs_growth", offset=40, limit=20)
    assert page.row_keys == [f"reelshort-{n:02}" for n in range(40, 50)]
    assert page.matched == 55
    assert page.has_more is False


@pytest.mark.asyncio
async def test_expired_parent_deadline_fails_before_sql_and_leaves_connection_usable(rank_db):
    import asyncio

    from ggwork_pick.query_rank import query_rank

    with pytest.raises(TimeoutError):
        await query_rank(
            rank_db, CommonQuery(domain="rankings", scope="full_catalog", rank="kd"), rules={}, meta={}, deadline=asyncio.get_running_loop().time() - 1
        )


@pytest.mark.asyncio
async def test_counterpart_typescript_loaders_on_same_committed_synthetic_mirror(empty_pg_url, tmp_path):
    """Existing TS implementation is an independent differential oracle, not a ported mock."""
    import asyncio
    import os

    import asyncpg

    from ggwork_pick.query_rank import query_rank

    root = Path(__file__).resolve().parents[3]
    binary = root / "frontend/node_modules/.bin/rstest"
    if not binary.exists():
        pytest.skip("frontend dependencies required for cross-language parity")
    url = make_url(empty_pg_url).set(drivername="postgresql").render_as_string(hide_password=False)
    conn = await asyncpg.connect(url)
    ddl = root / "customizations/pick-workbench/ggwork_pick/mirror/ddl.sql"
    await conn.execute("CREATE SCHEMA pickm_v000001")
    await conn.execute(ddl.read_text(encoding="utf-8").replace("__SCHEMA__", "pickm_v000001"))
    as_of = "2026-10-08T12:00:00Z"
    rs_counts = {
        "all": 58,
        "cand": 57,
        "growthD1": 2,
        "growthD7": 57,
        "growthDp1": 2,
        "growthDp7": 2,
        "pc": 57,
        "clk": 57,
        "gsc": 57,
        "bill": 57,
        "ledger": 209,
    }
    kinds = ["kd", "kw", "qc", "qr", "sm", "smd", "mg", "fh", "sh", "gh", "gn", "ghh", "dbn"]
    async with conn.transaction():
        await conn.execute("SET LOCAL search_path TO pickm_v000001")
        await seed_rs(conn)
        for n in range(55):
            await put(conn, "rs_rows", row_key=f"reelshort-x{n:02}", drama_id=f"x{n:02}", rr=100.0, s7_rr=float(n), rr7=float(n), has_signal=True)
        for key, title, evidence, listed, grade in [
            ("a", "Á", None, None, "A"),
            ("b", "a", "2026-10-01", "2026-09-01", "SSS"),
            ("c", "a", "2026-10-01", "2026-09-01", "SS"),
        ]:
            await put(
                conn,
                "catalog_rows",
                row_key=key,
                title=title,
                listed_on=listed,
                off_on="2026-10-02" if key == "c" else None,
                imported_at="2026-10-08T00:00:00Z",
            )
            for kind in kinds:
                history = (
                    [["2026-10-01", 2 if key == "a" else 1, "note"], ["2026-10-07", 3]]
                    if kind in ("kd", "qc", "qr")
                    else [["2025-10-01", "same"], ["2026-10-01", "same"], ["2026-09-01", "unique"]]
                )
                await put(
                    conn,
                    "catalog_signals",
                    row_key=key,
                    kind=kind,
                    ord=0,
                    grade=grade,
                    evidence_on=evidence,
                    payload={"h": history, "weeks": 1 if key == "a" else 2},
                )
        for book, orders in [("a", 1), ("gone", 2)]:
            await put(
                conn,
                "rs_bill_orders",
                bill_date="2026-10-01",
                book_id=book,
                promotion_type="test",
                canonical_id="a" if book == "a" else None,
                book_title="Fallback",
                order_cnt=orders,
                source_rows=2,
                same_day_clicks=1 if book == "a" else 0,
            )
        for n in range(205):
            await put(
                conn,
                "rs_bill_orders",
                bill_date="2026-10-02",
                book_id=f"ledger-{n:03}",
                promotion_type="test",
                canonical_id=None,
                book_title="Historic unmapped",
                order_cnt=1,
                source_rows=1,
                same_day_clicks=0,
            )
        await conn.execute("INSERT INTO meta VALUES ('rsCounts', $1::jsonb), ('sources', '{}'::jsonb)", json.dumps(rs_counts))
    cases = [(kind, {}, {}) for kind in kinds + ["rs_rr", "rs_growth", "rs_cand", "rs_pc", "rs_clk", "rs_gsc", "rs_bill", "rs_ledger"]]
    for kind in ("kd", "qc", "qr"):
        for day in ("2026-10-01", "2025-01-01"):
            cases.append((kind, {"day": day}, {"period": QueryPeriod(kind="daily", value=day)}))
    for week in ("2025-10-01", "missing", "same", "unique"):
        cases.append(("kw", {"week": week}, {"period": QueryPeriod(kind="weekly", value=week)} if week.startswith("2025") else {"legacy_week_label": week}))
    for kind in ("sm", "mg"):
        cases.append((kind, {"grade": "SS"}, {"grade": "SS"}))
    for sort in ("rr", "d1", "d7", "dp1", "dp7", "promoters", "publish", "bill", "eff", "gsc", "clicks"):
        cases.append(("rs_rr", {"rs": sort}, {"rs_sort": sort}))
    for bucket in ("0-7", "8-30", "31-90", "91-365", "366+"):
        cases.append(("rs_rr", {"bk": bucket}, {"rs_bucket": bucket}))
    cases.extend([("rs_rr", {"q": "sibling"}, {"query": "sibling"}), ("rs_rr", {"rl": "fr"}, {"rs_locale": "fr"}), ("kd", {"page": "2"}, {"offset": 20})])
    cases.extend([("rs_rr", {"page": "2"}, {"offset": 20}), ("rs_cand", {"page": "3"}, {"offset": 40}), ("rs_growth", {"rs": "dp1"}, {"rs_sort": "dp1"})])
    wire_cases = []
    async with conn.transaction(readonly=True):
        await conn.execute("SET LOCAL search_path TO pickm_v000001")
        for kind, params, args in cases:
            result = await query_rank(
                conn,
                CommonQuery(
                    domain="rankings",
                    scope="full_catalog",
                    rank=kind,
                    **({"limit": 50, **args} if kind == "rs_growth" else {"limit": 200, **args} if kind == "rs_ledger" else args),
                ),
                rules={},
                meta={"as_of": as_of, "rsCounts": rs_counts},
            )
            expected = {
                "facets": result.facets,
                "period_options": result.period_options,
                "actual_period": result.actual_period.model_dump() if result.actual_period else None,
            }
            if kind == "rs_ledger":
                expected.update(bill_rows=result.bill_rows, bill_totals=result.bill_totals)
            else:
                expected.update(row_keys=result.row_keys, legacy_total=result.legacy_total)
                if kind.startswith("rs_"):
                    expected["effective_sort"] = result.effective_sort
                else:
                    expected["rank_rows"] = result.rank_rows
            wire_cases.append({"params": {"rk": kind, "size": "20", **params}, "expected": expected})
    await conn.close()
    fixture = tmp_path / "rank-parity.json"
    fixture.write_text(json.dumps({"url": url, "asOf": as_of, "rules": MANIFEST["meta"]["rules"], "cases": wire_cases}), encoding="utf-8")
    fixture.chmod(0o600)
    env = {**os.environ, "PICK_RANK_PARITY_CASES": str(fixture)}
    process = await asyncio.create_subprocess_exec(
        str(binary),
        "run",
        "tests/unit/server/pick-board/query-rank-parity.integration.test.ts",
        "--reporter",
        "json",
        "--silent=true",
        cwd=root / "frontend",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    output, _ = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode == 0, output.decode()
    report_text = output.decode()
    report = json.loads(report_text[report_text.index("{") :])
    assert report["status"] == "pass", report_text
    summary = report["summary"]
    assert {key: summary[key] for key in ("testFiles", "tests", "passedTests", "failedTests", "skippedTests")} == {
        "testFiles": 1,
        "tests": 1,
        "passedTests": 1,
        "failedTests": 0,
        "skippedTests": 0,
    }, report_text


@pytest.mark.asyncio
async def test_rank_count_meta_scalar_fallback_matches_legacy_reader(rank_db):
    from ggwork_pick.query_rank import query_rank

    req = CommonQuery(domain="rankings", scope="full_catalog", rank="rs_rr")
    result = await query_rank(rank_db, req, rules={}, meta={"rsCounts": {"all": None, "cand": "bad", "pc": "2", "clk": True}})
    assert result.facets["ranks"]["rs_rr"] == 0
    assert result.facets["ranks"]["rs_cand"] == 0
    assert result.facets["ranks"]["rs_pc"] == 2
    assert result.facets["ranks"]["rs_clk"] == 1


@pytest.mark.asyncio
async def test_week_options_ignore_invalid_entries_as_legacy_reader_does(rank_db):
    await put(rank_db, "catalog_rows", row_key="a", title="A", imported_at="2026-10-08T00:00:00Z")
    await put(
        rank_db,
        "catalog_signals",
        row_key="a",
        kind="kw",
        ord=0,
        payload={"weeks": 1, "h": [["z-invalid", "bad"], ["2026-10-01", "valid"], ["2026-10-07", ""]]},
    )
    result = await query(rank_db, "kw")
    assert result.period_options["weeks"] == [{"start": "2026-10-01", "week": "valid"}]
    assert result.row_keys == ["a"]


@pytest.mark.asyncio
async def test_active_sql_wait_cancels_within_query_budget(rank_db, empty_pg_url):
    import asyncpg

    from ggwork_pick.query_rank import query_rank

    blocker = await asyncpg.connect(make_url(empty_pg_url).set(drivername="postgresql").render_as_string(hide_password=False))
    try:
        async with blocker.transaction():
            await blocker.execute("LOCK TABLE pickm_v000001.catalog_signals IN ACCESS EXCLUSIVE MODE")
            with pytest.raises(TimeoutError):
                async with rank_db.transaction():
                    await query_rank(rank_db, CommonQuery(domain="rankings", scope="full_catalog", rank="kd", budget_ms=50), rules={}, meta={})
        assert await rank_db.fetchval("SELECT 1") == 1
    finally:
        await blocker.close()
