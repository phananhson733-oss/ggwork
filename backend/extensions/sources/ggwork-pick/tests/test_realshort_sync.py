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


def feed_transport(rows, *, page=2, total=None, seen=None, fail_on=None):
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
            "rules": "# RealShort 选剧规则与口径\n\n## KalosTV\nYouTube：禁" if not cursor else None,
            "rows": chunk,
            "nextCursor": str(start + page) if start + page < len(rows) else None,
        }
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


@pytest_asyncio.fixture
async def service(tmp_path):
    from ggwork_pick.service import PickService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
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
