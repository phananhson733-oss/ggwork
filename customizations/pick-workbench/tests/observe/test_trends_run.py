"""TR-14: the nightly Trends session through the real entry point, on both dialects (plan TR-14; design 3.2, 4.5, 4.9,
4.10, 4.11, 6.3; D23, D34; counterexamples 1 and 10).

Each test seeds a shared catalog batch, writes its own copy of the canary's control list, and fires cron triggers at
chosen times on a ManualClock against FakeGoogle (MockTransport): the night passes in seconds and nothing leaves the
process. What the session wrote is read back through a separate engine.
"""

import io
import json
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
from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.crypto import load_cipher
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.instants import stamp
from ggwork_pick.observe.lease import DbStateStore, LeasedWriter
from ggwork_pick.observe.state import RuntimeState
from ggwork_pick.observe.trends import breaker


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


@pytest.mark.parametrize("when", [at(EVE, 21, 30), at(TARGET, 1, 45), at(TARGET, 1, 55)])
@pytest.mark.asyncio
async def test_outside_the_window_does_nothing(obs_url, tmp_path, when):
    """Before 22:00 (canary) or from the 01:45 hard deadline: exit 0, no HTTP, not even the lease."""
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
    clock.advance((at(TARGET, 0, 30) - clock.now()).total_seconds())
    assert await trigger(env, clock, FakeGoogle(clock), controls=controls) == ExitCode.OK
    (after,) = await batches(obs_url)
    assert after["id"] == batch["id"] and after["target_date"] == "2026-09-26"
    assert after["window_end"] == stamp(at(EVE, 20)) != stamp(at(EVE, 21))
    assert after["plan_json"] == batch["plan_json"]  # the task list is not recomputed either
    assert done_before < set(after["summary_json"]["units"]) and after["outcome"] == "withheld"
    budget = await rows(obs_url, "select budget_day from ggwp_obs_budget where channel = 'trends'")
    assert [row["budget_day"] for row in budget] == ["2026-09-26"]  # midnight did not open a new budget day (D23)


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


# ---- the breaker, the canary's end, and the daily alerts --------------------------------------------------------------


@pytest.mark.asyncio
async def test_canary_terminate_rule(obs_url, tmp_path):
    """[design 4.11] A canary put out twice (here: a sorry page, then five 429s) writes canary_terminated and never runs
    again: every later trigger exits 2 with no HTTP, and the row keeps the red code."""
    _, controls = await _night(obs_url, tmp_path, dramas=6)
    env = trends_env(obs_url)
    clock = ManualClock(at(EVE, 22, 0))
    walled = FakeGoogle(clock, script={3: "sorry"})
    assert await trigger(env, clock, walled, controls=controls) == ExitCode.OK
    (first,) = await batches(obs_url)
    assert "extinguished_today" in first["status_codes_json"] and "canary_terminated" not in first["status_codes_json"]
    clock.advance((at(TARGET, 22, 0) - clock.now()).total_seconds())
    limited = FakeGoogle(clock, script={ordinal: "429" for ordinal in range(2, 40)})
    assert await trigger(env, clock, limited, controls=controls) == ExitCode.OK
    second = (await batches(obs_url))[-1]
    assert {"extinguished_today", "canary_terminated"} <= set(second["status_codes_json"])
    for later in (at(TARGET + timedelta(days=1), 1, 40), at(TARGET + timedelta(days=1), 22, 0)):
        clock.advance((later - clock.now()).total_seconds())
        refused = FakeGoogle(clock)
        assert await trigger(env, clock, refused, controls=controls) == ExitCode.REFUSED
        assert refused.seen == []
    assert "canary_terminated" in (await batches(obs_url))[-1]["status_codes_json"]
    # TR-30's fix-and-rerun starts the count afresh from a date, without deleting a row.
    rerun = FakeGoogle(clock)
    since = trends_env(obs_url, key=env["PICK_OBS_STATE_KEY"], PICK_OBS_CANARY_SINCE=f"{clock.now().date() + timedelta(days=1):%Y-%m-%d}")
    assert await trigger(since, clock, rerun, controls=controls) == ExitCode.OK and rerun.seen
    assert "canary_terminated" not in (await batches(obs_url))[-1]["status_codes_json"]


async def _save_state(url, state, cipher, clock) -> None:
    db = open_db(url)
    try:
        async with await LeasedWriter.acquire(db, "trends", clock=clock, owner="proc-setup") as writer:
            await DbStateStore(writer, cipher).save(state)
    finally:
        await db.dispose()


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
    assert lines[0]["runtime"]["lease_owner"] is None and {"budget", "batch"} <= {key for line in lines for key in line}
    assert env["PICK_OBS_STATE_KEY"] not in out.getvalue() and "cookie" not in out.getvalue().lower()
    out = io.StringIO()
    google = FakeGoogle(clock)
    assert await trigger(env, clock, google, controls=controls, argv=["--selfcheck-only"], out=out) == ExitCode.OK
    assert "selfcheck ok" in out.getvalue() and google.seen == []
    assert await runtime_row(obs_url) == before  # neither command took the lease or wrote anything
