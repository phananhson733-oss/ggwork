"""D24's lapse input (G3 P2-2): the shared catalog batches since the previous Trends set, as catalog_history reads them.

The read runs on SQLite and PostgreSQL; its PostgreSQL half skips when PICK_TEST_PG_URL is unset. The last test carries
a confirmation through the batches from the database, including a batch whose content came back (repository._reuse).
"""

import json
from contextlib import asynccontextmanager

import pg
import pytest
import pytest_asyncio
from engines import host_engine
from obs_schema import insert
from sqlalchemy import text, update
from sqlalchemy.exc import DBAPIError, OperationalError

from ggwork_pick.models import import_batches
from ggwork_pick.observe.catalog_history import CATALOG_KIND, SHARED_OWNER, CatalogHistoryError, read_sightings
from ggwork_pick.observe.decisions_state import CorrespondenceKey, Sighting, decision_record, effective, lapse
from ggwork_pick.observe.errors import ExitCode, exit_code_for
from ggwork_pick.repository import SHARED_OWNER as REPOSITORY_SHARED_OWNER

X = json.dumps(["realshort", "x-650a1b2c", "en"], separators=(",", ":"))
Y = json.dumps(["realshort", "y-650a1b2d", "en"], separators=(",", ":"))
RS = "ReelShort"
# Two pulls a day (schedule.SLOTS_UTC), as repository.stamp() writes them.
T = (
    "2026-09-23T03:40:00.000000+00:00",
    "2026-09-23T15:40:00.000000+00:00",
    "2026-09-24T03:40:00.000000+00:00",
    "2026-09-24T15:40:00.000000+00:00",
    "2026-09-25T03:40:00.000000+00:00",
)
AFTER_A = "2026-09-23T10:00:00.000000+00:00"  # the previous set was read between the first pull and the second


def key_of(payload) -> CorrespondenceKey | None:
    """A stand-in for TR-18's: the platform as stored, the title folded; no title, no key."""
    title = payload.get("title")
    return CorrespondenceKey(payload.get("theater", ""), title.casefold()) if isinstance(title, str) else None


def as_plain(sightings) -> list:
    return [(sighting.batch_id, None if sighting.keys is None else dict(sighting.keys)) for sighting in sightings]


@pytest_asyncio.fixture
async def engine(pick_db_url, tmp_path):
    await pg.migrate(pick_db_url, tmp_path)
    engine = host_engine(pick_db_url)
    yield engine
    await engine.dispose()


async def batch(engine, batch_id, published_at, *dramas, status="published", owner=SHARED_OWNER, kind=CATALOG_KIND) -> None:
    """A batch and its dramas, each (identity, theater, title); a pruned batch keeps its row and loses its dramas."""
    values = dict(owner_id=owner, kind=kind, content_hash=f"hash-{batch_id}", raw_blob_path=f"blobs/{batch_id}.json", status=status)
    await insert(engine, "ggwp_import_batches", id=batch_id, created_at=published_at, published_at=published_at, validation_json={}, **values)
    for identity, theater, title in dramas:
        payload = {"source": "realshort", "language": "en", "title": title, "theater": theater}
        await insert(engine, "ggwp_drama_versions", batch_id=batch_id, identity=identity, payload_json=payload)


@asynccontextmanager
async def read_only(engine):
    """A connection in a read-only transaction; SQLite's switch is per connection, so it is turned back off for the pool."""
    async with engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        await conn.execute(text("PRAGMA query_only = ON" if sqlite else "SET LOCAL transaction_read_only = on"))
        try:
            yield conn
        finally:
            if sqlite:
                await conn.execute(text("PRAGMA query_only = OFF"))


def test_shared_owner_is_the_repository_s():
    assert SHARED_OWNER == REPOSITORY_SHARED_OWNER


@pytest.mark.asyncio
async def test_reads_the_batches_since_after_through_upto(engine):
    await batch(engine, "b1", T[0], (X, RS, "The Alpha"))
    await batch(engine, "b2", T[1], (X, RS, "The Alpha Returns"), (Y, RS, "Other"))
    await batch(engine, "b3", T[2], status="pruned")
    await batch(engine, "b4", T[3], (Y, "", "Other"))
    await batch(engine, "b5", T[4], (X, RS, "The Alpha"))
    # Neither a user's own catalog nor the shared knowledge batch is the shared catalog.
    await batch(engine, "mine", T[2], (X, RS, "Mine"), owner="alice")
    await batch(engine, "know", T[2], (X, RS, "Know"), kind="knowledge")
    async with read_only(engine) as conn:
        found = await read_sightings(conn, [X, Y, X], upto="b4", after=T[0], key_of=key_of)
        with pytest.raises((OperationalError, DBAPIError)):
            await conn.execute(text("DELETE FROM ggwp_import_batches"))
    assert all(isinstance(sighting, Sighting) for sighting in found)
    # b1 was read before `after`, b5 came after the batch the session uses; b3's rows are gone; b4 lacks X.
    assert as_plain(found) == [
        ("b2", {X: (RS, "the alpha returns"), Y: (RS, "other")}),
        ("b3", None),
        ("b4", {Y: ("", "other")}),
    ]
    with pytest.raises(TypeError):
        found[0].keys[X] = (RS, "x")


@pytest.mark.asyncio
async def test_without_a_previous_set_only_the_current_batch_is_read(engine):
    await batch(engine, "b1", T[0], (X, RS, "Earlier"))
    await batch(engine, "b2", T[1], (X, RS, "The Alpha"))
    async with engine.connect() as conn:
        assert as_plain(await read_sightings(conn, [X], upto="b2", after=None, key_of=key_of)) == [("b2", {X: (RS, "the alpha")})]
        # A current batch that was already current when the previous set was read is read all the same.
        assert as_plain(await read_sightings(conn, [X], upto="b2", after=T[3], key_of=key_of)) == [("b2", {X: (RS, "the alpha")})]


@pytest.mark.asyncio
async def test_what_it_asks_about_and_what_it_cannot_key(engine):
    await batch(engine, "b1", T[0], (X, RS, "The Alpha"), (Y, RS, "Other"))
    async with engine.connect() as conn:
        # Nothing asked, nothing read (not even the batch).
        assert await read_sightings(conn, [], upto="nowhere", after=None, key_of=key_of) == ()
        assert as_plain(await read_sightings(conn, [Y], upto="b1", after=None, key_of=key_of)) == [("b1", {Y: (RS, "other")})]
        # A drama TR-18's function gives no key for counts as absent, like one the batch lacks.
        untitled = await read_sightings(conn, [X], upto="b1", after=None, key_of=lambda payload: None)
        assert as_plain(untitled) == [("b1", {})]
        with pytest.raises(TypeError, match="key_of"):
            await read_sightings(conn, [X], upto="b1", after=None, key_of=lambda payload: ("ReelShort", "the alpha"))
        with pytest.raises(TypeError, match="identities"):
            await read_sightings(conn, [X, 7], upto="b1", after=None, key_of=key_of)


@pytest.mark.asyncio
async def test_a_stored_payload_as_text(engine):
    """A body stored as JSON text (encoded twice) is read like any other; text that is no JSON gives no key."""
    await batch(engine, "b1", T[0])
    twice = json.dumps({"title": "The Alpha", "theater": RS})
    await insert(engine, "ggwp_drama_versions", batch_id="b1", identity=X, payload_json=twice)
    await insert(engine, "ggwp_drama_versions", batch_id="b1", identity=Y, payload_json="not json")
    async with engine.connect() as conn:
        assert as_plain(await read_sightings(conn, [X, Y], upto="b1", after=None, key_of=key_of)) == [("b1", {X: (RS, "the alpha")})]


@pytest.mark.asyncio
async def test_refuses_a_batch_that_is_not_the_current_shared_catalog(engine):
    await batch(engine, "mine", T[0], owner="alice")
    await batch(engine, "know", T[0], kind="knowledge")
    await batch(engine, "staged", T[0], status="importing")
    await batch(engine, "gone", T[0], status="pruned")
    async with engine.connect() as conn:
        for upto in ("unknown", "mine", "know", "staged", "gone"):
            with pytest.raises(CatalogHistoryError, match=upto) as refused:
                await read_sightings(conn, [X], upto=upto, after=None, key_of=key_of)
            assert exit_code_for(refused.value) == ExitCode.STATE_UNAVAILABLE
        for after in ("2026-09-23", "2026-09-23T03:40:00+00:00", 1, b"x"):
            with pytest.raises(ValueError, match="after"):
                await read_sightings(conn, [X], upto="mine", after=after, key_of=key_of)


@pytest.mark.asyncio
async def test_a_title_back_after_a_reused_batch_stays_lapsed(engine):
    """A (the confirmed title), B (another), then A's content again: the repository publishes batch A again with a new
    published_at, so by batch id the window would be empty. Read from the moment the previous set was read, B is in
    it and the confirmation lapses; the next set, reading only A, does not bring it back."""
    await batch(engine, "A", T[0], (X, RS, "The Alpha"))
    await batch(engine, "B", T[1], (X, RS, "The Alpha Returns"))
    body = {"kind": "correspondence_confirm", "request_id": "r-1", "identity": X, "platform": RS, "normalized_title": "the alpha"}
    confirmed = decision_record(1, "correspondence_confirm", body)
    state = effective((confirmed,), 1)
    async with engine.begin() as conn:
        await conn.execute(update(import_batches).where(import_batches.c.id == "A").values(published_at=T[2]))
    async with engine.connect() as conn:
        first = await read_sightings(conn, [X], upto="A", after=AFTER_A, key_of=key_of)
        later = await read_sightings(conn, [X], upto="A", after=T[3], key_of=key_of)
    assert [sighting.batch_id for sighting in first] == ["B", "A"]
    lapsed = lapse(state, (), first)
    assert lapsed == {1} and state.correspondence(X, RS, "the alpha", lapsed=lapsed) == "unconfirmed"
    assert lapse(state, lapsed, later) == {1}
    # Read once before the change, the same confirmation held.
    assert lapse(state, (), [Sighting("A", {X: CorrespondenceKey(RS, "the alpha")})]) == frozenset()
