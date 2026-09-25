"""Query units, the day's task list and its truncation order (plan TR-14; design 4.5).

A query unit is identity x geo x query shape x time range: explore, then multiline for the series, and relatedsearches
when the unit asks for related queries, so 2 or 3 HTTP requests. A task source (CanaryTaskSource here, TR-18's
WatchTaskSource and TR-19's seeds later) hands the session its candidate units; the session orders them, cuts the list
to the mode's plan and keeps what did not fit, in that same order, in the batch's plan_json, so the data page can list
the uncovered units (design 4.5 "未覆盖 N 个单元").

The truncation order (design 4.5): rule priority, then listed_at newest first, then the latest evidence date, then a
hash of the identity and the target date, which rotates the ties from one day to the next. The plan keeps back the
design's allowance for warm-up, probes and retries (4.5: about 15 in the canary, 20 when stable); the mode's cap is the
hard ceiling on top of it, enforced request by request.
"""

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.trends import budget
from ggwork_pick.observe.trends import state_codec as codec
from ggwork_pick.observe.trends.source import TrendsQuery

PLAN_FORMAT = "trends-session-plan-v1"
# Budget items (ggwp_obs_requests.budget_item, design 4.5's rows): the unit's own, plus the ones a request takes from
# where it falls: warmup, probe (the first request after a pause), retry (a unit's second attempt), related.
UNIT_ITEMS = ("contract_check", "market", "control", "title")
REQUEST_ITEMS = ("warmup", "probe", "retry", "related", *UNIT_ITEMS)
# Design 4.5: warm-up, probes and retries are planned out of the day's total, not added to it.
OVERHEAD = MappingProxyType({"canary1": 15, "canary2": 15, "stable": 20})
DEFAULT_OVERHEAD = 20
_KEY = re.compile(r"[a-z0-9_]{1,20}:[0-9a-f]{12}")
_NO_DATE = -1  # sorts after every real date, newest first


@dataclass(frozen=True)
class QueryUnit:
    """One unit of the day's task list. terms and bare are TrendsQuery's (design 4.7: the judgement reads the bare
    line; a generic title has no bare term). identity is None for a market series and the contract check."""

    key: str
    item: str
    geo: str
    terms: tuple[str, ...]
    bare: str | None
    granularity: str
    priority: int
    timeline: bool = True
    related: bool = False
    identity: str | None = None
    listed_at: date | None = None
    evidence_on: date | None = None
    group: str | None = None  # the canary control's group (positive, negative, regional), for the report

    def __post_init__(self) -> None:
        if self.item not in UNIT_ITEMS:
            raise ValueError(f"unit item is one of {UNIT_ITEMS}")
        if not _KEY.fullmatch(self.key):
            raise ValueError("unit key is <item>:<12 hex digits>")
        if type(self.priority) is not int or self.priority < 0 or not (self.timeline or self.related):
            raise ValueError("a unit has a priority of 0 or more and asks for a series, related queries or both")
        self.query()  # the terms, geo and granularity are a valid request

    @property
    def http(self) -> int:
        """Its requests: explore, multiline for the series, relatedsearches for the related queries."""
        return 1 + int(self.timeline) + int(self.related)

    def query(self) -> TrendsQuery:
        related_term = (self.bare or self.terms[0]) if self.related else None
        return TrendsQuery(terms=self.terms, bare=self.bare, geo=self.geo, granularity=self.granularity, related_term=related_term)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "item": self.item,
            "geo": self.geo,
            "terms": list(self.terms),
            "bare": self.bare,
            "granularity": self.granularity,
            "priority": self.priority,
            "timeline": self.timeline,
            "related": self.related,
            "identity": self.identity,
            "listed_at": codec.encode_day(self.listed_at),
            "evidence_on": codec.encode_day(self.evidence_on),
            "group": self.group,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "QueryUnit":
        data = codec.exact_keys(data, _UNIT_KEYS, "query unit")
        terms = data["terms"]
        if not isinstance(terms, list | tuple):
            raise ValueError("query unit terms must be a list")
        return cls(
            key=data["key"],
            item=data["item"],
            geo=data["geo"],
            terms=tuple(terms),
            bare=data["bare"],
            granularity=data["granularity"],
            priority=data["priority"],
            timeline=codec.flag(data["timeline"], "timeline"),
            related=codec.flag(data["related"], "related"),
            identity=data["identity"],
            listed_at=codec.decode_day(data["listed_at"], "listed_at"),
            evidence_on=codec.decode_day(data["evidence_on"], "evidence_on"),
            group=data["group"],
        )


_UNIT_KEYS = frozenset(QueryUnit.__dataclass_fields__)


def unit_key(item: str, *parts: object) -> str:
    """A stable key: the item and a hash of what makes the unit (identity or term, geo, time range)."""
    digest = hashlib.sha256("\x1f".join(str(part) for part in parts).encode("utf-8")).hexdigest()[:12]
    return f"{item}:{digest}"


def _newest_first(day: date | None) -> int:
    return -day.toordinal() if day is not None else -_NO_DATE


def rotation(unit: QueryUnit, target_date: date) -> str:
    """The last tie-break: a hash of the identity (or the term) and the target date, so ties rotate daily."""
    who = unit.identity if unit.identity is not None else "\x1f".join(unit.terms)
    return hashlib.sha256(f"{target_date:%Y-%m-%d}\x1f{who}\x1f{unit.geo}\x1f{unit.granularity}".encode()).hexdigest()


def truncation_key(unit: QueryUnit, target_date: date) -> tuple:
    """Design 4.5's order: rule priority, listed_at newest first, latest evidence first, then the daily rotation."""
    return (unit.priority, _newest_first(unit.listed_at), _newest_first(unit.evidence_on), rotation(unit, target_date))


def ordered(units: Iterable[QueryUnit], target_date: date) -> tuple[QueryUnit, ...]:
    """The units in truncation order, each key once (the first in that order wins)."""
    seen, kept = set(), []
    for unit in sorted(units, key=lambda unit: truncation_key(unit, target_date)):
        if unit.key not in seen:
            seen.add(unit.key)
            kept.append(unit)
    return tuple(kept)


def plan_budget(limits: budget.ModeLimits) -> int:
    """The requests the units may plan for: the mode's plan less design 4.5's allowance for warm-up, probes, retries."""
    return max(0, limits.plan - OVERHEAD.get(limits.name, DEFAULT_OVERHEAD))


@dataclass(frozen=True)
class TaskList:
    """The day's units in the order they run, and what did not fit, in truncation order."""

    planned: tuple[QueryUnit, ...]
    truncated: tuple[QueryUnit, ...]

    @property
    def planned_http(self) -> int:
        return sum(unit.http for unit in self.planned)


def cut_to_plan(units: Sequence[QueryUnit], allowance: int) -> TaskList:
    """The longest prefix of `units` (already in truncation order) whose requests fit `allowance`; the rest truncated.
    A prefix, never a later smaller unit in place of an earlier one: truncation keeps the order."""
    used, cut = 0, len(units)
    for index, unit in enumerate(units):
        if used + unit.http > allowance:
            cut = index
            break
        used += unit.http
    return TaskList(tuple(units[:cut]), tuple(units[cut:]))


@dataclass(frozen=True)
class SessionPlan:
    """plan_json: the task list and where it came from, fixed when the batch is created and read back on a resume."""

    source: str
    granularity: str
    related: bool
    tasks: TaskList
    catalog_batch_id: str | None = None
    notes: Mapping[str, Any] = MappingProxyType({})

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": PLAN_FORMAT,
            "source": self.source,
            "granularity": self.granularity,
            "related": self.related,
            "catalog_batch_id": self.catalog_batch_id,
            "units": [unit.to_dict() for unit in self.tasks.planned],
            "truncated": [unit.to_dict() for unit in self.tasks.truncated],
            "notes": dict(self.notes),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "SessionPlan":
        keys = frozenset({"format", "source", "granularity", "related", "catalog_batch_id", "units", "truncated", "notes"})
        data = codec.exact_keys(data, keys, "session plan")
        if data["format"] != PLAN_FORMAT or not isinstance(data["notes"], Mapping):
            raise ValueError("session plan format is not known")
        tasks = TaskList(tuple(map(QueryUnit.from_dict, data["units"])), tuple(map(QueryUnit.from_dict, data["truncated"])))
        related = codec.flag(data["related"], "related")
        return cls(data["source"], data["granularity"], related, tasks, data["catalog_batch_id"], MappingProxyType(dict(data["notes"])))
