"""Engines that behave like the host's engine does (deerflow.persistence.engine).

In production the extension writes through the host's engine, whose serializer keeps non-ASCII text verbatim
(ensure_ascii=False). SQLAlchemy's default escapes it, which also turns a lone surrogate into a harmless
"\\ud800" escape: a test engine with the default stores text that production cannot. The conftest guard fails
any statement sent through an engine that serializes differently.

The host also configures every SQLite connection (WAL, foreign keys, a 30s busy timeout). SQLite's own defaults
are a different database: under a rollback journal any open reader blocks a writer's COMMIT, so a SELECT cursor
abandoned by a cancelled task fails the next commit with "database is locked" once the 5s default timeout runs
out. tests/test_engines.py compares these settings with the host engine's own connections.
"""

from deerflow.persistence.engine import _json_serializer as HOST_JSON_SERIALIZER
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine

# The host sets these in a listener local to init_engine, so they are repeated here.
HOST_SQLITE_PRAGMAS = ("journal_mode=WAL", "synchronous=NORMAL", "foreign_keys=ON", "busy_timeout=30000")


def _host_sqlite_settings(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    try:
        for pragma in HOST_SQLITE_PRAGMAS:
            cursor.execute(f"PRAGMA {pragma};")
    finally:
        cursor.close()


def host_engine(url: str, **kwargs):
    engine = create_async_engine(url, json_serializer=HOST_JSON_SERIALIZER, **kwargs)
    if engine.dialect.name == "sqlite":
        event.listen(engine.sync_engine, "connect", _host_sqlite_settings)
    return engine
