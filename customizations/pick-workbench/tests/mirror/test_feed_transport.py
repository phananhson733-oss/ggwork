"""The feed client's configuration, transport, secrets and metrics (P2-2a; plan 5.1, 5.5, P1-4, P1-6).

The RealShort side is tests/mirror/fake_realshort.py, shaped after rs 816ca2e; each test names the rs line it leans on.
"""

import asyncio
import json
import logging
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fake_realshort import BYPASS, EXPORT_TOKEN, FEED_TOKEN, ROW_SENTINEL, Clock, busy, v1_error, v2_error
from mirror_harness import BASE, collect, make_client, world

from ggwork_pick.mirror.client import FeedClient
from ggwork_pick.mirror.errors import ConfigError, ContractError, FeedConnectionError, FeedError

# --- configuration and transport -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_redirect_follow():
    fake, clock = world(bypass=BYPASS)
    async with make_client(fake, clock, bypass="wrong-bypass-value") as client:
        with pytest.raises(ConfigError) as caught:
            await client.manifest_when_free()
    assert fake.raw_paths == ["/api/pick-feed/v2/manifest"] and fake.calls == []
    assert caught.value.status == 307 and "sso" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (v2_error(401, "unauthorized"), ConfigError),
        (v2_error(404, "not_found"), ConfigError),
        ((418, {}, b"teapot"), FeedError),
        ((500, {}, b"<html>boom</html>"), FeedError),
        ((200, {}, b"<html>maintenance</html>"), FeedError),
        ((503, {}, b"<html>vercel</html>"), FeedError),
    ],
    ids=["401", "404", "418", "500-other", "200-not-json", "503-html"],
)
async def test_other_statuses(reply, error):
    fake, clock = world(intercept=lambda call: reply)
    async with make_client(fake, clock) as client:
        with pytest.raises(error) as caught:
            await client.manifest_when_free()
    assert type(caught.value) is error or error is ConfigError
    assert str(reply[0]) in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "says", "not_says"),
    [
        ((401, {"content-type": "text/html"}, b"<html>Authentication Required</html>"), "bypass", "token 不对"),
        (v1_error(401, "unauthorized"), "bypass", "token 不对"),
        (v2_error(401, "unauthorized"), "token 不对", "bypass"),
        ((404, {"content-type": "text/html"}, b"<html>404</html>"), "地址", "token"),
        (v2_error(404, "not_found"), "token", "地址"),
    ],
    ids=["401-html", "401-v1-body-on-v2", "401-realshort", "404-html", "404-realshort"],
)
async def test_401_and_404_say_whose_answer_it_was(reply, says, not_says):
    # rs:src/lib/pick/feed-http.ts:48-53: RealShort's own 401/404 are JSON with the fixed word, and v2's carry version.
    fake, clock = world(intercept=lambda call: reply)
    async with make_client(fake, clock) as client:
        with pytest.raises(ConfigError) as caught:
            await client.manifest_when_free()
    assert caught.value.status == reply[0] and says in str(caught.value) and not_says not in str(caught.value)


@pytest.mark.asyncio
async def test_connection_errors_name_the_class_only():
    def refuse(request):
        raise httpx.ConnectError(f"down {request.headers['authorization']}", request=request)

    clock = Clock()
    async with FeedClient(base_url=BASE, export_token=EXPORT_TOKEN, transport=httpx.MockTransport(refuse), clock=clock, sleep=clock.sleep) as client:
        with pytest.raises(FeedError) as caught:
            await client.manifest_when_free()
    assert "ConnectError" in str(caught.value) and EXPORT_TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_one_request_has_a_total_time_limit():
    # httpx's read timeout is per socket read; plan 5.1 gives one request 60 seconds in all.
    async def trickle(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={})

    clock = Clock()
    client = FeedClient(base_url=BASE, export_token=EXPORT_TOKEN, transport=httpx.MockTransport(trickle), clock=clock, sleep=clock.sleep, request_seconds=0.05)
    async with client:
        with pytest.raises(FeedConnectionError) as caught:
            await client.manifest_when_free()
    assert "0.05" in str(caught.value) and caught.value.resource == "manifest"


@pytest.mark.parametrize(
    "options",
    [
        {"base_url": "https://realshort.test/api"},
        {"base_url": "https://user:pw@realshort.test"},
        {"base_url": "ftp://realshort.test"},
        {"base_url": "https://realshort.test?x=1"},
        {"export_token": " "},
        {"export_token": "two words"},
        {"bypass": "line\nbreak"},
        {"feed_token": ""},
    ],
)
def test_bad_configuration_refused(options):
    with pytest.raises(ConfigError) as caught:
        FeedClient(**{"base_url": BASE, "export_token": EXPORT_TOKEN, **options})
    assert "pw" not in str(caught.value) and "words" not in str(caught.value) and "break" not in str(caught.value)


@pytest.mark.asyncio
async def test_oversized_body_is_refused():
    fake, clock = world(intercept=lambda call: (200, {}, b"[" + b"0," * 600 + b"0]"))
    async with make_client(fake, clock, max_body_bytes=1000) as client:
        with pytest.raises(ContractError):
            await client.manifest_when_free()


@pytest.mark.asyncio
async def test_errors_never_contain_secrets(caplog):
    caplog.set_level(logging.DEBUG)
    secret_body = {"ok": False, "version": "pick-export-v2", "error": f"{ROW_SENTINEL} {EXPORT_TOKEN} {BYPASS}", "reason": ROW_SENTINEL}
    replies = [
        v2_error(401, "unauthorized"),
        v2_error(404, "not_found"),
        (500, {}, json.dumps(secret_body).encode()),
        (400, {}, json.dumps(secret_body).encode()),
        (503, {}, json.dumps(secret_body).encode()),
        (302, {"location": f"https://sso.test/?{BYPASS}"}, b""),
        (500, {}, json.dumps({**secret_body, "error": "row_too_large", "resource": ROW_SENTINEL, "key": {"v": ROW_SENTINEL}}).encode()),
    ]
    for reply in replies:
        fake, clock = world(bypass=None, intercept=lambda call, reply=reply: reply)
        async with make_client(fake, clock, bypass=BYPASS) as client:
            with pytest.raises(FeedError) as caught:
                await client.manifest_when_free()
            text = f"{caught.value} {caught.value!r} {client!r}"
        for secret in (EXPORT_TOKEN, FEED_TOKEN, BYPASS, ROW_SENTINEL):
            assert secret not in text, (reply[0], secret)
    for secret in (EXPORT_TOKEN, FEED_TOKEN, BYPASS, ROW_SENTINEL):
        assert secret not in caplog.text


@pytest.mark.asyncio
async def test_reprs_hide_the_token_and_the_rows():
    from ggwork_pick.mirror import client as client_module

    request = client_module._Request("/api/pick-feed/v2/rs_ids", {}, EXPORT_TOKEN, "rs_ids", 1, datetime(2026, 9, 23, tzinfo=UTC), "page")
    fake, clock = world(sizes={"rs_ids": 1})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        (page,) = await collect(client.pages("rs_ids", manifest=manifest))
    assert EXPORT_TOKEN not in repr(request) and "rs_ids" in repr(request)
    assert ROW_SENTINEL in json.dumps(page.body) and ROW_SENTINEL not in repr(page)


# --- metrics -------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_measure_each_response():
    fake, clock = world(sizes={"rs_ids": 2}, page_rows={"rs_ids": 1})
    original = fake._rows

    def slow(name, params, as_of):
        clock.advance(1.5)
        return original(name, params, as_of)

    fake._rows = slow
    seen = []
    async with make_client(fake, clock, on_response=seen.append) as client:
        manifest = await client.manifest_when_free()
        pages = await collect(client.pages("rs_ids", manifest=manifest))
    first = pages[0].metrics
    assert (first.resource, first.page, first.status, first.rows, first.retry_after, first.attempt) == ("rs_ids", 1, 200, 1, None, 1)
    assert first.elapsed_ms == 1500.0 and pages[1].metrics.page == 2
    assert first.bytes == len(json.dumps(pages[0].body, ensure_ascii=False, separators=(",", ":")).encode()) == first.wire_bytes
    assert manifest.metrics.resource == "manifest" and manifest.metrics.rows == 1
    assert [m.resource for m in seen] == ["manifest", "rs_ids", "rs_ids"]
    line = first.line()
    assert set(line) == {"resource", "page", "status", "elapsed_ms", "bytes", "wire_bytes", "rows", "retry_after"}


@pytest.mark.asyncio
async def test_wire_bytes_count_the_compressed_body():
    fake, clock = world(sizes={"rs_ids": 50}, compress=True)
    seen = []
    async with make_client(fake, clock, on_response=seen.append) as client:
        manifest = await client.manifest_when_free()
        await collect(client.pages("rs_ids", manifest=manifest))
    assert [(m.bytes, m.wire_bytes) for m in seen] == [(body, sent) for _, body, sent in fake.wire]
    assert all(m.wire_bytes < m.bytes for m in seen) and manifest.metrics.bytes == fake.wire[0][1]


@pytest.mark.asyncio
async def test_busy_metrics_carry_retry_after():
    fake, clock = world(intercept=lambda call: busy() if call.resource == "manifest" and call.n == 1 else None)
    seen = []
    async with make_client(fake, clock, on_response=seen.append) as client:
        await client.manifest_when_free()
    assert [(m.status, m.retry_after, m.error) for m in seen] == [(503, 60, "source_busy"), (200, None, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["\u00b2", "soon", "999999"])
async def test_odd_retry_after_falls_back_to_sixty(header):
    odd = v2_error(503, "source_busy", {"retry-after": header})
    fake, clock = world(intercept=lambda call: odd if call.resource == "manifest" and call.n == 1 else None)
    async with make_client(fake, clock) as client:
        await client.manifest_when_free()
    assert clock.sleeps == [60]


@pytest.mark.asyncio
async def test_large_bodies_are_parsed_off_the_event_loop(monkeypatch):
    # Critique 1.9: the extension runs inside the gateway's single event loop; a 3 MB json.loads must not stall it.
    from ggwork_pick.mirror import client as client_module

    threads = []
    original = client_module._parse_json

    def spy(content):
        threads.append((len(content), threading.current_thread() is threading.main_thread()))
        return original(content)

    monkeypatch.setattr(client_module, "_parse_json", spy)
    fake, clock = world(sizes={"rs_ids": 800})
    async with make_client(fake, clock) as client:
        manifest = await client.manifest_when_free()
        await collect(client.pages("rs_ids", manifest=manifest))
    small, large = threads
    assert small[0] < client_module.THREAD_PARSE_BYTES < large[0]
    assert small[1] is True and large[1] is False


def test_as_of_age_limit_leaves_margin_inside_realshorts_window():
    from ggwork_pick.mirror import client

    assert client.AS_OF_MAX_AGE == timedelta(minutes=25) < timedelta(minutes=30)
