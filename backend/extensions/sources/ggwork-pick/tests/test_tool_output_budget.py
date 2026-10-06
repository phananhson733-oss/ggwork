"""2026-09-30: the host's ToolOutputBudgetMiddleware replaces any tool result over 12,000 characters with a prose
summary (and truncates over 30,000 regardless of per-tool thresholds). A candidate result reaches that at 8 to 10
dramas, so the card parsed "选剧查询未完成" and the model was told to read_file a path the tool gate refuses. The pick
tools are exempt in config.pick.example.yaml, which pick_entrypoint turns into the production config verbatim."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from deerflow.agents.middlewares.tool_output_budget_middleware import _patch_tool_message
from deerflow.config.tool_output_config import ToolOutputConfig
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from engines import host_engine
from langchain_core.messages import ToolMessage
from sqlalchemy.ext.asyncio import async_sessionmaker

TEMPLATE = Path(__file__).resolve().parents[3] / "config.pick.example.yaml"
PICK_TOOLS = ("pick_query_candidates", "pick_count_candidates", "pick_get_drama_detail", "pick_prepare_selection", "pick_search_knowledge")
KINDS = ("kd", "kw", "qc", "qr", "sm", "mg", "fh")


def pick_tool_output_config():
    return ToolOutputConfig.model_validate(yaml.safe_load(TEMPLATE.read_text(encoding="utf-8")).get("tool_output") or {})


def drama(i):
    return {
        "source": "realshort-pick",
        "source_id": f"b{i}",
        "language": "en",
        "title": f"Budget {i}",
        "theater": "KalosTV",
        "tags": ["复仇", "豪门"],
        "availability": "active",
        "channel_rules": {"youtube": "unknown"},
        # Distinct facts keep this pressure fixture above fallback_max_chars even
        # after dictionary compression; ordinary benchmark fixtures are unchanged.
        "signals": [
            {
                "kind": kind,
                "source_ref": f"ref:{kind}:{i}",
                "observed_at": "2026-09-28",
                "rank": i,
                "note": f"Synthetic row {i}, source {kind}: independent authorization review is required before publication.",
            }
            for kind in KINDS
        ],
        "posted": {"matched": False, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []},
    }


async def twenty_drama_result(tmp_path, *, wrapped=False):
    from ggwork_pick.context import PickLifecycle
    from ggwork_pick.imports import Importer
    from ggwork_pick.repository import PickRepository
    from ggwork_pick.service import PickService
    from ggwork_pick.tools import query_candidates_tool

    engine = host_engine(f"sqlite+aiosqlite:///{tmp_path / 'db'}")
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    await Importer(PickRepository(service.session_factory, "alice"), service.data_dir).catalog(json.dumps([drama(i) for i in range(20)]).encode(), "json")
    store = ExtensionData("task1")
    await PickLifecycle(service).on_task_start(ExtensionData("app"), store, TaskInfo("task1", "run1", "thread1", "lead"))
    runtime = SimpleNamespace(context={"user_id": "alice", EXTENSION_TASK_STORE_KEY: store}, tool_call_id="call1")
    try:
        if not wrapped:
            return await query_candidates_tool.coroutine(filters={"language": "en", "limit": 20}, runtime=runtime)
        from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
        from deerflow.agents.middlewares.tool_output_budget_middleware import ToolOutputBudgetMiddleware

        from ggwork_pick.context import task_from_runtime
        from ggwork_pick.middleware import PickToolGate

        request = SimpleNamespace(runtime=runtime, tool_call={"name": "pick_query_candidates", "id": "call1"})

        async def execute(request):
            output = await query_candidates_tool.coroutine(filters={"language": "en", "limit": 20}, runtime=request.runtime)
            return ToolMessage(content=output, name="pick_query_candidates", tool_call_id="call1")

        async def budgeted(request):
            return await ToolOutputBudgetMiddleware(pick_tool_output_config()).awrap_tool_call(request, execute)

        async def gated(request):
            return await PickToolGate().awrap_tool_call(request, budgeted)

        message = await ToolErrorHandlingMiddleware().awrap_tool_call(request, gated)
        assert message.status == "success"
        assert task_from_runtime(runtime).tool_calls == 1
        return message.content
    finally:
        await engine.dispose()


def test_the_pick_tools_are_exempt_and_the_host_read_file_exemption_stays():
    config = pick_tool_output_config()
    assert set(PICK_TOOLS) <= set(config.exempt_tools)
    assert {"read_file", "read_file_tool"} <= set(config.exempt_tools)


@pytest.mark.asyncio
async def test_a_twenty_drama_result_reaches_the_model_untouched_only_because_of_the_exemption(tmp_path):
    text = await twenty_drama_result(tmp_path)
    assert len(json.loads(text)["items"]) == 20
    assert len(text) > 30_000, "the synthetic result must exceed the host's fallback truncation, not just the externalize threshold"
    message = ToolMessage(content=text, name="pick_query_candidates", tool_call_id="call1")
    kept = _patch_tool_message(message, pick_tool_output_config(), outputs_path=str(tmp_path / "outputs"))
    assert kept is message
    replaced = _patch_tool_message(message, ToolOutputConfig(), outputs_path=str(tmp_path / "outputs"))
    assert replaced is not message
    with pytest.raises(ValueError):
        json.loads(replaced.content)


@pytest.mark.asyncio
async def test_twenty_projected_items_survive_real_host_wrapper_composition(tmp_path):
    text = await twenty_drama_result(tmp_path, wrapped=True)
    payload = json.loads(text)
    assert len(payload["items"]) == 20
    assert all(len(item["evidence"]) == len(KINDS) for item in payload["items"])
    assert payload["id"] and payload["matched_total"] == 20
