"""Resolve explicit wall time in the plan's IANA zone, never the machine/browser zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.repository import stamp

IANA_ZONES = frozenset(available_timezones())


def plan_zone(name):
    try:
        if name not in IANA_ZONES:
            raise ValueError("not an IANA timezone")
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise QueryFailure("invalid_query", "时区无效，请使用 IANA 时区名称") from None


def scheduled_instant(row, zone, position):
    label = f"第{position + 1}行"
    if row.local_time is None:
        if row.fold is not None:
            raise QueryFailure("invalid_query", label + "尚未填写时间，不能指定重复时刻偏移")
        return None
    try:
        local = datetime.strptime(row.local_time, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise QueryFailure("invalid_query", label + "的日期或时间无效") from None
    choices = {}
    for fold in (0, 1):
        try:
            instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            back = instant.astimezone(zone)
        except (OverflowError, ValueError):
            raise QueryFailure("invalid_query", label + "转换后的时刻超出支持的日期范围") from None
        if back.replace(tzinfo=None) == local and back.fold == fold:
            choices[fold] = instant
    if not choices:
        raise QueryFailure("invalid_query", label + "的当地时间因时区变化而不存在，请换一个时间")
    if len(choices) > 1 and row.fold is None:
        raise QueryFailure("invalid_query", label + "的当地时间出现两次，请明确选择第一次或第二次 UTC 偏移")
    fold = 0 if row.fold is None else row.fold
    if fold not in choices:
        raise QueryFailure("invalid_query", label + "的重复时刻偏移与此当地时间不一致")
    return stamp(choices[fold])
