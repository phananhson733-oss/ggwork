"""What every mirror gate shares (P2-4): the gate names and what a failure stops, GateResult, Findings, GateError.

gates.py is the entry point and re-exports what callers need; gate_totals.py holds G7. A detail says where and how many,
and names rows by identifier, never by value (plan 5.5, U37, U49): these helpers are the only way anything goes into one.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from ggwork_pick.contracts import storable
from ggwork_pick.mirror import pan
from ggwork_pick.mirror.contracts import ODD_KEY
from ggwork_pick.mirror.versions import describe_error

ROW_ID_CAP = 20  # U49: row identifiers a gate names, the total beside them
STATEMENT_TIMEOUT = 120  # seconds, each gate statement (P2-5c)
ID_PART_MAX = 200  # the longest piece of a key a row identifier repeats

V1_TEXT = "v1_text"  # G2
ROW_COUNTS = "row_counts"  # G3
FORBIDDEN_COLUMNS = "forbidden_columns"  # G4
MIRROR_TEXT = "mirror_text"  # G5
REFERENCES = "references"  # G6
CONTROL_TOTALS = "control_totals"  # G7
V1_CONSISTENCY = "v1_consistency"  # G8
EMPTY_TABLES = "empty_tables"  # G9
MIRROR_GATES = (ROW_COUNTS, FORBIDDEN_COLUMNS, MIRROR_TEXT, REFERENCES, CONTROL_TOTALS, V1_CONSISTENCY, EMPTY_TABLES)
# What a failure stops (plan 2.5, 5.3, 5.5): "v1" = neither side publishes, the staged batches are failed and the previous
# pair stays current; "mirror" = the version is failed and dropped, the staged v1 batches go out alone
# (publish_agent_only, reason "degraded:<gate name>").
CONSEQUENCES = MappingProxyType({V1_TEXT: "v1", **dict.fromkeys(MIRROR_GATES, "mirror")})

EMPTY = MappingProxyType({})
MISSING = object()  # value_at's answer for a path the value does not have
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_]{1,64}")
_NOT_TEXT = "<非文本>"


def no_hits() -> MappingProxyType:
    return EMPTY


def _frozen(value):
    """A read-only copy all the way down: mappings as MappingProxyType, lists and tuples as tuples."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _frozen(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


def _plain(value):
    """Back to JSON's shapes: dicts and lists."""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def shown_key(key) -> str:
    """A key as a path shows it: a plain name as it is, anything else (it could be text out of a row) as ODD_KEY."""
    return key if isinstance(key, str) and _PLAIN_KEY.fullmatch(key) else ODD_KEY


def dotted(path: Sequence[str]) -> str:
    return ".".join(shown_key(part) for part in path)


def is_count(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def count_or_none(value) -> int | None:
    """A count a detail may echo: a plain int, else None (never another value)."""
    return value if is_count(value) else None


def added(first: Mapping[str, int], second: Mapping[str, int]) -> MappingProxyType:
    return MappingProxyType(pan.add_hits(dict(first), dict(second)))


def id_part(value):
    """A key value as an identifier shows it: an int as it is; text made storable, scrubbed, then cut; anything else a
    marker. Keys are exempt from the scrub in the rows (plan 394), not here: details_json reaches every signed-in user
    (U37), so a key holding pan text is named by the replacement, scrubbed whole before the cut can split it."""
    if is_count(value):
        return value
    return pan.scrub_text(storable(value))[0][:ID_PART_MAX] if isinstance(value, str) else _NOT_TEXT


def value_at(value, path: Sequence[str]):
    """The item at path in nested mappings, or MISSING."""
    for key in path:
        if not isinstance(value, Mapping) or key not in value:
            return MISSING
        value = value[key]
    return value


@dataclass(frozen=True, slots=True)
class GateResult:
    """One gate's verdict. detail is read-only and holds paths, counts and row identifiers only."""

    name: str
    ok: bool
    detail: Mapping[str, object] = field(default_factory=no_hits)

    def __post_init__(self):
        if self.name not in CONSEQUENCES:
            raise ValueError("闸门名只能是 G2-G9 之一")
        object.__setattr__(self, "detail", _frozen(self.detail))

    @property
    def consequence(self) -> str:
        """What a failure stops: "v1" = neither side publishes; "mirror" = the version fails, the agent batches go alone."""
        return CONSEQUENCES[self.name]

    def as_json(self) -> str | dict:
        """For details_json: "pass" when there is nothing to say, else the verdict, its consequence and the detail."""
        if self.ok and not self.detail:
            return "pass"
        return {"ok": self.ok, "consequence": self.consequence, **_plain(self.detail)}


@dataclass(frozen=True, slots=True)
class Findings:
    """What a gate found, row by row: hits by path, the first ROW_ID_CAP rows' identifiers, how many rows in all."""

    paths: Mapping[str, int] = field(default_factory=no_hits)
    rows: tuple = ()
    total: int = 0

    def add(self, hits: Mapping[str, int], row_id=None) -> "Findings":
        """With one more row's hits; a row without any gives back this same object."""
        if not hits:
            return self
        rows = self.rows if row_id is None or len(self.rows) >= ROW_ID_CAP else (*self.rows, row_id)
        return Findings(added(self.paths, hits), rows, self.total + 1)

    def merge(self, other: "Findings") -> "Findings":
        if not other.total:
            return self
        return Findings(added(self.paths, other.paths), (*self.rows, *other.rows)[:ROW_ID_CAP], self.total + other.total)

    @property
    def hits(self) -> int:
        return sum(self.paths.values())

    def as_detail(self) -> dict:
        return {"paths": dict(self.paths), "rows": list(self.rows), "total": self.total}


class GateError(RuntimeError):
    """A gate could not read the version: a mirror-side failure (plan 5.5). Names the gate and the error class, no value."""

    def __init__(self, gate: str, reason: str):
        super().__init__(f"镜像闸门 {gate} 的查询失败：{reason}")
        self.gate = gate


async def read(conn, gate: str, method: str, statement: str, *args, timeout: float):
    """One SELECT on the dedicated connection, autocommitted, with its own timeout; a database failure is a GateError."""
    try:
        return await getattr(conn, method)(statement, *args, timeout=timeout)
    except Exception as exc:
        raise GateError(gate, describe_error(exc)) from None
