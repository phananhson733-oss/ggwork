"""verify_credentials: cheap, side-effect-free live checks for the connection-check endpoint."""

import httpx
import pytest

from deerflow.capabilities.business import verify_credentials


def recorder(status, body):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    return requests, handle


async def verify(provider, credentials, handler):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await verify_credentials(provider, credentials, http)


@pytest.mark.asyncio
async def test_hubspot_reads_one_company():
    requests, handle = recorder(200, {"results": [], "paging": {}})
    summary = await verify("hubspot", {"access_token": "private-token"}, handle)
    assert isinstance(summary, str) and "HubSpot" in summary
    [request] = requests
    assert request.method == "GET" and request.url.host == "api.hubapi.com" and request.url.path == "/crm/v3/objects/companies"
    assert request.url.params["limit"] == "1"
    assert request.headers["Authorization"] == "Bearer private-token"


@pytest.mark.asyncio
async def test_hubspot_rejection_is_safe_error():
    _, handle = recorder(401, {"message": "private-token is invalid"})
    with pytest.raises(ValueError) as error:
        await verify("hubspot", {"access_token": "private-token"}, handle)
    assert "private-token" not in str(error.value)


@pytest.mark.asyncio
async def test_feishu_docs_issues_tenant_token_only():
    requests, handle = recorder(200, {"code": 0, "msg": "ok", "tenant_access_token": "t-secret-tenant", "expire": 7200})
    summary = await verify("feishu-docs", {"app_id": "cli_a1b2c3", "app_secret": "app_secret"}, handle)
    assert summary == "Feishu app token issued"
    [request] = requests
    assert request.method == "POST" and str(request.url) == "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"


@pytest.mark.asyncio
async def test_feishu_docs_rejected_app():
    _, handle = recorder(200, {"code": 10014, "msg": "app secret invalid"})
    with pytest.raises(ValueError) as error:
        await verify("feishu-docs", {"app_id": "cli_a1b2c3", "app_secret": "app_secret"}, handle)
    assert "App ID" in str(error.value) and "app_secret" not in str(error.value)


@pytest.mark.asyncio
async def test_firecrawl_reads_credit_usage():
    requests, handle = recorder(200, {"success": True, "data": {"remainingCredits": 480, "planCredits": 500, "billingPeriodStart": None, "billingPeriodEnd": None}})
    summary = await verify("firecrawl", {"api_key": "fc-key"}, handle)
    assert "480" in summary
    [request] = requests
    assert request.method == "GET" and str(request.url) == "https://api.firecrawl.dev/v2/team/credit-usage"
    assert request.headers["Authorization"] == "Bearer fc-key"


@pytest.mark.asyncio
async def test_firecrawl_without_credit_figure_still_confirms_key():
    _, handle = recorder(200, {"success": True, "data": {}})
    assert await verify("firecrawl", {"api_key": "fc-key"}, handle) == "Firecrawl API key accepted"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500])
async def test_firecrawl_rejection_is_safe_error(status):
    _, handle = recorder(status, {"error": "fc-key rejected"})
    with pytest.raises(ValueError) as error:
        await verify("firecrawl", {"api_key": "fc-key"}, handle)
    assert "fc-key" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,credentials",
    [
        ("feishu-bot", {"webhook_token": "0f1e2d3c-4b5a", "sign_secret": "s"}),
        ("dingtalk", {"access_token": "t", "sign_secret": "s"}),
        ("wecom", {"webhook_key": "k"}),
        ("exa", {"api_key": "exa-key"}),
        ("google-docs", {}),
    ],
)
async def test_providers_without_free_side_effect_free_check_return_none(provider, credentials):
    requests, handle = recorder(200, {})
    assert await verify(provider, credentials, handle) is None
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,credentials", [("exa", {}), ("hubspot", {"access_token": "***"}), ("unknown", {}), ("feishu-docs", {"app_id": "cli_x"})])
async def test_invalid_credentials_are_rejected_before_network(provider, credentials):
    requests, handle = recorder(200, {})
    with pytest.raises(ValueError):
        await verify(provider, credentials, handle)
    assert requests == []


@pytest.mark.asyncio
async def test_network_failure_is_redacted():
    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url} fc-key", request=request)

    with pytest.raises(ValueError) as error:
        await verify("firecrawl", {"api_key": "fc-key"}, broken)
    assert "fc-key" not in str(error.value)
