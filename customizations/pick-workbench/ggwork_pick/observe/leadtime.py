"""eval-rules-v1: alert dedupe and the four lead-time metrics (plan TR-10, D33, D37; design 6.2).

Alerts (ggwp_obs_alerts) are deduplicated on D37's key: the root identity (followed back through the frozen alias
version), channel, state, geo or country, and mode. The same key within 14 days of the alert recorded for it is not
recorded again; mode is in the key, so a shadow alert never takes a live alert's window.

The metrics count every alert, not the ones that worked out (counterexample 13):
- in-pool lead time: within 14 days either side of the alert, the root identity's follow-up events that eval-rules-v1
  lists. An event already there before the alert is a lag (negative, the earliest); otherwise the first one after it;
- unfulfilled rate: of the alerts at least 14 days old, the share with no follow-up event. Younger alerts are listed
  as observing and are never counted as unfulfilled (counterexample 20);
- manual ignore rate: the share of alerts marked irrelevant on the data page;
- out-of-pool discovery delay: from a discovery's first sighting to its first unique match in the shared pool (D37);
  a title matching several pool rows is listed apart and never counts as entered.

Only live alerts published since the publish switch opened are evaluated, first and confirmed apart (per channel and
state). An event the alert's own channel observes does not fulfil it: a GSC from_zero alert is not its own follow-up.
Nothing is concluded before four weeks and 30 matured alerts. The event list is frozen by version: changing it means a
new eval-rules version next to this one.

Two parts of eval-rules-v1 are this module's derivation, written nowhere else yet (the contract has no milestone row
shape): the own-channel rule above (design 6.2 lists gsc_from_zero and gsc_rising as follow-ups without saying whose),
and the milestone event codes. The writers of milestones (TR-20, TR-23b) import them from here, MILESTONE_EVENTS or the
single names, and never spell the strings again: a code written differently is silently never counted.
"""

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Literal, Protocol

from ggwork_pick.observe.contract import ALERT_STATES, CHANNELS, Channel
from ggwork_pick.observe.contract_rows import AlertRow
from ggwork_pick.observe.instants import instant, whole_minutes

BASELINE_EVENT = "pre_existing"  # the launch-day row per identity: a starting state, never a follow-up
POOL_ENTRY = "pool_entry"
POOL_ENTRY_AMBIGUOUS = "pool_entry_ambiguous"
MILESTONE_TEXT = MappingProxyType(
    {
        BASELINE_EVENT: "上线前已存在",
        POOL_ENTRY: "池外发现首次唯一匹配进共享池",
        POOL_ENTRY_AMBIGUOUS: "池外发现入池时匹配到多行（单列，不进分子）",
    }
)

OutcomeKind = Literal["lead", "lag", "unfulfilled", "observing"]
Verdict = Literal["observing", "demote", "keep"]


@dataclass(frozen=True, slots=True)
class FollowUpKind:
    """A follow-up event; channel: the channel that observes it, whose own alerts it does not fulfil."""

    code: str
    text: str
    channel: Channel | None = None


@dataclass(frozen=True, slots=True)
class EvalRules:
    version: str
    events: tuple[FollowUpKind, ...]
    window: timedelta  # either side of an alert; an alert this old has matured
    dedupe_window: timedelta
    min_alerts: int
    review_after: timedelta  # since the publish switch opened

    def counted(self, channel: str) -> frozenset[str]:
        return frozenset(kind.code for kind in self.events if kind.channel != channel)


EVAL_RULES_V1 = EvalRules(
    version="eval-rules-v1",
    events=(
        FollowUpKind("candidate_pool", "进入 v1 候选池"),
        FollowUpKind("chart_first", "首次出现榜单证据"),
        FollowUpKind("gsc_from_zero", "GSC 越过「从零起量」", "gsc"),
        FollowUpKind("gsc_rising", "GSC 越过 7 天 rising", "gsc"),
        FollowUpKind("promoters_rise", "promoters_cnt 上升（镜像上线后）"),
        FollowUpKind("editorial_add", "进入编辑精选表（取自导入的精选历史，D33）"),
    ),
    window=timedelta(days=14),
    dedupe_window=timedelta(days=14),
    min_alerts=30,
    review_after=timedelta(weeks=4),
)
EVAL_RULES = MappingProxyType({EVAL_RULES_V1.version: EVAL_RULES_V1})
FOLLOW_UP_EVENTS = tuple(kind.code for kind in EVAL_RULES_V1.events)
# Every event code a milestone row may carry under eval-rules-v1: the follow-ups, the launch-day baseline and the two
# out-of-pool entry events.
MILESTONE_EVENTS = (*FOLLOW_UP_EVENTS, BASELINE_EVENT, POOL_ENTRY, POOL_ENTRY_AMBIGUOUS)


def eval_rules(version: str) -> EvalRules:
    found = EVAL_RULES.get(version)
    if found is None:
        raise LookupError(f"未登记的 eval-rules 版本：{version}")
    return found


# ---- identity and dedupe (D37) -------------------------------------------------------------------------------------


def root_identity(identity: str, predecessors: Mapping[str, str]) -> str:
    """The earliest identity `identity` descends from; predecessors maps a new identity to the old one it replaced, from
    the accepted aliases (auto, confirmed) of the frozen alias version. The alias rules refuse cycles; one is refused here too."""
    chain = (identity,)
    while (previous := predecessors.get(chain[-1])) is not None:
        if previous in chain:
            raise ValueError(f"别名成环：{' -> '.join((*chain, previous))}")
        chain = (*chain, previous)
    return chain[-1]


class Keyed(Protocol):
    dedupe_key: str
    published_at: str


def _latest_by_key(alerts: Sequence[Keyed]) -> dict[str, datetime]:
    keys = {alert.dedupe_key for alert in alerts}
    return {key: max(instant(alert.published_at) for alert in alerts if alert.dedupe_key == key) for key in keys}


def dedupe[K: Keyed](candidates: Iterable[K], recorded: Sequence[Keyed] = (), *, version: str) -> tuple[K, ...]:
    """The candidates to record, oldest first: a key within the dedupe window of the alert last recorded for it is dropped."""
    window = eval_rules(version).dedupe_window
    last = _latest_by_key(recorded)
    kept: tuple[K, ...] = ()
    for alert in sorted(candidates, key=lambda alert: instant(alert.published_at)):
        at, previous = instant(alert.published_at), last.get(alert.dedupe_key)
        if previous is None or at - previous >= window:
            kept, last = (*kept, alert), {**last, alert.dedupe_key: at}
    return kept


# ---- in-pool alerts ------------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FollowUp:
    """A milestone row as the metrics read it: the identity mapped to its root, the event code, when it happened."""

    root_identity: str
    event: str
    at: str


@dataclass(frozen=True, slots=True)
class AlertOutcome:
    alert_id: int
    published_at: str
    kind: OutcomeKind
    minutes: int | None
    event: str | None


@dataclass(frozen=True, slots=True)
class GroupMetrics:
    """One (channel, state): alerts evaluated, matured (the denominator), observing, and how the matured ones ended."""

    channel: str
    state: str
    alerts: int
    matured: int
    observing: int
    lead: int
    lag: int
    unfulfilled: int
    unfulfilled_rate: float | None
    median_minutes: float | None
    ignored: int
    ignored_rate: float | None
    outcomes: tuple[AlertOutcome, ...]


def _outcome(alert: AlertRow, follow_ups: Sequence[FollowUp], now: datetime, rules: EvalRules) -> AlertOutcome:
    at = instant(alert.published_at)
    if now - at < rules.window:
        return AlertOutcome(alert.id, alert.published_at, "observing", None, None)
    counted = rules.counted(alert.channel)
    near = sorted(
        (instant(follow.at), follow.event)
        for follow in follow_ups
        if follow.root_identity == alert.root_identity and follow.event in counted and abs(instant(follow.at) - at) <= rules.window
    )
    before = [item for item in near if item[0] < at]
    chosen = (before or near or [None])[0]
    if chosen is None:
        return AlertOutcome(alert.id, alert.published_at, "unfulfilled", None, None)
    kind = "lag" if chosen[0] < at else "lead"
    return AlertOutcome(alert.id, alert.published_at, kind, whole_minutes(chosen[0] - at), chosen[1])


def _rate(part: int, whole: int) -> float | None:
    return part / whole if whole else None


def _group(
    channel: str, state: str, alerts: tuple[AlertRow, ...], follow_ups: Sequence[FollowUp], now: datetime, rules: EvalRules, irrelevant_ids: frozenset[int]
) -> GroupMetrics:
    outcomes = tuple(_outcome(alert, follow_ups, now, rules) for alert in alerts)
    kinds = [outcome.kind for outcome in outcomes]
    resolved = [outcome.minutes for outcome in outcomes if outcome.kind in ("lead", "lag")]
    matured = len(outcomes) - kinds.count("observing")
    ignored = sum(1 for alert in alerts if alert.id in irrelevant_ids)
    return GroupMetrics(
        channel=channel,
        state=state,
        alerts=len(alerts),
        matured=matured,
        observing=kinds.count("observing"),
        lead=kinds.count("lead"),
        lag=kinds.count("lag"),
        unfulfilled=kinds.count("unfulfilled"),
        unfulfilled_rate=_rate(kinds.count("unfulfilled"), matured),
        median_minutes=float(statistics.median(resolved)) if resolved else None,
        ignored=ignored,
        ignored_rate=_rate(ignored, len(alerts)),
        outcomes=outcomes,
    )


def leadtime_metrics(
    alerts: Sequence[AlertRow],
    follow_ups: Sequence[FollowUp],
    *,
    now: datetime,
    since: datetime,
    version: str,
    irrelevant_ids: frozenset[int] = frozenset(),
) -> tuple[GroupMetrics, ...]:
    """The in-pool metrics per (channel, state) in contract order, for live alerts published from `since` (the moment
    the publish switch opened) as seen at `now`. The alerts are deduplicated again, so a repeated row never counts twice."""
    moment, start, rules = instant(now), instant(since), eval_rules(version)
    others = sorted({alert.eval_rules_version for alert in alerts} - {version})
    if others:
        raise ValueError(f"提示的评估口径是 {', '.join(others)}，不是 {version}")
    live = dedupe((alert for alert in alerts if alert.mode == "live" and instant(alert.published_at) >= start), version=version)
    groups = ((channel, state) for channel in CHANNELS for state in ALERT_STATES[channel])
    return tuple(
        _group(channel, state, members, tuple(follow_ups), moment, rules, irrelevant_ids)
        for channel, state in groups
        if (members := tuple(alert for alert in live if (alert.channel, alert.state) == (channel, state)))
    )


def verdict(metrics: GroupMetrics, *, now: datetime, since: datetime, unfulfilled_cap: float, version: str) -> Verdict:
    """Design 6.2's review: demote Trends to supporting evidence when the median lead is at or below zero or the unfulfilled
    rate is over the cap (the user's number, U10); nothing is concluded before four weeks and 30 matured alerts."""
    rules = eval_rules(version)
    if not 0 <= unfulfilled_cap <= 1:
        raise ValueError("unfulfilled_cap 是 0 到 1 之间的比例")
    if instant(now) - instant(since) < rules.review_after or metrics.matured < rules.min_alerts:
        return "observing"
    lagging = metrics.median_minutes is not None and metrics.median_minutes <= 0
    return "demote" if lagging or metrics.unfulfilled_rate > unfulfilled_cap else "keep"


# ---- out-of-pool discoveries (D37) ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Discovery:
    language: str
    normalized_title: str
    first_seen_at: str


@dataclass(frozen=True, slots=True)
class PoolEntry:
    language: str
    normalized_title: str
    event: str
    at: str

    def __post_init__(self):
        if self.event not in (POOL_ENTRY, POOL_ENTRY_AMBIGUOUS):
            raise ValueError(f"入池事件只能是 {POOL_ENTRY} 或 {POOL_ENTRY_AMBIGUOUS}：{self.event!r}")


@dataclass(frozen=True, slots=True)
class DiscoveryMetrics:
    total: int
    entered: int
    ambiguous: int
    pending: int
    entered_rate: float | None
    delays_minutes: tuple[int, ...]
    median_minutes: float | None


def _entry(key: tuple[str, str], seen: datetime, entries: Sequence[PoolEntry]) -> tuple[str, int | None]:
    """How one discovery ended: entered (with its delay), ambiguous, or pending. Only entries from its sighting on count."""
    after = [(instant(entry.at), entry.event) for entry in entries if (entry.language, entry.normalized_title) == key and instant(entry.at) >= seen]
    unique = sorted(at for at, event in after if event == POOL_ENTRY)
    if unique:
        return "entered", whole_minutes(unique[0] - seen)
    return ("ambiguous" if after else "pending"), None


def discovery_metrics(discoveries: Sequence[Discovery], entries: Sequence[PoolEntry]) -> DiscoveryMetrics:
    """Out-of-pool discovery delay, keyed by (language, normalized title); a title seen again counts from its first sighting."""
    keys = {(item.language, item.normalized_title) for item in discoveries}
    first_seen = {key: min(instant(item.first_seen_at) for item in discoveries if (item.language, item.normalized_title) == key) for key in keys}
    ends = [_entry(key, seen, entries) for key, seen in sorted(first_seen.items())]
    delays = tuple(sorted(minutes for kind, minutes in ends if kind == "entered"))
    kinds = [kind for kind, _ in ends]
    return DiscoveryMetrics(
        total=len(ends),
        entered=kinds.count("entered"),
        ambiguous=kinds.count("ambiguous"),
        pending=kinds.count("pending"),
        entered_rate=_rate(kinds.count("entered"), len(ends)),
        delays_minutes=delays,
        median_minutes=float(statistics.median(delays)) if delays else None,
    )
