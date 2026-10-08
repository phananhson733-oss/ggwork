"""Actual PostgreSQL cancellation and HTTP disconnect, with synthetic mirror data."""

import asyncio
import json
import socket

import asyncpg
import httpx
import pytest
import test_board_fixture
from engines import host_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

common_board = test_board_fixture.board


@pytest.mark.asyncio
async def test_reader_keeps_shorter_server_sql_timeout_and_reuses_connection(common_board, pg_cluster):
    from ggwork_pick.query_reader import QueryReader

    admin = await asyncpg.connect(pg_cluster.url.set(database=common_board["info"]["database"]).render_as_string(hide_password=False))
    role = common_board["info"]["role"]
    await admin.execute(f"ALTER ROLE \"{role}\" SET statement_timeout = '70ms'")
    reader = QueryReader(common_board["reader"], ssl=False)
    try:
        async with reader.connection(deadline=asyncio.get_running_loop().time() + 2) as conn:
            with pytest.raises(asyncpg.QueryCanceledError):
                await conn.execute("SELECT pg_sleep(0.3)")
        async with reader.connection(deadline=asyncio.get_running_loop().time() + 2) as conn:
            assert await conn.fetchval("SELECT 1") == 1
        assert reader.pool.get_max_size() == 3
    finally:
        await reader.close()
        await admin.execute(f"ALTER ROLE \"{role}\" SET statement_timeout = '8s'")
        await admin.close()


@pytest.mark.asyncio
async def test_real_http_disconnect_and_timeout_cancel_locked_query_then_reuse(common_board, pg_cluster, tmp_path):
    import uvicorn
    from app.gateway.auth_middleware import AuthMiddleware
    from app.gateway.internal_auth import create_internal_auth_headers
    from app.gateway.json_body_sanitizer import JsonBodySanitizer
    from app.gateway.pick_query_budget import PickQueryBudget
    from deerflow_extension_api.auth import EXTENSION_PRINCIPAL_RESOLVER_KEY, ExtensionPrincipal
    from fastapi import FastAPI

    from ggwork_pick.query_reader import QueryReader
    from ggwork_pick.routes import build_router
    from ggwork_pick.service import PickService

    engine = host_engine(pg_cluster.async_url(common_board["info"]["database"]))
    service = PickService(tmp_path / "files")
    await service.initialize(async_sessionmaker(engine, expire_on_commit=False))
    service.query_reader = QueryReader(common_board["reader"], ssl=False)
    app = FastAPI()
    setattr(app.state, EXTENSION_PRINCIPAL_RESOLVER_KEY, lambda request: ExtensionPrincipal(request.state.user.id))
    app.add_middleware(AuthMiddleware)
    headers = {**create_internal_auth_headers(owner_user_id="alice"), "content-type": "application/json"}
    app.include_router(build_router(service))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(PickQueryBudget(JsonBodySanitizer(app)), lifespan="off", log_level="error", ws="none"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    admin = await asyncpg.connect(pg_cluster.url.set(database=common_board["info"]["database"]).render_as_string(hide_password=False))
    tx = admin.transaction()
    await tx.start()
    schema = f"pickm_v{common_board['info']['versions']['v2']:06d}"
    await admin.execute(f'LOCK TABLE "{schema}".meta IN ACCESS EXCLUSIVE MODE')
    try:
        async with asyncio.timeout(3):
            while not server.started:
                await asyncio.sleep(0.01)
        body = json.dumps({"domain": "catalog", "scope": "full_catalog"}).encode()
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        wire_headers = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        writer.write(f"POST /api/pick/query HTTP/1.1\r\nHost: localhost\r\n{wire_headers}Content-Length: {len(body)}\r\n\r\n".encode() + body)
        await writer.drain()
        # The request must reach the real read-only SQL connection before disconnect.
        async with asyncio.timeout(3):
            while not await admin.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name='ggwp-common-query' AND datname=current_database() AND wait_event_type='Lock'"
            ):
                await asyncio.sleep(0.01)
                await admin.execute("SELECT pg_stat_clear_snapshot()")
        writer.close()
        await writer.wait_closed()
        async with asyncio.timeout(1):
            while await admin.fetchval(
                "SELECT count(*) FROM pg_stat_activity WHERE application_name='ggwp-common-query' AND datname=current_database() AND state='active'"
            ):
                await asyncio.sleep(0.01)
                await admin.execute("SELECT pg_stat_clear_snapshot()")
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            timeout = await client.post("/api/pick/query", content=body, headers={**headers, "x-pick-query-budget-ms": "80"})
            assert timeout.status_code == 504, timeout.text
            assert timeout.json()["detail"]["code"] == "query_timeout"
            assert "counts" not in timeout.json()
            await tx.rollback()
            tx = None
            ok = await client.post("/api/pick/query", content=body, headers=headers)
            assert ok.status_code == 200, ok.text
            assert ok.json()["counts"]["matched"] > 0
            assert (await client.post("/api/pick/query", content=body, headers={"content-type": "application/json"})).status_code == 401
        assert service.query_reader.pool.get_max_size() == 3
    finally:
        if tx is not None:
            await tx.rollback()
        server.should_exit = True
        await serving
        sock.close()
        await service.query_reader.close()
        await admin.close()
        await engine.dispose()
