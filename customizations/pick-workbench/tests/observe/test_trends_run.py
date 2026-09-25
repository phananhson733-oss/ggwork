"""TR-14: the nightly Trends session through the real entry point, on both dialects (plan TR-14; design 3.2, 4.5, 4.9,
4.10, 4.11, 6.3; D23, D34; counterexamples 1 and 10).

Each test seeds a shared catalog batch, writes its own copy of the canary's control list, and fires cron triggers at
chosen times on a ManualClock against FakeGoogle (MockTransport): the night passes in seconds and nothing leaves the
process. What the session wrote is read back through a separate engine.
"""

import io
import json
from dataclasses import replace
from datetime import timedelta

import pytest
import pytest_asyncio
from obs_db_helpers import execute, migrated, open_db, rows, runtime_row
from trends_fake_google import FakeGoogle
from trends_session_helpers import (
    EVE,
    TARGET,
    at,
    batches,
    budget_requests,
    drama,
    identity_of,
    raw_rows,
    recent_catalog,
    request_rows,
    seed_catalog,
    trends_env,
    trigger,
    write_controls,
)

from ggwork_pick.observe.admin import cmd_reset_disable
from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.crypto import load_cipher
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import DbStateStore, LeasedWriter, collector_session
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends import __main__ as entry
from ggwork_pick.observe.trends import breaker
from ggwork_pick.observe.trends import run as run_module
from ggwork_pick.observe.trends.canary import SourceUnits
from ggwork_pick.observe.trends.run import Day, Published, Wiring, run_session
from ggwork_pick.observe.trends.settings import settings_from
from ggwork_pick.observe.trends.units import QueryUnit, unit_key


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


class Crash(RuntimeError):
    """The process dies with a request out: nothing the executor could catch as an HTTP outcome."""


def crash_when(clock: ManualClock, predicate):
    def check(ordinal: int, phase: str) -> None:
        if predicate(ordinal, clock.now()):
            raise Crash("synthetic crash")

    return check


async def _night(obs_url, tmp_path, *, dramas=12, controls=2):
    catalog = recent_catalog(dramas)
    await seed_catalog(obs_url, catalog)
    return catalog, write_controls(tmp_path, catalog[:controls])


# ---- when a trigger does nothing ------------------------------------------------------------------------------------


@pytest.mark.parametrize("when", [at(EVE, 17, 0), at(EVE, 20, 30), at(TARGET, 1, 45), at(TARGET, 1, 55)])
@pytest.mark.asyncio
async def test_outside_the_window_does_nothing(obs_url, tmp_path, when):
    """Before canary1's 21:00 start (the cron's first trigger, 17:00, included) or from the 01:45 hard deadline: exit
    0, no HTTP, not even the lease."""
    _, controls = await _night(obs_url, tmp_path)
    clock = ManualClock(when)
    google = FakeGoogle(clock)
    assert await trigger(trends_env(obs_url), clock, google, controls=controls) == ExitCode.OK
    assert google.seen == [] and await batches(obs_url) == []
    assert (await runtime_row(obs_url))["lease_generation"] == 0


@pytest.mark.asyncio
async def test_missing_controls_refuses_the_canary(obs_url, tmp_path):
    """No control list: exit 2 before the database or any request, with a message naming the file."""
    await _night(obs_url, tmp_path)
    clock = ManualClock(at(EVE, 22, 10))
    google = FakeGoogle(clock)
    err = io.StringIO()
    status = await trigger(trends_env(obs_url), clock, google, controls=tmp_path / "canary_controls.json", err=err)
    assert (status, google.seen) == (ExitCode.REFUSED, [])
    assert "canary_controls.json" in err.getvalue() and "拒绝跑金丝雀" in err.getvalue()
    assert (await runtime_row(obs_url))["lease_generation"] == 0


@pytest.mark.asyncio
async def test_market_series_missing_refuses_the_canary(obs_url, tmp_path):
    """A control list without a market series for a geo the canary queries (here only US and DE, while the titles go
    to WW, ES, MX, FR, IT and BR too): exit 2 before the database or any request, naming the geos."""
    catalog = recent_catalog(12)
    await seed_catalog(obs_url, catalog)
    controls = write_controls(tmp_path, catalog[:2], market=[{"geo": "US", "term": "short drama"}, {"geo": "DE", "term": "Kurzdrama"}])
    clock = ManualClock(at(EVE, 22, 10))
    google = FakeGoogle(clock)
    err = io.StringIO()
    assert await trigger(trends_env(obs_url), clock, google, controls=controls, err=err) == ExitCode.REFUSED
    assert google.seen == [] and "BR、ES、FR、IT、MX、WW" in err.getvalue()
    assert (await runtime_row(obs_url))["lease_generation"] == 0


@pytest.mark.asyncio
async def test_stable_waits_for_its_task_source(obs_url, tmp_path):
    _, controls = await _night(obs_url, tmp_path)
    clock = ManualClock(at(EVE, 21, 0))
    google = FakeGoogle(clock)
    assert await trigger(trends_env(obs_url, mode="stable"), clock, google, controls=controls) == ExitCode.REFUSED
    assert google.seen == []


# ---- one night --------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_night_writes_batch_requests_and_raw(obs_url, tmp_path):
    """A canary1 night: one batch for the target date, a request row per HTTP request, raw rows per line; the budget,
    the rows and the transport agree; withheld, never published (TR-20 publishes)."""
    catalog, controls = await _night(obs_url, tmp_path, dramas=20)
    clock = ManualClock(at(EVE, 22, 10))
    google = FakeGoogle(clock)
    assert await trigger(trends_env(obs_url), clock, google, controls=controls) == ExitCode.OK
    (batch,) = await batches(obs_url)
    assert (batch["target_date"], batch["collect_mode"], batch["mode"], batch["outcome"]) == ("2026-09-26", "canary1", "shadow", "withheld")
    assert batch["window_end"] == stamp(at(EVE, 19)) and batch["published_set_id"] is None and batch["finished_at"] is not None
    sent = await request_rows(obs_url)
    assert len(google.seen) == len(sent) == await budget_requests(obs_url) == batch["requests"]
    assert google.phases()[0] == "warmup" and sent[0]["budget_item"] == "warmup"
    assert {row["budget_item"] for row in sent} <= {"warmup", "market", "control", "title", "related"}
    planned = batch["plan_json"]["units"]
    assert batch["planned_units"] == len(planned) == batch["fetched_units"] and batch["coverage"] == 1.0
    raw = await raw_rows(obs_url)
    assert {row["batch_id"] for row in raw} == {batch["id"]} and all(row["data_json"] is not None for row in raw)
    assert {row["line_role"] for row in raw} == {"market", "bare", "related"}
    assert {row["identity"] for row in raw if row["line_role"] == "bare"} >= {identity_of(payload) for payload in catalog[:2]}
    assert batch["summary_json"]["uncovered_units"] == [] and batch["status_codes_json"] == []


DONE_ROWS = {
    "withheld": "update ggwp_obs_batches set outcome = 'withheld'",
    "published": "update ggwp_obs_batches set outcome = 'published', published_set_id = 'set-x'",
    # TR-20 publishes in the finishing step, but a published row is done even without its finish time
    "published_unfinished": "update ggwp_obs_batches set outcome = 'published', published_set_id = 'set-x', finished_at = null",
}


@pytest.mark.parametrize("done", sorted(DONE_ROWS))
@pytest.mark.asyncio
async def test_idempotent_after_publish(obs_url, tmp_path, done):
    """A target date that is done (finished, or published by TR-20) does nothing when triggered again: exit 0, no HTTP,
    and its row exactly as it was (outcome, set id, finish time, generation, summary, codes), never rewritten."""
    _, controls = await _night(obs_url, tmp_path, dramas=4)
    clock = ManualClock(at(EVE, 22, 0))
    env = trends_env(obs_url)
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    await execute(obs_url, DONE_ROWS[done])
    (before,) = await batches(obs_url)
    for _ in range(2):
        clock.advance(1800)
        again = FakeGoogle(clock)
        assert await trigger(env, clock, again, controls=controls) == ExitCode.OK
        assert again.seen == []
    assert await batches(obs_url) == [before]


# ---- the window and resuming (counterexample 1) -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_window_end_shared_2210_0130(obs_url, tmp_path):
    """The 22:10 half and the 01:30 half of one night write to one batch with one window_end (19:00)."""
    _, controls = await _night(obs_url, tmp_path, dramas=40)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 10))
    first = FakeGoogle(clock, on_request=crash_when(clock, lambda ordinal, now: ordinal == 30))
    assert await trigger(env, clock, first, controls=controls) == ExitCode.FAILED
    (batch,) = await batches(obs_url)
    assert batch["outcome"] == "running" and batch["finished_at"] is None
    clock.advance((at(TARGET, 1, 30) - clock.now()).total_seconds())
    second = FakeGoogle(clock)
    assert await trigger(env, clock, second, controls=controls) == ExitCode.OK
    (after,) = await batches(obs_url)
    assert (after["id"], after["window_end"], after["target_date"]) == (batch["id"], stamp(at(EVE, 19)), "2026-09-26")
    fetched_at = [row["fetched_at"] for row in await raw_rows(obs_url)]
    assert min(fetched_at) < stamp(at(EVE, 23)) and max(fetched_at) >= stamp(at(TARGET, 1, 30))
    assert second.phases()[0] == "explore"  # the jar was warmed for this target date at 22:10: no second warm-up


@pytest.mark.asyncio
async def test_resume_after_midnight_same_window(obs_url, tmp_path):
    """[counterexample 1] A session that dies after midnight resumes on the same target date and window_end: 20:00 from
    its 23:10 creation, not 21:00 from the 00:30 resume."""
    _, controls = await _night(obs_url, tmp_path, dramas=60)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 23, 10))
    midnight = at(TARGET, 0, 5)
    first = FakeGoogle(clock, on_request=crash_when(clock, lambda ordinal, now: now >= midnight))
    assert await trigger(env, clock, first, controls=controls) == ExitCode.FAILED
    (batch,) = await batches(obs_url)
    done_before = set(batch["summary_json"]["units"])
    others = [drama(500 + index, listed_at=EVE, title=f"another drama {index}") for index in range(30)]
    await seed_catalog(obs_url, others, batch_id="cat-2", published_at=at(TARGET, 0, 20))
    clock.advance((at(TARGET, 0, 30) - clock.now()).total_seconds())
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    (after,) = await batches(obs_url)
    assert after["id"] == batch["id"] and after["target_date"] == "2026-09-26"
    assert after["window_end"] == stamp(at(EVE, 20)) != stamp(at(EVE, 21))
    # the task list is not recomputed either, although a newer shared catalog batch came out in between
    assert after["plan_json"] == batch["plan_json"] and after["plan_json"]["catalog_batch_id"] == "cat-1"
    assert done_before < set(after["summary_json"]["units"]) and after["outcome"] == "withheld"
    budget = await rows(obs_url, "select budget_day from ggwp_obs_budget where channel = 'trends'")
    assert [row["budget_day"] for row in budget] == ["2026-09-26"]  # midnight did not open a new budget day (D23)


@pytest.mark.asyncio
async def test_next_night_closes_the_one_left_running(obs_url, tmp_path):
    """A night that died after its last trigger stays running until the next night's batch opens: then it is closed
    as failed (abandon_unfinished), and the new night runs as usual."""
    _, controls = await _night(obs_url, tmp_path, dramas=12)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    crashing = FakeGoogle(clock, on_request=crash_when(clock, lambda ordinal, now: ordinal == 8))
    assert await trigger(env, clock, crashing, controls=controls) == ExitCode.FAILED
    clock.advance((at(TARGET, 22, 0) - clock.now()).total_seconds())
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    first, second = await batches(obs_url)
    assert (first["outcome"], first["finished_at"]) == ("failed", stamp(at(TARGET, 22, 0)))
    assert second["outcome"] == "withheld" and second["target_date"] == f"{TARGET + timedelta(days=1):%Y-%m-%d}"


# ---- the deadline and truncation --------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deadline_truncates(obs_url, tmp_path):
    """Nothing goes out at or after 01:45; the units left are uncovered with the reason deadline."""
    _, controls = await _night(obs_url, tmp_path, dramas=60)
    clock = ManualClock(at(TARGET, 1, 30))
    google = FakeGoogle(clock)
    assert await trigger(trends_env(obs_url), clock, google, controls=controls) == ExitCode.OK
    assert google.seen and max(seen.sent_at for seen in google.seen) < at(TARGET, 1, 45)
    (batch,) = await batches(obs_url)
    reasons = {unit["reason"] for unit in batch["summary_json"]["uncovered_units"]}
    assert "deadline" in reasons and batch["outcome"] == "withheld" and batch["coverage"] < 1


@pytest.mark.asyncio
async def test_uncovered_units_listed(obs_url, tmp_path):
    """More units than the plan holds: the rest are listed as truncated, in truncation order, with identity and geo;
    and units the night could not reach are listed with their own reason."""
    _, controls = await _night(obs_url, tmp_path, dramas=150)
    clock = ManualClock(at(TARGET, 1, 0))
    assert await trigger(trends_env(obs_url), clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    (batch,) = await batches(obs_url)
    truncated = batch["plan_json"]["truncated"]
    listed = batch["summary_json"]["uncovered_units"]
    assert truncated and [unit["key"] for unit in listed[: len(truncated)]] == [unit["key"] for unit in truncated]
    assert {unit["reason"] for unit in listed[: len(truncated)]} == {"truncated"}
    assert {unit["reason"] for unit in listed[len(truncated) :]} == {"deadline"}
    assert all(unit["geo"] and unit["identity"] for unit in listed if unit["item"] == "title")
    assert sum(1 + unit["timeline"] + unit["related"] for unit in batch["plan_json"]["units"]) <= 205  # canary1's plan less 15


# ---- the canary's payload (G3 seam 1) -------------------------------------------------------------------------------


async def _as_deployed(env, clock, google, controls, *, argv=("run",), out=None, err=None) -> int:
    """One command through the real entry with every production default, the payload gate included."""
    return await entry.amain(list(argv), environ=env, clock=clock, rng=random_source(7), transport=google.transport(), controls_path=controls, out=out, err=err)


def _stale_catalog(count: int) -> list[dict]:
    """A published batch whose dramas were all listed a month before the target date: no recent titles."""
    return [drama(index, listed_at=EVE - timedelta(days=30)) for index in range(count)]


@pytest.mark.asyncio
async def test_canary_without_its_payload_is_refused(obs_url, tmp_path):
    """[G3 seam 1] A published shared batch is not a canary's payload: here no drama is recent and none of the
    controls is in the batch, so the night would be the eight market series alone and come back at 100% coverage.
    Refused (exit 2) before any request, on a refusal row that carries not_published_low_coverage and the overview
    (planned requests against the plan, the controls matched per group, the missing ones), which status shows."""
    await seed_catalog(obs_url, _stale_catalog(40))
    controls = write_controls(tmp_path, [drama(900 + index) for index in range(4)])  # four positives, none in the batch
    clock = ManualClock(at(EVE, 22, 0))
    google, err = FakeGoogle(clock), io.StringIO()
    env = trends_env(obs_url)
    assert await _as_deployed(env, clock, google, controls, err=err) == ExitCode.REFUSED
    assert google.seen == [] and "计划请求" in err.getvalue() and "正对照" in err.getvalue()
    (row,) = await batches(obs_url)
    assert (row["outcome"], row["plan_json"], row["window_end"]) == ("failed", None, None)
    assert row["status_codes_json"] == ["not_published_low_coverage"]
    admission = row["summary_json"]["admission"]
    assert (admission["planned_requests"], admission["plan"], admission["recent_dramas"]) == (16, 220, 0)
    assert admission["controls"]["positive"] == {"listed": 4, "matched": 0} and admission["missing_controls"] == 4
    assert admission["missing_first"] == [identity_of(drama(900 + index)) for index in range(4)] and len(admission["reasons"]) == 2
    out = io.StringIO()
    assert await _as_deployed(env, clock, FakeGoogle(clock), controls, argv=["status"], out=out) == ExitCode.OK
    shown = [json.loads(line)["plan"] for line in out.getvalue().splitlines() if "plan" in json.loads(line)]
    assert shown[0]["missing_controls"] == 4 and shown[0]["reasons"] == admission["reasons"]


@pytest.mark.asyncio
async def test_canary_without_its_positive_controls_is_refused(obs_url, tmp_path):
    """Enough recent titles to fill the plan, but fewer than half the positive controls in the batch: refused, and
    only for the controls."""
    catalog = recent_catalog(150)
    await seed_catalog(obs_url, catalog)
    controls = write_controls(tmp_path, [*catalog[:1], *(drama(900 + index) for index in range(3))])  # 1 of 4 matched
    clock = ManualClock(at(EVE, 22, 0))
    google = FakeGoogle(clock)
    assert await _as_deployed(trends_env(obs_url), clock, google, controls) == ExitCode.REFUSED and google.seen == []
    (row,) = await batches(obs_url)
    admission = row["summary_json"]["admission"]
    assert admission["controls"]["positive"] == {"listed": 4, "matched": 1} and admission["planned_requests"] >= 176
    assert len(admission["reasons"]) == 1 and "正对照" in admission["reasons"][0]


USER_PACE = {"preset": "user", "bucket_capacity": 4, "refill_per_minute": 2}


def _plan_lines(out: io.StringIO) -> list[dict]:
    return [json.loads(line)["plan"] for line in out.getvalue().splitlines() if "plan" in json.loads(line)]


@pytest.mark.parametrize(("pace", "bucket", "refill"), [(None, 4, 2), ("design", 8, 4)])
@pytest.mark.asyncio
async def test_batch_keeps_the_pace_it_ran_at(obs_url, tmp_path, pace, bucket, refill):
    """[G3 review P3] The pace is one of the canary's parameters (plan section 9: changed mid-way, the count starts
    again), so the batch keeps it: plan_json's notes name the preset with its bucket and refill, and status shows them
    on the plan line, for TR-30 to check night by night from the database."""
    _, controls = await _night(obs_url, tmp_path, dramas=6)
    env = trends_env(obs_url, **({"PICK_OBS_TRENDS_PACE": pace} if pace else {}))
    clock = ManualClock(at(EVE, 21, 0))
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    (batch,) = await batches(obs_url)
    expected = {"preset": pace or "user", "bucket_capacity": bucket, "refill_per_minute": refill}
    assert batch["plan_json"]["notes"]["pace"] == expected and batch["plan_json"]["notes"]["late_admission"] is False
    out = io.StringIO()
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls, argv=["status"], out=out) == ExitCode.OK
    (shown,) = _plan_lines(out)
    assert (shown["pace"], shown["late_admission"]) == (expected, False)


@pytest.mark.asyncio
async def test_a_night_admitted_after_a_refusal_is_marked_late(obs_url, tmp_path):
    """[G3 review P3] The payload gate refuses canary1's 21:00 trigger; a fresh shared catalog batch comes out, and the
    22:30 trigger takes the refusal row over and runs. Its window_end is 19:00, not the on-time 18:00, and its window
    an hour and a half shorter: its start moved, so plan_json's notes mark it late_admission, status shows it, and
    TR-30 does not count the day toward the three or the seven (trends-session.md)."""
    await seed_catalog(obs_url, _stale_catalog(40))
    catalog = recent_catalog(150)
    bad = write_controls(tmp_path, [drama(900 + index) for index in range(4)], name="bad.json")
    good = write_controls(tmp_path, catalog[:4], name="good.json")
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 21, 0))
    assert await _as_deployed(env, clock, FakeGoogle(clock), bad) == ExitCode.REFUSED
    (refused,) = await batches(obs_url)
    await seed_catalog(obs_url, catalog, batch_id="cat-2", published_at=at(EVE, 22, 0))
    clock.advance((at(EVE, 22, 30) - clock.now()).total_seconds())
    google = FakeGoogle(clock)
    assert await _as_deployed(env, clock, google, good) == ExitCode.OK and google.seen
    (late,) = await batches(obs_url)
    assert (late["id"], late["outcome"], late["window_end"]) == (refused["id"], "withheld", stamp(at(EVE, 19)))
    assert late["plan_json"]["notes"]["late_admission"] is True and late["status_codes_json"] == []
    out = io.StringIO()
    assert await _as_deployed(env, clock, FakeGoogle(clock), good, argv=["status"], out=out) == ExitCode.OK
    (shown,) = _plan_lines(out)
    assert shown["late_admission"] is True and shown["reasons"] == []


@pytest.mark.asyncio
async def test_preflight_reads_tonight_without_sending(obs_url, tmp_path):
    """S6 -> S7: `preflight` prints tonight's task list in figures (no lease, no request, nothing written): 0 when it
    is a canary's payload, 2 with the reasons when it is not."""
    catalog = recent_catalog(150)
    await seed_catalog(obs_url, catalog)
    good = write_controls(tmp_path, catalog[:4], name="good.json")
    bad = write_controls(tmp_path, [drama(900 + index) for index in range(4)], name="bad.json")
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 12, 0))  # daytime, as S6 is
    before = await runtime_row(obs_url)
    for controls, expected in ((good, ExitCode.OK), (bad, ExitCode.REFUSED)):
        google, out = FakeGoogle(clock), io.StringIO()
        assert await _as_deployed(env, clock, google, controls, argv=["preflight"], out=out) == expected
        (line,) = [json.loads(text)["preflight"] for text in out.getvalue().splitlines()]
        assert (line["target_date"], line["mode"], line["pace"]) == ("2026-09-26", "canary1", USER_PACE) and google.seen == []
        assert line["planned_requests"] >= 176 and bool(line["reasons"]) == (expected == ExitCode.REFUSED)
    assert await runtime_row(obs_url) == before and await batches(obs_url) == []


# ---- the breaker, the canary's end, and the daily alerts --------------------------------------------------------------


async def _refused_from_now_on(env, clock, controls, obs_url) -> None:
    """Every later trigger, a night's first and its last, exits 2 with no HTTP; the row keeps the red code."""
    for hour, minute in ((22, 0), (1, 30), (22, 0)):
        later = at(clock.now().date(), hour, minute)
        clock.advance(((later if later > clock.now() else later + timedelta(days=1)) - clock.now()).total_seconds())
        refused = FakeGoogle(clock)
        assert await trigger(env, clock, refused, controls=controls) == ExitCode.REFUSED
        assert refused.seen == []
    assert "canary_terminated" in (await batches(obs_url))[-1]["status_codes_json"]


@pytest.mark.parametrize("wall", ["sorry", "consent"])
@pytest.mark.asyncio
async def test_canary_terminate_rule(obs_url, tmp_path, wall):
    """[design 4.11; plan section 9; G3 seam 3] One captcha or consent wall in the canary ends it: the night's row
    writes canary_terminated at once, and every later trigger exits 2 with no HTTP. (Until G3 this test pinned the
    opposite: a first sorry page only put the day out, and the canary ran again the next night.)"""
    _, controls = await _night(obs_url, tmp_path, dramas=6)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    walled = FakeGoogle(clock, script={3: wall})
    assert await trigger(env, clock, walled, controls=controls) == ExitCode.OK
    (first,) = await batches(obs_url)
    assert {"extinguished_today", "canary_terminated"} <= set(first["status_codes_json"])
    await _refused_from_now_on(env, clock, controls, obs_url)
    out = io.StringIO()  # preflight says so too, before any night
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls, argv=["preflight"], out=out) == ExitCode.REFUSED
    assert json.loads(out.getvalue())["preflight"]["refused_by"] == ["canary_terminated"]
    # TR-30's fix-and-rerun starts the count afresh from a date, without deleting a row.
    rerun = FakeGoogle(clock)
    since = trends_env(obs_url, key=env["PICK_OBS_STATE_KEY"], PICK_OBS_CANARY_SINCE=f"{clock.now().date() + timedelta(days=1):%Y-%m-%d}")
    assert await trigger(since, clock, rerun, controls=controls) == ExitCode.OK and rerun.seen
    assert "canary_terminated" not in (await batches(obs_url))[-1]["status_codes_json"]


@pytest.mark.asyncio
async def test_canary_other_extinguished_days_terminate_on_the_second(obs_url, tmp_path):
    """[G3 seam 3] A day put out by anything but a wall (here five 429s) does not end the canary: the next night runs.
    A second such day does, and from then on every trigger is refused."""
    _, controls = await _night(obs_url, tmp_path, dramas=6)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    limited = FakeGoogle(clock, script={ordinal: "429" for ordinal in range(2, 40)})
    assert await trigger(env, clock, limited, controls=controls) == ExitCode.OK
    (first,) = await batches(obs_url)
    assert "extinguished_today" in first["status_codes_json"] and "canary_terminated" not in first["status_codes_json"]
    clock.advance((at(TARGET, 22, 0) - clock.now()).total_seconds())
    again = FakeGoogle(clock, script={ordinal: "429" for ordinal in range(2, 40)})
    assert await trigger(env, clock, again, controls=controls) == ExitCode.OK and again.seen  # the second night ran
    second = (await batches(obs_url))[-1]
    assert {"extinguished_today", "canary_terminated"} <= set(second["status_codes_json"])
    await _refused_from_now_on(env, clock, controls, obs_url)


@pytest.mark.asyncio
async def test_canary_wall_terminates_after_a_crash(obs_url, tmp_path, monkeypatch):
    """[G3 seam 3] The rule reads what is committed: a wall's day is on its budget row (extinguish_reason wall) the
    moment its request row is. A process that dies before its finishing step writes no code, yet the next trigger
    refuses with canary_terminated and closes the batch the crash left running."""
    _, controls = await _night(obs_url, tmp_path, dramas=6)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))

    async def dies(*args, **kwargs):
        raise Crash("synthetic crash before the finishing step")

    monkeypatch.setattr(run_module, "_finish", dies)
    assert await trigger(env, clock, FakeGoogle(clock, script={3: "sorry"}), controls=controls) == ExitCode.FAILED
    monkeypatch.undo()
    (left,) = await batches(obs_url)
    assert left["outcome"] == "running" and left["status_codes_json"] == []
    clock.advance(1800)
    refused = FakeGoogle(clock)
    assert await trigger(env, clock, refused, controls=controls) == ExitCode.REFUSED and refused.seen == []
    (closed,) = await batches(obs_url)
    assert closed["outcome"] == "failed" and "canary_terminated" in closed["status_codes_json"]


async def _save_state(url, state, cipher, clock) -> None:
    db = open_db(url)
    try:
        async with await LeasedWriter.acquire(db, "trends", clock=clock, owner="proc-setup") as writer:
            await DbStateStore(writer, cipher).save(state)
    finally:
        await db.dispose()


async def _disable(url, cipher, clock, day) -> None:
    """The persisted state as it is, with the breaker's third extinguished day on `day`: disabled_7d."""
    db = open_db(url)
    try:
        async with await LeasedWriter.acquire(db, "trends", clock=clock, owner="proc-setup") as writer:
            store = DbStateStore(writer, cipher)
            state = await store.load()
            days = (day - timedelta(days=4), day - timedelta(days=2), day)
            disabled = breaker.BreakerState(breaker.BreakerDay(day, extinguished="rate_limited"), days, day)
            await store.save(replace(state, breaker=disabled.to_dict()))
    finally:
        await db.dispose()


@pytest.mark.parametrize("later", [False, True])
@pytest.mark.asyncio
async def test_refusal_closes_a_session_left_running(obs_url, tmp_path, later):
    """A session dies part-way, then the channel is refused (disabled_7d) on the same night or the next: the batch it
    left running is closed as failed, so the run_status view never shows a night running forever; the refused night
    carries the code."""
    _, controls = await _night(obs_url, tmp_path, dramas=12)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    crashing = FakeGoogle(clock, on_request=crash_when(clock, lambda ordinal, now: ordinal == 8))
    assert await trigger(env, clock, crashing, controls=controls) == ExitCode.FAILED
    clock.advance((at(TARGET if later else EVE, 22, 0) - clock.now()).total_seconds() + (0 if later else 1800))
    refused_day = TARGET + timedelta(days=1) if later else TARGET
    await _disable(obs_url, load_cipher(env), clock, refused_day)
    google = FakeGoogle(clock)
    assert await trigger(env, clock, google, controls=controls) == ExitCode.REFUSED
    assert google.seen == []
    found = await batches(obs_url)
    assert [row["outcome"] for row in found] == ["failed"] * len(found) and all(row["finished_at"] for row in found)
    assert found[0]["plan_json"] is not None and len(found) == (2 if later else 1)
    assert found[-1]["status_codes_json"] == ["disabled_7d"]


@pytest.mark.asyncio
async def test_no_shared_catalog_refuses_the_canary(obs_url, tmp_path):
    """No published shared catalog batch: the canary has no titles and no controls to ask, so it refuses (exit 2)
    rather than running the market series alone; no request, no batch row."""
    controls = write_controls(tmp_path, [])
    clock = ManualClock(at(EVE, 22, 0))
    google = FakeGoogle(clock)
    err = io.StringIO()
    assert await trigger(trends_env(obs_url), clock, google, controls=controls, err=err) == ExitCode.REFUSED
    assert google.seen == [] and await batches(obs_url) == [] and "共享剧库批次" in err.getvalue()


@pytest.mark.asyncio
async def test_unreadable_plan_is_state_unavailable(obs_url, tmp_path):
    """A running batch whose plan_json cannot be read back: exit 3 with no request, never a task list made afresh in
    its place (counterexample 1: the list, like window_end, is the batch's)."""
    _, controls = await _night(obs_url, tmp_path, dramas=12)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    crashing = FakeGoogle(clock, on_request=crash_when(clock, lambda ordinal, now: ordinal == 8))
    assert await trigger(env, clock, crashing, controls=controls) == ExitCode.FAILED
    await execute(obs_url, "update ggwp_obs_batches set plan_json = :p", p=json.dumps({"format": "trends-session-plan-v0"}))
    clock.advance(1800)
    google, err = FakeGoogle(clock), io.StringIO()
    assert await trigger(env, clock, google, controls=controls, err=err) == ExitCode.STATE_UNAVAILABLE
    assert google.seen == [] and "plan_json" in err.getvalue()
    (row,) = await batches(obs_url)
    assert row["outcome"] == "running"


@pytest.mark.asyncio
async def test_disabled_refused_with_its_code_until_reset(obs_url, tmp_path):
    """disabled_7d refuses the day before any HTTP, on a refusal row that carries the code; reset-disable lets the next
    trigger adopt that row and run."""
    _, controls = await _night(obs_url, tmp_path, dramas=4)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    days = (EVE - timedelta(days=4), EVE - timedelta(days=2), EVE)
    disabled = breaker.BreakerState(breaker.BreakerDay(EVE, extinguished="rate_limited"), days, EVE)
    await _save_state(obs_url, RuntimeState(breaker=disabled.to_dict()), load_cipher(env), ManualClock(at(EVE, 12)))
    google = FakeGoogle(clock)
    assert await trigger(env, clock, google, controls=controls) == ExitCode.REFUSED
    assert google.seen == []
    (row,) = await batches(obs_url)
    assert (row["outcome"], row["plan_json"], row["status_codes_json"], row["window_end"]) == ("failed", None, ["disabled_7d"], None)
    clock.advance(1800)
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.REFUSED  # every trigger, while it lasts
    assert await cmd_reset_disable.run(["--operator", "ops"], environ=env, clock=clock, out=io.StringIO()) == 0
    clock.advance(1800)
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    (row,) = await batches(obs_url)
    assert row["outcome"] == "withheld" and row["plan_json"] is not None and row["status_codes_json"] == []


@pytest.mark.asyncio
async def test_all_zero_rate_and_usertype_alert(obs_url, tmp_path):
    """Day 2's bare series go all-zero and its userType changes half-way: all_zero_jump and usertype_changed."""
    catalog, controls = await _night(obs_url, tmp_path, dramas=20)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    clock.advance((at(TARGET, 22, 0) - clock.now()).total_seconds())
    zero = [payload["title"] for payload in catalog]
    flipping = FakeGoogle(clock, zero=zero, user_type=lambda ordinal: "USER_TYPE_LEGIT_USER" if ordinal < 20 else "USER_TYPE_SCRAPER")
    assert await trigger(env, clock, flipping, controls=controls) == ExitCode.OK
    first, second = await batches(obs_url)
    assert first["summary_json"]["all_zero_rate"] == 0.0 and first["summary_json"]["user_types"] == ["USER_TYPE_LEGIT_USER"]
    assert second["summary_json"]["all_zero_rate"] == 1.0
    assert second["summary_json"]["user_types"] == ["USER_TYPE_LEGIT_USER", "USER_TYPE_SCRAPER"]
    assert {"all_zero_jump", "usertype_changed"} <= set(second["status_codes_json"])
    assert "all_zero_jump" not in first["status_codes_json"]


# ---- the weekly contract check ----------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contract_check_off_by_default_and_parse_error_on(obs_url, tmp_path):
    """Off unless PICK_OBS_CONTRACT_CHECK=1 (U12). On, on the Monday target date, its two units go first; a parse
    failure writes parse_error and no value, and the code stays on later rows until a check passes."""
    await seed_catalog(obs_url, [drama(1, listed_at=EVE)])
    controls = write_controls(tmp_path, [])
    monday = TARGET + timedelta(days=2)
    env = trends_env(obs_url)
    clock = ManualClock(at(monday - timedelta(days=1), 22, 0))
    off = FakeGoogle(clock)
    assert await trigger(env, clock, off, controls=controls) == ExitCode.OK
    assert "contract_check" not in {row["budget_item"] for row in await request_rows(obs_url)}
    await execute(obs_url, "delete from ggwp_obs_batches")
    await execute(obs_url, "update ggwp_obs_runtime set cookie_jar = null, user_agent = null, cookie_warmed_at = null where channel = 'trends'")
    on = trends_env(obs_url, key=env["PICK_OBS_STATE_KEY"], PICK_OBS_CONTRACT_CHECK="1")
    broken = FakeGoogle(clock, script={3: "bad"})  # warm-up, explore, then the multiline answers a changed shape
    assert await trigger(on, clock, broken, controls=controls) == ExitCode.OK
    (monday_row,) = await batches(obs_url)
    assert monday_row["status_codes_json"] == ["parse_error"]
    checks = [row for row in await raw_rows(obs_url) if row["params_json"]["item"] == "contract_check"]
    assert checks and all(row["data_json"] is None for row in checks if row["fetch_status"] == "parse_error")
    clock.advance((at(monday, 22, 0) - clock.now()).total_seconds())
    assert await trigger(on, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    assert (await batches(obs_url))[-1]["status_codes_json"] == ["parse_error"]  # carried: no check on Tuesday


@pytest.mark.asyncio
async def test_contract_check_without_an_answer_keeps_parse_error(obs_url, tmp_path):
    """A Monday check that gets no usable answer (its first unit a 429) is no verdict: the parse_error carried from the
    batch before stays on the row, rather than being cleared for a week by a check that never read a thing."""
    await seed_catalog(obs_url, [drama(1, listed_at=EVE)])
    controls = write_controls(tmp_path, [])
    monday = TARGET + timedelta(days=2)
    env = trends_env(obs_url, PICK_OBS_CONTRACT_CHECK="1")
    clock = ManualClock(at(monday - timedelta(days=1), 22, 0))
    assert await trigger(env, clock, FakeGoogle(clock, script={3: "bad"}), controls=controls) == ExitCode.OK
    assert (await batches(obs_url))[-1]["status_codes_json"] == ["parse_error"]
    next_monday = monday + timedelta(days=7)
    clock.advance((at(next_monday - timedelta(days=1), 22, 0) - clock.now()).total_seconds())
    limited = FakeGoogle(clock, script={2: "429"})  # warm-up, then the first check's explore
    assert await trigger(env, clock, limited, controls=controls) == ExitCode.OK
    last = (await batches(obs_url))[-1]
    assert last["target_date"] == f"{next_monday:%Y-%m-%d}" and last["summary_json"]["contract_check"] is None
    assert "parse_error" in last["status_codes_json"]


# ---- TR-20's seam -----------------------------------------------------------------------------------------------------


class FixedSource:
    """A stand-in task source for the stable mode, whose own (TR-18's WatchTaskSource) is not here yet."""

    name = "fixed"

    def __init__(self, units):
        self._units = tuple(units)

    async def units(self, step, *, target_date):
        return SourceUnits(self._units, None, {})


def _unit(item: str, term: str, geo: str, identity: str | None = None) -> QueryUnit:
    return QueryUnit(unit_key(item, identity or term, geo, "H"), item, geo, (term,), term, "H", 1 if item == "market" else 3, identity=identity)


PUBLISHED = {"published": Published("set-1"), "gated": Published(None, ("not_published_low_coverage",))}


@pytest.mark.parametrize("answer", sorted(PUBLISHED))
@pytest.mark.asyncio
async def test_publish_seam_for_tr20(obs_url, tmp_path, answer):
    """TR-20's seam in a stable session: after the units, a refetch hook runs a unit again through the same gate (paced,
    reserved, logged); then the publisher gets the whole finishing picture in the finishing step (the batch, the
    summary document, the codes, the uncovered dramas in the contract's shape) and returns the set id and the codes it
    adds. The row is published or withheld with them."""
    env = trends_env(obs_url, mode="stable")
    clock = ManualClock(at(EVE, 20, 30))
    google = FakeGoogle(clock, script={5: "429"})  # the second unit fails at its multiline; the third starts with the probe
    title = _unit("title", "synthetic drama 1", "US", identity_of(drama(1)))
    units = [_unit("market", "short drama", "US"), title, _unit("market", "Kurzdrama", "DE")]
    seen = {}

    async def refetch(run_unit):
        seen["refetched"] = await run_unit(units[0])

    async def publish(step, finishing):
        seen["finishing"] = finishing
        return PUBLISHED[answer]

    wiring = Wiring(clock=clock, rng=random_source(3), transport=google.transport())
    day = Day(settings_from(env), TARGET, settings_from(env).limits)
    async with collector_session("trends", clock=clock, environ=env) as session:
        status = await run_session(day, FixedSource(units), writer=session.writer, cipher=load_cipher(env), wiring=wiring, publish=publish, refetch=refetch)
    assert status == ExitCode.OK
    finishing = seen["finishing"]
    assert finishing.document["fetched_units"] == 2 and finishing.codes == ()
    assert finishing.uncovered_dramas == ()  # the title failed (a 429), it was not left uncovered
    assert [unit["status"] for unit in finishing.document["failed_units"]] == ["rate_limited"]
    assert seen["refetched"].result.status.value == "ok" and len(google.seen) == 1 + 2 * 4
    (batch,) = await batches(obs_url)
    assert len(await request_rows(obs_url)) == await budget_requests(obs_url) == batch["requests"] == len(google.seen)
    expected = ("published", "set-1", []) if answer == "published" else ("withheld", None, ["not_published_low_coverage"])
    assert (batch["outcome"], batch["published_set_id"], batch["status_codes_json"]) == expected


def test_published_codes_are_contract_codes():
    with pytest.raises(ValueError):
        Published("set-1", ("low_coverage",))


# ---- the read-only commands -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_status_and_selfcheck_only(obs_url, tmp_path):
    _, controls = await _night(obs_url, tmp_path, dramas=3)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    before = await runtime_row(obs_url)
    out = io.StringIO()
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls, argv=["status"], out=out) == ExitCode.OK
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert lines[0]["runtime"]["lease_owner"] is None and {"budget", "batch", "plan"} <= {key for line in lines for key in line}
    (plan,) = [line["plan"] for line in lines if "plan" in line]  # the night's task list in figures (G3 seam 1)
    assert (plan["target_date"], plan["plan"], plan["reasons"]) == ("2026-09-26", 220, []) and plan["planned_requests"] > 0
    assert plan["controls"]["positive"] == {"listed": 2, "matched": 2} and plan["missing_controls"] == 0
    assert all("plan_json" not in line.get("batch", {}) for line in lines)  # the task list itself is not printed
    assert env["PICK_OBS_STATE_KEY"] not in out.getvalue() and "cookie" not in out.getvalue().lower()
    out = io.StringIO()
    google = FakeGoogle(clock)
    assert await trigger(env, clock, google, controls=controls, argv=["--selfcheck-only"], out=out) == ExitCode.OK
    assert "selfcheck ok" in out.getvalue() and google.seen == []
    assert await runtime_row(obs_url) == before  # neither command took the lease or wrote anything


BAD_CONFIGURATIONS = {
    "mode": {"PICK_OBS_TRENDS_MODE": "canary9"},
    "state_key": {"PICK_OBS_STATE_KEY": None},
    "egress_url": {"PICK_OBS_EGRESS_ECHO_URL": "http://echo.invalid/secret-path"},
    "controls": {},  # the control list is missing
}


@pytest.mark.parametrize("bad", sorted(BAD_CONFIGURATIONS))
@pytest.mark.asyncio
async def test_selfcheck_only_checks_the_session_configuration(obs_url, tmp_path, bad):
    """S6 runs --selfcheck-only once after the deploy: it checks what a run checks before it reads or sends anything
    (the mode, the control list, the state key, the egress URL), so a bad value exits 2 then, not at the first
    night's trigger. No request, no lease, no value echoed."""
    _, controls = await _night(obs_url, tmp_path, dramas=3)
    env = {name: value for name, value in {**trends_env(obs_url), **BAD_CONFIGURATIONS[bad]}.items() if value is not None}
    clock = ManualClock(at(EVE, 12, 0))  # daytime, as S6 is
    google, out, err = FakeGoogle(clock), io.StringIO(), io.StringIO()
    path = tmp_path / "absent.json" if bad == "controls" else controls
    status = await trigger(env, clock, google, controls=path, argv=["--selfcheck-only"], out=out, err=err)
    assert (status, google.seen, out.getvalue()) == (ExitCode.REFUSED, [], "")
    assert "canary9" not in err.getvalue() and "secret-path" not in err.getvalue()
    assert (await runtime_row(obs_url))["lease_generation"] == 0


@pytest.mark.asyncio
async def test_unknown_variables_are_named(obs_url, tmp_path):
    """Plan S5 names PICK_OBS_EGRESS_URL; the code reads PICK_OBS_EGRESS_ECHO_URL. A PICK_OBS_* variable the trends
    entry does not read is named on stderr with the closest name it does read (never its value), and the command goes
    on as it would."""
    _, controls = await _night(obs_url, tmp_path, dramas=3)
    env = trends_env(obs_url, PICK_OBS_EGRESS_URL="https://echo.example/secret-path")
    clock = ManualClock(at(EVE, 12, 0))
    out, err = io.StringIO(), io.StringIO()
    status = await trigger(env, clock, FakeGoogle(clock), controls=controls, argv=["--selfcheck-only"], out=out, err=err)
    assert status == ExitCode.OK and "selfcheck ok" in out.getvalue()
    assert "PICK_OBS_EGRESS_URL" in err.getvalue() and "PICK_OBS_EGRESS_ECHO_URL" in err.getvalue()
    assert "secret-path" not in err.getvalue() and "echo.example" not in err.getvalue()
