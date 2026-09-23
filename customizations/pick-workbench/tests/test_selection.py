import asyncio
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def workspace(tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    repo = PickRepository(factory, "alice")
    rows = [
        {
            "source": "synthetic",
            "source_id": str(i),
            "language": language,
            "title": f"合成样例{i}",
            "theater": "Example",
            "availability": availability,
            "channel_rules": rules,
            "signals": [{"kind": "rank", "source_ref": f"fixture:{i}", "observed_at": observed, "value": i}],
        }
        for i, language, availability, rules, observed in [
            (1, "en", "active", {"youtube": "allowed"}, "2026-09-20"),
            (2, "en", "active", {"youtube": "allowed"}, "2026-09-19"),
            (3, "en", "unknown", {}, None),
            (4, "en", "delisted", {"youtube": "allowed"}, "2026-09-21"),
            (5, "ko", "active", {"youtube": "allowed"}, "2026-09-21"),
        ]
    ]
    importer = Importer(repo, service.data_dir)
    batch = await importer.catalog(json.dumps(rows).encode(), "json")
    yield repo, importer, batch, rows
    await engine.dispose()


@pytest.mark.asyncio
async def test_filters_snapshot_order_shortfall_and_call_retry(workspace):
    from ggwork_pick.selection import SelectionService

    repo, _, batch, _ = workspace
    service = SelectionService(repo)
    result = await service.query({"language": "en", "channel": "youtube", "limit": 5}, thread_id="t1", run_id="r1", call_id="c1")
    assert [r["title"] for r in result["items"]] == ["合成样例1", "合成样例2"]
    assert result["catalog_batch_id"] == batch["id"]
    assert result["items"][0]["evidence"][0]["source_ref"] == "fixture:1"
    assert await service.query({"language": "en", "channel": "youtube", "limit": 5}, thread_id="t1", run_id="r1", call_id="c1") == result
    with pytest.raises(ValueError, match="重复"):
        await service.query({"language": "ko"}, thread_id="t1", run_id="r1", call_id="c1")


@pytest.mark.asyncio
async def test_only_a_new_batch_request_inherits_the_parent_conditions_and_data(workspace):
    from ggwork_pick.selection import SelectionService

    repo, importer, batch, rows = workspace
    service = SelectionService(repo)
    old = await service.query({"language": "en", "limit": 2}, thread_id="t1", run_id="r1", call_id="c1")
    rows[0]["title"] = "更新后的名字"
    new_batch = await importer.catalog(json.dumps(rows).encode(), "json")
    # A new question in the same thread: only its own conditions, on the data pinned for this run.
    fresh = await service.query({"limit": 1}, thread_id="t1", run_id="r2", call_id="c2", parent_result_id=old["id"], pinned_versions=(new_batch["id"], None))
    assert fresh["conditions"]["language"] is None
    assert fresh["catalog_batch_id"] == new_batch["id"]
    # 换一批: the parent's conditions and data version, minus the parent's items.
    more = await service.query({"exclude_previous": True, "limit": 1}, thread_id="t1", run_id="r3", call_id="c3", parent_result_id=old["id"])
    assert more["conditions"]["language"] == "en" and more["catalog_batch_id"] == batch["id"]
    assert [i["title"] for i in more["items"]] == ["合成样例3"]
    # exclude_previous does not stick to the next question.
    after = await service.query({}, thread_id="t1", run_id="r4", call_id="c4", parent_result_id=more["id"], pinned_versions=(new_batch["id"], None))
    assert after["conditions"]["exclude_previous"] is False and after["conditions"]["language"] is None
    newer = await service.query({"exclude_previous": True}, thread_id="t1", run_id="r5", call_id="c5", parent_result_id=old["id"], use_latest=True)
    assert newer["catalog_batch_id"] == new_batch["id"]
    with pytest.raises(ValueError, match="换一批"):
        await service.query({"exclude_previous": True}, thread_id="t1", run_id="r6", call_id="c6")
    with pytest.raises(LookupError):
        await service.query({}, thread_id="other-thread", run_id="r7", call_id="c7", parent_result_id=old["id"])


@pytest.mark.asyncio
async def test_save_receipt_is_atomic_idempotent_and_does_not_resurrect_removed(workspace):
    from ggwork_pick.selection import SelectionService

    repo, _, _, _ = workspace
    result = await SelectionService(repo).query({"language": "en"}, thread_id="t1", run_id="r1", call_id="c1")
    ids = [result["items"][0]["item_id"], result["items"][1]["item_id"]]
    a, b = await asyncio.gather(repo.save_selection("save-1", result["id"], ids, "准备剪辑"), repo.save_selection("save-1", result["id"], ids, "准备剪辑"))
    assert a == b
    assert len(await repo.selections()) == 2
    with pytest.raises(ValueError, match="重复"):
        await repo.save_selection("save-1", result["id"], [ids[0]], "changed")
    selected = (await repo.selections())[0]
    await repo.update_selection(selected["id"], "remove-1", selected["version"], state="removed")
    assert await repo.save_selection("save-1", result["id"], ids, "准备剪辑") == a
    assert len(await repo.selections()) == 1
    assert await repo.command_receipt("save-1") == a
    with pytest.raises(ValueError, match="版本"):
        await repo.update_selection(selected["id"], "edit-stale", selected["version"], note="旧版本备注")


@pytest.mark.asyncio
async def test_foreign_or_invented_items_cannot_be_read_or_saved(workspace):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    repo, _, _, _ = workspace
    result = await SelectionService(repo).query({}, thread_id="t1", run_id="r1", call_id="c1")
    foreign = PickRepository(repo.session_factory, "bob")
    with pytest.raises(LookupError):
        await foreign.result(result["id"])
    with pytest.raises(LookupError):
        await foreign.save_selection("x", result["id"], [result["items"][0]["item_id"]])
    with pytest.raises(ValueError, match="候选"):
        await repo.save_selection("x", result["id"], ["invented"])
    assert await repo.selections() == []
    assert await repo.command_receipt("x") is None


@pytest.mark.asyncio
async def test_exclude_selected_and_unknown_publication_are_distinct(workspace):
    from ggwork_pick.selection import SelectionService

    repo, _, _, _ = workspace
    service = SelectionService(repo)
    first = await service.query({"language": "en"}, thread_id="t1", run_id="r1", call_id="c1")
    await repo.save_selection("s1", first["id"], [first["items"][0]["item_id"]])
    second = await service.query({"language": "en"}, thread_id="t1", run_id="r2", call_id="c2")
    assert first["items"][0]["identity"] not in [r["identity"] for r in second["items"]]
    unknown = next(r for r in second["items"] if r["title"] == "合成样例3")
    assert unknown["availability"] == "unknown"
    assert unknown["evidence"][0]["observed_at"] is None
    with pytest.raises(ValueError):
        await service.query({"unpublished": True}, thread_id="t1", run_id="r3", call_id="c3")


@pytest.mark.asyncio
async def test_query_replay_cannot_change_refresh_intent(workspace):
    from ggwork_pick.selection import SelectionService

    repo, _, _, _ = workspace
    service = SelectionService(repo)
    await service.query({}, thread_id="t1", run_id="r1", call_id="c1")
    with pytest.raises(ValueError, match="重复"):
        await service.query({}, thread_id="t1", run_id="r1", call_id="c1", use_latest=True)
