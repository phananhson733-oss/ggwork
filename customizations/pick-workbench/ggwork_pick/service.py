"""Use the host database lifecycle while owning a separate migration chain."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.repository import PickRepository
from ggwork_pick.schedule import CATCH_UP_DELAY_SECONDS, guarded_pull, run_schedule

STOP_GRACE_SECONDS = 20


def _upgrade(connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@dataclass(frozen=True)
class SyncSettings:
    """Server-side only. feed_token reads RealShort; the schedule runs in this process, so nothing else is needed."""

    feed_url: str = ""
    feed_token: str = ""

    @classmethod
    def from_env(cls) -> SyncSettings:
        return cls(
            feed_url=os.environ.get("PICK_REALSHORT_FEED_URL", "").strip(),
            feed_token=os.environ.get("PICK_REALSHORT_FEED_TOKEN", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.feed_url and self.feed_token)


class PickService:
    def __init__(self, data_dir: Path, sync_settings: SyncSettings | None = None, *, catch_up_delay: float = CATCH_UP_DELAY_SECONDS):
        self.data_dir = data_dir
        self.run_evidence_reader = None
        self.session_factory: async_sessionmaker | None = None
        self.sync_settings = sync_settings or SyncSettings()
        self.sync_transport = None
        self.sync_lock = asyncio.Lock()
        self.scheduler: asyncio.Task | None = None
        self.catch_up_delay = catch_up_delay
        self._background: set[asyncio.Task] = set()

    def realshort_sync(self):
        if not self.sync_settings.configured:
            return None
        from ggwork_pick.sync import RealShortSync

        return RealShortSync(self, base_url=self.sync_settings.feed_url, token=self.sync_settings.feed_token, transport=self.sync_transport)

    def spawn(self, coroutine) -> None:
        # Keep a strong reference: a bare create_task can be garbage collected mid-run.
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def wait_background(self, timeout: float | None = None) -> None:
        """Let in-flight pulls record their outcome; past the grace period they are cancelled and record that."""
        await _drain(list(self._background), timeout)

    def _start_schedule(self) -> None:
        if not self.sync_settings.configured:
            return
        factory = self.session_factory
        self.scheduler = asyncio.create_task(
            run_schedule(
                runs=lambda: PickRepository.shared(factory).sync_runs(limit=20),
                pull=lambda: guarded_pull(self.realshort_sync),
                catch_up_delay=self.catch_up_delay,
            )
        )

    async def initialize(self, session_factory: async_sessionmaker) -> None:
        engine = session_factory.kw.get("bind")
        if engine is None:
            raise RuntimeError("选剧需要持久化数据库")
        self.session_factory = None
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade)
        await asyncio.to_thread(self.data_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
        # One gateway process: whatever a previous process left "running" can never finish.
        await PickRepository.shared(session_factory).close_interrupted_runs()
        self.session_factory = session_factory

    async def start(self, deps) -> None:
        if deps.session_factory is None:
            raise RuntimeError("选剧需要持久化数据库，不能使用 memory backend")
        self.run_evidence_reader = deps.run_evidence_reader
        await self.initialize(deps.session_factory)
        self._start_schedule()

    async def stop(self) -> None:
        tasks = list(self._background)
        if self.scheduler is not None:
            self.scheduler.cancel()
            tasks.append(self.scheduler)
        # Let an in-flight pull record its outcome before the host disposes the engine, but never hang a deploy.
        await _drain(tasks, STOP_GRACE_SECONDS)
        self.session_factory = None


async def _drain(tasks: list[asyncio.Task], timeout: float | None) -> None:
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=timeout)
    for task in pending:
        task.cancel()  # a second cancel also ends a task waiting on a shielded outcome write
    await asyncio.gather(*tasks, return_exceptions=True)
