"""The mirror lock: one PostgreSQL session-level advisory lock, held on the dedicated connection (U13, U14; plan 5.5).

Session-level, not transaction-level: a run commits many statements (a COPY per page, index builds, ANALYZE) under one
hold, and the backfill and cleanup commands take the same key from their own processes. Ending the session releases it,
so a killed process never leaves it held; Supavisor's session mode resets the server session when the client goes,
although the next client gets the same backend pid (docs/pick-workbench/supabase.md). That reuse is why the holder
records when it took the lock in pick_mirror.control.lock_holder_since: pg_stat_activity.backend_start belongs to the
pooled backend, not to the hold, and would call a sync that just started stuck.
"""

import asyncio
import hashlib
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from sqlalchemy import text

from ggwork_pick.mirror.connection import open_dedicated

logger = logging.getLogger(__name__)

# Not under "ggwp:": repository._write keys each owner's writes as sha256("ggwp:" + owner) (U13).
MIRROR_LOCK_KEY = int.from_bytes(hashlib.sha256(b"pickm:mirror-sync").digest()[:8], "big", signed=True)
# pg_locks shows a bigint key as two oids: classid the high 32 bits, objid the low 32 bits, objsubid 1.
MIRROR_LOCK_CLASSID = (MIRROR_LOCK_KEY >> 32) & 0xFFFFFFFF
MIRROR_LOCK_OBJID = MIRROR_LOCK_KEY & 0xFFFFFFFF
# A legitimate run can reach about 51 minutes (20 of busy waiting, the run deadline, one drift retry, the curve fold).
LOCK_STUCK_AFTER = timedelta(minutes=60)
CLOSE_TIMEOUT = 10

# The granted mirror lock in this database; the key halves are integers computed above, never input.
_GRANTED = (
    "l.locktype = 'advisory' AND l.granted AND l.objsubid = 1"
    " AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    f" AND l.classid = CAST({MIRROR_LOCK_CLASSID} AS oid) AND l.objid = CAST({MIRROR_LOCK_OBJID} AS oid)"
)
_HELD_HERE = f"EXISTS (SELECT 1 FROM pg_locks AS l WHERE l.pid = pg_backend_pid() AND {_GRANTED})"
_RECORD = "UPDATE pick_mirror.control SET lock_holder_since = $1 WHERE id = 1"
_CLEAR = f"UPDATE pick_mirror.control SET lock_holder_since = NULL WHERE id = 1 AND {_HELD_HERE}"
_UNLOCK = f"SELECT CASE WHEN {_HELD_HERE} THEN pg_advisory_unlock($1) ELSE false END"
_STATUS = (
    f"SELECT (SELECT l.pid FROM pg_locks AS l WHERE {_GRANTED} LIMIT 1) AS holder_pid,"
    " (SELECT c.lock_holder_since FROM pick_mirror.control AS c WHERE c.id = 1) AS holder_since"
)
# Cleanups outliving a second cancellation: referenced here until they finish, as PickService.spawn does.
_CLEANUPS: set[asyncio.Task] = set()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 必须带时区")


async def try_mirror_lock(conn, *, now: datetime) -> bool:
    """Take the lock on this asyncpg connection without waiting; the holder records `now` as when it took it.

    Each statement commits on its own: the connection must not be inside a transaction. Call it once per connection:
    session-level advisory locks stack, and one release_mirror_lock undoes only one take.
    """
    _require_aware(now)
    if not await conn.fetchval("SELECT pg_try_advisory_lock($1)", MIRROR_LOCK_KEY):
        return False
    try:
        if await conn.execute(_RECORD, now) != "UPDATE 1":
            raise RuntimeError("pick_mirror.control 缺少 id=1 这一行，镜像锁无法记录拿锁时间")
    except Exception:
        # Held without its timestamp it could never be judged stuck: give it back rather than hold it blind.
        await _unlock_or_log(conn)
        raise
    return True


async def release_mirror_lock(conn) -> bool:
    """Clear the holder's timestamp, then unlock. A connection not holding the lock changes nothing and gets False."""
    await conn.execute(_CLEAR)
    return bool(await conn.fetchval(_UNLOCK, MIRROR_LOCK_KEY))


async def lock_status(conn, *, now: datetime, stuck_after: timedelta = LOCK_STUCK_AFTER) -> dict:
    """Who holds the mirror lock in this database, since when, and whether that is past `stuck_after`.

    `conn` is an asyncpg connection or a SQLAlchemy AsyncSession / AsyncConnection. holder_since is reported only
    while the lock is held: a timestamp left by a holder that died says nothing. Whether this process holds the lock
    itself (its sync_lock) is the caller's to add.
    """
    _require_aware(now)
    if hasattr(conn, "fetchrow"):
        row = await conn.fetchrow(_STATUS)
        holder_pid, since = row["holder_pid"], row["holder_since"]
    else:
        holder_pid, since = (await conn.execute(text(_STATUS))).one()
    held = holder_pid is not None
    holder_since = since if held else None
    stuck = holder_since is not None and holder_since < now - stuck_after
    return {"held": held, "holder_pid": holder_pid, "holder_since": holder_since, "stuck": stuck}


@asynccontextmanager
async def mirror_lock(dsn: str, *, clock: Callable[[], datetime] = _utc_now) -> AsyncIterator:
    """A dedicated connection holding the mirror lock, or None when another holder has it.

    On the way out, whatever happened, a cancellation included, the lock is released and the connection closed.
    """
    conn = await open_dedicated(dsn)
    held = False
    try:
        held = await try_mirror_lock(conn, now=clock())
        yield conn if held else None
    finally:
        await _shielded(_release_and_close(conn, held))


async def _shielded(coroutine) -> None:
    task = asyncio.ensure_future(coroutine)
    _CLEANUPS.add(task)
    task.add_done_callback(_CLEANUPS.discard)
    await asyncio.shield(task)


async def _release_and_close(conn, held: bool) -> None:
    if held:
        try:
            await release_mirror_lock(conn)
        except Exception:
            # A statement cancelled mid-flight can leave the connection busy; closing ends the session and the lock.
            logger.warning("[pick-mirror] releasing the mirror lock failed; closing the connection releases it", exc_info=True)
    try:
        await conn.close(timeout=CLOSE_TIMEOUT)
    except Exception:
        logger.warning("[pick-mirror] the dedicated connection did not close cleanly; terminated", exc_info=True)
        conn.terminate()


async def _unlock_or_log(conn) -> None:
    try:
        await conn.fetchval("SELECT pg_advisory_unlock($1)", MIRROR_LOCK_KEY)
    except Exception:
        logger.warning("[pick-mirror] giving the mirror lock back failed; closing the connection releases it", exc_info=True)
