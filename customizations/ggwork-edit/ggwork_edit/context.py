"""Task-local access for conversation adapters, without process-global owners."""

from contextvars import ContextVar

editing_service: ContextVar[object | None] = ContextVar("editing_service", default=None)
