"""Approved daily recovery: real executor/temporary DB, no external Google traffic."""

import io
import json
from datetime import timedelta

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from obs_db_helpers import execute, migrated
from top_dramas_fixtures import board_drama
from trends_fake_google import FakeGoogle
from trends_session_helpers import EVE, TARGET, at, batches, request_rows, seed_catalog, trends_env, trigger

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode, Refused
from ggwork_pick.observe.trends.settings import settings_from

KEY = Fernet.generate_key().decode()
PROFILE = {
    "PICK_OBS_TRENDS_GRANULARITY": "D",
    "PICK_OBS_TRENDS_ROUTE": "a_only",
    "PICK_OBS_TRENDS_PACE": "conservative",
    "PICK_OBS_CANARY_SINCE": str(TARGET),
    "PICK_OBS_TRENDS_RECOVERY_SINCE": str(TARGET),
}


def test_profile_has_bounded_daily_capacity():
    settings = settings_from({"PICK_OBS_TRENDS_MODE": "stable", **PROFILE})
    assert settings.recovery_since == TARGET
    assert settings.limits.plan == settings.limits.cap == 220
    assert settings.pace_params.refill_per_minute == 1
    assert settings.pace_params.hour_cap == 60


@pytest.mark.parametrize(
    "patch",
    [
        {"PICK_OBS_TRENDS_MODE": "canary1"},
        {"PICK_OBS_TRENDS_GRANULARITY": "H"},
        {"PICK_OBS_TRENDS_ROUTE": "both"},
        {"PICK_OBS_TRENDS_PACE": "user"},
        {"PICK_OBS_CANARY_SINCE": ""},
        {"PICK_OBS_CONTRACT_CHECK": "1"},
        {"PICK_OBS_PUBLISH": "1"},
    ],
)
def test_profile_refuses_mixed_or_unbounded_configuration(patch):
    with pytest.raises(Refused):
        settings_from({"PICK_OBS_TRENDS_MODE": "stable", **PROFILE, **patch})


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    url = await migrated(pick_db_url, tmp_path)
    await seed_catalog(url, [board_drama(i, title=f"Recovery Drama {i}", boards={"qc": i}) for i in range(1, 101)])
    return url


async def night(url, tmp_path, offset=0, script=None):
    clock = ManualClock(at(EVE + timedelta(days=offset), 17, 30))
    google = FakeGoogle(clock, script=script or {})
    env = trends_env(url, mode="stable", key=KEY, **PROFILE)
    code = await trigger(env, clock, google, controls=tmp_path / "none.json")
    return code, google


@pytest.mark.asyncio
async def test_advances_only_on_later_nights_and_remains_at_100(obs_url, tmp_path):
    for offset, target in enumerate((10, 30, 100, 100)):
        code, google = await night(obs_url, tmp_path, offset)
        assert code == ExitCode.OK
        batch = (await batches(obs_url))[-1]
        assert batch["planned_units"] == target
        assert batch["plan_json"]["notes"]["cap"] == 2 * target + 20
        assert batch["summary_json"]["daily_recovery"]["qualified"] is True
        assert batch["summary_json"]["daily_recovery"]["request_rows"] == 2 * target + 1
        # The real API projection exposes the stored phase, never a new catalog selection.
        from engines import host_engine
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from ggwork_pick.observe.trends_table import trends_table
        from ggwork_pick.repository import PickRepository

        engine = host_engine(obs_url)
        try:
            table = await trends_table(PickRepository.shared(async_sessionmaker(engine)), now=at(TARGET + timedelta(days=offset), 3))
            assert table["batch"]["daily_recovery"] == {
                "since": str(TARGET),
                "target": target,
                "qualified_nights": min(offset, 3),
                "qualified": True,
            }
        finally:
            await engine.dispose()
        assert batch["plan_json"]["notes"]["daily_recovery"]["qualified_nights"] == min(offset, 3)
        assert len(google.seen) == 2 * target + 1
        assert {seen.phase for seen in google.seen} <= {"warmup", "explore", "multiline"}
        # The same target date cannot start the next stage or emit a second request set.
        _, again = await night(obs_url, tmp_path, offset)
        assert again.seen == []


@pytest.mark.asyncio
async def test_failed_unit_does_not_advance_the_next_night(obs_url, tmp_path):
    code, _ = await night(obs_url, tmp_path, script={2: "bad"})
    assert code == ExitCode.OK
    assert (await batches(obs_url))[0]["summary_json"]["daily_recovery"]["qualified"] is False
    code, _ = await night(obs_url, tmp_path, offset=1)
    assert code == ExitCode.OK
    assert (await batches(obs_url))[-1]["planned_units"] == 10


@pytest.mark.asyncio
async def test_a_wall_stops_the_campaign_and_preserves_history(obs_url, tmp_path):
    await night(obs_url, tmp_path, script={2: "sorry"})
    before = len(await request_rows(obs_url))
    code, google = await night(obs_url, tmp_path, offset=1)
    assert code == ExitCode.REFUSED and google.seen == []
    assert len(await request_rows(obs_url)) == before
    assert "canary_terminated" in (await batches(obs_url))[-1]["status_codes_json"]


@pytest.mark.asyncio
@pytest.mark.parametrize("wall", [False, True])
@pytest.mark.parametrize("changed", ["epoch", "remove"])
async def test_active_campaign_cannot_be_bypassed_with_settings(obs_url, tmp_path, wall, changed):
    await night(obs_url, tmp_path, script={2: "sorry"} if wall else {})
    profile = dict(PROFILE)
    if changed == "epoch":
        profile["PICK_OBS_CANARY_SINCE"] = profile["PICK_OBS_TRENDS_RECOVERY_SINCE"] = str(TARGET + timedelta(days=1))
    else:
        profile.pop("PICK_OBS_TRENDS_RECOVERY_SINCE")
        profile["PICK_OBS_TRENDS_PACE"] = "user"
    clock = ManualClock(at(EVE + timedelta(days=1), 17, 30))
    google = FakeGoogle(clock)
    code = await trigger(trends_env(obs_url, mode="stable", key=KEY, **profile), clock, google, controls=tmp_path / "none.json")
    assert code == ExitCode.REFUSED and google.seen == []


@pytest.mark.asyncio
async def test_finished_summary_without_certificate_is_not_qualification(obs_url, tmp_path):
    await night(obs_url, tmp_path)
    row = (await batches(obs_url))[0]
    summary = dict(row["summary_json"])
    summary.pop("daily_recovery")
    await execute(obs_url, "update ggwp_obs_batches set summary_json=:summary where id=:id", summary=json.dumps(summary), id=row["id"])
    await night(obs_url, tmp_path, offset=1)
    assert (await batches(obs_url))[-1]["planned_units"] == 10


@pytest.mark.asyncio
async def test_wall_recorded_before_finish_crash_still_blocks_next_night(obs_url, tmp_path, monkeypatch):
    from ggwork_pick.observe.trends import run

    original = run._finish

    async def crash(*args, **kwargs):
        raise RuntimeError("synthetic failure before finishing")

    monkeypatch.setattr(run, "_finish", crash)
    code, _ = await night(obs_url, tmp_path, script={2: "sorry"})
    assert code == ExitCode.FAILED
    monkeypatch.setattr(run, "_finish", original)
    code, google = await night(obs_url, tmp_path, offset=1)
    assert code == ExitCode.REFUSED and google.seen == []


@pytest.mark.asyncio
async def test_crash_resume_keeps_stage_and_unknown_request_does_not_qualify(obs_url, tmp_path):
    def crash(ordinal, phase):
        if ordinal == 8:
            raise RuntimeError("synthetic transport crash")

    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock, on_request=crash)
    env = trends_env(obs_url, mode="stable", key=KEY, **PROFILE)
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.FAILED
    before = (await batches(obs_url))[0]
    clock.advance(600)
    resumed = FakeGoogle(clock)
    assert await trigger(env, clock, resumed, controls=tmp_path / "none.json") == ExitCode.OK
    after = (await batches(obs_url))[0]
    assert after["plan_json"] == before["plan_json"] and after["planned_units"] == 10
    assert after["fetched_units"] == 10
    assert after["summary_json"]["daily_recovery"]["qualified"] is False
    await night(obs_url, tmp_path, offset=1)
    assert (await batches(obs_url))[-1]["planned_units"] == 10


@pytest.mark.asyncio
async def test_insufficient_candidates_are_not_a_smaller_successful_stage(obs_url, tmp_path):
    await execute(obs_url, "delete from ggwp_drama_versions")
    await seed_catalog(obs_url, [board_drama(1, title="Only One", boards={"qc": 1})], batch_id="cat-small")
    code, google = await night(obs_url, tmp_path)
    assert code == ExitCode.REFUSED and google.seen == []
    assert (await batches(obs_url))[-1]["plan_json"] is None


@pytest.mark.asyncio
async def test_google_no_data_is_a_valid_response_not_a_failed_request(obs_url, tmp_path):
    class EmptyGoogle(FakeGoogle):
        def _ok(self, request, phase, ordinal):
            if phase == "multiline":
                return httpx.Response(200, text=")]}'\n" + json.dumps({"default": {"timelineData": []}}), request=request)
            return super()._ok(request, phase, ordinal)

    clock = ManualClock(at(EVE, 17, 30))
    google = EmptyGoogle(clock)
    assert await trigger(trends_env(obs_url, mode="stable", key=KEY, **PROFILE), clock, google, controls=tmp_path / "none.json") == ExitCode.OK
    batch = (await batches(obs_url))[0]
    assert batch["summary_json"]["daily_recovery"]["qualified"] is True
    assert {entry["series"] for entry in batch["summary_json"]["units"].values()} == {"no_data"}


@pytest.mark.asyncio
async def test_approved_epoch_preserves_old_stops_and_preflight_is_readonly(obs_url, tmp_path):
    for ago in (1, 2):
        day = str(TARGET - timedelta(days=ago))
        await execute(
            obs_url,
            "insert into ggwp_obs_budget (channel,budget_day,collect_mode,extinguished_at,extinguish_reason) values ('trends',:day,'canary1',:at,'trips')",
            day=day,
            at=f"{day}T01:00:00.000000+00:00",
        )
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    output = io.StringIO()
    code = await trigger(trends_env(obs_url, mode="stable", key=KEY, **PROFILE), clock, google, controls=tmp_path / "none.json", argv=["preflight"], out=output)
    assert code == ExitCode.OK and google.seen == []
    report = json.loads(output.getvalue())["preflight"]
    assert report["planned_units"] == 10 and report["plan"] == 40 and report["min_requests"] == 20
    assert report["cap"] == 40
    assert report["daily_recovery"]["qualified_nights"] == 0
    assert await batches(obs_url) == []
    assert (await night(obs_url, tmp_path))[0] == ExitCode.OK
    from obs_db_helpers import rows

    old = await rows(obs_url, "select budget_day from ggwp_obs_budget where collect_mode='canary1'")
    assert len(old) == 2


@pytest.mark.asyncio
async def test_qualified_history_survives_raw_request_retention(obs_url, tmp_path):
    for offset, target in enumerate((10, 30)):
        assert (await night(obs_url, tmp_path, offset))[0] == ExitCode.OK
        row = (await batches(obs_url))[-1]
        assert row["planned_units"] == target and row["summary_json"]["daily_recovery"]["qualified"]
        await execute(obs_url, "delete from ggwp_obs_raw where batch_id=:id", id=row["id"])
        await execute(obs_url, "delete from ggwp_obs_requests where batch_id=:id", id=row["id"])
    assert (await night(obs_url, tmp_path, offset=2))[0] == ExitCode.OK
    assert (await batches(obs_url))[-1]["planned_units"] == 100


@pytest.mark.asyncio
async def test_certificate_and_finish_are_atomic(obs_url, tmp_path, monkeypatch):
    from ggwork_pick.observe.trends import recovery

    original = recovery.certificate

    async def crash_after_certificate(*args, **kwargs):
        proof = await original(*args, **kwargs)
        assert proof["qualified"]
        raise RuntimeError("synthetic crash after certificate construction")

    monkeypatch.setattr(recovery, "certificate", crash_after_certificate)
    assert (await night(obs_url, tmp_path))[0] == ExitCode.FAILED
    row = (await batches(obs_url))[0]
    assert row["finished_at"] is None and "daily_recovery" not in row["summary_json"]
    monkeypatch.setattr(recovery, "certificate", original)
    assert (await night(obs_url, tmp_path, offset=1))[0] == ExitCode.OK
    assert (await batches(obs_url))[-1]["planned_units"] == 10


@pytest.mark.asyncio
async def test_refusal_adopted_later_is_not_a_qualifying_night(obs_url, tmp_path):
    await execute(obs_url, "delete from ggwp_drama_versions")
    await seed_catalog(obs_url, [board_drama(1, title="Only One", boards={"qc": 1})], batch_id="cat-small")
    assert (await night(obs_url, tmp_path))[0] == ExitCode.REFUSED
    await seed_catalog(
        obs_url, [board_drama(i, title=f"Restored {i}", boards={"qc": i}) for i in range(1, 101)], batch_id="cat-restored", published_at=at(EVE, 13)
    )
    assert (await night(obs_url, tmp_path))[0] == ExitCode.OK
    row = (await batches(obs_url))[0]
    assert row["plan_json"]["notes"]["late_admission"] is True
    assert row["summary_json"]["daily_recovery"]["qualified"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["request", "raw"])
async def test_certificate_requires_actual_successful_request_and_raw_rows(obs_url, tmp_path, missing):
    from ggwork_pick.observe.lease import status_reader
    from ggwork_pick.observe.trends.recovery import certificate
    from ggwork_pick.observe.trends.units import SessionPlan

    await night(obs_url, tmp_path)
    row = (await batches(obs_url))[0]
    if missing == "request":
        await execute(obs_url, "update ggwp_obs_requests set status_code=429 where batch_id=:id and endpoint='multiline'", id=row["id"])
    else:
        await execute(obs_url, "delete from ggwp_obs_raw where batch_id=:id", id=row["id"])
    async with status_reader("trends", environ=trends_env(obs_url, mode="stable", key=KEY, **PROFILE)) as step:
        proof = await certificate(step, row["id"], SessionPlan.from_dict(row["plan_json"]), row["summary_json"])
    assert proof["qualified"] is False
