"""The pick entrypoint's JSON body sanitizer (plan 6.7): NUL and lone surrogates never reach a route."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.gateway import json_body_sanitizer as sanitizer
from app.gateway.json_body_sanitizer import JsonBodySanitizer, sanitize_json_body

DISCONNECT = {"type": "http.disconnect"}
RESPONSE = [
    {"type": "http.response.start", "status": 200, "headers": []},
    {"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True},
    {"type": "http.response.body", "body": b"data: 2\n\n"},
]


def http_scope(content_type: str | None = "application/json", length: int | None = None) -> dict:
    headers = [] if content_type is None else [(b"content-type", content_type.encode())]
    if length is not None:
        headers.append((b"content-length", str(length).encode()))
    return {"type": "http", "method": "POST", "path": "/api/threads/t/runs/stream", "headers": headers}


def request(body: bytes, more: bool = False) -> dict:
    return {"type": "http.request", "body": body, "more_body": more}


def run(scope: dict, messages: list[dict], **options) -> tuple[dict, list[dict]]:
    """Serve one request through the sanitizer to a recording app that streams RESPONSE back."""
    incoming = iter(messages)
    seen = {}

    async def receive():
        return next(incoming, DISCONNECT)

    async def app(inner_scope, inner_receive, send):
        seen["scope"] = inner_scope
        received = []
        while True:
            message = await inner_receive()
            received.append(message)
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        seen["received"] = received
        seen["after"] = await inner_receive()
        for message in RESPONSE:
            await send(message)

    sent = []

    async def send(message):
        sent.append(message)

    asyncio.run(JsonBodySanitizer(app, **options)(scope, receive, send))
    return seen, sent


def body_of(seen: dict) -> bytes:
    return b"".join(message.get("body", b"") for message in seen["received"])


def content_length(scope: dict) -> bytes | None:
    return next((value for name, value in scope["headers"] if name == b"content-length"), None)


def test_a_nul_escape_becomes_the_replacement_character_and_content_length_follows():
    raw = json.dumps({"messages": [{"content": "a\x00b"}]}).encode()
    assert b"\\u0000" in raw
    seen, sent = run(http_scope(length=len(raw)), [request(raw)])
    body = body_of(seen)
    assert json.loads(body) == {"messages": [{"content": "a\ufffdb"}]}
    assert content_length(seen["scope"]) == str(len(body)).encode()
    assert seen["received"][-1]["more_body"] is False
    assert sent == RESPONSE


def test_lone_surrogate_escapes_are_replaced_and_a_valid_pair_is_kept():
    raw = b'{"high":"x\\ud800y","low":"\\uDC00","pair":"\\ud83d\\ude00","reversed":"\\ude00\\ud83d"}'
    seen, _ = run(http_scope(), [request(raw)])
    assert json.loads(body_of(seen)) == {"high": "x\ufffdy", "low": "\ufffd", "pair": "\U0001f600", "reversed": "\ufffd\ufffd"}


@pytest.mark.parametrize("escape", [b"\\uD800", b"\\uDBFF", b"\\uDC00", b"\\udfff"])
def test_each_lone_surrogate_escape_alone_is_replaced(escape):
    # The byte test sees each on its own: either case, both halves, both ends of each half.
    raw = b'{"content":"x' + escape + b'y"}'
    seen, _ = run(http_scope(), [request(raw)])
    assert json.loads(body_of(seen)) == {"content": "x\ufffdy"}


def test_surrogates_encoded_directly_as_utf8_bytes_are_replaced():
    # json.loads decodes bytes with surrogatepass, so these reach a route as a lone surrogate too
    raw = b'{"content":"x\xed\xa0\x80y"}'
    assert json.loads(raw) == {"content": "x\ud800y"}
    seen, _ = run(http_scope(), [request(raw)])
    assert json.loads(body_of(seen)) == {"content": "x\ufffdy"}


@pytest.mark.parametrize("encoded", [b"\xed\xa0\x80", b"\xed\xaf\xbf", b"\xed\xb0\x80", b"\xed\xbf\xbf"])
def test_every_surrogate_encoded_directly_as_utf8_is_replaced(encoded):
    # High halves are ED A0-AF, low halves ED B0-BF.
    raw = b'{"content":"x' + encoded + b'y"}'
    seen, _ = run(http_scope(), [request(raw)])
    assert json.loads(body_of(seen)) == {"content": "x\ufffdy"}


def test_utf16_and_utf32_bodies_are_sanitized_too():
    # json.loads (and so FastAPI) detects UTF-16 and UTF-32 from the bytes, where the UTF-8 byte test never matches
    for codec in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le"):
        raw = json.dumps({"content": "a\x00b\ud800c"}).encode(codec)
        seen, _ = run(http_scope(length=len(raw)), [request(raw)])
        body = body_of(seen)
        assert json.loads(body) == {"content": "a\ufffdb\ufffdc"}, codec
        assert content_length(seen["scope"]) == str(len(body)).encode(), codec
    clean = json.dumps({"content": "ok"}).encode("utf-16")
    assert sanitize_json_body(clean) is clean


def test_an_escaped_backslash_is_literal_text_and_the_body_is_untouched():
    raw = b'{"content":"C:\\\\u0000 and \\\\ud800"}'
    assert json.loads(raw) == {"content": "C:\\u0000 and \\ud800"}
    seen, _ = run(http_scope(length=len(raw)), [request(raw)])
    assert body_of(seen) == raw
    assert content_length(seen["scope"]) == str(len(raw)).encode()


def test_keys_and_nested_values_are_replaced_and_other_values_kept():
    raw = b'{"k\\u0000":[{"x":["\\ud800",1,2.5,null,true,false]}],"clean":"ok"}'
    seen, _ = run(http_scope(), [request(raw)])
    assert json.loads(body_of(seen)) == {"k\ufffd": [{"x": ["\ufffd", 1, 2.5, None, True, False]}], "clean": "ok"}


def test_json_media_types_are_matched_case_insensitively_with_parameters():
    raw = b'{"content":"\\u0000"}'
    for content_type in ("Application/JSON", "application/json; charset=utf-8", "application/vnd.api+json"):
        seen, _ = run(http_scope(content_type), [request(raw)])
        assert json.loads(body_of(seen)) == {"content": "\ufffd"}, content_type


def test_other_bodies_pass_through_untouched():
    raw = b'{"content":"\\u0000"}'
    for content_type in ("multipart/form-data; boundary=x", "text/plain", "application/jsonl", None):
        seen, _ = run(http_scope(content_type, length=len(raw)), [request(raw)])
        assert body_of(seen) == raw, content_type
        assert content_length(seen["scope"]) == str(len(raw)).encode()


def test_a_body_that_is_not_json_is_left_for_the_route_to_reject():
    for raw in (b'{"content":"\\u0000"', b'{"content":"\\u0000"} trailing', b'{"content":"\xff\\u0000"}'):
        seen, _ = run(http_scope(), [request(raw)])
        assert body_of(seen) == raw


def test_a_body_too_deep_even_to_parse_is_left_for_the_route():
    raw = b"[" * 100_000 + b'"\\u0000"' + b"]" * 100_000
    assert sanitize_json_body(raw) is raw


def test_a_body_the_route_parses_but_too_deep_to_walk_is_refused():
    # json.loads (C) parses thousands of levels; the replacement walk stops near the recursion limit
    raw = b"[" * 2_000 + b'"\\u0000"' + b"]" * 2_000
    assert json.loads(raw)
    called = []

    async def app(scope, receive, send):
        called.append(scope)

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return request(raw)

    asyncio.run(JsonBodySanitizer(app)(http_scope(), receive, send))
    assert called == []
    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 400
    assert json.loads(sent[1]["body"]) == {"detail": "JSON 请求体嵌套过深"}


def test_a_clean_body_is_the_same_bytes():
    raw = b'{"content":"\xe4\xbd\xa0\xe5\xa5\xbd \\u4f60\\n","n":1}'
    assert sanitize_json_body(raw) is raw


def test_a_clean_body_is_never_parsed(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a body without escapes or surrogate bytes was parsed")

    monkeypatch.setattr(sanitizer, "json", SimpleNamespace(loads=forbidden, dumps=json.dumps, detect_encoding=json.detect_encoding))
    raw = b'{"content":"\xe4\xbd\xa0\xe5\xa5\xbd \\u4f60\\n","n":1}'
    assert sanitize_json_body(raw) is raw


def test_a_body_in_chunks_is_joined_before_it_is_sanitized():
    raw = json.dumps({"content": "a\x00" + "b" * 100}).encode()
    chunks = [raw[:7], raw[7:12], raw[12:]]
    seen, _ = run(http_scope(), [request(chunks[0], True), request(chunks[1], True), request(chunks[2])])
    assert len(seen["received"]) == 1
    assert json.loads(body_of(seen)) == {"content": "a\ufffd" + "b" * 100}


def test_a_body_over_the_limit_passes_through_chunk_by_chunk():
    raw = json.dumps({"content": "a\x00" + "b" * 100}).encode()
    chunks = [request(raw[:40], True), request(raw[40:80], True), request(raw[80:])]
    seen, _ = run(http_scope(length=len(raw)), chunks, max_bytes=50)
    assert seen["received"] == chunks
    assert body_of(seen) == raw
    assert content_length(seen["scope"]) == str(len(raw)).encode()


def test_reading_stops_once_the_limit_is_passed():
    # The chunk that passes the limit is the last one buffered; the rest reach the app as the client sends them.
    chunks = [request(b'{"a":"' + b"x" * 34, True), request(b"x" * 40, True), request(b"x" * 40, True), request(b'"}')]
    delivered, seen = [], {}
    incoming = iter(chunks)

    async def receive():
        message = next(incoming, DISCONNECT)
        delivered.append(message)
        return message

    async def app(scope, inner_receive, send):
        seen["read_before_app"] = len(delivered)
        received = []
        while True:
            message = await inner_receive()
            received.append(message)
            if not message.get("more_body", False):
                break
        seen["received"] = received

    asyncio.run(JsonBodySanitizer(app, max_bytes=50)(http_scope(), receive, None))
    assert seen["read_before_app"] == 2
    assert seen["received"] == chunks


def test_a_body_over_the_limit_in_one_chunk_passes_through_untouched():
    raw = json.dumps({"content": "a\x00" + "b" * 100}).encode()
    seen, _ = run(http_scope(length=len(raw)), [request(raw)], max_bytes=50)
    assert body_of(seen) == raw


def test_a_client_that_disconnects_mid_body_is_handed_over_as_it_arrived():
    first = request(b'{"content":"\\u0000', True)
    seen, _ = run(http_scope(), [first, DISCONNECT])
    assert seen["received"] == [first, DISCONNECT]


def test_after_the_body_the_route_still_hears_the_client_disconnect():
    seen, _ = run(http_scope(), [request(b'{"content":"\\u0000"}')])
    assert seen["after"] == DISCONNECT


def test_non_http_scopes_are_not_touched():
    calls = []

    async def app(scope, receive, send):
        calls.append((scope, receive, send))

    async def receive():
        return {"type": "lifespan.startup"}

    async def send(message):
        return None

    for scope in ({"type": "lifespan"}, {"type": "websocket", "headers": [(b"content-type", b"application/json")]}):
        asyncio.run(JsonBodySanitizer(app)(scope, receive, send))
        assert calls[-1] == (scope, receive, send)
