"""Use the host database lifecycle while owning a separate migration chain."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.repository import PickRepository
from ggwork_pick.schedule import CATCH_UP_DELAY_SECONDS, guarded_pull, run_schedule

STOP_GRACE_SECONDS = 20
# The mirror run (plan 5.1; U31, U39): on only when PICK_MIRROR_ENABLED is exactly "1", the host database is PostgreSQL
# and the export token is set. Switched on without the other two, the run stays v1 and its details_json says why.
MIRROR_FLAG_ENV = "PICK_MIRROR_ENABLED"
EXPORT_TOKEN_ENV = "PICK_REALSHORT_EXPORT_TOKEN"
DB_SIZE_CAP_ENV = "PICK_DB_SIZE_CAP_BYTES"
NOT_POSTGRESQL = "not_postgresql"
MISSING_EXPORT_TOKEN = "missing_export_token"
_BYTES = re.compile(r"[0-9]{1,15}")

logger = logging.getLogger(__name__)


def _upgrade(connection) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


def _size_cap(raw: str | None) -> int | None:
    """PICK_DB_SIZE_CAP_BYTES as a positive whole number of bytes; None (the mirror's default) when unset or unreadable."""
    if raw is None or not raw.strip():
        return None
    if not _BYTES.fullmatch(raw.strip()) or int(raw.strip()) == 0:
        logger.warning("[pick-mirror] %s is not a positive whole number of bytes; the default cap applies", DB_SIZE_CAP_ENV)
        return None
    return int(raw.strip())


@dataclass(frozen=True)
class SyncSettings:
    """Server-side only. feed_token reads v1 and export_token feed v2 (the mirror); the schedule runs in this process."""

    feed_url: str = ""
    feed_token: str = field(default="", repr=False)
    export_token: str = field(default="", repr=False)
    mirror_flag: str = ""
    db_size_cap: int | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SyncSettings:
        env = os.environ if environ is None else environ
        return cls(
            feed_url=env.get("PICK_REALSHORT_FEED_URL", "").strip(),
            feed_token=env.get("PICK_REALSHORT_FEED_TOKEN", "").strip(),
            export_token=env.get(EXPORT_TOKEN_ENV, "").strip(),
            # Exactly "1" (U39): not stripped, so " 1" or "true" leave the mirror off.
            mirror_flag=env.get(MIRROR_FLAG_ENV, ""),
            db_size_cap=_size_cap(env.get(DB_SIZE_CAP_ENV)),
        )

    @property
    def configured(self) -> bool:
        return bool(self.feed_url and self.feed_token)

    def mirror_disabled(self, dialect: str | None) -> str | None:
        """Why the mirror does not run although the switch is exactly "1" (U31); None when it runs or is switched off."""
        if self.mirror_flag != "1":
            return None
        if dialect != "postgresql":
            return NOT_POSTGRESQL
        if not self.export_token:
            return MISSING_EXPORT_TOKEN
        return None

    def mirror_on(self, dialect: str | None) -> bool:
        return self.mirror_flag == "1" and self.mirror_disabled(dialect) is None


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

    def _engine(self):
        return self.session_factory.kw.get("bind") if self.session_factory is not None else None

    def mirror_enabled(self) -> bool:
        """Whether realshort_sync() hands out the mirror run: configured, and switched on where it can run (U31)."""
        engine = self._engine()
        return self.sync_settings.configured and self.sync_settings.mirror_on(engine.dialect.name if engine is not None else None)

    def realshort_sync(self):
        """The run the schedule and the manual button start: MirrorSync when the mirror is on, else the v1 RealShortSync."""
        settings = self.sync_settings
        if not settings.configured:
            return None
        engine = self._engine()
        dialect = engine.dialect.name if engine is not None else None
        if settings.mirror_on(dialect):
            return self._mirror_sync(engine)
        from ggwork_pick.sync import RealShortSync

        disabled = settings.mirror_disabled(dialect)
        details = {"mirror_disabled": disabled} if disabled else None
        sweep = self._leftover_sweep(engine) if dialect == "postgresql" else None
        return RealShortSync(self, base_url=settings.feed_url, token=settings.feed_token, transport=self.sync_transport, details=details, sweep=sweep)

    def _leftover_sweep(self, engine):
        """What a dead mirror run staged, cleaned before a v1 pull on PostgreSQL (the switch rolled back, U31; review
        flow-2). A closure, so no repr ever shows the DSN and its password."""
        from ggwork_pick.mirror.connection import dsn_from_engine
        from ggwork_pick.mirror.run import sweep_leftovers

        dsn = dsn_from_engine(engine)

        async def sweep(repo: PickRepository):
            return await sweep_leftovers(dsn, repo, data_dir=self.data_dir)

        return sweep

    def _mirror_sync(self, engine):
        # Imported here: the mirror's contracts and scrub rules load only where the mirror runs (PostgreSQL).
        from ggwork_pick.mirror.connection import dsn_from_engine
        from ggwork_pick.mirror.run import MirrorLimits, MirrorSync

        settings = self.sync_settings
        limits = MirrorLimits() if settings.db_size_cap is None else MirrorLimits(db_size_cap=settings.db_size_cap)
        return MirrorSync(
            self,
            base_url=settings.feed_url,
            export_token=settings.export_token,
            feed_token=settings.feed_token,
            dsn=dsn_from_engine(engine),
            transport=self.sync_transport,
            limits=limits,
        )

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
