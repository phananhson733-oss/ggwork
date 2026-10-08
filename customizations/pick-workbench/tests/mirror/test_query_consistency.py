"""Canonical identity/eligibility agrees with public mirror filtering before pagination."""

import base64
import json
from datetime import timedelta

import board_fixture as bf
import gate_world as gw
import pytest
import pytest_asyncio
import test_board_fixture

from ggwork_pick.completion_contracts import CommonQuery
from ggwork_pick.mirror.connection import dsn_from_url, open_dedicated
from ggwork_pick.query_reader import QueryReader
from ggwork_pick.query_service import CommonQueryService
from ggwork_pick.repository import PickRepository


@pytest.fixture
def common_board(pg_cluster, tmp_path_factory):
    # Each case publishes its own immutable sequence; no timestamp ties with another test.
    yield from test_board_fixture.board.__wrapped__(pg_cluster, tmp_path_factory)


@pytest_asyncio.fixture
async def canonical_world(common_board, pg_cluster):
    board = common_board
    url = pg_cluster.async_url(board["info"]["database"])
    engine, shared, importer = await bf._open_gateway(url, board["data_dir"])
    conn = await open_dedicated(dsn_from_url(url))
    reader = QueryReader(board["reader"], ssl=False)
    as_of = bf.AS_OF["v2"] + timedelta(days=1)
    world = bf.board_world("Canonical A", "ok", as_of)
    world = gw.with_table(world, "catalog_rows", [{**r, "platform": "dramabox"} if r["row_key"] == "c-4" else r for r in world.tables["catalog_rows"]])
    canonical = []
    for row in world.v1_rows:
        key = base64.urlsafe_b64decode(row["source_id"] + "=" * (-len(row["source_id"]) % 4)).decode()
        canonical.append({**row, "source": "synthetic", "source_id": key, "availability": "active", "channel_rules": {"youtube": "allowed"}})
    canonical.append(
        {**gw.v1_row("c-4", ("kd",), ()), "source": "synthetic", "source_id": "c-4", "availability": "active", "channel_rules": {"youtube": "allowed"}}
    )
    world = gw.with_v1(world, canonical)
    try:
        with bf._reader_role_env(board["info"]["role"]):
            await bf._publish(conn, shared, importer, world, as_of)
            repo = PickRepository(importer.repository.session_factory, "alice")
            yield CommonQueryService(repo, reader), repo, world, conn, shared, importer, as_of
    finally:
        await reader.close()
        await conn.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_canonical_qualification_source_and_pages_agree(canonical_world):
    service, repo, *_ = canonical_world
    req = CommonQuery(domain="catalog", scope="full_catalog", source="synthetic", channel="youtube", order="title", limit=2)
    first = await service.query(req)
    assert first.counts.matched == 8
    assert first.counts.returned == 2
    assert first.next_offset == 2
    assert sum(first.facets.platforms.values()) == 8
    assert sum(first.facets.languages.values()) == 8
    keys = list(first.board.row_keys)
    while first.next_offset is not None:
        first = await service.query(req.model_copy(update={"pin": first.pin, "offset": first.next_offset}))
        keys.extend(first.board.row_keys)
    assert len(keys) == len(set(keys)) == 8
    assert "c-4" not in keys  # raw explicit delisting still wins
    assert all(row.drama.source == "synthetic" for row in first.rows)


@pytest.mark.asyncio
async def test_public_tools_selection_exclusion_and_timeout_keep_semantics(canonical_world):
    import asyncio
    from types import SimpleNamespace

    import httpx
    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
    from fastapi import FastAPI

    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.routes import build_router
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import count_candidates_tool, query_candidates_tool, query_data_tool

    query, repo, _, _, _, importer, _ = canonical_world
    service = PickService(importer.data_dir)
    await service.initialize(repo.session_factory)
    service.query_reader = query.reader
    app = FastAPI()
    setattr(
        app.state,
        EXTENSION_PRINCIPAL_RESOLVER_KEY,
        lambda request: ExtensionPrincipal(request.headers["test-owner"]) if "test-owner" in request.headers else None,
    )
    app.include_router(build_router(service))
    store = ExtensionData("task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task", "run", "thread", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="first")
    first = json.loads(await query_candidates_tool.coroutine(filters={"channel": "youtube", "limit": 1}, runtime=runtime))
    assert first["matched_total"] == 8
    await repo.save_selection("choose", first["id"], [first["items"][0]["item_id"]])
    runtime.tool_call_id = "next"
    second = json.loads(await query_candidates_tool.coroutine(filters={"channel": "youtube"}, runtime=runtime))
    assert second["matched_total"] == 7
    assert first["items"][0]["identity"] not in {row["identity"] for row in second["items"]}
    body = {"domain": "catalog", "scope": "full_catalog", "source": "synthetic", "channel": "youtube", "exclude_selected": True, "limit": 2}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/pick/query", json=body)).status_code == 401
        result = await client.post("/api/pick/query", json=body, headers={"test-owner": "alice"})
        assert result.status_code == 200, result.text
        assert result.json()["counts"]["matched"] == 7
        assert sum(result.json()["facets"]["platforms"].values()) == 7
        other = await client.post("/api/pick/query", json=body, headers={"test-owner": "bob"})
        assert other.json()["counts"]["matched"] == 8
    from ggwork_pick.selection import SelectionService

    another = await SelectionService(repo, query_service=query).query(
        {"exclude_previous": True, "exclude_selected": False, "limit": 20},
        thread_id="thread",
        run_id="next-run",
        call_id="previous-exclusion",
        parent_result_id=first["id"],
    )
    assert another["matched_total"] == 7
    assert first["items"][0]["identity"] not in {row["identity"] for row in another["items"]}

    from ggwork_pick.context import query_call_loop_deadline

    token = query_call_loop_deadline.set(asyncio.get_running_loop().time() - 1)
    try:
        for tool, args in [
            (query_data_tool, {"query": body}),
            (query_candidates_tool, {"filters": {"channel": "youtube"}}),
            (count_candidates_tool, {"filters": {"channel": "youtube"}}),
        ]:
            runtime.tool_call_id = "expired-" + tool.name
            failed = json.loads(await tool.coroutine(runtime=runtime, **args))
            assert failed["status"] == "rejected"
            assert failed["code"] == "query_timeout"
            assert failed["retryable"] is True
            assert "id" not in failed and "items" not in failed
    finally:
        query_call_loop_deadline.reset(token)


@pytest.mark.asyncio
async def test_raw_denial_delisting_unknown_and_historical_pin(canonical_world):
    service, repo, world, conn, shared, importer, as_of = canonical_world
    original = await service.query(CommonQuery(domain="catalog", scope="full_catalog", channel="youtube", limit=200))
    assert original.counts.matched == 8
    canonical = [{**row, "availability": "unknown"} if row["source_id"] == "c-2" else row for row in world.v1_rows if row["source_id"] != "c-5"]
    changed = gw.with_v1(world, canonical)
    changed = gw.with_manifest(changed, ("meta", "rules", "platformRules", "shortmax", "yt"), "no")
    # Independent accepted pairing; no existing/baseline source fixture is modified.
    await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    latest = await repo.current_pin()
    raw = await service.query(CommonQuery(domain="catalog", scope="full_catalog", with_off=True, limit=200))
    facts = {r.drama.source_id: r.drama for r in raw.rows}
    assert facts["c-1"].channel_rules["youtube"] == "denied"
    assert facts["c-4"].availability == "delisted"
    assert facts["c-2"].availability == "unknown"
    raw_only = next(r.drama for r in raw.rows if r.drama.source_id == gw.b64url("c-5"))
    assert raw_only.availability == "unknown"
    assert raw_only.channel_rules.get("youtube", "unknown") == "unknown"
    cached = await repo.catalog_rows(latest.catalog_id)
    assert next(r for r in cached if r["source_id"] == "c-1")["channel_rules"]["youtube"] == "allowed"
    assert next(r for r in cached if r["source_id"] == "c-4")["availability"] == "active"
    eligible = await service.query(CommonQuery(domain="catalog", scope="full_catalog", channel="youtube", limit=200))
    assert eligible.counts.matched == 5
    including_off = await service.query(eligible.request.model_copy(update={"with_off": True}))
    assert including_off.counts.matched == 5
    assert not {"c-1", "c-2", "c-4", "c-5"} & set(eligible.board.row_keys)
    historical = await service.query(original.request.model_copy(update={"pin": original.pin}))
    assert historical.counts.matched == 8
    assert historical.pin != eligible.pin


@pytest.mark.asyncio
async def test_ambiguous_cross_source_identity_fails_closed(canonical_world):
    from ggwork_pick.query_reader import QueryFailure

    service, _, world, conn, shared, importer, as_of = canonical_world
    duplicate = {**world.v1_rows[0], "source": "other-source"}
    changed = gw.with_v1(world, [*world.v1_rows, duplicate])
    await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    with pytest.raises(QueryFailure) as caught:
        await service.query(CommonQuery(domain="catalog", scope="full_catalog", source="synthetic"))
    assert caught.value.code == "source_unavailable"
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_saved_realshort_identity_stays_excluded_when_canonical_row_disappears(canonical_world):
    from ggwork_pick.selection import SelectionService

    service, repo, world, conn, shared, importer, as_of = canonical_world
    canonical = [
        {**row, "source": "realshort-pick", "source_id": gw.b64url("c-1"), "language": bf.LANGUAGE} if row["source_id"] == "c-1" else row
        for row in world.v1_rows
    ]
    first_world = gw.with_v1(world, canonical)
    await bf._publish(conn, shared, importer, first_world, as_of + timedelta(days=1))
    chosen = await SelectionService(repo, query_service=service).query(
        {"query": "c-1", "limit": 1}, thread_id="thread", run_id="select-old", call_id="select-old"
    )
    assert chosen["matched_total"] == 1
    await repo.save_selection("save-old", chosen["id"], [chosen["items"][0]["item_id"]])
    next_world = gw.with_v1(first_world, [row for row in canonical if row["source"] != "realshort-pick"])
    next_world = gw.with_table(next_world, "catalog_rows", [{**r, "has_signal": False} if r["row_key"] == "c-1" else r for r in world.tables["catalog_rows"]])
    await bf._publish(conn, shared, importer, next_world, as_of + timedelta(days=2))
    req = CommonQuery(domain="catalog", scope="full_catalog", source_id="c-1", exclude_selected=True)
    visible = await service.query(req.model_copy(update={"exclude_selected": False}))
    assert visible.rows[0].identity == chosen["items"][0]["identity"]
    excluded = await service.query(req)
    assert excluded.counts.matched == excluded.counts.returned == 0
    assert excluded.board.row_keys == []
    assert sum(excluded.facets.platforms.values()) == 0
    bob = await CommonQueryService(PickRepository(repo.session_factory, "bob"), service.reader).query(req)
    assert bob.counts.matched == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown_language", ["", " \t "])
async def test_unknown_raw_language_never_becomes_confirmed_candidate(canonical_world, unknown_language):
    from ggwork_pick.query_model_projection import model_projection
    from ggwork_pick.selection import SelectionService

    service, repo, world, conn, shared, importer, as_of = canonical_world
    changed = gw.with_table(world, "catalog_rows", [{**r, "lang": unknown_language} if r["row_key"] == "c-2" else r for r in world.tables["catalog_rows"]])
    await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    request = CommonQuery(domain="catalog", scope="full_catalog", source_id="c-2", channel="youtube")
    confirmed = await service.query(request)
    assert confirmed.counts.matched == confirmed.counts.returned == 0
    candidate = await SelectionService(repo, query_service=service).query(
        {"query": "c-2", "channel": "youtube", "exclude_selected": False}, thread_id="t", run_id="r", call_id="unknown"
    )
    assert candidate["matched_total"] == 0
    unfiltered = await service.query(request.model_copy(update={"channel": None}))
    assert unfiltered.counts.matched == 1
    assert unfiltered.board.row_keys == ["c-2"]
    assert unfiltered.rows == []
    projection = model_projection(unfiltered.model_dump(mode="json"), call_id="unknown-language", requested_limit=20)
    assert projection["rows"][0]["kind"] == "catalog_record"
    assert projection["rows"][0]["identity"] is None
    assert projection["rows"][0]["eligibility"] == "unknown"


@pytest.mark.asyncio
async def test_paired_candidate_supported_absent_language_and_case_preserve_scope(canonical_world):
    from ggwork_pick.selection import SelectionService

    service, repo, *_ = canonical_world
    selection = SelectionService(repo, query_service=service)
    for language in ("ko", "KO"):
        result = await selection.query({"language": language, "exclude_selected": False}, thread_id="lang", run_id="lang", call_id=language)
        assert result["matched_total"] == 0 and result["items"] == []
    counts = []
    for language in ("en", "EN"):
        result = await selection.query({"language": language, "exclude_selected": False}, thread_id="lang", run_id="lang", call_id=language)
        counts.append(result["matched_total"])
    assert counts[0] == counts[1] > 0
    for language in ("USA", "english", "zz"):
        with pytest.raises(ValueError):
            await selection.query({"language": language}, thread_id="lang", run_id="lang", call_id=language)


@pytest.mark.asyncio
async def test_historical_detail_episode_sidecar_uses_exact_pin_without_changing_snapshot(canonical_world):
    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.answer_evidence import AnswerEvidence
    from ggwork_pick.query_model_projection import model_projection
    from ggwork_pick.selection import SelectionService

    service, repo, world, conn, shared, importer, as_of = canonical_world
    old = gw.with_table(world, "catalog_rows", [{**r, "episodes": 80} if r["row_key"] == "c-2" else r for r in world.tables["catalog_rows"]])
    await bf._publish(conn, shared, importer, old, as_of + timedelta(days=1))
    selection = SelectionService(repo, query_service=service)
    result = await selection.query({"limit": 20, "exclude_selected": False}, thread_id="historical", run_id="historical", call_id="query")
    item = next(i for i in result["items"] if json.loads(i["identity"])[1] == "c-2")
    stored = await repo.result(result["id"])
    later = gw.with_table(old, "catalog_rows", [{**r, "episodes": 999} if r["row_key"] == "c-2" else r for r in old.tables["catalog_rows"]])
    await bf._publish(conn, shared, importer, later, as_of + timedelta(days=2))
    detail = await selection.detail(result["id"], item["item_id"])
    supplement = detail["source_facts"]
    assert supplement["episodes"] == 80
    assert supplement["pin"]["mirror_version"] == stored["mirror_version"]
    assert supplement["source_ref"] == f"mirror:{stored['mirror_version']}:catalog_rows:c-2"
    assert "episodes" not in detail["item"]
    assert (await repo.result(result["id"]))["ordered_items_json"] == stored["ordered_items_json"]
    evidence = AnswerEvidence()
    evidence.capture("pick_get_drama_detail", "detail", detail)

    def checked(text):
        return build_checked_publication(text, evidence=evidence, thread_id="historical", run_id="check", message_id="check")

    assert checked(f"《{item['title']}》共80集 [{supplement['reference']}]").status == "confirmed"
    assert checked(f"《{item['title']}》共999集 [{supplement['reference']}]").status != "confirmed"
    summary = detail["historical_summary"]
    assert summary["matched_total"] == result["matched_total"]
    assert checked(f"该历史候选结果当时符合条件总数为{result['matched_total']}部 [result:wrong-result]").status != "confirmed"
    assert checked(f"《错误剧名》共80集 [{supplement['reference']}]").status != "confirmed"
    assert checked(f"该历史候选结果当时符合条件总数为{result['matched_total']}部 [{summary['reference']}]").status == "confirmed"
    assert checked(f"本次查询符合条件总数为{result['matched_total']}部 [{summary['reference']}]").status != "confirmed"
    response = await service.query(
        CommonQuery(
            domain="catalog", scope="full_catalog", source="synthetic", source_id="c-2", pin=supplement["pin"], with_off=True, confirmed_eligible_only=False
        )
    )
    projected = model_projection(response.model_dump(mode="json"), call_id="pinned", requested_limit=20)
    assert projected["rows"][0]["episodes"] == 80
    evidence.capture("pick_query_data", "pinned", response.model_dump(mode="json"))
    assert checked(f"《{item['title']}》共80集 [{projected['rows'][0]['reference']}]").status == "confirmed"
    with pytest.raises(LookupError):
        await SelectionService(PickRepository(repo.session_factory, "bob"), query_service=service).detail(result["id"], item["item_id"])
    with pytest.raises(LookupError):
        await selection.detail(result["id"], "wrong-item")
    from test_plural_references import envelope, runtime_for

    from ggwork_pick.context import PickTask, query_call_loop_deadline
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import get_drama_detail_tool

    host = PickService(importer.data_dir)
    await host.initialize(repo.session_factory)
    host.query_reader = service.reader
    runtime, store = await runtime_for(host, envelope([result]), thread="historical")
    tool_detail = json.loads(await get_drama_detail_tool.coroutine(result_id=result["id"], item_id=item["item_id"], runtime=runtime))
    assert tool_detail["source_facts"]["episodes"] == 80
    assert tool_detail["historical_summary"]["matched_total"] == result["matched_total"]
    assert len(json.dumps(tool_detail, ensure_ascii=False).encode()) < 48000
    proof = store.get(PickTask).answer_evidence
    assert (
        build_checked_publication(
            f"《{item['title']}》共80集 [{supplement['reference']}]", evidence=proof, thread_id="historical", run_id="r", message_id="m"
        ).status
        == "confirmed"
    )
    wrong_item = json.loads(await get_drama_detail_tool.coroutine(result_id=result["id"], item_id="wrong", runtime=runtime))
    assert wrong_item["status"] == "rejected" and "source_facts" not in wrong_item
    for owner, thread in (("bob", "historical"), ("alice", "other-thread")):
        wrong, _ = await runtime_for(host, envelope([result]), owner=owner, thread=thread)
        with pytest.raises((ValueError, LookupError)):
            await get_drama_detail_tool.coroutine(result_id=result["id"], item_id=item["item_id"], runtime=wrong)
    import asyncio

    token = query_call_loop_deadline.set(asyncio.get_running_loop().time() - 1)
    try:
        expired = json.loads(await get_drama_detail_tool.coroutine(result_id=result["id"], item_id=item["item_id"], runtime=runtime))
        assert expired["code"] == "query_timeout" and expired["retryable"] is True
        assert "source_facts" not in expired
    finally:
        query_call_loop_deadline.reset(token)
    await conn.execute("UPDATE pick_mirror.versions SET status='dropped' WHERE id=$1", stored["mirror_version"])
    unavailable = json.loads(await get_drama_detail_tool.coroutine(result_id=result["id"], item_id=item["item_id"], runtime=runtime))
    assert unavailable["code"] == "version_gone" and "source_facts" not in unavailable


@pytest.mark.asyncio
@pytest.mark.parametrize("episodes", [None, 0])
async def test_historical_episode_sidecar_retains_null_and_zero(canonical_world, episodes):
    from ggwork_pick.selection import SelectionService

    service, repo, world, conn, shared, importer, as_of = canonical_world
    changed = gw.with_table(world, "catalog_rows", [{**r, "episodes": episodes} if r["row_key"] == "c-2" else r for r in world.tables["catalog_rows"]])
    await bf._publish(conn, shared, importer, changed, as_of + timedelta(days=1))
    selection = SelectionService(repo, query_service=service)
    result = await selection.query({"limit": 20, "exclude_selected": False}, thread_id="episodes", run_id="r", call_id="c")
    item = next(i for i in result["items"] if json.loads(i["identity"])[1] == "c-2")
    detail = await selection.detail(result["id"], item["item_id"])
    assert detail["source_facts"]["episodes"] == episodes
