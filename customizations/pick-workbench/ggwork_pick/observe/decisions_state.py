"""Human decisions -> effective state: the one implementation of D24's chain (plan TR-35; D12, D24, D43; design 4.6, 4.7,
7.1, 7.2; contract section 12).

ggwp_obs_decisions is append-only: TR-25 writes each POST /api/pick/obs/decisions body into payload_json, its kind into
the kind column. A collector reads the log once when it publishes, up to the largest id it saw, and freezes that id into
the set as decisions_version; effective(decisions, version) is then what the set was judged with, whatever is decided
later, so an old set never changes (design 7.1: the agent reads judgment rows, never the live log). The gateway checks a
new decision against the same state before appending it (refusal).

- Decisions apply in id order, a later one overriding an earlier one on the same key; ids above upto_id are ignored.
- Correspondence (design 4.7): a confirmation holds for one (identity, platform, normalized title), the latest per
  identity; a revocation clears the identity. correspondence() answers for the identity's current platform and title in
  the current shared batch, so a changed title or platform is unconfirmed, and so is an identity an alias replaced: a
  confirmation never follows an alias to the new identity.
- Manual additions (design 4.6): an entry is (identity, geo). At most WATCH_ADD_CAP are in effect; an addition beyond
  the cap is dropped for good (over_cap names it), exactly what the gateway would have refused, so two appends racing
  past the gateway's check still leave 50. Re-adding an entry in effect changes nothing; active=false withdraws it.
- Pauses: per (identity, geo), geo None meaning every geo; for one geo the later of its own and the all-geos mark wins.
- Alias decisions are handed on, not applied here (D43): the gsc service's alias refresh reads alias_verdicts (by the
  alias queue's id) and alias_pairs (one new identity per old one, the latest pairing) and writes a new alias version;
  cycles and the other graphs design 7.2 refuses are the refresh's to refuse.
- Ambiguity overrides are per identity, the latest verdict; an alert marked irrelevant stays so.
- Time never enters: a decision applies by its id, never by when it was written or read.

decisions_version is a faithful cut only if no smaller id commits after a larger one is visible. PostgreSQL draws ids
when a row is inserted, not when it commits, so the writer has to append one decision at a time (TR-25's seam).

A log this code cannot apply (an unknown kind, a body that is not the contract's, a repeated id) is never applied in
part: DecisionLogError is a StateUnavailable, and a collector exits 3 instead of publishing a set on a guessed state.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from functools import reduce
from types import MappingProxyType
from typing import Any, Literal, NamedTuple

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select

from ggwork_pick.models import obs_decisions
from ggwork_pick.observe.contract import DECISION_KINDS, MAX_ROW_ID, AmbiguityVerdict, Correspondence
from ggwork_pick.observe.contract_api import DECISION_MODELS, Decision, WatchAdd
from ggwork_pick.observe.errors import StateUnavailable

WATCH_ADD_CAP = 50  # design 4.6 rule 5
AliasVerdict = Literal["confirmed", "rejected"]
_DECISION = TypeAdapter(Decision)
_EMPTY: Mapping = MappingProxyType({})
_MAX_REPORTED_PROBLEMS = 3


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
    """One row of ggwp_obs_decisions: its id and its body, one of the contract's nine decision models."""

    id: int
    decision: Decision

    def __post_init__(self):
        if not _is_id(self.id, low=1):
            raise DecisionLogError("ggwp_obs_decisions 的 id 是 1 到 2^63−1 的整数")
        if not isinstance(self.decision, DECISION_MODELS):
            raise UnknownDecisionKind(self.id)


class CorrespondenceKey(NamedTuple):
    """What a confirmation holds for, beside its identity (D24)."""

    platform: str
    normalized_title: str


class PauseMark(NamedTuple):
    """The latest pause decision on one key; decision ids order the per-geo mark against the all-geos one."""

    decision_id: int
    paused: bool


def _empty() -> Mapping:
    return _EMPTY


@dataclass(frozen=True, slots=True)
class EffectiveDecisions:
    """The effective state at `version` (decisions_version): every field immutable, built anew by each decision."""

    version: int
    alias_verdicts: Mapping[int, AliasVerdict] = field(default_factory=_empty)
    alias_pairs: Mapping[str, str] = field(default_factory=_empty)
    correspondences: Mapping[str, CorrespondenceKey] = field(default_factory=_empty)
    watch_added: frozenset[tuple[str, str]] = frozenset()
    over_cap: tuple[int, ...] = ()
    pauses: Mapping[tuple[str, str | None], PauseMark] = field(default_factory=_empty)
    ambiguity: Mapping[str, AmbiguityVerdict] = field(default_factory=_empty)
    irrelevant_alerts: frozenset[int] = frozenset()

    def correspondence(self, identity: str, platform: str, normalized_title: str) -> Correspondence:
        """The identity's correspondence for its platform and normalized title in the current shared batch."""
        return "confirmed" if self.correspondences.get(identity) == (platform, normalized_title) else "unconfirmed"

    def confirmed_key(self, identity: str) -> CorrespondenceKey | None:
        return self.correspondences.get(identity)

    def is_paused(self, identity: str, geo: str) -> bool:
        marks = [mark for mark in (self.pauses.get((identity, geo)), self.pauses.get((identity, None))) if mark is not None]
        return max(marks).paused if marks else False

    def ambiguity_verdict(self, identity: str) -> AmbiguityVerdict | None:
        return self.ambiguity.get(identity)


# ---- one step per kind: each returns a new state ------------------------------------------------------------------


def _put(mapping: Mapping, key, value) -> Mapping:
    return MappingProxyType({**mapping, key: value})


def _without(mapping: Mapping, key) -> Mapping:
    return MappingProxyType({kept: value for kept, value in mapping.items() if kept != key})


def _over_cap(state: EffectiveDecisions, entry: tuple[str, str]) -> bool:
    return entry not in state.watch_added and len(state.watch_added) >= WATCH_ADD_CAP


def _alias_verdict(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    verdict = "confirmed" if record.decision.kind == "alias_confirm" else "rejected"
    return replace(state, alias_verdicts=_put(state.alias_verdicts, record.decision.alias_id, verdict))


def _alias_pair(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    decision = record.decision
    return replace(state, alias_pairs=_put(state.alias_pairs, decision.old_identity, decision.new_identity))


def _confirm(state: EffectiveDecisions, record: DecisionRecord) -> EffectiveDecisions:
    decision = record.decision
    key = CorrespondenceKey(decision.platform, decision.normalized_title)
    return replace(state, correspondences=_put(state.correspondences, decision.identity, key))


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
        raise DecisionLogError("effective 只接受 DecisionRecord")
    if len({record.id for record in records}) != len(records):
        raise DecisionLogError("决定编号重复：ggwp_obs_decisions 的 id 从不重复，这份输入不是从它读出来的")
    return tuple(sorted(records, key=lambda record: record.id))


def effective(decisions: Iterable[DecisionRecord], upto_id: int) -> EffectiveDecisions:
    """The state the decisions with id <= upto_id leave, applied in id order whatever order they come in."""
    _require_version(upto_id)
    applied = (record for record in _in_id_order(decisions) if record.id <= upto_id)
    return reduce(_step, applied, EffectiveDecisions(version=upto_id))


def latest_id(decisions: Iterable[DecisionRecord]) -> int:
    """The decisions_version a set freezes after reading these: the largest id, 0 for none."""
    return max((record.id for record in decisions), default=0)


def refusal(state: EffectiveDecisions, decision: Decision) -> str | None:
    """Why the gateway does not append `decision` on top of `state`, or None (TR-25 asks before its write).

    Only an active manual addition is ever refused: when WATCH_ADD_CAP entries are in effect and it is not one of them.
    """
    if isinstance(decision, WatchAdd) and decision.active and _over_cap(state, (decision.identity, decision.geo)):
        return f"人工加入已有 {WATCH_ADD_CAP} 条生效，先撤回一条再加"
    return None


# ---- reading the log ------------------------------------------------------------------------------------------------


def _where(error: ValidationError) -> str:
    """Where a stored body breaks the contract: locations and error types only, never the values."""
    found = error.errors(include_url=False, include_context=False, include_input=False)[:_MAX_REPORTED_PROBLEMS]
    return "；".join(f"{'.'.join(str(part) for part in problem['loc'])}（{problem['type']}）" for problem in found)


def decision_record(row_id: Any, kind: Any, payload: Any) -> DecisionRecord:
    """A row of ggwp_obs_decisions as a DecisionRecord: kind is the column, payload the stored body (payload_json)."""
    if not _is_id(row_id, low=1):
        raise DecisionLogError("ggwp_obs_decisions 的 id 是 1 到 2^63−1 的整数")
    if kind not in DECISION_KINDS:
        raise UnknownDecisionKind(row_id)
    if not isinstance(payload, dict) or payload.get("kind") != kind:
        raise DecisionLogError(f"ggwp_obs_decisions 第 {row_id} 行的 payload_json 与 kind 列不一致")
    try:
        decision = _DECISION.validate_python(payload)
    except ValidationError as error:
        raise DecisionLogError(f"ggwp_obs_decisions 第 {row_id} 行不合合同：{_where(error)}") from None
    return DecisionRecord(row_id, decision)


async def read_decisions(conn, upto_id: int | None) -> tuple[DecisionRecord, ...]:
    """The decisions with id <= upto_id (every one for None), in id order.

    One SELECT and nothing else: no write and no lock, so pick_observer, which may only read this table (0007's
    OBSERVER_READS), can run it. `conn` is a SQLAlchemy AsyncConnection or AsyncSession; the caller owns the transaction.
    """
    query = select(obs_decisions.c.id, obs_decisions.c.kind, obs_decisions.c.payload_json).order_by(obs_decisions.c.id)
    if upto_id is not None:
        _require_version(upto_id)
        query = query.where(obs_decisions.c.id <= upto_id)
    rows = (await conn.execute(query)).all()
    return tuple(decision_record(row.id, row.kind, row.payload_json) for row in rows)


async def read_effective(conn, upto_id: int | None = None) -> EffectiveDecisions:
    """The effective state at upto_id, or, for None, at the largest id visible now: the decisions_version to freeze."""
    records = await read_decisions(conn, upto_id)
    return effective(records, latest_id(records) if upto_id is None else upto_id)
