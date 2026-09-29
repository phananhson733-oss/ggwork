"""Feishu contracts: custom-bot signing and delivery, docx/wiki reading and error mapping."""

import json

import httpx
import pytest

from deerflow.capabilities import providers_feishu
from deerflow.capabilities.business import BusinessClient
from deerflow.capabilities.providers_feishu import feishu_sign, parse_feishu_link

TOKEN = "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
SECRET = "demo-secret"
BOT = {"webhook_token": TOKEN, "sign_secret": SECRET}
APP = {"app_id": "cli_a1b2c3d4e5f6a7b8", "app_secret": "Xy7_app-secret"}
TENANT = "t-tenant-access-token"
DOC = "doxcnAbCdEf123456789"
WIKI = "wikcnAbCdEf123456789"


def test_feishu_signature_matches_official_algorithm():
    # Computed with the gen_sign() sample from the Feishu custom-bot guide:
    # HMAC-SHA256 keyed by f"{timestamp}\n{secret}" over an empty message, base64.
    assert feishu_sign("1599360473", "demo-secret") == "3/MaVZ8JLIy4TUG+7KSFJqvUkTKd+HWY8g+56DZWq8s="


async def send(handler, *args, **kwargs):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await BusinessClient("feishu-bot", BOT, http).send_message(*args, **kwargs)


@pytest.mark.asyncio
async def test_feishu_bot_text_wire_contract(monkeypatch):
    monkeypatch.setattr(providers_feishu.time, "time", lambda: 1599360473.9)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"StatusCode": 0, "StatusMessage": "success", "code": 0, "data": {}, "msg": "success"})

    assert await send(handle, "hello 飞书") == {"sent": True}
    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == f"https://open.feishu.cn/open-apis/bot/v2/hook/{TOKEN}"
    assert request.headers["content-type"].startswith("application/json")
    body = json.loads(request.content)
    assert body == {"timestamp": "1599360473", "sign": "3/MaVZ8JLIy4TUG+7KSFJqvUkTKd+HWY8g+56DZWq8s=", "msg_type": "text", "content": {"text": "hello 飞书"}}


@pytest.mark.asyncio
async def test_feishu_bot_markdown_is_card_with_title_header():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})

    assert await send(handle, "**done**", "markdown", "Daily report") == {"sent": True}
    body = json.loads(requests[0].content)
    assert body["msg_type"] == "interactive"
    assert "content" not in body
    card = body["card"]
    assert card["header"]["title"] == {"tag": "plain_text", "content": "Daily report"}
    assert {"tag": "markdown", "content": "**done**"} in card["elements"]
    assert body["timestamp"] and body["sign"]


@pytest.mark.asyncio
async def test_feishu_bot_accepts_legacy_status_code_but_prefers_code():
    assert await send(lambda _: httpx.Response(200, json={"StatusCode": 0, "StatusMessage": "success"}), "hi") == {"sent": True}
    with pytest.raises(ValueError):
        await send(lambda _: httpx.Response(200, json={"code": 19021, "StatusCode": 0}), "hi")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,fragment",
    [
        (200, {"code": 19021, "msg": "sign match fail or timestamp is not within one hour"}, "signing secret"),
        (200, {"code": 19024, "msg": "Key Words Not Found"}, "keyword"),
        (200, {"code": 19022, "msg": "Ip Not Allowed"}, "IP"),
        (400, {"code": 9499, "msg": "Bad Request"}, "9499"),
        (200, {"code": 11232, "msg": "frequency limited"}, "11232"),
        (200, {"code": 19001, "msg": f"param invalid: incoming webhook access token invalid {TOKEN}"}, "webhook token"),
        (200, {"msg": "ok"}, "unknown"),
        (200, {"code": "0"}, "unknown"),
        (500, {"message": TOKEN}, "HTTP 500"),
        (302, {}, "HTTP 302"),
    ],
)
async def test_feishu_bot_failures_are_errors_without_credentials(status, body, fragment):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status, json=body)

    with pytest.raises(ValueError) as error:
        await send(handle, "hello")
    message = str(error.value)
    assert fragment in message
    assert TOKEN not in message and SECRET not in message and "/hook/" not in message
    assert len(calls) == 1  # never retried automatically


@pytest.mark.asyncio
async def test_feishu_bot_network_error_is_redacted_and_not_retried():
    calls = []

    def broken(request):
        calls.append(request)
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    with pytest.raises(ValueError) as error:
        await send(broken, "hello")
    assert "delivery may be unknown" in str(error.value)
    assert TOKEN not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args",
    [("",), ("   ",), ("x" * 16_001,), ("好" * 5_334,), ("hi", "markdown", ""), ("hi", "markdown", "t" * 101), ("hi", "card")],
)
async def test_feishu_bot_limits_are_checked_before_network(args):
    calls = []
    with pytest.raises(ValueError):
        await send(lambda request: calls.append(request) or httpx.Response(200, json={"code": 0}), *args)
    assert calls == []


@pytest.mark.parametrize(
    "link,expected",
    [
        (f"https://acme.feishu.cn/docx/{DOC}", ("docx", DOC)),
        (f"https://acme.feishu.cn/docx/{DOC}/", ("docx", DOC)),
        (f"https://acme.feishu.cn/wiki/{WIKI}?from=from_copylink#share", ("wiki", WIKI)),
        (f"http://ACME.Feishu.cn/wiki/{WIKI}", ("wiki", WIKI)),
        (f"acme.feishu.cn/docx/{DOC}?x=1", ("docx", DOC)),
        (f"https://feishu.cn/docx/{DOC}", ("docx", DOC)),
        (f"https://acme.larkoffice.com/wiki/{WIKI}", ("wiki", WIKI)),
        (f"  {DOC} ", ("docx", DOC)),
    ],
)
def test_parse_feishu_link(link, expected):
    assert parse_feishu_link(link) == expected


@pytest.mark.parametrize(
    "link,fragment",
    [
        (f"https://acme.larksuite.com/docx/{DOC}", "larksuite.com"),
        (f"https://evil.example/docx/{DOC}", "feishu.cn"),
        (f"https://acme.feishu.cn.evil.example/docx/{DOC}", "feishu.cn"),
        (f"https://acme.larkoffice.com.evil.example/docx/{DOC}", "feishu.cn"),
        (f"https://acme.feishu.cn/sheets/{DOC}", "not supported yet"),
        (f"https://acme.feishu.cn/base/{DOC}", "not supported yet"),
        (f"https://acme.feishu.cn/docs/{DOC}", "not supported yet"),
        ("https://acme.feishu.cn/drive/folder/fldcnAbCdEf123", "/docx/"),
        ("https://acme.feishu.cn/docx/abc%2F..%2F", "/docx/"),
        ("../../etc/passwd", "Feishu"),
        ("", "Feishu"),
    ],
)
def test_parse_feishu_link_rejections(link, fragment):
    with pytest.raises(ValueError) as error:
        parse_feishu_link(link)
    assert fragment in str(error.value)


def feishu_api(overrides=None):
    """Simulated Open Platform keyed by path; records requests."""
    requests = []
    routes = {
        "/open-apis/auth/v3/tenant_access_token/internal": (200, {"code": 0, "msg": "ok", "tenant_access_token": TENANT, "expire": 7200}),
        f"/open-apis/docx/v1/documents/{DOC}": (200, {"code": 0, "msg": "success", "data": {"document": {"document_id": DOC, "revision_id": 3, "title": "Plan"}}}),
        f"/open-apis/docx/v1/documents/{DOC}/raw_content": (200, {"code": 0, "msg": "success", "data": {"content": "Hello Feishu"}}),
        **(overrides or {}),
    }

    def handle(request):
        requests.append(request)
        status, body = routes[request.url.path]
        return httpx.Response(status, json=body)

    return requests, handle


async def read(handler, link, offset=0, max_chars=20_000):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await BusinessClient("feishu-docs", APP, http).read_document(link, offset, max_chars)


@pytest.mark.asyncio
async def test_feishu_docx_read_wire_contract():
    requests, handle = feishu_api()
    result = await read(handle, f"https://acme.feishu.cn/docx/{DOC}?from=share")
    assert result == {"title": "Plan", "document_id": DOC, "content": "Hello Feishu", "offset": 0, "total_chars": 12, "truncated": False, "next_offset": None}
    token_request, *api_requests = requests
    assert token_request.method == "POST" and str(token_request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    assert json.loads(token_request.content) == APP
    assert [(item.method, item.url.host) for item in api_requests] == [("GET", "open.feishu.cn")] * 2
    assert all(item.headers["Authorization"] == f"Bearer {TENANT}" for item in api_requests)


@pytest.mark.asyncio
async def test_feishu_wiki_resolves_to_docx():
    node = {"code": 0, "data": {"node": {"node_token": WIKI, "obj_type": "docx", "obj_token": DOC, "title": "Wiki page"}}}
    requests, handle = feishu_api({"/open-apis/wiki/v2/spaces/get_node": (200, node)})
    result = await read(handle, f"https://acme.feishu.cn/wiki/{WIKI}")
    assert result["document_id"] == DOC and result["content"] == "Hello Feishu"
    wiki_request = requests[1]
    assert wiki_request.url.path == "/open-apis/wiki/v2/spaces/get_node"
    assert wiki_request.url.params["token"] == WIKI
    assert wiki_request.url.params.get("obj_type", "wiki") == "wiki"


@pytest.mark.asyncio
@pytest.mark.parametrize("obj_type", ["sheet", "bitable", "doc", "mindnote", "file", "slides", "something-new"])
async def test_feishu_wiki_non_docx_is_not_supported(obj_type):
    node = {"code": 0, "data": {"node": {"obj_type": obj_type, "obj_token": "shtcnAbCdEf123456789"}}}
    requests, handle = feishu_api({"/open-apis/wiki/v2/spaces/get_node": (200, node)})
    with pytest.raises(ValueError) as error:
        await read(handle, f"https://acme.feishu.cn/wiki/{WIKI}")
    assert "not supported yet" in str(error.value)
    assert len(requests) == 2  # token + node, never the docx endpoints


@pytest.mark.asyncio
async def test_feishu_wiki_rejects_unsafe_object_token():
    node = {"code": 0, "data": {"node": {"obj_type": "docx", "obj_token": "../../auth"}}}
    requests, handle = feishu_api({"/open-apis/wiki/v2/spaces/get_node": (200, node)})
    with pytest.raises(ValueError):
        await read(handle, f"https://acme.feishu.cn/wiki/{WIKI}")
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,status,code,fragments",
    [
        (f"/open-apis/docx/v1/documents/{DOC}", 403, 1770032, ["Add Document App", "wiki space"]),
        ("/open-apis/wiki/v2/spaces/get_node", 400, 131006, ["Add Document App", "wiki space"]),
        (f"/open-apis/docx/v1/documents/{DOC}", 400, 99991672, ["docx:document:readonly", "wiki:wiki:readonly", "publish"]),
        (f"/open-apis/docx/v1/documents/{DOC}/raw_content", 400, 99991679, ["docx:document:readonly", "publish"]),
        (f"/open-apis/docx/v1/documents/{DOC}", 404, 1770002, ["not found"]),
        ("/open-apis/wiki/v2/spaces/get_node", 400, 131005, ["not found"]),
        (f"/open-apis/docx/v1/documents/{DOC}/raw_content", 400, 99991400, ["rate"]),
        (f"/open-apis/docx/v1/documents/{DOC}/raw_content", 500, 1771001, ["1771001"]),
    ],
)
async def test_feishu_errors_are_actionable_and_redacted(path, status, code, fragments):
    link = f"https://acme.feishu.cn/wiki/{WIKI}" if "wiki" in path else DOC
    overrides = {path: (status, {"code": code, "msg": f"denied {APP['app_secret']} {TENANT}", "data": {}})}
    if "wiki" not in path:
        overrides["/open-apis/wiki/v2/spaces/get_node"] = (200, {"code": 0, "data": {"node": {"obj_type": "docx", "obj_token": DOC}}})
    _, handle = feishu_api(overrides)
    with pytest.raises(ValueError) as error:
        await read(handle, link)
    message = str(error.value)
    assert all(fragment in message for fragment in fragments), message
    assert APP["app_secret"] not in message and TENANT not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(200, {"code": 10014, "msg": "app secret invalid"}), (400, {"code": 10003, "msg": "invalid param"}), (200, {"code": 0}), (502, {})])
async def test_feishu_rejected_app_credentials(status, body):
    requests, handle = feishu_api({"/open-apis/auth/v3/tenant_access_token/internal": (status, body)})
    with pytest.raises(ValueError) as error:
        await read(handle, DOC)
    assert "App ID" in str(error.value) or "HTTP 502" in str(error.value)
    assert APP["app_secret"] not in str(error.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_feishu_pagination_slices_text():
    content = "飞书文档内容abcdefghij"
    _, handle = feishu_api({f"/open-apis/docx/v1/documents/{DOC}/raw_content": (200, {"code": 0, "data": {"content": content}})})
    first = await read(handle, DOC, 0, 6)
    assert first == {"title": "Plan", "document_id": DOC, "content": "飞书文档内容", "offset": 0, "total_chars": 16, "truncated": True, "next_offset": 6}
    last = await read(handle, DOC, 14, 6)
    assert (last["content"], last["truncated"], last["next_offset"]) == ("ij", False, None)
    end = await read(handle, DOC, 16, 6)
    assert end["content"] == "" and end["truncated"] is False
    with pytest.raises(ValueError):
        await read(handle, DOC, 17, 6)


@pytest.mark.asyncio
@pytest.mark.parametrize("offset,max_chars", [(-1, 10), (0, 0), (0, 50_001)])
async def test_feishu_page_bounds_checked_before_network(offset, max_chars):
    requests, handle = feishu_api()
    with pytest.raises(ValueError):
        await read(handle, DOC, offset, max_chars)
    assert requests == []


@pytest.mark.asyncio
async def test_feishu_invalid_document_payload_is_error():
    _, handle = feishu_api({f"/open-apis/docx/v1/documents/{DOC}/raw_content": (200, {"code": 0, "data": {"content": 42}})})
    with pytest.raises(ValueError):
        await read(handle, DOC)
