"""Full query fixtures remain separate from minimal observer permission fixtures."""

import asyncio
import os

import pytest
from sqlalchemy.engine import make_url


@pytest.mark.asyncio
async def test_full_pair_fixture_matches_common_query(pg_db_url, tmp_path):
    from mirror_pairs import fetch, open_query_service, publish_pair

    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_service import CommonQueryService
    from ggwork_pick.repository import PickRepository

    engine, service, shared, importer = await open_query_service(pg_db_url, tmp_path)
    try:
        reader_url = make_url(service.query_reader._dsn)
        assert reader_url.username == os.environ["PICK_MIRROR_READER_ROLE"]
        assert reader_url.password, "Local trust authentication must not hide a missing CI reader password"
        assert await fetch(engine, "SELECT rolpassword LIKE 'SCRAM-SHA-256$%' AS scram FROM pg_authid WHERE rolname = :role", role=reader_url.username) == [
            {"scram": True}
        ]
        version_id, _ = await publish_pair(engine, shared, importer, "fixture")
        schema = f"pickm_v{version_id:06d}"
        physical = await fetch(engine, f"SELECT row_key FROM {schema}.catalog_rows ORDER BY row_key")
        assert len(physical) == 3
        assert await fetch(engine, f"SELECT key FROM {schema}.meta")
        assert await fetch(engine, f"SELECT id FROM {schema}.rs_ids") == []
        result = await CommonQueryService(PickRepository(service.session_factory, "alice"), service.query_reader).query(
            CommonQuery(domain="catalog", scope="full_catalog", with_off=True)
        )
        assert result.pin.mirror_version == version_id
        assert result.counts.total == result.counts.matched == 3
        assert sorted(result.board.row_keys) == [row["row_key"] for row in physical]
        async with service.query_reader.connection(deadline=asyncio.get_running_loop().time() + 3) as conn:
            assert await conn.fetchval("SHOW transaction_read_only") == "on"
    finally:
        await service.query_reader.close()
        await engine.dispose()
