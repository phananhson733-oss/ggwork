"""Gateway lifecycle and narrow device credential authenticator."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text


def _upgrade(connection):
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    config.attributes["connection"] = connection
    command.upgrade(config, "head")


class EditingService:
    def __init__(self, *, hook_available=False):
        self.session_factory = None
        self.hook_available = hook_available
        self.execution_profiles = None

    async def profile_allowed(self, owner, profile):
        """Live owner policy injected by the configured planner; failures close admission."""
        if not self.hook_available or profile not in ("highlight", "hook"):
            return False
        if self.execution_profiles is None:
            return True
        try:
            profiles = await self.execution_profiles(owner)
        except Exception:
            return False
        return isinstance(profiles, (set, frozenset)) and profile in profiles

    async def initialize(self, session_factory):
        engine = session_factory.kw.get("bind")
        if engine is None:
            raise RuntimeError("Editing requires persistent host database")
        async with engine.begin() as connection:
            if engine.dialect.name == "sqlite":
                await connection.execute(text("BEGIN IMMEDIATE"))
            elif engine.dialect.name == "postgresql":
                await connection.execute(text("SELECT pg_advisory_xact_lock(7164798032461)"))
            else:
                raise RuntimeError("Editing supports SQLite and PostgreSQL")
            await connection.run_sync(_upgrade)
        self.session_factory = session_factory

    async def start(self, deps):
        if deps.session_factory is None:
            raise RuntimeError("Editing requires persistent host database")
        await self.initialize(deps.session_factory)

    async def stop(self):
        self.session_factory = None

    def repository(self, owner):
        from ggwork_edit.repository import EditingRepository

        if self.session_factory is None:
            raise RuntimeError("Editing service unavailable")
        return EditingRepository(self, owner)

    async def authenticate(self, token):
        from ggwork_edit.repository import authenticate

        return await authenticate(self, token)
