"""Synthetic Google only: groups spend HTTP once, each drama retains its own evidence."""

from datetime import timedelta

import pytest
from test_trends_daily_recovery import KEY, PROFILE, obs_url  # noqa: F401
from trends_fake_google import FakeGoogle
from trends_session_helpers import EVE, at, batches, trends_env, trigger

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode


@pytest.mark.asyncio
async def test_five_term_nights_budget_rows_pacing_and_stage_certificate(obs_url, tmp_path):  # noqa: F811
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.observe.trends_table import trends_table
    from ggwork_pick.repository import PickRepository

    for offset, target in enumerate((10, 30, 100)):
        clock = ManualClock(at(EVE + timedelta(days=offset), 17, 30))
        google = FakeGoogle(clock, zero={"Recovery Drama 1"})
        env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
        assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.OK
        batch = (await batches(obs_url))[-1]
        assert batch["planned_units"] == target // 5
        proof = batch["summary_json"]["daily_recovery"]
        assert proof["qualified"] is True and proof["raw_rows"] == target
        assert proof["request_rows"] == 2 * (target // 5) + 1
        assert batch["plan_json"]["notes"]["cap"] == 2 * (target // 5) + 20
        queries = [r for r in google.seen if r.phase == "explore"]
        assert len(queries) == target // 5 and all(len(r.terms) == 5 for r in queries)
        multis = [r for r in google.seen if r.phase == "multiline"]
        for previous, next_query in zip(multis, queries[1:]):
            assert (next_query.sent_at - previous.done_at).total_seconds() >= 120
        engine = host_engine(obs_url)
        try:
            table = await trends_table(PickRepository.shared(async_sessionmaker(engine, expire_on_commit=False)), now=clock.now())
            assert table["batch"]["counts"]["planned"] == target
            assert len({r["identity"] for r in table["rows"]}) == target
            assert len({r["query_group"] for r in table["rows"]}) == target // 5
            assert all(len(r["comparison_terms"]) == 5 and r["term"] in r["comparison_terms"] for r in table["rows"])
            assert all(r["result"] == "data" for r in table["rows"])
            assert table["rows"][0]["status"] == "ok_zero"
            assert all(p["value"] == 0 for p in table["rows"][0]["series"])
            assert [r["title"] for r in table["rows"]] == [f"Recovery Drama {i}" for i in range(1, target + 1)]
        finally:
            await engine.dispose()


def test_duplicate_names_are_never_merged_and_old_plan_format_roundtrips():
    from ggwork_pick.observe.trends.top_dramas import Pick, grouped_units, query_unit
    from ggwork_pick.observe.trends.units import QueryUnit

    picks = [Pick(f"id{i}", t, "platform", "en", t, ()) for i, t in enumerate(["Same", "Other", "Same", "Third", "Fourth", "Fifth", "Sixth"])]
    singles = tuple(query_unit(p, i + 1) for i, p in enumerate(picks))
    assert QueryUnit.from_dict(singles[0].to_dict()) == singles[0]
    assert "members" not in singles[0].to_dict()
    grouped = grouped_units(singles)
    assert [len(g.members) for g in grouped] == [2, 5]
    assert [m[1] for g in grouped for m in g.members] == [p.identity for p in picks]
    assert all(QueryUnit.from_dict(g.to_dict()) == g for g in grouped)


@pytest.mark.asyncio
async def test_group_failure_marks_each_drama_unknown_and_cannot_qualify(obs_url, tmp_path):  # noqa: F811
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock, fail_phases={"explore": "429"})
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    await trigger(env, clock, google, controls=tmp_path / "none.json")
    batch = (await batches(obs_url))[-1]
    assert batch["summary_json"]["daily_recovery"]["qualified"] is False
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from ggwork_pick.observe.trends_table import trends_table
    from ggwork_pick.repository import PickRepository

    engine = host_engine(obs_url)
    try:
        table = await trends_table(PickRepository.shared(async_sessionmaker(engine, expire_on_commit=False)), now=clock.now())
        assert len(table["rows"]) == 10
        assert all(r["result"] == "not_fetched" and r["series"] is None for r in table["rows"])
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_explicit_receipt_crosses_old_stop_once_and_keeps_new_stops(obs_url, tmp_path):  # noqa: F811
    import json

    from test_trends_daily_recovery import night
    from trends_session_helpers import TARGET

    code, _ = await night(obs_url, tmp_path, script={2: "sorry"})
    old = (await batches(obs_url))[-1]
    assert old["summary_json"]["daily_recovery"]["qualified"] is False
    since = TARGET + timedelta(days=1)
    profile = {
        **PROFILE,
        "PICK_OBS_TRENDS_BATCH_SIZE": "5",
        "PICK_OBS_TRENDS_PACE": "batched",
        "PICK_OBS_CANARY_SINCE": str(since),
        "PICK_OBS_TRENDS_RECOVERY_SINCE": str(since),
    }
    clock = ManualClock(at(EVE + timedelta(days=1), 17, 30))
    google = FakeGoogle(clock)
    env = trends_env(obs_url, mode="stable", key=KEY, **profile)
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.REFUSED
    assert not google.seen
    receipt = {
        "id": "synthetic-human-approval",
        "from_since": str(TARGET),
        "to_since": str(since),
        "previous_batch_id": old["id"],
        "approved_at": str(EVE + timedelta(days=1)) + "T17:00:00+00:00",
    }
    profile["PICK_OBS_TRENDS_RECOVERY_APPROVAL"] = json.dumps(receipt)
    # Refusal is audited but first acceptance later that night is late; no stage credit.
    google = FakeGoogle(clock, script={2: "sorry"})
    env = trends_env(obs_url, mode="stable", key=KEY, **profile)
    await trigger(env, clock, google, controls=tmp_path / "none.json")
    assert google.seen
    saved = (await batches(obs_url))[0]
    assert saved["id"] == old["id"] and saved["plan_json"] == old["plan_json"] and saved["summary_json"] == old["summary_json"]
    clock = ManualClock(at(EVE + timedelta(days=2), 17, 30))
    google = FakeGoogle(clock)
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.REFUSED
    assert not google.seen


@pytest.mark.asyncio
async def test_group_resume_does_not_repeat_finished_members_or_certify_unknown_request(obs_url, tmp_path):  # noqa: F811
    def crash(ordinal, phase):
        if ordinal == 4:
            raise RuntimeError("synthetic group interruption")

    clock = ManualClock(at(EVE, 17, 30))
    first = FakeGoogle(clock, on_request=crash)
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    assert await trigger(env, clock, first, controls=tmp_path / "none.json") == ExitCode.FAILED
    before = (await batches(obs_url))[-1]
    assert len(before["summary_json"]["units"]) == 1
    clock.advance(600)
    second = FakeGoogle(clock)
    assert await trigger(env, clock, second, controls=tmp_path / "none.json") == ExitCode.OK
    after = (await batches(obs_url))[-1]
    assert after["plan_json"] == before["plan_json"]
    assert after["summary_json"]["daily_recovery"]["qualified"] is False
    assert len([r for r in second.seen if r.phase == "explore"]) == 1
    assert set(second.seen[-1].terms).isdisjoint(first.seen[1].terms)


@pytest.mark.asyncio
async def test_certificate_rejects_raw_attached_to_wrong_drama(obs_url, tmp_path):  # noqa: F811
    from engines import host_engine
    from sqlalchemy import text

    from ggwork_pick.observe.lease import ReadStep
    from ggwork_pick.observe.trends.grouped_recovery import certificate
    from ggwork_pick.observe.trends.units import SessionPlan

    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.OK
    batch = (await batches(obs_url))[-1]
    engine = host_engine(obs_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("update ggwp_obs_raw set identity='wrong-identity' where id=(select min(id) from ggwp_obs_raw)"))
            proof = await certificate(ReadStep(conn), batch["id"], SessionPlan.from_dict(batch["plan_json"]), batch["summary_json"])
            assert proof["qualified"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_epoch_cannot_silently_switch_back_to_single_title_policy(obs_url, tmp_path):  # noqa: F811
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.OK
    clock = ManualClock(at(EVE + timedelta(days=1), 17, 30))
    google = FakeGoogle(clock)
    assert await trigger(trends_env(obs_url, mode="stable", key=KEY, **PROFILE), clock, google, controls=tmp_path / "none.json") == ExitCode.REFUSED
    assert not google.seen


def test_group_identity_cannot_alias_another_request():
    from dataclasses import replace

    from ggwork_pick.observe.trends.top_dramas import Pick, grouped_units, query_unit

    singles = tuple(query_unit(Pick(f"id{i}", f"Title {i}", "p", "en", f"Title {i}", ()), i + 1) for i in range(10))
    first, second = grouped_units(singles)
    with pytest.raises(ValueError):
        replace(second, identity=first.identity)


@pytest.mark.asyncio
async def test_certificate_rejects_out_of_plan_recorded_request(obs_url, tmp_path):  # noqa: F811
    from engines import host_engine
    from sqlalchemy import insert, select

    from ggwork_pick.models import obs_requests
    from ggwork_pick.observe.lease import ReadStep
    from ggwork_pick.observe.trends.grouped_recovery import certificate
    from ggwork_pick.observe.trends.units import SessionPlan

    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock)
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.OK
    batch = (await batches(obs_url))[-1]
    engine = host_engine(obs_url)
    try:
        async with engine.begin() as conn:
            row = dict((await conn.execute(select(obs_requests).where(obs_requests.c.endpoint == "multiline").limit(1))).mappings().one())
            row.pop("id")
            row["identity"] = "not-in-plan"
            await conn.execute(insert(obs_requests).values(**row))
            summary = {**batch["summary_json"], "requests_reserved": batch["summary_json"]["requests_reserved"] + 1}
            proof = await certificate(ReadStep(conn), batch["id"], SessionPlan.from_dict(batch["plan_json"]), summary)
            assert proof["qualified"] is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_legitimate_multiline_retry_keeps_one_raw_line_per_member(obs_url, tmp_path):  # noqa: F811
    clock = ManualClock(at(EVE, 17, 30))
    google = FakeGoogle(clock, script={3: "503"})
    env = trends_env(obs_url, mode="stable", key=KEY, **{**PROFILE, "PICK_OBS_TRENDS_BATCH_SIZE": "5", "PICK_OBS_TRENDS_PACE": "batched"})
    assert await trigger(env, clock, google, controls=tmp_path / "none.json") == ExitCode.OK
    proof = (await batches(obs_url))[-1]["summary_json"]["daily_recovery"]
    assert proof["qualified"] is True and proof["raw_rows"] == 10 and proof["request_rows"] == 7
