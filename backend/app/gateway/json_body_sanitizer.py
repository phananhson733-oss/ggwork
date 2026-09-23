"""Replace text the database drivers cannot store in JSON request bodies (pick workbench plan 6.7).

PostgreSQL refuses NUL in text and jsonb, and asyncpg, psycopg and sqlite3 encode strictly, so a lone surrogate
fails the write too. Python's JSON decoder makes one of an unpaired "\\ud800" escape, and ``json.loads`` decodes
bytes with ``surrogatepass``, so surrogates sent as raw UTF-8 bytes arrive the same way. Such a message would be
accepted by the route and then fail when the host stores it. The pick entrypoint serves the gateway behind this
middleware, which swaps both for U+FFFD before any route parses the body.
"""

from __future__ import annotations

import json
import re

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Larger bodies pass through untouched: chat messages and pick requests are far smaller, and buffering an
# unbounded body would hold it all in memory.
MAX_JSON_BODY_BYTES = 8 * 1024 * 1024
REPLACEMENT_CHARACTER = "\ufffd"
# Cheap test on the raw bytes before parsing: a \u0000 or \uD800-\uDFFF escape (paired or not, possibly behind
# an escaped backslash; the parse decides), or a surrogate encoded directly as UTF-8.
_MAYBE_UNSTORABLE = re.compile(rb"\\u(?:0000|[dD][89a-fA-F][0-9a-fA-F]{2})|\xed[\xa0-\xbf][\x80-\xbf]")
# A valid pair decodes to one code point above U+FFFF, so only lone surrogates match here.
_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")


def _is_json(scope: Scope) -> bool:
    media_type = Headers(scope=scope).get("content-type", "").split(";", 1)[0].strip().lower()
    return media_type == "application/json" or media_type.endswith("+json")


def _storable(value: object) -> tuple[object, bool]:
    """A copy with NUL and lone surrogates replaced in strings and dict keys, and whether anything changed."""
    if isinstance(value, str):
        clean = _UNSTORABLE.sub(REPLACEMENT_CHARACTER, value)
        return clean, clean != value
    if isinstance(value, list):
        items = [_storable(item) for item in value]
        return [item for item, _ in items], any(changed for _, changed in items)
    if isinstance(value, dict):
        pairs = [(_storable(key), _storable(item)) for key, item in value.items()]
        return {key: item for (key, _), (item, _) in pairs}, any(key_changed or item_changed for (_, key_changed), (_, item_changed) in pairs)
    return value, False


def sanitize_json_body(body: bytes) -> bytes:
    """The body with NUL and lone surrogates replaced; the same object when it has none or is not JSON."""
    if not _MAYBE_UNSTORABLE.search(body):
        return body
    try:
        clean, changed = _storable(json.loads(body))
    except (ValueError, RecursionError):
        # Not JSON, not UTF-8, or nested too deep to walk: the route's own parser answers it.
        return body
    return json.dumps(clean, ensure_ascii=False).encode() if changed else body


def _with_content_length(headers: list[tuple[bytes, bytes]], length: int) -> list[tuple[bytes, bytes]]:
    return [(name, str(length).encode() if name == b"content-length" else value) for name, value in headers]


def _replay(messages: list[Message], receive: Receive) -> Receive:
    """Hand over the buffered messages first, then the client's own (a later http.disconnect, for instance)."""
    queued = iter(messages)

    async def replayed() -> Message:
        return next(queued, None) or await receive()

    return replayed


class JsonBodySanitizer:
    """Pure ASGI, so responses (SSE included) and non-JSON requests such as uploads stream through untouched."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_JSON_BODY_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_json(scope):
            await self.app(scope, receive, send)
            return
        messages: list[Message] = []
        size = 0
        while True:
            message = await receive()
            messages = [*messages, message]
            if message["type"] != "http.request":
                break
            size += len(message.get("body", b""))
            if not message.get("more_body", False) or size > self.max_bytes:
                break
        last = messages[-1]
        if last["type"] == "http.request" and not last.get("more_body", False) and size <= self.max_bytes:
            body = b"".join(message.get("body", b"") for message in messages)
            clean = sanitize_json_body(body)
            if clean is not body:
                scope = {**scope, "headers": _with_content_length(scope["headers"], len(clean))}
            messages = [{"type": "http.request", "body": clean, "more_body": False}]
        await self.app(scope, _replay(messages, receive), send)
