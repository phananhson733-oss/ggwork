"""The stable mode on the simplified radar's task source, through the real entry (simplified scope 2026-09-30, section 6
items 1 to 3): the variables it needs, one night end to end on both dialects, the daily window_end, preflight, and a
night of TARGET_DRAMAS dramas against stable's window at the production pace.

As in test_trends_run, the night passes on a ManualClock against FakeGoogle (MockTransport); what the session wrote is
read back through a separate engine.
"""

import io
import json
from datetime import timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import is_postgres, migrated
from top_dramas_fixtures import board_drama
from trends_fake_google import FakeGoogle
from trends_session_helpers import EVE, TARGET, at, batches, budget_requests, raw_rows, request_rows, seed_catalog, trends_env, trigger

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.trends import budget, capacity, pacing
from ggwork_pick.observe.trends import top_dramas as top
from ggwork_pick.observe.trends.admission import STRICT

STABLE = {"PICK_OBS_TRENDS_GRANULARITY": "D", "PICK_OBS_TRENDS_ROUTE": "a_only"}


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


def _catalog(count: int) -> list[dict]:
    """`count` dramas on the conversion board, rank 1 first."""
    return [board_drama(index, title=f"Board Drama {index}", boards={"qc": index}) for index in range(1, count + 1)]


def _stable_env(url: str, **extra: str) -> dict[str, str]:
    return trends_env(url, mode="stable", **STABLE, **extra)


@pytest.mark.parametrize(
    "variables",
    [
        {},  # the canary's defaults: H and both
        {"PICK_OBS_TRENDS_GRANULARITY": "D"},
        {"PICK_OBS_TRENDS_ROUTE": "a_only"},
        {"PICK_OBS_TRENDS_GRANULARITY": "HD", "PICK_OBS_TRENDS_ROUTE": "a_only"},
        {"PICK_OBS_TRENDS_GRANULARITY": "D", "PICK_OBS_TRENDS_ROUTE": "b_only"},
    ],
)
@pytest.mark.asyncio
async def test_stable_needs_daily_and_a_only(obs_url, tmp_path, variables):
    """A variable left at the canary's value is refused at the self-check and at every trigger, before any request."""
    await seed_catalog(obs_url, _catalog(3))
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    env = trends_env(obs_url, mode="stable", **variables)
    err = io.StringIO()
    assert await trigger(env, clock, google, controls=tmp_path / "none.json", argv=["--selfcheck-only"], out=io.StringIO(), err=err) == ExitCode.REFUSED
    assert "PICK_OBS_TRENDS_GRANULARITY=D" in err.getvalue() and "PICK_OBS_TRENDS_ROUTE=a_only" in err.getvalue()
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.REFUSED
    assert google.seen == [] and await batches(obs_url) == []


@pytest.mark.asyncio
async def test_stable_night_on_the_top_dramas(obs_url, tmp_path):
    """One stable night from 17:30: the batch keeps the source's picks and the daily window_end (00:00 of the day it
    was created, the latest complete day being the one before), every unit is a daily worldwide series of two requests
    with no related queries, and the raw rows are the bare lines of today 1-m. No control list is read."""
    await seed_catalog(obs_url, _catalog(6))
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    assert await trigger(_stable_env(obs_url), clock, google, controls=tmp_path / "none.json", admission=STRICT) == ExitCode.OK
    (batch,) = await batches(obs_url)
    assert (batch["collect_mode"], batch["mode"], batch["outcome"], batch["target_date"]) == ("stable", "shadow", "withheld", f"{TARGET:%Y-%m-%d}")
    assert batch["window_end"] == stamp(at(EVE, 0)) and batch["coverage"] == 1.0
    plan = batch["plan_json"]
    assert (plan["source"], plan["granularity"], plan["related"]) == ("top_dramas", "D", False)
    units = plan["units"]
    assert [unit["priority"] for unit in units] == [1, 2, 3, 4, 5, 6] and plan["truncated"] == []
    assert {(unit["geo"], unit["granularity"], unit["related"], unit["item"]) for unit in units} == {("WW", "D", False, "title")}
    picks = plan["notes"]["top_dramas"]["picks"]
    assert [picks[unit["key"]]["title"] for unit in units] == [f"Board Drama {index}" for index in range(1, 7)]
    assert plan["notes"]["admission"]["planned_requests"] == 12  # the canary's gate figures are kept, never applied
    sent = await request_rows(obs_url)
    assert len(google.seen) == len(sent) == await budget_requests(obs_url) == 1 + 2 * 6
    assert {seen.phase for seen in google.seen} == {"warmup", "explore", "multiline"} and {seen.geo for seen in google.seen if seen.phase == "explore"} == {
        "WW"
    }
    raw = await raw_rows(obs_url)
    assert {(row["line_role"], row["time_range"], row["geo"]) for row in raw} == {("bare", "today 1-m", "WW")}
    assert sorted(row["params_json"]["unit"] for row in raw) == sorted(unit["key"] for unit in units)


@pytest.mark.asyncio
async def test_stable_preflight_reads_tonight(obs_url, tmp_path):
    """preflight on the stable mode: the daily window_end a 17:30 batch gets, the picks planned, nothing sent; the
    canary's payload gate does not apply."""
    await seed_catalog(obs_url, _catalog(4))
    clock = ManualClock(at(EVE, 12, 0))
    google = FakeGoogle(clock)
    out = io.StringIO()
    assert await trigger(_stable_env(obs_url), clock, google, controls=tmp_path / "none.json", argv=["preflight"], out=out, admission=STRICT) == ExitCode.OK
    line = json.loads(out.getvalue())["preflight"]
    assert (line["mode"], line["window_end_if_started_on_time"], line["planned_units"], line["planned_requests"]) == ("stable", stamp(at(EVE, 0)), 4, 8)
    assert line["reasons"] == [] and line["refused_by"] == [] and google.seen == []


# ---- a full night against stable's window (section 6 item 3) -------------------------------------------------------

USER = pacing.PRESETS["user"]


def test_top_dramas_fit_stable_at_the_production_pace():
    """TARGET_DRAMAS daily units of 2 requests fit stable's plan with room to spare, and its window at the user pace:
    a clear night covers them all, one 429 at the 56th request (a 30-minute pause, half speed after it) at least 95%,
    and the same night dying after midnight and resumed by the next trigger is reported."""
    stable = budget.mode_limits("stable")
    sizes = (2,) * top.TARGET_DRAMAS
    assert sum(sizes) <= budget.plan_budget(stable) and budget.plan_budget(stable) // 2 == 165
    found = {scenario.name: capacity.estimate(sizes, limits=stable, target_date=TARGET, params=USER, scenario=scenario) for scenario in capacity.SCENARIOS}
    assert found["clear"].coverage == 1.0 and found["one_limit"].coverage >= capacity.MIN_LIMITED_COVERAGE
    assert found["clear"].elapsed < timedelta(hours=3) and found["resumed"].coverage >= capacity.MIN_LIMITED_COVERAGE
    halved = stable.halved()  # two extinguished target dates in a row halve the next cap
    assert sum(sizes) <= budget.plan_budget(halved)
    print({name: estimate.summary() for name, estimate in found.items()})


@pytest.mark.parametrize("scenario", capacity.SCENARIOS[:2], ids=lambda scenario: scenario.name)
@pytest.mark.asyncio
async def test_a_stable_night_of_top_dramas_through_the_executor(obs_url, tmp_path, scenario):
    """The same night through the real entry and executor: all TARGET_DRAMAS units (all but the one a 429 abandons)
    are fetched, well before 01:45, and the estimate covers no more and finishes no sooner (capacity.py)."""
    if is_postgres(obs_url):
        pytest.skip("timing on a ManualClock does not depend on the dialect")
    await seed_catalog(obs_url, _catalog(top.TARGET_DRAMAS))
    start, deadline = budget.mode_limits("stable").window(TARGET)
    clock = ManualClock(start)
    google = FakeGoogle(clock, script={scenario.limit_at: "429"} if scenario.limit_at else {})
    assert await trigger(_stable_env(obs_url), clock, google, controls=tmp_path / "none.json", admission=STRICT) == ExitCode.OK
    (batch,) = await batches(obs_url)
    assert batch["planned_units"] == top.TARGET_DRAMAS
    lost = 1 if scenario.limit_at else 0
    assert batch["fetched_units"] >= top.TARGET_DRAMAS - lost
    last = max(seen.done_at for seen in google.seen)
    assert last < deadline
    found = capacity.estimate((2,) * top.TARGET_DRAMAS, limits=budget.mode_limits("stable"), target_date=TARGET, params=USER, scenario=scenario)
    assert found.covered <= batch["fetched_units"] and found.elapsed >= last - start
    print(f"{scenario.name}: {batch['fetched_units']}/{top.TARGET_DRAMAS} units by {last:%H:%M}; estimate {found.summary()}")


@pytest.mark.parametrize("reasons", [("wall",), ("trips", "trips")])
@pytest.mark.asyncio
async def test_stable_does_not_bypass_terminated_canary_history(obs_url, tmp_path, reasons):
    from obs_db_helpers import execute

    await seed_catalog(obs_url, _catalog(3))
    for index, reason in enumerate(reasons):
        await execute(
            obs_url,
            "insert into ggwp_obs_budget (channel,budget_day,collect_mode,extinguish_reason,extinguished_at) values ('trends',:day,'canary1',:reason,:at)",
            day=f"2026-09-{23 + index}",
            reason=reason,
            at=f"2026-09-{23 + index}T23:00:00.000000+00:00",
        )
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    env = _stable_env(obs_url)
    out = io.StringIO()
    assert await trigger(env, clock, google, controls=tmp_path / "none.json", argv=["preflight"], out=out) == ExitCode.REFUSED
    assert json.loads(out.getvalue())["preflight"]["refused_by"] == ["canary_terminated"]
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.REFUSED
    assert google.seen == []
    assert "canary_terminated" in (await batches(obs_url))[-1]["status_codes_json"]
