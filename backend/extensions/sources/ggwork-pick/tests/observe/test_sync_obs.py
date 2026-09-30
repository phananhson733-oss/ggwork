"""TR-25: /api/pick/sync's obs key (plan D10; design 3.7; contract section 13). Synthetic rows only.

The gateway reads each channel's current live set, its latest set of either mode and its latest run straight from the
0007 tables (the pick_obs views are the reader's), and status_rules turns them into banners at request time. Before the
crons run, which is production today, every field is empty and there is no banner: a channel that never ran is not
"missed", and nothing is shown as zero.
"""

import json
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from obs_status_rows import batch_row, insert_rows, set_id, set_row, table_batch_row
from pydantic import TypeAdapter
from sqlalchemy import delete

from ggwork_pick.models import obs_batches, obs_sets
from ggwork_pick.observe.contract_api import ObsSyncError, ObsSyncStatus, SyncObs
from ggwork_pick.observe.status import obs_status
from ggwork_pick.observe.status_rules import LatestRun, channel_banners
from ggwork_pick.repository import SHARED_OWNER, PickRepository

CASES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_status_cases.json"
SYNC_OBS = TypeAdapter(SyncObs)
NOW = datetime(2026, 9, 25, 4, 10, tzinfo=UTC)
EMPTY_CHANNEL = {
    "live_set_id": None,
    "live_published_at": None,
    "latest_set_id": None,
    "latest_published_at": None,
    "latest_mode": None,
    "last_run_at": None,
    "banners": [],
}


async def _insert(service, *, sets=(), batches=()) -> None:
    await insert_rows(service.session_factory, sets=sets, batches=batches)


async def _clear(service) -> None:
    async with service.session_factory() as session, session.begin():
        await session.execute(delete(obs_sets))
        await session.execute(delete(obs_batches))


def _shared(service) -> PickRepository:
    return PickRepository.shared(service.session_factory)


def _channel(obs: dict, name: str) -> dict:
    return next(channel for channel in obs["channels"] if channel["channel"] == name)


@pytest_asyncio.fixture
async def service(app_client):
    _, opened = app_client
    return opened


# ---------------------------------------------------------------- 1. the key on /sync, and the empty state before any run


@pytest.mark.asyncio
async def test_sync_obs_key_shape(app_client):
    """Production today: 0007 is there, the crons are not. Both channels, every field empty, no banner."""
    client, _ = app_client
    before = datetime.now(UTC)
    response = await client.get("/api/pick/sync", headers={"test-owner": "alice"})
    after = datetime.now(UTC)
    assert response.status_code == 200
    body = response.json()
    assert {"configured", "current", "runs", "mirror", "obs"} <= set(body)
    parsed = SYNC_OBS.validate_python(body["obs"])
    assert isinstance(parsed, ObsSyncStatus)
    assert [channel["channel"] for channel in body["obs"]["channels"]] == ["trends", "gsc"]
    for channel in body["obs"]["channels"]:
        assert {key: value for key, value in channel.items() if key != "channel"} == EMPTY_CHANNEL
    checked = datetime.fromisoformat(body["obs"]["checked_at"])
    assert before - timedelta(seconds=1) <= checked <= after + timedelta(seconds=1)


@pytest.mark.asyncio
async def test_sync_obs_needs_a_signed_in_user(app_client):
    client, _ = app_client
    assert (await client.get("/api/pick/sync")).status_code == 401


# ---------------------------------------------------------------- 2. which rows it reads


@pytest.mark.asyncio
async def test_obs_status_reads_the_latest_rows(service):
    await _insert(
        service,
        sets=[
            set_row(1, "trends", "live", "2026-09-24T01:50:00.000000+00:00"),
            set_row(2, "trends", "live", "2026-09-25T01:52:10.000000+00:00"),
            set_row(3, "trends", "live", "2026-09-25T01:52:10.000000+00:00"),  # same moment, larger id: the current one
            set_row(4, "trends", "shadow", "2026-09-25T02:10:00.000000+00:00"),
            set_row(5, "trends", "live", "2026-09-25T03:00:00.000000+00:00", status="pruned"),  # pruned sets never count
            set_row(6, "gsc", "shadow", "2026-09-25T03:31:40.000000+00:00"),
        ],
        batches=[
            batch_row("t-24", "trends", "live", "2026-09-23T20:30:05.000000+00:00", target_date="2026-09-24", codes=["disabled_7d"]),
            batch_row("t-25", "trends", "shadow", "2026-09-24T20:30:05.000000+00:00", target_date="2026-09-25", codes=[]),
            batch_row("g-1", "gsc", "live", "2026-09-25T00:25:04.000000+00:00", target_date=None, codes=["db_size_cap"]),
            batch_row("g-2", "gsc", "shadow", "2026-09-25T03:25:04.000000+00:00", target_date=None, codes=["gsc_unverifiable"]),
        ],
    )
    obs = await obs_status(_shared(service), now=NOW)
    assert SYNC_OBS.validate_python(obs) == ObsSyncStatus.model_validate(obs)
    assert obs["checked_at"] == "2026-09-25T04:10:00.000000+00:00"
    trends, gsc = _channel(obs, "trends"), _channel(obs, "gsc")
    assert (trends["live_set_id"], trends["live_published_at"]) == (set_id(3), "2026-09-25T01:52:10.000000+00:00")
    assert (trends["latest_set_id"], trends["latest_mode"]) == (set_id(4), "shadow")
    assert trends["last_run_at"] == "2026-09-24T20:30:05.000000+00:00"
    # An older run's code never lingers: disabled_7d was t-24's, the latest run is t-25.
    assert trends["banners"] == [{"code": "shadow_mode", "level": "info"}]
    assert (gsc["live_set_id"], gsc["live_published_at"]) == (None, None)
    assert (gsc["latest_set_id"], gsc["latest_published_at"], gsc["latest_mode"]) == (set_id(6), "2026-09-25T03:31:40.000000+00:00", "shadow")
    assert gsc["last_run_at"] == "2026-09-25T03:25:04.000000+00:00"
    assert gsc["banners"] == [{"code": "gsc_unverifiable", "level": "warn"}, {"code": "shadow_mode", "level": "info"}]


@pytest.mark.asyncio
async def test_obs_status_is_the_shared_status(service):
    """The same answer whoever asks; the owner's own imports have nothing to do with it."""
    with pytest.raises(ValueError, match="共享"):
        await obs_status(PickRepository(service.session_factory, "alice"), now=NOW)
    assert _shared(service).owner_id == SHARED_OWNER


@pytest.mark.asyncio
async def test_obs_status_refuses_a_naive_now(service):
    with pytest.raises(ValueError):
        await obs_status(_shared(service), now=NOW.replace(tzinfo=None))


# ---------------------------------------------------------------- 3. every banner case through the database


@pytest.mark.asyncio
async def test_obs_status_follows_every_status_case(service):
    """obs_status_cases.json, the fixture the data page's TS copy also reproduces, stored as rows and read back."""
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    for number, case in enumerate(cases, start=1):
        await _clear(service)
        channel, run = case["channel"], case["latest_run"]
        sets = [] if case["live_published_at"] is None else [set_row(number, channel, "live", case["live_published_at"])]
        batches = (
            []
            if run is None
            else [batch_row(f"b-{number}", channel, run["mode"], run["started_at"], target_date=run["target_date"], codes=run["status_codes"])]
        )
        through = case.get("table_through")
        if through is not None:  # the newest finished table batch, older than the latest run or that run itself
            batches = _with_table_batch(batches, number, through)
        await _insert(service, sets=sets, batches=batches)
        obs = await obs_status(_shared(service), now=datetime.fromisoformat(case["now"]))
        assert _channel(obs, channel)["banners"] == case["expected"], case["name"]
        other = "gsc" if channel == "trends" else "trends"
        assert _channel(obs, other)["banners"] == [], case["name"]
        expected = channel_banners(
            channel,
            latest_run=None if run is None else LatestRun.from_mapping(channel, run),
            live_published_at=case["live_published_at"],
            now=datetime.fromisoformat(case["now"]),
            table_through=None if through is None else date.fromisoformat(through),
        )
        assert _channel(obs, channel)["banners"] == [banner.model_dump() for banner in expected], case["name"]


def _with_table_batch(batches: list[dict], number: int, through: str) -> list[dict]:
    """The case's latest run as the finished table batch of `through` when it is that date's, else a finished table
    batch of `through` started a day earlier."""
    run = batches[0]
    if run["target_date"] == through:
        finished = table_batch_row(run["id"], through, run["started_at"], finished_at=run["started_at"], codes=run["status_codes_json"])
        return [{**finished, "mode": run["mode"]}]
    started = (datetime.fromisoformat(run["started_at"]) - timedelta(days=1)).isoformat(timespec="microseconds")
    return [table_batch_row(f"table-{number}", through, started, finished_at=started), *batches]


@pytest.mark.asyncio
async def test_table_through_reads_only_finished_planned_stable_batches(service):
    """The stale banner's date is the newest finished, planned, stable-mode Trends batch: a canary night, a refusal row
    (no window_end) and a night still running do not move it."""
    await _insert(
        service,
        batches=[
            table_batch_row("s-24", "2026-09-24", "2026-09-23T17:30:02.000000+00:00", finished_at="2026-09-23T19:40:00.000000+00:00"),
            batch_row(
                "c-25",
                "trends",
                "shadow",
                "2026-09-24T21:00:02.000000+00:00",
                target_date="2026-09-25",
                codes=[],
                collect_mode="canary1",
                window_end="2026-09-24T18:00:00.000000+00:00",
                finished_at="2026-09-24T23:00:00.000000+00:00",
            ),
            table_batch_row("s-26", "2026-09-26", "2026-09-25T17:30:02.000000+00:00", finished_at=None),
            batch_row(
                "r-27",
                "trends",
                "shadow",
                "2026-09-26T17:30:02.000000+00:00",
                target_date="2026-09-27",
                codes=["disabled_7d"],
                collect_mode="stable",
                finished_at="2026-09-26T17:30:02.000000+00:00",
                outcome="failed",
            ),
        ],
    )
    obs = await obs_status(_shared(service), now=datetime(2026, 9, 27, 4, 0, tzinfo=UTC))
    assert _channel(obs, "trends")["banners"] == [
        {"code": "stale_26h", "level": "red"},
        {"code": "disabled_7d", "level": "red"},
        {"code": "shadow_mode", "level": "info"},
    ]
    await _clear(service)
    await _insert(
        service,
        batches=[
            table_batch_row("s-25", "2026-09-25", "2026-09-24T17:30:02.000000+00:00", finished_at="2026-09-24T19:40:00.000000+00:00"),
            table_batch_row("s-26", "2026-09-26", "2026-09-25T17:30:02.000000+00:00", finished_at=None),
        ],
    )
    fresh = await obs_status(_shared(service), now=datetime(2026, 9, 26, 2, 29, tzinfo=UTC))
    assert _channel(fresh, "trends")["banners"] == [{"code": "shadow_mode", "level": "info"}]
    behind = await obs_status(_shared(service), now=datetime(2026, 9, 26, 2, 30, tzinfo=UTC))
    assert _channel(behind, "trends")["banners"] == [{"code": "stale_26h", "level": "red"}, {"code": "shadow_mode", "level": "info"}]


# ---------------------------------------------------------------- 4. a read that fails


@pytest.mark.asyncio
async def test_obs_read_error_names_the_class_only(app_client, monkeypatch, caplog):
    from ggwork_pick import routes

    async def broken(repo, **options):
        raise RuntimeError("synthetic value that must not leak")

    client, _ = app_client
    monkeypatch.setattr(routes, "obs_status", broken)
    with caplog.at_level(logging.WARNING):
        response = await client.get("/api/pick/sync", headers={"test-owner": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert body["obs"] == {"error": "RuntimeError"}
    assert isinstance(SYNC_OBS.validate_python(body["obs"]), ObsSyncError)
    assert "runs" in body and "mirror" in body
    assert "synthetic value" not in caplog.text and "synthetic value" not in response.text


@pytest.mark.asyncio
async def test_a_run_row_the_rules_cannot_read_fails_closed(app_client):
    """A code the contract does not know is not dropped: the banner would silently clear. The key names the class."""
    client, opened = app_client
    await _insert(opened, batches=[batch_row("t-1", "trends", "live", "2026-09-24T20:30:05.000000+00:00", target_date="2026-09-25", codes=["no_such_code"])])
    body = (await client.get("/api/pick/sync", headers={"test-owner": "alice"})).json()
    assert body["obs"] == {"error": "ValueError"}
    assert body["runs"] == []
