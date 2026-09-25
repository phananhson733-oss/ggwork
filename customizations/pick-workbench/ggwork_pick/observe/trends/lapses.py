"""D24's lapse ledger: what one Trends session's fold found, kept in its batch row and carried to the next session (G3
review of the D24 fix; plan D24, TR-35, TR-20; progress.md's TR-35 handoff).

A correspondence confirmation lapses for good once a shared catalog batch shows its identity otherwise
(decisions_state.lapse_causes). The shared batches are pruned within days (sync.KEEP_BATCHES newest, plus the
referenced ones), so what they showed is written down when a session reads them, and carried on from there:

- fold(state, previous, sightings) is the session's ledger. `previous` is the latest earlier session's (latest_ledger),
  None when no session has kept one; `sightings` is catalog_history.read_sightings(..., after=after_of(previous)),
  ending with the batch the session judges with. The ledger keeps that batch and its published_at as the database had
  it, so the next fold reads exactly the batches published after it: the gateway's stamps on both sides, never the
  collector's clock, and no margin.
- The carry runs from session to session, published or not. A withheld night (design 4.10's 80% gate), or one refused
  or failed after its batch was created, has read the batches up to its own all the same. Carried only from published
  set to published set, one withheld night left about four shared batches in the next window, the oldest likely
  pruned, and a pruned batch lapses every confirmation.
- Each lapse keeps the cause the fold that found it gave (decisions_state.LapseCause): changed, absent, unverifiable
  or carried. describe() is the line the session's log and summary show, so an operator sees how many lapsed because
  a batch could not be checked, and the ledger names which batch.
- The ledger lives in the session batch's plan_json, under notes[LEDGER_NOTE]: written with the plan when the batch is
  created, read back unchanged on a resume after midnight (counterexample 1: a batch's inputs are fixed once). The set
  the session publishes freezes ledger.lapsed as FrozenInputsTrends.lapsed_confirmations.
"""

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any

from sqlalchemy import select

from ggwork_pick.models import obs_batches
from ggwork_pick.observe.contract import MAX_ROW_ID, STAMP_PATTERN
from ggwork_pick.observe.decisions_state import LAPSE_REASONS, EffectiveDecisions, LapseCause, Sighting, is_lapse_cause, lapse_causes
from ggwork_pick.observe.errors import StateUnavailable
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends import state_codec as codec

LEDGER_FORMAT = "trends-lapse-ledger-v1"
LEDGER_NOTE = "lapses"  # the key of the session plan's notes (plan_json["notes"]) that holds the ledger
CHANNEL = "trends"
_PAGE = 8  # batch rows read at a time while looking for the latest ledger
_STAMP = re.compile(rf"^{STAMP_PATTERN}$")
_KEYS = frozenset({"format", "through", "through_at", "version", "lapsed"})
_CAUSE_KEYS = frozenset({"id", "reason", "batch_id"})
_REASON_WORDS = MappingProxyType(
    {"changed": "平台或标题变了", "absent": "批次里没有这个身份", "unverifiable": "批次已清理核对不了", "carried": "沿用、原因未记"}
)


def _is_row_id(value: Any, *, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= MAX_ROW_ID


def _empty() -> Mapping[int, LapseCause]:
    return MappingProxyType({})


@dataclass(frozen=True)
class LapseLedger:
    """One fold: the shared catalog batch it read through, that batch's published_at as read, the decisions_version it
    ran with, and each lapsed confirmation's cause by decision id (none above the version). Checked when built; the
    causes are kept as a read-only mapping in decision id order."""

    through: str
    through_at: str
    version: int
    causes: Mapping[int, LapseCause] = field(default_factory=_empty)

    __hash__ = None  # compared, never hashed: causes is a read-only proxy, which does not hash

    def __post_init__(self) -> None:
        if not isinstance(self.through, str) or not self.through:
            raise ValueError("through 是共享剧库批次 id")
        if not isinstance(self.through_at, str) or not _STAMP.match(self.through_at):
            raise ValueError("through_at 是 repository.stamp() 形式的时刻")
        if not _is_row_id(self.version, low=0):
            raise ValueError("version 是 decisions_version，0 到 2^63−1 的整数")
        if not isinstance(self.causes, Mapping) or not all(self._holds(key, cause) for key, cause in self.causes.items()):
            raise ValueError("causes 按决定 id（1 到 version）记 LapseCause")
        object.__setattr__(self, "causes", MappingProxyType(dict(sorted(self.causes.items()))))

    def _holds(self, key: Any, cause: Any) -> bool:
        return _is_row_id(key, low=1) and key <= self.version and is_lapse_cause(cause)

    @property
    def lapsed(self) -> tuple[int, ...]:
        """What the session's set freezes as lapsed_confirmations: the decision ids, increasing."""
        return tuple(self.causes)

    def counts(self) -> Mapping[str, int]:
        """How many lapses each reason accounts for, every reason named."""
        return _counts(self.causes)

    def fresh(self, previous: "LapseLedger | None") -> Mapping[int, LapseCause]:
        """The lapses this fold found, not carried from `previous`."""
        known = previous.causes if previous is not None else _empty()
        return MappingProxyType({key: cause for key, cause in self.causes.items() if key not in known})

    def to_dict(self) -> dict[str, Any]:
        lapsed = [{"id": key, "reason": cause.reason, "batch_id": cause.batch_id} for key, cause in self.causes.items()]
        return {"format": LEDGER_FORMAT, "through": self.through, "through_at": self.through_at, "version": self.version, "lapsed": lapsed}

    @classmethod
    def from_dict(cls, data: Any) -> "LapseLedger":
        """The ledger to_dict() wrote; ValueError for anything else."""
        data = codec.exact_keys(data, _KEYS, "lapse ledger")
        if data["format"] != LEDGER_FORMAT or not isinstance(data["lapsed"], list):
            raise ValueError("lapse ledger format is not known")
        entries = tuple(codec.exact_keys(entry, _CAUSE_KEYS, "lapse") for entry in data["lapsed"])
        if len({repr(entry["id"]) for entry in entries}) != len(entries):
            raise ValueError("lapse ledger names a decision twice")
        causes = {entry["id"]: LapseCause(entry["reason"], entry["batch_id"]) for entry in entries}
        return cls(data["through"], data["through_at"], data["version"], causes)


def _counts(causes: Mapping[int, LapseCause]) -> Mapping[str, int]:
    return MappingProxyType({reason: sum(1 for cause in causes.values() if cause.reason == reason) for reason in LAPSE_REASONS})


def after_of(previous: LapseLedger | None) -> str | None:
    """Where the next fold's window starts (catalog_history.read_sightings' `after`): right after the batch `previous`
    read through, as it was stamped then; None when there is no earlier ledger."""
    return None if previous is None else previous.through_at


def fold(state: EffectiveDecisions, previous: LapseLedger | None, sightings: Sequence[Sighting]) -> LapseLedger:
    """The session's ledger: `previous`'s lapses, each with its cause, plus what `sightings` show, for the latest
    confirmations of `state` (decisions_state.lapse_causes). `sightings` end with the batch the session judges with,
    its published_at read (catalog_history.read_sightings); the ledger records that batch as where it read through."""
    seen = tuple(sightings)
    if not seen or not isinstance(seen[-1], Sighting) or seen[-1].published_at is None:
        raise ValueError("sightings 以本会话判定用的共享剧库批次结尾，并带它的 published_at（catalog_history.read_sightings）")
    carried = previous.causes if previous is not None else _empty()
    last = seen[-1]
    return LapseLedger(last.batch_id, last.published_at, state.version, lapse_causes(state, carried, seen))


def describe(ledger: LapseLedger, previous: LapseLedger | None) -> str:
    """The line for the session's log and summary: the new lapses by reason, the total, the batch read through."""
    fresh = ledger.fresh(previous)
    parts = "、".join(f"{_REASON_WORDS[reason]} {count}" for reason, count in _counts(fresh).items() if reason != "carried")
    return f"对应确认新失效 {len(fresh)} 条（{parts}），累计 {len(ledger.causes)} 条，读到共享剧库批次 {ledger.through}"


def ledger_in(plan: Any) -> LapseLedger | None:
    """The ledger a session batch's plan_json keeps under notes[LEDGER_NOTE]; None for no plan or a plan that keeps
    none (a canary session's, a refusal row's). ValueError when the plan, or the ledger it keeps, cannot be read."""
    if plan is None:
        return None
    notes = plan.get("notes") if isinstance(plan, Mapping) else None
    if not isinstance(notes, Mapping):
        raise ValueError("session plan has no notes to keep a lapse ledger in")
    return LapseLedger.from_dict(notes[LEDGER_NOTE]) if LEDGER_NOTE in notes else None


def _plan(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _ledger_of_row(row) -> LapseLedger | None:
    try:
        return ledger_in(_plan(row.plan_json))
    except (ValueError, TypeError, KeyError):
        pass
    # Raised outside the except block: what could not be read stays out of the message.
    raise StateUnavailable(f"批次 {row.id} 的对应确认失效账本读不回来：不拿更早的账本顶替（那样会让这份记下的失效复原），先查这一行（手册 decisions.md）")


async def latest_ledger(step: ReadStep, before: date) -> LapseLedger | None:
    """The ledger of the latest Trends session batch before target date `before` that keeps one, whether its set was
    published or not; None when none does. One that cannot be read is StateUnavailable (exit 3), never passed over for
    an older one: the older one lacks the lapses the newer one found, and they would come back."""
    query = (
        select(obs_batches.c.id, obs_batches.c.plan_json)
        .where(obs_batches.c.channel == CHANNEL, obs_batches.c.target_date < codec.encode_day(before))
        .order_by(obs_batches.c.target_date.desc(), obs_batches.c.id.desc())
    )
    offset = 0
    while True:
        rows = (await step.execute(query.limit(_PAGE).offset(offset))).all()
        found = next((ledger for ledger in map(_ledger_of_row, rows) if ledger is not None), None)
        if found is not None or len(rows) < _PAGE:
            return found
        offset += _PAGE
