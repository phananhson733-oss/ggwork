"""The gateway's append to ggwp_obs_decisions (plan TR-25 and its 2026-09-30 note, D12, D24; contract section 12).

POST /api/pick/obs/decisions hands each body here, as bytes: parse_decision reads it against the contract and says
where it breaks the contract and which rule, never what was sent (no tag, no unexpected key, no value). One transaction
then does it all, in the order decisions_state asks (its lock_for_append, and the TR-35 handoff in progress.md): the
table lock is the first statement, then the effective state, the owner's earlier use of the request_id, refusal, and
the insert. Under that lock commit order is id order, so a set's decisions_version is a faithful cut, and the 50-entry
cap is checked against the state the append lands on.

A request_id is the owner's (as the selections' command receipts are): sent again with the same body, the row it wrote
is answered and nothing is appended; with another body it is refused. A log this code cannot apply stops every
request, a repeated one and a revocation too: read_effective raises DecisionLogError before anything else is looked at,
and the route answers it without the row's content.

Gateway side only: the collectors never import this module (they read the log with read_decisions).
"""

import json
from dataclasses import asdict, dataclass
from datetime import datetime

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import insert, select

from ggwork_pick.contracts import storable
from ggwork_pick.models import obs_decisions
from ggwork_pick.observe.contract_api import Decision
from ggwork_pick.observe.decisions_state import lock_for_append, read_effective, refusal
from ggwork_pick.observe.instants import stamp
from ggwork_pick.repository import SHARED_OWNER

# A decision is a few hundred bytes (note <= 500 characters); the route stops reading well past any valid one.
MAX_BODY_BYTES = 16384
_MAX_PROBLEMS = 5
_TAG_ERRORS = frozenset({"union_tag_invalid", "union_tag_not_found"})
_DECISION = TypeAdapter(Decision)


class DecisionInvalid(Exception):
    """The body is not one of the contract's nine decisions. problems name where and which rule, never a sent value."""

    def __init__(self, problems: tuple[str, ...]):
        super().__init__("；".join(problems))
        self.problems = problems


class DecisionRefused(Exception):
    """The effective state refuses the decision (refusal()); the message is refusal's, for the operator."""


class RequestIdReused(Exception):
    """The owner already used this request_id for another body."""

    def __init__(self):
        super().__init__("这个 request_id 已经用于另一条内容不同的决定：换一个 request_id 再提交")


@dataclass(frozen=True, slots=True)
class Appended:
    """The row a decision is in: appended now, or found again for a repeated request_id (replayed)."""

    id: int
    kind: str
    request_id: str
    created_at: str
    replayed: bool

    def as_dict(self) -> dict:
        return asdict(self)


def _location(problem: dict) -> str:
    """A problem's place in the body in the contract's own names: an unexpected key is <extra>, a bad tag is kind.

    Pydantic's loc holds model field names, list indices and the union's tag once the tag is one of the nine kinds; only
    an extra key (the last step of extra_forbidden's loc) comes from the sender. Its msg can quote the input, so it is
    never used."""
    loc = tuple(problem["loc"])
    if problem["type"] == "extra_forbidden":
        loc = (*loc[:-1], "<extra>")
    if not loc:
        loc = ("kind",) if problem["type"] in _TAG_ERRORS else ("body",)
    return ".".join(str(part) for part in loc)


def parse_decision(raw: bytes) -> Decision:
    """The contract decision in a request body, or DecisionInvalid naming at most five problems as 'place（type）'."""
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, ValueError):
        raise DecisionInvalid(("body（json_invalid）",)) from None
    try:
        return _DECISION.validate_python(payload)
    except ValidationError as error:
        found = error.errors(include_url=False, include_context=False, include_input=False)[:_MAX_PROBLEMS]
        problems = tuple(f"{_location(problem)}（{problem['type']}）" for problem in found)
    # Raised outside the except block: the ValidationError, which holds the body, is neither cause nor context.
    raise DecisionInvalid(problems)


def _require_owner(owner_id: str) -> None:
    if not isinstance(owner_id, str) or not owner_id.strip() or owner_id in ("default", SHARED_OWNER):
        raise ValueError("写人工决定要有认证过的操作人")


async def _earlier(session, owner_id: str, request_id: str) -> dict | None:
    query = select(obs_decisions).where(obs_decisions.c.owner_id == owner_id, obs_decisions.c.request_id == request_id).order_by(obs_decisions.c.id)
    row = (await session.execute(query)).mappings().first()
    return None if row is None else dict(row)


async def _insert(session, row: dict) -> int:
    return (await session.execute(insert(obs_decisions).values(**row))).inserted_primary_key[0]


def _answer(row: dict, *, replayed: bool) -> Appended:
    return Appended(row["id"], row["kind"], row["request_id"], row["created_at"], replayed)


async def append_decision(session_factory, owner_id: str, decision: Decision, *, now: datetime) -> Appended:
    """Append `decision` as `owner_id` at `now`, or answer the row an earlier identical request wrote.

    Raises DecisionRefused, RequestIdReused, or decisions_state.DecisionLogError when the log holds a row this code
    cannot apply; nothing is appended then.
    """
    _require_owner(owner_id)
    body = storable(decision.model_dump(mode="json"))
    async with session_factory() as session, session.begin():
        await lock_for_append(session)
        # The whole log first: a row this code cannot apply stops every request, a repeated one too (decisions.md).
        state = await read_effective(session)
        earlier = await _earlier(session, owner_id, decision.request_id)
        if earlier is not None:
            if earlier["payload_json"] != body:
                raise RequestIdReused()
            # Not asked again: the state has moved on since, and a repeat answers what the first request did.
            return _answer(earlier, replayed=True)
        reason = refusal(state, decision)
        if reason is not None:
            raise DecisionRefused(reason)
        row = {"kind": decision.kind, "request_id": decision.request_id, "owner_id": owner_id, "payload_json": body, "created_at": stamp(now)}
        return _answer({**row, "id": await _insert(session, row)}, replayed=False)
