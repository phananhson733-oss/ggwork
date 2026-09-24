"""Shared set-up for the curve tests (P2-7): synthetic daily snapshots, the RealShort double serving them, a dedicated
connection holding the mirror lock, and readers for pick_mirror.series.

Every drama id and value here is synthetic. A day's rows are what rs_series_day returns for it (rs:src/lib/pick/export-v2.ts
seriesDaySql: drama_id, recent_revenue_cents::float8, promoters_cnt of the metrics_valid snapshots), sorted by drama_id as
RealShort's keyset pages are.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fake_realshort import Clock, FakeRealShort
from mirror_harness import make_client

from ggwork_pick.mirror.connection import open_dedicated
from ggwork_pick.mirror.lock import try_mirror_lock

AS_OF_DAY = date(2026, 9, 23)  # the UTC day of fake_realshort.START's as_of (12:32)
FINGERPRINT = "a" * 64
# conftest's guard for statements sent through SQLAlchemy; the dedicated connection is bare asyncpg, so the tests apply it.
SESSION_SETTING = re.compile(r"(?is)^\s*(SET\s+(?!LOCAL\b)|RESET\b)|\bset_config\s*\([^)]*,\s*(false|0)\s*\)")
POINTS = "SELECT s.drama_id, u.d, u.rc, u.p FROM pick_mirror.series AS s CROSS JOIN LATERAL unnest(s.days, s.revenue_cents, s.promoters) AS u(d, rc, p)"
# The profile page's read (plan 3.2): the canonical id's points from the version's as_of day - 90 to its latest_snapshot.
CURVE = (
    "SELECT u.d, u.rc, u.p FROM pick_mirror.series AS s CROSS JOIN LATERAL unnest(s.days, s.revenue_cents, s.promoters) AS u(d, rc, p)"
    " WHERE s.drama_id = $1 AND u.d >= (($2::timestamptz AT TIME ZONE 'UTC')::date - 90) AND u.d <= $3 ORDER BY u.d"
)


def text(day: date) -> str:
    return day.strftime("%Y-%m-%d")


def span(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def observation(drama_id: str, day: date) -> dict:
    """One synthetic snapshot point; the values differ per drama and per day."""
    seed = sum(map(ord, drama_id))
    return {"drama_id": drama_id, "revenue_cents": float(day.toordinal() % 997 * 10 + seed % 7) + 0.25, "promoters_cnt": (day.toordinal() + seed) % 50}


def snapshots(days: Iterable[date], ids: Callable[[date], Iterable[str]]) -> dict[str, list[dict]]:
    """{day: rows} for rs_series_day and the manifest's snapshotDays."""
    return {text(day): sorted((observation(i, day) for i in ids(day)), key=lambda row: row["drama_id"]) for day in days}


def realshort_curve(series: dict[str, list[dict]], drama_id: str, as_of: datetime) -> list[tuple]:
    """loadDramaDetail's curve (rs:src/lib/observe/queries.ts:921-927): the canonical id, observed_on from
    utcDayOffset(SERIES_DAYS=90, asOf) to utcDayOffset(0, asOf)."""
    last = as_of.astimezone(UTC).date()
    first = last - timedelta(days=90)
    mine = [(date.fromisoformat(day), row) for day, rows in series.items() for row in rows if row["drama_id"] == drama_id]
    return sorted((day, row["revenue_cents"], row["promoters_cnt"]) for day, row in mine if first <= day <= last)


@dataclass
class World:
    clock: Clock
    fake: FakeRealShort
    conn: object
    client: object

    def serve(self, series: dict[str, list[dict]]) -> None:
        """What RealShort has now: the days the manifest lists and rs_series_day returns."""
        self.fake.series = dict(sorted(series.items()))

    def series_calls(self) -> list[str]:
        return [call.params["day"] for call in self.fake.calls if call.resource == "rs_series_day"]

    async def fold(self, **options):
        from ggwork_pick.mirror.series import fold_series

        manifest = await self.client.manifest_when_free()
        return await fold_series(self.conn, manifest=manifest, client=self.client, clock=self.clock, **options)


async def open_world(dsn: str, *, clock: Clock | None = None, intercept=None, holder: str | None = "sync") -> World:
    """The double, a client on it, and a dedicated connection that holds the mirror lock as `holder` (None: not taken)."""
    clock = clock or Clock()
    fake = FakeRealShort(now=clock, series={}, intercept=intercept)
    conn = await open_dedicated(dsn)
    if holder is not None:
        assert await try_mirror_lock(conn, now=clock(), holder=holder)
    return World(clock=clock, fake=fake, conn=conn, client=make_client(fake, clock))


async def close_world(world: World) -> None:
    await world.client.aclose()
    await world.conn.close()


async def set_state(conn, through: date | None, trimmed_before: date | None = None) -> None:
    await conn.execute("UPDATE pick_mirror.series_state SET through = $1, trimmed_before = $2 WHERE id = 1", through, trimmed_before)


async def state(conn) -> tuple:
    row = await conn.fetchrow("SELECT through, trimmed_before FROM pick_mirror.series_state WHERE id = 1")
    return row["through"], row["trimmed_before"]


async def points(conn) -> set[tuple]:
    return {tuple(row) for row in await conn.fetch(POINTS)}


def as_points(series: dict[str, list[dict]]) -> set[tuple]:
    return {(row["drama_id"], date.fromisoformat(day), row["revenue_cents"], row["promoters_cnt"]) for day, rows in series.items() for row in rows}


async def seed(conn, series: dict[str, list[dict]]) -> None:
    """Rows as a finished fold leaves them: one per drama, the three arrays sorted by day and of equal length."""
    by_drama: dict[str, list[tuple]] = {}
    for day, rows in sorted(series.items()):
        for row in rows:
            by_drama.setdefault(row["drama_id"], []).append((date.fromisoformat(day), row["revenue_cents"], row["promoters_cnt"]))
    await conn.executemany(
        "INSERT INTO pick_mirror.series (drama_id, days, revenue_cents, promoters, updated_at) VALUES ($1, $2, $3, $4, now())",
        [(drama, [p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts]) for drama, pts in by_drama.items()],
    )


async def add_version(conn, number: int, as_of: datetime, *, status: str = "published", latest_snapshot: date | None = None) -> None:
    published = as_of if status in ("published", "dropped") else None
    await conn.execute(
        "INSERT INTO pick_mirror.versions (schema_name, status, as_of, fingerprint, latest_snapshot, created_at, published_at)"
        " VALUES ($1, $2, $3, $4, $5, $3, $6)",
        f"pickm_v{number:06d}",
        status,
        as_of,
        FINGERPRINT,
        latest_snapshot,
        published,
    )


async def versions(conn) -> list[tuple]:
    return [tuple(row) for row in await conn.fetch("SELECT * FROM pick_mirror.versions ORDER BY id")]


async def curve(conn, drama_id: str, *, as_of: datetime, latest_snapshot: date) -> list[tuple]:
    return [tuple(row) for row in await conn.fetch(CURVE, drama_id, as_of, latest_snapshot)]


class Watched:
    """The dedicated connection with every statement recorded: the conftest guard never sees a bare asyncpg connection."""

    _QUERIES = ("execute", "executemany", "fetch", "fetchrow", "fetchval")

    def __init__(self, conn):
        self._conn = conn
        self.sent: list[str] = []

    def __getattr__(self, name):
        target = getattr(self._conn, name)
        if name not in self._QUERIES:
            return target

        async def recorded(query, *args, **kwargs):
            self.sent.append(query)
            return await target(query, *args, **kwargs)

        return recorded
