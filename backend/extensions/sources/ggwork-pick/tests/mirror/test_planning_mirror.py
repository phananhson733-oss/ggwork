"""A plan pin freezes the actual paired mirror permission revision, not a latest lookup."""

import pytest
import test_board_fixture
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

planning_board = test_board_fixture.board


@pytest.mark.asyncio
async def test_plan_pin_matches_real_frozen_mirror_and_preserves_candidate_policy(planning_board, pg_cluster):
    from ggwork_pick.completion_contracts import PlanCreate
    from ggwork_pick.planning import PlanningService
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    engine = host_engine(pg_cluster.async_url(planning_board["info"]["database"]))
    repo = PickRepository(async_sessionmaker(engine), "plan-pin-owner")
    try:
        card = await SelectionService(repo).query({"limit": 1}, thread_id="source", run_id="plan-pin", call_id="source")
        item = card["items"][0]
        created = await PlanningService(repo).create(
            PlanCreate(
                request_id="mirror-plan",
                title="Pinned mirror",
                timezone="UTC",
                rows=[{"row_id": "stable-row", "identity": item["identity"], "source_result_id": card["id"], "source_item_id": item["item_id"]}],
            )
        )
        pin = created["rows"][0]["source_pin"]
        assert pin["mirror_version"] == 3
        assert pin["rule_version"] == "mirror-rules-v3"
        assert pin["catalog_batch_id"] == card["catalog_batch_id"]
        assert pin["knowledge_batch_id"] == card["knowledge_batch_id"]
        assert (await repo.result(card["id"]))["rule_version"] == "pick-rules-v1"
    finally:
        await engine.dispose()
