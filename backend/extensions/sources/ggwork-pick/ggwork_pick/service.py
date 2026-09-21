"""Use the host database lifecycle while owning a separate migration chain."""

from __future__ import annotations

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker


def _upgrade(connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


class PickService:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.run_evidence_reader = None
        self.session_factory: async_sessionmaker | None = None

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
        # The host owns and disposes the engine.
        self.session_factory = None
