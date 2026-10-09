import csv
import io

import pytest
from test_radar_snapshot import record

from ggwork_pick.radar.snapshot import Snapshot, normalize_record


@pytest.mark.asyncio
async def test_historical_routes_require_auth_and_preserve_filters_export_and_detail(app_client, monkeypatch):
    from ggwork_pick.radar.routes import SnapshotReader

    client, _ = app_client
    rows = []
    for i in range(1, 54):
        raw = record([10] * 7 + [20] * 7)
        raw.update(id=i, original_title=f"Synthetic {i:02d}")
        row = normalize_record(raw, None)
        row.update(snapshot_id="synthetic", is_older_window=False, score_differences={})
        rows.append(row)
    raw = record([])
    raw.update(id=54, original_title="=Synthetic missing", avg_heat=5, peak_heat=10, momentum=0)
    missing = normalize_record(raw, None)
    missing.update(snapshot_id="synthetic", is_older_window=None, score_differences={})
    rows.append(missing)
    snap = Snapshot(rows, {"snapshot_id": "synthetic"}, {"catalog_total": 54}, {})
    monkeypatch.setattr(SnapshotReader, "get", lambda self: snap)
    base = "/api/pick/radar"
    for path in ["/stats", "/dramas", "/dramas/1", "/export"]:
        assert (await client.get(base + path)).status_code == 401
    headers = {"test-owner": "alice"}
    page = (await client.get(base + "/dramas", headers=headers)).json()
    assert page["total"] == 54 and len(page["items"]) == 50
    assert "timeline_data" not in page["items"][0]
    second = (await client.get(base + "/dramas?offset=50", headers=headers)).json()
    assert len(second["items"]) == 4
    detail = (await client.get(base + "/dramas/1", headers=headers)).json()
    assert detail["pilot"]["growth_pct"] == 100
    assert len(detail["timeline_data"]) == 14
    filtered = (await client.get(base + "/dramas?tier=unrated&platform=ReelShort&search=missing", headers=headers)).json()
    assert filtered["total"] == 1 and filtered["items"][0]["pilot"]["score"] is None
    exported = await client.get(base + "/export", headers=headers)
    assert len(list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))) == 54
    exported = await client.get(base + "/export?tier=unrated", headers=headers)
    result = list(csv.DictReader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert result[0]["pilot_score"] == "" and result[0]["original_title"].startswith("'=")
    assert (await client.get(base + "/dramas?series_end=not-a-date", headers=headers)).status_code == 422
    assert (await client.post(base + "/dramas", headers=headers)).status_code == 405


@pytest.mark.asyncio
async def test_missing_historical_file_is_explicitly_unavailable(app_client):
    client, _ = app_client
    response = await client.get("/api/pick/radar/stats", headers={"test-owner": "alice"})
    assert response.status_code == 503
    assert "/" not in response.json()["detail"]
