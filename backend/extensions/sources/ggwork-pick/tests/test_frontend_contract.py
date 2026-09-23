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


def _shape(value):
    if isinstance(value, dict):
        return {key: _shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [_shape(value[0])] if value else []
    return type(value).__name__


@pytest.mark.asyncio
async def test_result_payload_matches_frontend_fixture(tmp_path):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from test_realshort_sync import TOKEN, feed_row, feed_transport

    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService, result_view
    from ggwork_pick.service import PickService, SyncSettings

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files", SyncSettings("https://realshort.test", TOKEN, "t"))
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    posted = {"matched": True, "records": ["SD-1"], "post_count": 2, "sched_count": 0, "last_post_on": "2026-09-10", "accounts": ["acc"]}
    service.sync_transport = feed_transport([feed_row(1), feed_row(2, posted=posted)])
    await service.realshort_sync().run("cron")
    repo = PickRepository(service.session_factory, "alice")
    result = await SelectionService(repo).query({"signal_kind": "kd", "sort": "rank", "posted_account": "other"}, thread_id="t", run_id="r", call_id="c")
    record = await repo.result(result["id"])
    info = await repo.batch_info(record["catalog_batch_id"])
    payload = {
        **result_view(record),
        "run_status": "success",
        "data_as_of": {key: info[key] for key in ("source_as_of", "published_at", "freshness", "scope", "shared")},
    }
    await engine.dispose()
    if os.environ.get("PICK_WRITE_CONTRACT"):
        FIXTURE.parent.mkdir(parents=True, exist_ok=True)
        FIXTURE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    assert _shape(json.loads(FIXTURE.read_text())) == _shape(json.loads(json.dumps(payload)))
