"""TR-10: status codes to banner levels, with the time-based ones (plan D10; design 3.7, 4.10; contract section 13).

tests/fixtures/obs_status_cases.json is the shared truth: the data page's TS copy reproduces the same cases.
"""

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ggwork_pick.observe import contract
from ggwork_pick.observe.contract_api import ObsBanner
from ggwork_pick.observe.status_rules import (
    GSC_MISSED_AFTER,
    STATUS_LEVELS,
    STATUS_TEXT,
    TRENDS_DUE_AT,
    TRENDS_STALE_AFTER,
    LatestRun,
    channel_banners,
    run_missed,
    trends_due_date,
    trends_set_stale,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "obs_status_cases.json"


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _at(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _banners(case: dict) -> list[dict]:
    run = case["latest_run"]
    banners = channel_banners(
        case["channel"],
        latest_run=None if run is None else LatestRun.from_mapping(case["channel"], run),
        live_published_at=case["live_published_at"],
        now=_at(case["now"]),
    )
    assert all(isinstance(banner, ObsBanner) for banner in banners)
    return [banner.model_dump() for banner in banners]


def test_status_cases_fixture():
    fixture = _load()
    for case in fixture["cases"]:
        assert _banners(case) == case["expected"], case["name"]
    names = [case["name"] for case in fixture["cases"]]
    assert len(names) == len(set(names))
    # Every code shows up in some case, so the TS copy is checked against each level at least once.
    shown = {banner["code"] for case in fixture["cases"] for banner in case["expected"]}
    assert shown == set(contract.STATUS_CODES)


def test_status_tables_match_fixture():
    fixture = _load()
    assert fixture["levels"] == dict(STATUS_LEVELS)
    assert fixture["texts"] == dict(STATUS_TEXT)
    assert tuple(STATUS_LEVELS) == contract.STATUS_CODES == tuple(STATUS_TEXT)
    assert set(STATUS_LEVELS.values()) == set(contract.BANNER_LEVELS)
    params = fixture["params"]
    assert TRENDS_STALE_AFTER == timedelta(hours=params["trends_stale_hours"])
    assert TRENDS_DUE_AT.strftime("%H:%M") == params["trends_due_utc"]
    assert GSC_MISSED_AFTER == timedelta(hours=params["gsc_missed_hours"])


def test_levels_follow_what_the_banner_means():
    """Red: nothing fresh is being published, or collection has stopped. Warn: published, with degraded quality. Info: by design."""
    red = {code for code, level in STATUS_LEVELS.items() if level == "red"}
    assert {"stale_26h", "not_published_low_coverage", "extinguished_today", "disabled_7d", "canary_terminated", "run_missed"} <= red
    assert {"legacy_snapshot_missing", "db_size_cap"} <= red
    assert STATUS_LEVELS["shadow_mode"] == "info"
    assert {code for code, level in STATUS_LEVELS.items() if level == "warn"} == {
        "usertype_changed", "all_zero_jump", "legacy_unmapped_2pct", "gsc_gap_exceeded", "gsc_unverifiable",
    }  # fmt: skip


def test_trends_stale_boundary():
    published = "2026-09-25T01:52:10.000000+00:00"
    assert not trends_set_stale(published, _at(published) + TRENDS_STALE_AFTER)
    assert trends_set_stale(published, _at(published) + TRENDS_STALE_AFTER + timedelta(microseconds=1))
    assert not trends_set_stale(None, _at(published) + timedelta(days=30))


def test_trends_due_date():
    assert trends_due_date(datetime(2026, 9, 26, 2, 30, tzinfo=UTC)) == date(2026, 9, 26)
    assert trends_due_date(datetime(2026, 9, 26, 2, 29, 59, 999999, tzinfo=UTC)) == date(2026, 9, 25)
    # A clock in another zone is read in UTC: 10:30 in Shanghai is 02:30 UTC.
    shanghai = datetime.fromisoformat("2026-09-26T10:30:00+08:00")
    assert trends_due_date(shanghai) == date(2026, 9, 26)


def test_run_missed_boundaries():
    trends = LatestRun.from_mapping(
        "trends", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "live", "target_date": "2026-09-25", "status_codes": []}
    )
    assert not run_missed("trends", trends, datetime(2026, 9, 26, 2, 29, tzinfo=UTC))
    assert run_missed("trends", trends, datetime(2026, 9, 26, 2, 30, tzinfo=UTC))
    assert not run_missed("trends", None, datetime(2026, 9, 30, tzinfo=UTC))
    gsc = LatestRun.from_mapping("gsc", {"started_at": "2026-09-25T03:25:02.000000+00:00", "mode": "live", "target_date": None, "status_codes": []})
    assert not run_missed("gsc", gsc, gsc.started_at + GSC_MISSED_AFTER)
    assert run_missed("gsc", gsc, gsc.started_at + GSC_MISSED_AFTER + timedelta(microseconds=1))


@pytest.mark.parametrize(
    ("channel", "run", "message"),
    [
        ("trends", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "live", "target_date": None, "status_codes": []}, "target_date"),
        ("gsc", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "live", "target_date": "2026-09-25", "status_codes": []}, "target_date"),
        ("trends", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "live", "target_date": "2026-09-25", "status_codes": ["stale"]}, "status"),
        ("trends", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "both", "target_date": "2026-09-25", "status_codes": []}, "mode"),
        ("trends", {"started_at": "2026-09-24T20:30:05", "mode": "live", "target_date": "2026-09-25", "status_codes": []}, "时区"),
        ("web", {"started_at": "2026-09-24T20:30:05.000000+00:00", "mode": "live", "target_date": None, "status_codes": []}, "channel"),
    ],
)
def test_latest_run_refuses_malformed_rows(channel, run, message):
    with pytest.raises(ValueError, match=message):
        LatestRun.from_mapping(channel, run)


def test_latest_run_built_directly_is_checked_too():
    """A LatestRun built without from_mapping names its channel and is checked the same way, so a Trends run without a
    target date is refused up front instead of failing inside run_missed."""
    started = datetime(2026, 9, 24, 20, 30, tzinfo=UTC)
    trends = LatestRun(channel="trends", started_at=started, mode="live", target_date=date(2026, 9, 25), status_codes=())
    assert trends.channel == "trends"
    malformed = [
        ({"channel": "trends", "target_date": None}, "target_date"),
        ({"channel": "gsc", "target_date": date(2026, 9, 25)}, "target_date"),
        ({"channel": "web"}, "channel"),
        ({"mode": "both"}, "mode"),
        ({"status_codes": ("stale",)}, "status code"),
        ({"status_codes": ["shadow_mode"]}, "元组"),
        ({"started_at": datetime(2026, 9, 24, 20, 30)}, "时区"),
        ({"started_at": "2026-09-24T20:30:00+00:00"}, "datetime"),
        ({"target_date": "2026-09-25"}, "date"),
    ]
    fields = {"channel": "trends", "started_at": started, "mode": "live", "target_date": date(2026, 9, 25), "status_codes": ()}
    for change, message in malformed:
        with pytest.raises(ValueError, match=message):
            LatestRun(**{**fields, **change})


def test_banners_refuse_another_channels_run():
    gsc = LatestRun.from_mapping("gsc", {"started_at": "2026-09-25T03:25:02.000000+00:00", "mode": "live", "target_date": None, "status_codes": []})
    assert gsc.channel == "gsc"
    with pytest.raises(ValueError, match="channel"):
        channel_banners("trends", latest_run=gsc, live_published_at=None, now=datetime(2026, 9, 25, 4, tzinfo=UTC))
    with pytest.raises(ValueError, match="channel"):
        run_missed("trends", gsc, datetime(2026, 9, 25, 4, tzinfo=UTC))


def test_naive_now_refused():
    with pytest.raises(ValueError, match="时区"):
        channel_banners("gsc", latest_run=None, live_published_at=None, now=datetime(2026, 9, 25, 4))


def test_unknown_channel_refused():
    with pytest.raises(ValueError, match="channel"):
        channel_banners("web", latest_run=None, live_published_at=None, now=datetime(2026, 9, 25, 4, tzinfo=UTC))
