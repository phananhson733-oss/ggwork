"""Refreshes coalesce, retain schema identity and never silently reuse stale feedback."""

import asyncio

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.asyncio
async def test_refresh_pending_coalesces_and_can_resume_completed_job(pick_db_url, tmp_path):
    from ggwork_pick.feedback.contracts import SourceField, SourcePage, SourceRecord
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.service import PickService

    release = asyncio.Event()
    started = asyncio.Event()

    class Source:
        async def fields(self, table):
            started.set()
            await release.wait()
            return [SourceField(field_id="fldSynthetic", name="synthetic", field_type="text")]

        async def page(self, table, fields, offset):
            return SourcePage(table_id=table.table_id, records=[SourceRecord(record_id="synthetic", values={"fldSynthetic": "ok"})], has_more=False)

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    constructions = []

    def source_factory(owner, baseline):
        constructions.append((owner, baseline))
        return Source()

    service = FeedbackSyncService(factory, owner_id="alice", enabled=True, source_factory=source_factory)
    try:
        first = await service.refresh("alice", wait_seconds=0.01)
        await started.wait()
        assert first.status == "refresh_pending"
        run_id = (await service.repository("alice").status())["running"]["id"]
        assert first.run_id in (None, run_id)
        second = await service.refresh("alice", wait_seconds=0.01)
        assert second.status == "refresh_pending" and second.run_id in (None, run_id)
        await asyncio.gather(*service.admissions)
        assert len(constructions) == 1
        release.set()
        success = await service.refresh("alice", resume_run_id=run_id, wait_seconds=2)
        assert success.status == "ok"
        assert success.version_id
        assert len(constructions) == 1
        await service.refresh("alice", wait_seconds=2)
        assert len(constructions) == 2
        assert len(constructions[1][1]) == 15
        with pytest.raises(PermissionError):
            await service.refresh("bob", resume_run_id=run_id)
    finally:
        release.set()
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_failure_returns_safe_status_and_does_not_fake_current(pick_db_url, tmp_path):
    from ggwork_pick.feedback.source import FeedbackSourceError
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.service import PickService

    class NoAuth:
        async def fields(self, table):
            raise FeedbackSourceError("auth_required")

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    service = FeedbackSyncService(factory, owner_id="alice", enabled=True, source_factory=lambda *_args: NoAuth())
    try:
        outcome = await service.refresh("alice", wait_seconds=2)
        assert outcome.status == "auth_required"
        assert outcome.version_id is None
    finally:
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_feedback_does_not_touch_source_or_database():
    from ggwork_pick.feedback.sync import FeedbackSyncService

    service = FeedbackSyncService(None, owner_id="alice")
    assert (await service.refresh("alice")).status == "disabled"


@pytest.mark.asyncio
async def test_wait_budget_includes_slow_claim_without_cancelling_admission(monkeypatch):
    from ggwork_pick.feedback.sync import FeedbackSyncService

    release, admitted = asyncio.Event(), asyncio.Event()
    service = FeedbackSyncService(None, owner_id="alice", enabled=True)

    async def slow_claim(*_args):
        await release.wait()
        admitted.set()
        return {"id": "synthetic-job", "status": "failed", "error_code": "auth_required"}

    monkeypatch.setattr(service, "trigger", slow_claim)
    try:
        outcome = await asyncio.wait_for(service.refresh("alice", wait_seconds=0.01), 0.3)
        assert outcome.status == "refresh_pending"
        release.set()
        await asyncio.wait_for(admitted.wait(), 0.3)
    finally:
        release.set()
        await service.stop()


@pytest_asyncio.fixture
async def blocked_sync(pick_db_url, tmp_path):
    from ggwork_pick.feedback.contracts import SourceField, SourcePage, SourceRecord
    from ggwork_pick.feedback.sync import FeedbackSyncService
    from ggwork_pick.service import PickService

    release, started = asyncio.Event(), asyncio.Event()

    class Source:
        async def fields(self, table):
            started.set()
            await release.wait()
            return [SourceField(field_id="fldSynthetic", name="synthetic", field_type="text")]

        async def page(self, table, fields, offset):
            return SourcePage(table_id=table.table_id, records=[SourceRecord(record_id="synthetic", values={"fldSynthetic": "ok"})], has_more=False)

    engine = host_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    await PickService(tmp_path / "files").initialize(factory)
    service = FeedbackSyncService(factory, owner_id="alice", enabled=True, source_factory=lambda *_args: Source())
    try:
        yield service, engine, started, release
    finally:
        release.set()
        await service.stop()
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True], ids=["timeout", "disconnect"])
async def test_finished_callers_stop_receipt_queries_without_cancelling_worker(blocked_sync, disconnect):
    from sqlalchemy import event

    service, engine, started, release = blocked_sync
    run = await service.trigger("alice")
    await asyncio.wait_for(started.wait(), 2)
    selects = []

    def count_selects(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", count_selects)
    callers = [asyncio.create_task(service.refresh("alice", resume_run_id=run["id"], wait_seconds=2 if disconnect else 0.05)) for _ in range(3)]
    try:
        if disconnect:
            await asyncio.sleep(0.15)
            for caller in callers:
                caller.cancel()
        outcomes = await asyncio.gather(*callers, return_exceptions=True)
        if disconnect:
            assert all(isinstance(outcome, asyncio.CancelledError) for outcome in outcomes)
        else:
            assert all(outcome.status == "refresh_pending" and outcome.run_id == run["id"] for outcome in outcomes)
        # Source is still blocked: any SELECT here comes from an orphaned receipt poller.
        selects.clear()
        await asyncio.sleep(0.35)
        assert len(selects) == 0
        assert not service.tasks[run["id"]].done()
        release.set()
        assert (await service.refresh("alice", resume_run_id=run["id"], wait_seconds=2)).status == "ok"
    finally:
        for caller in callers:
            caller.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
        event.remove(engine.sync_engine, "before_cursor_execute", count_selects)


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True], ids=["timeout", "disconnect"])
async def test_caller_ending_during_committed_claim_still_launches_worker(blocked_sync, monkeypatch, disconnect):
    from ggwork_pick.feedback.repository import FeedbackRepository

    service, _engine, started, release = blocked_sync
    claimed, finish_claim = asyncio.Event(), asyncio.Event()
    original_claim = FeedbackRepository.claim
    run = None

    async def slow_claim(repo, *args, **kwargs):
        nonlocal run
        run = await original_claim(repo, *args, **kwargs)
        claimed.set()
        await finish_claim.wait()
        return run

    monkeypatch.setattr(FeedbackRepository, "claim", slow_claim)
    caller = asyncio.create_task(service.refresh("alice", wait_seconds=2 if disconnect else 0.05))
    try:
        await asyncio.wait_for(claimed.wait(), 2)
        if disconnect:
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
        else:
            outcome = await asyncio.wait_for(caller, 0.3)
            assert outcome.status == "refresh_pending"
            assert outcome.run_id is None
        assert not started.is_set()
        finish_claim.set()
        await asyncio.wait_for(started.wait(), 2)
        assert not service.tasks[run["id"]].done()
        release.set()
        assert (await service.refresh("alice", resume_run_id=run["id"], wait_seconds=2)).status == "ok"
    finally:
        finish_claim.set()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)


@pytest.mark.asyncio
async def test_foreign_worker_receipt_and_expired_lease_remain_durable(blocked_sync):
    from datetime import UTC, datetime, timedelta

    from ggwork_pick.feedback.contracts import TABLES, FeedbackSnapshot

    service, _engine, started, _release = blocked_sync
    repo = service.repository("alice")
    run = await repo.claim("manual")
    pending = await service.refresh("alice", wait_seconds=0.05)
    assert pending.status == "refresh_pending" and pending.run_id == run["id"]
    assert not service.tasks and not started.is_set()
    now = datetime.now(UTC)
    snapshot = FeedbackSnapshot(
        scan_started_at=now,
        scan_completed_at=now,
        consistency="bounded_scan",
        tables=[dict(table_id=table.table_id, fields=[], records=[], complete=True, pages=1) for table in TABLES],
    )
    await repo.publish(run["id"], snapshot)
    assert (await service.refresh("alice", resume_run_id=run["id"], wait_seconds=2)).status == "ok"
    old = await repo.claim("manual", now=now - timedelta(minutes=6), lease_seconds=1)
    expired = await service.refresh("alice", resume_run_id=old["id"], wait_seconds=2)
    assert expired.status == "refresh_failed" and expired.error_code == "lease_expired"
    await repo.fail(old["id"], "auth_required", now=now - timedelta(minutes=6))
    expired = await service.refresh("alice", resume_run_id=old["id"], wait_seconds=2)
    assert expired.status == "refresh_failed" and expired.error_code == "receipt_expired"


@pytest.mark.asyncio
async def test_stop_drains_slow_admission_before_worker_cleanup(blocked_sync, monkeypatch):
    from ggwork_pick.feedback.repository import FeedbackRepository

    service, _engine, started, _release = blocked_sync
    claimed = asyncio.Event()
    original_claim = FeedbackRepository.claim

    async def slow_claim(repo, *args, **kwargs):
        run = await original_claim(repo, *args, **kwargs)
        claimed.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # An already committed claim can finish while shutdown cancels admission.
            return run

    monkeypatch.setattr(FeedbackRepository, "claim", slow_claim)
    outcome = await service.refresh("alice", wait_seconds=0.05)
    assert outcome.status == "refresh_pending"
    await asyncio.wait_for(claimed.wait(), 2)
    await asyncio.wait_for(service.stop(), 2)
    assert not service.tasks
    assert not service.waiters
    assert not service.admissions


@pytest.mark.asyncio
@pytest.mark.parametrize("disconnect", [False, True], ids=["timeout", "disconnect"])
async def test_caller_does_not_wait_for_receipt_cancellation_cleanup(blocked_sync, monkeypatch, disconnect):
    from ggwork_pick.feedback.repository import FeedbackRepository

    service, _engine, started, release = blocked_sync
    run = await service.trigger("alice")
    await asyncio.wait_for(started.wait(), 2)
    polling, cleaning, finish_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original_status = FeedbackRepository.status
    calls = 0

    async def slow_status(_repo):
        nonlocal calls
        calls += 1
        polling.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await finish_cleanup.wait()

    monkeypatch.setattr(FeedbackRepository, "status", slow_status)
    caller = asyncio.create_task(service.refresh("alice", resume_run_id=run["id"], wait_seconds=2 if disconnect else 0.02))
    try:
        await asyncio.wait_for(polling.wait(), 2)
        if disconnect:
            caller.cancel()
        done, _pending = await asyncio.wait({caller}, timeout=0.1)
        assert caller in done, "caller must finish within its budget without waiting for cancellation cleanup"
        if disconnect:
            with pytest.raises(asyncio.CancelledError):
                caller.result()
        else:
            assert caller.result().status == "refresh_pending"
        await asyncio.wait_for(cleaning.wait(), 0.1)
        assert not service.tasks[run["id"]].done()
        finish_cleanup.set()
        drained = await asyncio.wait_for(asyncio.gather(*service.waiters, return_exceptions=True), 0.1)
        assert all(isinstance(outcome, asyncio.CancelledError) for outcome in drained)
        assert calls == 1
        assert not service.waiters
        monkeypatch.setattr(FeedbackRepository, "status", original_status)
        release.set()
        assert (await service.refresh("alice", resume_run_id=run["id"], wait_seconds=2)).status == "ok"
    finally:
        finish_cleanup.set()
        caller.cancel()
        await asyncio.gather(caller, return_exceptions=True)
