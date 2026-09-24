"""Pull the RealShort read-only feed into one shared, immutable catalog batch.

The feed pages are separate keyset SELECTs on a live source (ReelShort rows enter the pool
on click/order signals), so a few rows can appear or leave while paging. The cursor chain
proves no page was skipped; the first page's total bounds the drift. Small drift is recorded
on the batch, larger drift discards the pull and the previous batch stays current.
Nothing here writes to RealShort.

The mirror run (mirror/run.py) shares the v1 half: pull_v1 is RealShortSync's whole pull and the mirror's fallback when
the manifest cannot be read (plan 5.5); feed_meta checks a pull pinned to an as_of the same way (U19); import_v1 stages
the two batches (P2-5a), all or nothing.
"""

import asyncio
import json
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path

import httpx
from pydantic import ValidationError

from ggwork_pick.feed_url import HTTPS_REQUIRED, secure_enough
from ggwork_pick.imports import Importer, decode_payload
from ggwork_pick.repository import PickRepository

FEED_VERSION = "pick-feed-v1"
SOURCE = "realshort"
PAGE_LIMIT = 1000
MAX_PAGES = 40
KEEP_BATCHES = 3
DRIFT_MIN = 20
DRIFT_RATIO = 0.01
# The whole download, not one read: a feed that trickles bytes must not hold the sync lock forever.
DEADLINE_SECONDS = 600
# The pick tables share the host database's volume; a full disk would stop the whole gateway.
MIN_FREE_BYTES = 500 * 2**20
RULES_SOURCE_REF = "realshort:/api/pick-feed#rules"
_CAPTURE_LINE = re.compile(r"^采集时间：.*$", re.MULTILINE)


class FeedError(Exception):
    """A feed problem safe to show operators: never carries the token or response bodies."""


def _page_error(response: httpx.Response) -> FeedError:
    return FeedError(f"RealShort feed 返回 HTTP {response.status_code}")


def _safe_error(exc: Exception) -> str:
    """Run errors are shown to every signed-in user; a validation error names the field, never the value."""
    if isinstance(exc, ValidationError):
        first = exc.errors(include_input=False, include_url=False)[0]
        where = ".".join(str(part) for part in first["loc"])
        return f"RealShort feed 数据不符合约定：{where}（{first['type']}），共 {exc.error_count()} 处"
    return str(exc)[:500]


async def fetch_feed(client: httpx.AsyncClient, token: str) -> tuple[dict, list[dict]]:
    headers = {"authorization": f"Bearer {token}"}
    cursor, first, rows = "", None, []
    for _ in range(MAX_PAGES):
        params = {"limit": str(PAGE_LIMIT), **({"cursor": cursor} if cursor else {})}
        response = await client.get("/api/pick-feed", params=params, headers=headers)
        if response.status_code != 200:
            raise _page_error(response)
        try:
            page = response.json()
        except ValueError:
            raise FeedError("RealShort feed 返回的不是 JSON") from None
        if not isinstance(page, dict) or page.get("ok") is not True or page.get("version") != FEED_VERSION:
            raise FeedError("RealShort feed 版本或格式不符")
        if not isinstance(page.get("rows"), list):
            raise FeedError("RealShort feed 缺少 rows")
        if first is None:
            if not isinstance(page.get("total"), int):
                raise FeedError("RealShort feed 首页缺少总数")
            first = page
        rows.extend(page["rows"])
        cursor = page.get("nextCursor")
        if not cursor:
            break
        if not isinstance(cursor, str):
            raise FeedError("RealShort feed 游标无效")
    else:
        raise FeedError("RealShort feed 页数超过上限")
    return feed_meta(first, rows), rows


def feed_meta(first: Mapping, rows: list) -> dict:
    """The batch's feed metadata from the first page, once the paging drift is within bounds (a pinned pull keeps it, U19)."""
    drift = len(rows) - first["total"]
    if abs(drift) > max(DRIFT_MIN, int(first["total"] * DRIFT_RATIO)):
        raise FeedError(f"RealShort feed 条数不一致：首页总数 {first['total']}，实际 {len(rows)}；本次不发布")
    meta = {key: first.get(key) for key in ("capturedAt", "scope", "freshness", "sourceRevision", "rules", "total")}
    return {**meta, "drift": drift}


def rules_payload(meta: dict) -> bytes | None:
    """Validated before the catalog is published, so bad rules cannot leave a half-updated pair."""
    rules = meta.get("rules")
    if not isinstance(rules, str) or not rules.strip():
        return None
    # The capture timestamp lives on the batch; dropping it here lets unchanged rules dedupe.
    payload = _CAPTURE_LINE.sub("", rules).encode()
    decode_payload(payload)
    return payload


def _encode_rows(rows: list[dict]) -> bytes:
    # A string cut mid-emoji upstream arrives as a lone surrogate; one bad character must not sink the batch.
    return json.dumps(rows, ensure_ascii=False).encode("utf-8", errors="replace")


def delete_blobs(data_dir: Path, paths: list[str]) -> None:
    root = data_dir.resolve()
    for raw in paths:
        path = Path(raw).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)


# Cleanups that must outlive a second cancellation, referenced here until they finish (as PickService.spawn does).
_CLEANUPS: set[asyncio.Task] = set()
CANCELLED_ERROR = "同步被中止（进程停止或取消）"


async def shielded(coroutine):
    """Await coroutine to its end even when the caller is cancelled meanwhile; a second cancel leaves it running."""
    task = asyncio.ensure_future(coroutine)
    _CLEANUPS.add(task)
    task.add_done_callback(_CLEANUPS.discard)
    return await asyncio.shield(task)


async def start_run(repo: PickRepository, trigger: str, *, cancelled: Callable[[str], Awaitable[object]]) -> dict:
    """start_sync_run that a cancel cannot cut in half: a cancel landing while the record is written waits for the
    write, closes the record through cancelled(run_id) and re-raises, so no run is left at running (the runner's own
    except CancelledError only covers what comes after the record exists)."""
    starting = asyncio.ensure_future(repo.start_sync_run(SOURCE, trigger))
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        record = await starting
        await shielded(cancelled(record["id"]))
        raise


def batch_meta(meta: dict) -> dict:
    return {
        "source": SOURCE,
        "scope": meta.get("scope"),
        "freshness": meta.get("freshness"),
        "source_revision": meta.get("sourceRevision"),
        "source_total": meta.get("total"),
        "paging_drift": meta.get("drift"),
    }


async def prune_batches(service, repo: PickRepository, keep: int) -> int:
    """Rows of aged-out shared batches go, then the blobs no live batch uses; returns how many blobs were deleted."""
    unused = [*await repo.prune_shared("catalog", keep), *await repo.prune_shared("knowledge", keep)]
    await asyncio.to_thread(delete_blobs, service.data_dir, unused)
    return len(unused)


def check_disk(data_dir: Path, min_free_bytes: int) -> None:
    free = shutil.disk_usage(data_dir).free
    if free < min_free_bytes:
        raise FeedError(f"磁盘剩余空间不足（剩 {free // 2**20} MB），本次不发布")


async def discard_staged(service, repo: PickRepository, batches) -> int:
    """This run's staged batches failed (fail_staged: only those still importing), then their unused blobs; returns the blob count."""
    paths = await repo.fail_staged([batch["id"] for batch in batches])
    await asyncio.to_thread(delete_blobs, service.data_dir, paths)
    return len(paths)


async def import_v1(service, repo: PickRepository, meta: dict, rows: list[dict], rules: bytes | None, *, stage: bool = False) -> tuple[dict, dict | None]:
    """The catalog batch, then the rules as the knowledge batch. Staged (stage=True), the pair is all or nothing: a
    catalog staged before the knowledge failed, or was cancelled, is failed on the way out."""
    importer = Importer(repo, service.data_dir)
    as_of = meta["capturedAt"] if isinstance(meta.get("capturedAt"), str) else None
    described = batch_meta(meta)
    payload = await asyncio.to_thread(_encode_rows, rows)
    catalog = await importer.catalog(payload, "json", source_as_of=as_of, meta=described, keep_original=False, stage=stage)
    if rules is None:
        return catalog, None
    try:
        knowledge = await importer.knowledge_bundle([(rules, "realshort-rules.md", RULES_SOURCE_REF)], source_as_of=as_of, meta=described, stage=stage)
    except BaseException:
        if stage:
            await shielded(discard_staged(service, repo, [catalog]))
        raise
    return catalog, knowledge


async def pull_v1(
    service,
    repo: PickRepository,
    *,
    base_url: str,
    token: str,
    transport: httpx.AsyncBaseTransport | None = None,
    stage: bool = False,
    keep_batches: int = KEEP_BATCHES,
    deadline_seconds: float = DEADLINE_SECONDS,
    min_free_bytes: int = MIN_FREE_BYTES,
) -> dict:
    """The whole v1 pull: fetch, prune, disk check, Importer, prune; the run record's values. Raises what pull_error names."""
    if not secure_enough(base_url):
        raise FeedError(f"RealShort feed 地址{HTTPS_REQUIRED}")
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), transport=transport, timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        async with asyncio.timeout(deadline_seconds):
            meta, rows = await fetch_feed(client, token)
    rules = rules_payload(meta)
    # Reclaim what has aged out before the space check; otherwise a full disk could never recover.
    await prune_batches(service, repo, keep_batches)
    await asyncio.to_thread(check_disk, service.data_dir, min_free_bytes)
    catalog, knowledge = await import_v1(service, repo, meta, rows, rules, stage=stage)
    await prune_batches(service, repo, keep_batches)
    as_of = meta["capturedAt"] if isinstance(meta.get("capturedAt"), str) else None
    return dict(
        rows=len(rows),
        catalog_batch_id=catalog["id"],
        knowledge_batch_id=knowledge["id"] if knowledge else None,
        source_as_of=catalog.get("source_as_of") or as_of,
    )


def pull_error(exc: Exception, deadline_seconds: float) -> str | None:
    """The run's error text for an expected failure of pull_v1 (shown to every signed-in user); None for a bug."""
    if isinstance(exc, TimeoutError):
        return f"RealShort feed 超时（超过 {deadline_seconds:g} 秒）"
    if isinstance(exc, FeedError | ValueError):
        return _safe_error(exc)
    if isinstance(exc, httpx.HTTPError):
        return f"RealShort feed 连接失败：{type(exc).__name__}"
    return None


def _extra(details: dict) -> dict:
    """finish_sync_run's details_json keyword, left out when there is nothing to say (a v1 run's is usually None)."""
    return {"details_json": details} if details else {}


class RealShortSync:
    def __init__(
        self,
        service,
        *,
        base_url: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
        keep_batches: int = KEEP_BATCHES,
        deadline_seconds: float = DEADLINE_SECONDS,
        min_free_bytes: int = MIN_FREE_BYTES,
        details: Mapping | None = None,
        sweep: Callable[[PickRepository], Awaitable[dict | None]] | None = None,
    ):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.keep_batches = keep_batches
        self.deadline_seconds = deadline_seconds
        self.min_free_bytes = min_free_bytes
        # details_json of every run, e.g. why the mirror is not running although switched on (U31); None: none.
        self.details = dict(details) if details else None
        # PostgreSQL only (PickService.realshort_sync): what a dead mirror run staged is cleaned before the pull, under
        # the mirror lock taken as cleanup without waiting (mirror.run.sweep_leftovers; review flow-2). It never raises
        # but on cancellation; what it reports (None: nothing) goes into details_json.cleanup.
        self.sweep = sweep

    @property
    def _lock(self) -> asyncio.Lock:
        # One lock per service so the schedule and the manual button share it.
        return self.service.sync_lock

    async def run(self, trigger: str) -> dict:
        if self.service.session_factory is None:
            return {"status": "failed", "error": "选剧服务尚未就绪"}
        if self._lock.locked():
            return {"status": "already_running"}
        async with self._lock:
            repo = PickRepository.shared(self.service.session_factory)
            details = dict(self.details or {})
            record = await start_run(
                repo, trigger, cancelled=lambda run_id: repo.finish_sync_run(run_id, status="failed", error=CANCELLED_ERROR, **_extra(details))
            )
            try:
                cleanup = await self.sweep(repo) if self.sweep is not None else None
                details = {**details, "cleanup": cleanup} if cleanup is not None else details
                values = await self._pull(repo)
            except asyncio.CancelledError:
                await asyncio.shield(repo.finish_sync_run(record["id"], status="failed", error=CANCELLED_ERROR, **_extra(details)))
                raise
            except Exception as exc:
                error = pull_error(exc, self.deadline_seconds)
                if error is None:
                    # Recorded, never swallowed silently.
                    await repo.finish_sync_run(record["id"], status="failed", error=f"同步内部错误：{type(exc).__name__}", **_extra(details))
                    raise
                return await repo.finish_sync_run(record["id"], status="failed", error=error, **_extra(details))
            return await repo.finish_sync_run(record["id"], status="success", **values, **_extra(details))

    async def _pull(self, repo: PickRepository) -> dict:
        return await pull_v1(
            self.service,
            repo,
            base_url=self.base_url,
            token=self.token,
            transport=self.transport,
            keep_batches=self.keep_batches,
            deadline_seconds=self.deadline_seconds,
            min_free_bytes=self.min_free_bytes,
        )
