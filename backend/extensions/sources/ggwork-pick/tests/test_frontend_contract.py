"""The frontend parses /api/pick/results with strict zod schemas; keep one real payload as a shared fixture.

If this fails after a backend shape change, regenerate with
  PICK_WRITE_CONTRACT=1 uv run pytest tests/test_frontend_contract.py
and update frontend/src/core/pick/types.ts until frontend/tests/unit/core/pick/contract.test.ts passes.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

FIXTURE = Path(__file__).resolve().parents[3] / "frontend/tests/unit/core/pick/fixtures/backend-result.json"
# ggwp_candidate_sets columns from migration 0005 that never reach the result view.
NEW_COLUMNS = ("excluded_json", "mirror_version", "data_as_of_json")
# P4-1: data_as_of carries it beside DATA_AS_OF_KEYS only while PICK_EMIT_MIRROR_VERSION is "1". The fixture is written
# with the switch on (null here: SQLite never pairs a mirror); fixture_payload(emit_mirror_version=False) is the answer
# with it off. The frontend's contract test parses both.
MIRROR_VERSION = "mirror_version"


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


def fixture_payload(*, emit_mirror_version: bool) -> dict:
    """The fixture as /api/pick/results answers with the switch on (as written) or off (no mirror_version)."""
    payload = json.loads(FIXTURE.read_text())
    if emit_mirror_version:
        return payload
    return {**payload, "data_as_of": {key: value for key, value in payload["data_as_of"].items() if key != MIRROR_VERSION}}


@pytest.mark.asyncio
async def test_result_payload_matches_frontend_fixture(tmp_path):
    from engines import host_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick.repository import DATA_AS_OF_KEYS, PickRepository
    from ggwork_pick.selection import SelectionService, result_view
    from ggwork_pick.service import PickService, SyncSettings

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files", SyncSettings("https://realshort.test", TOKEN))
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    posted = {"matched": True, "records": ["SD-1"], "post_count": 2, "sched_count": 0, "last_post_on": "2026-09-10", "accounts": ["acc"]}
    by_other = {**posted, "records": ["SD-3"], "accounts": ["other"]}
    service.sync_transport = feed_transport([feed_row(1), feed_row(2, posted=posted), feed_row(3, posted=by_other)])
    await service.realshort_sync().run("cron")
    repo = PickRepository(service.session_factory, "alice")
    result = await SelectionService(repo).query({"signal_kind": "kd", "sort": "rank", "posted_account": "other"}, thread_id="t", run_id="r", call_id="c")
    record = await repo.result(result["id"])
    view = result_view(record)
    # The columns P2 added stay in the row: the strict pickResultSchema would refuse any new key (P2-8a).
    assert not set(NEW_COLUMNS) & set(view) and set(NEW_COLUMNS) <= set(record)
    # What status_view sends: the data_as_of the result froze, in the DATA_AS_OF_KEYS shape, and with the switch on
    # the version its row recorded after them (P4-1).
    off = {**view, "run_status": "success", "data_as_of": await repo.result_data_as_of(record, emit_mirror_version=False)}
    payload = {**view, "run_status": "success", "data_as_of": await repo.result_data_as_of(record, emit_mirror_version=True)}
    assert tuple(off["data_as_of"]) == DATA_AS_OF_KEYS and off["data_as_of"] == record["data_as_of_json"]
    assert payload["data_as_of"] == {**off["data_as_of"], MIRROR_VERSION: None}
    assert tuple(payload["data_as_of"]) == (*DATA_AS_OF_KEYS, MIRROR_VERSION)
    await engine.dispose()
    if os.environ.get("PICK_WRITE_CONTRACT"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    assert _shape(fixture_payload(emit_mirror_version=True)) == _shape(json.loads(json.dumps(payload)))
    assert _shape(fixture_payload(emit_mirror_version=False)) == _shape(json.loads(json.dumps(off)))


@pytest.mark.asyncio
@pytest.mark.parametrize("emit", [False, True], ids=["switch-off", "switch-on"])
async def test_results_route_payload_matches_frontend_fixture(app_client, emit):
    """The same result through GET /api/pick/results/{id}, on both dialects: status_view keeps the fixture's shape,
    with the P4-1 switch off (no mirror_version) and on (the row's, null for a v1-only sync)."""
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick.repository import DATA_AS_OF_KEYS, PickRepository
    from ggwork_pick.selection import SelectionService
    from ggwork_pick.service import SyncSettings

    client, service = app_client
    posted = {"matched": True, "records": ["SD-1"], "post_count": 2, "sched_count": 0, "last_post_on": "2026-09-10", "accounts": ["acc"]}
    by_other = {**posted, "records": ["SD-3"], "accounts": ["other"]}
    service.sync_settings = SyncSettings("https://realshort.test", TOKEN, mirror_version_flag="1" if emit else "")
    service.sync_transport = feed_transport([feed_row(1), feed_row(2, posted=posted), feed_row(3, posted=by_other)])
    await service.realshort_sync().run("cron")
    repo = PickRepository(service.session_factory, "alice")
    result = await SelectionService(repo).query({"signal_kind": "kd", "sort": "rank", "posted_account": "other"}, thread_id="t", run_id="r", call_id="c")
    payload = (await client.get(f"/api/pick/results/{result['id']}", headers={"test-owner": "alice"})).json()
    record = await repo.result(result["id"])
    assert not set(NEW_COLUMNS) & set(payload)
    extra = {MIRROR_VERSION: record[MIRROR_VERSION]} if emit else {}
    assert tuple(payload["data_as_of"]) == (*DATA_AS_OF_KEYS, *extra)
    assert payload["data_as_of"] == {**record["data_as_of_json"], **extra}
    assert _shape(fixture_payload(emit_mirror_version=emit)) == _shape(payload)
