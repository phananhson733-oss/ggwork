"""The per-process catalog cache (plan 6.7): a batch's rows are read from the database once.

The owner check (_require_batch) still runs on every call and comes before the cache; only the
row read (ggwp_drama_versions, about 5.4 MB of JSON for the shared batch) is skipped on a hit.
Statements are counted with SQLAlchemy's before_cursor_execute event.
"""

import copy
import json
from contextlib import contextmanager
from datetime import timedelta

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker


def _rows(tag: str, count: int = 3) -> bytes:
    rows = [
        {
            "source": "synthetic",
            "source_id": f"{tag}-{i}",
            "language": "en",
            "title": f"Drama {tag} {i}",
            "theater": "Example",
            "tags": ["t"],
            "availability": "active",
            "channel_rules": {"youtube": "allowed" if i % 2 else "unknown"},
            "signals": [{"kind": "kd", "label": "日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i}],
            "posted": {"matched": True, "records": [f"p{i}"], "post_count": i % 2, "sched_count": 1, "accounts": [f"acct-{i}"]},
        }
        for i in range(1, count + 1)
    ]
    return json.dumps(rows).encode()


@contextmanager
def statements(engine):
    seen: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        yield seen
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)


def row_reads(seen: list[str]) -> int:
    return sum("ggwp_drama_versions" in statement for statement in seen)


@pytest_asyncio.fixture
async def workspace(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    alice = PickRepository(service.session_factory, "alice")
    batch = await Importer(alice, service.data_dir).catalog(_rows("a"), "json")
    yield engine, service, alice, batch["id"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_a_second_read_of_a_batch_skips_the_row_query(workspace):
    from ggwork_pick.repository import CATALOG_CACHE

    engine, _, alice, batch_id = workspace
    with statements(engine) as seen:
        first = await alice.catalog_rows(batch_id)
    assert row_reads(seen) == 1 and batch_id in CATALOG_CACHE
    with statements(engine) as seen:
        second = await alice.catalog_rows(batch_id)
    # Only the owner check reached the database.
    assert row_reads(seen) == 0 and len(seen) == 1 and "ggwp_import_batches" in seen[0]
    assert second == first and second is not first


@pytest.mark.asyncio
async def test_repeated_queries_and_counts_read_the_rows_once(workspace):
    from ggwork_pick.selection import SelectionService

    engine, _, alice, batch_id = workspace
    service = SelectionService(alice)
    with statements(engine) as seen:
        first = await service.query({"limit": 2}, thread_id="t", run_id="r", call_id="c1")
        second = await service.query({"limit": 2}, thread_id="t", run_id="r", call_id="c2")
        counted = await service.count({})
    assert row_reads(seen) == 1
    assert [i["identity"] for i in first["items"]] == [i["identity"] for i in second["items"]]
    assert counted["total"] == 3 and counted["catalog_batch_id"] == batch_id


@pytest.mark.asyncio
async def test_the_owner_check_comes_before_the_cache(workspace):
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository

    engine, service, alice, batch_id = workspace
    await alice.catalog_rows(batch_id)
    assert batch_id in CATALOG_CACHE
    with statements(engine) as seen:
        with pytest.raises(LookupError):
            await PickRepository(service.session_factory, "bob").catalog_rows(batch_id)
        with pytest.raises(LookupError):
            await alice.knowledge_documents(batch_id)
    assert row_reads(seen) == 0
    # A batch retired behind this process's back (another process pruned it) is refused, cache or not.
    async with service.session_factory() as session, session.begin():
        await session.execute(text("update ggwp_import_batches set status = 'pruned' where id = :id"), {"id": batch_id})
    with pytest.raises(LookupError):
        await alice.catalog_rows(batch_id)


@pytest.mark.asyncio
async def test_shared_batches_are_cached_once_for_every_reader(workspace):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository

    engine, service, alice, _ = workspace
    shared = await Importer(PickRepository.shared(service.session_factory), service.data_dir).catalog(_rows("s"), "json")
    bob = PickRepository(service.session_factory, "bob")
    with statements(engine) as seen:
        rows = await alice.catalog_rows(shared["id"])
        assert await bob.catalog_rows(shared["id"]) == rows
    assert row_reads(seen) == 1


@pytest.mark.asyncio
async def test_prune_evicts_the_pruned_batches(workspace):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import CATALOG_CACHE, PickRepository

    engine, service, alice, _ = workspace
    shared_repo = PickRepository.shared(service.session_factory)
    importer = Importer(shared_repo, service.data_dir)
    oldest, older, newest = [(await importer.catalog(_rows(tag), "json"))["id"] for tag in ("s1", "s2", "s3")]
    await alice.catalog_rows(oldest)
    await alice.catalog_rows(newest)
    assert oldest in CATALOG_CACHE and newest in CATALOG_CACHE
    # Keeps the newest shared batch; the two older ones lose their rows.
    await shared_repo.prune_shared("catalog", keep=1, referenced_within=timedelta(0))
    assert oldest not in CATALOG_CACHE
    with pytest.raises(LookupError):
        await alice.catalog_rows(oldest)
    with pytest.raises(LookupError):
        await alice.catalog_rows(older)
    with statements(engine) as seen:
        await alice.catalog_rows(newest)
    assert row_reads(seen) == 0


@pytest.mark.asyncio
async def test_at_most_two_batches_are_kept_least_recently_used_first(workspace):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import CATALOG_CACHE

    engine, service, alice, first = workspace
    importer = Importer(alice, service.data_dir)
    second = (await importer.catalog(_rows("b"), "json"))["id"]
    third = (await importer.catalog(_rows("c"), "json"))["id"]
    for batch_id in (first, second, first, third):
        await alice.catalog_rows(batch_id)
    assert (first in CATALOG_CACHE, second in CATALOG_CACHE, third in CATALOG_CACHE) == (True, False, True)
    with statements(engine) as seen:
        await alice.catalog_rows(first)
        await alice.catalog_rows(third)
    assert row_reads(seen) == 0
    with statements(engine) as seen:
        await alice.catalog_rows(second)
    assert row_reads(seen) == 1


@pytest.mark.asyncio
async def test_filtering_and_building_items_leave_the_cached_rows_as_they_were(workspace):
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.selection import SelectionService, candidate_item, matching_rows

    _, _, alice, batch_id = workspace
    rows = await alice.catalog_rows(batch_id)
    before = copy.deepcopy(rows)
    for filters in (
        {},
        {"signal_kind": "kd", "sort": "rank"},
        {"exclude_posted": True},
        {"posted_account": "acct-1"},
        {"channel": "youtube", "confirmed_eligible_only": False},
        {"query": "Drama", "tags": ["t"], "theater": "example", "language": "EN"},
    ):
        conditions = PickConditions.model_validate(filters)
        matches = matching_rows(rows, conditions, {rows[0]["identity"]})
        items = [candidate_item(row, conditions, len(matches)) for row in matches]
        assert items
        # Whatever a caller then does with an item stays out of the shared rows.
        for item in items:
            item["evidence"][0]["label"] = "changed"
            item["warnings"].append("changed")
            item["posted"]["accounts"].append("changed")
            item["posted"]["post_count"] = 99
    assert rows == before
    service = SelectionService(alice)
    result = await service.query({"signal_kind": "kd", "sort": "rank"}, thread_id="t", run_id="r", call_id="c")
    result["items"][0]["posted"]["records"].append("changed")
    await service.count({"exclude_posted": True})
    rows.clear()
    assert await alice.catalog_rows(batch_id) == before
