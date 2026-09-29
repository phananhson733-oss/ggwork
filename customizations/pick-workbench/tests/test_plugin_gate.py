"""Administrator-enabled plugins reach the model and run under their own budget; nothing else does."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from deerflow.tools.mcp_metadata import tag_mcp_tool
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from langchain_core.tools import tool

TEMPLATE = Path(__file__).resolve().parents[3] / "config.pick.example.yaml"


@tool("github_search_code")
def github_search_code(query: str) -> str:
    """Search code."""
    return query


@tool("web_search")
def web_search(query: str) -> str:
    """Search the web."""
    return query


@tool("present_files")
def present_files(paths: list[str]) -> str:
    """Not a plugin."""
    return ""


tag_mcp_tool(github_search_code, server_name="github", transport="http")


def _runtime():
    from ggwork_pick.context import PickTask

    task = PickTask(service=None, info=TaskInfo("t", "r", "c", "lead"))
    task.repository = AsyncMock()
    store = ExtensionData("t")
    store.set(task)
    return SimpleNamespace(context={EXTENSION_TASK_STORE_KEY: store}), task


@pytest.mark.asyncio
async def test_model_sees_pick_and_enabled_plugin_tools_only():
    from langchain.agents.middleware.types import ModelRequest

    from ggwork_pick.middleware import PickModelGate

    runtime, _ = _runtime()
    # A provider-side dict tool never counts as a plugin, whatever it is called.
    tools = [{"name": "pick_query_candidates"}, github_search_code, web_search, present_files, {"name": "web_fetch"}, {"name": "bash"}]
    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=tools)
    adjusted = await PickModelGate().awrap_model_call(request, AsyncMock(side_effect=lambda adjusted: adjusted))
    names = [item["name"] if isinstance(item, dict) else item.name for item in adjusted.tools]
    assert names == ["pick_query_candidates", "github_search_code", "web_search"]
    assert "插件" in adjusted.system_message.content and "不使用外部搜索" not in adjusted.system_message.content


@pytest.mark.asyncio
async def test_plugin_calls_have_their_own_budget_and_unknown_tools_stay_blocked():
    from ggwork_pick.middleware import PLUGIN_CALL_LIMIT, PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()
    plugin = SimpleNamespace(runtime=runtime, tool_call={"name": "github_search_code"}, tool=github_search_code)
    for _ in range(PLUGIN_CALL_LIMIT):
        assert await gate.awrap_tool_call(plugin, handler) == "ok"
    with pytest.raises(ValueError, match="插件工具调用次数已达上限"):
        await gate.awrap_tool_call(plugin, handler)
    assert (task.plugin_calls, task.tool_calls) == (PLUGIN_CALL_LIMIT, 0)
    # The pick budget is untouched by plugin use.
    pick = SimpleNamespace(runtime=runtime, tool_call={"name": "pick_query_candidates"}, tool=None)
    assert await gate.awrap_tool_call(pick, handler) == "ok"
    for blocked in (
        SimpleNamespace(runtime=runtime, tool_call={"name": "present_files"}, tool=present_files),
        SimpleNamespace(runtime=runtime, tool_call={"name": "github_search_code"}, tool=None),
        SimpleNamespace(runtime=runtime, tool_call={"name": "web_fetch"}),
    ):
        with pytest.raises(ValueError, match="不允许"):
            await gate.awrap_tool_call(blocked, handler)
    assert handler.await_count == PLUGIN_CALL_LIMIT + 1


@tool("pick_export_board")
def mcp_pick_export(board: str) -> str:
    """An MCP server's tool whose name starts like the pick tools; no annotations."""
    return board


tag_mcp_tool(mcp_pick_export, server_name="exports", transport="http")


@pytest.mark.asyncio
async def test_an_mcp_tool_named_like_a_pick_tool_is_still_a_plugin():
    """09-29 review: a `pick_` prefix used to put an MCP tool on the pick budget and past the post-read block."""
    from ggwork_pick.middleware import PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()
    export = SimpleNamespace(runtime=runtime, tool_call={"name": "pick_export_board"}, tool=mcp_pick_export)
    assert await gate.awrap_tool_call(export, handler) == "ok"
    assert (task.plugin_calls, task.tool_calls) == (1, 0)
    read = SimpleNamespace(runtime=runtime, tool_call={"name": "web_search"}, tool=web_search)
    assert await gate.awrap_tool_call(read, handler) == "ok"
    with pytest.raises(ValueError, match="用户确认"):
        await gate.awrap_tool_call(export, handler)


@pytest.mark.asyncio
async def test_native_web_tools_count_as_plugins():
    from ggwork_pick.middleware import PickToolGate

    runtime, task = _runtime()
    request = SimpleNamespace(runtime=runtime, tool_call={"name": "web_search"}, tool=web_search)
    assert await PickToolGate().awrap_tool_call(request, AsyncMock(return_value="ok")) == "ok"
    assert task.plugin_calls == 1


def test_runtime_config_enables_web_tools_and_keeps_host_built_ins_denied():
    from deerflow.authz.principal import Principal
    from deerflow.authz.rbac import RbacAuthorizationProvider

    config = yaml.safe_load(TEMPLATE.read_text())
    assert {tool["name"] for tool in config["tools"]} >= {"web_search", "web_fetch"}
    assert {"name": "web"} in config["tool_groups"]
    provider = RbacAuthorizationProvider(**config["authorization"]["provider"]["config"])
    candidates = ["pick_query_candidates", "web_search", "github_search_code", "present_files", "task", "list_uploaded_files", "review_skill_package"]
    for role in ("admin", "user"):
        allowed = provider.filter_resources(Principal(user_id="u", role=role), "tool", candidates)
        assert allowed == ["pick_query_candidates", "web_search", "github_search_code"]
    assert config["sandbox"]["allow_host_bash"] is False


@tool("feishu-bot_send_message")
def send_message(content: str) -> str:
    """Send a group notification."""
    return content


@tool("jira_create_issue")
def create_issue(summary: str) -> str:
    """No annotations: counts as having external effects."""
    return summary


tag_mcp_tool(send_message, server_name="feishu-bot", transport="stdio")
tag_mcp_tool(create_issue, server_name="jira", transport="http")
send_message.metadata = {**send_message.metadata, "readOnlyHint": False}
github_search_code.metadata = {**github_search_code.metadata, "readOnlyHint": True}


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", [github_search_code, web_search])
@pytest.mark.parametrize("writer", [send_message, create_issue])
async def test_actions_with_external_effects_wait_for_the_user_after_a_plugin_read(reader, writer):
    from ggwork_pick.middleware import PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()

    def call(item):
        return SimpleNamespace(runtime=runtime, tool_call={"name": item.name}, tool=item)

    # Acting first is fine: the user's own message asked for it.
    assert await gate.awrap_tool_call(call(writer), handler) == "ok"
    assert await gate.awrap_tool_call(call(reader), handler) == "ok"
    with pytest.raises(ValueError, match="用户确认"):
        await gate.awrap_tool_call(call(writer), handler)
    assert handler.await_count == 2 and task.plugin_read is True
    # A new run (the user's next message) starts clean.
    fresh_runtime, _ = _runtime()
    fresh = SimpleNamespace(runtime=fresh_runtime, tool_call={"name": writer.name}, tool=writer)
    assert await gate.awrap_tool_call(fresh, handler) == "ok"
