"""Synthetic barriers prove CPU preparation yields without depending on timing benchmarks."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.feedback.contracts import TABLES, FeedbackSnapshot, SourceField, SourceRecord
from ggwork_pick.feedback.repository import FeedbackRepository, LostFeedbackLease
from ggwork_pick.models import feedback_versions
from ggwork_pick.service import PickService


@pytest.fixture
def payload():
    return FeedbackSnapshot.model_validate(
        {
            "scan_started_at": "2026-10-07T12:00:00Z",
            "scan_completed_at": "2026-10-07T12:00:08Z",
            "consistency": "bounded_scan",
            "tables": [
                {
                    "table_id": table.table_id,
                    "fields": [SourceField(field_id="synthetic", name="Synthetic", field_type="text")],
                    "records": [SourceRecord(record_id="synthetic", values={"synthetic": ["original"]})],
                    "complete": True,
                    "pages": 1,
                }
                for table in TABLES
            ],
        }
    )


@pytest_asyncio.fixture
async def repo(pick_db_url, tmp_path):
    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    try:
        yield FeedbackRepository(factory, "alice")
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def block_work():
    loop = asyncio.get_running_loop()
    entered, finished = asyncio.Event(), asyncio.Event()
    release = threading.Event()

    def wrap(original):
        def blocked(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert release.wait(2), "CPU preparation blocked the event loop"
                return original(*args, **kwargs)
            finally:
                loop.call_soon_threadsafe(finished.set)

        return blocked

    yield wrap, entered, release, finished
    release.set()


@pytest.mark.asyncio
async def test_publish_preparation_yields_and_uses_an_independent_snapshot(repo, payload, monkeypatch, block_work):
    digest = payload.content_hash()
    run = await repo.claim("manual")
    wrap, entered, release, _ = block_work
    loop_thread = threading.get_ident()
    preparation_threads = []
    original_dump, original_validate = FeedbackSnapshot.model_dump, FeedbackSnapshot.model_validate

    def dump(self, *args, **kwargs):
        preparation_threads.append(threading.get_ident())
        return original_dump(self, *args, **kwargs)

    def validate(*args, **kwargs):
        preparation_threads.append(threading.get_ident())
        return original_validate(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(FeedbackSnapshot, "model_dump", dump)
        patch.setattr(FeedbackSnapshot, "model_validate", validate)
        patch.setattr(FeedbackSnapshot, "content_hash", wrap(FeedbackSnapshot.content_hash))
        task = asyncio.create_task(repo.publish(run["id"], payload))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            assert not task.done()
            payload.tables[0].records[0].values["synthetic"].append("caller mutation")
            release.set()
            version = await task
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    assert preparation_threads and all(thread_id != loop_thread for thread_id in preparation_threads)
    assert version["content_hash"] == digest
    restored = await repo.snapshot(version["id"])
    assert restored.tables[0].records[0].values["synthetic"] == ["original"]


@pytest.mark.asyncio
async def test_snapshot_reconstruction_yields(repo, payload, monkeypatch, block_work):
    run = await repo.claim("manual")
    version = await repo.publish(run["id"], payload)
    wrap, entered, release, _ = block_work
    monkeypatch.setattr(FeedbackSnapshot, "model_validate", wrap(FeedbackSnapshot.model_validate))
    task = asyncio.create_task(repo.snapshot(version["id"]))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not task.done()
        release.set()
        assert (await task).content_hash() == payload.content_hash()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancelled_preparation_never_publishes_after_worker_finishes(repo, payload, monkeypatch, block_work):
    from ggwork_pick.feedback import repository

    run = await repo.claim("manual")
    wrap, entered, release, finished = block_work
    monkeypatch.setattr(repository, "_prepare_publication", wrap(repository._prepare_publication))
    task = asyncio.create_task(repo.publish(run["id"], payload))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not finished.is_set()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    await asyncio.wait_for(finished.wait(), 3)
    assert await repo.current() is None
    async with repo.factory() as session:
        assert await session.scalar(select(func.count()).select_from(feedback_versions)) == 0
    assert (await repo.run(run["id"]))["status"] == "running"


@pytest.mark.asyncio
async def test_default_publish_time_checks_lease_after_preparation(repo, payload, monkeypatch, block_work):
    from ggwork_pick.feedback import repository

    start = datetime(2026, 10, 7, 12, tzinfo=UTC)
    run = await repo.claim("manual", now=start, lease_seconds=5)
    current_time = start

    class Clock:
        @staticmethod
        def now(tz):
            return current_time

    monkeypatch.setattr(repository, "datetime", Clock)
    wrap, entered, release, _ = block_work
    monkeypatch.setattr(FeedbackSnapshot, "content_hash", wrap(FeedbackSnapshot.content_hash))
    task = asyncio.create_task(repo.publish(run["id"], payload))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        current_time = start + timedelta(seconds=6)
        release.set()
        with pytest.raises(LostFeedbackLease):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert await repo.current() is None


@pytest.mark.asyncio
async def test_source_snapshot_comparison_yields(payload, monkeypatch, block_work):
    from ggwork_pick.feedback import source

    async def scan(_source):
        return payload

    monkeypatch.setattr(source, "_scan", scan)
    wrap, entered, release, _ = block_work
    monkeypatch.setattr(FeedbackSnapshot, "content_hash", wrap(FeedbackSnapshot.content_hash))
    task = asyncio.create_task(source.read_snapshot(None))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        assert not task.done()
        release.set()
        assert (await task).tables == payload.tables
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
