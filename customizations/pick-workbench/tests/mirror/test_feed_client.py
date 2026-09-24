"""The feed v1/v2 HTTP client (plan 4.1, 4.7, 5.2 steps 3-6; P2-2a): as_of, busy and drift, v1, v2 pages, the manifest.

The RealShort side is tests/mirror/fake_realshort.py, shaped after rs 816ca2e; each test names the rs line it leans on.
Configuration, transport, secrets and metrics are in test_feed_transport.py.
"""

import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fake_realshort import BYPASS, EXPORT_TOKEN, FEED_TOKEN, ROW_SENTINEL, busy, make_rows, v1_error, v2_error
from mirror_harness import collect, make_client, only, world

from ggwork_pick.mirror.client import COUNTED_RESOURCES, format_as_of, select_as_of
from ggwork_pick.mirror.errors import AsOfExpiredError, BusyTimeout, ConfigError, ContractError, DriftError, RowTooLargeError, SourceReadError

# rs:src/lib/pick/export-v2-page.ts:22 (what parseAsOf accepts) with the seconds RealShort echoes back (:30).
ECHOED_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00\.000Z$")


# --- as_of ---------------------------------------------------------------------------------------------


def test_as_of_rounds_down_minus_two_and_formats_z():
    as_of = select_as_of(datetime(2026, 9, 23, 12, 34, 56, 789000, tzinfo=UTC))
    assert as_of == datetime(2026, 9, 23, 12, 32, tzinfo=UTC)
    # The exact text of asOf.toISOString() (rs:src/lib/pick/export-v2.ts:508, feed.ts:75).
    assert format_as_of(as_of) == "2026-09-23T12:32:00.000Z"
    beijing = datetime(2026, 9, 23, 20, 34, 5, tzinfo=timezone(timedelta(hours=8)))
    assert format_as_of(select_as_of(beijing)) == "2026-09-23T12:32:00.000Z"


@pytest.mark.parametrize("bad", [datetime(2026, 9, 23, 12, 32), datetime(2026, 9, 23, 12, 32, 1, tzinfo=UTC)])
def test_format_as_of_refuses_naive_or_partial_minutes(bad):
    with pytest.raises(ValueError):
        format_as_of(bad)


def test_select_as_of_refuses_naive_now():
    with pytest.raises(ValueError):
        select_as_of(datetime(2026, 9, 23, 12, 34))


@pytest.mark.asyncio
async def test_manifest_sends_only_as_of():
    fake, clock = world()
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
    (call,) = fake.calls
    # rs:src/lib/pick/export-v2-page.ts:75-78: manifest takes as_of and nothing else.
    assert set(call.params) == {"as_of"}
    assert call.params["as_of"] == "2026-09-23T12:32:00.000Z" == manifest.as_of_text
    assert ECHOED_AS_OF.match(call.params["as_of"])
    assert call.headers["authorization"] == f"Bearer {EXPORT_TOKEN}"
    assert "x-vercel-protection-bypass" not in call.headers
    assert manifest.counts == {name: len(fake.data[name]) for name in COUNTED_RESOURCES}
    assert manifest.fingerprint == fake.fingerprint() and manifest.source_revision == "sha-test"
    assert manifest.latest_snapshot == "2026-09-23" and dict(manifest.snapshot_days) == {"2026-09-22": 2, "2026-09-23": 3}


@pytest.mark.asyncio
async def test_row_and_v1_params_follow_the_whitelist():
    fake, clock = world(sizes={"catalog_rows": 3}, v1_rows=3, page_rows={"catalog_rows": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        await collect(client.pages("catalog_rows", manifest=manifest))
        await collect(client.pages("rs_rows", manifest=manifest, limit=7))
        await collect(client.pages("rs_series_day", manifest=manifest, day="2026-09-22"))
        await collect(client.v1_pages(manifest))
    fp, as_of = manifest.fingerprint, manifest.as_of_text
    rows = only(fake.calls, "catalog_rows")
    assert [set(c.params) for c in rows] == [{"as_of", "fp"}, {"as_of", "fp", "cursor"}, {"as_of", "fp", "cursor"}]
    assert all(c.params["as_of"] == as_of and c.params["fp"] == fp for c in rows)
    assert only(fake.calls, "rs_rows")[0].params == {"as_of": as_of, "fp": fp, "limit": "7"}
    assert only(fake.calls, "rs_series_day")[0].params == {"as_of": as_of, "fp": fp, "day": "2026-09-22"}
    (v1,) = only(fake.calls, "v1")
    # rs:src/lib/pick/feed-map.ts:46-63: v1 takes limit, cursor, as_of and fp; fp only together with as_of.
    assert v1.params == {"limit": "1000", "as_of": as_of, "fp": fp}
    assert v1.headers["authorization"] == f"Bearer {FEED_TOKEN}"


@pytest.mark.asyncio
async def test_bypass_header_goes_on_every_request():
    fake, clock = world(bypass=BYPASS)
    async with make_client(fake, clock, bypass=BYPASS) as client:
        manifest = await client.manifest_when_free()
        await collect(client.pages("rs_ids", manifest=manifest))
        await collect(client.v1_pages(manifest))
    assert [c.headers["x-vercel-protection-bypass"] for c in fake.calls] == [BYPASS] * 3


# --- busy, drift, read_failed --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_busy_waits_and_reselects():
    fake, clock = world(intercept=lambda call: busy() if call.resource == "manifest" and call.n <= 2 else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
    assert clock.sleeps == [60, 60]
    first, *_, third = [c.params["as_of"] for c in only(fake.calls, "manifest")]
    assert third > first and manifest.as_of_text == third == "2026-09-23T12:34:00.000Z"
    assert manifest.busy_sleeps == (60, 60)


@pytest.mark.asyncio
async def test_manifest_busy_gives_up_after_20_minutes():
    fake, clock = world(intercept=lambda call: busy() if call.resource == "manifest" else None)
    async with make_client(fake, clock) as client:
        with pytest.raises(BusyTimeout):
            await client.manifest_when_free()
    assert sum(clock.sleeps) == 1200 and set(clock.sleeps) == {60}


@pytest.mark.asyncio
async def test_manifest_busy_sleeps_what_retry_after_says():
    # RealShort always sends 60 today (rs:src/lib/pick/export-v2.ts:72); the client still honours the header (plan 5.2 step 3).
    odd = v2_error(503, "source_busy", {"retry-after": "30"})
    fake, clock = world(intercept=lambda call: odd if call.resource == "manifest" and call.n == 1 else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
    assert clock.sleeps == [30] and manifest.busy_sleeps == (30,)


@pytest.mark.asyncio
async def test_busy_budget_adds_up_each_retry_after():
    waits = {1: "1000", 2: "200", 3: "1"}
    fake, clock = world(intercept=lambda call: v2_error(503, "source_busy", {"retry-after": waits[call.n]}) if call.resource == "manifest" else None)
    async with make_client(fake, clock) as client:
        with pytest.raises(BusyTimeout) as caught:
            await client.manifest_when_free()
    # 1000 + 200 is exactly the budget and is slept; one more second would pass it.
    assert clock.sleeps == [1000, 200] and caught.value.waited == 1200


@pytest.mark.asyncio
async def test_manifest_409_is_drift():
    # rs:src/lib/pick/export-v2.ts:497-505: a write during the manifest is 409.
    fake, clock = world(intercept=lambda call: v2_error(409, "source_changed"))
    async with make_client(fake, clock) as client:
        with pytest.raises(DriftError):
            await client.manifest_when_free()
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_read_failed_retried_once_then_not_drift():
    # rs:src/lib/pick/feed-http.ts:81-86: any load failure is 503 read_failed without Retry-After.
    fake, clock = world(intercept=lambda call: v2_error(503, "read_failed") if call.resource == "rs_ids" else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(SourceReadError) as caught:
            await collect(client.pages("rs_ids", manifest=manifest))
    assert not isinstance(caught.value, DriftError)
    assert len(only(fake.calls, "rs_ids")) == 2 and clock.sleeps == [5]


@pytest.mark.asyncio
async def test_read_failed_once_recovers_on_the_retry():
    fake, clock = world(intercept=lambda call: v2_error(503, "read_failed") if call.resource == "rs_ids" and call.n == 1 else None)
    seen = []
    async with make_client(fake, clock, on_response=seen.append) as client:
        manifest = await client.manifest_when_free()
        (page,) = await collect(client.pages("rs_ids", manifest=manifest))
    assert page.metrics.attempt == 2 and page.metrics.status == 200
    assert [(m.resource, m.status, m.error) for m in seen] == [("manifest", 200, None), ("rs_ids", 503, "read_failed"), ("rs_ids", 200, None)]
    # The printed line says whether the request was the read_failed retry; "attempt" there is the dry-run's run number.
    assert [m.line()["retried"] for m in seen] == [False, False, True] and not any("attempt" in m.line() for m in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [v2_error(409, "source_changed"), busy()], ids=["409", "503-busy"])
async def test_v2_page_409_and_busy_after_manifest_are_drift(reply):
    fake, clock = world(page_rows={"catalog_rows": 1}, intercept=lambda call: reply if call.resource == "catalog_rows" and call.n == 2 else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(DriftError):
            await collect(client.pages("catalog_rows", manifest=manifest))
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_real_fingerprint_change_mid_resource_is_drift():
    fake, clock = world(page_rows={"catalog_rows": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        pages = client.pages("catalog_rows", manifest=manifest)
        await anext(pages)
        fake.generation += 1
        with pytest.raises(DriftError):
            await anext(pages)


# --- v1 ------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v1_pages_carry_as_of_and_fp():
    fake, clock = world(v1_rows=2500)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        pages = await collect(client.v1_pages(manifest))
    calls = only(fake.calls, "v1")
    assert len(pages) == len(calls) == 3
    assert {(c.params["as_of"], c.params["fp"], c.params["limit"]) for c in calls} == {(manifest.as_of_text, manifest.fingerprint, "1000")}
    assert [len(p.body["rows"]) for p in pages] == [1000, 1000, 500]
    assert pages[0].body["total"] == 2500


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", [v1_error(409, "source_changed"), busy(v2=False)], ids=["409", "503-busy"])
async def test_v1_page2_409_and_busy_are_drift(reply):
    fake, clock = world(v1_rows=2500, intercept=lambda call: reply if call.resource == "v1" and call.n == 2 else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(DriftError):
            await collect(client.v1_pages(manifest))
    assert clock.sleeps == []


def _rewrite_v1_page(fake, change):
    original = fake._v1_page

    def patched(cursor, limit, as_of):
        status, headers, body = original(cursor, limit, as_of)
        return status, headers, json.dumps(change(json.loads(body), cursor)).encode()

    fake._v1_page = patched


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"sourceRevision": "sha-other"}, {"fingerprint": "f" * 64}, {"capturedAt": "2026-09-23T12:31:00.000Z"}],
    ids=["sourceRevision", "fingerprint", "capturedAt"],
)
async def test_v1_page_must_match_the_manifest(changes):
    # plan 5.2 step 5 and 1520-1523; capturedAt is the as_of itself (rs:src/lib/pick/feed.ts:75).
    fake, clock = world(v1_rows=2500)
    _rewrite_v1_page(fake, lambda page, cursor: {**page, **changes} if cursor == "k1999" else page)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(DriftError):
            await collect(client.v1_pages(manifest))
    assert len(only(fake.calls, "v1")) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [{"version": "pick-feed-v2"}, {"ok": False}, {"rows": None}, {"total": None}, {"total": True}, {"nextCursor": 7}],
    ids=["version", "ok", "rows", "total", "total-bool", "cursor"],
)
async def test_v1_keeps_every_sync_check(changes):
    # The checks of ggwork_pick/sync.py:66-81, page by page.
    fake, clock = world(v1_rows=2)
    _rewrite_v1_page(fake, lambda page, cursor: {**page, **changes})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError):
            await collect(client.v1_pages(manifest))


@pytest.mark.asyncio
async def test_v1_stops_after_40_pages():
    fake, clock = world(v1_rows=1)
    _rewrite_v1_page(fake, lambda page, cursor: {**page, "nextCursor": f"c{len(only(fake.calls, 'v1')):04d}"})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError) as caught:
            await collect(client.v1_pages(manifest))
    assert len(only(fake.calls, "v1")) == 40 and "40" in str(caught.value)


@pytest.mark.asyncio
async def test_v1_400_names_the_likely_causes():
    # rs:src/lib/pick/feed-http.ts:60: a v1 400 carries no reason, so the client cannot tell the as_of window apart.
    fake, clock = world(intercept=lambda call: v1_error(400, "bad_request") if call.resource == "v1" else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError) as caught:
            await collect(client.v1_pages(manifest))
    assert "as_of" in str(caught.value) and "时钟" in str(caught.value)


def test_v1_constants_match_sync():
    from ggwork_pick import sync
    from ggwork_pick.mirror import client

    assert (client.V1_VERSION, client.V1_PAGE_LIMIT, client.V1_MAX_PAGES) == (sync.FEED_VERSION, sync.PAGE_LIMIT, sync.MAX_PAGES)


@pytest.mark.asyncio
async def test_v1_needs_its_own_token():
    fake, clock = world()
    async with make_client(fake, clock, feed_token=None) as client:
        manifest = await client.manifest_when_free()
        assert client.has_v1 is False
        with pytest.raises(ConfigError):
            await collect(client.v1_pages(manifest))
    assert only(fake.calls, "v1") == []


# --- v2 pages ------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_v2_row_too_large_not_retried():
    # rs:src/lib/pick/feed-http.ts:91-92: the 500 body has only the resource and the key.
    reply = v2_error(500, "row_too_large", resource="catalog_accounts", key=["acct-big"])
    fake, clock = world(sizes={"catalog_accounts": 1}, intercept=lambda call: reply if call.resource == "catalog_accounts" else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(RowTooLargeError) as caught:
            await collect(client.pages("catalog_accounts", manifest=manifest))
    assert len(only(fake.calls, "catalog_accounts")) == 1 and clock.sleeps == []
    assert (caught.value.resource, caught.value.key) == ("catalog_accounts", ("acct-big",))
    assert "catalog_accounts" in str(caught.value) and "acct-big" in str(caught.value)


def _rewrite_rows_page(fake, resource, change):
    original = fake._rows

    def patched(name, params, as_of):
        status, headers, body = original(name, params, as_of)
        if name != resource or status != 200:
            return status, headers, body
        return status, headers, json.dumps(change(json.loads(body), params)).encode()

    fake._rows = patched


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def test_page_limits_default_to_each_resources_server_maximum():
    # The brief's P2-2a: PAGE_LIMITS is a module constant, by default RealShort's maxLimit per resource (RESOURCE_SPECS,
    # rs:src/lib/pick/export-v2-map.ts:130-191, also its default limit, export-v2-page.ts:107); rs_rows is P1-6's (U47).
    from ggwork_pick.mirror import client

    specs = json.loads((FIXTURES / "export_v2_contract.json").read_text(encoding="utf-8"))["resource_specs"]
    maxima = {resource: spec["maxLimit"] for resource, spec in specs.items()}
    assert dict(client.MAX_LIMITS) == maxima
    assert list(client.PAGE_LIMITS) == list(maxima) and dict(client.PAGE_LIMITS) == maxima
    assert all(1 <= client.PAGE_LIMITS[r] <= maxima[r] for r in maxima)
    with pytest.raises(TypeError):
        client.PAGE_LIMITS["rs_rows"] = 1000


@pytest.mark.asyncio
async def test_a_page_limit_below_the_maximum_is_sent_and_honoured(monkeypatch):
    # What P1-6's first fallback does (--limit rs_rows=1000, then the constant): the page size goes out as limit.
    from ggwork_pick.mirror import client

    monkeypatch.setattr(client, "PAGE_LIMITS", {**client.PAGE_LIMITS, "rs_rows": 1})
    fake, clock = world(sizes={"rs_rows": 2, "rs_ids": 2})
    async with make_client(fake, clock) as feed:
        manifest = await feed.manifest_when_free()
        pages = await collect(feed.pages("rs_rows", manifest=manifest))
        await collect(feed.pages("rs_ids", manifest=manifest))
    assert [p.metrics.rows for p in pages] == [1, 1]
    assert {c.params.get("limit") for c in only(fake.calls, "rs_rows")} == {"1"}
    # At the server's own maximum nothing is sent: RealShort's default is that maximum.
    assert all("limit" not in c.params for c in only(fake.calls, "rs_ids"))


@pytest.mark.asyncio
async def test_repeated_cursor_stops():
    fake, clock = world(sizes={"rs_ids": 5}, page_rows={"rs_ids": 1})
    first_cursor = {}

    def loop(page, params):
        first_cursor.setdefault("c", page["nextCursor"])
        return {**page, "nextCursor": first_cursor["c"]} if "cursor" in params else page

    _rewrite_rows_page(fake, "rs_ids", loop)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError):
            await collect(client.pages("rs_ids", manifest=manifest))
    assert len(only(fake.calls, "rs_ids")) == 2


@pytest.mark.asyncio
async def test_short_pages_are_fine_within_rows_plus_one():
    # RealShort cuts a page at 3,000,000 bytes (rs:src/lib/pick/export-v2-page.ts:207-229): fewer rows than limit is normal.
    fake, clock = world(sizes={"rs_ids": 4}, page_rows={"rs_ids": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        pages = await collect(client.pages("rs_ids", manifest=manifest, limit=3))
    assert [p.metrics.rows for p in pages] == [1, 1, 1, 1]


@pytest.mark.asyncio
async def test_page_cap_is_manifest_rows_plus_one():
    fake, clock = world(sizes={"rs_ids": 6}, counts=None, page_rows={"rs_ids": 1})
    fake.counts = {**{name: len(rows) for name, rows in fake.data.items()}, "rs_ids": 2}
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError):
            await collect(client.pages("rs_ids", manifest=manifest))
    assert len(only(fake.calls, "rs_ids")) == 3


@pytest.mark.asyncio
async def test_a_chain_of_exactly_rows_plus_one_pages_is_accepted():
    # The cap is counts + 1 pages, not ceil(counts / limit) + 2: the (counts + 1)th page still passes when it ends the chain.
    fake, clock = world(sizes={"rs_ids": 3}, page_rows={"rs_ids": 1})
    fake.counts = {**{name: len(rows) for name, rows in fake.data.items()}, "rs_ids": 2}
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        pages = await collect(client.pages("rs_ids", manifest=manifest, limit=2000))
    assert [p.metrics.rows for p in pages] == [1, 1, 1] and pages[-1].body["nextCursor"] is None


@pytest.mark.asyncio
async def test_a_series_day_chain_is_capped_at_its_snapshot_rows_plus_one():
    fake, clock = world(series={"2026-09-23": 1}, page_rows={"rs_series_day": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        # More rows behind the day than the manifest promised (the double does not tie them to the fingerprint).
        fake.series["2026-09-23"] = make_rows("rs_series_day", 5)
        with pytest.raises(ContractError) as caught:
            await collect(client.pages("rs_series_day", manifest=manifest, day="2026-09-23"))
    # snapshotDays promised 1 row: 2 pages are allowed, the second one's cursor ends it.
    assert len(only(fake.calls, "rs_series_day")) == 2 and "2" in str(caught.value)


@pytest.mark.asyncio
async def test_empty_page_with_a_cursor_is_a_contract_error():
    fake, clock = world(sizes={"rs_ids": 3}, page_rows={"rs_ids": 1})
    _rewrite_rows_page(fake, "rs_ids", lambda page, params: {**page, "rows": []})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError):
            await collect(client.pages("rs_ids", manifest=manifest))


@pytest.mark.asyncio
async def test_more_rows_than_limit_is_a_contract_error():
    fake, clock = world(sizes={"rs_ids": 3})
    _rewrite_rows_page(fake, "rs_ids", lambda page, params: {**page, "rows": page["rows"] * 2})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError):
            await collect(client.pages("rs_ids", manifest=manifest, limit=3))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda page, params: {**page, "fingerprint": "e" * 64}, DriftError),
        # The brief's P2-2a v2 page check: only the fingerprint is drift; another resource or asOf is a contract error.
        (lambda page, params: {**page, "asOf": "2026-09-23T12:31:00.000Z"}, ContractError),
        (lambda page, params: {**page, "resource": "rs_rows"}, ContractError),
        (lambda page, params: {**page, "version": "pick-export-v3"}, ContractError),
        (lambda page, params: {**page, "rows": {"a": 1}}, ContractError),
        (lambda page, params: {**page, "nextCursor": 5}, ContractError),
        (lambda page, params: {**page, "extra": 1}, ContractError),
    ],
    ids=["fingerprint", "asOf", "resource", "version", "rows", "cursor", "extra-key"],
)
async def test_v2_page_echo_is_checked_on_every_page(change, error):
    # plan 1523 for v2: page 3 differs; the envelope is rs:src/lib/pick/export-v2.ts:534, :538.
    fake, clock = world(sizes={"rs_ids": 4}, page_rows={"rs_ids": 1})
    _rewrite_rows_page(fake, "rs_ids", lambda page, params: change(page, params) if len(fake.calls) == 4 else page)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(error) as caught:
            await collect(client.pages("rs_ids", manifest=manifest))
    assert len(only(fake.calls, "rs_ids")) == 3
    # A contract error is never drift: the dry-run and the mirror run start over only on drift.
    assert isinstance(caught.value, DriftError) == (error is DriftError)


@pytest.mark.asyncio
async def test_empty_resource_first_page():
    # rs:src/lib/pick/export-v2-page.ts:223-227: nothing left means rows [] and nextCursor null.
    fake, clock = world(sizes={"catalog_accounts": 0})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        (page,) = await collect(client.pages("catalog_accounts", manifest=manifest))
    assert page.body["rows"] == [] and page.body["nextCursor"] is None and page.metrics.rows == 0


@pytest.mark.asyncio
async def test_series_day_row_cap_comes_from_snapshot_days():
    fake, clock = world(series={"2026-09-23": 2}, page_rows={"rs_series_day": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        assert manifest.row_cap("rs_series_day", "2026-09-23") == 2 and manifest.row_cap("rs_series_day", "2026-09-01") == 0
        pages = await collect(client.pages("rs_series_day", manifest=manifest, day="2026-09-23"))
        assert [p.metrics.rows for p in pages] == [1, 1]
        fake.series["2026-09-01"] = fake.series["2026-09-23"]
        with pytest.raises(ContractError):
            await collect(client.pages("rs_series_day", manifest=manifest, day="2026-09-01"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resource", "options"),
    [
        ("rs_series_day", {"day": "2026-06-22"}),
        ("rs_series_day", {"day": "2026-09-24"}),
        ("rs_series_day", {"day": "2026-02-30"}),
        ("rs_series_day", {}),
        ("rs_ids", {"day": "2026-09-23"}),
        ("rs_ids", {"limit": 10001}),
        ("rs_ids", {"limit": 0}),
        ("manifest", {}),
        ("nope", {}),
    ],
)
async def test_bad_page_arguments_refused_before_any_request(resource, options):
    # rs:src/lib/pick/export-v2-page.ts:49-54: day runs from the as_of day back 92 days; limits from :56-61.
    fake, clock = world()
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ValueError):
            await collect(client.pages(resource, manifest=manifest, **options))
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_series_day_window_edges_are_accepted():
    fake, clock = world(series={"2026-06-23": 1, "2026-09-23": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        for day in ("2026-06-23", "2026-09-23"):
            assert len(await collect(client.pages("rs_series_day", manifest=manifest, day=day))) == 1


# --- manifest contract ---------------------------------------------------------------------------------


def _rewrite_manifest(fake, change):
    original = fake._manifest

    def patched(as_of):
        status, headers, body = original(as_of)
        return status, headers, json.dumps(change(json.loads(body))).encode()

    fake._manifest = patched


def _row(change):
    return lambda body: {**body, "rows": [change(body["rows"][0])]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        lambda body: {**body, "rows": []},
        lambda body: {**body, "rows": body["rows"] * 2},
        lambda body: {**body, "nextCursor": "abc"},
        lambda body: {**body, "resource": "catalog_rows"},
        _row(lambda row: {**row, "extra": 1}),
        _row(lambda row: {k: v for k, v in row.items() if k != "meta"}),
        _row(lambda row: {**row, "counts": {**row["counts"], "rs_series_day": 3}}),
        _row(lambda row: {k: v for k, v in row.items() if k != "counts"} | {"counts": {k: v for k, v in row["counts"].items() if k != "rs_ids"}}),
        _row(lambda row: {**row, "counts": {**row["counts"], "rs_ids": -1}}),
        _row(lambda row: {**row, "counts": {**row["counts"], "rs_ids": "2"}}),
        _row(lambda row: {**row, "fingerprint": "f" * 64}),
        _row(lambda row: {**row, "version": "pick-export-v1"}),
        _row(lambda row: {**row, "latestSnapshot": "2026-09-22"}),
        _row(lambda row: {**row, "snapshotDays": list(reversed(row["snapshotDays"]))}),
        _row(lambda row: {**row, "snapshotDays": [{**row["snapshotDays"][0], "extra": 1}]}),
        _row(lambda row: {**row, "meta": {**row["meta"], "extra": 1}}),
        _row(lambda row: {**row, "meta": {**row["meta"], "scrub": {"rs_rows.title": "1"}}}),
        _row(lambda row: {**row, "meta": {**row["meta"], "scrub": {"rs_rows.title ROWVALUE=x": 1}}}),
        _row(lambda row: {**row, "meta": {**row["meta"], "warnings": [{"source": "x"}]}}),
        _row(lambda row: {**row, "sourceRevision": 5}),
        _row(lambda row: {**row, "asOf": "2026-09-23T12:31:00.000Z"}),
    ],
)
async def test_manifest_contract(change):
    # Top-level keys rs:src/lib/pick/export-v2-map.ts:900-909, counts without rs_series_day export-v2.ts:420-437, meta :878-897.
    fake, clock = world()
    _rewrite_manifest(fake, change)
    async with make_client(fake, clock) as client:
        with pytest.raises(ContractError) as caught:
            await client.manifest_when_free()
    assert ROW_SENTINEL not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("day", ["2026-06-22", "2026-09-24"])
async def test_snapshot_days_outside_the_series_window_are_a_contract_error(day):
    # rs:src/lib/pick/export-v2.ts:440-446: snapshotDays runs from the as_of day back 92 days, the window day takes.
    fake, clock = world(series={day: 1, "2026-09-23": 1})
    async with make_client(fake, clock) as client:
        with pytest.raises(ContractError) as caught:
            await client.manifest_when_free()
    assert "snapshotDays" in str(caught.value)


@pytest.mark.asyncio
async def test_manifest_as_of_echo_mismatch_is_a_contract_error():
    # The manifest page is a v2 page too: echoing another asOf answers a request that was not sent, not drift.
    fake, clock = world()
    _rewrite_manifest(fake, lambda body: {**body, "asOf": "2026-09-23T12:30:00.000Z"})
    async with make_client(fake, clock) as client:
        with pytest.raises(ContractError) as caught:
            await client.manifest_when_free()
    assert not isinstance(caught.value, DriftError) and len(only(fake.calls, "manifest")) == 1


@pytest.mark.asyncio
async def test_manifest_keeps_meta_and_null_source_revision():
    fake, clock = world(
        sha=None,
        scrub={"rs_rows.description": 2},
        warnings=[{"code": "catalog_import_incomplete", "source": "pick_catalog", "status": "failed", "attemptedAt": "x"}],
        series={},
    )
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
    assert manifest.source_revision is None and manifest.latest_snapshot is None and dict(manifest.snapshot_days) == {}
    assert dict(manifest.meta["scrub"]) == {"rs_rows.description": 2}
    assert manifest.row["counts"] == dict(manifest.counts)


# --- as_of window --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_refused_locally_after_25_minutes():
    fake, clock = world()
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        clock.advance(25 * 60 + 5)
        with pytest.raises(AsOfExpiredError):
            await collect(client.pages("rs_ids", manifest=manifest))
    assert only(fake.calls, "rs_ids") == []


@pytest.mark.asyncio
async def test_as_of_400_is_expired_not_contract():
    # rs:src/lib/pick/export-v2-page.ts:99-100: RealShort judges the window by its own clock, here 40 minutes ahead.
    fake, clock = world()
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        fake.now = lambda: clock() + timedelta(minutes=40)
        with pytest.raises(AsOfExpiredError) as caught:
            await collect(client.pages("rs_ids", manifest=manifest))
    assert not isinstance(caught.value, ContractError)
    # Also what a local clock more than two minutes ahead of RealShort looks like: as_of lands in its future.
    assert "时钟" in str(caught.value)


@pytest.mark.asyncio
async def test_other_400_reasons_are_contract_errors():
    fake, clock = world(intercept=lambda call: v2_error(400, "bad_request", reason="limit") if call.resource == "rs_ids" else None)
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        with pytest.raises(ContractError) as caught:
            await collect(client.pages("rs_ids", manifest=manifest))
    assert "limit" in str(caught.value)
