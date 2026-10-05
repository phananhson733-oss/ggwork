"""Catalog-dependent refusal provenance is observed only after authorized batch access."""

import json

import pytest
from test_result_notes import _import, _runtime, drama, signal


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["query_candidates_tool", "count_candidates_tool"])
@pytest.mark.parametrize(
    "filters,status",
    [
        ({"signal_kind": "kw", "sort": "rank"}, "rejected"),
        ({"language": "zz"}, "rejected"),
        ({"theater": "US"}, "rejected"),
        ({"posted_account": "unknown"}, "rejected"),
        ({"exclude_posted": True}, "posted_unavailable"),
    ],
)
async def test_catalog_refusals_carry_actual_current_pin(app_client, monkeypatch, tool_name, filters, status):
    from ggwork_pick import tools
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.pin import Pin
    from ggwork_pick.repository import PickRepository

    _, service = app_client
    row = drama(1, signals=[signal("kw", "2026-09-30", rank=None)])
    if status != "posted_unavailable":
        row["posted"] = {"matched": True, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": ["known"]}
    batch = await _import(service, [row])
    runtime = await _runtime(service, "refused")
    task = task_from_runtime(runtime)
    repo = await task.repository(runtime)
    frozen = {"source_as_of": "2026-09-30T00:00:00+00:00", "published_at": "2026-09-30T00:00:00+00:00", "freshness": {}, "scope": "synthetic", "shared": False}
    task.repin(Pin(batch["id"], data_as_of=frozen))

    async def no_lookup(*args):
        pytest.fail("A populated Pin must not require another metadata query")

    monkeypatch.setattr(PickRepository, "data_as_of", no_lookup)
    output = json.loads(await getattr(tools, tool_name).coroutine(filters=filters, runtime=runtime))
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.selection import matching_rows

    with pytest.raises(ValueError) as original:
        matching_rows(await repo.catalog_rows(batch["id"]), PickConditions.model_validate(filters), set())
    suffix = "。可以改为排除个人已选，或等数据同步带上发布记录后再查。" if status == "posted_unavailable" else ""
    assert output["status"] == status and output["notice"] == str(original.value) + suffix
    assert output["catalog_batch_id"] == batch["id"]
    assert output["data_as_of"] == frozen
    assert set(output) == {"status", "notice", "catalog_batch_id", "data_as_of"}
    assert await repo.results("thread1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["query_candidates_tool", "count_candidates_tool"])
async def test_derived_refusal_keeps_old_parent_scope(app_client, tool_name):
    from test_result_notes import _freeze_result

    from ggwork_pick import tools
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.repository import PickRepository

    _, service = app_client
    old = await _import(service, [drama(1, signals=[signal("kw", "2026-09-30", rank=None)])])
    original = await _runtime(service, "original")
    parent = json.loads(await tools.query_candidates_tool.coroutine(filters={}, runtime=original))
    frozen = await _freeze_result(service, parent["id"])
    new = await _import(service, [drama(2, signals=[signal("kw", "2026-10-05", rank=1)])])
    runtime = await _runtime(service, "derived")
    runtime.context["pick_reference"] = {"result_id": parent["id"]}
    await task_from_runtime(runtime).repository(runtime)
    assert task_from_runtime(runtime).catalog_id == new["id"]
    output = json.loads(await getattr(tools, tool_name).coroutine(filters={"exclude_previous": True, "signal_kind": "kw", "sort": "rank"}, runtime=runtime))
    assert output["status"] == "rejected" and "没有名次" in output["notice"]
    assert output["catalog_batch_id"] == old["id"] and output["data_as_of"] == frozen
    assert len(await PickRepository(service.session_factory, "alice").results("thread1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["query_candidates_tool", "count_candidates_tool"])
async def test_pre_access_refusals_and_zero_success_do_not_gain_scope(app_client, tool_name):
    from ggwork_pick import tools
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.pin import Pin

    _, service = app_client
    empty = json.loads(await getattr(tools, tool_name).coroutine(filters={}, runtime=await _runtime(service, "empty")))
    assert empty["status"] == "catalog_unavailable" and "data_as_of" not in empty
    await _import(service, [drama(1)])
    runtime = await _runtime(service, "no-parent")
    refusal = json.loads(await getattr(tools, tool_name).coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert refusal["status"] == "rejected" and "data_as_of" not in refusal and "catalog_batch_id" not in refusal
    with pytest.raises(ValueError):
        await getattr(tools, tool_name).coroutine(filters={"limit": "not-a-number"}, runtime=runtime)
    task_from_runtime(runtime).repin(Pin("unreadable-batch", data_as_of={"private": "never expose"}))
    denied = json.loads(await getattr(tools, tool_name).coroutine(filters={}, runtime=runtime))
    assert denied["status"] == "rejected" and "private" not in str(denied) and "data_as_of" not in denied
    zero = json.loads(await getattr(tools, tool_name).coroutine(filters={"query": "no-such-title"}, runtime=await _runtime(service, "zero")))
    assert "status" not in zero and zero.get("matched_total", zero.get("total")) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["query_candidates_tool", "count_candidates_tool"])
async def test_foreign_catalog_and_parent_never_expose_provenance(app_client, tool_name):
    from ggwork_pick import tools
    from ggwork_pick.context import task_from_runtime
    from ggwork_pick.imports import Importer
    from ggwork_pick.pin import Pin
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    _, service = app_client
    await _import(service, [drama(1)])
    foreign = PickRepository(service.session_factory, "bob")
    batch = await Importer(foreign, service.data_dir).catalog(json.dumps([drama(2)]).encode(), "json")
    result = await SelectionService(foreign).query({}, thread_id="thread1", run_id="bob", call_id="bob")
    runtime = await _runtime(service, "foreign-pin")
    task = task_from_runtime(runtime)
    await task.repository(runtime)
    task.repin(Pin(batch["id"], data_as_of={"scope": "private-bob-scope"}))
    output = json.loads(await getattr(tools, tool_name).coroutine(filters={"language": "zz"}, runtime=runtime))
    assert output["status"] == "rejected"
    assert set(output) == {"status", "notice"} and "private-bob-scope" not in str(output)
    assert batch["id"] not in str(output)
    task.reference_id = result["id"]
    parent_denied = json.loads(await getattr(tools, tool_name).coroutine(filters={"exclude_previous": True}, runtime=runtime))
    assert set(parent_denied) == {"status", "notice"} and parent_denied["status"] == "rejected"
    anonymous = await _runtime(service, "anonymous")
    anonymous.context.pop("user_id")
    with pytest.raises(ValueError):
        await getattr(tools, tool_name).coroutine(filters={}, runtime=anonymous)


@pytest.mark.asyncio
async def test_legacy_pin_refusal_uses_own_batch_metadata(app_client):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import CatalogRefusal, SelectionService

    _, service = app_client
    batch = await _import(service, [drama(1)])
    repo = PickRepository(service.session_factory, "alice")
    expected = await repo.data_as_of(batch["id"])
    with pytest.raises(CatalogRefusal) as refused:
        await SelectionService(repo).count({"language": "zz"}, pinned_versions=(batch["id"], None))
    assert refused.value.catalog_batch_id == batch["id"] and refused.value.data_as_of == expected
