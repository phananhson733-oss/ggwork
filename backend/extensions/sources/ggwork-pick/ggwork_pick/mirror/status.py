"""GET /api/pick/sync's mirror key (implementation note P2-8b; plan 1623; U14; P3 critique B20).

Every signed-in user reads it, so it carries only ids, times, fixed codes and counts. None on SQLite (no pick_mirror);
on PostgreSQL it is there whether the mirror is switched on or not:
- current: the published version readers see (the latest published_at, the id breaking ties, as pin.py reads it);
- series_through / trimmed_before: how far the curve reaches (pick_mirror.series_state);
- behind: a current version whose pair is not the shared current pair, i.e. a degraded run published newer agent
  batches. The shared pair is the shared owner's latest published batch of each kind, never the signed-in user's: their
  own import may shadow the shared catalog (repository.current_batch reads both owners);
- consecutive_failures, last_failure_at, last_failure (a fixed code, U11) and alert from ALERT_AFTER failures on;
- warnings: the codes of manifest.meta.warnings the current version was built with;
- lock_stuck (U14), judged at request time: {pid, holder, since} only when pg_locks shows the mirror lock held, this
  process's sync_lock is not held, and pick_mirror.control.lock_holder_since is more than LOCK_STUCK_AFTER old. Never
  pg_stat_activity.backend_start: behind Supavisor the next client inherits the same backend (lock.py);
- shared_source_as_of: the shared current catalog's source_as_of, for the P3 banner (B20): top-level current is the
  signed-in user's and may be their own import.
Version, curve, control and the shared batches come from one statement, one snapshot: a paired publish committing
meanwhile never shows as behind.
"""

from collections.abc import Mapping
from datetime import date, datetime

from sqlalchemy import text

from ggwork_pick.mirror.feed_shape import format_as_of
from ggwork_pick.mirror.lock import lock_status
from ggwork_pick.mirror.publish import ALERT_AFTER
from ggwork_pick.models import import_batches
from ggwork_pick.repository import SHARED_OWNER, PickRepository, stamp

_BATCHES = import_batches.name  # a constant table name, never input


def _shared_latest(column: str, kind: str) -> str:
    # kind and column are the constants below; the owner is bound.
    return (
        f"(SELECT b.{column} FROM {_BATCHES} AS b WHERE b.owner_id = :owner AND b.kind = '{kind}' AND b.status = 'published'"
        " ORDER BY b.published_at DESC, b.id DESC LIMIT 1)"
    )


_STATUS = f"""
SELECT v.id AS version_id, v.as_of, v.latest_snapshot, v.published_at, v.warnings,
       v.agent_catalog_batch_id, v.agent_knowledge_batch_id,
       s.through, s.trimmed_before,
       c.consecutive_failures, c.last_failure_at, c.last_failure,
       {_shared_latest("id", "catalog")} AS shared_catalog_id,
       {_shared_latest("source_as_of", "catalog")} AS shared_source_as_of,
       {_shared_latest("id", "knowledge")} AS shared_knowledge_id
FROM (SELECT 1) AS one
LEFT JOIN pick_mirror.control AS c ON c.id = 1
LEFT JOIN pick_mirror.series_state AS s ON s.id = 1
LEFT JOIN LATERAL (
    SELECT id, as_of, latest_snapshot, published_at, warnings, agent_catalog_batch_id, agent_knowledge_batch_id
    FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1
) AS v ON true
"""


def _day(value: date | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value is not None else None


def _moment(value: datetime | None) -> str | None:
    return stamp(value) if value is not None else None


def _as_of(value: datetime) -> str:
    """RealShort's asOf text, as data_as_of's source_as_of reads (pin.py); stamp() for a value off the minute."""
    try:
        return format_as_of(value)
    except ValueError:
        return stamp(value)


def warning_codes(warnings: object) -> list[str]:
    """manifest.meta.warnings as stored on the version, reduced to their codes (fixed words, contracts.py)."""
    if not isinstance(warnings, list):
        return []
    return [item["code"] for item in warnings if isinstance(item, Mapping) and isinstance(item.get("code"), str)]


def _current(row) -> dict | None:
    if row.version_id is None:
        return None
    return {"id": row.version_id, "as_of": _as_of(row.as_of), "latest_snapshot": _day(row.latest_snapshot), "published_at": _moment(row.published_at)}


def _behind(row) -> bool:
    if row.version_id is None:
        return False
    return (row.agent_catalog_batch_id, row.agent_knowledge_batch_id) != (row.shared_catalog_id, row.shared_knowledge_id)


def _lock_stuck(status: dict, *, sync_running: bool) -> dict | None:
    if not status["held"] or sync_running or not status["stuck"]:
        return None
    return {"pid": status["holder_pid"], "holder": status["holder"], "since": _moment(status["holder_since"])}


def _view(row, lock: dict, *, enabled: bool, sync_running: bool) -> dict:
    failures = row.consecutive_failures or 0
    return {
        "enabled": enabled,
        "current": _current(row),
        "series_through": _day(row.through),
        "trimmed_before": _day(row.trimmed_before),
        "behind": _behind(row),
        "consecutive_failures": failures,
        "last_failure_at": _moment(row.last_failure_at),
        "last_failure": row.last_failure,
        "alert": failures >= ALERT_AFTER,
        "warnings": warning_codes(row.warnings),
        "lock_stuck": _lock_stuck(lock, sync_running=sync_running),
        "shared_source_as_of": row.shared_source_as_of,
    }


async def mirror_status(repo: PickRepository, *, enabled: bool, sync_running: bool, now: datetime) -> dict | None:
    """The mirror key for GET /api/pick/sync; None on SQLite. repo must be PickRepository.shared(...): the shared pair
    and the shared source_as_of are read as the shared owner's (B20), whoever is signed in. sync_running is whether
    this process holds its sync_lock (a run of its own holds the mirror lock legitimately)."""
    if repo.owner_id != SHARED_OWNER:
        raise ValueError("镜像状态按共享批次计算：传 PickRepository.shared(...)")
    async with repo.session_factory() as session:
        if session.bind.dialect.name != "postgresql":
            return None
        row = (await session.execute(text(_STATUS), {"owner": repo.owner_id})).one()
        lock = await lock_status(session, now=now)
    return _view(row, lock, enabled=enabled, sync_running=sync_running)
