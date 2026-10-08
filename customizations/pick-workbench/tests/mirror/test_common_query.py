"""Public shared-query behavior on the same synthetic database as the board SQL."""

import asyncio

import pytest
import test_board_fixture
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

common_board = test_board_fixture.board


@pytest.mark.asyncio
async def test_reader_is_bounded_readonly_and_cancel_releases_connection(common_board):
    board = common_board
    from ggwork_pick.query_reader import QueryReader

    reader = QueryReader(board["reader"], ssl=False)
    try:
        async with reader.connection(deadline=asyncio.get_running_loop().time() + 3) as conn:
            assert await conn.fetchval("SHOW transaction_read_only") == "on"
            assert await conn.fetchval("SHOW statement_timeout") == "3s" or int((await conn.fetchval("SHOW statement_timeout")).removesuffix("ms")) <= 3000
            assert await conn.fetchval("SELECT current_user") == board["info"]["role"]
            with pytest.raises(Exception):
                await conn.execute("CREATE TABLE should_not_exist (id int)")

        async def slow():
            async with reader.connection(deadline=asyncio.get_running_loop().time() + 3) as conn:
                await conn.execute("SELECT pg_sleep(20)")

        task = asyncio.create_task(slow())
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with reader.connection(deadline=asyncio.get_running_loop().time() + 1) as conn:
            assert await conn.fetchval("SELECT 1") == 1
        assert reader.pool.get_max_size() == 3
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_catalog_complete_rows_fixed_pin_unknown_and_search(common_board, pg_cluster):
    board = common_board
    from ggwork_pick.completion_contracts import CommonQuery, QueryResponse
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    engine = host_engine(pg_cluster.async_url(board["info"]["database"]))
    reader = QueryReader(board["reader"], ssl=False)
    service = CommonQueryService(PickRepository(async_sessionmaker(engine), "alice"), reader)
    try:
        result = await service.query(CommonQuery(domain="catalog", scope="full_catalog", with_off=True, limit=2))
        assert isinstance(result, QueryResponse)
        assert result.pin.mirror_version == board["info"]["versions"]["v2"]
        assert result.counts.total == result.counts.matched == 11
        assert len(result.board.row_keys) == 2 and result.next_offset == 2
        exact = await service.query(CommonQuery(domain="catalog", scope="full_catalog", query="c-1", pin=result.pin))
        assert exact.board.row_keys == ["c-1"]
        assert exact.board.catalog_rows[0].title == "c-1 的剧名（v2 改名）"
        assert exact.pin == result.pin
        unknown = await service.query(CommonQuery(domain="catalog", scope="full_catalog", language="", pin=result.pin))
        assert unknown.counts.matched == 0
        assert unknown.facets.languages["英语"] > 0  # own language dimension is excluded
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_posted_archive_states_and_rules_use_same_version(common_board, pg_cluster):
    board = common_board
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    engine = host_engine(pg_cluster.async_url(board["info"]["database"]))
    reader = QueryReader(board["reader"], ssl=False)
    service = CommonQueryService(PickRepository(async_sessionmaker(engine), "alice"), reader)
    try:
        posted = await service.query(CommonQuery(domain="posted", scope="full_catalog", posted_state="pub"))
        assert posted.counts.matched > 0
        assert all(p.post_count > 0 for p in posted.board.posted)
        assert posted.facets.posted_states["pub"] == posted.counts.matched
        assert posted.facets.posted_states["nomatch"] > 0
        rules = await service.query(CommonQuery(domain="rules", scope="full_catalog", pin=posted.pin))
        assert rules.board.rules == posted.board.rules
        assert rules.pin == posted.pin
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_common_historical_rank_and_rule_pin_never_substitute_latest(common_board, pg_cluster):
    from ggwork_pick.completion_contracts import CommonQuery, QueryPeriod, QueryPin
    from ggwork_pick.query_reader import QueryFailure, QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    board = common_board
    engine = host_engine(pg_cluster.async_url(board["info"]["database"]))
    reader = QueryReader(board["reader"], ssl=False)
    service = CommonQueryService(PickRepository(async_sessionmaker(engine), "alice"), reader)
    try:
        latest = await service.query(CommonQuery(domain="catalog", scope="full_catalog"))
        async with reader.connection(deadline=asyncio.get_running_loop().time() + 3) as conn:
            old = await conn.fetchrow(
                "SELECT agent_catalog_batch_id,agent_knowledge_batch_id FROM pick_mirror.versions WHERE id=$1", board["info"]["versions"]["v1"]
            )
        pin = QueryPin(
            catalog_batch_id=old["agent_catalog_batch_id"],
            knowledge_batch_id=old["agent_knowledge_batch_id"],
            mirror_version=board["info"]["versions"]["v1"],
            rule_version="mirror-rules-v2",
        )
        ranked = await service.query(
            CommonQuery(domain="rankings", scope="full_catalog", rank="kd", period=QueryPeriod(kind="daily", value="2026-09-02"), pin=pin)
        )
        assert ranked.actual_period.value == "2026-09-02"
        assert ranked.pin == pin and ranked.pin != latest.pin
        assert ranked.board.rank_rows[0].day_rank == 1
        old_rules = await service.query(CommonQuery(domain="rules", scope="full_catalog", pin=pin))
        assert old_rules.board.rules.platformRules["shortmax"].yt != latest.board.rules.platformRules["shortmax"].yt
        with pytest.raises(QueryFailure) as caught:
            await service.query(CommonQuery(domain="catalog", scope="full_catalog", pin=pin.model_copy(update={"rule_version": latest.pin.rule_version})))
        assert caught.value.code == "version_conflict"
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_mirrored_candidate_adapter_uses_latest_rank_not_evidence_order(common_board, pg_cluster):
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    board = common_board
    engine = host_engine(pg_cluster.async_url(board["info"]["database"]))
    reader = QueryReader(board["reader"], ssl=False)
    repo = PickRepository(async_sessionmaker(engine), "alice")
    service = CommonQueryService(repo, reader)
    try:
        pin = await repo.current_pin()
        imported = await repo.catalog_rows(pin.catalog_id)
        matched = await service.candidate_matches(imported, PickConditions(signal_kind="kd", sort="rank", exclude_selected=False), set(), pin)
        assert len(matched) > 0
        ranks = [next(s["rank"] for s in row["signals"] if s["kind"] == "kd") for row in matched]
        dates = {next(s["observed_at"] for s in row["signals"] if s["kind"] == "kd") for row in matched}
        assert ranks == sorted(ranks)
        assert dates == {"2026-09-02"}
        absent = await service.candidate_matches(
            imported, PickConditions(signal_kind="kd", sort="rank", query="no-such-title", exclude_selected=False), set(), pin
        )
        assert absent == []
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_legacy_candidate_rank_refuses_week_membership_without_numeric_rank(common_board, pg_cluster):
    from ggwork_pick.contracts import PickConditions
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    reader = QueryReader(common_board["reader"], ssl=False)
    repo = PickRepository(async_sessionmaker(engine), "alice")
    try:
        pin = await repo.current_pin()
        with pytest.raises(ValueError, match="名次"):
            await CommonQueryService(repo, reader).candidate_matches(
                await repo.catalog_rows(pin.catalog_id), PickConditions(signal_kind="kw", sort="rank"), set(), pin
            )
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_query_tool_historical_facts_reach_checker_with_actual_data(common_board, pg_cluster):
    import json
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY

    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_data_tool

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    service = PickService(common_board["data_dir"])
    await service.initialize(async_sessionmaker(engine))
    service.query_reader = QueryReader(common_board["reader"], ssl=False)
    store = ExtensionData("common-tool-task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("common-tool-task", "run", "thread", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="history")
    try:
        payload = json.loads(
            await query_data_tool.coroutine(
                query={"domain": "rankings", "scope": "full_catalog", "rank": "kd", "period": {"kind": "daily", "value": "2026-09-02"}}, runtime=runtime
            )
        )
        evidence = task_from_runtime(runtime).answer_evidence

        def check(text):
            return build_checked_publication(text, evidence=evidence, thread_id="thread", run_id="run", message_id="message")

        rank = next(atom for atom in evidence.atoms if atom.field_name == "kd.rank" and atom.value is not None)
        assert payload["actual_period"]["value"] == "2026-09-02"
        assert check(rank.claim + "。").status == "confirmed"
        assert "[" not in rank.reference and "]" not in rank.reference
        assert check(rank.claim + " [" + rank.reference + "]。").status == "confirmed"
        assert check("本次榜单期次为2026-09-01。").status == "incomplete"
        title = payload["rows"][0]["title"]
        assert check(f"《{title}》的来源为wrong-source。").status == "incomplete"
        assert check(f"《{title}》在账号“other”范围内的发布状态为未发布。").status == "incomplete"
        assert check("整个剧库没有发布记录。").status == "incomplete"
        assert check("剧场“ShortMax”的youtube规则为禁止。").status == "confirmed"
        assert check("剧场“ShortMax”的youtube规则为允许。").status == "incomplete"
        assert all(read.result_id is None for read in evidence.reads)
    finally:
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_scoped_scheduled_account_and_checked_absence_use_actual_posts(common_board, pg_cluster):
    import json
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
    from sqlalchemy import text

    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.query_reader import QueryFailure, QueryReader
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_data_tool

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    service = PickService(common_board["data_dir"])
    await service.initialize(async_sessionmaker(engine))
    service.query_reader = QueryReader(common_board["reader"], ssl=False)
    schema = f"pickm_v{common_board['info']['versions']['v2']:06d}"
    async with engine.begin() as conn:
        old = dict(
            (await conn.execute(text(f"SELECT posts,row_keys,post_count,sched_count,accounts FROM {schema}.catalog_posted WHERE sd='SD-2'"))).mappings().one()
        )
    update = text(
        f"UPDATE {schema}.catalog_posted SET posts=CAST(:posts AS jsonb),row_keys=:keys,post_count=:pub,sched_count=:sched,accounts=:accounts WHERE sd='SD-2'"
    )
    try:
        # Fault/edge injection is confined to the disposable synthetic fixture and restored below.
        async with engine.begin() as conn:
            await conn.execute(
                update, {"posts": json.dumps([{"st": "待公开", "acct": "Account A", "d": "2026-09-01"}]), "keys": ["c-2"], "pub": 0, "sched": 1, "accounts": []}
            )
        repo = PickRepository(service.session_factory, "alice")
        posted = await service.common_query(repo).query(CommonQuery(domain="posted", scope="full_catalog", account="Account A", posted_state="sched"))
        assert [p.sd for p in posted.board.posted] == ["SD-2"]
        pub_page = await service.common_query(repo).query(CommonQuery(domain="posted", scope="full_catalog", account="Account A", posted_state="pub"))
        assert pub_page.facets.posted_states == posted.facets.posted_states
        assert pub_page.counts.matched == 0
        store = ExtensionData("posted-tool")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("posted-tool", "r", "t", "lead"))
        runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="scoped")
        answer = json.loads(
            await query_data_tool.coroutine(query={"domain": "catalog", "scope": "full_catalog", "source_id": "c-2", "account": "Account A"}, runtime=runtime)
        )
        assert answer["rows"][0]["posted_status"] == "not_posted" and answer["rows"][0]["posted_scope_complete"]
        evidence = task_from_runtime(runtime).answer_evidence
        atom = next(a for a in evidence.atoms if a.field_name == "posted_status")

        def check(claim):
            return build_checked_publication(claim, evidence=evidence, thread_id="t", run_id="r", message_id="m")

        assert check(atom.claim + "。").status == "confirmed"
        assert check(atom.claim.replace("Account A", "Account B") + "。").status == "incomplete"
        assert check("以上都从未发布。").status == "incomplete"
        async with engine.begin() as conn:
            await conn.execute(
                update,
                {
                    "posts": json.dumps([{"st": "已公开", "acct": "Account A", "d": "20260901"}]),
                    "keys": ["c-2"],
                    "pub": 1,
                    "sched": 0,
                    "accounts": ["Account A"],
                },
            )
        with pytest.raises(QueryFailure) as error:
            await service.common_query(repo).query(CommonQuery(domain="posted", scope="full_catalog", account="Account A", published_from="2026-09-01"))
        assert error.value.code == "source_unavailable"
        for posts, accounts in (([{"st": "已公开", "d": "2026-09-02"}], ["Account A"]), ([], [])):
            async with engine.begin() as conn:
                await conn.execute(update, {"posts": json.dumps(posts), "keys": ["c-2"], "pub": 1, "sched": 0, "accounts": accounts})
            with pytest.raises(QueryFailure) as error:
                await service.common_query(repo).query(CommonQuery(domain="posted", scope="full_catalog", account="Account A", published_from="2026-09-01"))
            assert error.value.code == "source_unavailable"
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                update,
                {
                    "posts": json.dumps(old["posts"]),
                    "keys": old["row_keys"],
                    "pub": old["post_count"],
                    "sched": old["sched_count"],
                    "accounts": old["accounts"],
                },
            )
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_common_candidate_snapshot_replays_same_order_and_rank_facts(common_board, pg_cluster):
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    reader = QueryReader(common_board["reader"], ssl=False)
    repo = PickRepository(async_sessionmaker(engine), "replay-owner")
    selection = SelectionService(repo, query_service=CommonQueryService(repo, reader))
    try:
        card = await selection.query({"signal_kind": "kd", "sort": "rank", "limit": 2}, thread_id="replay-thread", run_id="replay-run", call_id="replay-call")
        replay = await selection.replay(card["id"])
        assert card["ranking_version"] == "mirror-board-v1"
        assert replay["ranking_reproducible"]
        assert replay["shown"] == [item["identity"] for item in card["items"]]
        assert replay["total"] == card["matched_total"]
    finally:
        await reader.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_unknown_language_common_tool_preserves_readonly_source_record(common_board, pg_cluster):
    import json
    from types import SimpleNamespace

    from deerflow_extension_api import ExtensionData, TaskInfo
    from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
    from sqlalchemy import text

    from ggwork_pick.answer_check import build_checked_publication
    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_data_tool

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    service = PickService(common_board["data_dir"])
    await service.initialize(async_sessionmaker(engine))
    service.query_reader = QueryReader(common_board["reader"], ssl=False)
    schema = f"pickm_v{common_board['info']['versions']['v2']:06d}"
    async with engine.begin() as conn:
        old = dict((await conn.execute(text(f"SELECT lang,title FROM {schema}.catalog_rows WHERE row_key='c-2'"))).mappings().one())
        await conn.execute(text(f"UPDATE {schema}.catalog_rows SET lang='',title='Unknown source language' WHERE row_key='c-2'"))
    try:
        store = ExtensionData("unknown-language-task")
        await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("unknown-language-task", "r", "t", "lead"))
        runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="unknown-language")
        payload = json.loads(await query_data_tool.coroutine(query={"domain": "catalog", "scope": "full_catalog", "language": ""}, runtime=runtime))
        assert payload["projection_version"] == "pick-query-model-v1"
        assert payload["counts"]["returned"] == payload["projection"]["shown"] == 1
        assert payload["projection"]["omitted_rows"] == 0
        row = payload["rows"][0]
        assert row["kind"] == "catalog_record" and row["identity"] is None
        assert row["row_key"] == "c-2" and row["language"] == "" and row["eligibility"] == "unknown"
        evidence = task_from_runtime(runtime).answer_evidence

        def check(claim):
            return build_checked_publication(claim, evidence=evidence, thread_id="t", run_id="r", message_id="m")

        assert check("《Unknown source language》的语种状态为未知。").status == "confirmed"
        assert check("《Unknown source language》的语种为en。").status == "incomplete"
        assert check("《Unknown source language》的youtube规则为允许。").status == "incomplete"
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"UPDATE {schema}.catalog_rows SET lang=:lang,title=:title WHERE row_key='c-2'"), old)
        await service.stop()
        await engine.dispose()
