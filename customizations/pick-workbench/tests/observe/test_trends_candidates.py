"""Candidate preview reads real catalog selections without creating an observation run."""

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from engines import host_engine
from sqlalchemy import event, text
from top_dramas_fixtures import board_drama
from trends_session_helpers import TARGET, seed_catalog

from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends import top_dramas as top
from ggwork_pick.observe.trends.top_dramas import TopDramasTaskSource

PATH = "/api/pick/obs/trends-candidates"
ALICE = {"test-owner": "alice"}


@pytest.mark.asyncio
async def test_preview_requires_auth_and_empty_is_not_a_failed_read(app_client):
    client, _ = app_client
    assert (await client.get(PATH)).status_code == 401
    response = await client.get(PATH, headers=ALICE)
    assert response.status_code == 200
    data = response.json()
    assert data["kind"] == "candidate_preview"
    assert (data["selection_state"], data["selected"], data["rows"]) == ("empty", 0, [])
    assert "batch_id" not in data and "planned" not in data


@pytest.mark.asyncio
async def test_preview_matches_collector_selection_without_writes(app_client, pick_db_url):
    client, service = app_client
    await seed_catalog(
        pick_db_url,
        [
            board_drama(1, title="Alpha’s Bride (Dubbed)", boards={"qc": 2, "qr": 1}),
            board_drama(2, title="First Choice", boards={"qc": 1}),
            board_drama(3, title="!!!", boards={"kd": 1}),
            board_drama(4, title="First Choice", boards={"kd": 2}),
        ],
    )
    statements = []
    async with service.session_factory() as session:
        conn = await session.connection()
        engine = conn.engine.sync_engine
        expected = await TopDramasTaskSource().units(ReadStep(conn), target_date=TARGET)

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        response = await client.get(PATH, headers=ALICE)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert response.status_code == 200
    body = response.json()
    if os.environ.get("PICK_WRITE_CONTRACT") == "1":
        path = Path(__file__).resolve().parents[4] / "frontend/tests/unit/core/pick/fixtures/backend-trends-candidates.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n")
    assert body["selection_state"] == "ready" and body["unusable_titles"] == 1
    assert body["selected"] == 3
    assert body["catalog_batch_id"] == expected.catalog_batch_id
    for row, unit in zip(body["rows"], expected.units, strict=True):
        note = expected.notes["top_dramas"]["picks"][unit.key]
        assert {key: row[key] for key in note} == note
        assert (row["term"], row["geo"], row["time_range"]) == (unit.bare, "WW", "today 1-m")
        assert not {"series", "result", "unit", "status"} & row.keys()
    assert statements and set(statements) <= {"SELECT", "SET", "BEGIN", "PRAGMA"}


@pytest.mark.asyncio
async def test_failed_preview_is_safe_503(app_client, monkeypatch, caplog):
    client, _ = app_client

    async def broken(repo, *, now):
        raise RuntimeError("private database content")

    monkeypatch.setattr("ggwork_pick.routes.trends_candidates", broken)
    with caplog.at_level(logging.WARNING):
        response = await client.get(PATH, headers=ALICE)
    assert response.status_code == 503
    assert "private database content" not in response.text + caplog.text


@pytest.mark.asyncio
async def test_selection_keeps_snapshot_when_catalog_is_published_mid_read(app_client, pick_db_url, monkeypatch):
    client, _ = app_client
    engine = host_engine(pick_db_url)
    try:
        if engine.dialect.name == "sqlite":
            async with engine.connect() as conn:
                await conn.execute(text("PRAGMA journal_mode=WAL"))
                await conn.commit()
        await seed_catalog(pick_db_url, [board_drama(1, title="Original", boards={"qc": 1})])
        original_revenue = top.revenue
        snapshots = []

        async def publish_then_read(step, *, limit):
            await seed_catalog(
                pick_db_url,
                [board_drama(2, title="Newly Published", boards={"qc": 1})],
                batch_id="cat-2",
                published_at=datetime(2026, 10, 7, tzinfo=UTC),
            )
            batch, _ = await top.shared_catalog(step)
            snapshots.append(batch)
            return await original_revenue(step, limit=limit)

        monkeypatch.setattr(top, "revenue", publish_then_read)
        response = await client.get(PATH, headers=ALICE)
        assert response.status_code == 200
        assert snapshots == ["cat-1"]
        assert response.json()["rows"][0]["title"] == "Original"
        monkeypatch.setattr(top, "revenue", original_revenue)
        next_response = await client.get(PATH, headers=ALICE)
        assert next_response.json()["catalog_batch_id"] == "cat-2"
        assert next_response.json()["rows"][0]["title"] == "Newly Published"
    finally:
        await engine.dispose()
