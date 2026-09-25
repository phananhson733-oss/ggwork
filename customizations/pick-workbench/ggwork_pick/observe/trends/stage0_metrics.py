"""Stage 0's measurements: visibility, the two gates, the granularity, the section 8 routes, and the manual shape
comparison (plan TR-05, section 8; design 4.9, 4.11). Pure functions over the result lines the day runner writes.

Premise 1 holds here too: a unit whose request failed is unobserved. It is never counted as a series of zeros, never as
"not visible", and it stays out of every rate's denominator; the report lists it on its own. So is an answer the parser
judged but that lacks the bare title's line (line_missing). A series with fewer complete points than the window is
counted on what came back and flagged (short), since design 4.9 itself reads today 1-m as 30 points less the partial.

Visibility (design 4.11):
- hourly: non-zero hours among the last 144 complete hours (the partial point left out) >= 12, the H rule's own floor:
  four baseline blocks of at least 3 non-zero hours each (design 4.9 rule 3);
- daily: non-zero days among the last 30 complete days >= N. DEFAULT_N = 12 mirrors the hourly floor (four 7-day blocks
  of at least 3 non-zero days); the report prints how the verdicts move for other N, for G2 to settle.

Gate A passes when, for one granularity, at least half of the observed exact-title positives are visible (a
query_mismatch positive is listed apart: the plan's positives are exact-title queries) and the estimated number of
non-generic judgements a day reaches 5: A_TIER_DRAMAS times the share of visible regional controls, the recently listed
dramas that stand in for the watch list (design 4.6 rule 2). That estimate is loose, and the report says so for G2: one
visible regional control in twelve already makes 5; generic titles are not taken out; and "visible" (rule 2's
sufficiency) stands in for "judged". Beside it the report prints a strict estimate, the share of regional controls whose
blocks could carry the rising rule at all (B1-B4, or W1-W3, each with at least 3 non-zero points, design 4.9 rule 3).

Gate B asks whether a direct session is stable and gets related queries and a readable userType (design 4.11). It
fails when a session was put out by the breaker or had units the breaker skipped, when a day that asked got usable
related queries for fewer than 80% of its units, when seeds went out and none came back with lists, or when an answered
explore lacked a userType. It is undecided while a day that ran sent no related query or no seed, or no explore was
answered; a day that has not run leaves it provisional (final is False), and the report gives no route until it is final.
"""

import csv
import io
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType

from ggwork_pick.observe.trends.budget import SKIPPED_BREAKER

HOURS_WINDOW = 144
HOURS_VISIBLE = 12
DAYS_WINDOW = 30
DEFAULT_N = 12
N_CANDIDATES = (6, 9, 12, 15, 20)
POSITIVE_VISIBLE_RATE = 0.5
DAILY_JUDGED_MIN = 5
A_TIER_DRAMAS = 60  # design 4.5: A tier's 110-120 units are about 60-70 dramas (settle_granularity halves it for H+D)
MIN_POSITIVES_OBSERVED = 8  # below this, gate A is not decided
MIN_REGIONAL_OBSERVED = 4
RELATED_USABLE_RATE = 0.8
H_PLUS_D_FLOOR = 0.25  # H fails but still sees this share of positives: keep it as H+D's hourly description
SHAPE_CONSISTENT_RHO = 0.6
JUDGEABLE = frozenset({"ok", "ok_zero"})
LINE_MISSING = "line_missing"
EXACT_KINDS = frozenset({"exact_title"})
BLOCKS = MappingProxyType({"H": (24, 6, 4), "D": (7, 4, 3)})  # block size, blocks in the window, baseline blocks
BLOCK_FLOOR = 3  # design 4.9 rule 3: each baseline block needs at least 3 non-zero points
EXTINGUISH_NAMES = MappingProxyType({"wall": "验证码/同意页", "rate_limited": "429 满 5 次", "trips": "跳闸 3 次", "probe_failures": "试探连续失败 3 次"})
STEP_SECONDS = MappingProxyType({"H": 3600, "D": 86400})
LABEL_FORMATS = MappingProxyType({"H": "%Y-%m-%dT%H", "D": "%Y-%m-%d"})
# The first column's label in an English or a Chinese browser; the Chinese ones are to be checked against U5's files.
MANUAL_HEADERS = MappingProxyType({"time": "H", "day": "D", "时间": "H", "天": "D", "日": "D", "日期": "D"})
DAY_NAMES = MappingProxyType({1: "第一天", 2: "第二天"})
_MANUAL_TERM = re.compile(r"^(?P<term>.+?)\s*[:：]\s*[(（](?P<geo>.+)[)）]$")


# ---- series --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """One unit's bare line as the runner stored it; values empty when the unit failed (status says how)."""

    unit: str
    control: str
    group: str
    kind: str
    geo: str
    term: str
    granularity: str
    day: int
    requested_at: datetime | None  # None: the unit never sent
    status: str
    times: tuple[int, ...] = ()
    values: tuple[int, ...] = ()
    partial: tuple[bool | None, ...] = ()
    repeat_of: str | None = None
    method: str = "GET"

    @property
    def observed(self) -> bool:
        return self.status in JUDGEABLE

    def complete(self) -> tuple[tuple[int, int], ...]:
        """(time, value) of every point not marked partial, in time order."""
        return tuple((time, value) for time, value, partial in zip(self.times, self.values, self.partial) if partial is not True)


def _line_of(line: Mapping, term: str) -> Mapping | None:
    lines = (line.get("timeline") or {}).get("lines") or ()
    return next((entry for entry in lines if entry.get("term") == term), None)


def series_of(line: Mapping) -> Series | None:
    """The bare line of a unit that asked for a timeline; None for a seed, which asks for related queries only. A unit
    stopped before it sent (cap, breaker, deadline) or whose explore failed comes back unobserved, its status or skip
    reason kept."""
    if line.get("group") == "seed":
        return None
    timeline = line.get("timeline") or {}
    requested = line.get("requested_at")
    base = Series(
        unit=line["unit"],
        control=line["control"],
        group=line["group"],
        kind=line["kind"],
        geo=line["geo"],
        term=line["term"],
        granularity=line["granularity"],
        day=line["day"],
        requested_at=datetime.fromisoformat(requested) if requested else None,
        status=timeline.get("status") or line.get("status") or line.get("reason") or "not_run",
        repeat_of=line.get("repeat_of"),
        method=line.get("method", "GET"),
    )
    if base.status not in JUDGEABLE:
        return base
    raw = _line_of(line, line["term"])
    if raw is None:
        return Series(**{**base.__dict__, "status": LINE_MISSING})
    return Series(**{**base.__dict__, "times": tuple(int(t) for t in raw["time"]), "values": tuple(raw["value"]), "partial": tuple(raw["isPartial"])})


# ---- visibility ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Visibility:
    nonzero: int | None  # None: unobserved
    window: int
    points: int  # complete points the answer had
    short: bool = False  # fewer complete points than the window asks for: counted on what came back


def _visibility(series: Series, window: int) -> Visibility:
    if not series.observed:
        return Visibility(nonzero=None, window=0, points=0)
    complete = series.complete()
    tail = complete[-window:]
    return Visibility(nonzero=sum(1 for _, value in tail if value > 0), window=len(tail), points=len(complete), short=len(tail) < window)


def nonzero_hours(series: Series) -> Visibility:
    return _visibility(series, HOURS_WINDOW)


def nonzero_days(series: Series) -> Visibility:
    return _visibility(series, DAYS_WINDOW)


def counted(series: Series) -> Visibility:
    return nonzero_hours(series) if series.granularity == "H" else nonzero_days(series)


def visible(series: Series, *, n: int = DEFAULT_N) -> bool | None:
    """Visible by its granularity's floor; None when unobserved (never False for a failure)."""
    found = counted(series)
    if found.nonzero is None:
        return None
    return found.nonzero >= (HOURS_VISIBLE if series.granularity == "H" else n)


def block_sufficient(series: Series) -> bool | None:
    """Could the rising rule judge this series at all: every baseline block (B1-B4 of the last 144 hours, W1-W3 of the
    last 28 days) with at least BLOCK_FLOOR non-zero points (design 4.9 rule 3). None when unobserved or too short."""
    size, count, baseline = BLOCKS[series.granularity]
    complete = series.complete() if series.observed else ()
    if len(complete) < size * count:
        return None
    window = complete[-size * count :]
    blocks = [window[k * size : (k + 1) * size] for k in range(baseline)]
    return all(sum(1 for _, value in block if value > 0) >= BLOCK_FLOOR for block in blocks)


# ---- gate A --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GroupRate:
    observed: int
    visible: int
    unobserved: int
    short: int = 0  # observed verdicts that rest on a short window
    sufficient: int = 0  # observed series whose blocks could carry the rising rule (block_sufficient)

    @property
    def rate(self) -> float | None:
        return self.visible / self.observed if self.observed else None

    @property
    def sufficient_rate(self) -> float | None:
        return self.sufficient / self.observed if self.observed else None


@dataclass(frozen=True)
class GateA:
    granularity: str
    positive: GroupRate  # exact-title positives only
    regional: GroupRate
    estimate_daily: int | None
    estimate_basis: str
    passed: bool | None  # None: too few observations to decide
    reasons: tuple[str, ...]
    mismatch: GroupRate = GroupRate(0, 0, 0)  # query_mismatch positives, listed apart
    strict_estimate: int | None = None  # the same estimate by block sufficiency instead of visibility; shown, never gating


def group_rate(series_list: Iterable[Series], *, group: str, granularity: str, n: int, kinds: frozenset[str] | None = None) -> GroupRate:
    chosen = [s for s in series_list if s.group == group and s.granularity == granularity and s.repeat_of is None and (kinds is None or s.kind in kinds)]
    verdicts = [(visible(s, n=n), s) for s in chosen]
    seen = [s for v, s in verdicts if v is not None]
    return GroupRate(
        observed=len(seen),
        visible=sum(v is True for v, _ in verdicts),
        unobserved=len(chosen) - len(seen),
        short=sum(counted(s).short for s in seen),
        sufficient=sum(block_sufficient(s) is True for s in seen),
    )


def _estimate(positive: GroupRate, regional: GroupRate, a_tier_dramas: int, *, strict: bool = False) -> tuple[int | None, str]:
    def share(rate: GroupRate) -> float:
        return rate.sufficient_rate if strict else rate.rate

    if regional.observed >= MIN_REGIONAL_OBSERVED:
        return round(a_tier_dramas * share(regional)), "regional"
    pooled = GroupRate(positive.observed + regional.observed, positive.visible + regional.visible, 0, sufficient=positive.sufficient + regional.sufficient)
    return (round(a_tier_dramas * share(pooled)), "positive+regional") if pooled.observed else (None, "none")


def gate_a(
    series_list: Sequence[Series],
    *,
    granularity: str,
    n: int = DEFAULT_N,
    a_tier_dramas: int = A_TIER_DRAMAS,
    min_positives: int = MIN_POSITIVES_OBSERVED,
) -> GateA:
    """Gate A for one granularity; `min_positives` is halved by the interim report, which holds one day's half."""
    positive = group_rate(series_list, group="positive", granularity=granularity, n=n, kinds=EXACT_KINDS)
    mismatch = group_rate(series_list, group="positive", granularity=granularity, n=n, kinds=frozenset({"query_mismatch"}))
    regional = group_rate(series_list, group="regional", granularity=granularity, n=n)
    estimate, basis = _estimate(positive, regional, a_tier_dramas)
    strict, _ = _estimate(positive, regional, a_tier_dramas, strict=True)
    reasons = []
    if positive.observed < min_positives:
        reasons.append(f"正对照只观测到 {positive.observed} 部（至少 {min_positives} 部才判定）")
        return GateA(granularity, positive, regional, estimate, basis, None, tuple(reasons), mismatch, strict)
    if positive.rate < POSITIVE_VISIBLE_RATE:
        reasons.append(f"正对照可见率 {positive.visible}/{positive.observed} 低于 {POSITIVE_VISIBLE_RATE:.0%}")
    if estimate is None or estimate < DAILY_JUDGED_MIN:
        reasons.append(f"估算每天非泛词判定 {estimate} 条，低于 {DAILY_JUDGED_MIN} 条")
    return GateA(granularity, positive, regional, estimate, basis, not reasons, tuple(reasons), mismatch, strict)


def choose_granularity(gate_h: GateA, gate_d: GateA) -> str | None:
    """H when hourly passes; H+D when only daily passes but hourly still sees H_PLUS_D_FLOOR of the positives (D judges,
    H describes, design 4.9); D when only daily passes; None when neither does."""
    if gate_h.passed:
        return "H"
    if not gate_d.passed:
        return None
    hourly_rate = gate_h.positive.rate
    return "H+D" if hourly_rate is not None and hourly_rate >= H_PLUS_D_FLOOR else "D"


# ---- gate B --------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DayRelated:
    attempts: int  # units that asked for related queries and sent (their related part has an outcome)
    usable: int
    seeds_ok: int
    ran: bool = True  # the day has results or a session
    seeds_asked: int = 0
    unstable: tuple[str, ...] = ()  # why the day's sessions were not stable

    @property
    def rate(self) -> float | None:
        return self.usable / self.attempts if self.attempts else None


@dataclass(frozen=True)
class GateB:
    per_day: Mapping[int, DayRelated]
    user_types: Mapping[str, int]
    explores_without_user_type: int
    widget_missing: int
    breakout_seen: bool
    passed: bool | None
    final: bool  # every day has run; until then the verdict is provisional and no route is given
    reasons: tuple[str, ...]
    pending: tuple[str, ...]


def _usable(related: Mapping | None) -> bool:
    """An answer the channel can use: lists came back, or empty lists from a widget that was offered."""
    return related is not None and (related.get("status") == "ok" or (related.get("status") == "no_data" and not related.get("widget_missing")))


def _unstable(day: int, lines: Sequence[Mapping], sessions: Sequence[Mapping]) -> tuple[str, ...]:
    """A session the breaker put out, or one with units it skipped, was not stable (design 4.3's limit signals); a
    skipped unit still on file counts even when its session left no record."""
    name = DAY_NAMES[day]
    put_out = tuple(
        f"{name}第 {k} 次会话熄火（{EXTINGUISH_NAMES.get(reason, reason)}）"
        for k, reason in enumerate(((s.get("breaker") or {}).get("extinguished") for s in sessions), 1)
        if reason
    )
    skipped = tuple(
        f"{name}第 {k} 次会话有 {count} 个单元被熔断跳过"
        for k, count in enumerate((sum(1 for _, why in s.get("uncovered") or () if why == SKIPPED_BREAKER) for s in sessions), 1)
        if count
    )
    left = sum(1 for line in lines if line.get("reason") == SKIPPED_BREAKER)
    return (*put_out, *skipped) or ((f"{name}有 {left} 个单元被熔断跳过",) if left else ())


def _day_related(day: int, lines: Sequence[Mapping], sessions: Sequence[Mapping]) -> DayRelated:
    asked = [line for line in lines if line.get("related") is not None]
    seeds = [line for line in asked if line.get("group") == "seed"]
    return DayRelated(
        attempts=len(asked),
        usable=sum(_usable(line["related"]) for line in asked),
        seeds_ok=sum(1 for line in seeds if line["related"].get("status") == "ok"),
        ran=bool(lines) or bool(sessions),
        seeds_asked=len(seeds),
        unstable=_unstable(day, lines, sessions),
    )


def _day_findings(day: int, stats: DayRelated) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(failures, undecided) for a day that ran."""
    name = DAY_NAMES[day]
    if not stats.attempts:
        return stats.unstable, (f"{name}跑过，但要相关查询的单元一个都没发出去",)
    low = (f"{name}相关查询可用 {stats.usable}/{stats.attempts}，低于 {RELATED_USABLE_RATE:.0%}",) if stats.rate < RELATED_USABLE_RATE else ()
    seedless = (f"{name}没有一个种子拿到相关查询列表",) if stats.seeds_asked and not stats.seeds_ok else ()
    return (*stats.unstable, *low, *seedless), ((f"{name}没有种子发出去",) if not stats.seeds_asked else ())


def gate_b(lines: Sequence[Mapping], *, days: Sequence[int] = (1, 2), sessions: Mapping[int, Sequence[Mapping]] | None = None) -> GateB:
    """Gate B over the result lines and each day's session records (meta.json); see the module notes for the rule."""
    sessions = sessions or {}
    per_day = {day: _day_related(day, [line for line in lines if line.get("day") == day], tuple(sessions.get(day) or ())) for day in days}
    ran = {day: stats for day, stats in per_day.items() if stats.ran}
    findings = [_day_findings(day, stats) for day, stats in ran.items()]
    answered = [line for line in lines if line.get("status") in JUDGEABLE | {"no_data"}]
    types = Counter(line["user_type"] for line in answered if line.get("user_type"))
    missing_type = sum(1 for line in answered if not line.get("user_type"))
    failures = (*(f for found, _ in findings for f in found), *((f"{missing_type} 个有回答的 explore 没带 userType",) if missing_type else ()))
    no_answer = ("没有一个 explore 有回答，userType 无从判断",) if ran and not answered else ()
    undecided = (*(u for _, found in findings for u in found), *no_answer)
    pending = tuple(f"{DAY_NAMES[day]}的会话还没跑（闸门 B 要两次会话都稳定）" for day in days if day not in ran)
    passed = False if failures else (None if undecided or not ran else True)
    related = [line["related"] for line in lines if line.get("related") is not None]
    breakout = any(item.get("formattedValue") == "Breakout" for entry in related for item in (entry.get("rising") or ()))
    widget_missing = sum(1 for entry in related if entry.get("widget_missing"))
    return GateB(
        MappingProxyType(per_day), MappingProxyType(dict(types)), missing_type, widget_missing, breakout, passed, not pending, (*failures, *undecided), pending
    )


# ---- the four routes (plan section 8) ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Route:
    key: str
    title: str
    trends: tuple[str, ...]
    gsc: str
    switches: tuple[str, ...]
    person_days: str


ROUTES = MappingProxyType(
    {
        (True, True): Route(
            "both",
            "A、B 都过",
            ("Trends 侧任务全部照做",),
            "照做",
            ("S12a（GSC）与 S12b（Trends）各自按第 10 节的门槛",),
            "0",
        ),
        (True, False): Route(
            "a_only",
            "只过 A",
            (
                "TR-19 取消（不做发现段）",
                "TR-18 的歧义第三道输出 manual_required，由资料页歧义队列与 ambiguity_override 处理",
                "金丝雀与稳定期不混 relatedsearches",
            ),
            "照做",
            ("S12b 门槛不变",),
            "−1.5",
        ),
        (False, True): Route(
            "b_only",
            "只过 B",
            (
                "逐剧轮询降为旁证：Trends 采集只跑种子、发现队列与对照序列，不跑 A/B 清单",
                "TR-17 取消",
                "TR-18 只做发现命中所需的歧义与别名部分（−1.0）",
                "TR-19 照做",
                "智能体侧 trend_state、trend_include_first、trend_include_presumed 不开放，link_state 只剩 site_only",
                "资料页 trends tab 只显示发现队列",
            ),
            "照做；腾出的约 2 人日投 GSC 阈值回标（TR-31 加量）",
            ("S12b 门槛改为「金丝雀阶段 2 达标 + 发现段影子抽检」",),
            "−2.0（TR-17 −1.0、TR-18 −1.0），GSC +2.0",
        ),
        (False, False): Route(
            "neither",
            "都不过",
            (
                "Trends 采集不上线：取消 TR-17、TR-19",
                "TR-20 只保留通用 store.py（−0.5）；TR-18 只保留别名（−1.0）",
                "S5–S7、S10、TR-30 不做",
                "资料页 trends tab 只显示「未上线，保留人工对照入口」的说明",
                "智能体侧不开放任何 trend_* 字段",
            ),
            "照做",
            ("只有 S12a；S12b 不执行",),
            "约 −5.5",
        ),
    }
)


def route_for(gate_a_passed: bool | None, gate_b_passed: bool | None) -> Route | None:
    """Plan section 8's row for the two verdicts; None while either gate is undecided."""
    if gate_a_passed is None or gate_b_passed is None:
        return None
    return ROUTES[(bool(gate_a_passed), bool(gate_b_passed))]


# ---- the manual comparison (U5) ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ManualSeries:
    term: str
    geo_label: str
    granularity: str
    points: tuple[tuple[str, float], ...]  # (time label as the export wrote it, value; "<1" is 0.5)


def _manual_value(text: str) -> float:
    text = text.strip()
    return 0.5 if text == "<1" else float(int(text))


def parse_manual_csv(text: str) -> ManualSeries:
    """A browser export of Trends' interest over time: a category line, a blank line, `Time|Day,<term>: (<geo>)`, rows.
    A Chinese browser writes 时间 or 天 and full-width punctuation; both are read."""
    rows = [row for row in csv.reader(io.StringIO(text)) if row]
    header = next((k for k, row in enumerate(rows) if row[0].strip().lower() in MANUAL_HEADERS and len(row) == 2), None)
    if header is None:
        raise ValueError("不是 Trends 导出的走势 CSV：找不到 Time 或 Day 表头")
    named = _MANUAL_TERM.match(rows[header][1].strip())
    try:
        points = tuple((row[0].strip(), _manual_value(row[1])) for row in rows[header + 1 :])
    except (IndexError, ValueError):
        raise ValueError("走势 CSV 的数据行不合格") from None
    if not points or named is None:
        raise ValueError("走势 CSV 没有数据行，或表头不是「词: (地区)」")
    return ManualSeries(named["term"], named["geo"], MANUAL_HEADERS[rows[header][0].strip().lower()], points)


def _ranks(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda k: values[k])
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for k in order[start : end + 1]:
            ranks[k] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Spearman's rho with average ranks for ties; None when there are fewer than two points or a side is constant."""
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sx = math.sqrt(sum((r - mx) ** 2 for r in rx))
    sy = math.sqrt(sum((r - my) ** 2 for r in ry))
    if sx == 0 or sy == 0:
        return None
    return sum((a - mx) * (b - my) for a, b in zip(rx, ry)) / (sx * sy)


@dataclass(frozen=True)
class ShapeComparison:
    term: str
    granularity: str
    n: int
    rho: float | None
    verdict: str  # 一致 / 不一致 / 无法比较


def shape_compare(manual: ManualSeries, series: Series, *, utc_offset_minutes: int) -> ShapeComparison:
    """Align the export's labels (the browser's clock, `utc_offset_minutes` from UTC) with the scraped points' times,
    then rank-correlate the common points. Partial points are left out on the scraped side."""
    offset, fmt = timedelta(minutes=utc_offset_minutes), LABEL_FORMATS[series.granularity]
    scraped = {(datetime.fromtimestamp(time, UTC) + offset).strftime(fmt): value for time, value in series.complete()}
    common = [(value, scraped[label]) for label, value in manual.points if label in scraped]
    rho = spearman([m for m, _ in common], [s for _, s in common]) if manual.granularity == series.granularity else None
    verdict = "无法比较" if rho is None else ("一致" if rho >= SHAPE_CONSISTENT_RHO else "不一致")
    return ShapeComparison(manual.term, series.granularity, len(common) if rho is not None else 0, rho, verdict)
