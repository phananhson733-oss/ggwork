"""The pick middleware offers lark_cli per user and budgets it on its own (docs/pick-workbench/lark-personal-auth.md section 4)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from deerflow.integrations import lark_cli
from deerflow.tools.mcp_metadata import tag_mcp_tool
from deerflow_extension_api import ExtensionData, TaskInfo
from deerflow_extension_api.runtime_bridge import EXTENSION_TASK_STORE_KEY
from langchain_core.tools import tool

from ggwork_pick.lark_tool import CONNECT_LINK, lark_cli_tool

TEMPLATE = Path(__file__).resolve().parents[3] / "config.pick.example.yaml"
NOT_CONFIGURED = {"configured": False, "app_id": None, "brand": None}


@tool("lark_cli")
def mcp_lark_cli(argv: list[str]) -> str:
    """An MCP server's tool that happens to share the name."""
    return ""


@tool("feishu-bot_send_message")
def send_message(content: str) -> str:
    """Send a group notification."""
    return content


@tool("github_search_code")
def github_search_code(query: str) -> str:
    """Search code."""
    return query


tag_mcp_tool(mcp_lark_cli, server_name="lark", transport="stdio")
tag_mcp_tool(send_message, server_name="feishu-bot", transport="stdio")
tag_mcp_tool(github_search_code, server_name="github", transport="http")
send_message.metadata = {**send_message.metadata, "readOnlyHint": False}
github_search_code.metadata = {**github_search_code.metadata, "readOnlyHint": True}


def _runtime(user_id="alice"):
    from ggwork_pick.context import PickTask

    task = PickTask(service=None, info=TaskInfo("t", "r", "c", "lead"))
    task.repository = AsyncMock()
    store = ExtensionData("t")
    store.set(task)
    return SimpleNamespace(context={"user_id": user_id, EXTENSION_TASK_STORE_KEY: store}), task


@pytest.fixture
def connections(monkeypatch):
    """Users who finished the capability center's connect step; every lookup is recorded."""
    connected = {"alice"}
    lookups = []

    def read(user_id):
        lookups.append(user_id)
        return {"configured": True, "app_id": "cli_x", "brand": "feishu"} if user_id in connected else NOT_CONFIGURED

    monkeypatch.setattr(lark_cli, "peek_lark_app_config", read)
    return lookups


async def _model_view(tools, user_id="alice"):
    from langchain.agents.middleware.types import ModelRequest

    from ggwork_pick.middleware import PickModelGate

    runtime, _ = _runtime(user_id)
    request = ModelRequest(model=SimpleNamespace(), messages=[], runtime=runtime, tools=tools)
    adjusted = await PickModelGate().awrap_model_call(request, AsyncMock(side_effect=lambda adjusted: adjusted))
    names = [item["name"] if isinstance(item, dict) else item.name for item in adjusted.tools]
    return names, adjusted.system_message.content


@pytest.mark.asyncio
async def test_a_connected_user_sees_lark_cli_and_is_told_its_output_is_data(connections):
    from ggwork_pick.middleware import LARK_READY

    names, system = await _model_view([{"name": "pick_query_candidates"}, lark_cli_tool, github_search_code])

    assert names == ["pick_query_candidates", "lark_cli", "github_search_code"]
    assert LARK_READY in system and CONNECT_LINK not in system
    assert connections == ["alice"]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["bob", "default", ""])
async def test_without_a_connection_lark_cli_is_withheld_and_the_model_gets_the_settings_link(connections, user_id):
    from ggwork_pick.middleware import LARK_READY

    names, system = await _model_view([{"name": "pick_query_candidates"}, lark_cli_tool], user_id=user_id)

    assert names == ["pick_query_candidates"]
    assert CONNECT_LINK in system and LARK_READY not in system
    # Without an authenticated owner there is nothing to look up.
    assert connections == (["bob"] if user_id == "bob" else [])


@pytest.mark.asyncio
async def test_a_deployment_without_lark_cli_says_nothing_about_feishu(connections):
    names, system = await _model_view([{"name": "pick_query_candidates"}, github_search_code])

    assert names == ["pick_query_candidates", "github_search_code"]
    assert "lark_cli" not in system and CONNECT_LINK not in system
    assert connections == []


@pytest.mark.asyncio
async def test_only_the_configured_tool_is_lark_cli(connections):
    """An MCP tool or a provider's dict tool of the same name neither enables the Feishu notice nor passes as lark_cli."""
    from ggwork_pick.middleware import PickToolGate, is_lark_tool

    assert is_lark_tool(lark_cli_tool) and not is_lark_tool(mcp_lark_cli) and not is_lark_tool({"name": "lark_cli"})
    names, system = await _model_view([{"name": "lark_cli"}, mcp_lark_cli])
    assert names == ["lark_cli"] and CONNECT_LINK not in system and connections == []

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    with pytest.raises(ValueError, match="不允许"):
        await PickToolGate().awrap_tool_call(SimpleNamespace(runtime=runtime, tool_call={"name": "lark_cli"}, tool=None), handler)
    # The MCP namesake is an ordinary plugin and spends the plugin budget.
    assert await PickToolGate().awrap_tool_call(SimpleNamespace(runtime=runtime, tool_call={"name": "lark_cli"}, tool=mcp_lark_cli), handler) == "ok"
    assert (task.lark_calls, task.plugin_calls) == (0, 1)


@pytest.mark.asyncio
async def test_lark_calls_have_their_own_budget():
    from ggwork_pick.middleware import LARK_CALL_LIMIT, PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()
    lark = SimpleNamespace(runtime=runtime, tool_call={"name": "lark_cli"}, tool=lark_cli_tool)
    for _ in range(LARK_CALL_LIMIT):
        assert await gate.awrap_tool_call(lark, handler) == "ok"
    with pytest.raises(ValueError, match="飞书命令调用次数已达上限"):
        await gate.awrap_tool_call(lark, handler)
    assert (task.lark_calls, task.plugin_calls, task.tool_calls) == (LARK_CALL_LIMIT, 0, 0)
    # Neither the pick budget nor the plugin budget is spent by Feishu reads.
    assert await gate.awrap_tool_call(SimpleNamespace(runtime=runtime, tool_call={"name": "pick_query_candidates"}, tool=None), handler) == "ok"
    assert await gate.awrap_tool_call(SimpleNamespace(runtime=runtime, tool_call={"name": "github_search_code"}, tool=github_search_code), handler) == "ok"
    assert handler.await_count == LARK_CALL_LIMIT + 2


@pytest.mark.asyncio
async def test_after_a_feishu_read_an_external_effect_plugin_waits_for_the_user():
    from ggwork_pick.middleware import PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()

    def call(item):
        return SimpleNamespace(runtime=runtime, tool_call={"name": item.name}, tool=item)

    assert await gate.awrap_tool_call(call(lark_cli_tool), handler) == "ok"
    assert task.plugin_read is True
    with pytest.raises(ValueError, match="用户确认"):
        await gate.awrap_tool_call(call(send_message), handler)
    # Reading more is still fine.
    assert await gate.awrap_tool_call(call(github_search_code), handler) == "ok"
    assert handler.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("argv", [["skills", "read", "lark-doc"], ["docs", "--help"], ["schema", "docs.+fetch"]])
async def test_lark_clis_own_guides_are_not_outside_content(argv):
    """lark-cli's help and skill text come from the binary, not from Feishu: they do not hold back a group message."""
    from ggwork_pick.middleware import PickToolGate

    runtime, task = _runtime()
    handler = AsyncMock(return_value="ok")
    gate = PickToolGate()
    guide = SimpleNamespace(runtime=runtime, tool_call={"name": "lark_cli", "args": {"argv": argv}}, tool=lark_cli_tool)
    assert await gate.awrap_tool_call(guide, handler) == "ok"
    assert task.plugin_read is False and task.lark_calls == 1
    assert await gate.awrap_tool_call(SimpleNamespace(runtime=runtime, tool_call={"name": send_message.name}, tool=send_message), handler) == "ok"
    read = SimpleNamespace(runtime=runtime, tool_call={"name": "lark_cli", "args": {"argv": ["docs", "+fetch", "--doc", "AbC"]}}, tool=lark_cli_tool)
    assert await gate.awrap_tool_call(read, handler) == "ok"
    assert task.plugin_read is True


def test_asking_whether_a_user_connected_writes_nothing(monkeypatch, tmp_path):
    from deerflow.config import paths as paths_module
    from deerflow.config.paths import Paths

    from ggwork_pick.lark_tool import lark_connected

    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=tmp_path / "home"))
    assert lark_connected("bob") is False
    assert not (tmp_path / "home").exists()


def test_runtime_config_registers_lark_cli_for_both_roles():
    from deerflow.authz.principal import Principal
    from deerflow.authz.rbac import RbacAuthorizationProvider

    config = yaml.safe_load(TEMPLATE.read_text())
    [entry] = [item for item in config["tools"] if item["name"] == "lark_cli"]
    assert entry["use"] == "ggwork_pick.lark_tool:lark_cli_tool"
    assert {"name": entry["group"]} in config["tool_groups"]
    provider = RbacAuthorizationProvider(**config["authorization"]["provider"]["config"])
    for role in ("admin", "user"):
        assert provider.filter_resources(Principal(user_id="u", role=role), "tool", ["lark_cli", "present_files"]) == ["lark_cli"]
