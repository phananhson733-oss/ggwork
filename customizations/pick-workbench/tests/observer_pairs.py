"""Minimal observer GRANT fixtures: no role mutation, empty meta and no implicit rs_ids.

Full query fixtures live in mirror_pairs and must not be used with the restricted
application connection in observer permission tests.
"""

import json
from datetime import datetime

from engines import host_engine
from mirror_pairs import AS_OF, FINGERPRINT, V2_FRESHNESS, now
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker


async def open_service(url: str, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    shared = PickRepository.shared(service.session_factory)
    return engine, service, shared, Importer(shared, service.data_dir)


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
