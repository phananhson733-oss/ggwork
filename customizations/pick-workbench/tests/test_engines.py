"""The SQLite half of the suite runs on the connection settings the host's engine gives production."""

from unittest.mock import AsyncMock

import pytest
from engines import host_engine

SETTINGS = ("journal_mode", "synchronous", "foreign_keys", "busy_timeout")


async def _settings(engine) -> dict:
    async with engine.connect() as conn:
        return {name: (await conn.exec_driver_sql(f"PRAGMA {name}")).scalar_one() for name in SETTINGS}


@pytest.mark.asyncio
async def test_sqlite_connections_are_configured_like_the_host_engine(tmp_path, monkeypatch):
    from deerflow.persistence import bootstrap
    from deerflow.persistence import engine as host

    # Only the connections matter here; the host's own tables (and Alembic's private engine) stay out of it.
    monkeypatch.setattr(bootstrap, "bootstrap_schema", AsyncMock())
    await host.init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'host.db'}", sqlite_dir=str(tmp_path))
    try:
        expected = await _settings(host.get_engine())
    finally:
        await host.close_engine()
    # Readers never block a writer's commit in WAL; a rollback journal makes them wait out busy_timeout.
    assert expected["journal_mode"] == "wal"
    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}")
    try:
        assert await _settings(engine) == expected
    finally:
        await engine.dispose()
