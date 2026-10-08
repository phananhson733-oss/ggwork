"""Bounded, independent read-only pool for common queries; never borrows the writer."""

import asyncio
import json
import os
import ssl as tls
from contextlib import asynccontextmanager
from urllib.parse import urlsplit


class QueryFailure(ValueError):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class QueryReader:
    def __init__(self, dsn: str, *, ssl):
        parsed = urlsplit(dsn)
        if parsed.scheme not in {"postgres", "postgresql"} or parsed.query or parsed.fragment:
            raise QueryFailure("source_unavailable", "公共查询只读连接配置不可用")
        self._dsn = dsn
        self._ssl = ssl
        self.pool = None
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls):
        dsn = os.environ.get("PICK_MIRROR_READER_URL", "").strip()
        if not dsn:
            return None
        ca = os.environ.get("PICK_MIRROR_CA_PEM", "")
        if "-----BEGIN CERTIFICATE-----" not in ca:
            raise QueryFailure("source_unavailable", "公共查询只读连接缺少 CA 证书")
        return cls(dsn, ssl=tls.create_default_context(cadata=ca))

    async def _pool(self):
        import asyncpg

        async with self._lock:
            if self.pool is None:

                async def init(conn):
                    await conn.set_type_codec("json", schema="pg_catalog", encoder=json.dumps, decoder=json.loads)
                    await conn.set_type_codec("jsonb", schema="pg_catalog", encoder=json.dumps, decoder=json.loads)

                async def reset(_conn):
                    # asyncpg still cancels/rolls back before this callback. Only SET LOCAL
                    # and SELECT run here; skip session RESET ALL behind transaction pools.
                    pass

                self.pool = await asyncpg.create_pool(
                    self._dsn,
                    ssl=self._ssl,
                    min_size=0,
                    max_size=3,
                    timeout=5,
                    command_timeout=8,
                    statement_cache_size=0,
                    max_inactive_connection_lifetime=5,
                    init=init,
                    reset=reset,
                    server_settings={"application_name": "ggwp-common-query"},
                )
            return self.pool

    @asynccontextmanager
    async def connection(self, *, deadline: float):
        """Absolute loop deadline includes pool wait. Cancellation rolls back and releases."""
        async with asyncio.timeout_at(deadline):
            pool = await self._pool()
            async with pool.acquire(timeout=max(0.001, deadline - asyncio.get_running_loop().time())) as conn:
                async with conn.transaction(readonly=True):
                    ms = max(1, min(8000, int((deadline - asyncio.get_running_loop().time()) * 1000)))
                    await conn.execute(f"SET LOCAL statement_timeout = {ms}")
                    await conn.execute("SET LOCAL search_path = pick_mirror")
                    yield conn

    async def close(self):
        if self.pool is not None:
            pool, self.pool = self.pool, None
            await pool.close()
