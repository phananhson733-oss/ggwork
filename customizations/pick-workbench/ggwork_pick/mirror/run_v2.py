"""The mirror half of a run (plan 5.2 steps 6-8; P2-5c): feed v2 into the building version, then G3-G9.

Page by page, one page in memory (plan 5.2 step 6): the contract check (contracts.parse_page: strict models, NUL and lone
surrogates, forbidden names) and G5's scan run together in a worker thread, the rows are converted and COPYed on the
dedicated connection (writer.copy_rows converts in a thread too); then the meta rows, the keys and ANALYZE (step 7), and
the gates on the same connection (step 8). Every statement carries its own timeout and commits on its own.

The half has its own deadline, mirror_seconds after as_of was chosen (plan 5.1: 750 of the run's 900 s, the rest kept
for a degraded publish). It is enforced twice: asyncio.timeout around the whole half (a statement or a request that
hangs is cancelled, and asyncpg cancels it on the server), and a check of the injected timer between pages and steps,
which is also how a test's fake clock reaches it. Either way the half ends in MirrorDeadline.
"""

import asyncio
from collections.abc import Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass

from ggwork_pick.mirror.client import FeedClient
from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, parse_page
from ggwork_pick.mirror.feed_shape import Manifest
from ggwork_pick.mirror.gates import MirrorGates, MirrorTextScan, V1Scan, run_mirror_gates, scan_mirror_meta, scan_mirror_page
from ggwork_pick.mirror.run_result import ms
from ggwork_pick.mirror.versions import MirrorBuildError, MirrorVersion
from ggwork_pick.mirror.writer import copy_rows, finalize_version, write_meta


class MirrorDeadline(TimeoutError):
    """The mirror half's own deadline passed before it was done (plan 5.1, 5.5): the version fails, the agent batches go alone."""


@dataclass(frozen=True)
class Budget:
    """One attempt's clock from the moment its as_of was chosen (plan 5.1): seconds for the run and for the mirror half."""

    timer: Callable[[], float]
    started: float
    run_seconds: float
    mirror_seconds: float

    def run_left(self) -> float:
        return max(self.run_seconds - (self.timer() - self.started), 0.0)

    def mirror_left(self) -> float:
        return max(self.mirror_seconds - (self.timer() - self.started), 0.0)

    def check_mirror(self) -> None:
        if self.mirror_left() <= 0:
            raise MirrorDeadline(f"镜像阶段超过截止时刻（选定 as_of 之后 {self.mirror_seconds:g} 秒）")


@dataclass(frozen=True)
class Built:
    """A version written, finalized and gated: the gates' verdicts, G5's scan, rows copied, read retries and step times."""

    gates: MirrorGates
    text: MirrorTextScan
    copied: Mapping[str, int]
    retries: int
    stages: Mapping[str, int]


@dataclass(frozen=True)
class _Limits:
    copy_timeout: float
    statement_timeout: float


def _checked(text: MirrorTextScan, resource: str, body: Mapping) -> tuple[tuple, MirrorTextScan]:
    """One page's contract check and G5 scan: CPU work for a worker thread (brief 0.3)."""
    models = parse_page(resource, body)
    return models, scan_mirror_page(text, resource, body["rows"])


async def _copy_resource(conn, client: FeedClient, manifest: Manifest, schema: str, resource: str, text: MirrorTextScan, budget: Budget, limits: _Limits):
    copied, retries = 0, 0
    async with aclosing(client.pages(resource, manifest=manifest)) as pages:
        async for page in pages:
            budget.check_mirror()
            models, text = await asyncio.to_thread(_checked, text, resource, page.body)
            copied += await copy_rows(conn, schema, resource, models, timeout=limits.copy_timeout)
            retries += page.metrics.attempt - 1
    return text, copied, retries


async def _write(conn, client: FeedClient, manifest: Manifest, version: MirrorVersion, budget: Budget, limits: _Limits):
    """Step 6: manifest.meta and every page scanned, checked and copied; the meta rows last."""
    text = await asyncio.to_thread(scan_mirror_meta, MirrorTextScan(), manifest.meta)
    copied, retries = {}, 0
    for resource in COUNTED_RESOURCES:
        text, rows, retried = await _copy_resource(conn, client, manifest, version.schema_name, resource, text, budget, limits)
        copied, retries = {**copied, resource: rows}, retries + retried
    await write_meta(conn, version.schema_name, manifest.row, timeout=limits.copy_timeout)
    budget.check_mirror()
    return text, copied, retries


async def _build(conn, client: FeedClient, manifest: Manifest, version: MirrorVersion, v1: V1Scan, budget: Budget, limits: _Limits, timer) -> Built:
    begun = timer()
    text, copied, retries = await _write(conn, client, manifest, version, budget, limits)
    written = timer()
    await finalize_version(conn, version.schema_name, timeout=limits.statement_timeout)
    budget.check_mirror()
    finalized = timer()
    gates = await run_mirror_gates(conn, schema_name=version.schema_name, manifest=manifest.row, v1=v1, text=text, timeout=limits.statement_timeout)
    budget.check_mirror()
    if conn.is_in_transaction():
        # publish_mirror_pair's GRANT would wait on whatever is uncommitted here (brief 0.3). Rolled back first: the
        # degraded path fails and drops the version on this connection, and the lock's release clears lock_holder here.
        await conn.execute("ROLLBACK", timeout=limits.statement_timeout)
        raise MirrorBuildError("镜像专用连接上还有未结束的事务，不能配对发布")
    stages = {"v2_ms": ms(written - begun), "finalize_ms": ms(finalized - written), "gates_ms": ms(timer() - finalized)}
    return Built(gates=gates, text=text, copied=copied, retries=retries, stages=stages)


async def build_mirror(
    conn,
    client: FeedClient,
    manifest: Manifest,
    version: MirrorVersion,
    v1: V1Scan,
    *,
    budget: Budget,
    copy_timeout: float,
    statement_timeout: float,
    timer: Callable[[], float],
) -> Built:
    """Steps 6-8 into `version` within the mirror deadline. Raises on any mirror-side failure (the caller degrades):
    DriftError, a feed or contract error, MirrorBuildError / MirrorRecordError, GateError, MirrorDeadline, or a statement's
    own TimeoutError. Gates that ran and failed are not an exception: Built.gates says so."""
    limits = _Limits(copy_timeout=copy_timeout, statement_timeout=statement_timeout)
    guard = asyncio.timeout(budget.mirror_left())
    try:
        async with guard:
            return await _build(conn, client, manifest, version, v1, budget, limits, timer)
    except TimeoutError as exc:
        if guard.expired() and not isinstance(exc, MirrorDeadline):
            raise MirrorDeadline(f"镜像阶段超过截止时刻（选定 as_of 之后 {budget.mirror_seconds:g} 秒）") from None
        raise
