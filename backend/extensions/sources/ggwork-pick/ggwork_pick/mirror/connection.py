"""The mirror's own PostgreSQL connection, outside the ORM pool (plan 3.1, 5.2 step 1; U33).

COPY, index builds, ANALYZE and the gate scans take minutes: the ORM pool's command_timeout of 30 seconds would cut them,
and holding a pooled connection that long would starve the chat. One bare asyncpg connection carries them instead, every
statement's timeout given by its caller. It names its own search_path: behind Supavisor the role default is only a
fallback. TLS follows PGSSLMODE, which asyncpg reads like libpq does (backend/app/gateway/pick_entrypoint.py), so the
DSN never carries ssl* parameters. Nothing here echoes a URL: it holds the password.
"""

import os
from collections.abc import Mapping

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

DATABASE_URL_ENV = "PICK_DATABASE_URL"
APPLICATION_NAME = "ggwp-mirror"
SEARCH_PATH = "deerflow"
_POSTGRES_BACKENDS = ("postgresql", "postgres")


class MirrorConnectionError(RuntimeError):
    """The dedicated connection could not be opened; the text names the error class and SQLSTATE, never the DSN."""


def dsn_from_url(url: str | URL) -> str:
    """A libpq-style DSN for asyncpg from a SQLAlchemy URL: the driver suffix dropped, the password kept."""
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError, TypeError):
        raise ValueError("数据库 URL 无法解析") from None
    if parsed.get_backend_name() not in _POSTGRES_BACKENDS:
        raise ValueError("选剧镜像只支持 PostgreSQL 数据库")
    return parsed.set(drivername="postgresql").render_as_string(hide_password=False)


def dsn_from_engine(engine) -> str:
    """The gateway's case: the host engine the extension's session factory is bound to (sync or async)."""
    return dsn_from_url(engine.url)


def dsn_from_env(environ: Mapping[str, str] | None = None) -> str:
    """The command-line case: PICK_DATABASE_URL, the variable the gateway entrypoint reads."""
    value = (os.environ if environ is None else environ).get(DATABASE_URL_ENV, "").strip()
    if not value:
        raise ValueError(f"缺少环境变量 {DATABASE_URL_ENV}")
    try:
        return dsn_from_url(value)
    except ValueError as exc:
        raise ValueError(f"{DATABASE_URL_ENV} 不可用：{exc}") from None


async def open_dedicated(dsn: str):
    """One asyncpg connection for the mirror; the caller closes it. Statements run without a default timeout."""
    import asyncpg  # the host's postgres extra; a SQLite install never gets here

    try:
        return await asyncpg.connect(dsn, server_settings={"search_path": SEARCH_PATH, "application_name": APPLICATION_NAME}, command_timeout=None)
    except Exception as exc:
        raise MirrorConnectionError(f"镜像专用连接失败：{_describe(exc)}") from None


def _describe(exc: Exception) -> str:
    sqlstate = getattr(exc, "sqlstate", None)
    return f"{type(exc).__name__}（SQLSTATE {sqlstate}）" if isinstance(sqlstate, str) else type(exc).__name__
