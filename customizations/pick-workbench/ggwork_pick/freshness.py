"""Freshness facts for the selected batch, never a judgement of sync health.

Live model calls measure age now; historical notes recheck at result creation. Board age
always uses batch capture time. These display-only facts do not change stored snapshots.
"""

from datetime import UTC, datetime, timedelta

from ggwork_pick.contracts import PickConditions

# banner-rules.ts STALE_AFTER_MS and sourceFreshnessBanners; rank-view.tsx TheaterRank; request.ts DAILY_RANKS.
STALE_BATCH_HOURS = 14
STALE_SOURCE_HOURS = 36
STALE_DAILY_RANK_DAYS = 2
STALE_WEEKLY_RANK_DAYS = 14
DAILY_RANKS = ("kd", "qc", "qr")
WEEKLY_RANK = "kw"
RANK_KINDS = (*DAILY_RANKS, WEEKLY_RANK)


def parse_timestamp(value) -> datetime | None:
    """An ISO 8601 time as data_as_of carries them (Z or an offset); None for anything else."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)
    except (ValueError, OverflowError):
        return None


def _day(value) -> datetime | None:
    """A signal's observed_at, whose first ten characters are the board's day."""
    return parse_timestamp(value[:10]) if isinstance(value, str) and len(value) >= 10 else None


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _captured_at(data_as_of: dict) -> datetime | None:
    """When the data was captured: the version's as_of, or the batch's publish for a batch without one."""
    return parse_timestamp(data_as_of.get("source_as_of")) or parse_timestamp(data_as_of.get("published_at"))


def _batch_notice(captured: datetime | None, now: datetime, *, historical: bool) -> list[str]:
    if captured is None or now - captured <= timedelta(hours=STALE_BATCH_HOURS):
        return []
    scope = "查询时" if historical else "本轮"
    return [f"{scope}使用的剧库批次采集于 {_stamp(captured)}，资料已超过 {STALE_BATCH_HOURS} 小时，可能不是最新资料。"]


def _source_notice(data_as_of: dict, now: datetime, *, historical: bool) -> list[str]:
    freshness = data_as_of.get("freshness")
    imported = parse_timestamp(freshness.get("catalogImportedAt")) if isinstance(freshness, dict) else None
    if imported is None or now - imported <= timedelta(hours=STALE_SOURCE_HOURS):
        return []
    scope = "查询时" if historical else "本轮"
    return [
        f"{scope}使用的剧场剧单导入于 {_stamp(imported)}，已超过 {STALE_SOURCE_HOURS} 小时：新剧、下架状态与榜单信号可能滞后；工作台同步只复制上游已有数据。"
    ]


def _latest_board(rows: list[dict], kind: str) -> datetime | None:
    days = (_day(s.get("observed_at")) for row in rows for s in row.get("signals", ()) if s.get("kind") == kind)
    return max((day for day in days if day is not None), default=None)


def _board_kinds(rows: list[dict], conditions: PickConditions) -> list[str]:
    """The board kinds the question stands on: the one asked for, or under hot_only every one the batch holds."""
    if conditions.signal_kind in RANK_KINDS:
        return [conditions.signal_kind]
    if not conditions.hot_only:
        return []
    present = {s.get("kind") for row in rows for s in row.get("signals", ())}
    return [kind for kind in RANK_KINDS if kind in present]


def _board_notice(rows: list[dict], kind: str, reference: datetime) -> list[str]:
    latest = _latest_board(rows, kind)
    days = STALE_WEEKLY_RANK_DAYS if kind == WEEKLY_RANK else STALE_DAILY_RANK_DAYS
    if latest is None or reference - latest <= timedelta(days=days):
        return []
    edition = latest.strftime("%Y-%m-%d")
    return [f"{kind} 最新一期是 {edition}，采集时已超过 {days} 天：只能作历史榜参考，不能据此确认当前热门；镜像同步不会刷新上游榜单。"]


def data_notices(data_as_of: dict | None, rows: list[dict], conditions: PickConditions, *, now: datetime | None = None, historical: bool = False) -> list[str]:
    """Use creation time for historical notes, current time for model calls.

    A missing historical reference never falls back to today. Board age can still be
    established from capture time; without capture time it cannot be established.
    """
    unknown = ["无法核对查询时点，查询时的资料时效未知。"] if historical and now is None else []
    if not isinstance(data_as_of, dict):
        return unknown
    reference = now if historical else (now or datetime.now(UTC))
    captured = _captured_at(data_as_of)
    boards = [notice for kind in _board_kinds(rows, conditions) for notice in _board_notice(rows, kind, captured)] if captured else []
    if historical and reference is not None:
        boards = [f"查询时所用资料：{notice}" for notice in boards]
    ages = [*_batch_notice(captured, reference, historical=historical), *_source_notice(data_as_of, reference, historical=historical)] if reference else []
    return [*unknown, *ages, *boards]
