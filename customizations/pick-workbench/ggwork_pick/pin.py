"""The data a run works on: (catalog batch, knowledge batch, mirror version) and the data_as_of it answers with.

One SELECT reads them all (U7): under READ COMMITTED a statement sees one snapshot, so a publish committing
meanwhile can never pair an old catalog with a new version. Every batch column is its own scalar subquery with the
same WHERE and ORDER BY (the id breaks ties), so within the snapshot they all read the same row; that part runs on
both dialects. Only PostgreSQL has pick_mirror, so only there is the current version appended, as one JSON value.

A version counts only when its pair is exactly the batches read: a degraded run's newer batch, or a user's own
import shadowing the shared one, leaves the run without a version, and data_as_of then comes from the batch.
"""

from datetime import UTC, datetime
from typing import NamedTuple

from sqlalchemy import JSON, literal_column, or_, select

from ggwork_pick.mirror.feed_shape import format_as_of
from ggwork_pick.models import import_batches
from ggwork_pick.repository import DATA_AS_OF_KEYS, SHARED_OWNER, stamp

# The current version, as json. row_to_json is PostgreSQL's: the SQLite statement never carries this column.
_VERSION = (
    "(SELECT row_to_json(v) FROM (SELECT id, as_of, published_at, agent_catalog_batch_id, agent_knowledge_batch_id, freshness"
    " FROM pick_mirror.versions WHERE status = 'published' ORDER BY published_at DESC, id DESC LIMIT 1) AS v)"
)
# manifest.meta.freshness (v2 names) onto the v1 page's names (rs:src/lib/pick/feed.ts:56-62), which the frontend
# reads (frontend/src/core/pick/format.ts:22-23): same values, other keys (U5).
V1_FRESHNESS_KEYS = (
    ("catalogImportedAt", "importedAt"),
    ("reelshortSyncedAt", "rsSyncedAt"),
    ("catalogRows", "rows"),
    ("withSignal", "withSignal"),
    ("postedRecords", "posted"),
)


class Pin(NamedTuple):
    """Pin(id, None) still reads like the (catalog, knowledge) pair it replaces."""

    catalog_id: str | None
    knowledge_id: str | None = None
    mirror_version: int | None = None
    data_as_of: dict | None = None


def as_pin(value) -> Pin:
    """A Pin from a Pin or a plain (catalog, knowledge[, version, data_as_of]) tuple."""
    return value if isinstance(value, Pin) else Pin(*value)


def _latest(column, kind: str, owner_id: str):
    return (
        select(column)
        .where(
            or_(import_batches.c.owner_id == owner_id, import_batches.c.owner_id == SHARED_OWNER),
            import_batches.c.kind == kind,
            import_batches.c.status == "published",
        )
        .order_by(import_batches.c.published_at.desc(), import_batches.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )


def pin_statement(owner_id: str, dialect: str):
    catalog = import_batches.c
    columns = [
        _latest(catalog.id, "catalog", owner_id).label("catalog_id"),
        _latest(catalog.owner_id, "catalog", owner_id).label("catalog_owner"),
        _latest(catalog.source_as_of, "catalog", owner_id).label("catalog_source_as_of"),
        _latest(catalog.published_at, "catalog", owner_id).label("catalog_published_at"),
        _latest(catalog.validation_json, "catalog", owner_id).label("catalog_validation"),
        _latest(catalog.id, "knowledge", owner_id).label("knowledge_id"),
    ]
    if dialect == "postgresql":
        columns.append(literal_column(_VERSION, JSON).label("version"))
    return select(*columns)


def _moment(value: str) -> datetime:
    """A timestamptz as row_to_json writes it: ISO 8601 with the session's offset."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("镜像版本的时间缺少时区")
    return moment.astimezone(UTC)


def v1_freshness(freshness: dict | None) -> dict | None:
    if freshness is None:
        return None
    return {v1: freshness.get(v2) for v1, v2 in V1_FRESHNESS_KEYS}


def _batch_data_as_of(row) -> dict:
    validation = row.catalog_validation or {}
    values = {
        "source_as_of": row.catalog_source_as_of,
        "published_at": row.catalog_published_at,
        "freshness": validation.get("freshness"),
        "scope": validation.get("scope"),
        "shared": row.catalog_owner == SHARED_OWNER,
    }
    return {key: values[key] for key in DATA_AS_OF_KEYS}


def _version_data_as_of(row, version: dict) -> dict:
    values = {
        "source_as_of": format_as_of(_moment(version["as_of"])),
        # stamp() of the very instant the paired publish also wrote on the batches, so the two read alike.
        "published_at": stamp(_moment(version["published_at"])),
        "freshness": v1_freshness(version.get("freshness")),
        "scope": (row.catalog_validation or {}).get("scope"),
        "shared": True,
    }
    return {key: values[key] for key in DATA_AS_OF_KEYS}


def pin_from_row(row) -> Pin:
    if row.catalog_id is None:
        return Pin(None, row.knowledge_id)
    version = getattr(row, "version", None)
    if version is not None and (version["agent_catalog_batch_id"], version["agent_knowledge_batch_id"]) == (row.catalog_id, row.knowledge_id):
        return Pin(row.catalog_id, row.knowledge_id, version["id"], _version_data_as_of(row, version))
    return Pin(row.catalog_id, row.knowledge_id, None, _batch_data_as_of(row))


async def read_pin(session_factory, owner_id: str) -> Pin:
    async with session_factory() as session:
        statement = pin_statement(owner_id, session.bind.dialect.name)
        row = (await session.execute(statement)).one()
    return pin_from_row(row)
