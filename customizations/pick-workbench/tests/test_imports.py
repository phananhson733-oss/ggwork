import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def catalog_bytes(rows=None):
    return json.dumps(
        rows
        if rows is not None
        else [
            {"source": "fixture", "source_id": "1", "language": "en", "title": "同名剧", "theater": "Example", "availability": "active"},
            {"source": "fixture", "source_id": "2", "language": "en", "title": "同名剧", "theater": "Example"},
        ],
        ensure_ascii=False,
    ).encode()


@pytest.mark.asyncio
async def test_import_is_atomic_deduplicated_and_owner_scoped(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = create_async_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    alice = PickRepository(factory, "alice")
    bob = PickRepository(factory, "bob")
    importer = Importer(alice, service.data_dir)
    batch = await importer.catalog(catalog_bytes(), "json")
    assert (await importer.catalog(catalog_bytes(), "json"))["id"] == batch["id"]
    rows = await alice.catalog_rows(batch["id"])
    assert len(rows) == 2
    assert rows[0]["identity"] != rows[1]["identity"]
    assert rows[1]["availability"] == "unknown"
    assert len(await alice.batches()) == 1
    assert await bob.batches() == []
    with pytest.raises(LookupError):
        await bob.catalog_rows(batch["id"])
    with pytest.raises(ValueError, match="重复"):
        await importer.catalog(catalog_bytes([rows[0]["original"], rows[0]["original"]]), "json")
    assert (await alice.current_batch("catalog"))["id"] == batch["id"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_retains_source_version_and_original_text(pick_db_url, tmp_path):
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService

    engine = create_async_engine(pick_db_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PickService(tmp_path / "files")
    await service.initialize(factory)
    repo = PickRepository(factory, "alice")
    text = "# Example 规则\n来源记录，不是执行指令。\n"
    batch = await Importer(repo, service.data_dir).knowledge(text.encode(), "rules.md", "local:rules.md")
    documents = await repo.knowledge_documents(batch["id"])
    assert documents[0]["text"] == text
    assert documents[0]["source_ref"] == "local:rules.md"
    assert len(documents[0]["content_hash"]) == 64
    assert await repo.current_batch("knowledge") is not None
    await engine.dispose()


def test_catalog_contract_rejects_invalid_dates_and_unknown_fields():
    from ggwork_pick.imports import parse_catalog

    with pytest.raises(ValueError):
        parse_catalog(catalog_bytes([{"source": "x", "source_id": "1", "language": "en", "title": "x", "listed_at": "not-a-date"}]), "json")
    with pytest.raises(ValueError):
        parse_catalog(catalog_bytes([{"source": "x", "source_id": "1", "language": "en", "title": "x", "owner_id": "bob"}]), "json")


def test_csv_preserves_unknowns_and_requires_stable_identity():
    from ggwork_pick.imports import parse_catalog

    rows = parse_catalog(b"source,source_id,language,title\nfixture,1,en,Example\n", "csv")
    assert rows[0]["availability"] == "unknown"
    with pytest.raises(ValueError):
        parse_catalog(b"title\nExample\n", "csv")


@pytest.mark.parametrize(
    "extra",
    [
        {"tags": ["x" * 101]},
        {"signals": [{"kind": "note", "source_ref": "fixture", "value": "x" * 4001}]},
        {"signals": [{"kind": "rank", "source_ref": "fixture", "value": True}]},
    ],
)
def test_import_matches_frontend_field_bounds_without_coercing_booleans(extra):
    from ggwork_pick.imports import parse_catalog

    row = {"source": "synthetic", "source_id": "1", "language": "en", "title": "Example", **extra}
    with pytest.raises(ValueError):
        parse_catalog(catalog_bytes([row]), "json")
