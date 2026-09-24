"""Move a test database along the private ggwp migration chain, one transaction per move like the gateway's startup upgrade."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory


def config(connection=None) -> Config:
    import ggwork_pick

    config = Config()
    config.set_main_option("script_location", str(Path(ggwork_pick.__file__).parent / "migrations"))
    if connection is not None:
        config.attributes["connection"] = connection
    return config


def head() -> str:
    return ScriptDirectory.from_config(config()).get_current_head()


async def upgrade(engine, revision: str = "head") -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: command.upgrade(config(sync), revision))


async def downgrade(engine, revision: str) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: command.downgrade(config(sync), revision))
