"""Synthetic ggwp_obs_sets and ggwp_obs_batches rows for the /sync obs key's tests (TR-25): every NOT NULL column, and
nothing inside the JSON columns, which the status read never opens. Shared by tests/observe/test_sync_obs.py and the
frontend fixture in tests/mirror/test_mirror_sync_status.py."""

from collections.abc import Iterable

from sqlalchemy import insert

from ggwork_pick.models import obs_batches, obs_sets


def set_id(n: int) -> str:
    return f"{n:032x}"


def set_row(n: int, channel: str, mode: str, published_at: str, *, status: str = "published") -> dict:
    return {
        "id": set_id(n),
        "channel": channel,
        "mode": mode,
        "status": status,
        "batch_id": f"batch-{n}",
        "published_at": published_at,
        "as_of": published_at,
        "source_catalog_batch_id": "catalog-1",
        "collector_version": "obs-test",
        "rules_version": "gsc-rules-v1" if channel == "gsc" else "trend-rules-v1",
        "link_rules_version": "link-rules-v1",
        "alias_version": 1,
        "decisions_version": 0,
        "frozen_inputs_json": {},
        "summary_json": {},
    }


def batch_row(batch_id: str, channel: str, mode: str, started_at: str, *, target_date: str | None, codes: list[str], **table: object) -> dict:
    """A batch row; `table` sets the columns a table batch of the simplified radar has (collect_mode, window_end,
    finished_at, outcome, plan_json, summary_json)."""
    return {
        "id": batch_id,
        "channel": channel,
        "mode": mode,
        "target_date": target_date,
        "round_id": None if channel == "trends" else f"round-{batch_id}",
        "collector_version": "obs-test",
        "started_at": started_at,
        "outcome": "published",
        "status_codes_json": codes,
        **table,
    }


def table_batch_row(batch_id: str, target_date: str, started_at: str, *, finished_at: str | None, codes: list[str] | None = None, **extra: object) -> dict:
    """A stable-mode, planned batch: one night of the simplified radar's table."""
    window_end = f"{target_date[:8]}{int(target_date[8:]) - 1:02d}T00:00:00.000000+00:00"  # the eve's 00:00 UTC, as a 17:30 start has it
    outcome = "withheld" if finished_at is not None else "running"
    values = {"collect_mode": "stable", "window_end": window_end, "finished_at": finished_at, "outcome": outcome}
    return batch_row(batch_id, "trends", "shadow", started_at, target_date=target_date, codes=codes or [], **{**values, **extra})


async def insert_rows(session_factory, *, sets: Iterable[dict] = (), batches: Iterable[dict] = ()) -> None:
    sets, batches = list(sets), list(batches)
    async with session_factory() as session, session.begin():
        if sets:
            await session.execute(insert(obs_sets), sets)
        for batch in batches:  # one at a time: a table batch sets columns the others leave out
            await session.execute(insert(obs_batches).values(**batch))
