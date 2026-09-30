"""GET /api/pick/obs/trends-table (simplified scope 2026-09-30, section 2 and section 6 items 4 and 6), on both dialects.

The rows come from real stable nights: the collector's entry runs on the gateway's own database (ManualClock, FakeGoogle),
then the gateway reads the table. What is checked: the table is the newest night's task list in its order, a drama the
night did not get shows "not fetched" and never an older night's curve, a day without data is null, the banners are
/sync's without shadow_mode, and a read that fails is a 503 naming nothing.
"""

import json
import logging
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from obs_db_helpers import execute, migrated
from pydantic import TypeAdapter
from test_frontend_contract import _shape
from top_dramas_fixtures import board_drama
from trends_fake_google import FakeGoogle
from trends_session_helpers import EVE, TARGET, at, batches, seed_catalog, trends_env, trigger

from ggwork_pick.observe import trends_table as table_module
from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.trends import top_dramas as top
from ggwork_pick.observe.trends.admission import STRICT
from ggwork_pick.observe.trends.units import QueryUnit, SessionPlan, TaskList, unit_key
from ggwork_pick.observe.trends_table import TrendsTable, series_of, table_of, trends_table
from ggwork_pick.repository import PickRepository

ALICE = {"test-owner": "alice"}
TABLE = TypeAdapter(TrendsTable)
STABLE = {"PICK_OBS_TRENDS_GRANULARITY": "D", "PICK_OBS_TRENDS_ROUTE": "a_only"}
TITLES = ["Alpha Bride", "Second Chance", "Zero Interest", "Fourth Wall", "Fifth Night"]
# The frontend's trends-table-schema parses this answer (frontend/tests/unit/core/pick/trends-table-contract.test.ts).
# After a shape change, regenerate with PICK_WRITE_CONTRACT=1 and this test.
TABLE_FIXTURE = Path(__file__).resolve().parents[4] / "frontend/tests/unit/core/pick/fixtures/backend-trends-table.json"


@pytest_asyncio.fixture
async def world(app_client, pick_db_url, tmp_path):
    """The gateway's router and the collector on one database."""
    client, service = app_client
    url = await migrated(pick_db_url, tmp_path)
    await seed_catalog(url, [board_drama(index, title=title, boards={"qc": index}) for index, title in enumerate(TITLES, start=1)])
    return client, service, url


KEY = Fernet.generate_key().decode()  # one state key for every night of a test: the state stays readable


async def _night(url: str, tmp_path, google: FakeGoogle, clock: ManualClock) -> int:
    env = trends_env(url, mode="stable", key=KEY, **STABLE)
    return await trigger(env, clock, google, controls=tmp_path / "none.json", admission=STRICT)


async def _table(service, now: datetime) -> dict:
    return await trends_table(PickRepository.shared(service.session_factory), now=now)


def _by_title(table: dict) -> dict[str, dict]:
    return {row["title"]: row for row in table["rows"]}


# ---- the route --------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_table_needs_a_signed_in_user_and_is_empty_before_any_stable_night(app_client):
    client, _ = app_client
    assert (await client.get("/api/pick/obs/trends-table")).status_code == 401
    response = await client.get("/api/pick/obs/trends-table", headers=ALICE)
    assert response.status_code == 200
    body = TABLE.validate_python(response.json()).model_dump(mode="json")
    assert (body["batch"], body["rows"], body["banners"], body["truncated"]) == (None, [], [], False)
    assert body["row_limit"] == table_module.ROW_LIMIT


@pytest.mark.asyncio
async def test_a_failed_read_is_a_503_naming_nothing(app_client, monkeypatch, caplog):
    client, _ = app_client

    async def broken(repo, *, now):
        raise RuntimeError("secret value in the message")

    monkeypatch.setattr("ggwork_pick.routes.trends_table", broken)
    with caplog.at_level(logging.WARNING):
        response = await client.get("/api/pick/obs/trends-table", headers=ALICE)
    assert response.status_code == 503 and "secret" not in response.text
    assert "RuntimeError" in caplog.text and "secret value" not in caplog.text


# ---- a night, as the table shows it -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_table_is_the_nights_task_list(world, tmp_path):
    """Every pick in its order with its basis; data, no data (a series of zeros) and not fetched (a 429 lost the unit)
    kept apart; the counts add up; the curve's last day is partial; no shadow_mode banner."""
    client, service, url = world
    clock = ManualClock(at(EVE, 17, 30))
    # warm-up 1, then two requests a unit: the 4th unit's multiline is request 9
    google = FakeGoogle(clock, zero=["Zero Interest"], script={9: "429"})
    assert await _night(url, tmp_path, google, clock) == ExitCode.OK
    response = await client.get("/api/pick/obs/trends-table", headers=ALICE)
    body = TABLE.validate_python(response.json()).model_dump(mode="json")
    assert [row["title"] for row in body["rows"]] == TITLES and [row["order"] for row in body["rows"]] == [1, 2, 3, 4, 5]
    rows = _by_title(body)
    assert rows["Alpha Bride"]["basis"] == [{"kind": "qc", "board_date": "2026-09-24", "rank": 1, "identity": None}]
    assert (rows["Alpha Bride"]["term"], rows["Alpha Bride"]["geo"], rows["Alpha Bride"]["time_range"]) == ("Alpha Bride", "WW", "today 1-m")
    assert (rows["Alpha Bride"]["result"], rows["Alpha Bride"]["status"]) == ("data", "ok")
    assert (rows["Zero Interest"]["result"], rows["Zero Interest"]["status"]) == ("no_data", "ok_zero")
    assert (rows["Fourth Wall"]["result"], rows["Fourth Wall"]["status"], rows["Fourth Wall"]["series"]) == ("not_fetched", "rate_limited", None)
    series = rows["Alpha Bride"]["series"]
    assert len(series) == 30 and series[-1]["partial"] and not series[0]["partial"] and all(point["value"] is not None for point in series)
    assert [point["date"] for point in series] == sorted(point["date"] for point in series)
    header = body["batch"]
    assert (header["target_date"], header["collect_mode"], header["outcome"]) == (f"{TARGET:%Y-%m-%d}", "stable", "withheld")
    assert header["counts"] == {"planned": 5, "data": 3, "no_data": 1, "not_fetched": 1, "pending": 0}
    assert header["sources"]["boards"][0] == {"kind": "qc", "board_date": "2026-09-24", "listed": 5}
    assert header["window_end"] == "2026-09-25T00:00:00.000000+00:00"
    # The route reads the wall clock (long past this night: run_missed and stale); at the night's own morning the
    # latest run is shadow, which /sync shows as shadow_mode and the table does not.
    assert [banner["code"] for banner in (await _table(service, at(TARGET, 3)))["banners"]] == []
    assert {banner["code"] for banner in body["banners"]} <= {"run_missed", "stale_26h"}


@pytest.mark.asyncio
async def test_a_drama_the_night_did_not_get_never_shows_an_older_curve(world, tmp_path):
    """Night one fetches everything; night two loses one drama to a 429 and dies before the last one. The table is
    night two's: the lost one is not fetched and the unreached one is pending before 01:45, not fetched after it, never
    night one's curve. Before 02:30 the table is not stale; after it, with night two still running, it is."""
    client, service, url = world
    clock = ManualClock(at(EVE, 17, 30))
    assert await _night(url, tmp_path, FakeGoogle(clock), clock) == ExitCode.OK
    clock.advance((at(TARGET, 17, 30) - clock.now()).total_seconds())

    class Crash(RuntimeError):
        pass

    def crash(ordinal: int, phase: str) -> None:
        if ordinal == 10:  # the 5th unit's explore
            raise Crash("synthetic crash")

    second = FakeGoogle(clock, script={5: "429"}, on_request=crash)
    assert await _night(url, tmp_path, second, clock) == ExitCode.FAILED
    rows = _by_title(await _table(service, clock.now()))
    assert (rows["Second Chance"]["result"], rows["Second Chance"]["series"]) == ("not_fetched", None)
    assert (rows["Fifth Night"]["result"], rows["Fifth Night"]["status"], rows["Fifth Night"]["series"]) == ("pending", None, None)
    assert rows["Alpha Bride"]["result"] == "data"
    later = await _table(service, datetime.combine(TARGET + timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=2))
    assert (_by_title(later)["Fifth Night"]["result"], _by_title(later)["Fifth Night"]["status"]) == ("not_fetched", "not_reached")
    assert later["batch"]["target_date"] == f"{TARGET + timedelta(days=1):%Y-%m-%d}" and later["batch"]["outcome"] == "running"
    assert [banner["code"] for banner in later["banners"]] == []  # 02:00: night one is the newest finished, and due
    due = await _table(service, datetime.combine(TARGET + timedelta(days=1), datetime.min.time(), UTC) + timedelta(hours=2, minutes=30))
    assert [banner["code"] for banner in due["banners"]] == ["stale_26h"]


@pytest.mark.asyncio
async def test_canary_and_refused_nights_are_not_the_table(world, tmp_path):
    """A canary batch and a refusal row are not table batches: the newest stable night stays the table."""
    client, service, url = world
    clock = ManualClock(at(EVE, 17, 30))
    assert await _night(url, tmp_path, FakeGoogle(clock), clock) == ExitCode.OK
    await execute(
        url,
        "insert into ggwp_obs_batches (id, channel, mode, collect_mode, target_date, collector_version, started_at, finished_at, outcome, status_codes_json)"
        " values ('refused', 'trends', 'shadow', 'stable', :d, 'obs-test', :s, :s, 'failed', :c)",
        d=f"{TARGET + timedelta(days=1):%Y-%m-%d}",
        s="2026-09-26T17:30:02.000000+00:00",
        c='["disabled_7d"]',
    )
    table = await _table(service, datetime(2026, 9, 27, 1, 0, tzinfo=UTC))
    assert table["batch"]["target_date"] == f"{TARGET:%Y-%m-%d}" and len(table["rows"]) == 5
    assert [banner["code"] for banner in table["banners"]] == ["disabled_7d"]
    (first, _) = await batches(url)
    assert first["collect_mode"] == "stable"


@pytest.mark.asyncio
async def test_the_table_matches_the_frontend_fixture(world, tmp_path):
    """The real answer the frontend parses: a finished night with data, no data and not fetched rows, read the next
    morning when no night ran (a run_missed banner). The fixture pins every key and value type."""
    _, service, url = world
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock, zero=["Zero Interest"], script={9: "429"})
    assert await _night(url, tmp_path, google, clock) == ExitCode.OK
    answer = await _table(service, at(TARGET + timedelta(days=1), 3))
    assert [banner["code"] for banner in answer["banners"]] == ["run_missed"]
    assert [row["result"] for row in answer["rows"]] == ["data", "data", "no_data", "not_fetched", "data"]
    assert answer["batch"]["finished_at"] is not None and answer["batch"]["sources"]["revenue"] is not None
    if os.environ.get("PICK_WRITE_CONTRACT"):
        TABLE_FIXTURE.write_text(json.dumps(answer, ensure_ascii=False, indent=2) + "\n")
    assert _shape(json.loads(TABLE_FIXTURE.read_text())) == _shape(answer)


# ---- the pure parts ---------------------------------------------------------------------------------------------------


def test_series_keeps_a_day_without_data_null_and_marks_incomplete_days():
    day = 86400
    start = int(datetime(2026, 9, 22, tzinfo=UTC).timestamp())
    data = {
        "time": [str(start + index * day) for index in range(4)],
        "value": [10, 0, 30, 40],
        "isPartial": [False, False, False, True],
        "hasData": [True, False, True, True],
    }
    points = series_of(data, date(2026, 9, 24))
    assert points == [
        {"date": "2026-09-22", "value": 10, "partial": False},
        {"date": "2026-09-23", "value": None, "partial": False},
        {"date": "2026-09-24", "value": 30, "partial": True},  # on window_end's day: not a complete day of this batch
        {"date": "2026-09-25", "value": 40, "partial": True},
    ]
    with pytest.raises(ValueError):
        series_of({"time": ["1"], "value": [101]}, date(2026, 9, 24))
    with pytest.raises(ValueError):
        series_of({"time": ["1", "2"], "value": [1]}, date(2026, 9, 24))


def test_units_cut_from_the_plan_are_rows_too():
    """A unit truncated from the plan is on the night's list: a row, not fetched, after the planned ones."""
    picks, _ = top.merged([top.Candidate(f"id-{n}", f"Title {n}", "DramaBox", "en", top.Basis("qc", date(2026, 9, 24), n)) for n in (1, 2)])
    units = [top.query_unit(pick, order) for order, pick in enumerate(picks, start=1)]
    check = QueryUnit(unit_key("contract_check", "x"), "contract_check", "US", ("short drama",), "short drama", "H", 0)
    notes = {
        "top_dramas": {
            "picks": {unit.key: pick.to_note(order) for order, (unit, pick) in enumerate(zip(units, picks, strict=True), start=1)},
            "boards": [],
            "revenue": None,
        }
    }
    plan = SessionPlan("top_dramas", "D", False, TaskList((check, units[0]), (units[1],)), "cat-1", notes)
    batch = {
        "id": "b",
        "target_date": "2026-09-26",
        "collect_mode": "stable",
        "outcome": "withheld",
        "started_at": "2026-09-25T17:30:00.000000+00:00",
        "finished_at": "2026-09-25T19:00:00.000000+00:00",
        "window_end": "2026-09-25T00:00:00.000000+00:00",
        "plan_json": plan.to_dict(),
        "summary_json": {"units": {}, "uncovered_units": []},
    }
    header, rows, cut = table_of(batch, {}, datetime(2026, 9, 26, 3, 0, tzinfo=UTC))
    assert [(row["title"], row["result"], row["status"]) for row in rows] == [
        ("Title 1", "not_fetched", "not_reached"),
        ("Title 2", "not_fetched", "truncated"),
    ]
    assert header["counts"]["planned"] == 2 and cut is False and header["catalog_batch_id"] == "cat-1"
