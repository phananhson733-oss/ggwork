"""The radar's rows in the pick data board's test database (plan TR-24): what the trends and search tabs read.

Built from the contract's own valid view rows (tests/fixtures/obs_contract/views.json and state_rows.json), each moved
into one small world, and written to the ggwp_obs_* tables; migration 0007's pick_obs views and the reader's grants
expose them. test_board_fixture.py reads every view row back as the reader and validates it with VIEW_ROW_MODELS, so the
world stays contract-valid. Every value is synthetic.

The world, one identity (IDENTITY) throughout:
  trends  T_LIVE (live, the one shown by default), T_SHADOW (newer, shadow), T_PRUNED (newest, live, pruned)
  gsc     G_LIVE (live, shown by default), G_SHADOW (newer, shadow)
  states  41 (T_LIVE, US rising), 907 (G_LIVE, USA surge), 908 (G_LIVE, GBR present, no label)
  links   71 for T_LIVE x G_LIVE, 72 for T_SHADOW x G_SHADOW
  discoveries of T_LIVE: 12 queue (the identity), 13 display_only (out of pool), 14 a_tier (the identity)
  runs    one per channel: a running trends session and a published gsc round
"""

import json
from pathlib import Path

from sqlalchemy import insert

HERE = Path(__file__).resolve()
FIXTURES = HERE.parents[1] / "fixtures" / "obs_contract"

T_LIVE = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6c"
T_SHADOW = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6d"
T_PRUNED = "7a1c0e9b5d3f4a2e8b6c1d0f9e8a7b6e"
G_LIVE = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c"
G_SHADOW = "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8d"
IDENTITY = '["realshort","UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ","en"]'
TRENDS_BATCH = "0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5e"
GSC_BATCH = "0b1c2d3e4f5a6b7c8d9e0f1a2b3c4d5f"
# The view column a table column is exposed as, where the names differ (migration 0007's VIEWS).
_RENAMED = {
    "sets": {"set_id": "id", "frozen_inputs": "frozen_inputs_json", "summary": "summary_json"},
    "states": {
        "row_id": "id",
        "labels": "labels_json",
        "flags": "flags_json",
        "metrics": "metrics_json",
        "quality_note": "quality_note_json",
        "paste_row": "paste_row_json",
    },
    "discoveries": {"discovery_id": "id"},
    "run_status": {"batch_id": "id", "status_codes": "status_codes_json"},
    "links": {},
}
# What the view hides and the table needs.
_TABLE_ONLY = {
    "sets": {"batch_id": "board-obs-batch"},
    "states": {},
    "discoveries": {"created_at": "2026-09-25T01:52:10.000000+00:00"},
    "run_status": {"collector_version": "obs-0.3.0"},
    "links": {},
}


def _view_row(name: str) -> dict:
    cases = json.loads((FIXTURES / "views.json").read_text(encoding="utf-8"))["valid"]
    return next(case["row"] for case in cases if case["name"] == name)


def _state_row(name: str) -> dict:
    cases = json.loads((FIXTURES / "state_rows.json").read_text(encoding="utf-8"))["valid"]
    return next(case["value"] for case in cases if case["name"] == name)


def _table_row(view: str, row: dict) -> dict:
    renamed = _RENAMED[view]
    return {**{renamed.get(key, key): value for key, value in row.items()}, **_TABLE_ONLY[view]}


def view_rows() -> dict[str, list[dict]]:
    """The world as the reader sees it, view by view."""
    trends, gsc = _view_row("sets_trends"), _view_row("sets_gsc")
    discovery = {**_view_row("discoveries_unique"), "matched_identity": IDENTITY}
    link = _view_row("links")
    return {
        "sets": [
            trends,
            {**trends, "set_id": T_SHADOW, "mode": "shadow", "published_at": "2026-09-25T05:10:00.000000+00:00"},
            {**trends, "set_id": T_PRUNED, "status": "pruned", "published_at": "2026-09-25T06:00:00.000000+00:00"},
            {**gsc, "mode": "live", "published_at": "2026-09-25T03:31:40.000000+00:00"},
            {**gsc, "set_id": G_SHADOW, "published_at": "2026-09-25T04:00:00.000000+00:00"},
        ],
        "states": [
            _view_row("states_trends"),
            _view_row("states_gsc"),
            {**_state_row("gsc_present_descriptive"), "row_id": 908, "scope": "GBR"},
        ],
        "links": [
            link,
            {**link, "id": 72, "trends_set_id": T_SHADOW, "gsc_set_id": G_SHADOW, "mode": "shadow"},
        ],
        "discoveries": [
            {**discovery, "route": "queue"},
            {
                **_view_row("discoveries_out_of_pool"),
                "discovery_id": 13,
                "term": "reelshort the hidden heiress",
                "normalized_term": "the hidden heiress",
            },
            {**discovery, "discovery_id": 14, "geo": "US"},
        ],
        "run_status": [
            {**_view_row("run_status_trends"), "batch_id": TRENDS_BATCH},
            {**_view_row("run_status_gsc"), "batch_id": GSC_BATCH, "mode": "live", "status_codes": []},
        ],
    }


def _normalized_title(row: dict) -> dict:
    """ggwp_obs_states keeps a Trends row's correspondence title, which the view does not show (D24)."""
    return {**row, "normalized_title": row["title"].lower() if row["channel"] == "trends" else None}


async def insert_obs(session_factory) -> dict[str, int]:
    """Write the world; returns how many rows each view shows."""
    from ggwork_pick.models import obs_batches, obs_discoveries, obs_links, obs_sets, obs_states

    world = view_rows()
    tables = (
        ("sets", obs_sets),
        ("states", obs_states),
        ("links", obs_links),
        ("discoveries", obs_discoveries),
        ("run_status", obs_batches),
    )
    async with session_factory() as session, session.begin():
        for view, table in tables:
            rows = [_table_row(view, row) for row in world[view]]
            if view == "states":
                rows = [_normalized_title(row) for row in rows]
            await session.execute(insert(table), rows)
    return {view: len(rows) for view, rows in world.items()}
