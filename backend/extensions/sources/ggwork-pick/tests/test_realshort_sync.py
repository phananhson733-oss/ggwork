import json

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TOKEN = "feed-token-for-tests"


def feed_row(i, **extra):
    row = {
        "source": "realshort-pick",
        "source_id": f"row{i}",
        "language": "en",
        "title": f"Feed Drama {i}",
        "theater": "KalosTV",
        "tags": [],
        "listed_at": "2026-09-01",
        "availability": "unknown",
        "signals": [{"kind": "kd", "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i, "grade": "", "note": ""}],
        "channel_rules": {"youtube": "unknown"},
        "detail_url": f"ref:{i}",
        "posted": {"matched": False, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []},
    }
    return {**row, **extra}


RULES = "# RealShort 选剧规则与口径\n\n## KalosTV\nYouTube：禁"


def feed_transport(rows, *, page=2, total=None, seen=None, fail_on=None, rules=RULES):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"ok": False})
        cursor = request.url.params.get("cursor", "")
        start = int(cursor) if cursor else 0
        if fail_on is not None and start == fail_on:
            return httpx.Response(503, json={"ok": False, "error": "read_failed"})
        chunk = rows[start : start + page]
        body = {
            "ok": True,
            "version": "pick-feed-v1",
            "capturedAt": "2026-09-23T03:00:00.000Z",
            "scope": "test scope",
            "total": (len(rows) if total is None else total) if not cursor else None,
            "freshness": {"catalogImportedAt": "2026-09-22T03:19:21.327Z", "reelshortSyncedAt": "2026-09-23T00:05:56.475Z"} if not cursor else None,
            "rules": rules if not cursor else None,
            "rows": chunk,
            "nextCursor": str(start + page) if start + page < len(rows) else None,
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest_asyncio.fixture
async def service(pick_db_url, tmp_path):
    from ggwork_pick.service import PickService

    engine = create_async_engine(pick_db_url)
    svc = PickService(tmp_path / "files")
    await svc.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield svc
    await engine.dispose()


def make_sync(service, transport, **kwargs):
    from ggwork_pick.sync import RealShortSync

    return RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=transport, **kwargs)


@pytest.mark.asyncio
async def test_sync_publishes_one_shared_batch_visible_to_every_user(service):
    from ggwork_pick.repository import PickRepository

    seen = []
    rows = [feed_row(i) for i in range(1, 6)]
    outcome = await make_sync(service, feed_transport(rows, seen=seen)).run("cron")
    assert outcome["status"] == "success"
    assert outcome["rows"] == 5
    assert [r.url.params.get("cursor", "") for r in seen] == ["", "2", "4"]
    for user in ("alice", "bob"):
        repo = PickRepository(service.session_factory, user)
        catalog = await repo.current_batch("catalog")
        knowledge = await repo.current_batch("knowledge")
        assert catalog["id"] == outcome["catalog_batch_id"]
        assert knowledge["id"] == outcome["knowledge_batch_id"]
        assert catalog["source_as_of"] == "2026-09-23T03:00:00.000Z"
        assert catalog["validation_json"]["freshness"]["catalogImportedAt"] == "2026-09-22T03:19:21.327Z"
        assert len(await repo.catalog_rows(catalog["id"])) == 5
    runs = await PickRepository.shared(service.session_factory).sync_runs()
    assert runs[0]["status"] == "success" and runs[0]["trigger"] == "cron"


@pytest.mark.asyncio
async def test_sync_rejects_incomplete_feed_and_keeps_previous_batch(service):
    from ggwork_pick.repository import PickRepository

    good = await make_sync(service, feed_transport([feed_row(1), feed_row(2)])).run("manual")
    short = await make_sync(service, feed_transport([feed_row(i) for i in range(1, 4)], total=40)).run("cron")
    assert short["status"] == "failed" and "条数" in short["error"]
    broken = await make_sync(service, feed_transport([feed_row(i) for i in range(1, 6)], fail_on=2)).run("cron")
    assert broken["status"] == "failed" and "503" in broken["error"]
    bad_token = (
        await make_sync(service, feed_transport([feed_row(1)]))
        .__class__(service, base_url="https://realshort.test", token="wrong", transport=feed_transport([feed_row(1)]))
        .run("cron")
    )
    assert bad_token["status"] == "failed" and "401" in bad_token["error"]
    assert TOKEN not in json.dumps([short, broken, bad_token])
    catalog = await PickRepository(service.session_factory, "alice").current_batch("catalog")
    assert catalog["id"] == good["catalog_batch_id"]


@pytest.mark.asyncio
async def test_duplicate_source_ids_are_rejected(service):
    outcome = await make_sync(service, feed_transport([feed_row(1), feed_row(1)])).run("cron")
    assert outcome["status"] == "failed"


@pytest.mark.asyncio
async def test_unchanged_feed_reuses_batch_and_old_unreferenced_batches_are_pruned(service):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    first = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    again = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    assert again["status"] == "success" and again["catalog_batch_id"] == first["catalog_batch_id"]
    alice = PickRepository(service.session_factory, "alice")
    kept = await SelectionService(alice).query({"language": "en"}, thread_id="t", run_id="r", call_id="c")
    assert kept["catalog_batch_id"] == first["catalog_batch_id"]
    ids = [first["catalog_batch_id"]]
    for i in range(2, 7):
        ids.append((await make_sync(service, feed_transport([feed_row(i)]), keep_batches=2).run("cron"))["catalog_batch_id"])
    shared = PickRepository.shared(service.session_factory)
    published = {b["id"] for b in await shared.batches() if b["kind"] == "catalog" and b["status"] == "published"}
    # the referenced first batch survives, the latest two survive, the rest are pruned
    assert published == {ids[0], ids[-1], ids[-2]}
    assert (await alice.result(kept["id"]))["catalog_batch_id"] == ids[0]
    assert len(await alice.catalog_rows(ids[0])) == 1


@pytest.mark.asyncio
async def test_concurrent_sync_reports_already_running(service):
    import asyncio

    sync = make_sync(service, feed_transport([feed_row(i) for i in range(1, 4)], page=1))
    first, second = await asyncio.gather(sync.run("cron"), sync.run("manual"))
    assert sorted([first["status"], second["status"]]) == ["already_running", "success"]


def test_shared_owner_cannot_be_a_user():
    from ggwork_pick.repository import SHARED_OWNER, PickRepository

    with pytest.raises(ValueError):
        PickRepository(None, SHARED_OWNER)


@pytest.mark.asyncio
async def test_small_paging_drift_on_a_live_source_is_accepted_and_recorded(service):
    from ggwork_pick.repository import PickRepository

    outcome = await make_sync(service, feed_transport([feed_row(i) for i in range(1, 4)], total=2)).run("cron")
    assert outcome["status"] == "success" and outcome["rows"] == 3
    catalog = await PickRepository(service.session_factory, "alice").current_batch("catalog")
    assert catalog["validation_json"]["paging_drift"] == 1
    assert catalog["validation_json"]["source_total"] == 2


async def _current(service, kind):
    from ggwork_pick.repository import PickRepository

    return await PickRepository(service.session_factory, "alice").current_batch(kind)


@pytest.mark.asyncio
async def test_feed_reverting_to_earlier_content_becomes_current_again(service):
    first = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    await make_sync(service, feed_transport([feed_row(2)], rules=RULES + "\n## ShortMax")).run("cron")
    back = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    assert back["status"] == "success" and back["catalog_batch_id"] == first["catalog_batch_id"]
    assert (await _current(service, "catalog"))["id"] == first["catalog_batch_id"]
    assert (await _current(service, "knowledge"))["id"] == first["knowledge_batch_id"]


@pytest.mark.asyncio
async def test_old_references_age_out_and_pruned_batches_lose_their_blob(service):
    from pathlib import Path

    from sqlalchemy import text

    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    alice = PickRepository(service.session_factory, "alice")
    old = await make_sync(service, feed_transport([feed_row(1), feed_row(2)])).run("cron")
    aged = await SelectionService(alice).query({"limit": 1}, thread_id="t", run_id="r1", call_id="c1")
    recent_batch = await make_sync(service, feed_transport([feed_row(3), feed_row(4)]), keep_batches=1).run("cron")
    recent = await SelectionService(alice).query({"limit": 1}, thread_id="t", run_id="r2", call_id="c2")
    async with service.session_factory() as session, session.begin():
        await session.execute(text("update ggwp_candidate_sets set created_at = '2026-01-01T00:00:00+00:00' where id = :id"), {"id": aged["id"]})
    shared = PickRepository.shared(service.session_factory)
    blob = Path(next(b for b in await shared.batches() if b["id"] == old["catalog_batch_id"])["raw_blob_path"])
    assert blob.exists()
    await make_sync(service, feed_transport([feed_row(5)]), keep_batches=1).run("cron")
    status = {b["id"]: b["status"] for b in await shared.batches()}
    assert status[old["catalog_batch_id"]] == "pruned" and not blob.exists()
    assert status[recent_batch["catalog_batch_id"]] == "published"
    # The old card still shows; only 换一批 on its pruned data is refused.
    assert (await alice.result(aged["id"]))["ordered_items_json"]
    with pytest.raises(ValueError, match="保留期"):
        await SelectionService(alice).query({"exclude_previous": True}, thread_id="t", run_id="r3", call_id="c3", parent_result_id=aged["id"])
    assert recent["catalog_batch_id"] == recent_batch["catalog_batch_id"]


@pytest.mark.asyncio
async def test_synced_rows_are_stored_once_and_low_disk_refuses_to_publish(service):
    from ggwork_pick.repository import PickRepository

    done = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    rows = await PickRepository(service.session_factory, "alice").catalog_rows(done["catalog_batch_id"])
    assert "original" not in rows[0]
    full = await make_sync(service, feed_transport([feed_row(2)]), min_free_bytes=10**18).run("cron")
    assert full["status"] == "failed" and "磁盘" in full["error"]
    assert (await _current(service, "catalog"))["id"] == done["catalog_batch_id"]


@pytest.mark.asyncio
async def test_a_full_disk_still_reclaims_batches_whose_references_aged_out(service):
    from pathlib import Path

    from sqlalchemy import text

    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    alice = PickRepository(service.session_factory, "alice")
    old = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    card = await SelectionService(alice).query({"limit": 1}, thread_id="t", run_id="r1", call_id="c1")
    await make_sync(service, feed_transport([feed_row(2)]), keep_batches=1).run("cron")
    shared = PickRepository.shared(service.session_factory)
    blob = Path(next(b for b in await shared.batches() if b["id"] == old["catalog_batch_id"])["raw_blob_path"])
    async with service.session_factory() as session, session.begin():
        await session.execute(text("update ggwp_candidate_sets set created_at = '2026-01-01T00:00:00+00:00' where id = :id"), {"id": card["id"]})
    full = await make_sync(service, feed_transport([feed_row(3)]), keep_batches=1, min_free_bytes=10**18).run("cron")
    assert full["status"] == "failed" and "磁盘" in full["error"]
    status = {b["id"]: b["status"] for b in await shared.batches()}
    assert status[old["catalog_batch_id"]] == "pruned" and not blob.exists()


@pytest.mark.asyncio
async def test_bad_rules_fail_before_the_catalog_is_published(service):
    good = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    bad = await make_sync(service, feed_transport([feed_row(2)], rules="# 规则\x00")).run("cron")
    assert bad["status"] == "failed"
    assert (await _current(service, "catalog"))["id"] == good["catalog_batch_id"]


@pytest.mark.asyncio
async def test_validation_errors_name_the_field_without_feed_values(service):
    broken = feed_row(1, posted={"matched": True, "records": [], "post_count": "secret-looking-value", "sched_count": 0, "last_post_on": None, "accounts": []})
    outcome = await make_sync(service, feed_transport([broken])).run("cron")
    assert outcome["status"] == "failed"
    assert "post_count" in outcome["error"] and "secret-looking-value" not in outcome["error"]


def _handler_transport(handler):
    def guarded(request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return handler(request)

    return httpx.MockTransport(guarded)


def _page(**extra):
    body = {"ok": True, "version": "pick-feed-v1", "capturedAt": "2026-09-23T03:00:00.000Z", "total": 1, "rows": [feed_row(1)], "nextCursor": None}
    return {**body, **extra}


def _refuse_connection(request):
    raise httpx.ConnectError("down", request=request)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "expected"),
    [
        (lambda r: httpx.Response(200, text="<html>maintenance</html>"), "不是 JSON"),
        (lambda r: httpx.Response(200, json=_page(version="pick-feed-v2")), "版本"),
        (lambda r: httpx.Response(200, json=_page(total=None)), "总数"),
        (lambda r: httpx.Response(200, json=_page(nextCursor="same")), "页数"),
        (_refuse_connection, "ConnectError"),
    ],
)
async def test_broken_feeds_fail_readably_and_keep_the_previous_batch(service, handler, expected):
    good = await make_sync(service, feed_transport([feed_row(1)])).run("cron")
    outcome = await make_sync(service, _handler_transport(handler)).run("cron")
    assert outcome["status"] == "failed" and expected in outcome["error"]
    assert TOKEN not in outcome["error"]
    assert (await _current(service, "catalog"))["id"] == good["catalog_batch_id"]


@pytest.mark.asyncio
async def test_a_stalled_feed_hits_the_deadline_and_a_cancelled_run_is_closed(service):
    import asyncio

    from ggwork_pick.repository import PickRepository

    gate = asyncio.Event()

    async def stall(request):
        await gate.wait()
        return httpx.Response(200, json=_page())

    slow = await make_sync(service, _handler_transport(stall), deadline_seconds=0.05).run("cron")
    assert slow["status"] == "failed" and "超时" in slow["error"]
    task = asyncio.create_task(make_sync(service, _handler_transport(stall)).run("manual"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    last = (await PickRepository.shared(service.session_factory).sync_runs())[0]
    assert last["status"] == "failed" and "中止" in last["error"] and last["finished_at"]
    assert not service.sync_lock.locked()


@pytest.mark.asyncio
async def test_restart_closes_runs_left_running_by_a_dead_process(service):
    from ggwork_pick.repository import PickRepository

    shared = PickRepository.shared(service.session_factory)
    crashed = await shared.start_sync_run("realshort", "cron")
    await service.initialize(service.session_factory)
    run = next(r for r in await shared.sync_runs() if r["id"] == crashed["id"])
    assert run["status"] == "failed" and run["error"] == "interrupted"


@pytest.mark.asyncio
async def test_lone_surrogates_from_a_cut_emoji_do_not_fail_the_batch(service):
    cut = json.dumps(_page(rows=[feed_row(1, title="Drama \ud83d")])).encode()
    outcome = await make_sync(service, _handler_transport(lambda r: httpx.Response(200, content=cut))).run("cron")
    assert outcome["status"] == "success"
