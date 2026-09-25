"""D24's lapse ledger (G3 review of the D24 fix, P2): every Trends session's fold is kept in its batch row and carried to
the next session, whether it published a set or not.

Carried only from published set to published set, one withheld night left about four shared batches in the next
window; the shared catalog keeps the newest three unreferenced ones (sync.KEEP_BATCHES), so the oldest was likely
pruned, and a pruned batch lapses every confirmation. The database half runs on SQLite and PostgreSQL; its PostgreSQL
half skips when PICK_TEST_PG_URL is unset.
"""

import json
from datetime import date

import pg
import pytest
import pytest_asyncio
from engines import host_engine
from obs_schema import insert
from sqlalchemy import delete, update

from ggwork_pick.models import drama_versions, import_batches
from ggwork_pick.observe.catalog_history import CATALOG_KIND, SHARED_OWNER, read_sightings
from ggwork_pick.observe.decisions_state import CorrespondenceKey, LapseCause, Sighting, decision_record, effective
from ggwork_pick.observe.errors import ExitCode, StateUnavailable, exit_code_for
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends.lapses import LEDGER_FORMAT, LEDGER_NOTE, LapseLedger, after_of, describe, fold, latest_ledger, ledger_in
from ggwork_pick.observe.trends.units import SessionPlan, TaskList

X = json.dumps(["realshort", "x-650a1b2c", "en"], separators=(",", ":"))
Y = json.dumps(["realshort", "y-650a1b2d", "en"], separators=(",", ":"))
RS, TITLE, RETITLED = "ReelShort", "the alpha", "the alpha returns"
# Two pulls a day (schedule.SLOTS_UTC), as repository.stamp() writes them.
T = (
    "2026-09-22T15:40:00.000000+00:00",
    "2026-09-23T03:40:00.000000+00:00",
    "2026-09-23T15:40:00.000000+00:00",
    "2026-09-24T03:40:00.000000+00:00",
    "2026-09-24T15:40:00.000000+00:00",
    "2026-09-25T03:40:00.000000+00:00",
)


def confirm(row_id, identity=X, title=TITLE):
    body = {"kind": "correspondence_confirm", "request_id": f"r-{row_id}", "identity": identity, "platform": RS, "normalized_title": title}
    return decision_record(row_id, "correspondence_confirm", body)


def key_of(payload) -> CorrespondenceKey | None:
    """A stand-in for TR-18's: the platform as stored, the title folded; no title, no key."""
    title = payload.get("title")
    return CorrespondenceKey(payload.get("theater", ""), title.casefold()) if isinstance(title, str) else None


def ledger(through="b1", through_at=T[0], version=7, causes=None) -> LapseLedger:
    return LapseLedger(through, through_at, version, causes if causes is not None else {7: LapseCause("unverifiable", "b0")})


def plan_with(notes) -> dict:
    return SessionPlan("watch", "H", False, TaskList((), ()), "b1", notes).to_dict()


# ---- the fold (pure) ------------------------------------------------------------------------------------------------


def test_fold_carries_the_previous_ledger_on():
    state = effective((confirm(7), confirm(8, Y)), 8)
    previous = ledger(causes={7: LapseCause("changed", "b0")})
    window = [Sighting("b2", {X: CorrespondenceKey(RS, TITLE)}, T[1]), Sighting("b3", {X: CorrespondenceKey(RS, TITLE)}, T[2])]
    folded = fold(state, previous, window)
    # 7 keeps the cause the earlier fold found although these batches show it as confirmed; 8 is missing from b2.
    assert folded == LapseLedger("b3", T[2], 8, {7: LapseCause("changed", "b0"), 8: LapseCause("absent", "b2")})
    assert folded.lapsed == (7, 8)
    assert folded.fresh(previous) == {8: LapseCause("absent", "b2")}
    assert dict(folded.counts()) == {"changed": 1, "absent": 1, "unverifiable": 0, "carried": 0}
    assert after_of(folded) == T[2] and after_of(None) is None
    # No earlier ledger: only the window counts.
    assert fold(state, None, window[-1:]) == LapseLedger("b3", T[2], 8, {8: LapseCause("absent", "b3")})
    # The window ends with the batch the session judges with, as read_sightings returns it: published_at and all.
    for bad in ([], [Sighting("b3", {})]):
        with pytest.raises(ValueError, match="sightings"):
            fold(state, previous, bad)


def test_ledger_round_trips_and_refuses_what_it_cannot_be():
    kept = LapseLedger("b3", T[2], 9, {9: LapseCause("absent", "b2"), 4: LapseCause("carried", None)})
    assert kept.lapsed == (4, 9)
    stored = json.loads(json.dumps(kept.to_dict()))
    assert stored == {
        "format": LEDGER_FORMAT,
        "through": "b3",
        "through_at": T[2],
        "version": 9,
        "lapsed": [{"id": 4, "reason": "carried", "batch_id": None}, {"id": 9, "reason": "absent", "batch_id": "b2"}],
    }
    assert LapseLedger.from_dict(stored) == kept
    with pytest.raises(TypeError):
        kept.causes[5] = LapseCause("absent", "b2")
    bad_ledgers = (
        {**stored, "format": "trends-lapse-ledger-v0"},
        {**stored, "extra": 1},
        {**stored, "through": ""},
        {**stored, "through_at": "2026-09-23T15:40:00+00:00"},
        {**stored, "version": -1},
        {**stored, "version": True},
        {**stored, "lapsed": {"4": "carried"}},
        {**stored, "lapsed": [{"id": 10, "reason": "absent", "batch_id": "b2"}]},  # above the version
        {**stored, "lapsed": [{"id": 4, "reason": "moved", "batch_id": "b2"}]},
        {**stored, "lapsed": [{"id": 4, "reason": "absent", "batch_id": 2}]},
        {**stored, "lapsed": [{"id": 4, "reason": "absent"}]},
        {**stored, "lapsed": [{"id": 4, "reason": "absent", "batch_id": "b2"}, {"id": 4, "reason": "changed", "batch_id": "b2"}]},
        [],
    )
    for bad in bad_ledgers:
        with pytest.raises(ValueError):
            LapseLedger.from_dict(bad)


def test_the_ledger_lives_in_the_plan_notes():
    """Written with the plan when the batch is created and read back unchanged on a resume after midnight."""
    kept = ledger()
    plan = plan_with({"contract_check": False, LEDGER_NOTE: kept.to_dict()})
    assert ledger_in(json.loads(json.dumps(plan))) == kept
    assert ledger_in(SessionPlan.from_dict(plan).to_dict()) == kept
    # A canary session's plan and a refusal row keep none.
    assert ledger_in(plan_with({"contract_check": False})) is None
    assert ledger_in(None) is None
    for bad in (plan_with({LEDGER_NOTE: {"format": "?"}}), {"format": "x", "notes": []}, "plan"):
        with pytest.raises(ValueError):
            ledger_in(bad)


def test_describe_names_the_pruned_batches():
    previous = ledger(causes={7: LapseCause("changed", "b0")})
    folded = LapseLedger("b5", T[5], 9, {7: LapseCause("changed", "b0"), 8: LapseCause("unverifiable", "b2"), 9: LapseCause("unverifiable", "b2")})
    line = describe(folded, previous)
    assert "新失效 2 条" in line and "批次已清理核对不了 2" in line and "累计 3 条" in line and "b5" in line
    assert "新失效 0 条" in describe(previous, previous)


# ---- carried from session to session (the database) -----------------------------------------------------------------


@pytest_asyncio.fixture
async def engine(pick_db_url, tmp_path):
    await pg.migrate(pick_db_url, tmp_path)
    engine = host_engine(pick_db_url)
    yield engine
    await engine.dispose()


async def shared(engine, batch_id, published_at, title=TITLE) -> None:
    """A shared catalog batch holding X under `title`."""
    values = dict(owner_id=SHARED_OWNER, kind=CATALOG_KIND, content_hash=f"hash-{batch_id}", raw_blob_path=f"blobs/{batch_id}.json", status="published")
    await insert(engine, "ggwp_import_batches", id=batch_id, created_at=published_at, published_at=published_at, validation_json={}, **values)
    payload = {"source": "realshort", "language": "en", "title": title, "theater": RS}
    await insert(engine, "ggwp_drama_versions", batch_id=batch_id, identity=X, payload_json=payload)


async def prune(engine, batch_id) -> None:
    """What repository.prune_shared does to a batch: its rows go, its row stays with status pruned."""
    async with engine.begin() as conn:
        await conn.execute(delete(drama_versions).where(drama_versions.c.batch_id == batch_id))
        await conn.execute(update(import_batches).where(import_batches.c.id == batch_id).values(status="pruned", content_hash=f"pruned-{batch_id}"))


async def session_row(engine, target_date, *, plan, outcome="withheld", published_set_id=None, mode="shadow") -> None:
    values = dict(channel="trends", mode=mode, collector_version="test", started_at=T[0], outcome=outcome, status_codes_json=[])
    await insert(engine, "ggwp_obs_batches", id=f"trends-{target_date}", target_date=target_date, plan_json=plan, published_set_id=published_set_id, **values)


async def session(engine, state, target: date, upto: str) -> LapseLedger:
    """TR-20's fold at the start of a session, in one read-only step: the latest earlier ledger, the batches after it."""
    async with engine.connect() as conn:
        step = ReadStep(conn)
        previous = await latest_ledger(step, target)
        sightings = await read_sightings(step, state.correspondences, upto=upto, after=after_of(previous), key_of=key_of)
    return fold(state, previous, sightings)


@pytest.mark.asyncio
async def test_a_withheld_night_does_not_lapse_every_confirmation(engine):
    """The review's case: confirmation 7, then a night whose set was withheld, then more pulls and a manual sync; the
    batch that night read is pruned by the next session. Carried from that night's ledger, the next window holds only
    the newer batches and 7 stands. Carried from the last published set, the window would start with the pruned batch
    and 7 would lapse, as unverifiable, naming it."""
    state = effective((confirm(7),), 7)
    await shared(engine, "b1", T[0])
    first = await session(engine, state, date(2026, 9, 23), "b1")
    await session_row(engine, "2026-09-23", plan=plan_with({LEDGER_NOTE: first.to_dict()}), outcome="published", published_set_id="set-23")
    await shared(engine, "b2", T[1])
    second = await session(engine, state, date(2026, 9, 24), "b2")
    await session_row(engine, "2026-09-24", plan=plan_with({LEDGER_NOTE: second.to_dict()}))  # A-tier coverage < 80%: withheld
    await session_row(engine, "2026-09-25", plan=None, outcome="failed")  # a refusal row keeps no ledger
    await session_row(engine, "2026-09-26", plan=plan_with({}), mode="shadow")  # nor does a canary session's plan
    for batch_id, stamp in (("b3", T[2]), ("b4", T[3]), ("b5", T[4]), ("b6", T[5])):
        await shared(engine, batch_id, stamp)
    await prune(engine, "b2")
    assert (first.lapsed, second.lapsed) == ((), ())

    today = await session(engine, state, date(2026, 9, 27), "b6")
    assert today == LapseLedger("b6", T[5], 7, {})
    assert state.correspondence(X, RS, TITLE, lapsed=today.lapsed) == "confirmed"

    async with engine.connect() as conn:
        from_the_set = fold(state, first, await read_sightings(conn, [X], upto="b6", after=after_of(first), key_of=key_of))
    assert from_the_set.causes == {7: LapseCause("unverifiable", "b2")}
    assert "批次已清理核对不了 1" in describe(from_the_set, first)


@pytest.mark.asyncio
async def test_a_title_changed_and_back_across_three_sessions_stays_lapsed(engine):
    """D24 across sessions: A, then B, then A again (a batch whose content came back is published anew)."""
    state = effective((confirm(7),), 7)
    await shared(engine, "A", T[0])
    first = await session(engine, state, date(2026, 9, 23), "A")
    await session_row(engine, "2026-09-23", plan=plan_with({LEDGER_NOTE: first.to_dict()}), outcome="published")
    await shared(engine, "B", T[1], title="The Alpha Returns")
    second = await session(engine, state, date(2026, 9, 24), "B")
    await session_row(engine, "2026-09-24", plan=plan_with({LEDGER_NOTE: second.to_dict()}), outcome="published")
    async with engine.begin() as conn:
        await conn.execute(update(import_batches).where(import_batches.c.id == "A").values(published_at=T[2]))
    third = await session(engine, state, date(2026, 9, 25), "A")
    assert (first.lapsed, second.lapsed, third.lapsed) == ((), (7,), (7,))
    assert third.causes == {7: LapseCause("changed", "B")}
    assert state.correspondence(X, RS, TITLE, lapsed=third.lapsed) == "unconfirmed"
    # Confirmed anew, it holds from the next session on.
    await session_row(engine, "2026-09-25", plan=plan_with({LEDGER_NOTE: third.to_dict()}), outcome="published")
    again = effective((confirm(7), confirm(9)), 9)
    assert (await session(engine, again, date(2026, 9, 26), "A")).lapsed == ()


@pytest.mark.asyncio
async def test_an_unreadable_ledger_is_never_passed_over(engine):
    """Reading an older ledger instead would bring back what the newer one lapsed: exit 3, the row named."""
    await session_row(engine, "2026-09-23", plan=plan_with({LEDGER_NOTE: ledger().to_dict()}))
    await session_row(engine, "2026-09-24", plan=plan_with({LEDGER_NOTE: {**ledger().to_dict(), "version": "seven"}}))
    async with engine.connect() as conn:
        step = ReadStep(conn)
        with pytest.raises(StateUnavailable, match="trends-2026-09-24") as refused:
            await latest_ledger(step, date(2026, 9, 25))
        assert exit_code_for(refused.value) == ExitCode.STATE_UNAVAILABLE
        assert await latest_ledger(step, date(2026, 9, 24)) == ledger()
        assert await latest_ledger(step, date(2026, 9, 23)) is None


@pytest.mark.asyncio
async def test_the_latest_ledger_is_found_past_many_rows_without_one(engine):
    """A canary run of days before the first stable session: the search reads on page after page."""
    await session_row(engine, "2026-08-01", plan=plan_with({LEDGER_NOTE: ledger().to_dict()}))
    for day in range(2, 22):
        await session_row(engine, f"2026-08-{day:02d}", plan=plan_with({"contract_check": False}))
    await insert(
        engine,
        "ggwp_obs_batches",
        id="gsc-round",
        channel="gsc",
        mode="shadow",
        collector_version="test",
        started_at=T[0],
        outcome="published",
        status_codes_json=[],
    )
    async with engine.connect() as conn:
        assert await latest_ledger(ReadStep(conn), date(2026, 9, 1)) == ledger()
