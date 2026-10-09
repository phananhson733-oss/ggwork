"""An editing tool call through the real worker, graph, journal receipt and pick publication gate."""

from typing import Any

import pytest
from ggwork_edit.context import EditingLifecycle
from ggwork_edit.service import EditingService
from ggwork_edit.tools import get_tool
from ggwork_pick.context import PickLifecycle
from ggwork_pick.middleware import PickModelGate, PickToolGate
from ggwork_pick.service import PickService
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deerflow.agents.middlewares.pick_publication_middleware import with_pick_publication_boundary
from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.extensions.registry import ExtensionRegistry
from deerflow.persistence.engine import _json_serializer
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.worker import RunContext, run_agent
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge


class ScriptedModel(BaseChatModel):
    replies: list[Any]
    inputs: list[Any] = Field(default_factory=list)

    @property
    def _llm_type(self):
        return "offline-scripted-editing"

    def bind_tools(self, tools, **kwargs):
        return self

    def _reply(self, messages):
        self.inputs.append(messages)
        reply = self.replies[min(len(self.inputs) - 1, len(self.replies) - 1)]
        return reply if isinstance(reply, AIMessage) else AIMessage(content=reply)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(generations=[ChatGeneration(message=self._reply(messages))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._reply(messages)
        yield ChatGenerationChunk(message=AIMessageChunk(**message.model_dump(exclude={"type", "usage_metadata"})))


@pytest.fixture
def skill_config(tmp_path, monkeypatch):
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
    from deerflow.skills.storage import reset_skill_storage

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    set_app_config(AppConfig.model_validate({"config_version": 46, "models": [], "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}, "skills": {"path": str(tmp_path / "skills")}}))
    yield
    reset_skill_storage()
    reset_app_config()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"limit": 9}])
async def test_editing_result_reaches_the_checked_final_through_the_host_receipt(tmp_path, skill_config, arguments):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'host.db'}", json_serializer=_json_serializer)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    pick = PickService(tmp_path / "pick")
    await pick.initialize(sessions)
    editing = EditingService(hook_available=True)
    await editing.initialize(sessions)
    registry = ExtensionRegistry()
    with registry.attributed_to("synthetic-pick"):
        registry.task_lifecycle(PickLifecycle(pick))
    with registry.attributed_to("synthetic-edit"):
        registry.task_lifecycle(EditingLifecycle(editing))
    model = ScriptedModel(replies=[AIMessage(content="我来查一下", tool_calls=[{"id": "call-1", "name": "clip_get", "args": arguments}]), "你有0个任务，《甲》共80集。"])
    saver = InMemorySaver()
    graph = create_agent(model=model, tools=[get_tool], middleware=with_pick_publication_boundary([ToolErrorHandlingMiddleware(), PickModelGate(), PickToolGate()]), checkpointer=saver)
    manager, events, bridge = RunManager(), MemoryRunEventStore(), MemoryStreamBridge(queue_maxsize=2000)
    record = await manager.create("editing-checked")
    await run_agent(
        bridge,
        manager,
        record,
        ctx=RunContext(checkpointer=saver, event_store=events, extensions=registry.build()),
        agent_factory=lambda config: graph,
        graph_input={"messages": [{"role": "user", "content": "我有哪些剪辑任务"}]},
        config={"configurable": {"thread_id": record.thread_id}},
        stream_modes=["messages-tuple", "values", "updates"],
    )
    rows = [row["content"] for row in await events.list_messages(record.thread_id)]
    assert [row["type"] for row in rows] == ["human", "ai", "tool", "ai"]
    assert rows[1]["content"] == "" and rows[1]["tool_calls"][0]["name"] == "clip_get"
    final = rows[-1]
    assert "甲" not in final["content"] and "未确认" not in final["content"]
    if arguments:
        assert rows[2]["status"] == "error"
        assert final["additional_kwargs"]["pick_completion"]["status"] == "incomplete"
        assert "剪辑操作未完成" in final["content"] and "limit" not in final["content"]
    else:
        assert final["additional_kwargs"]["pick_completion"]["status"] == "confirmed"
        assert final["content"] == "剪辑历史共 0 个任务。[打开剪辑页面](/workspace/editing)"
    assert len(model.inputs) == 2
    await editing.stop()
    await engine.dispose()
