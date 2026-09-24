"""The mirror run (plan 5.2; implementation note P2-5c): MirrorSync.run(trigger), switched on by PickService (U31).

One run, under the process's sync_lock and the mirror lock on a dedicated connection (P2-0):
1. the run record first, so a run that cannot take the lock is still seen (U15); the lock taken as "sync" or, held by the
   backfill or the cleanup command, the run fails with who holds it, not counted (U46);
2. leftovers of a dead run cleaned (clean_leftovers), version and batch retention (an unexpected error in either is
   recorded by step and class and the run goes on, F6), the disk check, the size cap (U43);
3. the manifest, waiting out source_busy (a busy past 20 minutes fails, counted, U42; a 409 is drift; any other manifest
   failure falls back to v1 without as_of, counted, U16). The 20 minutes are each attempt's: the drift retry's manifest
   waits afresh (the brief's loop), so a run can outlast U14's 51-minute estimate only when a drift and a second busy
   spell meet;
4. a building version (none over the size cap), then the v1 half (run_v1) and the mirror half (run_v2);
5. publish: the pair when both halves pass; the agent batches alone when only the mirror half failed (degraded, counted);
   nothing when the v1 half failed (counted). Drift repeats the attempt once after 90 s with a new as_of; the second drift
   in v2 degrades (U17), in v1 or the manifest publishes nothing;
6. retention again, the curve fold when a manifest was read (U29), the ERROR log from the third failure in a row (U50).
The lock is released and the connection closed on the way out whatever happens, a cancellation included; a cancelled run
fails what it built and staged under a shield, is recorded as failed and is not counted (U11).
"""

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from ggwork_pick.mirror.client import FeedClient
from ggwork_pick.mirror.connection import MirrorConnectionError
from ggwork_pick.mirror.errors import BusyTimeout, ConfigError, DriftError, FeedError
from ggwork_pick.mirror.feed_shape import Manifest
from ggwork_pick.mirror.gates import GateError
from ggwork_pick.mirror.lock import LOCK_STUCK_AFTER, lock_status, mirror_lock
from ggwork_pick.mirror.publish import ALERT_AFTER
from ggwork_pick.mirror.retention import clean_leftover_versions, prune_versions
from ggwork_pick.mirror.run_result import (
    AT_MANIFEST,
    AT_V1,
    AT_V2,
    BUSY,
    CAPACITY,
    DEGRADED,
    DRIFT,
    FALLBACK_V1,
    LOCK_BUSY,
    LOCK_BUSY_ERROR,
    NOT_PUBLISHED,
    PAIRED,
    SUCCESS,
    V1,
    Retry,
    RunOutcome,
    cancelled_values,
    failed,
    internal_error_values,
    ms,
    safe_error,
    warning_codes,
)
from ggwork_pick.mirror.run_v1 import V1Failed, V1Staged, stage_v1
from ggwork_pick.mirror.run_v2 import Budget, Built, MirrorDeadline, build_mirror
from ggwork_pick.mirror.series import fold_series
from ggwork_pick.mirror.versions import MirrorBuildError, MirrorVersion, create_version, mark_failed
from ggwork_pick.mirror.writer import MirrorRecordError
from ggwork_pick.repository import SHARED_OWNER, PickRepository, stamp
from ggwork_pick.sync import (
    DEADLINE_SECONDS,
    KEEP_BATCHES,
    MIN_FREE_BYTES,
    SOURCE,
    check_disk,
    discard_staged,
    prune_batches,
    pull_error,
    pull_v1,
    shielded,
)
from ggwork_pick.sync import FeedError as V1FeedError

__all__ = [
    "BLOB_OUTCOMES",
    "LOCK_STUCK_AFTER",
    "MirrorLimits",
    "MirrorSync",
    "clean_leftovers",
    "delete_unused",
    "describe_db_error",
    "permission_refused",
    "sqlstate",
    "sweep_leftovers",
    "tolerate_permission",
]

logger = logging.getLogger(__name__)

SYNC_HOLDER = "sync"
CLEANUP_HOLDER = "cleanup"  # sweep_leftovers takes the lock as the cleanup command does
ATTEMPTS = 2  # plan 5.1: a drift repeats the run once
BUSY_WAIT_TOTAL = 1200
RUN_DEADLINE = 900
MIRROR_DEADLINE = 750
DRIFT_RETRY_DELAY = 90
READ_FAILED_RETRY_DELAY = 5
COPY_TIMEOUT = 60
STATEMENT_TIMEOUT = 120
DEFAULT_DB_SIZE_CAP = 6 * 2**30  # PICK_DB_SIZE_CAP_BYTES when unset (SyncSettings.db_size_cap)
# A cleanup step refused for want of privilege (42501: an orphan schema another role owns, say, raised by asyncpg or
# through the ORM; or a PermissionError on a blob) is recorded in details_json and the next step still runs; anything
# else ends that step (cleanup or retention), is recorded by class, and the run goes on (_going_on; F6).
PERMISSION_SQLSTATES = frozenset({"42501"})
BLOB_OUTCOMES = ("deleted", "missing", "refused", "outside")
_DB_SIZE = "SELECT pg_database_size(current_database())"
_PAIRED = (
    "SELECT coalesce(status = 'published' AND agent_catalog_batch_id = $2 AND agent_knowledge_batch_id = $3, false) FROM pick_mirror.versions WHERE id = $1"
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class MirrorLimits:
    """The run's budgets (implementation note P2-5c constants), in seconds and bytes; tests pass smaller ones."""

    busy_wait_total: float = BUSY_WAIT_TOTAL  # per attempt: FeedClient.manifest_when_free starts from zero each call
    run_deadline: float = RUN_DEADLINE
    mirror_deadline: float = MIRROR_DEADLINE
    drift_retry_delay: float = DRIFT_RETRY_DELAY
    read_failed_retry_delay: float = READ_FAILED_RETRY_DELAY
    copy_timeout: float = COPY_TIMEOUT
    statement_timeout: float = STATEMENT_TIMEOUT
    alert_after: int = ALERT_AFTER
    db_size_cap: int = DEFAULT_DB_SIZE_CAP
    keep_batches: int = KEEP_BATCHES
    min_free_bytes: int = MIN_FREE_BYTES


def sqlstate(exc: BaseException) -> str | None:
    """A database error's SQLSTATE, raised by asyncpg itself or wrapped by SQLAlchemy (DBAPIError.orig)."""
    for candidate in (exc, getattr(exc, "orig", None)):
        state = getattr(candidate, "sqlstate", None)
        if isinstance(state, str):
            return state
    return None


def permission_refused(exc: BaseException) -> bool:
    """The database (42501) or the file system (PermissionError) refused for want of privilege."""
    return isinstance(exc, PermissionError) or sqlstate(exc) in PERMISSION_SQLSTATES


def describe_db_error(exc: BaseException) -> str:
    """The error class and SQLSTATE only: PostgreSQL's own message can quote a value, the file system's a path."""
    state = sqlstate(exc)
    return f"{type(exc).__name__}（SQLSTATE {state}）" if state is not None else type(exc).__name__


async def tolerate_permission(step: str, action: Callable[[], Awaitable]) -> tuple[object | None, str | None]:
    """(action's result, None), or (None, safe text) when it was refused for want of privilege (permission_refused).
    The text names the step, the error class and the SQLSTATE, never the database's or the file system's message."""
    try:
        return await action(), None
    except Exception as exc:
        if not permission_refused(exc):
            raise
        text = f"{step}：{describe_db_error(exc)}"
        logger.warning("[pick-mirror] %s; going on without it", text)
        return None, text


async def clean_leftovers(conn, repo: PickRepository, *, data_dir: Path, clock: Callable[[], datetime] = _utc_now) -> dict:
    """What a dead run left (U9, U41), under the mirror lock held on `conn` as sync or cleanup: building versions failed,
    pickm_v* schemas no building or published version claims dropped, the shared owner's importing batches failed and
    their unused blobs deleted. The admin cleanup command calls this too. A step refused for want of privilege is
    recorded in errors and the next one still runs (a blob the file system refuses is counted there and the others
    still go); any other error raises. Returns details_json's cleanup (safe): blobs_deleted counts files deleted."""
    if repo.owner_id != SHARED_OWNER:
        raise ValueError("遗留清理只处理共享属主的批次：传 PickRepository.shared(...)")
    report, versions_error = await tolerate_permission("leftover_versions", lambda: clean_leftover_versions(conn, clock=clock))
    found, batches_error = await tolerate_permission("leftover_batches", repo.fail_leftover_staged)
    blobs = await asyncio.to_thread(delete_unused, data_dir, found or [])
    blobs_error = f"leftover_blobs：PermissionError（{blobs['refused']} 个文件）" if blobs["refused"] else None
    errors = [error for error in (versions_error, batches_error, blobs_error) if error]
    return {"versions": report.details() if report is not None else None, "blobs_deleted": blobs["deleted"], "errors": errors}


async def sweep_leftovers(dsn: str, repo: PickRepository, *, data_dir: Path, clock: Callable[[], datetime] = _utc_now) -> dict | None:
    """clean_leftovers for a run that holds no mirror lock of its own (review flow-2): the v1 RealShortSync on PostgreSQL
    (the switch rolled back to 0, or on without its token, U31) and MirrorSync's fallback when its dedicated connection
    failed. A mirror run killed after staging leaves importing batches holding their content-hash slots, and the v1
    import of the same content would stop on the unique constraint every time. The lock is taken as cleanup without
    waiting, so a live mirror run elsewhere is never touched.

    Never raises but on cancellation, and returns details_json's cleanup: None when there was nothing to clean,
    {"skipped": "lock_busy"} when another holder has the lock, {"error": <class and SQLSTATE>} when it failed, else
    clean_leftovers' report."""
    try:
        async with mirror_lock(dsn, holder=CLEANUP_HOLDER, clock=clock) as conn:
            if conn is None:
                return {"skipped": LOCK_BUSY}
            cleanup = await clean_leftovers(conn, repo, data_dir=data_dir, clock=clock)
    except Exception as exc:
        logger.warning("[pick-mirror] the leftover sweep before a v1 pull failed (%s); the pull goes on", describe_db_error(exc), exc_info=True)
        return {"error": describe_db_error(exc)}
    return cleanup if _cleaned_anything(cleanup) else None


def _cleaned_anything(cleanup: dict) -> bool:
    versions = cleanup["versions"] or {}
    touched = any(versions.get(key) for key in ("skipped", "failed_building", "dropped_schemas", "lock_busy"))
    return touched or bool(cleanup["blobs_deleted"]) or bool(cleanup["errors"])


def delete_unused(data_dir: Path, paths: Sequence[str]) -> dict[str, int]:
    """Delete each blob under data_dir, one by one: counts of deleted, already gone (missing), refused by the file system
    (PermissionError; the rest still go) and outside data_dir (never touched). Blocking: call it in a worker thread."""
    root = data_dir.resolve()
    counted = Counter(_delete_one(root, Path(raw)) for raw in paths)
    return {outcome: counted.get(outcome, 0) for outcome in BLOB_OUTCOMES}


def _delete_one(root: Path, path: Path) -> str:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        return "outside"
    try:
        resolved.unlink()
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "refused"
    return "deleted"


async def _going_on(step: str, step_done: Awaitable[dict]) -> dict:
    """step_done's report, or {"error": "<step>：<class and SQLSTATE>"} when it raised: never the error's own text."""
    try:
        return await step_done
    except Exception as exc:
        text = f"{step}：{describe_db_error(exc)}"
        logger.warning("[pick-mirror] %s before publishing failed; the run goes on", text, exc_info=True)
        return {"error": text}


def _failure_code(exc: BaseException) -> str:
    """The <gate or error class> of degraded:<...> (publish.FAILURE_REASONS)."""
    if isinstance(exc, MirrorDeadline):
        return "deadline"
    if isinstance(exc, GateError):
        return exc.gate
    return type(exc).__name__[:64]


def _v1_details(v1: V1Staged | V1Failed) -> dict:
    scan = v1.scan
    details = {"gates": {"v1_text": v1.gate.as_json()}} if v1.gate is not None else {}
    if scan is not None:
        details = {**details, "scrub_hits": {"v1": scan.row_hits, "v1_rules": scan.rules_hits}}
    if isinstance(v1, V1Staged):
        details = {**details, "read_failed_retries": v1.retries, "paging_drift": v1.paging_drift}
    return details


def _with_built(outcome: RunOutcome, built: Built) -> RunOutcome:
    merged = outcome.merged("gates", **built.gates.as_json()).merged("scrub_hits", mirror=built.text.found.hits).with_stages(**built.stages)
    retries = merged.details.get("read_failed_retries", 0) + built.retries
    return merged.with_details(read_failed_retries=retries, copied=dict(built.copied))


class MirrorSync:
    """One mirror run per call of run(trigger); the schedule and the manual button get it from PickService.realshort_sync."""

    def __init__(
        self,
        service,
        *,
        base_url: str,
        export_token: str,
        feed_token: str,
        dsn: str,
        transport=None,
        limits: MirrorLimits | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timer: Callable[[], float] = time.monotonic,
        cpu_timer: Callable[[], float] = time.process_time,
    ):
        self.service = service
        self.base_url = base_url
        self._export_token, self._feed_token, self._dsn = export_token, feed_token, dsn
        self.transport = transport
        self.limits = limits or MirrorLimits()
        self._clock, self._sleep, self._timer, self._cpu = clock, sleep, timer, cpu_timer

    def __repr__(self) -> str:
        return f"MirrorSync(base_url={self.base_url!r})"

    # ---- the run and its record

    async def run(self, trigger: str) -> dict:
        if self.service.session_factory is None:
            return {"status": "failed", "error": "选剧服务尚未就绪"}
        if self.service.sync_lock.locked():
            return {"status": "already_running"}
        async with self.service.sync_lock:
            repo = PickRepository.shared(self.service.session_factory)
            record = await repo.start_sync_run(SOURCE, trigger)
            return await self._recorded(repo, record["id"])

    async def _recorded(self, repo: PickRepository, run_id: str) -> dict:
        cpu = self._cpu()
        try:
            outcome = await self._locked(repo, run_id)
        except asyncio.CancelledError:
            await shielded(repo.finish_sync_run(run_id, **cancelled_values()))
            raise
        except Exception as exc:
            await repo.finish_sync_run(run_id, **internal_error_values(exc))
            raise
        outcome = self._alerted(outcome).with_details(cpu_ms=ms(self._cpu() - cpu))
        return await repo.finish_sync_run(run_id, **outcome.finish_values(alert_after=self.limits.alert_after))

    def _alerted(self, outcome: RunOutcome) -> RunOutcome:
        count = outcome.consecutive_failures
        if count is not None and count >= self.limits.alert_after and outcome.reason is not None:
            # plan 808, U50: the count and the reason code only.
            logger.error("[pick-mirror] 连续失败 %d 次：%s", count, outcome.reason)
        return outcome

    async def _locked(self, repo: PickRepository, run_id: str) -> RunOutcome:
        try:
            async with mirror_lock(self._dsn, holder=SYNC_HOLDER, clock=self._clock) as conn:
                if conn is None:
                    return await self._lock_busy()
                return await self._held(conn, repo, run_id)
        except MirrorConnectionError as exc:
            logger.warning("[pick-mirror] the dedicated connection failed (%s); this run falls back to v1", exc)
            # No lock was held, so nothing a dead run staged was cleaned: the sweep tries once more (review flow-2).
            return await self._fallback(repo, cause="connection", error=safe_error(exc), sweep=True)

    async def _lock_busy(self) -> RunOutcome:
        """Held by the backfill or the cleanup command (or a stuck holder): failed with who holds it, not counted (U15, U46)."""
        async with self.service.session_factory() as session:
            status = await lock_status(session, now=self._clock())
        since = status["holder_since"]
        holder = {"pid": status["holder_pid"], "holder": status["holder"], "since": stamp(since) if since is not None else None}
        return failed(LOCK_BUSY, error=LOCK_BUSY_ERROR, lock_holder=holder)

    # ---- under the lock

    async def _held(self, conn, repo: PickRepository, run_id: str) -> RunOutcome:
        prepared, stages = await self._prepare(conn, repo)
        outcome = await self._disk_blocked(repo)
        if outcome is None:
            outcome = await self._mirror_run(conn, repo, run_id)
        return outcome.with_details(**prepared).with_stages(**stages)

    async def _mirror_run(self, conn, repo: PickRepository, run_id: str) -> RunOutcome:
        size = await conn.fetchval(_DB_SIZE, timeout=self.limits.statement_timeout)
        capacity = {"db_size_bytes": size, "db_size_cap": self.limits.db_size_cap, "over_cap": size >= self.limits.db_size_cap}
        try:
            client = self._client()
        except ConfigError as exc:
            return (await self._fallback(repo, cause="client", error=safe_error(exc))).with_details(capacity=capacity)
        async with client:
            outcome = await self._attempts(conn, repo, client, run_id, over_cap=capacity["over_cap"])
            outcome = await self._after(conn, repo, client, outcome)
        return outcome.with_details(capacity=capacity)

    async def _prepare(self, conn, repo: PickRepository) -> tuple[dict, dict]:
        """Plan 5.2 step 2 before anything is read: leftovers, then version and batch retention (details, stage times).
        Neither stops the run (F6, the owner's call): an unexpected error is recorded as {"error": "<step>：<class>"},
        not counted, as retention after publishing does; a refusal for want of privilege is recorded inside each step."""
        begun = self._timer()
        cleanup = await _going_on("cleanup", clean_leftovers(conn, repo, data_dir=self.service.data_dir, clock=self._clock))
        cleaned = self._timer()
        retention = await _going_on("retention", self._retain(conn, repo))
        return {"cleanup": cleanup, "retention": retention}, {"cleanup_ms": ms(cleaned - begun), "retention_ms": ms(self._timer() - cleaned)}

    async def _retain(self, conn, repo: PickRepository) -> dict:
        versions, error = await tolerate_permission("retention", lambda: prune_versions(conn, clock=self._clock))
        blobs = await prune_batches(self.service, repo, self.limits.keep_batches)
        return {"versions": versions.details() if versions is not None else None, "batch_blobs_deleted": blobs, "errors": [error] if error else []}

    async def _disk_blocked(self, repo: PickRepository) -> RunOutcome | None:
        try:
            await asyncio.to_thread(check_disk, self.service.data_dir, self.limits.min_free_bytes)
        except V1FeedError as exc:
            return await self._count_failed(repo, V1, error=safe_error(exc), blocked="disk")
        return None

    def _client(self) -> FeedClient:
        return FeedClient(
            base_url=self.base_url,
            export_token=self._export_token,
            feed_token=self._feed_token,
            transport=self.transport,
            clock=self._clock,
            sleep=self._sleep,
            timer=self._timer,
            busy_budget=self.limits.busy_wait_total,
            read_retry_delay=self.limits.read_failed_retry_delay,
        )

    async def _attempts(self, conn, repo: PickRepository, client: FeedClient, run_id: str, *, over_cap: bool) -> RunOutcome:
        drifts: tuple[str, ...] = ()
        for attempt in range(1, ATTEMPTS + 1):
            result = await self._attempt(conn, repo, client, run_id, over_cap=over_cap, final=attempt == ATTEMPTS)
            if isinstance(result, Retry):
                drifts = (*drifts, result.stage)
                await self._sleep(self.limits.drift_retry_delay)
                continue
            seen = (*drifts, result.drift_stage) if result.drift_stage else drifts
            return result.with_details(attempts=attempt, drift={"count": len(seen), "stages": list(seen)})
        raise AssertionError("the last attempt never asks for another")

    async def _attempt(self, conn, repo: PickRepository, client: FeedClient, run_id: str, *, over_cap: bool, final: bool) -> RunOutcome | Retry:
        begun = self._timer()
        manifest = await self._read_manifest(client, repo, final=final)
        if not isinstance(manifest, Manifest):
            return manifest
        stages = {"manifest_ms": ms(self._timer() - begun)}
        budget = Budget(self._timer, manifest.metrics.started, self.limits.run_deadline, self.limits.mirror_deadline)
        version, build_error = await self._open_version(conn, manifest, run_id, over_cap=over_cap)
        staged: tuple = ()
        try:
            begun = self._timer()
            v1 = await stage_v1(self.service, repo, client, manifest, seconds=budget.run_left())
            stages = {**stages, "v1_ms": ms(self._timer() - begun)}
            if isinstance(v1, V1Failed):
                result = await self._v1_failed(conn, repo, version, v1, final=final)
            else:
                staged = v1.batches
                result = await self._publish(conn, repo, client, manifest, version, build_error, v1, budget, final=final)
        except BaseException:
            await shielded(self._discard(conn, repo, version, staged))
            raise
        if isinstance(result, Retry):
            return result
        # The fold reads rs_series_day at this manifest's as_of and fp (U29); after a drift that fp is stale.
        pinned = replace(result, manifest=manifest if result.drift_stage is None else None, source_as_of=manifest.as_of_text)
        attempt_details = {"as_of": manifest.as_of_text, "version": version.id if version else None, "warnings": warning_codes(manifest)}
        return pinned.with_details(**attempt_details).with_stages(**stages)

    async def _read_manifest(self, client: FeedClient, repo: PickRepository, *, final: bool) -> Manifest | RunOutcome | Retry:
        try:
            return await client.manifest_when_free()
        except BusyTimeout as exc:
            return await self._count_failed(repo, BUSY, error=safe_error(exc), busy_waited_s=exc.waited)
        except DriftError as exc:
            if not final:
                return Retry(AT_MANIFEST)
            return replace(await self._count_failed(repo, DRIFT, error=safe_error(exc)), drift_stage=AT_MANIFEST)
        except FeedError as exc:
            return await self._fallback(repo, cause=f"manifest:{type(exc).__name__}", error=safe_error(exc))

    async def _open_version(self, conn, manifest: Manifest, run_id: str, *, over_cap: bool) -> tuple[MirrorVersion | None, str | None]:
        """The building version (plan 5.2 step 4); none over the size cap (U43). A failure to build one is a mirror-side
        failure: the attempt goes on without it and degrades with its class."""
        if over_cap:
            return None, None
        meta = manifest.meta
        arguments = {"freshness": meta["freshness"], "warnings": meta["warnings"], "latest_snapshot": manifest.latest_snapshot}
        try:
            version = await create_version(
                conn, as_of=manifest.as_of, fingerprint=manifest.fingerprint, counts=manifest.counts, sync_run_id=run_id, clock=self._clock, **arguments
            )
        except (MirrorBuildError, ValueError) as exc:
            logger.warning("[pick-mirror] building a version failed (%s); this attempt publishes the agent batches alone", safe_error(exc))
            return None, _failure_code(exc)
        return version, None

    async def _v1_failed(self, conn, repo: PickRepository, version: MirrorVersion | None, v1: V1Failed, *, final: bool) -> RunOutcome | Retry:
        """plan 5.5: neither side publishes; the version is failed, and nothing is left importing (stage_v1)."""
        await self._drop_version(conn, version, v1.error)
        if v1.kind == DRIFT and not final:
            return Retry(AT_V1)
        reason = DRIFT if v1.kind == DRIFT else V1
        outcome = await self._count_failed(repo, reason, error=v1.error, **_v1_details(v1))
        return replace(outcome, drift_stage=AT_V1) if v1.kind == DRIFT else outcome

    async def _publish(self, conn, repo, client, manifest, version, build_error, v1: V1Staged, budget: Budget, *, final: bool) -> RunOutcome | Retry:
        if version is None:
            return await self._agent_only(repo, v1, CAPACITY if build_error is None else f"degraded:{build_error}")
        try:
            built = await build_mirror(
                conn,
                client,
                manifest,
                version,
                v1.scan,
                budget=budget,
                copy_timeout=self.limits.copy_timeout,
                statement_timeout=self.limits.statement_timeout,
                timer=self._timer,
            )
        except DriftError:
            return await self._mirror_drift(conn, repo, version, v1, final=final)
        except Exception as exc:
            return await self._mirror_failed(conn, repo, version, v1, exc)
        if not built.gates.ok:
            await self._drop_version(conn, version, f"镜像闸门未通过：{built.gates.first_failure}")
            return _with_built(await self._agent_only(repo, v1, f"degraded:{built.gates.first_failure}"), built)
        return _with_built(await self._pair(conn, repo, version, v1, built), built)

    async def _mirror_drift(self, conn, repo: PickRepository, version: MirrorVersion, v1: V1Staged, *, final: bool) -> RunOutcome | Retry:
        await self._drop_version(conn, version, "镜像拉取途中来源变了（漂移）")
        if not final:
            await discard_staged(self.service, repo, v1.batches)
            return Retry(AT_V2)
        # U17: v1 passed every page's check at this as_of; only v2 drifted, a second time: the agent batches go alone.
        return replace(await self._agent_only(repo, v1, f"degraded:{DRIFT}"), drift_stage=AT_V2)

    async def _mirror_failed(self, conn, repo: PickRepository, version: MirrorVersion, v1: V1Staged, exc: Exception) -> RunOutcome:
        expected = isinstance(exc, FeedError | MirrorBuildError | MirrorRecordError | GateError | TimeoutError)
        logger.warning("[pick-mirror] the mirror half failed (%s); degrading to the agent batches", safe_error(exc), exc_info=not expected)
        await self._drop_version(conn, version, safe_error(exc))
        outcome = await self._agent_only(repo, v1, f"degraded:{_failure_code(exc)}")
        return outcome.with_details(mirror_error=safe_error(exc))

    async def _pair(self, conn, repo: PickRepository, version: MirrorVersion, v1: V1Staged, built: Built) -> RunOutcome:
        """Step 9. The pair transaction rolled back (a GRANT refused, the version no longer building) is a mirror-side
        failure like any other: the version fails and the agent batches go alone, which raises in turn if they cannot.
        An error raised after the COMMIT took effect (the reply lost with the connection) leaves the version published
        with this pair: the run is paired, and the error is kept in details.pair_error."""
        begun = self._timer()
        accept = built.gates.accept_empty
        paired = self._published(PAIRED, v1, reason=None, count=0)
        try:
            await repo.publish_mirror_pair(
                version_id=version.id,
                schema_name=version.schema_name,
                batches=list(v1.batches),
                t=self._clock(),
                accept_empty_used=accept.used,
                accept_empty_seen=accept.seen,
            )
        except Exception as exc:
            if not await self._paired_anyway(conn, version, v1):
                return await self._mirror_failed(conn, repo, version, v1, exc)
            logger.warning("[pick-mirror] the pair transaction raised %s after its COMMIT took effect; the run is paired", safe_error(exc))
            paired = paired.with_details(pair_error=safe_error(exc))
        return paired.with_stages(publish_ms=ms(self._timer() - begun))

    async def _paired_anyway(self, conn, version: MirrorVersion, v1: V1Staged) -> bool:
        """Whether the version is published with this attempt's pair although publish_mirror_pair raised; False when that
        cannot be read either (the degraded path then decides)."""
        catalog, knowledge = v1.batches
        try:
            return bool(await conn.fetchval(_PAIRED, version.id, catalog["id"], knowledge["id"], timeout=self.limits.statement_timeout))
        except Exception as exc:
            logger.warning("[pick-mirror] whether the pair was published could not be read (%s)", safe_error(exc))
            return False

    async def _agent_only(self, repo: PickRepository, v1: V1Staged, reason: str) -> RunOutcome:
        begun = self._timer()
        count = await repo.publish_agent_only(batches=list(v1.batches), reason=reason, t=self._clock())
        return self._published(DEGRADED, v1, reason=reason, count=count).with_stages(publish_ms=ms(self._timer() - begun))

    def _published(self, outcome: str, v1: V1Staged, *, reason: str | None, count: int | None) -> RunOutcome:
        catalog, knowledge = v1.batches
        return RunOutcome(
            SUCCESS,
            outcome,
            reason=reason,
            rows=v1.rows,
            catalog_batch_id=catalog["id"],
            knowledge_batch_id=knowledge["id"],
            consecutive_failures=count,
            details=_v1_details(v1),
        )

    # ---- failure bookkeeping

    async def _count_failed(self, repo: PickRepository, reason: str, *, error: str, **details) -> RunOutcome:
        count = await repo.record_mirror_failure(reason=reason, t=self._clock())
        return failed(NOT_PUBLISHED, reason=reason, error=error, count=count, **details)

    async def _fallback(self, repo: PickRepository, *, cause: str, error: str, sweep: bool = False) -> RunOutcome:
        """v1 straight, without as_of (plan 5.5 "manifest 本身失败"): pull_v1, never RealShortSync.run, which would see the
        sync_lock held and open a second run record. Published, it is a success counted as fallback_v1. sweep: no lock
        was held, so leftovers are swept first (sweep_leftovers)."""
        details = {"fallback": {"cause": cause, "error": error}}
        if sweep:
            cleanup = await sweep_leftovers(self._dsn, repo, data_dir=self.service.data_dir, clock=self._clock)
            details = {**details, "cleanup": cleanup} if cleanup is not None else details
        try:
            values = await pull_v1(
                self.service,
                repo,
                base_url=self.base_url,
                token=self._feed_token,
                transport=self.transport,
                keep_batches=self.limits.keep_batches,
                min_free_bytes=self.limits.min_free_bytes,
            )
        except Exception as exc:
            text = pull_error(exc, DEADLINE_SECONDS)
            if text is None:
                raise
            return await self._count_failed(repo, V1, error=text, **details)
        count = await repo.record_mirror_failure(reason=FALLBACK_V1, t=self._clock())
        return RunOutcome(SUCCESS, FALLBACK_V1, reason=FALLBACK_V1, consecutive_failures=count, details=details, **values)

    async def _drop_version(self, conn, version: MirrorVersion | None, error: str) -> None:
        """mark_failed, never raising: a DROP a reader holds up, or a connection past use, is left to the next run's cleanup."""
        if version is None:
            return
        try:
            await mark_failed(conn, version.id, error=error, clock=self._clock)
        except Exception:
            logger.warning("[pick-mirror] version %s was not dropped now; the next run's cleanup will", version.id, exc_info=True)

    async def _discard(self, conn, repo: PickRepository, version: MirrorVersion | None, staged: tuple) -> None:
        """A cancelled or broken attempt: its version failed and dropped, its staged batches failed; never raises."""
        await self._drop_version(conn, version, "同步被中止或内部错误，本次版本作废")
        try:
            if staged:
                await discard_staged(self.service, repo, staged)
        except Exception:
            logger.warning("[pick-mirror] staged batches were not failed now; the next run's cleanup will", exc_info=True)

    # ---- after publishing (plan 5.2 steps 10-11)

    async def _after(self, conn, repo: PickRepository, client: FeedClient, outcome: RunOutcome) -> RunOutcome:
        begun = self._timer()
        try:
            retention = await self._retain(conn, repo)
        except Exception as exc:
            # The run has published (or decided not to): a retention problem is reported, not made the run's failure.
            logger.warning("[pick-mirror] retention after publishing failed: %s", safe_error(exc))
            retention = {"error": safe_error(exc)}
        outcome = outcome.with_details(retention_after=retention).with_stages(retention_after_ms=ms(self._timer() - begun))
        if outcome.manifest is None:
            return outcome
        begun = self._timer()
        folded = await fold_series(conn, manifest=outcome.manifest, client=client, clock=self._clock)
        return outcome.with_details(series=folded.details()).with_stages(series_ms=ms(self._timer() - begun))
