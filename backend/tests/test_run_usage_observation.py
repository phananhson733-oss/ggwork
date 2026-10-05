"""Observed usage survives cancellation without turning missing provider data into zero."""

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager, RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore

KEY = "deerflow_usage_observation"


def response(usage=None):
    return LLMResult(generations=[[ChatGeneration(message=AIMessage(content="synthetic", usage_metadata=usage))]])


def start(journal, rid):
    journal.on_chat_model_start({}, [[HumanMessage(content="synthetic")]], run_id=rid)


@pytest.mark.anyio
async def test_cancel_retains_known_partial_usage_but_not_completed_message():
    assert str(Path(__file__).resolve().parents[1]) in __import__("deerflow.runtime.journal", fromlist=["x"]).__file__
    journal = RunJournal("r", "t", MemoryRunEventStore())
    rid = uuid4()
    start(journal, rid)
    partial = response({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    journal.on_llm_error(asyncio.CancelledError(), run_id=rid, response=partial)
    journal.on_llm_error(asyncio.CancelledError(), run_id=rid, response=partial)
    data = journal.get_completion_data()
    observed = data["usage_observation"]
    assert observed["calls_started"] == 1 and observed["calls_completed"] == 0 and observed["calls_cancelled"] == 1
    assert observed["coverage"] == "partial" and observed["known_total_tokens"] == 10
    assert data["total_input_tokens"] == 7 and data["total_output_tokens"] == 3 and data["total_tokens"] == 10
    assert data["last_ai_message"] is None
    await journal.close(flush=False)
    data = journal.get_completion_data()
    journal.on_llm_error(RuntimeError(), run_id=uuid4(), response=partial)
    start(journal, uuid4())
    assert journal.get_completion_data() == data


@pytest.mark.anyio
@pytest.mark.parametrize("usage,coverage,known", [(None, "unknown", None), ({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, "complete", 0)])
async def test_completed_zero_and_absent_usage_are_distinct(usage, coverage, known):
    journal = RunJournal("r", "t", MemoryRunEventStore())
    rid = uuid4()
    start(journal, rid)
    journal.on_llm_end(response(usage), run_id=rid)
    observed = journal.get_completion_data()["usage_observation"]
    assert observed["calls_completed"] == 1 and observed["coverage"] == coverage
    assert observed["known_total_tokens"] == known
    await journal.close(flush=False)


@pytest.mark.anyio
async def test_manager_stamps_and_persists_observation_without_losing_metadata():
    store = MemoryRunStore()
    manager = RunManager(store=store)
    metadata = {"deerflow_trace_id": "trace", "replay_kind": "regenerate", KEY: {"coverage": "complete", "known_total_tokens": 999}}
    record = await manager.create("t", metadata=metadata)
    assert record.metadata[KEY]["coverage"] == "unknown" and record.metadata[KEY]["finalized"] is False
    assert metadata[KEY]["known_total_tokens"] == 999
    await manager.set_status(record.run_id, RunStatus.running)
    journal = RunJournal(record.run_id, "t", MemoryRunEventStore())
    rid = uuid4()
    start(journal, rid)
    journal.on_llm_error(asyncio.CancelledError(), run_id=rid, response=response())
    await manager.update_run_progress(record.run_id, **journal.get_completion_data())
    await manager.set_status(record.run_id, RunStatus.interrupted, persist=False)
    await manager.update_finalizing_progress(record.run_id, **journal.get_completion_data())
    await manager.persist_current_status(record.run_id)
    await manager.update_run_completion(record.run_id, status="interrupted", **journal.get_completion_data())
    row = await store.get(record.run_id)
    assert row["metadata"][KEY]["finalized"] is True and row["metadata"][KEY]["calls_cancelled"] == 1
    assert row["metadata"]["deerflow_trace_id"] == "trace" and row["metadata"]["replay_kind"] == "regenerate"
    assert row["metadata"][KEY]["known_total_tokens"] is None
    await journal.close(flush=False)


@pytest.mark.anyio
async def test_zero_replay_partial_completion_and_external_usage_are_not_double_counted():
    journal = RunJournal("r", "t", MemoryRunEventStore())
    rid = uuid4()
    start(journal, rid)
    journal.on_llm_end(response({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}), run_id=rid)
    full = response({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10})
    journal.on_llm_end(full, run_id=rid)
    journal.on_llm_end(full, run_id=rid)
    second = uuid4()
    start(journal, second)
    journal.on_llm_error(asyncio.CancelledError(), run_id=second, response=full)
    journal.on_llm_end(response({"input_tokens": 8, "output_tokens": 4, "total_tokens": 12}), run_id=second)
    external = {"source_run_id": "external", "caller": "subagent:x", "input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
    journal.record_external_llm_usage_records([external, external])
    data = journal.get_completion_data()
    assert data["total_tokens"] == data["usage_observation"]["known_total_tokens"] == 27
    assert data["usage_observation"]["calls_completed"] == 2 and data["usage_observation"]["calls_started"] == 2
    assert data["usage_observation"]["external_usage_reports"] == 1
    assert data["usage_observation"]["coverage"] == "partial"
    await journal.close(flush=False)


@pytest.mark.anyio
async def test_real_async_callback_dispatcher_keeps_cancelled_unknown_and_disabled_distinct():
    from langchain_core.callbacks.manager import AsyncCallbackManager

    for tracking in (True, False):
        journal = RunJournal("r", "t", MemoryRunEventStore(), track_token_usage=tracking)
        callback = AsyncCallbackManager(handlers=[journal])
        (run,) = await callback.on_chat_model_start({}, [[HumanMessage(content="synthetic")]])
        await run.on_llm_error(asyncio.CancelledError(), response=LLMResult(generations=[]))
        observed = journal.get_completion_data()["usage_observation"]
        assert observed["calls_started"] == 1 and observed["calls_cancelled"] == 1
        assert observed["coverage"] == "unknown" and observed["known_total_tokens"] is None
        assert ("tracking_disabled" in observed["reasons"]) == (not tracking)
        await journal.close(flush=False)


@pytest.mark.anyio
async def test_worker_cancel_persists_unknown_call_lifecycle(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from deerflow.runtime.runs.worker import RunContext, run_agent

    store = MemoryRunStore()
    manager = RunManager(store=store)
    record = await manager.create("cancel-thread")
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())

    class Agent:
        async def astream(self, graph_input, config=None, **kwargs):
            journal = config["context"]["__run_journal"]
            rid = uuid4()
            start(journal, rid)
            journal.on_llm_error(asyncio.CancelledError(), run_id=rid, response=LLMResult(generations=[]))
            raise asyncio.CancelledError()
            yield {}

    await run_agent(bridge, manager, record, ctx=RunContext(checkpointer=None, event_store=MemoryRunEventStore()), agent_factory=lambda **kwargs: Agent(), graph_input={}, config={})
    persisted = await store.get(record.run_id)
    assert persisted["status"] == "interrupted"
    observed = persisted["metadata"][KEY]
    assert observed["finalized"] and observed["coverage"] == "unknown" and observed["calls_cancelled"] == 1
    assert observed["known_total_tokens"] is None


@pytest.fixture(params=["memory", "sqlite", "postgres"])
async def observation_store(request, tmp_path):
    if request.param == "memory":
        yield MemoryRunStore()
        return
    import os

    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from deerflow.persistence.run import RunRepository
    from deerflow.persistence.run.model import RunChangeClockRow, RunRow

    cluster = None
    database = "usage_observation_" + uuid4().hex[:12]
    if request.param == "postgres":
        if not os.environ.get("PICK_TEST_PG_URL"):
            pytest.skip("PICK_TEST_PG_URL requires a disposable test PostgreSQL cluster")
        import psycopg
        from psycopg import sql

        cluster = make_url(os.environ["PICK_TEST_PG_URL"])
        with psycopg.connect(cluster.render_as_string(hide_password=False), autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        url = cluster.set(drivername="postgresql+asyncpg", database=database)
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}"
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(lambda sync: RunRow.__table__.create(sync))
            await connection.run_sync(lambda sync: RunChangeClockRow.__table__.create(sync))
        yield RunRepository(async_sessionmaker(engine, expire_on_commit=False))
    finally:
        await engine.dispose()
        if cluster:
            with psycopg.connect(cluster.render_as_string(hide_password=False), autocommit=True) as connection:
                connection.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


@pytest.mark.anyio
async def test_metadata_roundtrip_status_fence_and_owner_scope(observation_store):
    import copy

    manager = RunManager(store=observation_store)
    record = await manager.create_or_reject("scoped-thread", metadata={KEY: {"coverage": "complete"}, "trace": {"value": "keep"}}, user_id="alice")
    assert record.metadata[KEY]["coverage"] == "unknown"
    await manager.set_status(record.run_id, RunStatus.running)
    journal = RunJournal(record.run_id, record.thread_id, MemoryRunEventStore())
    rid = uuid4()
    start(journal, rid)
    journal.on_llm_error(asyncio.CancelledError(), run_id=rid, response=response({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}))
    data = journal.get_completion_data()
    await manager.update_run_progress(record.run_id, **data)
    data["usage_observation"]["known_total_tokens"] = 999
    assert (await observation_store.get(record.run_id, user_id="alice"))["metadata"][KEY]["known_total_tokens"] == 10
    await manager.set_status(record.run_id, RunStatus.interrupted, persist=False)
    await manager.update_finalizing_progress(record.run_id, **journal.get_completion_data())
    await manager.persist_current_status(record.run_id)
    await manager.update_run_completion(record.run_id, status="interrupted", **journal.get_completion_data())
    before = copy.deepcopy(await observation_store.get(record.run_id, user_id="alice"))
    assert before["metadata"][KEY]["finalized"] and before["metadata"]["trace"] == {"value": "keep"}
    assert await observation_store.get(record.run_id, user_id="bob") is None
    await observation_store.update_run_progress(record.run_id, metadata={KEY: {"finalized": False}})
    assert (await observation_store.get(record.run_id, user_id="alice"))["metadata"] == before["metadata"]
    record.ownership_lost = True
    await manager.update_run_completion(record.run_id, status="interrupted", **data)
    await manager.update_finalizing_progress(record.run_id, **data)
    assert (await observation_store.get(record.run_id, user_id="alice"))["metadata"] == before["metadata"]
    await journal.close(flush=False)


@pytest.mark.anyio
async def test_real_local_stream_delivers_partial_usage_on_cancellation():
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessageChunk
    from langchain_core.outputs import ChatGenerationChunk

    class LocalCancelledModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "synthetic-cancelled"

        def _generate(self, *args, **kwargs):
            raise NotImplementedError

        async def _astream(self, *args, **kwargs):
            yield ChatGenerationChunk(message=AIMessageChunk(content="synthetic", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}))
            raise asyncio.CancelledError()

    journal = RunJournal("r", "t", MemoryRunEventStore())
    with pytest.raises(asyncio.CancelledError):
        async for _ in LocalCancelledModel().astream([HumanMessage(content="synthetic")], config={"callbacks": [journal]}):
            pass
    observed = journal.get_completion_data()["usage_observation"]
    assert observed["calls_started"] == observed["calls_cancelled"] == 1
    assert observed["known_total_tokens"] == 10 and observed["coverage"] == "partial"
    assert journal.get_completion_data()["last_ai_message"] is None
    await journal.close(flush=False)


@pytest.mark.anyio
async def test_empty_completion_schedules_observation_and_legacy_hydration_stays_unknown():
    snapshots = []

    async def reporter(snapshot):
        snapshots.append(snapshot)

    journal = RunJournal("r", "t", MemoryRunEventStore(), progress_reporter=reporter, progress_flush_interval=0)
    rid = uuid4()
    start(journal, rid)
    await journal.flush()
    journal.on_llm_end(LLMResult(generations=[]), run_id=rid)
    await journal.flush()
    assert snapshots[-1]["usage_observation"]["calls_completed"] == 1
    assert snapshots[-1]["usage_observation"]["coverage"] == "unknown"
    store = MemoryRunStore()
    await store.put("legacy", thread_id="t", status="interrupted", metadata={"trace": "old"})
    record = await RunManager(store=store).get("legacy")
    assert KEY not in record.metadata
    await journal.close(flush=False)
