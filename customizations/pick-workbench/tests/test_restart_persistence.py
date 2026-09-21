"""Persistence acceptance uses a new engine and service, not an in-memory fixture."""

import hashlib

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository
from ggwork_pick.selection import SelectionService
from ggwork_pick.service import PickService


@pytest.mark.asyncio
async def test_restart_preserves_candidates_sources_notes_and_command_receipts(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'pick.db'}"
    engine = create_async_engine(url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    importer = Importer(repo, service.data_dir)
    catalog = b'[{"source":"synthetic","source_id":"one","title":"Synthetic Example","language":"en"}]'
    batch = await importer.catalog(catalog, "json")
    knowledge = await importer.knowledge_bundle([(b"# Rules\nSynthetic evidence only.", "rules.md", "fixture:rules")])
    original = await SelectionService(repo).query({}, thread_id="thread", run_id="run", call_id="call")
    item_id = original["items"][0]["item_id"]
    receipt = await repo.save_selection("save-1", original["id"], [item_id], "next week")
    selection = (await repo.selections())[0]
    await repo.update_selection(selection["id"], "note-1", selection["version"], note="reviewed")
    await service.stop()
    await engine.dispose()

    second = create_async_engine(url)
    restarted = PickService(tmp_path / "files")
    try:
        await restarted.initialize(async_sessionmaker(second, expire_on_commit=False))
        restored = PickRepository(restarted.session_factory, "alice")
        assert await SelectionService(restored).query({}, thread_id="thread", run_id="run", call_id="call") == original
        assert await restored.command_receipt("save-1") == receipt
        assert await restored.save_selection("save-1", original["id"], [item_id], "next week") == receipt
        assert (await restored.selections())[0]["note"] == "reviewed"
        assert (await restored.current_batch("catalog"))["id"] == batch["id"]
        assert (await restored.knowledge_documents(knowledge["id"]))[0]["source_ref"] == "fixture:rules"
        assert hashlib.sha256(catalog).hexdigest() == batch["content_hash"]
        assert await PickRepository(restarted.session_factory, "bob").selections() == []
    finally:
        await restarted.stop()
        await second.dispose()
