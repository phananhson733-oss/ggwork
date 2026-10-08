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
    from ggwork_pick.completion_contracts import CommonQuery, QueryPin, QueryPeriod
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
