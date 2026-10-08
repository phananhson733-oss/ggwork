"""The common tool is bounded structured data through the real host budget wrapper."""

import json
from types import SimpleNamespace

import pytest
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from langchain_core.messages import ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_tool_output_budget import drama, pick_tool_output_config


@pytest.mark.asyncio
async def test_twenty_common_rows_survive_host_budget_and_capture_full_facts(tmp_path):
    from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware

    from ggwork_pick.context import PickLifecycle, task_from_runtime
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_data_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine))
    rows = [{**drama(i), "source": "synthetic", "title": "Ordinary synthetic title " + str(i)} for i in range(20)]
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(json.dumps(rows).encode(), "json")
    store = ExtensionData("projection-task")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("projection-task", "r", "t", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="common-budget")
    request = SimpleNamespace(runtime=runtime, tool_call={"name": "pick_query_data", "id": "common-budget"})

    async def execute(request):
        output = await query_data_tool.coroutine(query={"domain": "catalog", "scope": "full_catalog", "limit": 200}, runtime=request.runtime)
        return ToolMessage(content=output, name="pick_query_data", tool_call_id="common-budget")

    try:
        message = await ToolOutputBudgetMiddleware(pick_tool_output_config()).awrap_tool_call(request, execute)
        payload = json.loads(message.content)
        assert payload["projection_version"] == "pick-query-model-v1"
        assert payload["projection"]["page_limit"] == 20
        assert payload["projection"]["shown"] == 20
        assert len(payload["rows"]) == 20
        assert 12000 < len(message.content)
        assert len(message.content.encode()) <= 48000
        assert "board" not in payload
        assert all(row["reference"].startswith("tool:common-budget:row:") for row in payload["rows"])
        assert all(row["signals_truncated"] and row["signal_count"] == 7 for row in payload["rows"])
        evidence = task_from_runtime(runtime).answer_evidence
        assert len([a for a in evidence.atoms if a.field_name == "fh.rank"]) == 20  # seventh source captured before model projection
        assert all("note" not in s for row in payload["rows"] for s in row["signals"])
    finally:
        await service.stop()
        await engine.dispose()


def payload_for(drama_rows):
    from ggwork_pick.completion_contracts import CommonQuery, QueryPin, QueryResponse
    from ggwork_pick.contracts import DramaInput

    rows = []
    for raw in drama_rows:
        drama = DramaInput.model_validate(raw)
        rows.append({"identity": drama.identity, "drama": drama, "posted_status": "unknown", "posted_scope_complete": False})
    return QueryResponse(
        request=CommonQuery(domain="catalog", scope="full_catalog", limit=20, offset=40),
        pin=QueryPin(catalog_batch_id="synthetic-batch", knowledge_batch_id=None, mirror_version=None, rule_version="pick-rules-v1"),
        actual_period=None,
        order_version="evidence-date-v1",
        counts={"total": 100, "matched": 100, "returned": len(rows)},
        truncated=True,
        next_offset=40 + len(rows),
        rows=rows,
        source_as_of=None,
        mirror_synced_at=None,
    ).model_dump(mode="json")


def test_multibyte_byte_cap_declares_omissions_and_advances_only_shown_rows():
    from ggwork_pick.query_model_contracts import QueryModelProjection
    from ggwork_pick.query_model_projection import model_projection

    raw = [
        {
            **drama(i),
            "source": "synthetic",
            "source_id": str(i) + "a" * 240,
            "title": "剧" * 500,
            "theater": "场" * 100,
            "signals": [{**s, "grade": "等" * 100} for s in drama(i)["signals"]],
        }
        for i in range(20)
    ]
    original = payload_for(raw)
    projected = model_projection(original, call_id="long-query-call", requested_limit=200)
    assert QueryModelProjection.model_validate(projected).model_dump(mode="json") == projected
    encoded = json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode()
    assert len(encoded) <= 48000
    page = projected["projection"]
    assert 0 < page["shown"] < 20 and page["omitted_rows"] == 20 - page["shown"]
    assert page["next_offset"] == 40 + page["shown"]
    assert page["truncated"] and projected["counts"]["returned"] == 20
    assert original["rows"][0]["drama"]["signals"][0]["note"]  # projection did not mutate full source


def test_closed_model_contract_rejects_raw_blobs_authority_and_more_than_twenty_rows():
    from copy import deepcopy

    from pydantic import ValidationError

    from ggwork_pick.query_model_contracts import QueryModelProjection
    from ggwork_pick.query_model_projection import model_projection

    projected = model_projection(payload_for([drama(1)]), call_id="closed", requested_limit=20)
    for extra in ({"board": {}}, {"audit_facts": []}, {"owner_id": "other"}):
        with pytest.raises(ValidationError):
            QueryModelProjection.model_validate({**projected, **extra})
    too_many = deepcopy(projected)
    too_many["rows"] = too_many["rows"] * 21
    with pytest.raises(ValidationError):
        QueryModelProjection.model_validate(too_many)


def test_shared_projection_examples_round_trip_with_closed_contract():
    from pathlib import Path

    from ggwork_pick.query_model_contracts import QueryModelProjection

    path = Path(__file__).resolve().parents[3] / "frontend/tests/unit/core/pick/fixtures/query-model-v1.json"
    for payload in json.loads(path.read_text(encoding="utf-8")).values():
        assert QueryModelProjection.model_validate(payload).model_dump(mode="json") == payload


def test_malformed_source_projection_fails_with_bounded_safe_message():
    from ggwork_pick.completion_contracts import CommonQuery
    from ggwork_pick.query_model_projection import model_projection
    from ggwork_pick.query_reader import QueryFailure

    payload = payload_for([])
    payload["request"] = CommonQuery(domain="rules", scope="full_catalog").model_dump(mode="json")
    payload["board"] = {"rules": {"platformRules": {"PRIVATE-SOURCE-" * 300: {"name": "Example", "yt": "no"}}}}
    with pytest.raises(QueryFailure) as error:
        model_projection(payload, call_id="safe-failure", requested_limit=20)
    assert error.value.code == "source_unavailable"
    assert len(str(error.value)) < 200 and "PRIVATE-SOURCE" not in str(error.value)
