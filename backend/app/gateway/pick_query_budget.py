"""One pick-query budget starts before body parsing/auth and ends after response encoding."""

import asyncio
from contextlib import suppress

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class PickQueryBudget:
    """Bound one pick HTTP query across request parsing, execution and response encoding."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass through other traffic and bound the complete pick-query lifecycle."""
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"].rstrip("/") != "/api/pick/query":
            await self.app(scope, receive, send)
            return
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            budget_ms = max(1, min(10000, int(Headers(scope=scope).get("x-pick-query-budget-ms", "10000"))))
        except ValueError:
            budget_ms = 10000
        state = scope.setdefault("state", {})
        state["pick_query_started"] = started
        state["pick_query_deadline"] = started + budget_ms / 1000
        messages: asyncio.Queue[Message] = asyncio.Queue(maxsize=1)
        disconnected = False
        response_started = False

        async def bounded_send(message: Message) -> None:
            nonlocal response_started
            # JSON encoding is synchronous and can run past a scheduled timeout.
            if loop.time() >= state["pick_query_deadline"]:
                raise TimeoutError
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        work = asyncio.create_task(self.app(scope, messages.get, bounded_send))

        async def receive_client() -> None:
            nonlocal disconnected
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    disconnected = True
                    work.cancel()
                    return
                await messages.put(message)

        receiver = asyncio.create_task(receive_client())
        try:
            async with asyncio.timeout_at(state["pick_query_deadline"]):
                await work
        except asyncio.CancelledError:
            if not disconnected:
                raise
        except TimeoutError:
            if not disconnected and not response_started:
                await JSONResponse({"detail": {"code": "query_timeout", "message": "查询超过时限，请缩小范围后重试", "retryable": True}}, status_code=504)(scope, receive, send)
        finally:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
            if not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
