"""Plural refs cross real authenticated HTTP admission, model tools and publication."""

import json
from typing import ClassVar

import httpx
import pytest
from fastapi import FastAPI
from ggwork_pick.context import PickLifecycle
from ggwork_pick.imports import Importer
from ggwork_pick.repository import PickRepository
from ggwork_pick.selection import SelectionService
from ggwork_pick.service import PickService
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessageChunk, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.internal_auth import create_internal_auth_headers
from app.gateway.routers import thread_runs, threads
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.extensions.registry import ExtensionRegistry
from deerflow.persistence.engine import _json_serializer
from deerflow.persistence.feedback.model import FeedbackRow
from deerflow.persistence.feedback.sql import FeedbackRepository
from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.stream_bridge.memory import MemoryStreamBridge


class PluralModel(BaseChatModel):
    seen: ClassVar[list] = []

    @property
    def _llm_type(self):
        return "offline-plural-reference"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, *args, **kwargs):
        raise AssertionError("only async scripted provider is supported")

    async def _astream(self, messages, **kwargs):
        self.seen.append(messages)
        system = next(m.content for m in messages if isinstance(m, SystemMessage))
        line = next(line for line in system.splitlines() if line.startswith("本轮显式候选引用："))
        refs = json.loads(line.split("：", 1)[1])
        replies = [m for m in messages if isinstance(m, ToolMessage) and m.name == "pick_get_drama_detail"]
        if len(replies) < 2:
            calls = [{"id": f"detail-{i}", "name": "pick_get_drama_detail", "args": {"result_id": ref["result_id"], "item_id": ref["item_ids"][0]}} for i, ref in enumerate(refs)]
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_calls=calls))
        else:
            claims = []
            for ref, reply in zip(refs, replies[-2:], strict=True):
                item = json.loads(reply.content)["item"]
                claims.append(f"《{item['title']}》的上架日期为{item['listed_at']} [result:{ref['result_id']}:{item['item_id']}]")
            yield ChatGenerationChunk(message=AIMessageChunk(content="。\n".join(claims)))


@pytest.mark.asyncio
async def test_plural_references_through_authenticated_http_and_real_lead_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "host"))
    monkeypatch.setenv("PICK_RUN_TIMEOUT_SECONDS", "30")
    config = AppConfig.model_validate(
        {
            "models": [{"name": "scripted", "use": "test_pick_plural_runtime:PluralModel", "model": "offline"}],
            "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"},
            "tools": [{"name": "pick_get_drama_detail", "group": "pick", "use": "ggwork_pick.tools:get_drama_detail_tool"}],
            "tool_groups": [{"name": "pick"}],
            "memory": {"enabled": False},
            "summarization": {"enabled": False},
            "title": {"enabled": False},
            "suggestions": {"enabled": False},
            "extensions": {"middlewares": ["ggwork_pick.middleware:PickModelGate", "ggwork_pick.middleware:PickToolGate"]},
        }
    )
    set_app_config(config)
    PluralModel.seen.clear()
    engine = create_async_engine("sqlite+aiosqlite://", json_serializer=_json_serializer)
    service = PickService(tmp_path / "pick")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    repo = PickRepository(service.session_factory, "alice")
    records = []
    for i, date in enumerate(["2026-08-01", "2026-09-01"]):
        await Importer(repo, service.data_dir).catalog(json.dumps([{"source": "synthetic", "source_id": "same", "language": "en", "title": "共同剧名", "listed_at": date}]).encode(), "json")
        records.append(await SelectionService(repo).query({}, thread_id="plural-thread", run_id=f"seed-{i}", call_id=f"seed-{i}"))
    registry = ExtensionRegistry()
    with registry.attributed_to("synthetic-pick"):
        registry.task_lifecycle(PickLifecycle(service))
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(thread_runs.router)
    app.include_router(threads.router)
    app.state.store = InMemoryStore()
    app.state.thread_store = MemoryThreadMetaStore(app.state.store)
    await app.state.thread_store.create("plural-thread", user_id="alice")
    app.state.checkpointer = InMemorySaver()
    app.state.run_manager = RunManager()
    app.state.run_event_store = MemoryRunEventStore()
    app.state.stream_bridge = MemoryStreamBridge(queue_maxsize=2000)
    app.state.extensions = registry.build()
    async with engine.begin() as conn:
        await conn.run_sync(FeedbackRow.__table__.create, checkfirst=True)
    app.state.feedback_repo = FeedbackRepository(async_sessionmaker(engine, expire_on_commit=False))
    refs = {"version": "pick-references-v1", "references": [{"result_id": r["id"], "item_ids": [r["items"][0]["item_id"]]} for r in records]}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            seeded = await client.post("/api/threads/plural-thread/state", headers=create_internal_auth_headers(owner_user_id="alice"), json={"values": {"messages": []}})
            assert seeded.status_code == 200, seeded.text
            response = await client.post(
                "/api/threads/plural-thread/runs/stream",
                headers=create_internal_auth_headers(owner_user_id="alice"),
                json={
                    "input": {"messages": [{"role": "user", "content": "比较这两批候选的上架日期", "additional_kwargs": {"pick_references": {**refs, "thread_id": "plural-thread"}}}]},
                    "context": {"pick_references": refs},
                    "stream_mode": ["messages-tuple", "values"],
                },
            )
            assert response.status_code == 200, response.text
            rows = await app.state.run_event_store.list_messages("plural-thread")
            final = [r["content"] for r in rows if r["content"]["type"] == "ai"][-1]
            assert final["additional_kwargs"]["pick_completion"]["status"] == "confirmed", response.text
            for r in records:
                assert f"[result:{r['id']}:{r['items'][0]['item_id']}]" in final["content"]
            assert "2026-08-01" in final["content"] and "2026-09-01" in final["content"]
            assert final["content"] in response.text or json.dumps(final["content"], ensure_ascii=False)[1:-1] in response.text
            assert len(PluralModel.seen) == 2
            history = await client.get("/api/threads/plural-thread/messages", headers=create_internal_auth_headers(owner_user_id="alice"))
            assert history.status_code == 200, history.text
            human = next(row["content"] for row in history.json() if row["content"]["type"] == "human")
            assert human["additional_kwargs"]["pick_references"] == {**refs, "thread_id": "plural-thread"}
            for endpoint, body in [("regenerate", {"message_id": final["id"]}), ("edit-regenerate", {"human_message_id": human["id"], "replacement_text": "比较原两批的日期"})]:
                prepared = await client.post(f"/api/threads/plural-thread/runs/{endpoint}/prepare", headers=create_internal_auth_headers(owner_user_id="alice"), json=body)
                assert prepared.status_code == 200, prepared.text
                if endpoint == "regenerate":
                    assert prepared.json()["input"]["messages"][0]["additional_kwargs"]["pick_references"] == {**refs, "thread_id": "plural-thread"}
                else:
                    # Existing edit API keeps only attachment metadata; the frontend
                    # explicitly reapplies the original turn refs with its edited input.
                    assert "pick_references" not in prepared.json()["input"]["messages"][0]["additional_kwargs"]
            malformed = await client.post(
                "/api/threads/plural-thread/runs/stream",
                headers=create_internal_auth_headers(owner_user_id="alice"),
                json={"input": {"messages": [{"role": "user", "content": "无效引用"}]}, "context": {"pick_references": refs["references"]}, "stream_mode": ["messages-tuple", "values"]},
            )
            assert malformed.status_code == 200
            assert len(PluralModel.seen) == 2
            assert '"status": "incomplete"' in malformed.text
            assert (await client.post("/api/threads/plural-thread/runs", headers=create_internal_auth_headers(owner_user_id="bob"), json={"input": {"messages": []}, "context": {"pick_references": refs}})).status_code == 404
    finally:
        reset_app_config()
        await engine.dispose()
