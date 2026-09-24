"""The v1 half of a mirror run (plan 5.2 step 5; P2-5c): v1 pinned to the manifest, G2, then the two batches staged.

FeedClient.v1_pages sends the manifest's as_of and fp with every page and checks each page's capturedAt, fingerprint and
sourceRevision against the manifest (a mismatch, a 409 or a source_busy is DriftError). Each raw page goes through G2's
scanner in a worker thread before anything else reads it, so G2 sees the JSON exactly as RealShort sent it (brief 0.3,
P2-4). The paging drift keeps v1's max(20, 1%) tolerance (U19), the rules Markdown must be there (U8), and the batches are
staged through sync.import_v1: importing, invisible to every reader until the run publishes or fails them.

v1 comes before v2 so that the agent batches never wait on the mirror: whatever the mirror half does later, they are
staged and can go out alone (plan 5.1, 5.5). The whole half runs within what is left of the run deadline (900 s).
"""

import asyncio
from collections.abc import Mapping
from contextlib import aclosing
from dataclasses import dataclass

from ggwork_pick.mirror.client import FeedClient
from ggwork_pick.mirror.errors import ContractError, DriftError, FeedError
from ggwork_pick.mirror.feed_shape import Manifest
from ggwork_pick.mirror.gates import GateResult, V1Scan, scanned_v1_page, v1_text_gate
from ggwork_pick.mirror.run_result import DRIFT, V1, safe_error
from ggwork_pick.repository import PickRepository
from ggwork_pick.sync import FeedError as V1FeedError
from ggwork_pick.sync import feed_meta, import_v1, rules_payload

G2_FAILED = "v1 文本闸门（G2）未通过：v1 行里有网盘片段或 NUL，或规则 Markdown 里有网盘片段；两边都不发布"
NO_RULES = "RealShort feed 首页没有规则 Markdown：镜像模式要求规则非空（U8）；两边都不发布"


@dataclass(frozen=True)
class V1Pull:
    """Every v1 page of one attempt: G2's scan, the rows as sent, the first page (total, rules, freshness) and read retries."""

    scan: V1Scan
    rows: tuple
    first: Mapping
    retries: int


@dataclass(frozen=True)
class V1Staged:
    """The catalog and knowledge batches staged (or reused, with their deferred values) for this attempt."""

    batches: tuple[dict, dict]
    scan: V1Scan
    gate: GateResult
    rows: int
    paging_drift: int
    retries: int


@dataclass(frozen=True)
class V1Failed:
    """The v1 half did not stage a pair: kind is DRIFT (the attempt may be repeated) or V1 (neither side publishes)."""

    kind: str
    error: str
    gate: GateResult | None = None
    scan: V1Scan | None = None


async def pull_pinned_v1(client: FeedClient, manifest: Manifest) -> V1Pull:
    """All v1 pages at the manifest's as_of and fp, each scanned for G2 off the event loop as it arrives."""
    scan, chunks, first, retries = V1Scan(), (), None, 0
    async with aclosing(client.v1_pages(manifest)) as pages:
        async for page in pages:
            scan = await scanned_v1_page(scan, page.body)
            chunks = (*chunks, tuple(page.rows))
            first = page.body if first is None else first
            retries += page.metrics.attempt - 1
    if first is None:
        raise ContractError("RealShort feed v1 没有返回任何页", resource="v1")
    return V1Pull(scan=scan, rows=tuple(row for chunk in chunks for row in chunk), first=first, retries=retries)


async def _stage(service, repo: PickRepository, pulled: V1Pull) -> V1Staged | V1Failed:
    gate = v1_text_gate(pulled.scan)
    if not gate.ok:
        return V1Failed(V1, G2_FAILED, gate=gate, scan=pulled.scan)
    rows = list(pulled.rows)
    meta = feed_meta(pulled.first, rows)
    rules = rules_payload(meta)
    if rules is None:
        return V1Failed(V1, NO_RULES, gate=gate, scan=pulled.scan)
    catalog, knowledge = await import_v1(service, repo, meta, rows, rules, stage=True)
    return V1Staged(batches=(catalog, knowledge), scan=pulled.scan, gate=gate, rows=len(rows), paging_drift=meta["drift"], retries=pulled.retries)


async def stage_v1(service, repo: PickRepository, client: FeedClient, manifest: Manifest, *, seconds: float) -> V1Staged | V1Failed:
    """Pull, G2 and stage within `seconds`. Drift is DRIFT; any other feed, contract, validation or time failure is V1.

    Anything else (a database error, a bug) is raised: the run records it as an internal error. A catalog staged before a
    failure is failed again by import_v1 on the way out, so a V1Failed never leaves a batch importing.
    """
    try:
        async with asyncio.timeout(seconds):
            return await _stage(service, repo, await pull_pinned_v1(client, manifest))
    except DriftError as exc:
        return V1Failed(DRIFT, safe_error(exc))
    except TimeoutError:
        return V1Failed(V1, f"v1 拉取与暂存超过总时限（剩 {seconds:g} 秒）")
    except (FeedError, V1FeedError, ValueError) as exc:
        return V1Failed(V1, safe_error(exc))
