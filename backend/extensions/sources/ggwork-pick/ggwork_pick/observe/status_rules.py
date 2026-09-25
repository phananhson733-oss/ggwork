"""Status codes to banners, with the ones that come from time (plan TR-10, D10; design 3.7, 4.10; contract section 13).

A channel's banners are every status code its latest run wrote, plus three computed here from an explicit now:
- stale_26h (Trends only): the current live set was published more than 26 hours ago. Exactly 26 hours is not stale,
  matching link actionability; no live set is "not ready yet" for the agent, not stale.
- run_missed: for Trends, from 02:30 UTC the due target date is today (before it, yesterday) and the latest run's
  target_date is earlier; for GSC, the latest round started more than 4 hours ago. A channel that never ran has none:
  before the cron exists that is the rollout, and the page shows last_run_at as empty.
- shadow_mode: the latest run publishes as shadow (the publish switch is off).

Only the latest run's codes count; a code from an older run never lingers after a newer run. So a condition that lasts
(disabled_7d, canary_terminated, parse_error, db_size_cap, legacy_snapshot_missing) must be written again by every run
row while it holds, including the row of a run that refuses to start: a refusal row without its code silently clears
the red banner. The collectors' writers (TR-13, TR-14, TR-21) owe that; the contract's run_status section does not say it
yet. Each code appears once, ordered by level (red, warn, info) and then by STATUS_CODES order.
tests/fixtures/obs_status_cases.json holds the cases, the levels and the texts; the data page's TS copy reproduces them.

Levels: red when nothing fresh is published or collection has stopped; warn when data is published with degraded
quality (descriptive-only rounds, signs of silent degradation); info for shadow mode, which is by design.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.contract import BANNER_LEVELS, CHANNELS, MODES, STATUS_CODES, Channel, Mode, StatusCode
from ggwork_pick.observe.contract_api import ObsBanner
from ggwork_pick.observe.instants import instant

TRENDS_STALE_AFTER = timedelta(hours=26)
TRENDS_DUE_AT = time(2, 30)  # UTC: the 02:00 publication target plus half an hour (design 6.3)
GSC_MISSED_AFTER = timedelta(hours=4)  # rounds every 3 hours, one hour of slack

STATUS_LEVELS = MappingProxyType(
    {
        "stale_26h": "red",
        "not_published_low_coverage": "red",
        "extinguished_today": "red",
        "disabled_7d": "red",
        "canary_terminated": "red",
        "usertype_changed": "warn",
        "all_zero_jump": "warn",
        "legacy_unmapped_2pct": "warn",
        "legacy_snapshot_missing": "red",
        "gsc_gap_exceeded": "warn",
        "gsc_unverifiable": "warn",
        "run_missed": "red",
        "shadow_mode": "info",
        "db_size_cap": "red",
        "parse_error": "red",
    }
)
STATUS_TEXT = MappingProxyType(
    {
        "stale_26h": "当前生效的 Trends 集合已超过 26 小时：带趋势条件的查询返回「数据陈旧」，不给加码建议",
        "not_published_low_coverage": "A 档覆盖率低于 80%，本批没有发布，上一个集合继续生效",
        "extinguished_today": "今天的直连采集已熄火（限流、验证页或同意墙），剩余单元未采",
        "disabled_7d": "7 天内熄火 3 次，直连已停用，人工重置后才恢复",
        "canary_terminated": "金丝雀已终止（验证码或熄火达到 2 次），不再运行",
        "usertype_changed": "Trends 返回的 userType 变了，会话可能被标成自动访问",
        "all_zero_jump": "全零序列的比例比前一天明显升高",
        "legacy_unmapped_2pct": "未映射的旧页点击超过旧页总点击的 2%，需要 RealShort 重跑旧页解析导出",
        "legacy_snapshot_missing": "没有可用的旧页解析快照，GSC 集合没有发布",
        "gsc_gap_exceeded": "GSC 明细与全站总量的缺口超过门槛，本轮只出描述性标签",
        "gsc_unverifiable": "GSC 全站总量取不到，覆盖无法核对，本轮只出描述性标签",
        "run_missed": "采集没有按时运行：02:30 UTC 仍没有当天的 Trends 批次，或 GSC 超过 4 小时没有新轮次",
        "shadow_mode": "发布开关关着：新集合只发成影子，智能体与联动不使用",
        "db_size_cap": "数据库容量达到上限，集合没有发布",
        "parse_error": "Trends 线上合同检查解析失败，接口可能改版，没有写入数值",
    }
)


def _check_channel(channel: str) -> None:
    if channel not in CHANNELS:
        raise ValueError(f"channel 只能是 trends 或 gsc：{channel!r}")


@dataclass(frozen=True, slots=True)
class LatestRun:
    """The fields of a channel's latest pick_obs.run_status row that decide its banners. Built directly or through
    from_mapping, it is checked the same way: a Trends run names its target date, a GSC round does not."""

    channel: Channel
    started_at: datetime
    mode: Mode
    target_date: date | None
    status_codes: tuple[StatusCode, ...]

    def __post_init__(self):
        _check_channel(self.channel)
        codes = self.status_codes
        problems = (
            (isinstance(self.started_at, datetime), "started_at 是带时区的 datetime"),
            (self.mode in MODES, f"mode 只能是 live 或 shadow：{self.mode!r}"),
            (self.target_date is None or type(self.target_date) is date, "target_date 是 date 或 None"),
            ((self.target_date is not None) == (self.channel == "trends"), "Trends 批次带 target_date，GSC 轮次不带"),
            (isinstance(codes, tuple), "status_codes 是元组"),
            (set(codes) <= set(STATUS_CODES), f"未知的 status code：{sorted(set(codes) - set(STATUS_CODES))}"),
        )
        problem = next((message for holds, message in problems if not holds), None)
        if problem is not None:
            raise ValueError(problem)
        instant(self.started_at)  # a naive moment is refused

    @classmethod
    def from_mapping(cls, channel: Channel, row: Mapping[str, Any]) -> "LatestRun":
        """A run_status row (or the fixture's run) of `channel`."""
        target = row["target_date"]
        return cls(channel, instant(row["started_at"]), row["mode"], None if target is None else date.fromisoformat(target), tuple(row["status_codes"]))


def trends_set_stale(live_published_at: str | datetime | None, now: datetime) -> bool:
    """The live Trends set is over 26 hours old; with no live set there is nothing to call stale."""
    return live_published_at is not None and instant(now) - instant(live_published_at) > TRENDS_STALE_AFTER


def trends_due_date(now: datetime) -> date:
    """The target date whose batch must exist by now: today from 02:30 UTC, yesterday before it."""
    moment = instant(now)
    due_at = datetime.combine(moment.date(), TRENDS_DUE_AT, UTC)
    return moment.date() if moment >= due_at else moment.date() - timedelta(days=1)


def _check_run(channel: Channel, latest_run: LatestRun | None) -> None:
    _check_channel(channel)
    if latest_run is not None and latest_run.channel != channel:
        raise ValueError(f"latest_run 属于 channel {latest_run.channel}，不是 {channel}")


def run_missed(channel: Channel, latest_run: LatestRun | None, now: datetime) -> bool:
    _check_run(channel, latest_run)
    if latest_run is None:
        return False
    if channel == "trends":
        return latest_run.target_date < trends_due_date(now)
    return instant(now) - latest_run.started_at > GSC_MISSED_AFTER


def channel_banners(channel: Channel, *, latest_run: LatestRun | None, live_published_at: str | datetime | None, now: datetime) -> tuple[ObsBanner, ...]:
    """One channel's banners at `now`: the latest run's codes and the three time-based ones, each once, in banner order."""
    _check_run(channel, latest_run)
    moment = instant(now)
    computed = {
        "stale_26h": channel == "trends" and trends_set_stale(live_published_at, moment),
        "run_missed": run_missed(channel, latest_run, moment),
        "shadow_mode": latest_run is not None and latest_run.mode == "shadow",
    }
    codes = {code for code, holds in computed.items() if holds} | set(() if latest_run is None else latest_run.status_codes)
    ordered = sorted(codes, key=lambda code: (BANNER_LEVELS.index(STATUS_LEVELS[code]), STATUS_CODES.index(code)))
    return tuple(ObsBanner(code=code, level=STATUS_LEVELS[code]) for code in ordered)
