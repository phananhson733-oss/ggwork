"""Stage 0 of the Trends channel: the control list, the two daily task lists and the command line (plan TR-05;
design 4.9, 4.11).

Stage 0 is not a rate-limit test. It asks whether Trends can see the dramas GSC saw (gate A), whether a direct session
gets related queries and a readable userType (gate B), and what the answers really look like. It runs on this machine
over two days, at most MAX_HTTP_PER_DAY requests a day and MAX_HTTP_TOTAL in all, warm-ups, related queries and repeat
checks included, paced and broken by TR-03's machines exactly like the cron (stage0_run).

The control list (controls.json, built outside the repo because it holds real titles) has five groups:
- positive: 15-20 dramas whose exact-title query had GSC impressions, each in the country its query language points to;
- negative: generic titles and delisted dramas;
- regional: a few recently listed dramas for each of BG, DE, FR, IT;
- market: a localized "short drama" series per geo (design 4.7's market control);
- seed: platform seeds whose related queries gate B reads (design 4.8).
Every drama is asked hourly (now 7-d) and daily (today 1-m), each in a request of its own so no other line sets its
scale; related queries ride on the daily unit of the controls marked `related`. Each control runs whole on one day.

`plan` writes day1.json and day2.json; `run --day N` runs only that day; `report` writes the report, an interim one
while only day 1 is in.
"""

import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ggwork_pick.observe.contract import TREND_GEO_PATTERN
from ggwork_pick.observe.trends.source import TrendsQuery

CONTROLS_FORMAT = "trends-stage0-controls-v1"
PLAN_FORMAT = "trends-stage0-plan-v1"
GROUPS = ("positive", "negative", "regional", "market", "seed")
IDENTITY_GROUPS = frozenset({"positive", "negative", "regional"})
KINDS = MappingProxyType(
    {
        "positive": ("exact_title", "query_mismatch"),
        "negative": ("generic", "delisted"),
        "regional": ("recent",),
        "market": ("market",),
        "seed": ("seed",),
    }
)
INTERLEAVED = ("positive", "regional", "negative", "seed")  # round robin; market goes last (the cap cuts it first)
DAYS = (1, 2)
MAX_HTTP_PER_DAY = 90
MAX_HTTP_TOTAL = 180
WARMUPS_PER_DAY = 1
POSITIVE_COUNT = (15, 20)
REGIONAL_GEOS = frozenset({"BG", "DE", "FR", "IT"})
REPEAT_GAP_UNITS = 6  # units between a series and its repeat: a few minutes (design 4.11 "隔几分钟")
REPEAT_METHODS = MappingProxyType({1: "GET", 2: "POST"})  # day 2's repeat also checks explore by POST
WORKBENCH_SOURCE = "realshort-pick"
GEO = re.compile(rf"(?:{TREND_GEO_PATTERN})")
_CONTROL_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_SOURCE_ID = re.compile(r"[A-Za-z0-9_-]{1,400}")
_LANGUAGE = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,15}")
_UNIT_KEY = re.compile(r"[a-z0-9][a-z0-9-]{0,59}")


@dataclass(frozen=True)
class Control:
    id: str
    group: str
    kind: str
    term: str
    geo: str
    related: bool = False
    identity: str | None = None  # the workbench identity key, for dramas; None for market series and seeds

    def query(self, granularity: str) -> TrendsQuery:
        """One term per request: no other line sets this one's scale (design 4.7)."""
        return TrendsQuery(terms=(self.term,), bare=self.term, geo=self.geo, granularity=granularity)


@dataclass(frozen=True)
class Controls:
    built_at: str
    controls: tuple[Control, ...]
    provenance: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))  # where each part came from

    def by_id(self) -> Mapping[str, Control]:
        return MappingProxyType({control.id: control for control in self.controls})

    def group(self, name: str) -> tuple[Control, ...]:
        return tuple(control for control in self.controls if control.group == name)


def _identity_problem(value: object) -> str | None:
    try:
        parts = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        parts = None
    if not (isinstance(parts, list) and len(parts) == 3 and all(isinstance(part, str) for part in parts)):
        return "identity 须是 [source, source_id, language] 的 JSON 文本"
    source, source_id, language = parts
    if source != WORKBENCH_SOURCE or not _SOURCE_ID.fullmatch(source_id) or not _LANGUAGE.fullmatch(language):
        return f"identity 须是工作台身份键：source 为 {WORKBENCH_SOURCE}，source_id 为 base64url，language 为语种码"
    return None


def _control_problems(entry: object) -> list[str]:
    if not isinstance(entry, Mapping):
        return ["对照须是 JSON 对象"]
    cid, group = entry.get("id"), entry.get("group")
    where = cid if isinstance(cid, str) else "?"
    problems = [] if isinstance(cid, str) and _CONTROL_ID.fullmatch(cid) else [f"{where}: id 须是小写字母、数字与连字符"]
    if group not in KINDS:
        return [*problems, f"{where}: group 须是 {', '.join(GROUPS)} 之一"]
    if entry.get("kind") not in KINDS[group]:
        problems.append(f"{where}: {group} 的 kind 须是 {', '.join(KINDS[group])} 之一")
    term, geo = entry.get("term"), entry.get("geo")
    try:
        TrendsQuery(terms=(term,), bare=term, geo=geo, granularity="H")
    except (TypeError, ValueError):
        problems.append(f"{where}: term 须是 1–200 个可打印字符，geo 须是 WW 或两位国家码")
    if group == "regional" and geo not in REGIONAL_GEOS:
        problems.append(f"{where}: 地区对照的 geo 须是 {', '.join(sorted(REGIONAL_GEOS))} 之一")
    related = entry.get("related", False)
    if type(related) is not bool or (related and group == "market") or (group == "seed" and not related):
        problems.append(f"{where}: related 是布尔值；市场序列不取相关查询，种子只取相关查询")
    has_identity = entry.get("identity") is not None
    if group in IDENTITY_GROUPS:
        problem = _identity_problem(entry.get("identity")) if has_identity else "缺少 identity"
        problems += [f"{where}: {problem}"] if problem else []
    elif has_identity:
        problems.append(f"{where}: {group} 不是剧，不带 identity")
    return problems


def _list_problems(controls: tuple[Control, ...]) -> list[str]:
    ids = [control.id for control in controls]
    problems = [f"id 重复：{', '.join(sorted({i for i in ids if ids.count(i) > 1}))}"] if len(set(ids)) != len(ids) else []
    positives = sum(control.group == "positive" for control in controls)
    low, high = POSITIVE_COUNT
    if not low <= positives <= high:
        problems.append(f"正对照须有 {low}–{high} 部，现有 {positives} 部")
    return problems


def _provenance_of(document: Mapping) -> Mapping[str, str]:
    provenance = document.get("provenance") or {}
    if not isinstance(provenance, Mapping) or not all(isinstance(k, str) and isinstance(v, str) for k, v in provenance.items()):
        raise ValueError("对照清单的 provenance 须是「键: 文字」的对象")
    return MappingProxyType(dict(provenance))


def parse_controls(document: object) -> Controls:
    """The control list, or ValueError naming every problem at once."""
    if not isinstance(document, Mapping) or document.get("format") != CONTROLS_FORMAT:
        raise ValueError(f"对照清单的 format 须是 {CONTROLS_FORMAT}")
    provenance = _provenance_of(document)
    entries = document.get("controls")
    if not isinstance(entries, list):
        raise ValueError("对照清单缺少 controls 列表")
    problems = [problem for entry in entries for problem in _control_problems(entry)]
    if problems:
        raise ValueError("对照清单不合格：" + "；".join(problems))
    fields = ("id", "group", "kind", "term", "geo")
    controls = tuple(Control(*(entry[name] for name in fields), related=entry.get("related", False), identity=entry.get("identity")) for entry in entries)
    problems = _list_problems(controls)
    if problems:
        raise ValueError("对照清单不合格：" + "；".join(problems))
    return Controls(built_at=str(document.get("built_at", "")), controls=controls, provenance=provenance)


def load_controls(path: Path) -> Controls:
    return parse_controls(json.loads(path.read_text(encoding="utf-8")))


# ---- the two-day plan ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Unit:
    """One query unit: explore, then multiline when `timeline`, then relatedsearches when `related`."""

    key: str
    control: str
    granularity: str
    timeline: bool
    related: bool
    method: str = "GET"
    repeat_of: str | None = None

    @property
    def http(self) -> int:
        return 1 + int(self.timeline) + int(self.related)

    def to_document(self) -> dict:
        return {
            "key": self.key,
            "control": self.control,
            "granularity": self.granularity,
            "timeline": self.timeline,
            "related": self.related,
            "method": self.method,
            "repeat_of": self.repeat_of,
        }


@dataclass(frozen=True)
class DayPlan:
    day: int
    units: tuple[Unit, ...]
    warmups: int = WARMUPS_PER_DAY

    @property
    def http(self) -> int:
        return self.warmups + sum(unit.http for unit in self.units)

    def to_document(self, controls: Controls) -> dict:
        """The task list for one day, with each unit's term and geo so it reads on its own."""
        by_id = controls.by_id()
        units = [{**unit.to_document(), "term": by_id[unit.control].term, "geo": by_id[unit.control].geo, "http": unit.http} for unit in self.units]
        return {"format": PLAN_FORMAT, "day": self.day, "warmups": self.warmups, "http": self.http, "units": units}

    @classmethod
    def from_document(cls, document: object) -> "DayPlan":
        if not isinstance(document, Mapping) or document.get("format") != PLAN_FORMAT or document.get("day") not in DAYS:
            raise ValueError(f"任务清单的 format 须是 {PLAN_FORMAT}，day 须是 1 或 2")
        units = tuple(_unit_of(entry) for entry in document.get("units") or ())
        plan = cls(day=document["day"], units=units, warmups=document.get("warmups", WARMUPS_PER_DAY))
        if len({unit.key for unit in units}) != len(units) or plan.warmups != WARMUPS_PER_DAY:
            raise ValueError("任务清单的单元重复，或预热次数不对")
        if document.get("http") != plan.http or plan.http > MAX_HTTP_PER_DAY:
            raise ValueError(f"任务清单记的请求数与单元对不上，或超过每天 {MAX_HTTP_PER_DAY} 次")
        return plan


def _unit_of(entry: object) -> Unit:
    if not isinstance(entry, Mapping):
        raise ValueError("任务清单的单元须是 JSON 对象")
    unit = Unit(*(entry.get(name) for name in ("key", "control", "granularity", "timeline", "related", "method", "repeat_of")))
    valid = (
        isinstance(unit.key, str)
        and _UNIT_KEY.fullmatch(unit.key)
        and isinstance(unit.control, str)
        and unit.granularity in ("H", "D")
        and type(unit.timeline) is bool
        and type(unit.related) is bool
        and (unit.timeline or unit.related)
        and unit.method in ("GET", "POST")
        and (unit.repeat_of is None or isinstance(unit.repeat_of, str))
    )
    if not valid:
        raise ValueError("任务清单的单元字段不合格")
    return unit


def units_of(control: Control) -> tuple[Unit, ...]:
    """A seed asks only for related queries; every other control for its hourly and its daily series."""
    if control.group == "seed":
        return (Unit(f"{control.id}-r", control.id, "H", timeline=False, related=True),)
    hourly = Unit(f"{control.id}-h", control.id, "H", timeline=True, related=False)
    return (hourly, Unit(f"{control.id}-d", control.id, "D", timeline=True, related=control.related))


def _split_days(controls: Controls) -> Mapping[int, Mapping[str, tuple[Control, ...]]]:
    """Within each group, alternate controls between the days, so each day sees about half of every group."""
    return MappingProxyType({day: MappingProxyType({group: controls.group(group)[day - 1 :: 2] for group in GROUPS}) for day in DAYS})


def _interleaved(lists: Sequence[tuple[Control, ...]]) -> tuple[Control, ...]:
    longest = max((len(items) for items in lists), default=0)
    return tuple(items[k] for k in range(longest) for items in lists if k < len(items))


def _with_repeat(day: int, units: tuple[Unit, ...], first_positive: Control | None) -> tuple[Unit, ...]:
    """The day's first positive hourly series again, REPEAT_GAP_UNITS units later (design 4.11 "隔几分钟")."""
    if first_positive is None:
        return units
    original = units_of(first_positive)[0]
    at = min([unit.key for unit in units].index(original.key) + REPEAT_GAP_UNITS + 1, len(units))
    repeat = Unit(f"{original.key}-rep", original.control, "H", timeline=True, related=False, method=REPEAT_METHODS[day], repeat_of=original.key)
    return (*units[:at], repeat, *units[at:])


def _day_plan(day: int, groups: Mapping[str, tuple[Control, ...]]) -> DayPlan:
    ordered = _interleaved([groups[name] for name in INTERLEAVED]) + groups["market"]
    units = tuple(unit for control in ordered for unit in units_of(control))
    return DayPlan(day=day, units=_with_repeat(day, units, next(iter(groups["positive"]), None)))


def build_plan(controls: Controls, *, max_per_day: int = MAX_HTTP_PER_DAY, max_total: int = MAX_HTTP_TOTAL) -> tuple[DayPlan, DayPlan]:
    """The two days' task lists; ValueError when either day or both together would exceed the budget."""
    days = _split_days(controls)
    plans = tuple(_day_plan(day, days[day]) for day in DAYS)
    over = [f"第 {plan.day} 天 {plan.http} 次" for plan in plans if plan.http > max_per_day]
    total = sum(plan.http for plan in plans)
    if over or total > max_total:
        raise ValueError(f"任务清单超出预算（每天至多 {max_per_day} 次、两天至多 {max_total} 次）：{'、'.join(over) or f'合计 {total} 次'}")
    return plans


def check_plan_matches(plan: DayPlan, document: Mapping, controls: Controls) -> None:
    """Refuse a task list written against another version of the control list."""
    by_id = controls.by_id()
    for entry in document.get("units", ()):
        control = by_id.get(entry.get("control"))
        if control is None or (control.term, control.geo) != (entry.get("term"), entry.get("geo")):
            raise ValueError("对照清单在生成任务清单之后改过：重新运行 plan")


def main(argv: Sequence[str] | None = None) -> int:
    from ggwork_pick.observe.trends.stage0_cli import main as cli_main

    return cli_main(sys.argv[1:] if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
