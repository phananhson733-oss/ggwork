"""Shared set-up for the staging, pair-publishing and pin tests (P2-5a, P2-5b). Synthetic data only.

P2-3's writer is not on this branch: a building version here is a versions row plus an empty pickm_vNNNNNN schema
with one table, which is all the publish transaction touches (the GRANTs, the status flip).
"""

import json
from datetime import UTC, datetime

from engines import host_engine
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

AS_OF = datetime(2026, 9, 24, 3, 38, tzinfo=UTC)
AS_OF_TEXT = "2026-09-24T03:38:00.000Z"
FINGERPRINT = "0" * 64
# manifest.meta.freshness, RealShort's v2 key names (rs:src/lib/pick/export-v2-map.ts:879)
V2_FRESHNESS = {
    "importedAt": "2026-09-24T02:10:00.000Z",
    "rsSyncedAt": "2026-09-24T01:05:00.000Z",
    "rows": 120,
    "withSignal": 80,
    "signals": 300,
    "posted": 40,
    "rsCanonical": 60,
    "rsCandidates": 30,
}
# The first v1 page's freshness at the same as_of (rs:src/lib/pick/feed.ts:56-62): the same values under v1 names.
V1_FRESHNESS = {
    "catalogImportedAt": "2026-09-24T02:10:00.000Z",
    "reelshortSyncedAt": "2026-09-24T01:05:00.000Z",
    "catalogRows": 120,
    "withSignal": 80,
    "postedRecords": 40,
}
RULES_REF = "realshort:/api/pick-feed#rules"
# publish_mirror_pair takes both on every call (P2-5c passes what G9 read); a run that did not lean on accept-empty.
NO_ACCEPT_EMPTY = {"accept_empty_used": False, "accept_empty_seen": None}


def now() -> datetime:
    return datetime.now(UTC)


def catalog_payload(tag: str, count: int = 3) -> bytes:
    rows = [{"source": "synthetic", "source_id": f"{tag}-{i}", "language": "en", "title": f"合成{tag}{i}", "theater": "Example"} for i in range(1, count + 1)]
    return json.dumps(rows, ensure_ascii=False).encode()


def batch_meta(tag: str) -> dict:
    return {"source": "realshort", "scope": f"scope-{tag}", "freshness": {"catalogImportedAt": f"at-{tag}"}, "source_revision": f"rev-{tag}"}


async def open_service(url: str, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    shared = PickRepository.shared(service.session_factory)
    return engine, service, shared, Importer(shared, service.data_dir)


async def stage_pair(importer, tag: str, *, rules: str | None = None, as_of_text: str = AS_OF_TEXT, meta: dict | None = None) -> list[dict]:
    """A mirror run's v1 half: the catalog and the rules staged, neither visible yet."""
    meta = meta if meta is not None else batch_meta(tag)
    catalog = await importer.catalog(catalog_payload(tag), "json", source_as_of=as_of_text, meta=meta, keep_original=False, stage=True)
    body = (rules or f"# 规则 {tag}").encode()
    knowledge = await importer.knowledge_bundle([(body, "realshort-rules.md", RULES_REF)], source_as_of=as_of_text, meta=meta, stage=True)
    return [catalog, knowledge]


async def building_version(engine, *, as_of: datetime = AS_OF, freshness: dict | None = None) -> tuple[int, str]:
    async with engine.begin() as conn:
        version_id = (await conn.execute(text("SELECT nextval(pg_get_serial_sequence('pick_mirror.versions', 'id'))"))).scalar_one()
        schema = f"pickm_v{version_id:06d}"
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        await conn.execute(text(f"CREATE TABLE {schema}.meta (key text PRIMARY KEY, value jsonb NOT NULL)"))
        await conn.execute(
            text(
                "INSERT INTO pick_mirror.versions (id, schema_name, status, as_of, fingerprint, freshness, created_at)"
                " VALUES (:id, :schema, 'building', :as_of, :fp, CAST(:freshness AS jsonb), :created)"
            ),
            {"id": version_id, "schema": schema, "as_of": as_of, "fp": FINGERPRINT, "freshness": json.dumps(freshness or V2_FRESHNESS), "created": now()},
        )
    return version_id, schema


async def publish_pair(engine, shared, importer, tag: str, *, as_of: datetime = AS_OF, rules: str | None = None) -> tuple[int, list[dict]]:
    """One whole paired run: stage both batches, build a version, publish the pair."""
    staged = await stage_pair(importer, tag, rules=rules, as_of_text=_as_of_text(as_of))
    version_id, schema = await building_version(engine, as_of=as_of)
    await shared.publish_mirror_pair(version_id=version_id, schema_name=schema, batches=staged, t=now(), **NO_ACCEPT_EMPTY)
    return version_id, staged


async def degrade(shared, importer, tag: str, *, rules: str | None = None, reason: str = "degraded:G5") -> list[dict]:
    """A degraded run: the v1 half published on its own, the failure counted."""
    staged = await stage_pair(importer, tag, rules=rules)
    await shared.publish_agent_only(batches=staged, reason=reason, t=now())
    return staged


def _as_of_text(as_of: datetime) -> str:
    return as_of.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:00.000Z")


async def fetch(engine, sql: str, **params) -> list[dict]:
    async with engine.connect() as conn:
        return [dict(row) for row in (await conn.execute(text(sql), params)).mappings()]


async def control(engine) -> dict:
    return (await fetch(engine, "SELECT * FROM pick_mirror.control WHERE id = 1"))[0]


async def version(engine, version_id: int) -> dict:
    return (await fetch(engine, "SELECT * FROM pick_mirror.versions WHERE id = :id", id=version_id))[0]


async def batch(engine, batch_id: str) -> dict:
    rows = await fetch(engine, "SELECT * FROM ggwp_import_batches WHERE id = :id", id=batch_id)
    return {**rows[0], "validation_json": _json(rows[0]["validation_json"])}


async def row_count(engine, batch_id: str) -> int:
    drama = await fetch(engine, "SELECT count(*) AS n FROM ggwp_drama_versions WHERE batch_id = :id", id=batch_id)
    knowledge = await fetch(engine, "SELECT count(*) AS n FROM ggwp_knowledge_versions WHERE batch_id = :id", id=batch_id)
    return drama[0]["n"] + knowledge[0]["n"]


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


async def behind(engine, shared) -> bool:
    """P2-8b's rule, spelled out here: the current version's pair is not the shared current pair."""
    current = await fetch(
        engine,
        "SELECT agent_catalog_batch_id, agent_knowledge_batch_id FROM pick_mirror.versions"
        " WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1",
    )
    if not current:
        return False
    catalog, knowledge = await shared.current_batch("catalog"), await shared.current_batch("knowledge")
    return (current[0]["agent_catalog_batch_id"], current[0]["agent_knowledge_batch_id"]) != (catalog["id"], knowledge["id"])
