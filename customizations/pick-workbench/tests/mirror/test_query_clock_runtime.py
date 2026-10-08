"""Real loop implementations drive authenticated Agent queries and readonly PostgreSQL."""

import asyncio
import json
import time
from typing import ClassVar

import httpx
import pytest
import test_board_fixture
from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.internal_auth import create_internal_auth_headers
from app.gateway.routers import thread_runs
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.extensions.registry import ExtensionRegistry
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge
from engines import host_engine
from fastapi import FastAPI
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_query_deadlines import test_real_http_disconnect_and_timeout_cancel_locked_query_then_reuse as exercise_query_transport

from ggwork_pick.context import PickLifecycle
from ggwork_pick.query_reader import QueryReader
from ggwork_pick.service import PickService

common_board = test_board_fixture.board


def loop_factory(kind):
    return pytest.importorskip("uvloop").new_event_loop if kind == "uvloop" else asyncio.new_event_loop


class ClockQueryModel(BaseChatModel):
    calls: ClassVar[int] = 0
    query: ClassVar[dict] = {}

    @property
    def _llm_type(self):
        return "offline-clock-query"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError("scripted async provider only")

    async def _astream(self, messages, **kwargs):
        type(self).calls += 1
        replies = [message for message in messages if isinstance(message, ToolMessage) and message.name == "pick_query_data"]
        if not replies:
            yield ChatGenerationChunk(
                message=AIMessageChunk(content="", tool_calls=[{"id": "clock-query", "name": "pick_query_data", "args": {"query": type(self).query}}])
            )
        else:
            result = json.loads(replies[-1].content)
            assert result["counts"]["matched"] > 0, result
            yield ChatGenerationChunk(message=AIMessageChunk(content=f"本次查询符合条件总数为{result['counts']['matched']}部 [tool:clock-query]"))


@pytest.mark.parametrize("kind", ["asyncio", "uvloop"])
@pytest.mark.parametrize("domain", ["catalog", "rankings"])
def test_scripted_gateway_query_to_postgres_preserves_host_and_loop_budgets(common_board, pg_cluster, tmp_path, monkeypatch, kind, domain):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "60")

    async def run():
        loop = asyncio.get_running_loop()
        if kind == "uvloop":
            assert type(loop).__module__.startswith("uvloop")
        # Real uvloop's epoch can differ by thousands of seconds on macOS.
        print(f"clock-evidence: {kind}, loop-minus-monotonic={loop.time() - time.monotonic():.3f}")
        config = AppConfig.model_validate(
            {
                "models": [{"name": "scripted", "use": "test_query_clock_runtime:ClockQueryModel", "model": "offline"}],
                "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
                "tools": [{"name": "pick_query_data", "group": "pick", "use": "ggwork_pick.tools:query_data_tool"}],
                "tool_groups": [{"name": "pick"}],
                "memory": {"enabled": False},
                "summarization": {"enabled": False},
                "title": {"enabled": False},
                "suggestions": {"enabled": False},
                "extensions": {"middlewares": ["ggwork_pick.middleware:PickModelGate", "ggwork_pick.middleware:PickToolGate"]},
            }
        )
        set_app_config(config)
        ClockQueryModel.calls = 0
        ClockQueryModel.query = {"domain": domain, "scope": "full_catalog", **({"rank": "kd"} if domain == "rankings" else {})}
        engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
        service = PickService(tmp_path / "pick")
        await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
        service.query_reader = QueryReader(common_board["reader"], ssl=False)
        registry = ExtensionRegistry()
        with registry.attributed_to("clock-test"):
            registry.task_lifecycle(PickLifecycle(service))
        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.include_router(thread_runs.router)
        app.state.store = InMemoryStore()
        app.state.thread_store = MemoryThreadMetaStore(app.state.store)
        await app.state.thread_store.create("clock-query-thread", user_id="alice")
        app.state.checkpointer = InMemorySaver()
        app.state.run_manager = RunManager()
        app.state.run_event_store = MemoryRunEventStore()
        app.state.stream_bridge = MemoryStreamBridge(queue_maxsize=2000)
        app.state.extensions = registry.build()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                async with asyncio.timeout(5):
                    response = await client.post(
                        "/api/threads/clock-query-thread/runs/stream",
                        headers=create_internal_auth_headers(owner_user_id="alice"),
                        json={"input": {"messages": [{"role": "user", "content": "查完整剧库数量"}]}, "stream_mode": ["messages-tuple", "values"]},
                    )
                assert response.status_code == 200, response.text
            rows = await app.state.run_event_store.list_messages("clock-query-thread")
            final = [row["content"] for row in rows if row["content"]["type"] == "ai"][-1]
            assert final["additional_kwargs"]["pick_completion"]["status"] == "confirmed", response.text
            assert "[tool:clock-query]" in final["content"]
            assert ClockQueryModel.calls == 2
            assert service.query_reader.pool.get_max_size() == 3
        finally:
            reset_app_config()
            await service.query_reader.close()
            await engine.dispose()

    with asyncio.Runner(loop_factory=loop_factory(kind)) as runner:
        runner.run(run())


@pytest.mark.parametrize("kind", ["asyncio", "uvloop"])
def test_real_http_and_postgres_cancel_and_reuse_on_both_loops(common_board, pg_cluster, tmp_path, kind):
    with asyncio.Runner(loop_factory=loop_factory(kind)) as runner:
        runner.run(exercise_query_transport(common_board, pg_cluster, tmp_path))
