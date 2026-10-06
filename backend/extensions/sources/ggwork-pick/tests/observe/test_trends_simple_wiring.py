from datetime import UTC, datetime

import pytest

from ggwork_pick.observe.trends.__main__ import source_for
from ggwork_pick.observe.trends.run import window_end_of
from ggwork_pick.observe.trends.settings import settings_from


def test_stable_daily_reference_source_is_available_without_changing_canary():
    source = source_for(settings_from({"PICK_OBS_TRENDS_MODE": "stable", "PICK_OBS_TRENDS_ROUTE": "a_only", "PICK_OBS_TRENDS_GRANULARITY": "D"}), None)
    assert source.name == "top_dramas"


def test_daily_window_ends_at_utc_midnight_not_hourly_lag():
    assert window_end_of(datetime(2026, 10, 6, 17, 30, tzinfo=UTC), "D") == datetime(2026, 10, 6, tzinfo=UTC)


@pytest.mark.asyncio
async def test_trends_reference_route_requires_owner_and_returns_empty_table(app_client):
    client, _ = app_client
    assert (await client.get("/api/pick/obs/trends-table")).status_code == 401
    response = await client.get("/api/pick/obs/trends-table", headers={"test-owner": "alice"})
    assert response.status_code == 200
    assert response.json()["rows"] == []
