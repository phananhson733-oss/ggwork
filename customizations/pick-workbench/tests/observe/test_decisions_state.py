"""TR-35: human decisions -> effective state -> decisions_version -> the set (plan D24, D43; design 4.6, 4.7, 7.1, 7.2).

effective() is the one implementation of the chain: the gateway checks a new decision against it (TR-25), the collectors
apply it at publish time and freeze the version they read (TR-18, TR-20, TR-21). The database half runs on SQLite and
PostgreSQL; its PostgreSQL half skips when PICK_TEST_PG_URL is unset.
"""

import ast
import asyncio
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
from pydantic import ValidationError
from sqlalchemy import insert as sa_insert
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker

from ggwork_pick.models import obs_decisions
from ggwork_pick.observe import contract
from ggwork_pick.observe.contract_api import CorrespondenceRevoke
from ggwork_pick.observe.decisions_state import (
    STEPS,
    WATCH_ADD_CAP,
    AliasMark,
    ConfirmMark,
    CorrespondenceKey,
    DecisionLogError,
    DecisionRecord,
    EffectiveDecisions,
    PairMark,
    Sighting,
    UnknownDecisionKind,
    decision_record,
    effective,
    lapse,
    latest_id,
    lock_for_append,
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
RETITLED = "the alpha's bride returns"
NONE = frozenset()  # no confirmation has lapsed


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


def seen(batch_id, platform=PLATFORM, title=TITLE, identity=X) -> Sighting:
    """A shared batch holding `identity` under this platform and normalized title."""
    return Sighting(batch_id, {identity: CorrespondenceKey(platform, title)})


def missing(batch_id) -> Sighting:
    """A shared batch without the identity asked about."""
    return Sighting(batch_id, {})


def _identity(n: int) -> str:
    return json.dumps(["kalostv", f"kalostv-drama-{n}-en", "en"], separators=(",", ":"))


# ---- correspondence (design 4.7; D24: a confirmation holds for one revision) -------------------------------------


def test_confirm_then_revoke():
    assert effective((), 0).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "unconfirmed"
    assert effective((confirm(1),), 1).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "confirmed"
    assert effective((confirm(1), revoke(2)), 2).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "unconfirmed"
    # Confirmed again after the revocation; revoking another identity touches nothing.
    assert effective((confirm(1), revoke(2), confirm(3)), 3).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "confirmed"
    assert effective((confirm(1), revoke(2, Y)), 2).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "confirmed"
    # Every answer is one of the contract's correspondence values, the ones a judgment row stores.
    assert {"confirmed", "unconfirmed"} == set(contract.ENUMS["CORRESPONDENCES"])


def test_title_change_invalidates_confirmation():
    state = effective((confirm(1),), 1)
    assert state.correspondence(X, PLATFORM, RETITLED, lapsed=NONE) == "unconfirmed"
    assert state.correspondence(X, "DramaBox", TITLE, lapsed=NONE) == "unconfirmed"
    # A new confirmation under the new title holds; the old title no longer does (only the latest confirmation counts).
    again = effective((confirm(1), confirm(2, title=RETITLED)), 2)
    assert again.correspondence(X, PLATFORM, RETITLED, lapsed=NONE) == "confirmed"
    assert again.correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "unconfirmed"
    assert again.confirmed_key(X) == (PLATFORM, RETITLED)
    assert again.correspondences[X] == ConfirmMark(2, CorrespondenceKey(PLATFORM, RETITLED))


def test_alias_change_requires_reconfirm():
    # SLUG was confirmed; a manual pairing (or an auto alias the gsc service writes) replaces it with X.
    decisions = (confirm(1, SLUG, "KalosTV"), pair(2, SLUG, X))
    state = effective(decisions, 2)
    assert state.correspondence(X, "KalosTV", TITLE, lapsed=NONE) == "unconfirmed"
    assert state.confirmed_key(X) is None
    assert state.alias_pairs == {SLUG: PairMark(2, X)}
    # Only a confirmation of the new identity confirms it.
    assert effective((*decisions, confirm(3, X, "KalosTV")), 3).correspondence(X, "KalosTV", TITLE, lapsed=NONE) == "confirmed"


def test_title_revert_does_not_restore_confirmation():
    """G3 P2-2 (the test that stood here pinned the opposite): a confirmation holds for the revision it was made on. Once
    a shared batch since then shows the identity under another title it has lapsed for good; the title changed back
    does not bring it back, whether the collector saw the three batches on three days or in one read. Only a new
    confirmation does."""
    state = effective((confirm(1),), 1)
    first = lapse(state, NONE, [seen("b1")])
    assert first == NONE and state.correspondence(X, PLATFORM, TITLE, lapsed=first) == "confirmed"
    changed = lapse(state, first, [seen("b2", title=RETITLED)])
    assert changed == {1} and state.correspondence(X, PLATFORM, RETITLED, lapsed=changed) == "unconfirmed"
    back = lapse(state, changed, [seen("b3")])
    assert back == {1} and state.correspondence(X, PLATFORM, TITLE, lapsed=back) == "unconfirmed"
    assert lapse(state, NONE, [seen("b1"), seen("b2", title=RETITLED), seen("b3")]) == {1}
    # A new confirmation holds; the lapsed one is no longer any identity's latest, so what a set keeps drops it.
    again = effective((confirm(1), confirm(2)), 2)
    kept = lapse(again, back, [seen("b4")])
    assert kept == NONE and again.correspondence(X, PLATFORM, TITLE, lapsed=kept) == "confirmed"


def test_platform_revert_does_not_restore_confirmation():
    state = effective((confirm(1),), 1)
    moved = lapse(state, NONE, [seen("b1", platform="DramaBox")])
    back = lapse(state, moved, [seen("b2")])
    assert back == {1} and state.correspondence(X, PLATFORM, TITLE, lapsed=back) == "unconfirmed"
    # A batch where the platform is unknown (DramaInput.theater defaults to "") is a change as well.
    assert lapse(state, NONE, [seen("b1", platform=""), seen("b2")]) == {1}
    again = effective((confirm(1), confirm(3)), 3)
    assert again.correspondence(X, PLATFORM, TITLE, lapsed=lapse(again, back, [seen("b3")])) == "confirmed"


def test_alias_swap_and_back_does_not_restore_confirmation():
    """An identity an alias replaced loses its confirmation for good, even when a later alias brings it back: by the
    pairing decision itself, and by the batches, where an identity another one replaced is missing."""
    swapped_back = effective((confirm(1, SLUG, "KalosTV"), pair(2, SLUG, X), pair(3, X, SLUG)), 3)
    assert swapped_back.confirmed_key(SLUG) is None
    assert swapped_back.correspondence(SLUG, "KalosTV", TITLE, lapsed=NONE) == "unconfirmed"
    # Pairing another identity into a confirmed one leaves that one's confirmation alone.
    into = effective((confirm(1, X, "KalosTV"), pair(2, SLUG, X)), 2)
    assert into.correspondence(X, "KalosTV", TITLE, lapsed=NONE) == "confirmed"
    # An alias the gsc service wrote (or an upstream id that changed and changed back) shows only in the batches.
    state = effective((confirm(1, SLUG, "KalosTV"),), 1)
    lapsed = lapse(state, NONE, [seen("b1", "KalosTV", identity=SLUG), missing("b2"), seen("b3", "KalosTV", identity=SLUG)])
    assert lapsed == {1} and state.correspondence(SLUG, "KalosTV", TITLE, lapsed=lapsed) == "unconfirmed"
    again = effective((confirm(1, SLUG, "KalosTV"), pair(2, SLUG, X), pair(3, X, SLUG), confirm(4, SLUG, "KalosTV")), 4)
    assert again.correspondence(SLUG, "KalosTV", TITLE, lapsed=lapse(again, lapsed, [seen("b4", "KalosTV", identity=SLUG)])) == "confirmed"


def test_unreadable_batch_lapses_every_confirmation():
    """A batch whose rows are gone (pruned) could have shown anything: every confirmation it could contradict lapses."""
    state = effective((confirm(1), confirm(2, Y)), 2)
    both = Sighting("b1", {X: CorrespondenceKey(PLATFORM, TITLE), Y: CorrespondenceKey(PLATFORM, TITLE)})
    assert lapse(state, NONE, [both]) == NONE
    assert lapse(state, NONE, [both, Sighting("b2", None)]) == {1, 2}
    # No sighting at all changes nothing; lapses already known stay.
    assert lapse(state, {2}, []) == {2}


def test_lapses_kept_only_for_latest_confirmations():
    """What a set freezes stays small: ids that are no longer any identity's latest confirmation are dropped."""
    state = effective((confirm(1), revoke(2), confirm(3, Y), confirm(4, Y, title=RETITLED)), 4)
    assert lapse(state, {1, 3, 4}, []) == {4}
    assert lapse(effective((), 0), {1}, [Sighting("b1", None)]) == NONE


def test_lapse_refuses_what_is_not_its_input():
    state = effective((confirm(1),), 1)
    for bad in ([0], [True], ["1"], [1.0], [contract.MAX_ROW_ID + 1]):
        with pytest.raises(ValueError, match="lapsed"):
            lapse(state, bad, [])
    for bad in ((X, {}), {"batch_id": "b1", "keys": {}}, Sighting("b1", [X])):
        with pytest.raises(TypeError, match="Sighting"):
            lapse(state, NONE, [bad])


def test_empty_platform_never_confirmed():
    """A row with an empty platform (DramaInput.theater defaults to "") stays unconfirmed: the contract's
    CorrespondenceConfirm.platform takes at least one character (G3 kept it; decisions.md says what the page shows)."""
    with pytest.raises(DecisionLogError, match="correspondence_confirm.platform"):
        confirm(1, platform="")
    assert effective((confirm(1),), 1).correspondence(X, "", TITLE, lapsed=NONE) == "unconfirmed"


# ---- versions (D24: a set freezes the largest id it read) ---------------------------------------------------------


def test_old_set_unchanged_after_revoke():
    decisions = (confirm(1), add(2), revoke(3), add(4, geo="US", active=False), pause(5))
    at_two = effective(decisions, 2)
    assert at_two == effective(decisions[:2], 2)
    assert (at_two.version, at_two.correspondence(X, PLATFORM, TITLE, lapsed=NONE), at_two.watch_added) == (2, "confirmed", frozenset({(X, "US")}))
    assert at_two.is_paused(X, "US") is False
    later = effective(decisions, 5)
    assert (later.correspondence(X, PLATFORM, TITLE, lapsed=NONE), later.watch_added, later.is_paused(X, "US")) == ("unconfirmed", frozenset(), True)
    # Version 2 is still what it was after the later decisions.
    assert effective(decisions, 2) == at_two


def test_old_set_keeps_its_frozen_lapses():
    """A set freezes decisions_version and the lapses it judged with (FrozenInputsTrends.lapsed_confirmations): later
    decisions and later batches change neither what it froze nor the answer they give."""
    decisions = (confirm(1), confirm(2, Y), revoke(3, Y), confirm(4, Y))
    old = effective(decisions, 2)
    frozen = lapse(old, NONE, [Sighting("b1", {X: CorrespondenceKey(PLATFORM, TITLE), Y: CorrespondenceKey(PLATFORM, RETITLED)})])
    assert frozen == {2}
    answers = (old.correspondence(X, PLATFORM, TITLE, lapsed=frozen), old.correspondence(Y, PLATFORM, TITLE, lapsed=frozen))
    assert answers == ("confirmed", "unconfirmed")
    # The next set: X's title changes; Y, its title back to the one confirmed anew as decision 4, holds.
    new = effective(decisions, 4)
    now = lapse(new, frozen, [Sighting("b2", {X: CorrespondenceKey(PLATFORM, RETITLED), Y: CorrespondenceKey(PLATFORM, TITLE)})])
    assert now == {1}
    assert (new.correspondence(X, PLATFORM, RETITLED, lapsed=now), new.correspondence(Y, PLATFORM, TITLE, lapsed=now)) == ("unconfirmed", "confirmed")
    assert effective(decisions, 2) == old and frozen == {2}
    assert (old.correspondence(X, PLATFORM, TITLE, lapsed=frozen), old.correspondence(Y, PLATFORM, TITLE, lapsed=frozen)) == answers


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


def test_watch_add_cap_counts_entries_outside_the_batch():
    """The cap counts every entry in effect, in the current shared batch or not, so the gateway and a collector reading
    another batch agree; entries, pauses and ambiguity verdicts stay on the identity they name (aliases are TR-18's)."""
    in_batch = frozenset(_identity(n) for n in range(1, 50)) | {X}
    decisions = (*(add(n, _identity(n)) for n in range(1, 50)), add(50, SLUG), pair(51, SLUG, X))
    state = effective(decisions, 51)
    # The pairing replaced SLUG with X in the batch; SLUG's entry keeps its slot and does not become X's ...
    assert (SLUG, "US") in state.watch_added and (X, "US") not in state.watch_added
    assert refusal(state, add(52, X).decision) is not None
    # ... until the operator withdraws it: the data board lists the entries outside the batch so that they can.
    assert state.watch_added_outside(in_batch) == frozenset({(SLUG, "US")})
    freed = effective((*decisions, add(52, SLUG, active=False)), 52)
    assert refusal(freed, add(53, X).decision) is None and freed.watch_added_outside(in_batch) == frozenset()
    moved = effective((pause(1, SLUG), _rec(2, "ambiguity_override", identity=SLUG, verdict="clear"), pair(3, SLUG, X)), 3)
    assert (moved.is_paused(SLUG, "US"), moved.is_paused(X, "US")) == (True, False)
    assert (moved.ambiguity_verdict(SLUG), moved.ambiguity_verdict(X)) == ("clear", None)


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
    assert state.alias_verdicts == {5: AliasMark(3, "rejected"), 6: AliasMark(2, "rejected")}
    assert effective(decisions, 1).alias_verdicts == {5: AliasMark(1, "confirmed")}
    # One old identity pairs with the latest new one; the alias refresh rejects the graphs design 7.2 refuses.
    assert state.alias_pairs == {SLUG: PairMark(5, X), _identity(1): PairMark(6, _identity(2))}


def test_alias_marks_keep_the_order_across_kinds():
    """D43's refresh replays alias decisions by id: every mark carries the id of the decision that set it."""
    reject_then_pair = effective((_rec(1, "alias_reject", alias_id=5), pair(2, SLUG, X)), 2)
    pair_then_reject = effective((pair(1, SLUG, X), _rec(2, "alias_reject", alias_id=5)), 2)
    assert reject_then_pair != pair_then_reject
    assert (reject_then_pair.alias_verdicts[5], reject_then_pair.alias_pairs[SLUG]) == (AliasMark(1, "rejected"), PairMark(2, X))
    # A manual pairing, then a confirmed suggestion for the same old identity (SLUG -> Y as queue entry 7): the refresh
    # sees the confirmation is the later one instead of two pairings it can only refuse as many-to-many.
    later = effective((pair(1, SLUG, X), _rec(2, "alias_confirm", alias_id=7)), 2)
    assert later.alias_pairs[SLUG].decision_id < later.alias_verdicts[7].decision_id


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
    assert state.version == len(records) and state.irrelevant_alerts == frozenset({88})
    assert {alias_id: mark.verdict for alias_id, mark in state.alias_verdicts.items()} == {5: "rejected"}


# ---- order, immutability, refusals ---------------------------------------------------------------------------------


def test_order_by_id_deterministic():
    decisions = (confirm(1), add(2), pause(3), revoke(4), confirm(5, title="t2"), add(6, geo="GB"), add(7, active=False), pause(8, geo="US", paused=False))
    expected = effective(decisions, 8)
    for seed in range(20):
        shuffled = random.Random(seed).sample(decisions, len(decisions))
        assert effective(shuffled, 8) == expected
    # The later id wins wherever it sits in the input.
    assert effective((revoke(2), confirm(1)), 2).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "unconfirmed"
    assert effective((confirm(2), revoke(1)), 2).correspondence(X, PLATFORM, TITLE, lapsed=NONE) == "confirmed"
    # A repeated id is a caller's mistake, not a log this code cannot apply: ValueError, not exit 3.
    with pytest.raises(ValueError, match="重复") as repeated:
        effective((confirm(1), revoke(1)), 1)
    assert not isinstance(repeated.value, DecisionLogError)


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
    # The state is compared, never hashed: its mappings are read-only proxies.
    with pytest.raises(TypeError, match="unhashable"):
        hash(state)
    # The input records are left as they were.
    records = (confirm(1), revoke(2))
    before = [record.decision.model_dump() for record in records]
    effective(records, 2)
    assert [record.decision.model_dump() for record in records] == before
    # A record's body cannot be changed in place, whether it was read from the table or built from a caller's model; the
    # record holds its own frozen copy, so changing the caller's model later changes nothing in it.
    with pytest.raises(ValidationError, match="frozen"):
        records[0].decision.identity = Y
    plain = CorrespondenceRevoke(kind="correspondence_revoke", request_id="r-9", identity=X)
    held = DecisionRecord(9, plain)
    with pytest.raises(ValidationError, match="frozen"):
        held.decision.identity = Y
    plain.identity = Y
    assert held.decision.identity == X and isinstance(held.decision, CorrespondenceRevoke)


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
    # Anything but a contract decision is refused before it is applied. Built in code rather than read from the table,
    # that is a programming error (TypeError, ValueError, exit 1), never a DecisionLogError sending operators to the log.
    misuse = []
    with pytest.raises(TypeError) as caught:
        DecisionRecord(1, SimpleNamespace(kind="alias_delete", alias_id=5))
    misuse.append(caught.value)
    for bad_id in (0, True, contract.MAX_ROW_ID + 1):
        with pytest.raises(ValueError, match="id") as caught:
            DecisionRecord(bad_id, confirm(1).decision)
        misuse.append(caught.value)
    with pytest.raises(TypeError, match="DecisionRecord") as caught:
        effective(({"id": 1, "kind": "correspondence_revoke", "identity": X},), 1)
    misuse.append(caught.value)
    assert not any(isinstance(error, DecisionLogError) for error in misuse)
    # The collectors do not run on a log they cannot apply: exit 3, like any unreadable state (D34).
    assert exit_code_for(UnknownDecisionKind(1)) == ExitCode.STATE_UNAVAILABLE


def test_errors_carry_no_values():
    secret = "提取码 x7k2"
    with pytest.raises(DecisionLogError) as caught:
        decision_record(1, "correspondence_confirm", {"kind": "correspondence_confirm", "request_id": secret, "identity": X, "platform": secret})
    assert secret not in str(caught.value) and X not in str(caught.value)
    # A stored key is content too: an unexpected one is named <extra>, never spelled out.
    body = {"kind": "watch_add", "request_id": "r-2", "identity": X, "geo": "US", "active": True, f"pwd {secret}": 1}
    with pytest.raises(DecisionLogError) as extra:
        decision_record(2, "watch_add", body)
    assert secret not in str(extra.value) and "watch_add.<extra>（extra_forbidden）" in str(extra.value)
    # Nor does the error keep the ValidationError, which holds the stored body, as its cause or its context.
    for error in (caught.value, extra.value):
        assert error.__cause__ is None and error.__context__ is None


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
        assert (now.version, now.correspondence(X, PLATFORM, TITLE, lapsed=NONE), now.watch_added) == (3, "unconfirmed", frozenset({(X, "US")}))
        assert (then.version, then.correspondence(X, PLATFORM, TITLE, lapsed=NONE), then.watch_added) == (1, "confirmed", frozenset())
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


@pytest.mark.asyncio
async def test_read_decisions_upto_beyond_the_id_column(db_url):
    """A version is any id up to 2^63-1 (the contract's); the id column is int4 on PostgreSQL, and the read still works."""
    engine = host_engine(db_url)
    try:
        await _write(engine, confirm(1), revoke(2))
        async with engine.connect() as conn:
            for upto in (2**31 - 1, 2**31, contract.MAX_ROW_ID):
                assert [record.id for record in await read_decisions(conn, upto)] == [1, 2]
            assert (await read_effective(conn, contract.MAX_ROW_ID)).version == contract.MAX_ROW_ID
    finally:
        await engine.dispose()


# ---- appending (TR-25's seam): commit order is id order, the cap check sees the state it lands on ------------------


async def _gateway_append(engine, decision, *, session=False, locked=None, appended=None, hold=None) -> int | None:
    """The gateway's write as lock_for_append asks for it: lock first, check against the effective state, append.

    On an AsyncConnection, or with session=True on an AsyncSession (what TR-25's repository hands out).
    """
    opener = async_sessionmaker(engine) if session else engine.connect
    async with opener() as conn, conn.begin():
        await lock_for_append(conn)
        if locked is not None:
            locked.set()
        if refusal(await read_effective(conn), decision) is not None:
            return None
        body = decision.model_dump(mode="json")
        row = dict(kind=body["kind"], request_id=body["request_id"], owner_id="alice", payload_json=body, created_at="2026-09-25T01:52:10.000000+00:00")
        appended_id = (await conn.execute(sa_insert(obs_decisions).values(**row))).inserted_primary_key[0]
        if appended is not None:
            appended.set()
        if hold is not None:
            await hold.wait()
        return appended_id


async def _race(engine, first, second) -> tuple[int | None, int | None]:
    """`first` appends and holds its transaction open; `second` must wait for it before it even reads the state."""
    first_appended, release, second_locked = asyncio.Event(), asyncio.Event(), asyncio.Event()
    held = asyncio.create_task(_gateway_append(engine, first, appended=first_appended, hold=release))
    await asyncio.wait_for(first_appended.wait(), 10)
    waiting = asyncio.create_task(_gateway_append(engine, second, locked=second_locked))
    await asyncio.sleep(0.3)
    blocked = not second_locked.is_set()
    release.set()
    results = (await held, await waiting)
    assert blocked, "the second append ran while the first one's id was drawn but not committed"
    return results


@pytest.mark.asyncio
async def test_append_lock_orders_commits_and_holds_the_cap(db_url):
    engine = host_engine(db_url)
    try:
        # The record ids below only number the request ids (unique per owner); the table draws the row ids.
        for n in range(1, 49):
            assert await _gateway_append(engine, add(n, _identity(n)).decision, session=True) == n
        # A smaller id never commits after a larger one is visible: a set's decisions_version is a faithful cut.
        first_id, second_id = await _race(engine, add(49, _identity(49)).decision, pause(100).decision)
        assert (first_id, second_id) == (49, 50)
        # Two additions racing for the last slot: the second sees the first and is refused, none left for over_cap.
        first_id, second_id = await _race(engine, add(50, _identity(50)).decision, add(51, _identity(51)).decision)
        assert (first_id, second_id) == (51, None)
        async with engine.connect() as conn:
            state = await read_effective(conn)
        assert (state.version, len(state.watch_added), state.over_cap) == (51, WATCH_ADD_CAP, ())
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_lock_for_append_knows_only_the_two_dialects():
    with pytest.raises(ValueError, match="mysql"):
        await lock_for_append(SimpleNamespace(dialect=SimpleNamespace(name="mysql")))
