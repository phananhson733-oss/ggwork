"""Outermost pick-only model boundary: graph writes must match host approval."""

from __future__ import annotations

from typing import TYPE_CHECKING

from deerflow_extension_api import task_store_from_runtime
from deerflow_extension_api.pick_publication import PickPublication
from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelCallResult, ModelRequest


class PickPublicationMiddleware(AgentMiddleware):
    """Publish only host-approved pick responses at the outer model boundary."""

    async def awrap_model_call(self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]) -> ModelCallResult:
        """Keep tool correlation or return the host's canonical final response."""
        store = task_store_from_runtime(request.runtime)
        gate = store.get(PickPublication) if store is not None else None
        if gate is None:
            return await handler(request)
        gate.activate()
        response = await handler(request)
        messages = getattr(response, "result", [])
        tool_messages = [message for message in messages if isinstance(message, AIMessage) and message.tool_calls]
        if tool_messages and not gate.has_final:
            return ModelResponse(result=[AIMessage(**gate.tool_message(message.model_dump())) for message in tool_messages])
        # Reuse the approved object, never response metadata that a nested
        # wrapper/provider could forge or overwrite after the checker returned.
        return ModelResponse(result=[AIMessage(**gate.incomplete())])


def with_pick_publication_boundary(middlewares: list[AgentMiddleware]) -> list[AgentMiddleware]:
    """Prepend the host gate only when a business middleware requires it."""
    if any(getattr(middleware, "requires_pick_publication", False) for middleware in middlewares):
        return [PickPublicationMiddleware(), *middlewares]
    return middlewares
