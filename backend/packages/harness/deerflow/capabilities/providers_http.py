"""Bounded HTTP plumbing shared by the bundled provider clients.

Redirects are never followed here, bodies are streamed under a hard size cap and
request exceptions become a fixed message: httpx errors embed request URLs, and
some provider URLs carry credentials.
"""

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_SECONDS = 20
MAX_PAGE_CHARS = 50_000


class ResponseTooLarge(ValueError):
    """The provider body exceeded the caller's byte cap."""


@dataclass(frozen=True)
class Reply:
    status: int
    headers: httpx.Headers
    content: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def json_object(self) -> dict[str, Any] | None:
        """The body as a JSON object, or None when it is anything else."""
        try:
            data = json.loads(self.content)
        except (ValueError, UnicodeError):
            return None
        return data if isinstance(data, dict) else None


class Fetcher:
    """One provider's bounded HTTP access; reuses an injected client (tests) when given."""

    def __init__(self, label: str, http: httpx.AsyncClient | None = None):
        self.label = label
        self.http = http

    async def fetch(self, method: str, url: str, *, unknown_outcome: bool = False, max_bytes: int = MAX_RESPONSE_BYTES, timeout: float = TIMEOUT_SECONDS, **kwargs: Any) -> Reply:
        """Perform one request without redirects or retries.

        ``unknown_outcome`` marks writes whose delivery cannot be confirmed after a
        network failure, so the caller is told to check before retrying.
        """

        async def perform(client: httpx.AsyncClient) -> Reply:
            try:
                async with client.stream(method, url, follow_redirects=False, timeout=timeout, **kwargs) as response:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            raise ResponseTooLarge("Provider response exceeds the size limit")
                    return Reply(response.status_code, response.headers, bytes(chunks))
            except (httpx.RequestError, httpx.InvalidURL):
                hint = "delivery may be unknown, check before retrying" if unknown_outcome else "try again later"
                raise ValueError(f"{self.label}: network request failed; {hint}") from None

        if self.http is not None:
            return await perform(self.http)
        async with httpx.AsyncClient() as client:
            return await perform(client)


def expect_json(reply: Reply) -> dict[str, Any]:
    """Parse a successful body as a JSON object with the historical error wording."""
    try:
        data = json.loads(reply.content)
    except (ValueError, UnicodeError):
        raise ValueError("Provider returned an invalid JSON response") from None
    if not isinstance(data, dict):
        raise ValueError("Provider returned an invalid response")
    return data


def check_page(offset: int, max_chars: int) -> None:
    if type(offset) is not int or type(max_chars) is not int or offset < 0 or not 1 <= max_chars <= MAX_PAGE_CHARS:
        raise ValueError(f"offset must be 0 or more and max_chars 1–{MAX_PAGE_CHARS}")


def page_text(text: str, offset: int, max_chars: int) -> dict[str, Any]:
    """Slice long text for the model; next_offset continues where this page ends."""
    check_page(offset, max_chars)
    total = len(text)
    if offset > total:
        raise ValueError(f"offset {offset} is past the end of the text ({total} characters)")
    end = min(total, offset + max_chars)
    return {"content": text[offset:end], "offset": offset, "total_chars": total, "truncated": end < total, "next_offset": end if end < total else None}


def http_url(url: str, limit: int = 2048) -> str:
    """Validate a model-supplied absolute http(s) URL with a host."""
    candidate = url.strip() if isinstance(url, str) else ""
    if not candidate or len(candidate) > limit or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in candidate):
        raise ValueError(f"Supply an absolute http(s) URL of at most {limit} characters")
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
    except ValueError:
        raise ValueError("Supply an absolute http(s) URL") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise ValueError("Supply an absolute http(s) URL with a host")
    return candidate


def short_text(value: Any, limit: int) -> str | None:
    """A provider string field bounded for the model, or None."""
    return value[:limit] if isinstance(value, str) and value else None
