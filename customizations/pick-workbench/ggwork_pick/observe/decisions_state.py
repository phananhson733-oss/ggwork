"""Human decisions -> effective state: the one implementation of D24's chain (plan TR-35; D12, D24, D43; design 4.6, 4.7,
7.1, 7.2; contract section 12).

ggwp_obs_decisions is append-only: TR-25 writes each POST /api/pick/obs/decisions body into payload_json, its kind into
the kind column. A collector reads the log once when it publishes, up to the largest id it saw, and freezes that id into
the set as decisions_version; effective(decisions, version) is then what the set was judged with, whatever is decided
later, so an old set never changes (design 7.1: the agent reads judgment rows, never the live log). The gateway checks a
new decision against the same state before appending it (refusal), under lock_for_append.

- Decisions apply in id order, a later one overriding an earlier one on the same key; ids above upto_id are ignored.
- Correspondence (design 4.7, D24): a confirmation holds for one revision of one identity's correspondence: its
  (platform, normalized title), the latest confirmation per identity. It lapses for good once the identity is seen
  otherwise after it was made: another platform or title in a shared catalog batch, the identity missing from one (an
  alias or a changed upstream id replaced it), a batch whose rows are gone and cannot say, or an alias_pair decision
  naming it as the old identity. Changing back never restores it; only a new confirmation, a new decision id, does
  (G3 P2-2). A revocation clears the identity. The decisions alone cannot know what the batches showed, and the batches
  are pruned within days, so the lapses are a fold the Trends collector carries from set to set: lapse() adds what
  the batches read since the previous set show (catalog_history.read_sightings), the set freezes the result next to
  decisions_version (FrozenInputsTrends.lapsed_confirmations), and correspondence() takes it. A confirmation never
  follows an alias to the new identity. A row with an empty platform is never confirmed, since the contract's
  CorrespondenceConfirm.platform takes at least one character.
- Manual additions (design 4.6): an entry is (identity, geo). At most WATCH_ADD_CAP are in effect, counted whether or
  not the identity is in the current shared batch, so the gateway and a collector reading another batch agree;
  watch_added_outside() names the entries outside a batch, which keep their slot until withdrawn. Re-adding an entry in
  effect changes nothing; active=false withdraws it. An addition beyond the cap (only a writer bypassing
  lock_for_append can land one) is dropped for good and named in over_cap, exactly what the gateway would have refused.
- Pauses: per (identity, geo), geo None meaning every geo; for one geo the later of its own and the all-geos mark wins.
- Ambiguity overrides are per identity, the latest verdict, and do not lapse when the title changes; an alert marked
  irrelevant stays so.
- Additions, pauses and ambiguity verdicts stay on the identity they name. Whether they follow an alias is TR-18's to
  decide with the set's frozen alias version; the gateway's count of raw entries never undercounts a mapped one.
- Alias decisions are handed on, not applied here (D43): the gsc service's alias refresh reads alias_verdicts (by the
  alias queue's id) and alias_pairs (one new identity per old one, the latest pairing), each mark carrying the id of the
  decision that set it, so the refresh can replay them in order across kinds; cycles and the other graphs design 7.2
  refuses are the refresh's to refuse.
- Time never enters: a decision applies by its id, never by when it was written or read, and a lapse is named by the
  confirmation's decision id.

decisions_version is a faithful cut only if no smaller id commits after a larger one is visible, and PostgreSQL draws an
id when a row is inserted, not when it commits. Every append therefore takes lock_for_append first: commit order is then
id order and the cap check is atomic, so version k and D43's (previous version, k] both see every decision up to k.

A log this code cannot apply (an unknown kind, a body that is not the contract's) is never applied in part:
DecisionLogError is a StateUnavailable, and a collector exits 3 instead of publishing a set on a guessed state. A
DecisionRecord or an effective() input built wrongly in code is a TypeError or a ValueError instead (exit 1).
"""

from collections.abc import Callable, Container, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import reduce
from types import MappingProxyType
from typing import Annotated, Any, Literal, NamedTuple, Union

from pydantic import ConfigDict, Field, TypeAdapter, ValidationError
from sqlalchemy import BigInteger, literal, select, text

from ggwork_pick.models import obs_decisions
from ggwork_pick.observe.contract import DECISION_KINDS, MAX_ROW_ID, AmbiguityVerdict, Correspondence
from ggwork_pick.observe.contract_api import DECISION_MODELS, Decision, WatchAdd
from ggwork_pick.observe.errors import StateUnavailable

WATCH_ADD_CAP = 50  # design 4.6 rule 5
AliasVerdict = Literal["confirmed", "rejected"]
_EMPTY: Mapping = MappingProxyType({})
_MAX_REPORTED_PROBLEMS = 3


def _frozen(model: type) -> type:
    """The contract model with frozen=True, same name and fields: a record's body cannot be changed in place."""
    return type(model.__name__, (model,), {"__module__": __name__, "__qualname__": model.__name__, "model_config": ConfigDict(frozen=True)})


_FROZEN_MODELS = tuple(_frozen(model) for model in DECISION_MODELS)
_DECISION = TypeAdapter(Annotated[Union[_FROZEN_MODELS], Field(discriminator="kind")])  # noqa: UP007


class DecisionLogError(StateUnavailable):
    """ggwp_obs_decisions holds what this code cannot apply. The message names the row, never its content."""


class UnknownDecisionKind(DecisionLogError):
    """A kind outside the contract's nine: written by a newer gateway, so the collectors go first when one is added."""

    def __init__(self, row_id: int):
        super().__init__(f"ggwp_obs_decisions 第 {row_id} 行的 kind 不在合同的九种之内：先部署认识它的采集服务")


def _is_id(value: Any, *, low: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= MAX_ROW_ID


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One row of ggwp_obs_decisions: its id and its body, a frozen copy of one of the contract's nine decision models.

    Built in code, a bad id is a ValueError and a body that is not a contract decision a TypeError: programming errors,
    never a DecisionLogError (decision_record raises that for what it reads from the table).
    """

    id: int
    decision: Decision

    def __post_init__(self):
        if not _is_id(self.id, low=1):
            raise ValueError("决定的 id 是 1 到 2^63−1 的整数")
        if not isinstance(self.decision, DECISION_MODELS):
            raise TypeError("DecisionRecord 的正文只能是合同的九种决定模型之一")
        if not isinstance(self.decision, _FROZEN_MODELS):
            # The caller's model stays the caller's; the record keeps a frozen copy of it.
            object.__setattr__(self, "decision", _DECISION.validate_python(self.decision.model_dump()))


class CorrespondenceKey(NamedTuple):
    """What a confirmation holds for, beside its identity (D24)."""

    platform: str
    normalized_title: str


class ConfirmMark(NamedTuple):
    """The latest confirmation of one identity: the id of the decision that made it (a lapse names it) and its key."""

    decision_id: int
    key: CorrespondenceKey


class Sighting(NamedTuple):
    """One shared catalog batch as the lapse fold reads it (catalog_history.read_sightings).

    keys holds the correspondence key of each identity asked about that the batch has; one it lacks is absent. keys is
    None when the batch's rows are gone (pruned): nothing about it is known.
    """

    batch_id: str
    keys: Mapping[str, CorrespondenceKey] | None


class PauseMark(NamedTuple):
    """The latest pause decision on one key; decision ids order the per-geo mark against the all-geos one."""

    decision_id: int
    paused: bool


class AliasMark(NamedTuple):
    """The latest verdict on one alias queue entry, and the id of the decision that gave it (D43 replays by id)."""

    decision_id: int
    verdict: AliasVerdict


class PairMark(NamedTuple):
    """The latest manual pairing of one old identity, and the id of the decision that made it (D43 replays by id)."""

    decision_id: int
    new_identity: str


def _empty() -> Mapping:
    return _EMPTY


@dataclass(frozen=True, slots=True)
class EffectiveDecisions:
    """The effective state at `version` (decisions_version): every field immutable, built anew by each decision.

    Compared, never hashed: its mappings are read-only proxies, which do not hash.
    """

    version: int
    alias_verdicts: Mapping[int, AliasMark] = field(default_factory=_empty)
    alias_pairs: Mapping[str, PairMark] = field(default_factory=_empty)
    correspondences: Mapping[str, ConfirmMark] = field(default_factory=_empty)
    watch_added: frozenset[tuple[str, str]] = frozenset()
    over_cap: tuple[int, ...] = ()
    pauses: Mapping[tuple[str, str | None], PauseMark] = field(default_factory=_empty)
    ambiguity: Mapping[str, AmbiguityVerdict] = field(default_factory=_empty)
    irrelevant_alerts: frozenset[int] = frozenset()

    __hash__ = None

    def correspondence(self, identity: str, platform: str, normalized_title: str, *, lapsed: Container[int]) -> Correspondence:
        """The identity's correspondence for its platform and normalized title in the current shared batch: confirmed
        exactly when they are the latest confirmation's and that confirmation is not among `lapsed` (the set's frozen
        lapses, what lapse() returned for this state)."""
        mark = self.correspondences.get(identity)
        holds = mark is not None and mark.decision_id not in lapsed and mark.key == (platform, normalized_title)
        return "confirmed" if holds else "unconfirmed"

    def confirmed_key(self, identity: str) -> CorrespondenceKey | None:
        """The key the identity's latest confirmation names, lapsed or not."""
        mark = self.correspondences.get(identity)
        return None if mark is None else mark.key

    def is_paused(self, identity: str, geo: str) -> bool:
        marks = [mark for mark in (self.pauses.get((identity, geo)), self.pauses.get((identity, None))) if mark is not None]
        return max(marks).paused if marks else False

    def ambiguity_verdict(self, identity: str) -> AmbiguityVerdict | None:
        return self.ambiguity.get(identity)

    def watch_added_outside(self, identities: Container[str]) -> frozenset[tuple[str, str]]:
        """The entries in effect whose identity is not in `identities` (a shared batch's). They keep their slot until
        withdrawn, so the data board lists them for the operator to withdraw (TR-25, TR-24)."""
        return frozenset(entry for entry in self.watch_added if entry[0] not in identities)


# ---- one step per kind: each returns a new state ------------------------------------------------------------------


def _put(mapping: Mapping, key, value) -> Mapping:
    return MappingProxyType({**mapping, key: value})


def _without(mapping: Mapping, key) -> Mapping:
    return MappingProxyType({kept: value for kept, value in mapping.items() if kept != key})


def _over_cap(state: EffectiveDecisions, entry: tuple[str, str]) -> bool:
    return entry not in state.watch_added and len(state.watch_added) >= WATCH_ADD_CAP


def _alias_verdict(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    mark = AliasMark(record.id, "confirmed" if record.decision.kind == "alias_confirm" else "rejected")
    return replace(state, alias_verdicts=_put(state.alias_verdicts, record.decision.alias_id, mark))


def _alias_pair(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    """The old identity is replaced: its confirmation goes, and a later pairing back does not bring it back (D24)."""
    decision = record.decision
    return replace(
        state,
        alias_pairs=_put(state.alias_pairs, decision.old_identity, PairMark(record.id, decision.new_identity)),
        correspondences=_without(state.correspondences, decision.old_identity),
    )


def _confirm(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    decision = record.decision
    mark = ConfirmMark(record.id, CorrespondenceKey(decision.platform, decision.normalized_title))
    return replace(state, correspondences=_put(state.correspondences, decision.identity, mark))


def _revoke(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    return replace(state, correspondences=_without(state.correspondences, record.decision.identity))


def _watch_add(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    entry = (record.decision.identity, record.decision.geo)
    if not record.decision.active:
        return replace(state, watch_added=state.watch_added - {entry})
    if _over_cap(state, entry):
        return replace(state, over_cap=(*state.over_cap, record.id))
    return replace(state, watch_added=state.watch_added | {entry})


def _watch_pause(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    decision = record.decision
    return replace(state, pauses=_put(state.pauses, (decision.identity, decision.geo), PauseMark(record.id, decision.paused)))


def _ambiguity(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    return replace(state, ambiguity=_put(state.ambiguity, record.decision.identity, record.decision.verdict))


def _irrelevant(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    return replace(state, irrelevant_alerts=state.irrelevant_alerts | {record.decision.alert_id})


Step = Callable[[EffectiveDecisions, DecisionRecord], EffectiveDecisions]
STEPS: Mapping[str, Step] = MappingProxyType(
    {
        "alias_confirm": _alias_verdict,
        "alias_reject": _alias_verdict,
        "alias_pair": _alias_pair,
        "correspondence_confirm": _confirm,
        "correspondence_revoke": _revoke,
        "watch_add": _watch_add,
        "watch_pause": _watch_pause,
        "ambiguity_override": _ambiguity,
        "alert_irrelevant": _irrelevant,
    }
)


def _step(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    return STEPS[record.decision.kind](state, record)


# ---- the chain ------------------------------------------------------------------------------------------------------


def _require_version(upto_id: Any) -> None:
    if not _is_id(upto_id, low=0):
        raise ValueError("upto_id 是 0 到 2^63−1 的整数（decisions_version，0 表示没有决定）")


def _in_id_order(decisions: Iterable[DecisionRecord]) -> tuple[DecisionRecord, ...]:
    records = tuple(decisions)
    if not all(isinstance(record, DecisionRecord) for record in records):
        raise TypeError("effective 只接受 DecisionRecord")
    if len({record.id for record in records}) != len(records):
        raise ValueError("决定编号重复：ggwp_obs_decisions 的 id 从不重复，这份输入是拼出来的")
    return tuple(sorted(records, key=lambda record: record.id))


def effective(decisions: Iterable[DecisionRecord], upto_id: int) -> EffectiveDecisions:
    """The state the decisions with id <= upto_id leave, applied in id order whatever order they come in."""
    _require_version(upto_id)
    applied = (record for record in _in_id_order(decisions) if record.id <= upto_id)
    return reduce(_step, applied, EffectiveDecisions(version=upto_id))


def latest_id(decisions: Iterable[DecisionRecord]) -> int:
    """The decisions_version a set freezes after reading these: the largest id, 0 for none."""
    return max((record.id for record in decisions), default=0)


# ---- lapses (D24): the fold the Trends collector carries from one set to the next ----------------------------------


def _require_lapsed(lapsed: Iterable[int]) -> frozenset[int]:
    ids = tuple(lapsed)
    if not all(_is_id(value, low=1) for value in ids):
        raise ValueError("lapsed 是对应确认的决定 id，1 到 2^63−1 的整数")
    return frozenset(ids)


def _require_sightings(sightings: Iterable[Sighting]) -> tuple[Sighting, ...]:
    seen = tuple(sightings)
    if not all(isinstance(sighting, Sighting) and (sighting.keys is None or isinstance(sighting.keys, Mapping)) for sighting in seen):
        raise TypeError("sightings 只接受 Sighting（keys 是映射，或批次行已清理时为 None）")
    return seen


def _contradicts(sighting: Sighting, identity: str, key: CorrespondenceKey) -> bool:
    """The batch shows the identity otherwise than the confirmation: another key, missing, or nothing known."""
    return sighting.keys is None or sighting.keys.get(identity) != key


def lapse(state: EffectiveDecisions, lapsed: Iterable[int], sightings: Iterable[Sighting]) -> frozenset[int]:
    """The confirmations lapsed once `sightings` are seen on top of `lapsed`, the ids a set freezes.

    `lapsed` is the previous Trends set's frozen lapses; `sightings` are the shared catalog batches that became current
    since that set was read, through this set's own batch (catalog_history.read_sightings, asked about every identity
    `state` has a confirmation for). A latest confirmation of `state` is in the result when it had lapsed already or a
    sighting contradicts it. Ids that are no longer any identity's latest confirmation are dropped: a lapse only ever
    concerns the confirmation it names, and a new one is a new id.
    """
    known = _require_lapsed(lapsed)
    seen = _require_sightings(sightings)
    return frozenset(
        mark.decision_id
        for identity, mark in state.correspondences.items()
        if mark.decision_id in known or any(_contradicts(sighting, identity, mark.key) for sighting in seen)
    )


def refusal(state: EffectiveDecisions, decision: Decision) -> str | None:
    """Why the gateway does not append `decision` on top of `state`, or None (TR-25 asks under lock_for_append).

    Only an active manual addition is ever refused: when WATCH_ADD_CAP entries are in effect and it is not one of them.
    Entries outside the current batch hold their slot too (watch_added_outside), which the message says.
    """
    if isinstance(decision, WatchAdd) and decision.active and _over_cap(state, (decision.identity, decision.geo)):
        return f"人工加入已有 {WATCH_ADD_CAP} 条生效（已不在当前批次或已换别名的也算），先撤回一条再加"
    return None


# ---- appending (TR-25) ----------------------------------------------------------------------------------------------

_APPEND_LOCKS: Mapping[str, str] = MappingProxyType(
    {
        # Waits for another holder and for any plain INSERT, never for a reader. It needs the table's owner or UPDATE
        # (the gateway's deerflow_app); pick_observer only reads the table and never takes it.
        "postgresql": f"LOCK TABLE {obs_decisions.name} IN SHARE ROW EXCLUSIVE MODE",
        # The database's write lock, taken now rather than at the first write: so nothing may run before it.
        "sqlite": "BEGIN IMMEDIATE",
    }
)


def _dialect_name(conn) -> str:
    """An AsyncConnection has the dialect; an AsyncSession reaches it through its bind."""
    return (conn.dialect if hasattr(conn, "dialect") else conn.get_bind().dialect).name


async def lock_for_append(conn) -> None:
    """The first statement of every transaction that appends to ggwp_obs_decisions (TR-25): one append at a time.

    The appender then reads the effective state (read_effective), asks refusal, inserts, and commits, all under the
    lock. So ids commit in id order, which makes decisions_version a faithful cut, and the cap check sees the state the
    append lands on. `conn` is the AsyncConnection or AsyncSession of that transaction; the lock ends with it.
    """
    name = _dialect_name(conn)
    if name not in _APPEND_LOCKS:
        raise ValueError(f"lock_for_append 只认 PostgreSQL 与 SQLite，不认 {name}")
    await conn.execute(text(_APPEND_LOCKS[name]))


# ---- reading the log ------------------------------------------------------------------------------------------------


def _location(problem: Mapping) -> str:
    loc = problem["loc"]
    if problem["type"] == "extra_forbidden":
        loc = (*loc[:-1], "<extra>")  # the stored key itself is content
    return ".".join(str(part) for part in loc)


def _where(error: ValidationError) -> str:
    """Where a stored body breaks the contract: locations and error types only, never a value or an unexpected key."""
    found = error.errors(include_url=False, include_context=False, include_input=False)[:_MAX_REPORTED_PROBLEMS]
    return "；".join(f"{_location(problem)}（{problem['type']}）" for problem in found)


def _contract_body(row_id: int, payload: dict) -> Decision:
    try:
        return _DECISION.validate_python(payload)
    except ValidationError as error:
        where = _where(error)
    # Raised outside the except block: the ValidationError, which holds the stored body, is neither cause nor context.
    raise DecisionLogError(f"ggwp_obs_decisions 第 {row_id} 行不合合同：{where}")


def decision_record(row_id: Any, kind: Any, payload: Any) -> DecisionRecord:
    """A row of ggwp_obs_decisions as a DecisionRecord: kind is the column, payload the stored body (payload_json)."""
    if not _is_id(row_id, low=1):
        raise DecisionLogError("ggwp_obs_decisions 的 id 是 1 到 2^63−1 的整数")
    if kind not in DECISION_KINDS:
        raise UnknownDecisionKind(row_id)
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        raise DecisionLogError(f"ggwp_obs_decisions 第 {row_id} 行的 payload_json 与 kind 列不一致")
    return DecisionRecord(row_id, _contract_body(row_id, payload))


async def read_decisions(conn, upto_id: int | None) -> tuple[DecisionRecord, ...]:
    """The decisions with id <= upto_id (every one for None), in id order.

    One SELECT and nothing else: no write and no lock, so pick_observer, which may only read this table (0007's
    OBSERVER_READS), can run it. `conn` is a SQLAlchemy AsyncConnection or AsyncSession; the caller owns the transaction.
    """
    query = select(obs_decisions.c.id, obs_decisions.c.kind, obs_decisions.c.payload_json).order_by(obs_decisions.c.id)
    if upto_id is not None:
        _require_version(upto_id)
        # A version runs to 2^63-1 (the contract's MAX_ROW_ID) while the id column is int4 on PostgreSQL: bound as bigint.
        query = query.where(obs_decisions.c.id <= literal(upto_id, BigInteger))
    rows = (await conn.execute(query)).all()
    return tuple(decision_record(row.id, row.kind, row.payload_json) for row in rows)


async def read_effective(conn, upto_id: int | None = None) -> EffectiveDecisions:
    """The effective state at upto_id, or, for None, at the largest id visible now: the decisions_version to freeze."""
    records = await read_decisions(conn, upto_id)
    return effective(records, latest_id(records) if upto_id is None else upto_id)
