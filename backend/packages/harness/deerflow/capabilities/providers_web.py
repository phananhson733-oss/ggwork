"""Link-shared Google Docs export, Exa search and Firecrawl scrape.

Contracts: https://support.google.com/docs/answer/2494822 (link sharing; export
endpoint as observed: 307 to *.googleusercontent.com, text/x-markdown or
text/plain with a UTF-8 BOM), https://exa.ai/docs/reference/search,
https://docs.firecrawl.dev/api-reference/endpoint/scrape and
https://docs.firecrawl.dev/api-reference/endpoint/credit-usage.
"""

import math
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from deerflow.capabilities.providers_http import Fetcher, ResponseTooLarge, check_page, expect_json, http_url, page_text, short_text

GOOGLE_EXPORT = "https://docs.google.com/document/d/{}/export?format={}"
GOOGLE_MAX_HOPS = 5
GOOGLE_TEXT_TYPES = ("text/plain", "text/x-markdown", "text/markdown")
EXA_SEARCH = "https://api.exa.ai/search"
EXA_TEXT_CHARS = 1500
FIRECRAWL_SCRAPE = "https://api.firecrawl.dev/v2/scrape"
FIRECRAWL_CREDITS = "https://api.firecrawl.dev/v2/team/credit-usage"

_DATA_IMAGE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+")
_GOOGLE_ID = re.compile(r"[A-Za-z0-9_-]{20,200}")
_GOOGLE_DOC_PATH = re.compile(r"/document(?:/u/\d+)?/d/([^/]+)(?:/.*)?")
_GOOGLE_OTHER_PATH = re.compile(r"/(?:spreadsheets|presentation|forms|drawings|file)(?:/|$)")
_GOOGLE_LINK_HINT = "Supply a Google Docs link like https://docs.google.com/document/d/<id>/edit"
_NOT_SHARED = 'google-docs: the document is not readable without signing in. Check the link and change sharing to "Anyone with the link can view", then retry'
_STATUS_HINTS = {401: "rejected the API key", 402: "account is out of credits", 429: "rate limit reached; wait before retrying"}


class _ExportFailed(ValueError):
    """This export format failed in a way another format might not."""


def parse_google_doc_id(link: str) -> str:
    """Return the document ID from a docs.google.com/document link or a bare ID."""
    text = link.strip() if isinstance(link, str) else ""
    if _GOOGLE_ID.fullmatch(text):
        return text
    if not text or any(char.isspace() for char in text):
        raise ValueError(_GOOGLE_LINK_HINT)
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError(_GOOGLE_LINK_HINT) from None
    if host in ("docs.google.com", "drive.google.com") and parts.scheme.lower() in ("http", "https"):
        match = _GOOGLE_DOC_PATH.fullmatch(parts.path) if host == "docs.google.com" else None
        if match and _GOOGLE_ID.fullmatch(match.group(1)):
            return match.group(1)
        if host == "drive.google.com" or _GOOGLE_OTHER_PATH.match(parts.path):
            raise ValueError("Only Google Docs documents are supported; Sheets, Slides, Forms and Drive files are not")
    raise ValueError(_GOOGLE_LINK_HINT)


def _next_hop(current: str, location: str | None) -> str:
    if not location:
        raise _ExportFailed("google-docs: export redirect had no target")
    target = urljoin(current, location)
    try:
        parts = urlsplit(target)
        host, port = (parts.hostname or "").lower(), parts.port
    except ValueError:
        raise ValueError("google-docs: refused an invalid export redirect") from None
    if host == "accounts.google.com":
        raise ValueError(_NOT_SHARED)
    allowed_host = host == "docs.google.com" or host.endswith(".googleusercontent.com")
    if parts.scheme != "https" or not allowed_host or port not in (None, 443) or parts.username or parts.password:
        raise ValueError("google-docs: refused a redirect to an unexpected host")
    return target


async def _export(fetcher: Fetcher, document_id: str, fmt: str) -> str:
    url = GOOGLE_EXPORT.format(document_id, fmt)
    for _ in range(GOOGLE_MAX_HOPS + 1):
        try:
            reply = await fetcher.fetch("GET", url)
        except ResponseTooLarge:
            # Markdown inlines images as base64, so plain text may still fit.
            raise _ExportFailed("google-docs: the document export exceeds the 2 MB limit") from None
        if reply.status in (301, 302, 303, 307, 308):
            url = _next_hop(url, reply.headers.get("location"))
            continue
        if reply.status in (401, 403, 404):
            raise ValueError(_NOT_SHARED)
        if not reply.ok:
            raise _ExportFailed(f"google-docs: export failed (HTTP {reply.status}); try again later")
        content_type = reply.headers.get("content-type", "").split(";")[0].strip().lower()
        if content_type == "text/html":
            raise ValueError(_NOT_SHARED)
        if content_type not in GOOGLE_TEXT_TYPES:
            raise _ExportFailed("google-docs: export returned an unexpected content type")
        text = reply.content.decode("utf-8", errors="replace").removeprefix("\ufeff")
        return _DATA_IMAGE.sub("data:image/omitted", text)
    raise ValueError("google-docs: too many export redirects")


def _markdown_title(text: str) -> str | None:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    heading = re.fullmatch(r"#{1,6}\s+(.+?)\s*#*", first)
    return heading.group(1)[:300] if heading else None


async def read_google_doc(fetcher: Fetcher, link: str, offset: int, max_chars: int) -> dict[str, Any]:
    check_page(offset, max_chars)
    document_id = parse_google_doc_id(link)
    try:
        text, title = await _export(fetcher, document_id, "md"), True
    except _ExportFailed:
        text, title = await _export(fetcher, document_id, "txt"), False
    return {"title": _markdown_title(text) if title else None, "document_id": document_id, **page_text(text, offset, max_chars)}


def _status_error(label: str, name: str, status: int) -> ValueError:
    hint = _STATUS_HINTS.get(status)
    return ValueError(f"{label}: {name} {hint} (HTTP {status})" if hint else f"{label}: {name} request failed (HTTP {status})")


async def exa_search(fetcher: Fetcher, api_key: str, query: str, num_results: int) -> list[dict[str, Any]]:
    if not isinstance(query, str) or not query.strip() or len(query) > 2000:
        raise ValueError("query must contain 1–2000 characters")
    if type(num_results) is not int or not 1 <= num_results <= 10:
        raise ValueError("num_results must be 1–10")
    body = {"query": query, "numResults": num_results, "contents": {"text": {"maxCharacters": EXA_TEXT_CHARS}}}
    reply = await fetcher.fetch("POST", EXA_SEARCH, headers={"x-api-key": api_key}, json=body)
    if not reply.ok:
        raise _status_error("exa", "Exa", reply.status)
    results = (reply.json_object() or {}).get("results")
    if not isinstance(results, list):
        raise ValueError("exa: Exa returned an invalid search response")
    return [
        {"title": short_text(item.get("title"), 500), "url": item["url"][:2048], "published_date": short_text(item.get("publishedDate"), 64), "text": short_text(item.get("text"), EXA_TEXT_CHARS) or ""}
        for item in results[:num_results]
        if isinstance(item, dict) and isinstance(item.get("url"), str)
    ]


async def firecrawl_scrape(fetcher: Fetcher, api_key: str, url: str, offset: int, max_chars: int) -> dict[str, Any]:
    check_page(offset, max_chars)
    target = http_url(url)
    body = {"url": target, "formats": ["markdown"], "onlyMainContent": True}
    # Firecrawl renders pages server-side (default 60 s budget), so reads wait longer.
    reply = await fetcher.fetch("POST", FIRECRAWL_SCRAPE, headers={"Authorization": f"Bearer {api_key}"}, json=body, timeout=75)
    if not reply.ok:
        raise _status_error("firecrawl", "Firecrawl", reply.status)
    payload = reply.json_object() or {}
    data = payload.get("data")
    markdown = data.get("markdown") if isinstance(data, dict) else None
    if payload.get("success") is not True or not isinstance(markdown, str):
        raise ValueError("firecrawl: Firecrawl did not return page content; the page may block scraping")
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    status = metadata.get("statusCode")
    if type(status) is int and status >= 400:
        raise ValueError(f"firecrawl: the page responded with HTTP {status}")
    title = metadata.get("title")
    title = title[0] if isinstance(title, list) and title else title
    final_url = short_text(metadata.get("url"), 2048) or short_text(metadata.get("sourceURL"), 2048) or target
    return {"url": final_url, "title": short_text(title, 500), **page_text(markdown, offset, max_chars)}


async def firecrawl_credits(fetcher: Fetcher, api_key: str) -> str:
    reply = await fetcher.fetch("GET", FIRECRAWL_CREDITS, headers={"Authorization": f"Bearer {api_key}"})
    if not reply.ok:
        raise _status_error("firecrawl", "Firecrawl", reply.status)
    data = expect_json(reply).get("data")
    remaining = data.get("remainingCredits") if isinstance(data, dict) else None
    if type(remaining) in (int, float) and math.isfinite(remaining):
        return f"Firecrawl API key accepted; {remaining:,.0f} credits remaining"
    return "Firecrawl API key accepted"
