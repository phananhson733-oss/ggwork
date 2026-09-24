"""A mirror run against a RealShort double that serves gate_world's consistent world (P2-5c). Synthetic data only.

WorldRealShort answers like FakeRealShort (status codes, envelopes, as_of window, fingerprint checks, v1 pinning), but
its eight tables, manifest totals and v1 rows are gate_world's: a run through it passes every gate and publishes a pair.
A test changes one thing (the world, an intercept, the clock, the limits) to reach one outcome.

The harness is one PostgreSQL database per test, the service initialized on it the way the gateway does, the dedicated
connection's DSN derived from the same URL, and one fake clock shared by the run, its FeedClient and the double.
"""

import json
from dataclasses import dataclass

import gate_world as gw
from engines import host_engine
from fake_realshort import EXPORT_TOKEN, FEED_TOKEN, SPECS, Clock, FakeRealShort
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

BASE = "https://realshort.test"
# What the v1 double's first page names as its rules (fake_realshort._v1_page).
V1_RULES = "# RealShort 选剧规则与口径"
V2_ROW_RESOURCES = tuple(name for name in SPECS if name != "rs_series_day")


def _sorted(table: str, rows) -> list[dict]:
    keys = SPECS[table][0]
    return sorted((dict(row) for row in rows), key=lambda row: tuple(str(row[key]) for key in keys))


class WorldRealShort(FakeRealShort):
    """FakeRealShort with gate_world's tables, manifest and v1 rows; v1_page_rows cuts v1 into short pages."""

    def __init__(self, world: gw.World | None = None, *, v1_page_rows: int = 1000, v1_rules: str | None = V1_RULES, **options):
        super().__init__(**options)
        self.world = world or gw.baseline()
        self.data = {table: _sorted(table, rows) for table, rows in self.world.tables.items()}
        self.v1 = [(f"k{index:04d}", dict(row)) for index, row in enumerate(self.world.v1_rows)]
        self.v1_page_rows = v1_page_rows
        self.v1_rules = v1_rules

    def _manifest(self, as_of: str):
        status, headers, body = super()._manifest(as_of)
        envelope = json.loads(body)
        served = envelope["rows"][0]
        pinned = ("asOf", "fingerprint", "sourceRevision", "latestSnapshot", "snapshotDays")
        row = {**self.world.manifest, **{key: served[key] for key in pinned}}
        data = json.dumps({**envelope, "rows": [row]}, ensure_ascii=False, separators=(",", ":")).encode()
        return status, headers, data

    def _v1_page(self, cursor: str, limit: int, as_of: str | None):
        status, headers, body = super()._v1_page(cursor, min(limit, self.v1_page_rows), as_of)
        if cursor or self.v1_rules == V1_RULES:
            return status, headers, body
        page = {**json.loads(body), "rules": self.v1_rules}
        return status, headers, json.dumps(page, ensure_ascii=False, separators=(",", ":")).encode()


def recording(events: list, reply=None):
    """An intercept that logs every request's resource into events, answering reply(call) (None: the double answers)."""

    def intercept(call):
        events.append(call.resource)
        return reply(call) if reply is not None else None

    return intercept


@dataclass(frozen=True)
class Harness:
    engine: object
    service: object
    dsn: str
    clock: Clock


async def open_harness(url: str, tmp_path) -> Harness:
    from ggwork_pick.mirror.connection import dsn_from_url
    from ggwork_pick.service import PickService

    engine = host_engine(url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    return Harness(engine=engine, service=service, dsn=dsn_from_url(url), clock=Clock())


def make_sync(harness: Harness, fake: FakeRealShort, **overrides):
    from ggwork_pick.mirror.run import MirrorSync

    options = {
        "base_url": BASE,
        "export_token": EXPORT_TOKEN,
        "feed_token": FEED_TOKEN,
        "dsn": harness.dsn,
        "transport": fake.transport(),
        "clock": harness.clock,
        "sleep": harness.clock.sleep,
        "timer": harness.clock.timer,
    }
    return MirrorSync(harness.service, **{**options, **overrides})


def world_fake(harness: Harness, world: gw.World | None = None, **options) -> WorldRealShort:
    return WorldRealShort(world, now=harness.clock, **options)


async def fetch(engine, statement: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(text(statement), params)).mappings()]


async def versions(engine) -> list[dict]:
    return await fetch(
        engine, "SELECT id, schema_name, status, dropped_at, error, agent_catalog_batch_id, agent_knowledge_batch_id FROM pick_mirror.versions ORDER BY id"
    )


async def control(engine) -> dict:
    return (await fetch(engine, "SELECT * FROM pick_mirror.control WHERE id = 1"))[0]


async def schema_exists(engine, name: str) -> bool:
    return bool(await fetch(engine, "SELECT 1 FROM pg_namespace WHERE nspname = :n", n=name))


async def advisory_locks(engine) -> int:
    rows = await fetch(
        engine, "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    )
    return rows[0]["n"]


async def batches(engine) -> list[dict]:
    return await fetch(
        engine,
        "SELECT id, kind, status, content_hash, source_as_of, published_at FROM ggwp_import_batches WHERE owner_id = 'system:shared' ORDER BY created_at, id",
    )


async def shared_current(engine) -> tuple[str | None, str | None]:
    """The shared catalog and knowledge batches every reader sees now (latest published of each kind)."""
    current = {}
    for kind in ("catalog", "knowledge"):
        rows = await fetch(
            engine,
            "SELECT id FROM ggwp_import_batches WHERE owner_id = 'system:shared' AND kind = :k AND status = 'published'"
            " ORDER BY published_at DESC, id DESC LIMIT 1",
            k=kind,
        )
        current[kind] = rows[0]["id"] if rows else None
    return current["catalog"], current["knowledge"]


async def behind(engine) -> bool:
    """P2-8b's behind: the current version's pair is not the shared current pair."""
    current = await fetch(
        engine,
        "SELECT agent_catalog_batch_id AS c, agent_knowledge_batch_id AS k FROM pick_mirror.versions WHERE status = 'published'"
        " ORDER BY published_at DESC, id DESC LIMIT 1",
    )
    return bool(current) and (current[0]["c"], current[0]["k"]) != await shared_current(engine)


async def rows_of(engine, batch_id: str) -> int:
    return (await fetch(engine, "SELECT count(*) AS n FROM ggwp_drama_versions WHERE batch_id = :b", b=batch_id))[0]["n"]


async def sync_runs(harness: Harness) -> list[dict]:
    from ggwork_pick.repository import PickRepository

    return await PickRepository.shared(harness.service.session_factory).sync_runs(limit=50)


def v2_row_calls(calls) -> list:
    return [call for call in calls if call.resource in V2_ROW_RESOURCES]


def v1_calls(calls) -> list:
    return [call for call in calls if call.resource == "v1"]
