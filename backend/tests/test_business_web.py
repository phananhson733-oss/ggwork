"""Google Docs, Exa and Firecrawl contracts: request shape, redirects, bounds and error mapping."""

import json

import httpx
import pytest

from deerflow.capabilities.business import BusinessClient
from deerflow.capabilities.providers_web import parse_google_doc_id

DOC_ID = "1mt8aYM88Jj5qkep1xYC5vj0wBlbX2u6gdxhf_puaiQI"
EXPORT_HOST = "doc-10-0s-docstext.googleusercontent.com"
EXA_KEY = "exa-fixture-key"
FIRECRAWL_KEY = "fc-fixture-key"
MARKDOWN = {"content-type": "text/x-markdown; charset=utf-8"}


async def call(provider, credentials, handler, method, *args):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await getattr(BusinessClient(provider, credentials, http), method)(*args)


async def read_google(handler, link=DOC_ID, offset=0, max_chars=20_000):
    return await call("google-docs", {}, handler, "read_document", link, offset, max_chars)


@pytest.mark.parametrize(
    "link",
    [
        f"https://docs.google.com/document/d/{DOC_ID}/edit?tab=t.0",
        f"https://docs.google.com/document/d/{DOC_ID}",
        f"https://docs.google.com/document/u/1/d/{DOC_ID}/edit#heading=h.abc",
        f"docs.google.com/document/d/{DOC_ID}/",
        f" {DOC_ID} ",
    ],
)
def test_parse_google_doc_id(link):
    assert parse_google_doc_id(link) == DOC_ID


@pytest.mark.parametrize(
    "link,fragment",
    [
        (f"https://docs.google.com/spreadsheets/d/{DOC_ID}/edit", "Only Google Docs documents"),
        (f"https://docs.google.com/presentation/d/{DOC_ID}/edit", "Only Google Docs documents"),
        (f"https://drive.google.com/file/d/{DOC_ID}/view", "Only Google Docs documents"),
        (f"https://evil.example/document/d/{DOC_ID}", "docs.google.com/document/d/"),
        (f"https://docs.google.com.evil.example/document/d/{DOC_ID}", "docs.google.com/document/d/"),
        ("https://docs.google.com/document/d/short/edit", "docs.google.com/document/d/"),
        ("", "docs.google.com/document/d/"),
    ],
)
def test_parse_google_doc_id_rejections(link, fragment):
    with pytest.raises(ValueError) as error:
        parse_google_doc_id(link)
    assert fragment in str(error.value)


@pytest.mark.asyncio
async def test_google_export_follows_allowlisted_redirect_and_strips_bom():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.host == "docs.google.com":
            return httpx.Response(307, headers={"location": f"https://{EXPORT_HOST}/export/abc?sig=1"})
        return httpx.Response(200, headers=MARKDOWN, content="\ufeff# Launch plan\n\nBody text".encode())

    result = await read_google(handle, f"https://docs.google.com/document/d/{DOC_ID}/edit")
    assert result == {"title": "Launch plan", "document_id": DOC_ID, "content": "# Launch plan\n\nBody text", "offset": 0, "total_chars": 24, "truncated": False, "next_offset": None}
    first, second = requests
    assert first.method == "GET" and first.url.path == f"/document/d/{DOC_ID}/export" and first.url.params["format"] == "md"
    assert second.url.host == EXPORT_HOST and second.url.scheme == "https"
    assert "authorization" not in first.headers and "cookie" not in first.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "https://evil.example/steal",
        f"http://{EXPORT_HOST}/export/abc",
        "https://googleusercontent.com.evil.example/x",
        f"https://{EXPORT_HOST}:8443/export/abc",
        f"https://user:pass@{EXPORT_HOST}/export/abc",
        "//evil.example/steal",
    ],
)
async def test_google_redirect_outside_allowlist_is_refused(location):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": location})

    with pytest.raises(ValueError) as error:
        await read_google(handle)
    assert "redirect" in str(error.value)
    assert [item.url.host for item in requests] == ["docs.google.com"]


@pytest.mark.asyncio
async def test_google_relative_redirect_stays_on_docs_host():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/export"):
            return httpx.Response(302, headers={"location": "/document/export-final"})
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"plain")

    assert (await read_google(handle))["content"] == "plain"
    assert str(requests[1].url) == "https://docs.google.com/document/export-final"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=b"<html>Sign in - Google Accounts</html>"),
        httpx.Response(302, headers={"location": "https://accounts.google.com/ServiceLogin?continue=https://docs.google.com/"}),
        httpx.Response(401),
        httpx.Response(403),
        httpx.Response(404, headers={"content-type": "text/html"}, content=b"<html>not found</html>"),
    ],
)
async def test_google_private_documents_get_sharing_instructions(response):
    requests = []

    def handle(request):
        requests.append(request)
        return response

    with pytest.raises(ValueError) as error:
        await read_google(handle)
    assert "Anyone with the link can view" in str(error.value)
    assert all(item.url.host == "docs.google.com" for item in requests)


@pytest.mark.asyncio
async def test_google_markdown_failure_falls_back_to_text():
    requests = []

    def handle(request):
        requests.append(request)
        if request.url.params["format"] == "md":
            return httpx.Response(500)
        return httpx.Response(200, headers={"content-type": "text/plain; charset=utf-8"}, content="\ufeffPlain body".encode())

    result = await read_google(handle)
    assert (result["content"], result["title"]) == ("Plain body", None)
    assert [item.url.params["format"] for item in requests] == ["md", "txt"]


@pytest.mark.asyncio
async def test_google_unexpected_content_type_is_rejected():
    def handle(request):
        return httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF")

    with pytest.raises(ValueError):
        await read_google(handle)


@pytest.mark.asyncio
async def test_google_download_is_capped():
    def handle(request):
        return httpx.Response(200, headers=MARKDOWN, content=b"a" * 2_000_001)

    with pytest.raises(ValueError) as error:
        await read_google(handle)
    assert "2 MB" in str(error.value)


@pytest.mark.asyncio
async def test_google_oversized_markdown_falls_back_to_text_and_inline_images_are_omitted():
    image = "data:image/png;base64," + "A" * 100

    def handle(request):
        if request.url.params["format"] == "md":
            return httpx.Response(200, headers=MARKDOWN, content=b"a" * 2_000_001)
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=f"Text [image1]: <{image}>".encode())

    result = await read_google(handle)
    assert result["content"] == "Text [image1]: <data:image/omitted>"


@pytest.mark.asyncio
async def test_google_redirect_loop_is_bounded():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": f"https://docs.google.com/loop/{len(requests)}"})

    with pytest.raises(ValueError) as error:
        await read_google(handle)
    assert "redirect" in str(error.value)
    assert len(requests) == 6  # the export request plus five followed hops


@pytest.mark.asyncio
async def test_google_pagination_and_heading_title():
    body = "Intro line\n# Not a title"

    def handle(request):
        return httpx.Response(200, headers=MARKDOWN, content=body.encode())

    result = await read_google(handle, DOC_ID, 6, 4)
    assert (result["title"], result["content"], result["next_offset"], result["total_chars"]) == (None, "line", 10, len(body))


@pytest.mark.asyncio
async def test_exa_search_wire_contract():
    requests = []

    def handle(request):
        requests.append(request)
        results = [
            {"id": "1", "title": "Market report", "url": "https://a.test/r", "publishedDate": "2026-01-02T00:00:00.000Z", "author": None, "text": "x" * 3000},
            {"id": "2", "title": None, "url": "https://b.test", "text": None},
        ]
        return httpx.Response(200, json={"requestId": "req", "results": results, "costDollars": {"total": 0.01}})

    result = await call("exa", {"api_key": EXA_KEY}, handle, "search", "short drama market", 3)
    assert result == [
        {"title": "Market report", "url": "https://a.test/r", "published_date": "2026-01-02T00:00:00.000Z", "text": "x" * 1500},
        {"title": None, "url": "https://b.test", "published_date": None, "text": ""},
    ]
    [request] = requests
    assert request.method == "POST" and str(request.url) == "https://api.exa.ai/search"
    assert request.headers["x-api-key"] == EXA_KEY
    assert json.loads(request.content) == {"query": "short drama market", "numResults": 3, "contents": {"text": {"maxCharacters": 1500}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,fragment", [(401, "API key"), (402, "credits"), (429, "rate"), (500, "HTTP 500"), (200, "invalid")])
async def test_exa_errors_are_mapped_and_redacted(status, fragment):
    def handle(request):
        return httpx.Response(status, json={"error": f"bad key {EXA_KEY}"})

    with pytest.raises(ValueError) as error:
        await call("exa", {"api_key": EXA_KEY}, handle, "search", "query", 5)
    assert fragment in str(error.value) and EXA_KEY not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("query,count", [("", 5), ("   ", 5), ("q", 0), ("q", 11), ("q" * 2001, 5)])
async def test_exa_arguments_checked_before_network(query, count):
    calls = []
    with pytest.raises(ValueError):
        await call("exa", {"api_key": EXA_KEY}, lambda request: calls.append(request) or httpx.Response(200, json={}), "search", query, count)
    assert calls == []


def firecrawl_reply(markdown="# Hi\n\nBody", metadata=None, success=True):
    meta = {"title": "Example", "sourceURL": "https://example.com", "url": "https://example.com/", "statusCode": 200} if metadata is None else metadata
    return {"success": success, "data": {"markdown": markdown, "metadata": meta}}


@pytest.mark.asyncio
async def test_firecrawl_scrape_wire_contract():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=firecrawl_reply())

    result = await call("firecrawl", {"api_key": FIRECRAWL_KEY}, handle, "scrape", "https://example.com", 0, 20_000)
    assert result == {"url": "https://example.com/", "title": "Example", "content": "# Hi\n\nBody", "offset": 0, "total_chars": 10, "truncated": False, "next_offset": None}
    [request] = requests
    assert request.method == "POST" and str(request.url) == "https://api.firecrawl.dev/v2/scrape"
    assert request.headers["Authorization"] == f"Bearer {FIRECRAWL_KEY}"
    assert json.loads(request.content) == {"url": "https://example.com", "formats": ["markdown"], "onlyMainContent": True}


@pytest.mark.asyncio
async def test_firecrawl_pagination_and_list_title():
    def handle(request):
        return httpx.Response(200, json=firecrawl_reply("abcdefghij", {"title": ["First", "Second"], "sourceURL": "https://example.com"}))

    result = await call("firecrawl", {"api_key": FIRECRAWL_KEY}, handle, "scrape", "https://example.com", 2, 3)
    assert result == {"url": "https://example.com", "title": "First", "content": "cde", "offset": 2, "total_chars": 10, "truncated": True, "next_offset": 5}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,body,fragment",
    [
        (401, {"error": "Unauthorized"}, "API key"),
        (402, {"error": "Payment required"}, "credits"),
        (429, {"error": "rate limit"}, "rate"),
        (500, {"error": "boom"}, "HTTP 500"),
        (200, firecrawl_reply(success=False), "did not return"),
        (200, firecrawl_reply(markdown=None), "did not return"),
        (200, firecrawl_reply(metadata={"statusCode": 404, "url": "https://example.com/missing"}), "HTTP 404"),
    ],
)
async def test_firecrawl_errors_are_mapped_and_redacted(status, body, fragment):
    def handle(request):
        return httpx.Response(status, json=body)

    with pytest.raises(ValueError) as error:
        await call("firecrawl", {"api_key": FIRECRAWL_KEY}, handle, "scrape", "https://example.com", 0, 100)
    assert fragment in str(error.value) and FIRECRAWL_KEY not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["ftp://example.com/x", "https://", "example.com", "javascript:alert(1)", "http:///path", "https://exa mple.com", "https://example.com/a\nb", "https://" + "a" * 2050 + ".com"])
async def test_firecrawl_url_validated_before_network(url):
    calls = []
    with pytest.raises(ValueError):
        await call("firecrawl", {"api_key": FIRECRAWL_KEY}, lambda request: calls.append(request) or httpx.Response(200, json={}), "scrape", url, 0, 100)
    assert calls == []


@pytest.mark.asyncio
async def test_read_providers_report_network_failure_without_delivery_wording():
    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url} with {EXA_KEY}", request=request)

    with pytest.raises(ValueError) as error:
        await call("exa", {"api_key": EXA_KEY}, broken, "search", "query", 5)
    assert "network request failed" in str(error.value)
    assert "delivery" not in str(error.value) and EXA_KEY not in str(error.value)


@pytest.mark.asyncio
async def test_provider_methods_are_scoped_to_their_provider():
    with pytest.raises(ValueError):
        await call("exa", {"api_key": EXA_KEY}, lambda request: httpx.Response(200, json={}), "scrape", "https://example.com", 0, 10)
    with pytest.raises(ValueError):
        await call("google-docs", {}, lambda request: httpx.Response(200, json={}), "search", "query", 5)
