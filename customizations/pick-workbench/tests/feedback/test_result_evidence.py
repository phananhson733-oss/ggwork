"""Feedback lives beside the strict candidate snapshot and is immutable/owner scoped."""

import json

import pytest
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from .fakes import operating_rows, snapshot_from_rows


@pytest.mark.asyncio
async def test_freeze_result_keeps_old_feedback_and_rejects_foreign_items(pick_db_url, tmp_path):
    from ggwork_pick.feedback.contracts import FeedbackItem, FeedbackReply
    from ggwork_pick.feedback.repository import FeedbackRepository
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    pick = PickRepository(factory, "alice")
    await Importer(pick, service.data_dir).catalog(json.dumps([{"source": "synthetic", "source_id": "1", "language": "en", "title": "A"}]).encode(), "json")
    result = await SelectionService(pick).query({}, thread_id="thread-1", run_id="run-1", call_id="call-1")
    original = await pick.result(result["id"])
    repo = FeedbackRepository(factory, "alice")
    run = await repo.claim("manual")
    snapshot = snapshot_from_rows(operating_rows())
    version = await repo.publish(run["id"], snapshot)
    reply = FeedbackReply(
        status="ok",
        feedback_version_id=version["id"],
        scan_started_at=snapshot.scan_started_at,
        scan_completed_at=snapshot.scan_completed_at,
        freshness="fresh_scan",
        items=[FeedbackItem(key=result["items"][0]["identity"], evidence_kind="unknown")],
    )
    await repo.freeze_result(result["id"], reply)
    assert (await repo.result_evidence(result["id"])).feedback_version_id == version["id"]
    assert (await pick.result(result["id"]))["ordered_items_json"] == original["ordered_items_json"]
    changed = reply.model_copy(update={"notice": "later recomputation"})
    assert (await repo.freeze_result(result["id"], changed)).notice == ""
    with pytest.raises(LookupError):
        await FeedbackRepository(factory, "bob").result_evidence(result["id"])
    with pytest.raises(ValueError):
        await repo.freeze_result(result["id"], reply.model_copy(update={"items": [FeedbackItem(key="foreign", evidence_kind="unknown")]}))
    await engine.dispose()
