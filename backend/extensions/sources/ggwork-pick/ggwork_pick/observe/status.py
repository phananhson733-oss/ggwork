"""GET /api/pick/sync's obs key (plan TR-25, D10; design 3.7; contract section 13).

Every signed-in user reads it, so it carries only set ids, times, modes and fixed status codes. Per channel, from the 0007
tables (the gateway owns them; the pick_obs views are the data page's):
- live: the current live set, the latest published one with mode live (published_at, then id, breaks ties);
- latest: the latest published set of either mode, so a shadow-only rollout shows what it produced;
- last_run_at: when the channel's latest run (ggwp_obs_batches, by started_at, then id) started.
Pruned sets never count. For Trends, also the target dates of the newest finished batch and of the first batch of the
simplified radar's table (table_batch: the stable mode's planned batches; a refusal row has no window_end).
status_rules.channel_banners turns the latest run, the live set and those dates into banners at `now`.

Before the crons run every field is null and there is no banner: a channel that never ran is not "missed" (status_rules),
and nothing here stands for a count. The six values of both channels come from one statement, one snapshot, so a set
published meanwhile never pairs with the run before it.

A run row the rules cannot read (an unknown code, a Trends run without its target date) raises instead of being skipped:
skipping it would silently clear its red banner. routes answers {"error": <class>} for that, as for any failed read.
"""

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import select

from ggwork_pick.models import obs_batches, obs_sets
from ggwork_pick.observe.contract import CHANNELS
from ggwork_pick.observe.contract_api import ObsChannelStatus, ObsSyncStatus
from ggwork_pick.observe.instants import instant, stamp
from ggwork_pick.observe.status_rules import LatestRun, channel_banners
from ggwork_pick.repository import SHARED_OWNER, PickRepository

_SET_FIELDS = ("id", "published_at", "mode")
_RUN_FIELDS = ("started_at", "mode", "target_date", "status_codes_json")


def _latest_set(channel: str, column: str, *, live_only: bool):
    where = [obs_sets.c.channel == channel, obs_sets.c.status == "published"]
    if live_only:
        where.append(obs_sets.c.mode == "live")
    order = (obs_sets.c.published_at.desc(), obs_sets.c.id.desc())
    return select(obs_sets.c[column]).where(*where).order_by(*order).limit(1).scalar_subquery()


TABLE_MODE = "stable"  # the collect mode of the simplified radar's nights (trends/top_dramas.py)


def table_batch():
    """Where a batch is one of the simplified radar's table (trends-table and the stale banner read the same): Trends,
    the stable mode, planned (a refusal row has no window_end)."""
    return (obs_batches.c.channel == "trends") & (obs_batches.c.collect_mode == TABLE_MODE) & obs_batches.c.window_end.is_not(None)


def _table_through():
    """The target date of the newest finished table batch."""
    where = table_batch() & obs_batches.c.finished_at.is_not(None)
    return select(obs_batches.c.target_date).where(where).order_by(obs_batches.c.target_date.desc()).limit(1).scalar_subquery()


def _table_since():
    """The target date of the first table batch, finished or not: from when on a table is due."""
    return select(obs_batches.c.target_date).where(table_batch()).order_by(obs_batches.c.target_date.asc()).limit(1).scalar_subquery()


def _latest_run(channel: str, column: str):
    order = (obs_batches.c.started_at.desc(), obs_batches.c.id.desc())
    return select(obs_batches.c[column]).where(obs_batches.c.channel == channel).order_by(*order).limit(1).scalar_subquery()


def _statement():
    """One row: for each channel, the live set's id and time, the latest set's id, time and mode, and the latest run."""
    columns = []
    for channel in CHANNELS:
        columns += [_latest_set(channel, name, live_only=True).label(f"{channel}_live_{name}") for name in ("id", "published_at")]
        columns += [_latest_set(channel, name, live_only=False).label(f"{channel}_latest_{name}") for name in _SET_FIELDS]
        columns += [_latest_run(channel, name).label(f"{channel}_run_{name}") for name in _RUN_FIELDS]
    return select(*columns, _table_through().label("trends_table_through"), _table_since().label("trends_table_since"))


def _latest_run_of(channel: str, row: Mapping[str, Any]) -> LatestRun | None:
    started_at = row[f"{channel}_run_started_at"]
    if started_at is None:
        return None
    return LatestRun.from_mapping(
        channel,
        {
            "started_at": started_at,
            "mode": row[f"{channel}_run_mode"],
            "target_date": row[f"{channel}_run_target_date"],
            "status_codes": row[f"{channel}_run_status_codes_json"],
        },
    )


def banners_of(channel: str, row: Mapping[str, Any], now: datetime):
    """The channel's banners from one row of the statement."""
    through, since = (_day(row[f"trends_table_{name}"]) if channel == "trends" else None for name in ("through", "since"))
    live = row[f"{channel}_live_published_at"]
    return channel_banners(channel, latest_run=_latest_run_of(channel, row), live_published_at=live, now=now, table_through=through, table_since=since)


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


async def status_row(session, now: datetime) -> Mapping[str, Any]:
    """The statement's one row, read in `session` (the table's read shares it: one snapshot)."""
    instant(now)
    return (await session.execute(_statement())).mappings().one()


def _channel_status(channel: str, row: Mapping[str, Any], now: datetime) -> ObsChannelStatus:
    live_published_at = row[f"{channel}_live_published_at"]
    return ObsChannelStatus(
        channel=channel,
        live_set_id=row[f"{channel}_live_id"],
        live_published_at=live_published_at,
        latest_set_id=row[f"{channel}_latest_id"],
        latest_published_at=row[f"{channel}_latest_published_at"],
        latest_mode=row[f"{channel}_latest_mode"],
        last_run_at=row[f"{channel}_run_started_at"],
        banners=list(banners_of(channel, row, now)),
    )


async def obs_status(repo: PickRepository, *, now: datetime) -> dict:
    """The obs key for GET /api/pick/sync at `now` (timezone-aware). repo must be PickRepository.shared(...): the radar's
    status is the same for everyone, like the mirror's."""
    if repo.owner_id != SHARED_OWNER:
        raise ValueError("观测状态按共享数据计算：传 PickRepository.shared(...)")
    moment = instant(now)
    async with repo.session_factory() as session:
        row = await status_row(session, moment)
    status = ObsSyncStatus(checked_at=stamp(moment), channels=[_channel_status(channel, row, moment) for channel in CHANNELS])
    return status.model_dump(mode="json")
