"""G3 seam 2 and P2-1: the capacity estimate (capacity.py) against the real executor, and the modes it admits (plan
section 9).

The estimate replays a night through the real pacer and breaker with every gap at its longest. These tests run the
same nights through the real entry and executor, on a ManualClock against FakeGoogle (MockTransport), and check the
estimate is never the optimistic one: it covers no more units than the night did, and when the night covered all it
could, it finishes no sooner. Three nights (capacity.SCENARIOS): no limit signal; a 429 at the 56th request, a
30-minute pause, a good probe and half speed for the rest of the target date; and that night dying with its first
request after midnight out, resumed by the next cron trigger after the lease ran out, on the same target date and
window_end.

The nights run on SQLite only: on a ManualClock the timing does not depend on the dialect, and the PostgreSQL half of
every other session test already runs the same executor.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import is_postgres, migrated
from trends_fake_google import FakeGoogle
from trends_session_helpers import TARGET, batches, recent_catalog, seed_catalog, trends_env, trigger, write_controls

from ggwork_pick.observe import lease
from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.trends import budget, canary, capacity, pacing
from ggwork_pick.observe.trends.run import window_end_of

USER = pacing.PRESETS["user"]
CANARY_EARLIEST_START = time(18, 0)
DEADLINE = time(1, 45)


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    if is_postgres(pick_db_url):
        pytest.skip("timing on a ManualClock does not depend on the dialect")
    return await migrated(pick_db_url, tmp_path)


class Crash(RuntimeError):
    """The process dies with a request out."""


@dataclass(frozen=True)
class Night:
    sizes: tuple[int, ...]
    fetched: int
    started: datetime
    last_done: datetime
    window_end: str

    @property
    def elapsed(self) -> timedelta:
        return self.last_done - self.started


def _crasher(clock: ManualClock, at: datetime | None, crashed: list[datetime]):
    def check(ordinal: int, phase: str) -> None:
        if at is not None and not crashed and clock.now() >= at:
            crashed.append(clock.now())
            raise Crash("synthetic crash after midnight")

    return check


async def _night(url, tmp_path, *, mode: str, scenario: capacity.Scenario, seed: int, pace: str = "user") -> Night:
    """One night of `mode` through the real entry, from the mode's start, as `scenario` has it."""
    catalog = recent_catalog(260)
    await seed_catalog(url, catalog)
    controls = write_controls(tmp_path, catalog[:8])
    start, deadline = budget.mode_limits(mode).window(TARGET)
    env = trends_env(url, mode=mode, PICK_OBS_TRENDS_PACE=pace)
    clock, crashed = ManualClock(start), []
    script = {scenario.limit_at: "429"} if scenario.limit_at else {}
    googles = [FakeGoogle(clock, script=script, on_request=_crasher(clock, capacity.crash_moment(scenario, TARGET), crashed))]
    status = await trigger(env, clock, googles[0], controls=controls, seed=seed)
    assert status == (ExitCode.FAILED if crashed else ExitCode.OK)
    if crashed:  # the next trigger after the lease ran out resumes the night
        resume = capacity.next_trigger(crashed[0] + timedelta(seconds=lease.LEASE_SECONDS))
        assert resume < deadline
        clock.advance((resume - clock.now()).total_seconds())
        googles.append(FakeGoogle(clock))
        assert await trigger(env, clock, googles[1], controls=controls, seed=seed) == ExitCode.OK
    (batch,) = await batches(url)
    sizes = tuple(1 + unit["timeline"] + unit["related"] for unit in batch["plan_json"]["units"])
    last = max(seen.done_at for google in googles for seen in google.seen)
    return Night(sizes, batch["fetched_units"], start, last, batch["window_end"])


def _estimate(night: Night, mode: str, scenario: capacity.Scenario, params=USER) -> capacity.Estimate:
    return capacity.estimate(night.sizes, limits=budget.mode_limits(mode), target_date=TARGET, params=params, scenario=scenario)


def _assert_not_optimistic(night: Night, found: capacity.Estimate, scenario: capacity.Scenario) -> None:
    lost = 1 if scenario.limit_at else 0  # the unit the 429 abandoned
    assert found.covered <= night.fetched, (found.summary(), night)
    if night.fetched == len(night.sizes) - lost:  # the night covered all it could: compare when it was done
        assert found.elapsed >= night.elapsed, (found.summary(), night.elapsed)
    print(f"{scenario.name}: night {night.fetched}/{len(night.sizes)} units in {night.elapsed}; estimate {found.summary()}")


# ---- the estimate against the real executor ------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [3, 11])
@pytest.mark.parametrize("scenario", capacity.SCENARIOS, ids=lambda scenario: scenario.name)
@pytest.mark.parametrize("mode", ["canary1", "canary2"])
@pytest.mark.asyncio
async def test_estimate_never_beats_the_executor(obs_url, tmp_path, mode, scenario, seed):
    """At the production pace, for the modes the canary runs: the estimate of the night's own task list covers no more
    and, when the night covered all it could, finishes no sooner; so does the mode check's canonical plan, which is
    never smaller. The resumed night keeps its target date's window_end (D23)."""
    night = await _night(obs_url, tmp_path, mode=mode, scenario=scenario, seed=seed)
    _assert_not_optimistic(night, _estimate(night, mode, scenario), scenario)
    fit = capacity.mode_fit(budget.mode_limits(mode), USER)
    canonical = {"clear": fit.clear, "one_limit": fit.one_limit, "resumed": fit.resumed}[scenario.name]
    assert sum(night.sizes) <= budget.plan_budget(budget.mode_limits(mode)) and canonical.elapsed >= night.elapsed
    assert night.window_end == stamp(window_end_of(night.started))


@pytest.mark.parametrize("scenario", [capacity.CLEAR, capacity.ONE_LIMIT], ids=lambda scenario: scenario.name)
@pytest.mark.asyncio
async def test_estimate_never_beats_the_executor_at_design_pace(obs_url, tmp_path, scenario):
    night = await _night(obs_url, tmp_path, mode="canary1", scenario=scenario, seed=5, pace="design")
    _assert_not_optimistic(night, _estimate(night, "canary1", scenario, params=pacing.PRESETS["design"]), scenario)


@pytest.mark.asyncio
async def test_estimate_never_beats_a_night_cut_by_the_deadline(obs_url, tmp_path, monkeypatch):
    """canary1 as it was before G3 (22:00): a 429 and a crash after midnight leave the night short of its task list at
    01:45, and the estimate covers no more of it."""
    modes = {**budget.MODES, "canary1": budget.ModeLimits("canary1", start=time(22, 0), plan=220, cap=220)}
    monkeypatch.setattr(budget, "MODES", modes)
    night = await _night(obs_url, tmp_path, mode="canary1", scenario=capacity.RESUMED, seed=3)
    found = _estimate(night, "canary1", capacity.RESUMED)
    assert night.fetched < len(night.sizes) - 1 and found.covered <= night.fetched
    print(f"cut by the deadline: night {night.fetched}/{len(night.sizes)} units; estimate {found.summary()}")


# ---- the modes (plan section 9) ------------------------------------------------------------------------------------


def test_modes_fit_at_the_production_pace():
    """Section 9's table at the user preset: no limit signal covers every unit; one 429 at the 56th request, with
    half speed after it, still covers at least 95%; every session stops at 01:45, and a canary starts no earlier
    than 18:00. canary2's cap is 1.5 times its plan. (The 2.9-a-minute check this replaces admitted canary2 at 430
    from 22:00 and stable at 650 from 20:30: at this pace they cover 89% and 81% of a clear night.)"""
    for name, limits in budget.MODES.items():
        fit = capacity.mode_fit(limits, USER)
        assert fit.fits and fit.problem() is None, (name, fit.clear.summary(), fit.one_limit.summary())
        assert fit.clear.coverage == 1.0 and fit.one_limit.coverage >= capacity.MIN_LIMITED_COVERAGE
        assert limits.deadline == DEADLINE
        if name.startswith("canary"):
            assert limits.start >= CANARY_EARLIEST_START
    canary2 = budget.mode_limits("canary2")
    assert canary2.cap == round(canary2.plan * 1.5)


def test_modes_fit_at_either_preset():
    """design is faster than user everywhere, so a mode that fits the one fits the other."""
    for limits in budget.MODES.values():
        assert capacity.mode_fit(limits, pacing.PRESETS["design"]).fits


@pytest.mark.parametrize(
    ("name", "start", "plan", "fits"),
    [
        ("canary2", time(22, 0), 430, False),  # canary2 before G3: 89% of a clear night
        ("stable", time(20, 30), 650, False),  # stable before G3: 81%
        ("canary2", time(18, 30), 300, True),
        ("canary2", time(18, 30), 415, True),  # about the most canary2 could plan from 18:30 (plan section 9)
        ("canary2", time(18, 30), 420, False),
        ("canary1", time(21, 0), 220, True),
        ("canary1", time(22, 0), 220, True),  # the old start clears the bar, barely (96%); a crash on top leaves 86%
        ("stable", time(1, 0), 40, True),  # an after-midnight start is on the target date itself
        ("canary1", time(1, 30), 220, False),  # 15 minutes
    ],
)
def test_mode_fit_at_the_user_pace(name, start, plan, fits):
    assert capacity.mode_fit(budget.ModeLimits(name, start=start, plan=plan, cap=plan), USER).fits is fits


def test_canonical_shape_is_the_canarys():
    """Every RELATED_EVERY-th drama unit also asks for related queries (canary.with_related): 3 requests, then 2."""
    assert len(capacity.CANONICAL_SHAPE) == canary.RELATED_EVERY and capacity.CANONICAL_SHAPE[0] == 3
    assert set(capacity.CANONICAL_SHAPE[1:]) == {2}
    assert sum(capacity.canonical_sizes(205)) <= 205 and sum(capacity.canonical_sizes(205)) > 202


def test_trigger_and_lease_are_the_crons():
    assert capacity.LEASE == timedelta(seconds=lease.LEASE_SECONDS)
    assert capacity.next_trigger(datetime(2026, 9, 26, 0, 0, 20, tzinfo=capacity.UTC) + capacity.LEASE) == datetime(2026, 9, 26, 0, 30, tzinfo=capacity.UTC)
    assert capacity.next_trigger(datetime(2026, 9, 26, 0, 30, tzinfo=capacity.UTC)) == datetime(2026, 9, 26, 0, 30, tzinfo=capacity.UTC)
