"""Pull the RealShort read-only feed into one shared, immutable catalog batch.

The feed pages are separate keyset SELECTs on a live source (ReelShort rows enter the pool
on click/order signals), so a few rows can appear or leave while paging. The cursor chain
proves no page was skipped; the first page's total bounds the drift. Small drift is recorded
on the batch, larger drift discards the pull and the previous batch stays current.
Nothing here writes to RealShort.
"""

import asyncio
import json
import re

import httpx

from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository

FEED_VERSION = "pick-feed-v1"
SOURCE = "realshort"
PAGE_LIMIT = 1000
MAX_PAGES = 40
KEEP_BATCHES = 3
DRIFT_MIN = 20
DRIFT_RATIO = 0.01
RULES_SOURCE_REF = "realshort:/api/pick-feed#rules"
_CAPTURE_LINE = re.compile(r"^采集时间：.*$", re.MULTILINE)


class FeedError(Exception):
    """A feed problem safe to show operators: never carries the token or response bodies."""


def _page_error(response: httpx.Response) -> FeedError:
    return FeedError(f"RealShort feed 返回 HTTP {response.status_code}")


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
    drift = len(rows) - first["total"]
    if abs(drift) > max(DRIFT_MIN, int(first["total"] * DRIFT_RATIO)):
        raise FeedError(f"RealShort feed 条数不一致：首页总数 {first['total']}，实际 {len(rows)}；本次不发布")
    meta = {key: first.get(key) for key in ("capturedAt", "scope", "freshness", "sourceRevision", "rules", "total")}
    return {**meta, "drift": drift}, rows


class RealShortSync:
    def __init__(self, service, *, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None, keep_batches: int = KEEP_BATCHES):
        self.service = service
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.transport = transport
        self.keep_batches = keep_batches

    @property
    def _lock(self) -> asyncio.Lock:
        # One lock per service so the cron call and the manual button share it.
        return self.service.sync_lock

    async def run(self, trigger: str) -> dict:
        if self.service.session_factory is None:
            return {"status": "failed", "error": "选剧服务尚未就绪"}
        if self._lock.locked():
            return {"status": "already_running"}
        async with self._lock:
            repo = PickRepository.shared(self.service.session_factory)
            record = await repo.start_sync_run(SOURCE, trigger)
            try:
                values = await self._pull(repo)
            except (FeedError, ValueError) as exc:
                return await repo.finish_sync_run(record["id"], status="failed", error=str(exc)[:500])
            except httpx.HTTPError as exc:
                return await repo.finish_sync_run(record["id"], status="failed", error=f"RealShort feed 连接失败：{type(exc).__name__}")
            except Exception as exc:  # noqa: BLE001 - recorded, never swallowed silently
                await repo.finish_sync_run(record["id"], status="failed", error=f"同步内部错误：{type(exc).__name__}")
                raise
            return await repo.finish_sync_run(record["id"], status="success", **values)

    async def _pull(self, repo: PickRepository) -> dict:
        async with httpx.AsyncClient(base_url=self.base_url, transport=self.transport, timeout=httpx.Timeout(60.0, connect=10.0)) as client:
            meta, rows = await fetch_feed(client, self.token)
        importer = Importer(repo, self.service.data_dir)
        as_of = meta["capturedAt"] if isinstance(meta.get("capturedAt"), str) else None
        batch_meta = {
            "source": SOURCE,
            "scope": meta.get("scope"),
            "freshness": meta.get("freshness"),
            "source_revision": meta.get("sourceRevision"),
            "source_total": meta.get("total"),
            "paging_drift": meta.get("drift"),
        }
        catalog = await importer.catalog(json.dumps(rows, ensure_ascii=False).encode(), "json", source_as_of=as_of, meta=batch_meta)
        knowledge = None
        if isinstance(meta.get("rules"), str) and meta["rules"].strip():
            # The capture timestamp lives on the batch; dropping it here lets unchanged rules dedupe.
            rules = _CAPTURE_LINE.sub("", meta["rules"]).encode()
            knowledge = await importer.knowledge_bundle([(rules, "realshort-rules.md", RULES_SOURCE_REF)], source_as_of=as_of, meta=batch_meta)
        await repo.prune_shared("catalog", self.keep_batches)
        await repo.prune_shared("knowledge", self.keep_batches)
        return dict(
            rows=len(rows),
            catalog_batch_id=catalog["id"],
            knowledge_batch_id=knowledge["id"] if knowledge else None,
            source_as_of=catalog.get("source_as_of") or as_of,
        )
