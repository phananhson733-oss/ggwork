"""The gates that read a built version (P2-4; plan 5.3, 1546-1554; implementation note P2-4, U49).

PostgreSQL only (skipped when PICK_TEST_PG_URL is unset). gate_world builds one consistent version, manifest and v1 pull
where every gate passes; each case changes one thing. The pure scanners and comparisons are in test_mirror_gates.py.
"""

import json
import re
import threading
from datetime import UTC, datetime

import gate_world as gw
import pytest
import pytest_asyncio
from gate_world import PAN, REPLACEMENT, b64url, baseline, build, text_scan, v1_row, v1_scan, with_counts, with_manifest, with_table, with_v1
from mirror_rows import synthetic_row

from ggwork_pick.mirror import gates
from ggwork_pick.mirror.contracts import parse_manifest

SENTINEL = "资源 https://pan.baidu.com/s/1SENTINELx 提取码：zz99"
T1 = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
SESSION_SETTING = re.compile(r"(?is)^\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")
# Every count the manifest carries that G7 recomputes on the version (queries.ts, observe/queries.ts, queries-rank.ts,
# queries-posted.ts, export-v2.ts readManifestParts), with the baseline's hand count.
COUNT_PATHS = (
    *((("meta", "freshness", key), value) for key, value in gw.FRESHNESS.items() if key not in ("importedAt", "rsSyncedAt")),
    *((("meta", "rsCounts", key), value) for key, value in gw.RS_COUNTS.items()),
    *((("meta", "control", "rankCounts", key), value) for key, value in gw.RANK_COUNTS.items()),
    *((("meta", "control", "postedStats", key), value) for key, value in gw.POSTED_STATS.items() if key not in ("metricAt", "importedAt")),
    *((("meta", "control", "postedStates", key), value) for key, value in gw.POSTED_STATES.items()),
    *((("meta", "control", "ledger", key), value) for key, value in gw.LEDGER.items()),
)


def _dotted(path: tuple) -> str:
    return ".".join(path)


@pytest.fixture
def dsn(pg_db_url):
    from ggwork_pick.mirror.connection import dsn_from_url

    return dsn_from_url(pg_db_url)


@pytest_asyncio.fixture
async def conn(dsn):
    from ggwork_pick.mirror.connection import open_dedicated

    connection = await open_dedicated(dsn)
    yield connection
    await connection.close()


async def _run(conn, world, version=None, **overrides):
    version = version or await build(conn, world)
    arguments = {"schema_name": version.schema_name, "manifest": world.manifest, "v1": v1_scan(world), "text": text_scan(world), **overrides}
    return await gates.run_mirror_gates(conn, **arguments)


def _by_name(outcome) -> dict:
    return {result.name: result for result in outcome.results}


@pytest.mark.asyncio
async def test_baseline_all_pass(conn):
    world = baseline()
    parse_manifest(world.manifest)  # the world's manifest is one RealShort could send
    outcome = await _run(conn, world)
    assert [result.name for result in outcome.results] == list(gates.MIRROR_GATES)
    assert {name: result.as_json() for name, result in _by_name(outcome).items()} == {name: "pass" for name in gates.MIRROR_GATES}
    assert outcome.ok and outcome.first_failure is None
    assert outcome.accept_empty == gates.AcceptEmpty(used=False, seen=None)
    assert dict(outcome.measured) == gw.COUNTS
    assert outcome.as_json() == {name: "pass" for name in gates.MIRROR_GATES}
    assert not conn.is_in_transaction()


@pytest.mark.asyncio
async def test_forbidden_column(conn):
    world = baseline()
    version = await build(conn, world)
    await conn.execute(f"ALTER TABLE {version.schema_name}.catalog_rows ADD COLUMN pan_url text")
    results = _by_name(await _run(conn, world, version))
    assert results["forbidden_columns"].as_json() == {"ok": False, "consequence": "mirror", "paths": {"catalog_rows.pan_url": 1}, "total": 1}
    # has_pan and bill_rank are derived flags and ranks, not the forbidden columns (plan 13.3).
    assert [name for name, result in results.items() if not result.ok] == ["forbidden_columns"]


@pytest.mark.asyncio
async def test_counts_short_by_one(conn):
    world = baseline()
    version = await build(conn, world)
    held = gw.COUNTS["rs_ids"]
    claimed = with_counts(world, rs_ids=held + 1)
    outcome = await _run(conn, claimed, version)
    assert _by_name(outcome)["row_counts"].detail == {"tables": {"rs_ids": {"manifest": held + 1, "mirror": held}}, "total": 1}
    assert outcome.first_failure == "row_counts"


@pytest.mark.parametrize(
    "change, path, row_id",
    [
        (
            lambda w: with_counts(with_table(w, "catalog_signals", [*w.tables["catalog_signals"], gw.signal_row(9, "c-9", "kd", 0)]), catalog_signals=6),
            "catalog_signals.row_key",
            ["catalog_signals", "c-9", "kd", 0],
        ),
        (
            lambda w: with_counts(with_table(w, "rs_ids", [row for row in w.tables["rs_ids"] if row["id"] != "d-3"]), rs_ids=gw.COUNTS["rs_ids"] - 1),
            "rs_rows.drama_id",
            ["rs_rows", "reelshort-d-3"],
        ),
        (
            lambda w: with_counts(
                with_table(w, "rs_clicks14", [*w.tables["rs_clicks14"], synthetic_row("rs_clicks14", 9, drama_id="d-9", day="2026-09-22")]), rs_clicks14=3
            ),
            "rs_clicks14.drama_id",
            ["rs_clicks14", "d-9", "2026-09-22"],
        ),
        # d-6 is in rs_ids (a sibling) but no canonical row: a click must have its rs row, not only an id (plan 787).
        (
            lambda w: with_counts(
                with_table(w, "rs_clicks14", [*w.tables["rs_clicks14"], synthetic_row("rs_clicks14", 9, drama_id="d-6", day="2026-09-22")]), rs_clicks14=3
            ),
            "rs_clicks14.drama_id",
            ["rs_clicks14", "d-6", "2026-09-22"],
        ),
    ],
    ids=["signal-without-row", "rs-row-without-id", "click-without-rs-row", "click-on-a-sibling-id"],
)
@pytest.mark.asyncio
async def test_referential_breaks(conn, change, path, row_id):
    result = _by_name(await _run(conn, change(baseline())))["references"]
    assert result.as_json() == {"ok": False, "consequence": "mirror", "paths": {path: 1}, "rows": [row_id], "total": 1}


@pytest.mark.asyncio
async def test_references_name_twenty_orphans_and_count_all(conn):
    # U49: the first twenty by primary key, and the total over every orphan, not over the twenty read.
    orphans = [gw.signal_row(n, f"x-{n:02d}", "kd", 0) for n in range(25)]
    world = with_table(baseline(), "catalog_signals", [*baseline().tables["catalog_signals"], *orphans])
    world = with_counts(world, catalog_signals=gw.COUNTS["catalog_signals"] + 25)
    result = _by_name(await _run(conn, world))["references"]
    assert result.detail["paths"] == {"catalog_signals.row_key": 25}
    assert result.detail["total"] == 25
    assert [list(row) for row in result.detail["rows"]] == [["catalog_signals", f"x-{n:02d}", "kd", 0] for n in range(20)]


@pytest.mark.asyncio
async def test_bill_book_missing_ok(conn):
    # plan 787: a bill's book_id may be in no rs_ids row; RealShort falls back to book_title.
    world = baseline()
    assert "book-x" not in {row["id"] for row in world.tables["rs_ids"]}
    assert _by_name(await _run(conn, world))["references"].ok


@pytest.mark.asyncio
async def test_control_total_off_by_one(conn):
    world = baseline()
    version = await build(conn, world)
    totals = await gates.control_totals(conn, version.schema_name)
    assert gates.control_totals_gate(totals, world.manifest).ok
    for path, value in COUNT_PATHS:
        result = gates.control_totals_gate(totals, gw.replaced(world.manifest, path, value + 1))
        assert not result.ok, path
        assert result.detail == {"paths": {_dotted(path): 1}, "counts": {_dotted(path): {"manifest": value + 1, "mirror": value}}, "total": 1}, path


@pytest.mark.parametrize(
    "path, claimed",
    [
        (("meta", "freshness", "importedAt"), gw.IMPORTED),
        (("meta", "freshness", "importedAt"), None),
        (("meta", "freshness", "importedAt"), "yesterday"),
        (("meta", "freshness", "importedAt"), "2026-09-22T03:10:06.501Z"),  # a millisecond after IMPORTED_LAST
        (("meta", "control", "postedStats", "importedAt"), gw.POSTED_EARLIER),
        (("meta", "control", "postedStats", "importedAt"), "2026-09-22T03:10:59.999Z"),  # a millisecond before POSTED_LAST
        (("meta", "control", "postedStats", "metricAt"), "2026-09-19"),
        (("meta", "control", "postedStats", "metricAt"), None),
        (("meta", "rsCounts", "gsc"), True),  # 1 as a boolean is not the count 1
    ],
)
@pytest.mark.asyncio
async def test_control_moments_and_days_compare_exactly(conn, path, claimed):
    world = baseline()
    version = await build(conn, world)
    totals = await gates.control_totals(conn, version.schema_name)
    result = gates.control_totals_gate(totals, gw.replaced(world.manifest, path, claimed))
    assert not result.ok
    assert result.detail["paths"] == {_dotted(path): 1}
    assert "yesterday" not in json.dumps(result.as_json())
    # rsSyncedAt is every dramas row's, the version only has canonical ones: never compared (implementation note G7).
    assert gates.control_totals_gate(totals, gw.replaced(world.manifest, ("meta", "freshness", "rsSyncedAt"), None)).ok


@pytest.mark.asyncio
async def test_rank_counts_missing_theater_kind(conn):
    # queries-rank.ts:139-140: only theater kinds with signals have a key; a missing key is 0 on either side.
    world = baseline()
    version = await build(conn, world)
    totals = await gates.control_totals(conn, version.schema_name)
    rank = ("meta", "control", "rankCounts")
    assert [key for key, kind, _ in gw.SIGNALS if kind == "kd"] == ["c-1", "c-4", "c-1"]  # three kd signals, two rows
    assert totals.kinds["kd"] == 2
    assert "qc" not in world.manifest["meta"]["control"]["rankCounts"]
    assert gates.control_totals_gate(totals, gw.replaced(world.manifest, (*rank, "qc"), 0)).ok
    one_sided = gates.control_totals_gate(totals, gw.replaced(world.manifest, (*rank, "qc"), 1))
    assert one_sided.detail["counts"] == {"meta.control.rankCounts.qc": {"manifest": 1, "mirror": 0}}
    missing = gates.control_totals_gate(totals, gw.removed(world.manifest, (*rank, "kd")))
    assert missing.detail["counts"] == {"meta.control.rankCounts.kd": {"manifest": None, "mirror": 2}}
    # zz has signals on the version but is no theater basis: RealShort never counts it, nor does G7.
    assert gates.control_totals_gate(totals, gw.replaced(world.manifest, (*rank, "zz"), 1)).as_json() == {
        "ok": True,
        "consequence": "mirror",
        "unchecked": ["meta.control.rankCounts.zz"],
    }


@pytest.mark.asyncio
async def test_empty_posted_passes_g7_after_accept_empty(conn):
    # queries-posted.ts:251-255: sum over no rows is NULL, the manifest says 0; coalesce makes them agree.
    world = gw.empty_posted(baseline())
    parse_manifest(world.manifest)
    outcome = await _run(conn, world)
    assert _by_name(outcome)["control_totals"].ok
    assert outcome.ok


@pytest.mark.asyncio
async def test_empty_ledger_passes_g7(conn):
    # export-v2.ts:423-436 and observe/queries.ts:554: no bill rows is a ledger of 0 rows and 0 orders on RealShort's
    # side; sum over no rows is NULL here, so control.ledger needs coalesce like postedStats (implementation note G7).
    world = with_counts(with_table(baseline(), "rs_bill_orders", ()), rs_bill_orders=0)
    world = with_manifest(world, ("meta", "rsCounts", "ledger"), 0)
    world = with_manifest(world, ("meta", "control", "rankCounts", "rs_ledger"), 0)
    world = with_manifest(world, ("meta", "control", "ledger"), {"rows": 0, "orders": 0})
    parse_manifest(world.manifest)
    outcome = await _run(conn, world)
    assert _by_name(outcome)["control_totals"].as_json() == "pass"
    assert outcome.ok, outcome.as_json()


@pytest.mark.asyncio
async def test_two_promotion_values_pass(conn):
    # plan 1553, section 14 export-4: two promotion_values of one (bill_date, book_id, promotion_type) are one row with
    # source_rows 2; the ledger counts raw rows, so rsCounts.ledger, rs_ledger and control.ledger.rows are all 2.
    world = with_counts(with_table(baseline(), "rs_bill_orders", [gw.bill_row(1, "2026-09-20", "d-1", "cps", 5, 2)]), rs_bill_orders=1)
    world = with_manifest(world, ("meta", "rsCounts", "ledger"), 2)
    world = with_manifest(world, ("meta", "control", "rankCounts", "rs_ledger"), 2)
    world = with_manifest(world, ("meta", "control", "ledger"), {"rows": 2, "orders": 5})
    outcome = await _run(conn, world)
    assert outcome.ok, outcome.as_json()


@pytest.mark.asyncio
async def test_v1_extra_row_kind_diff_and_sd_diff_on_the_version(conn):
    world = baseline()
    version = await build(conn, world)
    changes = {
        "rows.only_v1": [*world.v1_rows, v1_row("c-3", ("kd",), ())],
        "signals[*].kind": [v1_row("c-1", ("kd", "kw", "zz"), ("SD-1",)), *world.v1_rows[1:]],
        "posted.records": [v1_row("c-1", ("kd", "kw"), ()), *world.v1_rows[1:]],
    }
    for path, rows in changes.items():
        result = _by_name(await _run(conn, with_v1(world, rows), version))["v1_consistency"]
        assert result.detail["paths"] == {path: 1}, path
        assert list(result.detail["rows"]) == [path == "rows.only_v1" and "c-3" or "c-1"]


@pytest.mark.asyncio
async def test_signal_cut_at_50_consistent(conn):
    # feed-map.ts:323: v1 keeps the first 50 signals by ord. 49 sm, then kw at ord 49, then 10 kd: kd never reaches v1.
    # Sorted by kind instead of ord, kd would come first and the set would differ; COPY order is neither.
    kinds = ["sm"] * 49 + ["kw"] + ["kd"] * 10
    signals = [gw.signal_row(n, "c-1", kind, n) for n, kind in reversed(list(enumerate(kinds)))]
    others = [row for row in baseline().tables["catalog_signals"] if row["row_key"] != "c-1"]
    world = with_counts(with_table(baseline(), "catalog_signals", [*signals, *others]), catalog_signals=len(signals) + len(others))
    world = with_v1(world, [v1_row("c-1", ("sm", "kw"), ("SD-1",)), *world.v1_rows[1:]])
    version = await build(conn, world)
    assert _by_name(await _run(conn, world, version))["v1_consistency"].ok
    uncut = [v1_row("c-1", ("sm", "kw", "kd"), ("SD-1",)), *world.v1_rows[1:]]
    result = _by_name(await _run(conn, with_v1(world, uncut), version))["v1_consistency"]
    assert result.detail["paths"] == {"signals[*].kind": 1}


@pytest.mark.asyncio
async def test_rs_flags_follow_the_signals_before_the_cut(conn):
    # feed-map.ts:303-323: clk, bill, gsc go after the signals, then the cut; 49 signals leave room for clk alone.
    extra = [gw.signal_row(n, "reelshort-d-1", "kd", n) for n in range(49)]
    world = with_table(baseline(), "catalog_signals", [*baseline().tables["catalog_signals"], *extra])
    world = with_counts(world, catalog_signals=gw.COUNTS["catalog_signals"] + len(extra))
    rows = [row if row["source_id"] != b64url("reelshort-d-1") else v1_row("reelshort-d-1", ("kd", "clk"), ("SD-2",)) for row in world.v1_rows]
    version = await build(conn, world)
    results = _by_name(await _run(conn, with_v1(world, rows), version))
    assert results["v1_consistency"].ok
    assert not results["references"].ok  # an rs row's catalog signals break G6; G8 is judged on its own
    whole = [row if row["source_id"] != b64url("reelshort-d-1") else v1_row("reelshort-d-1", ("kd", "clk", "bill", "gsc"), ("SD-2",)) for row in rows]
    assert _by_name(await _run(conn, with_v1(world, whole), version))["v1_consistency"].detail["paths"] == {"signals[*].kind": 1}


@pytest.mark.asyncio
async def test_non_bases_kind_ignored(conn):
    # queries-shared.ts:183: loadSignalsFor keeps BASES only; the baseline's zz is on the version and in no v1 row.
    world = baseline()
    assert ("c-1", "zz", 2) in gw.SIGNALS
    version = await build(conn, world)
    candidates = await gates.mirror_candidates(conn, version.schema_name, gates.signal_kinds(world.manifest))
    assert {c.row_key: c.kinds for c in candidates}["c-1"] == frozenset({"kd", "kw"})
    assert _by_name(await _run(conn, world, version))["v1_consistency"].ok


@pytest.mark.asyncio
async def test_sd_scrubbed_before_it_is_compared(conn):
    # queries-shared.ts:210-237, feed-map.ts:268: v1's posted.records are scrubbed like any nested text; v2's sd is not.
    world = gw.with_row(baseline(), "catalog_posted", 0, sd=PAN)
    world = with_v1(world, [v1_row("c-1", ("kd", "kw"), (REPLACEMENT,)), *world.v1_rows[1:]])
    version = await build(conn, world)
    results = _by_name(await _run(conn, world, version))
    assert results["v1_consistency"].ok and results["mirror_text"].ok
    raw = with_v1(world, [v1_row("c-1", ("kd", "kw"), (PAN,)), *world.v1_rows[1:]])
    assert _by_name(await _run(conn, raw, version))["v1_consistency"].detail["paths"] == {"posted.records": 1}


async def _publish(conn, version, at: datetime) -> None:
    """Stand-in for a paired publish of an earlier run: the version becomes the current one."""
    await conn.execute("UPDATE pick_mirror.versions SET status = 'published', published_at = $2 WHERE id = $1", version.id, at)


@pytest.mark.asyncio
async def test_empty_table_blocked_then_accepted(conn):
    # plan 790, 1575: a table that was not empty in the current version and is now is held back until accept-empty.
    earlier = await build(conn, baseline())
    await _publish(conn, earlier, T1)
    world = gw.empty_posted(baseline())
    version = await build(conn, world)
    blocked = await _run(conn, world, version)
    assert _by_name(blocked)["empty_tables"].as_json() == {
        "ok": False,
        "consequence": "mirror",
        "tables": {"catalog_posted": {"current": gw.COUNTS["catalog_posted"], "this_run": 0}},
        "total": 1,
        "accepted": False,
    }
    assert blocked.first_failure == "empty_tables"
    assert blocked.accept_empty == gates.AcceptEmpty(used=False, seen=None)
    await conn.execute("UPDATE pick_mirror.control SET accept_empty_once = true, accept_empty_set_at = $1 WHERE id = 1", T1)
    passed = await _run(conn, world, version)
    assert passed.ok
    assert _by_name(passed)["empty_tables"].detail["accepted"] is True
    # What publish_mirror_pair takes to consume the pass it used (P2-5a: CASE WHEN :used AND set_at = :seen).
    assert passed.accept_empty == gates.AcceptEmpty(used=True, seen=T1)


@pytest.mark.asyncio
async def test_accept_empty_not_used_when_nothing_emptied(conn):
    earlier = await build(conn, baseline())
    await _publish(conn, earlier, T1)
    await conn.execute("UPDATE pick_mirror.control SET accept_empty_once = true, accept_empty_set_at = $1 WHERE id = 1", T1)
    outcome = await _run(conn, baseline())
    assert outcome.ok
    assert _by_name(outcome)["empty_tables"].as_json() == "pass"
    assert outcome.accept_empty == gates.AcceptEmpty(used=False, seen=T1)


@pytest.mark.asyncio
async def test_shrunk_table_is_not_an_emptied_one(conn):
    # plan 790: G9 holds back a table that went from rows to none; one that only lost rows goes out, flag untouched.
    earlier = await build(conn, baseline())
    await _publish(conn, earlier, T1)
    world = with_counts(with_table(baseline(), "catalog_accounts", baseline().tables["catalog_accounts"][:1]), catalog_accounts=1)
    world = with_manifest(world, ("meta", "control", "postedStats", "accountCount"), 1)
    outcome = await _run(conn, world)
    assert outcome.measured["catalog_accounts"] == 1 < gw.COUNTS["catalog_accounts"]
    assert _by_name(outcome)["empty_tables"].as_json() == "pass"
    assert outcome.ok, outcome.as_json()
    assert outcome.accept_empty == gates.AcceptEmpty(used=False, seen=None)


@pytest.mark.asyncio
async def test_only_the_current_published_version_counts_for_g9(conn):
    # The current version is the published one with the latest published_at, not the highest id (plan 3.2); building
    # and failed versions are nobody's current one. Only the current one had no posted rows either.
    current = await build(conn, baseline())
    await _publish(conn, current, datetime(2026, 9, 24, 4, 0, tzinfo=UTC))
    await conn.execute("UPDATE pick_mirror.versions SET counts = counts || '{\"catalog_posted\": 0}'::jsonb WHERE id = $1", current.id)
    superseded = await build(conn, baseline())
    await _publish(conn, superseded, T1)
    failed = await build(conn, baseline())
    await conn.execute("UPDATE pick_mirror.versions SET status = 'failed' WHERE id = $1", failed.id)
    world = gw.empty_posted(baseline())
    assert _by_name(await _run(conn, world))["empty_tables"].ok


class _Recorder:
    """A dedicated connection that records each statement with the timeout it was sent with."""

    def __init__(self, conn):
        self._conn = conn
        self.calls: tuple = ()

    def _call(self, method: str):
        async def call(statement, *args, **kwargs):
            self.calls = (*self.calls, (method, statement, kwargs.get("timeout")))
            return await getattr(self._conn, method)(statement, *args, **kwargs)

        return call

    def __getattr__(self, name: str):
        if name in ("fetch", "fetchrow", "fetchval"):
            return self._call(name)
        raise AttributeError(f"the gates used {name}")


@pytest.mark.asyncio
async def test_gates_only_read_each_statement_with_its_timeout(conn, monkeypatch):
    world = baseline()
    version = await build(conn, world)
    loop_thread = threading.get_ident()
    compared = []
    real = gates.v1_consistency_gate
    monkeypatch.setattr(gates, "v1_consistency_gate", lambda *args: compared.append(threading.get_ident()) or real(*args))
    recorder = _Recorder(conn)
    outcome = await gates.run_mirror_gates(
        recorder, schema_name=version.schema_name, manifest=world.manifest, v1=v1_scan(world), text=text_scan(world), timeout=77
    )
    assert outcome.ok
    assert recorder.calls and {timeout for _, _, timeout in recorder.calls} == {77}
    assert all(statement.lstrip().upper().startswith(("SELECT", "WITH")) for _, statement, _ in recorder.calls)
    assert not any(SESSION_SETTING.search(statement) for _, statement, _ in recorder.calls)
    assert not conn.is_in_transaction()
    assert len(compared) == 1 and compared[0] != loop_thread  # G8's comparison runs in a worker thread (brief 0.3)


@pytest.mark.asyncio
async def test_a_failing_statement_is_a_gate_error_without_values(conn):
    world = baseline()
    version = await build(conn, world)
    await conn.execute(f"DROP TABLE {version.schema_name}.rs_clicks14")
    with pytest.raises(gates.GateError) as caught:
        await _run(conn, world, version)
    assert caught.value.gate == "row_counts"
    assert "UndefinedTableError" in str(caught.value) and "42P01" in str(caught.value)
    assert version.schema_name not in str(caught.value)
    assert not conn.is_in_transaction()


@pytest.mark.asyncio
async def test_gate_record_has_no_values(conn):
    # plan 800, U37: details_json goes to every signed-in user. Every gate fails here, over rows full of one marker text.
    world = baseline()
    world = gw.with_row(world, "catalog_signals", 0, note=SENTINEL)
    world = gw.with_row(world, "catalog_rows", 1, title=SENTINEL, title_cn=SENTINEL)
    world = with_table(world, "rs_ids", world.tables["rs_ids"][:2])
    world = with_manifest(world, ("meta", "rsCounts", "cand"), 9)
    world = with_v1(world, [*world.v1_rows, v1_row("c-3", ("kd",), (), title=SENTINEL)])
    earlier = await build(conn, baseline())
    await _publish(conn, earlier, T1)
    version = await build(conn, with_table(world, "catalog_accounts", ()))
    await conn.execute(f"ALTER TABLE {version.schema_name}.rs_rows ADD COLUMN revenue_usd text")
    outcome = await _run(conn, world, version, v1=v1_scan(world))
    failed = {result.name for result in outcome.results if not result.ok}
    assert failed == set(gates.MIRROR_GATES)
    g2 = gates.v1_text_gate(v1_scan(world))
    text = json.dumps([outcome.as_json(), g2.as_json()], ensure_ascii=False)
    assert "SENTINEL" not in text and "pan.baidu" not in text and "zz99" not in text
    allowed = {"ok", "consequence", "paths", "rows", "total", "tables", "counts", "unchecked", "unscanned", "accepted", "pan", "nul", "surrogates", "rules"}
    for detail in outcome.as_json().values():
        assert set(detail) <= allowed
