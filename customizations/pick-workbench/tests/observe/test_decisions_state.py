"""TR-35: human decisions -> effective state -> decisions_version -> the set (plan D24, D43; design 4.6, 4.7, 7.1, 7.2).

effective() is the one implementation of the chain: the gateway checks a new decision against it (TR-25), the collectors
apply it at publish time and freeze the version they read (TR-18, TR-20, TR-21). The database half runs on SQLite and
PostgreSQL; its PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import ast
import dataclasses
import json
import random
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pg
import pytest
import pytest_asyncio
from engines import host_engine
from obs_schema import insert
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from ggwork_pick.observe import contract
from ggwork_pick.observe.decisions_state import (
    STEPS,
    WATCH_ADD_CAP,
    DecisionLogError,
    DecisionRecord,
    EffectiveDecisions,
    UnknownDecisionKind,
    decision_record,
    effective,
    latest_id,
    read_decisions,
    read_effective,
    refusal,
)
from ggwork_pick.observe.errors import ExitCode, exit_code_for

MODULE = Path(__file__).resolve().parents[2] / "ggwork_pick" / "observe" / "decisions_state.py"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "obs_contract"
X = json.dumps(["realshort", "UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ", "en"], separators=(",", ":"))
Y = json.dumps(["realshort", "UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZg", "en"], separators=(",", ":"))
SLUG = json.dumps(["kalostv", "kalostv-the-alpha-s-bride-en", "en"], separators=(",", ":"))
PLATFORM, TITLE = "ReelShort", "the alpha's bride"


def _rec(row_id: int, kind: str, **fields) -> DecisionRecord:
    return decision_record(row_id, kind, {"kind": kind, "request_id": f"r-{row_id}", "note": "", **fields})


def confirm(row_id, identity=X, platform=PLATFORM, title=TITLE):
    return _rec(row_id, "correspondence_confirm", identity=identity, platform=platform, normalized_title=title)


def revoke(row_id, identity=X):
    return _rec(row_id, "correspondence_revoke", identity=identity)


def add(row_id, identity=X, geo="US", active=True):
    return _rec(row_id, "watch_add", identity=identity, geo=geo, active=active)


def pause(row_id, identity=X, geo=None, paused=True):
    return _rec(row_id, "watch_pause", identity=identity, geo=geo, paused=paused)


def pair(row_id, old=SLUG, new=X):
    return _rec(row_id, "alias_pair", old_identity=old, new_identity=new)


def _identity(n: int) -> str:
    return json.dumps(["kalostv", f"kalostv-drama-{n}-en", "en"], separators=(",", ":"))


# ---- correspondence (design 4.7) ----------------------------------------------------------------------------------


def test_confirm_then_revoke():
    assert effective((), 0).correspondence(X, PLATFORM, TITLE) == "unconfirmed"
    assert effective((confirm(1),), 1).correspondence(X, PLATFORM, TITLE) == "confirmed"
    assert effective((confirm(1), revoke(2)), 2).correspondence(X, PLATFORM, TITLE) == "unconfirmed"
    # Confirmed again after the revocation; revoking another identity touches nothing.
    assert effective((confirm(1), revoke(2), confirm(3)), 3).correspondence(X, PLATFORM, TITLE) == "confirmed"
    assert effective((confirm(1), revoke(2, Y)), 2).correspondence(X, PLATFORM, TITLE) == "confirmed"
    # Every answer is one of the contract's correspondence values, the ones a judgment row stores.
    assert {"confirmed", "unconfirmed"} == set(contract.ENUMS["CORRESPONDENCES"])


def test_title_change_invalidates_confirmation():
    state = effective((confirm(1),), 1)
    assert state.correspondence(X, PLATFORM, "the alpha's bride returns") == "unconfirmed"
    assert state.correspondence(X, "DramaBox", TITLE) == "unconfirmed"
    # A new confirmation under the new title holds; the old title no longer does (only the latest confirmation counts).
    again = effective((confirm(1), confirm(2, title="the alpha's bride returns")), 2)
    assert again.correspondence(X, PLATFORM, "the alpha's bride returns") == "confirmed"
    assert again.correspondence(X, PLATFORM, TITLE) == "unconfirmed"
    assert again.confirmed_key(X) == (PLATFORM, "the alpha's bride returns")


def test_alias_change_requires_reconfirm():
    # SLUG was confirmed; a manual pairing (or an auto alias the gsc service writes) replaces it with X.
    decisions = (confirm(1, SLUG, "KalosTV"), pair(2, SLUG, X))
    state = effective(decisions, 2)
    assert state.correspondence(X, "KalosTV", TITLE) == "unconfirmed"
    assert state.confirmed_key(X) is None
    assert state.alias_pairs == {SLUG: X}
    # Only a confirmation of the new identity confirms it.
    assert effective((*decisions, confirm(3, X, "KalosTV")), 3).correspondence(X, "KalosTV", TITLE) == "confirmed"


# ---- versions (D24: a set freezes the largest id it read) ---------------------------------------------------------


def test_old_set_unchanged_after_revoke():
    decisions = (confirm(1), add(2), revoke(3), add(4, geo="US", active=False), pause(5))
    at_two = effective(decisions, 2)
    assert at_two == effective(decisions[:2], 2)
    assert (at_two.version, at_two.correspondence(X, PLATFORM, TITLE), at_two.watch_added) == (2, "confirmed", frozenset({(X, "US")}))
    assert at_two.is_paused(X, "US") is False
    later = effective(decisions, 5)
    assert (later.correspondence(X, PLATFORM, TITLE), later.watch_added, later.is_paused(X, "US")) == ("unconfirmed", frozenset(), True)
    # Version 2 is still what it was after the later decisions.
    assert effective(decisions, 2) == at_two


def test_upto_id_must_be_a_version():
    for bad in (-1, True, 1.0, "2", None, contract.MAX_ROW_ID + 1):
        with pytest.raises(ValueError, match="upto_id"):
            effective((), bad)
    assert effective((), contract.MAX_ROW_ID).version == contract.MAX_ROW_ID


# ---- manual additions and pauses (design 4.6: at most 50) ---------------------------------------------------------


def test_watch_add_cap_50_effective():
    assert WATCH_ADD_CAP == 50
    adds = tuple(add(n, _identity(n)) for n in range(1, 52))
    state = effective(adds, 51)
    assert len(state.watch_added) == 50 and (_identity(51), "US") not in state.watch_added
    assert state.over_cap == (51,)
    assert refusal(effective(adds[:50], 50), adds[50].decision) is not None
    # Re-adding an entry already in, withdrawing, pausing and every other kind are never refused at the cap.
    full = effective(adds[:50], 50)
    for decision in (add(99, _identity(1)).decision, add(99, _identity(77), active=False).decision, pause(99).decision, confirm(99).decision):
        assert refusal(full, decision) is None
    assert refusal(effective(adds[:49], 49), adds[50].decision) is None
    # A withdrawal frees a slot for a later addition; the one dropped at the cap stays dropped.
    freed = effective((*adds, add(52, _identity(3), active=False), add(53, _identity(60))), 53)
    assert len(freed.watch_added) == 50
    assert (_identity(60), "US") in freed.watch_added and (_identity(51), "US") not in freed.watch_added
    assert (_identity(3), "US") not in freed.watch_added and freed.over_cap == (51,)
    # An entry is (identity, geo): the same drama in a second geo is a second entry.
    assert effective((add(1), add(2, geo="GB"), add(3)), 3).watch_added == frozenset({(X, "US"), (X, "GB")})


def test_pause_latest_wins_between_one_geo_and_all():
    assert effective((), 0).is_paused(X, "US") is False
    everywhere = effective((pause(1),), 1)
    assert everywhere.is_paused(X, "US") and everywhere.is_paused(X, "WW") and not everywhere.is_paused(Y, "US")
    one_back = effective((pause(1), pause(2, geo="US", paused=False)), 2)
    assert (one_back.is_paused(X, "US"), one_back.is_paused(X, "GB")) == (False, True)
    all_again = effective((pause(1), pause(2, geo="US", paused=False), pause(3)), 3)
    assert all_again.is_paused(X, "US") is True
    resumed = effective((pause(1, geo="US"), pause(2, paused=False)), 2)
    assert resumed.is_paused(X, "US") is False


# ---- aliases, ambiguity, alerts (D43, design 4.7, 6.2) --------------------------------------------------------------


def test_alias_decisions_latest_wins():
    decisions = (
        _rec(1, "alias_confirm", alias_id=5),
        _rec(2, "alias_reject", alias_id=6),
        _rec(3, "alias_reject", alias_id=5),
        pair(4, SLUG, Y),
        pair(5, SLUG, X),
        pair(6, _identity(1), _identity(2)),
    )
    state = effective(decisions, 6)
    assert state.alias_verdicts == {5: "rejected", 6: "rejected"}
    assert effective(decisions, 1).alias_verdicts == {5: "confirmed"}
    # One old identity pairs with the latest new one; the alias refresh rejects the graphs design 7.2 refuses.
    assert state.alias_pairs == {SLUG: X, _identity(1): _identity(2)}


def test_ambiguity_and_irrelevant_alerts():
    decisions = (
        _rec(1, "ambiguity_override", identity=X, verdict="clear"),
        _rec(2, "ambiguity_override", identity=Y, verdict="clear"),
        _rec(3, "ambiguity_override", identity=X, verdict="ambiguous"),
        _rec(4, "alert_irrelevant", alert_id=88),
        _rec(5, "alert_irrelevant", alert_id=88),
        _rec(6, "alert_irrelevant", alert_id=7),
    )
    state = effective(decisions, 6)
    assert (state.ambiguity_verdict(X), state.ambiguity_verdict(Y), state.ambiguity_verdict(SLUG)) == ("ambiguous", "clear", None)
    assert state.irrelevant_alerts == frozenset({7, 88})


def test_every_contract_kind_has_a_step():
    assert tuple(STEPS) == contract.DECISION_KINDS


def test_contract_fixture_decisions_all_apply():
    """Every valid body of TR-33's decisions.json is a decision the chain applies."""
    bodies = [case["value"] for case in json.loads((FIXTURES / "decisions.json").read_text(encoding="utf-8"))["valid"]]
    records = tuple(decision_record(n, body["kind"], body) for n, body in enumerate(bodies, start=1))
    assert {record.decision.kind for record in records} == set(contract.DECISION_KINDS)
    state = effective(records, len(records))
    assert state.version == len(records) and state.alias_verdicts == {5: "rejected"} and state.irrelevant_alerts == frozenset({88})


# ---- order, immutability, refusals ---------------------------------------------------------------------------------


def test_order_by_id_deterministic():
    decisions = (confirm(1), add(2), pause(3), revoke(4), confirm(5, title="t2"), add(6, geo="GB"), add(7, active=False), pause(8, geo="US", paused=False))
    expected = effective(decisions, 8)
    for seed in range(20):
        shuffled = random.Random(seed).sample(decisions, len(decisions))
        assert effective(shuffled, 8) == expected
    # The later id wins wherever it sits in the input.
    assert effective((revoke(2), confirm(1)), 2).correspondence(X, PLATFORM, TITLE) == "unconfirmed"
    assert effective((confirm(2), revoke(1)), 2).correspondence(X, PLATFORM, TITLE) == "confirmed"
    with pytest.raises(DecisionLogError, match="重复"):
        effective((confirm(1), revoke(1)), 1)


def test_effective_is_immutable():
    state = effective(
        (confirm(1), add(2), pause(3), pair(4), _rec(5, "alias_confirm", alias_id=9), _rec(6, "ambiguity_override", identity=X, verdict="clear")), 6
    )
    assert isinstance(state, EffectiveDecisions)
    with pytest.raises(dataclasses.FrozenInstanceError):
        state.version = 7
    for mapping in (state.alias_verdicts, state.alias_pairs, state.correspondences, state.pauses, state.ambiguity):
        with pytest.raises(TypeError):
            mapping["x"] = "y"
    assert isinstance(state.watch_added, frozenset) and isinstance(state.irrelevant_alerts, frozenset) and isinstance(state.over_cap, tuple)
    # The input records are left as they were.
    records = (confirm(1), revoke(2))
    before = [record.decision.model_dump() for record in records]
    effective(records, 2)
    assert [record.decision.model_dump() for record in records] == before


def test_unknown_kind_rejected():
    body = {"kind": "alias_delete", "request_id": "r-1", "note": "", "alias_id": 5}
    with pytest.raises(UnknownDecisionKind, match="1"):
        decision_record(1, "alias_delete", body)
    # The column and the body disagree, or the body is not the contract's.
    with pytest.raises(DecisionLogError, match="kind"):
        decision_record(2, "alias_confirm", body | {"kind": "alias_reject"})
    with pytest.raises(DecisionLogError, match="alias_confirm.alias_id"):
        decision_record(3, "alias_confirm", body | {"kind": "alias_confirm", "alias_id": "5"})
    with pytest.raises(DecisionLogError):
        decision_record(4, "alias_confirm", ["alias_confirm"])
    for bad_id in (0, True, "5", contract.MAX_ROW_ID + 1):
        with pytest.raises(DecisionLogError):
            decision_record(bad_id, "alias_confirm", body | {"kind": "alias_confirm"})
    # Anything but a contract decision is refused before it is applied.
    with pytest.raises(UnknownDecisionKind):
        DecisionRecord(1, SimpleNamespace(kind="alias_delete", alias_id=5))
    with pytest.raises(DecisionLogError, match="id"):
        DecisionRecord(0, confirm(1).decision)
    with pytest.raises(DecisionLogError, match="DecisionRecord"):
        effective(({"id": 1, "kind": "correspondence_revoke", "identity": X},), 1)
    # The collectors do not run on a log they cannot apply: exit 3, like any unreadable state (D34).
    assert exit_code_for(UnknownDecisionKind(1)) == ExitCode.STATE_UNAVAILABLE


def test_errors_carry_no_values():
    secret = "提取码 x7k2"
    with pytest.raises(DecisionLogError) as caught:
        decision_record(1, "correspondence_confirm", {"kind": "correspondence_confirm", "request_id": secret, "identity": X, "platform": secret})
    assert secret not in str(caught.value) and X not in str(caught.value)


def test_latest_id():
    assert latest_id(()) == 0
    assert latest_id((confirm(3), revoke(9), add(4))) == 9


def test_pure_part_reads_no_clock():
    """Time never enters the chain: a decision applies by its id, never by when it was written or read."""
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    calls = {node.func.attr for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"now", "utcnow", "today", "time"}
    writes = {"insert", "update", "delete", "add", "commit", "flush"}
    assert not calls & writes


# ---- reading the log (both dialects) --------------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_url(pick_db_url, tmp_path):
    await pg.migrate(pick_db_url, tmp_path)
    return pick_db_url


async def _write(engine, *records: DecisionRecord, owner: str = "alice") -> None:
    for record in records:
        body = record.decision.model_dump(mode="json")
        await insert(engine, "ggwp_obs_decisions", id=record.id, kind=body["kind"], request_id=body["request_id"], owner_id=owner, payload_json=body)


@asynccontextmanager
async def _read_only(engine):
    """A connection in a read-only transaction; SQLite's switch is per connection, so it is turned back off for the pool."""
    async with engine.connect() as conn:
        sqlite = conn.dialect.name == "sqlite"
        await conn.execute(text("PRAGMA query_only = ON" if sqlite else "SET LOCAL transaction_read_only = on"))
        try:
            yield conn
        finally:
            if sqlite:
                await conn.execute(text("PRAGMA query_only = OFF"))


@pytest.mark.asyncio
async def test_read_decisions_reads_upto_in_id_order_read_only(db_url):
    engine = host_engine(db_url)
    try:
        written = (confirm(1), add(2), revoke(3), pause(4))
        await _write(engine, written[2], written[0], written[3], written[1])
        async with _read_only(engine) as conn:
            everything = await read_decisions(conn, None)
            upto = await read_decisions(conn, 2)
            # The read-only transaction really refuses a write, so the reads above wrote nothing.
            with pytest.raises((OperationalError, DBAPIError)):
                await conn.execute(text("DELETE FROM ggwp_obs_decisions"))
        assert [record.id for record in everything] == [1, 2, 3, 4]
        assert [record.decision.model_dump() for record in everything] == [record.decision.model_dump() for record in written]
        assert [record.id for record in upto] == [1, 2]
        # A later decision does not change what an older version reads.
        await _write(engine, revoke(5, Y))
        async with engine.connect() as conn:
            assert [record.id for record in await read_decisions(conn, 2)] == [1, 2]
            assert effective(await read_decisions(conn, 2), 2) == effective(written[:2], 2)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_effective_freezes_the_latest_id(db_url):
    engine = host_engine(db_url)
    try:
        async with engine.connect() as conn:
            empty = await read_effective(conn)
        assert (empty.version, empty.correspondences) == (0, {})
        await _write(engine, confirm(1), add(2))
        await _write(engine, revoke(3), owner="bob")
        async with engine.connect() as conn:
            now = await read_effective(conn)
            then = await read_effective(conn, 1)
        assert (now.version, now.correspondence(X, PLATFORM, TITLE), now.watch_added) == (3, "unconfirmed", frozenset({(X, "US")}))
        assert (then.version, then.correspondence(X, PLATFORM, TITLE), then.watch_added) == (1, "confirmed", frozenset())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_read_decisions_refuses_rows_it_cannot_apply(db_url):
    engine = host_engine(db_url)
    try:
        await _write(engine, confirm(1))
        await insert(engine, "ggwp_obs_decisions", id=2, kind="alias_delete", request_id="r-2", owner_id="alice", payload_json={"kind": "alias_delete"})
        async with engine.connect() as conn:
            assert [record.id for record in await read_decisions(conn, 1)] == [1]
            with pytest.raises(UnknownDecisionKind):
                await read_decisions(conn, None)
            with pytest.raises(UnknownDecisionKind):
                await read_effective(conn)
    finally:
        await engine.dispose()
