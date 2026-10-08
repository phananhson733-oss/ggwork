"""Service-owned refresh jobs; bounded callers join without cancelling shared work."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ggwork_pick.feedback.contracts import SourceField
from ggwork_pick.feedback.feishu import FeishuFeedbackSource
from ggwork_pick.feedback.repository import FeedbackRepository, LostFeedbackLease
from ggwork_pick.feedback.source import FeedbackSourceError, read_snapshot
from ggwork_pick.repository import stamp

logger = logging.getLogger(__name__)
WAIT_SECONDS = 20
# Real all-table CLI reads take minutes; caller waiting remains independently bounded.
JOB_SECONDS = 1800
RECEIPT_SECONDS = 300


@dataclass(frozen=True)
class RefreshOutcome:
    status: str
    run_id: str | None = None
    version_id: str | None = None
    verified_at: str | None = None
    scan_started_at: str | None = None
    error_code: str | None = None


def _source(owner, baseline, baseline_transform_version):
    return FeishuFeedbackSource(owner, baseline=baseline, baseline_transform_version=baseline_transform_version)


class FeedbackSyncService:
    def __init__(self, session_factory, *, owner_id: str = "", enabled: bool = False, source_factory=_source):
        self.factory = session_factory
        self.owner_id = owner_id
        self.enabled = enabled
        self.source_factory = source_factory
        self.tasks: dict[str, asyncio.Task] = {}
        self.waiters: set[asyncio.Task] = set()
        self.admissions: set[asyncio.Task] = set()
        self.stopping = False

    def repository(self, owner_id: str) -> FeedbackRepository:
        if not self.owner_id or owner_id != self.owner_id:
            raise PermissionError("当前用户未获反馈来源授权")
        return FeedbackRepository(self.factory, owner_id)

    async def trigger(self, owner_id: str, trigger: str = "manual") -> dict:
        if not self.enabled or self.stopping:
            raise ValueError("反馈同步未启用")
        repo = self.repository(owner_id)
        run = await repo.claim(trigger, lease_seconds=JOB_SECONDS + 30)
        if run is None:
            status = await repo.status()
            run = status["running"] or status["last_run"]
            if run is None:
                raise FeedbackSourceError("refresh_failed")
            return run
        task = asyncio.create_task(self._run(repo, run["id"]), name=f"pick-feedback-{run['id']}")
        self.tasks[run["id"]] = task
        task.add_done_callback(lambda _task: self.tasks.pop(run["id"], None))
        return run

    async def _run(self, repo: FeedbackRepository, run_id: str):
        try:
            async with asyncio.timeout(JOB_SECONDS):
                current = await repo.current()
                baseline = {
                    table["table_id"]: [SourceField.model_validate(field) for field in table["fields"]]
                    for table in (current["manifest_json"]["tables"] if current else [])
                }
                baseline_transform_version = current["manifest_json"].get("transform_version", "feedback-v1") if current else None
                snapshot = await read_snapshot(self.source_factory(repo.owner_id, baseline, baseline_transform_version))
                await repo.publish(run_id, snapshot)
        except asyncio.CancelledError:
            await repo.fail(run_id, "cancelled")
            raise
        except LostFeedbackLease:
            # A replacement worker now owns the scope. Never publish/release its lease.
            await repo.fail(run_id, "refresh_failed")
        except FeedbackSourceError as exc:
            await repo.fail(run_id, exc.code)
        except Exception as exc:
            logger.warning("Feedback refresh failed: %s", type(exc).__name__)
            await repo.fail(run_id, "refresh_failed")

    async def refresh(self, owner_id: str, *, wait_seconds: float = WAIT_SECONDS, resume_run_id: str | None = None, trigger: str = "query") -> RefreshOutcome:
        if not self.enabled:
            return RefreshOutcome("disabled")
        self.repository(owner_id)
        receipt = {}
        # The caller budget includes admission, DB waits and receipt polling.
        waiter = asyncio.create_task(self._wait_receipt(owner_id, resume_run_id, receipt, trigger))
        self.waiters.add(waiter)
        waiter.add_done_callback(self.waiters.discard)
        try:
            done, _pending = await asyncio.wait({waiter}, timeout=max(0, min(WAIT_SECONDS, wait_seconds)))
            if done:
                return waiter.result()
            return RefreshOutcome("refresh_pending", run_id=receipt.get("id"))
        finally:
            # Cleanup may await a database rollback; it must not extend the caller's budget.
            # Keep the waiter tracked until it drains so stop() can still collect it.
            if not waiter.done():
                waiter.cancel()

    async def _admit(self, owner_id, trigger):
        async with asyncio.timeout(JOB_SECONDS + 60):
            return await self.trigger(owner_id, trigger)

    def _admission_done(self, task):
        self.admissions.discard(task)
        if not task.cancelled():
            # Consume failures even if the caller has already timed out or disconnected.
            task.exception()

    async def _wait_receipt(self, owner_id, resume_run_id, receipt, trigger):
        try:
            async with asyncio.timeout(JOB_SECONDS + 60):
                return await self._poll_receipt(owner_id, resume_run_id, receipt, trigger)
        except (LookupError, TimeoutError):
            return RefreshOutcome("refresh_failed", run_id=receipt.get("id"), error_code="receipt_unavailable")
        except Exception as exc:
            logger.warning("Feedback admission failed: %s", type(exc).__name__)
            return RefreshOutcome("refresh_failed", run_id=receipt.get("id"), error_code="refresh_failed")

    async def _poll_receipt(self, owner_id, resume_run_id, receipt, trigger):
        repo = self.repository(owner_id)
        if resume_run_id:
            run = await repo.run(resume_run_id)
            cutoff = stamp(datetime.now(UTC) - timedelta(seconds=RECEIPT_SECONDS))
            if run["status"] != "running" and (not run["finished_at"] or run["finished_at"] < cutoff):
                return RefreshOutcome("refresh_failed", run_id=run["id"], error_code="receipt_expired")
        else:
            # Only admission is service-owned: a committed lease must launch its worker even
            # after the caller leaves. Receipt polling ends with the caller.
            admission = asyncio.create_task(self._admit(owner_id, trigger))
            self.admissions.add(admission)
            admission.add_done_callback(self._admission_done)
            run = await asyncio.shield(admission)
        receipt["id"] = run["id"]
        while run["status"] == "running":
            if (await repo.status())["lease_expired"]:
                return RefreshOutcome("refresh_failed", run_id=run["id"], error_code="lease_expired")
            # Local/foreign workers use the same durable receipt. No waiter owns/cancels the service task.
            await asyncio.sleep(0.1)
            run = await repo.run(run["id"])
        if run["status"] == "success":
            return RefreshOutcome("ok", run_id=run["id"], version_id=run["version_id"], verified_at=run["finished_at"], scan_started_at=run["started_at"])
        error = run["error_code"]
        status = error if error in {"auth_required", "unavailable", "schema_changed"} else "refresh_failed"
        return RefreshOutcome(status, run_id=run["id"], error_code=error)

    async def stop(self):
        self.stopping = True
        waiters = list(self.waiters)
        for waiter in waiters:
            waiter.cancel()
        if waiters:
            await asyncio.gather(*waiters, return_exceptions=True)
        admissions = list(self.admissions)
        for admission in admissions:
            admission.cancel()
        if admissions:
            await asyncio.gather(*admissions, return_exceptions=True)
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
