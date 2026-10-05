"""Canary evidence is read-only and never a promotion decision."""

import io
import json
from datetime import UTC, datetime

import pytest
from obs_db_helpers import migrated, open_db, snapshot
from sqlalchemy import insert

from ggwork_pick.models import obs_batches, obs_budget, obs_requests
from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.trends.__main__ import amain

TABLES = ("ggwp_obs_runtime", "ggwp_obs_budget", "ggwp_obs_batches", "ggwp_obs_requests")


async def report(url):
    out = io.StringIO()
    assert await amain(["canary-report"], environ={"PICK_DATABASE_URL": url}, clock=ManualClock(datetime(2026, 10, 6, 12, tzinfo=UTC)), out=out) == 0
    return json.loads(out.getvalue())


@pytest.mark.asyncio
async def test_empty_database_is_unknown_without_collector_configuration(pick_db_url, tmp_path):
    url = await migrated(pick_db_url, tmp_path)
    before = await snapshot(url, TABLES)
    result = await report(url)
    assert result["latest_recorded_date"] is None
    assert len(result["days"]) == 7
    assert all(day["load_and_request_valid"] is None for day in result["days"])
    assert result["days"][-1]["evidence"] == "pending"
    assert result["qualification"] == "not_evaluated"
    assert await snapshot(url, TABLES) == before


@pytest.mark.asyncio
async def test_stored_admission_and_actual_requests_not_reservations_define_valid_load(pick_db_url, tmp_path, monkeypatch):
    url = await migrated(pick_db_url, tmp_path)
    import httpx

    async def forbidden(*args, **kwargs):
        pytest.fail("read-only report attempted HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    accepted = {"reasons": [], "planned_requests": 204, "min_requests": 176, "missing_first": ["PRIVATE_TITLE"]}
    async with open_db(url).transaction() as conn:
        for day, plan, summary, late in [
            ("2026-10-01", accepted, None, False),
            ("2026-10-02", None, {"reasons": ["PRIVATE_TITLE"]}, None),
            ("2026-10-04", None, None, None),
            ("2026-10-05", accepted, None, True),
            ("2026-10-06", accepted, None, False),
        ]:
            await conn.execute(
                insert(obs_batches).values(
                    id=day,
                    channel="trends",
                    mode="shadow",
                    collect_mode="canary1",
                    target_date=day,
                    collector_version="test",
                    started_at=day + "T00:00:00.000000+00:00",
                    outcome="failed",
                    requests=1,
                    plan_json={"notes": {"admission": plan, "late_admission": late}},
                    summary_json={"admission": summary},
                    status_codes_json=["canary_terminated"],
                )
            )
        await conn.execute(insert(obs_budget).values(channel="trends", budget_day="2026-10-01", requests=3, cap=220, http_429=1, breaker_trips=2))
        for day in ("2026-10-01", "2026-10-02", "2026-10-04", "2026-10-05"):
            await conn.execute(
                insert(obs_requests).values(
                    channel="trends",
                    batch_id=day,
                    budget_day=day,
                    budget_item="collect",
                    endpoint="timeline",
                    sent_at=day + "T00:00:01.000000+00:00",
                    status_code=429,
                    fetch_status="rate_limited",
                    identity="PRIVATE_TITLE",
                    error="PRIVATE_SECRET",
                )
            )
    before = await snapshot(url, TABLES)
    result = await report(url)
    days = {day["date"]: day for day in result["days"]}
    assert days["2026-10-03"]["evidence"] == "missing"
    assert days["2026-10-03"]["load_and_request_valid"] is None
    assert days["2026-10-01"]["load_and_request_valid"] is True
    assert days["2026-10-02"]["load_and_request_valid"] is False
    assert days["2026-10-04"]["load_and_request_valid"] is None
    assert days["2026-10-05"]["load_and_request_valid"] is True
    assert days["2026-10-05"]["batches"][0]["late_admission"] is True
    assert days["2026-10-06"]["load_and_request_valid"] is False
    assert days["2026-10-01"]["budget"]["requests"] == 3
    assert days["2026-10-01"]["recorded_requests"] == 1
    assert days["2026-10-01"]["recorded_http_429"] == 1
    assert "PRIVATE" not in json.dumps(result)
    assert result["qualification"] == "not_evaluated"
    assert await snapshot(url, TABLES) == before


@pytest.mark.asyncio
async def test_old_records_do_not_hide_missing_recent_days(pick_db_url, tmp_path):
    url = await migrated(pick_db_url, tmp_path)
    async with open_db(url).transaction() as conn:
        await conn.execute(insert(obs_budget).values(channel="trends", budget_day="2026-09-01", requests=10))
        await conn.execute(insert(obs_budget).values(channel="gsc", budget_day="2026-10-06", requests=20))
    result = await report(url)
    assert result["anchor_date"] == "2026-10-06"
    assert result["latest_recorded_date"] == "2026-09-01"
    assert result["latest_recorded_age_days"] == 35
    assert result["days"][0]["date"] == "2026-09-30"
    assert all(day["recorded_requests"] is None for day in result["days"])


@pytest.mark.asyncio
async def test_unfinished_batch_and_mismatched_request_do_not_establish_valid_night(pick_db_url, tmp_path):
    url = await migrated(pick_db_url, tmp_path)
    async with open_db(url).transaction() as conn:
        await conn.execute(
            insert(obs_batches).values(
                id="active",
                channel="trends",
                mode="shadow",
                collect_mode="canary1",
                target_date="2026-10-06",
                collector_version="test",
                started_at="2026-10-06T00:00:00.000000+00:00",
                outcome="running",
                requests=4,
                plan_json={"notes": {"admission": {"reasons": []}}},
                status_codes_json=[],
            )
        )
        await conn.execute(
            insert(obs_requests).values(
                channel="trends",
                batch_id="unrelated",
                budget_day="2026-10-06",
                budget_item="collect",
                endpoint="timeline",
                sent_at="2026-10-06T00:00:01.000000+00:00",
            )
        )
    day = (await report(url))["days"][-1]
    assert day["recorded_requests"] == 1
    assert day["batches"][0]["recorded_requests"] == 0
    assert day["load_and_request_valid"] is None
