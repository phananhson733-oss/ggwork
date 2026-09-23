"""Use the host database lifecycle while owning a separate migration chain."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker


def _upgrade(connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


@dataclass(frozen=True)
class SyncSettings:
    """Server-side only. feed_token reads RealShort; trigger_token lets the internal cron call start a pull."""

    feed_url: str = ""
    feed_token: str = ""
    trigger_token: str = ""

    @classmethod
    def from_env(cls) -> SyncSettings:
        return cls(
            feed_url=os.environ.get("PICK_REALSHORT_FEED_URL", "").strip(),
            feed_token=os.environ.get("PICK_REALSHORT_FEED_TOKEN", "").strip(),
            trigger_token=os.environ.get("PICK_SYNC_TOKEN", "").strip(),
        )

    @property
    def configured(self) -> bool:
        return bool(self.feed_url and self.feed_token)


class PickService:
    def __init__(self, data_dir: Path, sync_settings: SyncSettings | None = None):
        self.data_dir = data_dir
        self.run_evidence_reader = None
        self.session_factory: async_sessionmaker | None = None
        self.sync_settings = sync_settings or SyncSettings()
        self.sync_transport = None
        self.sync_lock = asyncio.Lock()
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

    async def wait_background(self) -> None:
        if self._background:
            await asyncio.gather(*list(self._background), return_exceptions=True)

    async def initialize(self, session_factory: async_sessionmaker) -> None:
        engine = session_factory.kw.get("bind")
        if engine is None:
            raise RuntimeError("选剧需要持久化数据库")
        self.session_factory = None
        async with engine.begin() as connection:
            await connection.run_sync(_upgrade)
        await asyncio.to_thread(self.data_dir.mkdir, parents=True, exist_ok=True, mode=0o700)
        self.session_factory = session_factory

    async def start(self, deps) -> None:
        if deps.session_factory is None:
            raise RuntimeError("选剧需要持久化数据库，不能使用 memory backend")
        self.run_evidence_reader = deps.run_evidence_reader
        await self.initialize(deps.session_factory)

    async def stop(self) -> None:
        # Let an in-flight pull record its outcome before the host disposes the engine.
        await self.wait_background()
        self.session_factory = None
