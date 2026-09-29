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


def batch_row(batch_id: str, channel: str, mode: str, started_at: str, *, target_date: str | None, codes: list[str]) -> dict:
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
    }


async def insert_rows(session_factory, *, sets: Iterable[dict] = (), batches: Iterable[dict] = ()) -> None:
    sets, batches = list(sets), list(batches)
    async with session_factory() as session, session.begin():
        if sets:
            await session.execute(insert(obs_sets), sets)
        if batches:
            await session.execute(insert(obs_batches), batches)
