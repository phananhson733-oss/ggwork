"""The data page's stale warnings, for the model (2026-09-30).

The page warns when the mirror's latest version is over 14 hours old, when the catalog it copied was imported over
36 hours before, and on the rank tab when the board's latest edition was already days old at capture (2 for the daily
boards, 14 for the weekly one). A sync only copies what upstream has, so a successful sync says nothing about the
data being current. The tools had no such judgement, so an old edition read as the latest. data_notices returns the
same four judgements as sentences for the model; test_freshness pins the thresholds to the frontend source.

For the model only, like zero_diagnosis: not part of data_as_of (a fixed key set the frontend's strict schema
checks) and never stored with a result. Unreadable or missing times give no notice rather than an error.
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


def _moment(value) -> datetime | None:
    """An ISO 8601 time as data_as_of carries them (Z or an offset); None for anything else."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(UTC) if moment.tzinfo else moment.replace(tzinfo=UTC)


def _day(value) -> datetime | None:
    """A signal's observed_at, whose first ten characters are the board's day."""
    return _moment(value[:10]) if isinstance(value, str) and len(value) >= 10 else None


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _captured_at(data_as_of: dict) -> datetime | None:
    """When the data was captured: the version's as_of, or the batch's publish for a batch without one."""
    return _moment(data_as_of.get("source_as_of")) or _moment(data_as_of.get("published_at"))


def _batch_notice(captured: datetime | None, now: datetime) -> list[str]:
    if captured is None or now - captured <= timedelta(hours=STALE_BATCH_HOURS):
        return []
    return [f"剧库批次采集于 {_stamp(captured)}，已超过 {STALE_BATCH_HOURS} 小时没有新批次，同步可能停了；回答里说明数据可能不是最新的。"]


def _source_notice(data_as_of: dict, now: datetime) -> list[str]:
    freshness = data_as_of.get("freshness")
    imported = _moment(freshness.get("catalogImportedAt")) if isinstance(freshness, dict) else None
    if imported is None or now - imported <= timedelta(hours=STALE_SOURCE_HOURS):
        return []
    return [
        f"剧场剧单导入于 {_stamp(imported)}，已超过 {STALE_SOURCE_HOURS} 小时：新剧、下架状态与榜单信号可能滞后；"
        "工作台同步只复制上游已有数据，需要先刷新上游资料。"
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
    return [f"{kind} 最新一期是 {edition}，采集时已超过 {days} 天：只能作历史榜参考，不能说成当前热门或最新一期；镜像同步不会刷新上游榜单。"]


def data_notices(data_as_of: dict | None, rows: list[dict], conditions: PickConditions, *, now: datetime | None = None) -> list[str]:
    """The stale warnings the data page would show for this data and question, as sentences; [] when none apply.

    Batch and catalog age are measured against now, like the page's banners on the current version. Board age is
    measured against the capture, like the rank tab: a board that was current when captured is the batch notice's
    business, not the board's.
    """
    if not isinstance(data_as_of, dict):
        return []
    now = now or datetime.now(UTC)
    captured = _captured_at(data_as_of)
    boards = [notice for kind in _board_kinds(rows, conditions) for notice in _board_notice(rows, kind, captured or now)]
    return [*_batch_notice(captured, now), *_source_notice(data_as_of, now), *boards]
