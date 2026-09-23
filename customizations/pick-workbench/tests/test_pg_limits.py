"""VARCHAR lengths (plan 6.7): SQLite ignores them, PostgreSQL rejects the whole statement.

At the limit a value round-trips on both dialects; one character over is refused the same way on
both, before the database sees it, and an HTTP caller gets 422 instead of a 500. The ids come from
the host (thread 64, run 64) and the model provider (tool call, message); today's are far shorter.
"""

import json

import pytest
import pytest_asyncio
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

ALICE = {"test-owner": "alice"}


@pytest_asyncio.fixture
async def workspace(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    importer = Importer(repo, service.data_dir)
    await importer.catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
    yield repo, importer
    await engine.dispose()


@pytest_asyncio.fixture
async def shared_repo(pick_db_url, tmp_path):
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = host_engine(pick_db_url)
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    yield PickRepository.shared(service.session_factory)
    await engine.dispose()


def _drama(identity_length: int) -> dict:
    """A row whose identity is exactly identity_length characters.

    The identity is ["source","source_id","language"]: 10 characters of punctuation around the values. Plain
    values top out at 100 + 256 + 40 + 10 = 406; each quote in the id is escaped to two characters.
    """
    quotes = identity_length - 406
    return {"source": "s" * 100, "source_id": '"' * quotes + "x" * (256 - quotes), "language": "l" * 40, "title": "Long identity"}


@pytest.mark.asyncio
async def test_candidate_ids_at_the_column_limits(workspace):
    from ggwork_pick.selection import SelectionService

    repo, _ = workspace
    service = SelectionService(repo)
    thread, run, call = "t" * 64, "r" * 128, "c" * 128
    result = await service.query({}, thread_id=thread, run_id=run, call_id=call)
    assert [row["id"] for row in await repo.results(thread)] == [result["id"]]
    assert (await repo.result_for_call(run, call))["id"] == result["id"]
    for over in ({"thread_id": "t" * 65}, {"run_id": "r" * 129}, {"call_id": "c" * 129}):
        ids = {"thread_id": "t2", "run_id": "r2", "call_id": "c2", **over}
        with pytest.raises(ValueError, match="超过"):
            await service.query({}, **ids)
    assert await repo.results("t" * 65) == []


@pytest.mark.asyncio
async def test_answer_check_ids_at_the_column_limits(workspace):
    repo, _ = workspace
    thread, run, message = "t" * 64, "r" * 128, "m" * 256
    await repo.record_answer_check(thread_id=thread, run_id=run, message_id=message, notes=["note"])
    assert [(check["message_id"], check["run_id"]) for check in await repo.answer_checks(thread)] == [(message, run)]
    for over in ({"thread_id": "t" * 65}, {"run_id": "r" * 129}, {"message_id": "m" * 257}):
        with pytest.raises(ValueError, match="超过"):
            await repo.record_answer_check(**{"thread_id": "t2", "run_id": "r2", "message_id": "m2", "notes": ["note"], **over})
    assert await repo.answer_checks("t2") == []


def test_the_identity_limit_is_the_column_length():
    from ggwork_pick.contracts import IDENTITY_MAX_LENGTH
    from ggwork_pick.models import drama_versions, selections

    assert drama_versions.c.identity.type.length == selections.c.identity.type.length == IDENTITY_MAX_LENGTH


@pytest.mark.asyncio
async def test_a_512_character_identity_is_imported_queried_and_saved(workspace):
    from ggwork_pick.imports import parse_catalog
    from ggwork_pick.selection import SelectionService

    repo, importer = workspace
    payload = json.dumps([_drama(512)]).encode()
    assert len(parse_catalog(payload, "json")[0]["identity"]) == 512
    batch = await importer.catalog(payload, "json")
    result = await SelectionService(repo).query({}, thread_id="t", run_id="r", call_id="c", pinned_versions=(batch["id"], None))
    identity = result["items"][0]["identity"]
    assert len(identity) == 512
    await repo.save_selection("save-1", result["id"], [result["items"][0]["item_id"]])
    assert [row["identity"] for row in await repo.selections()] == [identity]


@pytest.mark.asyncio
async def test_a_longer_identity_is_a_validation_error_and_a_422(app_client):
    from pydantic import ValidationError

    from ggwork_pick.imports import parse_catalog

    with pytest.raises(ValidationError, match="identity"):
        parse_catalog(json.dumps([_drama(513)]).encode(), "json")
    client, _ = app_client
    files = [("files", ("c.json", json.dumps([_drama(513)]), "application/json"))]
    response = await client.post("/api/pick/imports", headers=ALICE, data={"kind": "catalog"}, files=files)
    assert response.status_code == 422
    assert (await client.get("/api/pick/imports", headers=ALICE)).json()["batches"] == []


@pytest.mark.asyncio
async def test_knowledge_titles_are_cut_to_500_characters(workspace):
    repo, importer = workspace
    name = "k" * 597 + ".md"
    batch = await importer.knowledge(b"# K", name, "ref")
    assert [doc["title"] for doc in await repo.knowledge_documents(batch["id"])] == [name[:500]]


@pytest.mark.asyncio
async def test_request_ids_and_source_times_at_the_column_limits(app_client):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.selection import SelectionService

    client, service = app_client
    repo = PickRepository(service.session_factory, "alice")
    await Importer(repo, service.data_dir).catalog(b'[{"source":"synthetic","source_id":"1","language":"en","title":"Example"}]', "json")
    result = await SelectionService(repo).query({}, thread_id="t", run_id="r", call_id="c")
    save = {"result_id": result["id"], "item_ids": [result["items"][0]["item_id"]]}
    assert (await client.post("/api/pick/selections", headers=ALICE, json={**save, "request_id": "q" * 129})).status_code == 422
    assert (await client.post("/api/pick/selections", headers=ALICE, json={**save, "request_id": "q" * 128})).status_code == 200
    assert (await repo.command_receipt("q" * 128))["request_id"] == "q" * 128
    shared = PickRepository.shared(service.session_factory)
    batch = await shared.publish_import(kind="knowledge", content_hash="h1", raw_blob_path="/x", rows=[], source_as_of="9" * 40)
    assert (await shared.batch_info(batch["id"]))["source_as_of"] == "9" * 40
    with pytest.raises(ValueError, match="超过"):
        await shared.publish_import(kind="knowledge", content_hash="h2", raw_blob_path="/x", rows=[], source_as_of="9" * 41)


@pytest.mark.asyncio
async def test_publish_import_refuses_rows_longer_than_their_columns(shared_repo):
    # The repository's own check, independent of DramaInput and the Importer's title cut.
    with pytest.raises(ValueError, match="identity 超过 512"):
        await shared_repo.publish_import(kind="catalog", content_hash="h1", raw_blob_path="/x", rows=[{"identity": "i" * 513, "title": "T"}])
    knowledge = dict(document_id="d", content_hash="c", title="t" * 501, source_ref="r", text="x", metadata_json={})
    with pytest.raises(ValueError, match="title 超过 500"):
        await shared_repo.publish_import(kind="knowledge", content_hash="h2", raw_blob_path="/x", rows=[knowledge])
    assert await shared_repo.batches() == []


@pytest.mark.asyncio
async def test_finish_sync_run_refuses_a_source_time_longer_than_its_column(shared_repo):
    run = await shared_repo.start_sync_run("realshort", "manual")
    with pytest.raises(ValueError, match="source_as_of 超过 40"):
        await shared_repo.finish_sync_run(run["id"], status="success", source_as_of="9" * 41)
