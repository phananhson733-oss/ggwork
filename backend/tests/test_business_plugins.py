"""Provider contracts: actual HTTP serialization, business errors and MCP tools."""

import json

import httpx
import pytest

from deerflow.capabilities.business import CREDENTIALS, MODULE, BusinessClient, build_server, connection_config, is_bundled_connection

NEW_PROVIDERS = {
    "feishu-bot": ({"webhook_token": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0", "sign_secret": "fixture-secret"}, {"send_message"}),
    "feishu-docs": ({"app_id": "cli_a1b2c3d4e5f6a7b8", "app_secret": "fixture_app-secret"}, {"read_document"}),
    "google-docs": ({}, {"read_document"}),
    "exa": ({"api_key": "fixture-exa-key"}, {"search"}),
    "firecrawl": ({"api_key": "fc-fixture-key"}, {"scrape"}),
}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,credentials,host",
    [
        ("dingtalk", {"access_token": "robot-token", "sign_secret": "SEC-sign"}, "oapi.dingtalk.com"),
        ("wecom", {"webhook_key": "robot-key"}, "qyapi.weixin.qq.com"),
    ],
)
async def test_robot_wire_contract(provider, credentials, host):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = BusinessClient(provider, credentials, http)
        assert await client.send_message("通知", "markdown", "日报") == {"sent": True}
    request = requests[0]
    assert request.url.host == host
    body = json.loads(request.content)
    assert body["msgtype"] == "markdown"
    if provider == "dingtalk":
        assert request.url.params["access_token"] == "robot-token"
        assert request.url.params["timestamp"] and request.url.params["sign"]
        assert body["markdown"] == {"text": "通知", "title": "日报"}
    else:
        assert request.url.params["key"] == "robot-key"
        assert body["markdown"] == {"content": "通知"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body", [(200, {"errcode": 40014, "errmsg": "secret-token"}), (401, {"message": "secret-token"}), (302, {})])
async def test_errors_never_claim_success_or_expose_response_secrets(status, body):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status, json=body))) as http:
        client = BusinessClient("wecom", {"webhook_key": "secret-token"}, http)
        with pytest.raises(ValueError) as error:
            await client.send_message("hello")
        assert "secret-token" not in str(error.value)


@pytest.mark.asyncio
async def test_hubspot_pagination_and_contact_creation():
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"id": "12"}], "paging": {"next": {"after": "next-page"}}})
        return httpx.Response(201, json={"id": "42", "properties": {"email": "person@example.test"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        client = BusinessClient("hubspot", {"access_token": "private-token"}, http)
        assert (await client.get_companies(2, "cursor"))["next_after"] == "next-page"
        assert (await client.create_contact("person@example.test", firstname="A"))["id"] == "42"
    assert requests[0].url.params["after"] == "cursor"
    assert requests[0].url.params["limit"] == "2"
    assert requests[0].headers["Authorization"] == "Bearer private-token"
    assert requests[1].url.path == "/crm/v3/objects/contacts"
    assert json.loads(requests[1].content) == {"properties": {"email": "person@example.test", "firstname": "A"}}


@pytest.mark.asyncio
async def test_mcp_discovery_does_not_need_or_expose_credentials():
    server = build_server("hubspot")
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {"get_companies", "create_contact"}
    for tool in tools:
        assert "access_token" not in json.dumps(tool.inputSchema)
        assert tool.annotations.readOnlyHint == (tool.name == "get_companies")


@pytest.mark.parametrize("provider,values", [("wecom", {}), ("dingtalk", {"access_token": "x"}), ("hubspot", {"access_token": "x", "url": "http://localhost"}), ("hubspot", {"access_token": "***"})])
def test_invalid_configuration_rejected(provider, values):
    with pytest.raises(ValueError):
        connection_config(provider, values)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,credentials,tool,arguments",
    [
        ("dingtalk", {"access_token": "fixture-token", "sign_secret": "fixture-secret"}, "send_message", {"content": ""}),
        ("wecom", {"webhook_key": "fixture-key"}, "send_message", {"content": ""}),
        ("hubspot", {"access_token": "fixture-token"}, "create_contact", {"email": "invalid"}),
        ("feishu-bot", NEW_PROVIDERS["feishu-bot"][0], "send_message", {"content": ""}),
        ("feishu-docs", NEW_PROVIDERS["feishu-docs"][0], "read_document", {"link": "https://acme.larksuite.com/docx/doxcnAbCdEf123456789"}),
        ("google-docs", {}, "read_document", {"link": "https://docs.google.com/spreadsheets/d/1mt8aYM88Jj5qkep1xYC5vj0wBlbX2u6gdxhf_puaiQI"}),
        ("exa", NEW_PROVIDERS["exa"][0], "search", {"query": "x", "num_results": 11}),
        ("firecrawl", NEW_PROVIDERS["firecrawl"][0], "scrape", {"url": "ftp://example.com/file"}),
    ],
)
async def test_exact_installed_launcher_discovers_and_rejects_invalid_calls(provider, credentials, tool, arguments):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    config = connection_config(provider, credentials)
    params = StdioServerParameters(command=config["command"], args=config["args"], env=config["env"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        discovered = await session.list_tools()
        assert tool in {item.name for item in discovered.tools}
        result = await session.call_tool(tool, arguments)
        assert result.isError
        for secret in credentials.values():
            assert secret not in result.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,credentials,tool,arguments,expected",
    [
        ("dingtalk", {"access_token": "fixture-token", "sign_secret": "fixture-secret"}, "send_message", {"content": "Report ready"}, "sent"),
        ("wecom", {"webhook_key": "fixture-key"}, "send_message", {"content": "Report ready"}, "sent"),
        ("hubspot", {"access_token": "fixture-token"}, "get_companies", {"limit": 1}, "Acme"),
        ("hubspot", {"access_token": "fixture-token"}, "create_contact", {"email": "person@example.test"}, "contact-42"),
        ("feishu-bot", NEW_PROVIDERS["feishu-bot"][0], "send_message", {"content": "Report ready", "message_type": "markdown"}, "sent"),
        ("feishu-docs", NEW_PROVIDERS["feishu-docs"][0], "read_document", {"link": "https://acme.feishu.cn/docx/doxcnAbCdEf123456789"}, "Quarterly plan body"),
        ("google-docs", {}, "read_document", {"link": "https://docs.google.com/document/d/1mt8aYM88Jj5qkep1xYC5vj0wBlbX2u6gdxhf_puaiQI/edit"}, "Shared doc body"),
        ("exa", NEW_PROVIDERS["exa"][0], "search", {"query": "short drama market"}, "exa-result-title"),
        ("firecrawl", NEW_PROVIDERS["firecrawl"][0], "scrape", {"url": "https://example.com"}, "Scraped page body"),
    ],
)
async def test_real_mcp_tool_invocation_with_simulated_provider(tmp_path, provider, credentials, tool, arguments, expected):
    """Real stdio protocol and client code; only external HTTP is simulated."""
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    script = tmp_path / "business_fixture.py"
    script.write_text(
        """import httpx
from deerflow.capabilities.business import build_server
import sys
original = httpx.AsyncClient
def handle(request):
    host, path = request.url.host, request.url.path
    if host == "api.hubapi.com":
        if request.method == "GET":
            return httpx.Response(200, json={"results": [{"id": "1", "properties": {"name": "Acme"}}]})
        return httpx.Response(201, json={"id": "contact-42", "properties": {"email": "person@example.test"}})
    if host == "open.feishu.cn" and path.startswith("/open-apis/bot/"):
        return httpx.Response(200, json={"code": 0, "msg": "success", "data": {}})
    if host == "open.feishu.cn" and path.endswith("/tenant_access_token/internal"):
        return httpx.Response(200, json={"code": 0, "msg": "ok", "tenant_access_token": "t-fixture", "expire": 7200})
    if host == "open.feishu.cn" and path.endswith("/raw_content"):
        return httpx.Response(200, json={"code": 0, "data": {"content": "Quarterly plan body"}})
    if host == "open.feishu.cn":
        return httpx.Response(200, json={"code": 0, "data": {"document": {"document_id": "doxcnAbCdEf123456789", "title": "Plan"}}})
    if host == "docs.google.com":
        return httpx.Response(200, headers={"content-type": "text/x-markdown; charset=utf-8"}, content="# Shared doc body".encode())
    if host == "api.exa.ai":
        return httpx.Response(200, json={"results": [{"title": "exa-result-title", "url": "https://a.test", "text": "t"}]})
    if host == "api.firecrawl.dev":
        return httpx.Response(200, json={"success": True, "data": {"markdown": "Scraped page body", "metadata": {"title": "Example", "url": "https://example.com/"}}})
    return httpx.Response(200, json={"errcode": 0})
httpx.AsyncClient = lambda: original(transport=httpx.MockTransport(handle))
build_server(sys.argv[1]).run()
""",
        encoding="utf-8",
    )
    params = StdioServerParameters(command=sys.executable, args=["-I", str(script), provider], env=connection_config(provider, credentials)["env"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool(tool, arguments)
        assert not result.isError, result
        assert expected in result.model_dump_json()


@pytest.mark.asyncio
async def test_robot_missing_business_status_is_failure_and_network_exception_is_redacted():
    async def missing(_):
        return httpx.Response(200, json={"ok": True})

    def broken(request):
        raise httpx.ConnectError("https://example.test?key=super-secret", request=request)

    for handler in (missing, broken):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ValueError) as error:
                await BusinessClient("wecom", {"webhook_key": "super-secret"}, http).send_message("hello")
            assert "super-secret" not in str(error.value)


@pytest.mark.parametrize("provider", sorted(NEW_PROVIDERS))
def test_new_provider_launcher_is_exact_and_accepted(provider):
    import sys

    credentials, _ = NEW_PROVIDERS[provider]
    config = connection_config(provider, credentials)
    assert config["command"] == sys.executable
    assert config["args"] == ["-I", "-m", MODULE, provider]
    assert set(config["env"]) == set(CREDENTIALS[provider].values())
    assert sorted(config["env"].values()) == sorted(credentials.values())
    assert is_bundled_connection(config["command"], config["args"], config["env"])
    assert not is_bundled_connection(config["command"], config["args"], {**config["env"], "PYTHONPATH": "/tmp"})
    assert not is_bundled_connection(config["command"], ["-m", MODULE, provider], config["env"])
    assert not is_bundled_connection("/opt/other/python", config["args"], config["env"])


def test_credential_environment_names_are_stable():
    assert CREDENTIALS["feishu-bot"] == {"webhook_token": "DEERFLOW_FEISHU_BOT_TOKEN", "sign_secret": "DEERFLOW_FEISHU_BOT_SECRET"}
    assert CREDENTIALS["feishu-docs"] == {"app_id": "DEERFLOW_FEISHU_APP_ID", "app_secret": "DEERFLOW_FEISHU_APP_SECRET"}
    assert CREDENTIALS["google-docs"] == {}
    assert CREDENTIALS["exa"] == {"api_key": "DEERFLOW_EXA_API_KEY"}
    assert CREDENTIALS["firecrawl"] == {"api_key": "DEERFLOW_FIRECRAWL_API_KEY"}


def test_google_docs_needs_no_credentials():
    import sys

    config = connection_config("google-docs", {})
    assert config["env"] == {}
    assert is_bundled_connection(sys.executable, ["-I", "-m", MODULE, "google-docs"], {})
    assert not is_bundled_connection(sys.executable, ["-I", "-m", MODULE, "google-docs"], {"DEERFLOW_EXA_API_KEY": "x"})


def test_underscore_is_allowed_in_credentials():
    config = connection_config("feishu-docs", {"app_id": "cli_a1b2c3d4", "app_secret": "sec_ret"})
    assert config["env"] == {"DEERFLOW_FEISHU_APP_ID": "cli_a1b2c3d4", "DEERFLOW_FEISHU_APP_SECRET": "sec_ret"}


@pytest.mark.parametrize(
    "provider,values",
    [
        ("feishu-bot", {"webhook_token": "abc-123"}),
        ("feishu-bot", {"webhook_token": "../../other/path", "sign_secret": "s"}),
        ("feishu-bot", {"webhook_token": "abc/def", "sign_secret": "s"}),
        ("feishu-docs", {"app_id": "cli_x", "app_secret": "s", "extra": "x"}),
        ("feishu-docs", {"app_id": "cli x", "app_secret": "s"}),
        ("feishu-docs", {"app_id": "cli_x", "app_secret": 42}),
        ("google-docs", {"api_key": "x"}),
        ("exa", {}),
        ("exa", {"api_key": ""}),
        ("firecrawl", {"api_key": "fc-1", "url": "http://localhost"}),
        ("firecrawl", {"api_key": "fc-1\n"}),
        ("unknown", {}),
    ],
)
def test_new_provider_invalid_configuration_rejected(provider, values):
    with pytest.raises(ValueError):
        connection_config(provider, values)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", sorted(NEW_PROVIDERS))
async def test_new_provider_tools_and_annotations(provider):
    credentials, expected = NEW_PROVIDERS[provider]
    tools = await build_server(provider).list_tools()
    assert {tool.name for tool in tools} == expected
    for tool in tools:
        schema = json.dumps(tool.inputSchema)
        assert all(field not in schema for field in CREDENTIALS[provider])
        assert tool.description and len(tool.description) < 600
        assert tool.annotations.openWorldHint is True
        assert tool.annotations.destructiveHint is False
        if tool.name == "send_message":
            assert tool.annotations.readOnlyHint is False and tool.annotations.idempotentHint is False
        else:
            assert tool.annotations.readOnlyHint is True
