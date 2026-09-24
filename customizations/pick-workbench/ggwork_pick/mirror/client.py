"""HTTP client for the RealShort feed: the v2 manifest and row pages, and v1 pages pinned to the same as_of and fp.

Plan 4.1, 4.7 and 5.2 steps 3, 5 and 6, checked against RealShort 816ca2e (rs = realshort-pick-export-v2):
- one as_of per pull, the current minute less two (plan 2.5), sent as the text of asOf.toISOString();
- only the parameters RealShort accepts (rs:src/lib/pick/export-v2-page.ts:75-92, feed-map.ts:46-63);
- every response becomes a page or one of the errors in mirror/errors.py; every page is checked in mirror/feed_shape.py;
- pages() and v1_pages() are async generators, so one page is in memory at a time.
Nothing here reads the database or the app config: the dry-run (mirror/dry_run.py) and the mirror run (P2-5c) share it.

`python -m ggwork_pick.mirror.client --dry-run ...` is the P1-6 measurement (plan 1486).
"""

import asyncio
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from urllib.parse import SplitResult, urlsplit

import httpx

from ggwork_pick.mirror.errors import (
    AsOfExpiredError,
    BusyError,
    BusyTimeout,
    ConfigError,
    ContractError,
    DriftError,
    FeedConnectionError,
    FeedError,
    RowTooLargeError,
    SourceReadError,
)
from ggwork_pick.mirror.feed_shape import (
    COUNTED_RESOURCES,
    ERROR_WORDS,
    EXPORT_VERSION,
    MAX_LIMITS,
    REASON_WORDS,
    ROW_RESOURCES,
    SERIES_RESOURCE,
    V1_MAX_PAGES,
    V1_PAGE_LIMIT,
    V1_VERSION,
    Manifest,
    Page,
    PageMetrics,
    check_row_page,
    check_series_day,
    check_v1_page,
    format_as_of,
    parse_manifest,
    select_as_of,
)

__all__ = [
    "AS_OF_MAX_AGE",
    "COUNTED_RESOURCES",
    "MAX_LIMITS",
    "PAGE_LIMITS",
    "ROW_RESOURCES",
    "SERIES_RESOURCE",
    "V1_MAX_PAGES",
    "V1_PAGE_LIMIT",
    "V1_VERSION",
    "FeedClient",
    "Manifest",
    "Page",
    "PageMetrics",
    "format_as_of",
    "select_as_of",
]

# Rows asked per page (the brief's P2-2a): by default each resource's MAX_LIMITS, RealShort's maxLimit and also its
# default, so no limit parameter goes out; a value below it is sent as limit. --limit overrides one run.
PAGE_LIMITS: Mapping[str, int] = MappingProxyType(
    {
        "catalog_rows": 5000,
        "catalog_signals": 5000,
        "catalog_posted": 1000,
        "catalog_accounts": 1000,
        # 由 P1-6 实测确定（U47）：超门槛先试 --limit rs_rows=1000，通过的值写回这里（docs/pick-workbench/mirror-dry-run.md）
        "rs_rows": 2000,
        "rs_ids": 10000,
        "rs_clicks14": 20000,
        "rs_bill_orders": 5000,
        "rs_series_day": 40000,
    }
)
V2_PATH = "/api/pick-feed/v2/"
V1_PATH = "/api/pick-feed"
BYPASS_HEADER = "x-vercel-protection-bypass"
# RealShort takes an as_of back to its own now - 30 minutes (rs:src/lib/pick/export-v2-page.ts:18, :34); stop 5 short.
AS_OF_MAX_AGE = timedelta(minutes=25)
BUSY_BUDGET_SECONDS = 1200  # plan 5.1: waiting on source_busy, at most 20 minutes in all
BUSY_DEFAULT_RETRY_AFTER = 60  # rs:src/lib/pick/export-v2.ts:72
# read_failed is whatever RealShort's load threw, a Neon hiccup included (rs:src/lib/pick/feed-http.ts:81-86): one retry.
READ_ATTEMPTS = 2
READ_RETRY_DELAY_SECONDS = 5.0
# A page is at most 3 MB and a lone row 4 MB (export-v2-page.ts:183-185); anything past this is not RealShort.
MAX_BODY_BYTES = 8_000_000
DEFAULT_TIMEOUTS = httpx.Timeout(60.0, connect=10.0)
# httpx's read timeout is per socket read; plan 5.1 gives one request 60 seconds in all (RealShort's maxDuration too).
REQUEST_SECONDS = 60.0
# The extension shares the gateway's one event loop (critique 1.9): a page body past this is parsed in a worker thread.
THREAD_PARSE_BYTES = 64 * 1024
_SECRET_TEXT = re.compile(r"^[\x21-\x7e]{1,4096}$")
_SECONDS = re.compile(r"^[0-9]{1,4}$")  # str.isdigit() also takes digits int() refuses, such as "²"
_MANIFEST, _PAGE = "manifest", "page"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _split(base_url: object) -> SplitResult | None:
    """urlsplit, or None when the text is not a URL with a host and a valid port."""
    if not isinstance(base_url, str):
        return None
    try:
        parts = urlsplit(base_url.strip())
        _ = parts.port  # a port that is not a number in range raises ValueError here
    except ValueError:
        return None
    return parts if parts.hostname else None


def _origin(base_url: object) -> str:
    parts = _split(base_url)
    if parts is None or parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise ConfigError("base URL 只写源站：http(s)://主机[:端口]，不带账号")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ConfigError("base URL 只写源站，不带路径、查询或锚点；路径由客户端拼")
    return f"{parts.scheme}://{parts.netloc}"


def _secret(value: object, what: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _SECRET_TEXT.match(text):
        raise ConfigError(f"{what} 为空，或含空白、控制字符、非 ASCII 字符")
    return text


@dataclass(frozen=True)
class _Request:
    path: str
    params: dict[str, str]
    token: str = field(repr=False)
    resource: str
    page: int
    as_of: datetime
    phase: str
    day: str | None = None

    @property
    def label(self) -> str:
        if self.resource == "v1":
            return f"feed v1 第 {self.page} 页"
        return "feed v2 manifest" if self.phase == _MANIFEST else f"feed v2 {self.resource} 第 {self.page} 页"


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: httpx.Headers
    content: bytes = field(repr=False)
    wire_bytes: int
    started: float
    elapsed_ms: float


_NOT_JSON = object()


def _parse_json(content: bytes) -> object:
    try:
        return json.loads(content)
    except (ValueError, RecursionError):
        return _NOT_JSON


def _word(parsed: object, field: str, vocabulary: frozenset[str]) -> str | None:
    value = parsed.get(field) if isinstance(parsed, dict) else None
    return value if isinstance(value, str) and value in vocabulary else None


def _retry_after(headers: httpx.Headers) -> int | None:
    value = headers.get("retry-after", "")
    return int(value) if _SECONDS.match(value) else None


def _metrics(request: _Request, reply: _Reply, parsed: object, attempt: int) -> PageMetrics:
    rows = parsed.get("rows") if reply.status == 200 and isinstance(parsed, dict) else None
    return PageMetrics(
        resource=request.resource,
        page=request.page,
        status=reply.status,
        elapsed_ms=reply.elapsed_ms,
        bytes=len(reply.content),
        wire_bytes=reply.wire_bytes,
        rows=len(rows) if isinstance(rows, list) else None,
        retry_after=_retry_after(reply.headers),
        attempt=attempt,
        error=_word(parsed, "error", ERROR_WORDS),
        day=request.day,
        started=reply.started,
    )


def _row_key(value: object) -> tuple[str | int, ...] | None:
    """The key of a row_too_large body, only when it looks like one (rs:src/lib/pick/export-v2-page.ts:147-155)."""
    if not isinstance(value, list) or not 1 <= len(value) <= 3:
        return None
    parts = [part for part in value if (isinstance(part, int) and not isinstance(part, bool)) or (isinstance(part, str) and len(part) <= 200)]
    return tuple(parts) if len(parts) == len(value) else None


def _row_too_large(request: _Request, parsed: object, head: str) -> RowTooLargeError:
    named = parsed.get("resource") if isinstance(parsed, dict) else None
    resource = named if named in ROW_RESOURCES else request.resource
    key = _row_key(parsed.get("key") if isinstance(parsed, dict) else None)
    shown = json.dumps(list(key), ensure_ascii=False) if key is not None else "（形状不符，不显示）"
    return RowTooLargeError(f"{head}（row_too_large）：{resource} 有一行连同信封超过 4 MB，主键 {shown}；不重试", resource=resource, key=key)


# 401 and 404 either come from RealShort's own gate or from something in front of it (deployment protection, a wrong host).
_CONFIG_HINTS = {
    (401, True): "token 不对",
    (401, False): "不是 RealShort 的 401 正文：多半被部署保护拦下，先检查 bypass，再检查 token",
    (404, True): "RealShort 没配这条 feed 的 token",
    (404, False): "不是 RealShort 的 404 正文：地址不对或路由不存在",
}
_CLOCK_HINT = "已过期，或本机时钟与 RealShort 偏差超过 2 分钟"


def _from_realshort(request: _Request, parsed: object, word: str) -> bool:
    """RealShort's own gate answer (rs:src/lib/pick/feed-http.ts:48-53): the fixed word, and on v2 the version too."""
    if _word(parsed, "error", ERROR_WORDS) != word:
        return False
    return request.resource == "v1" or parsed.get("version") == EXPORT_VERSION


def _bad_request(request: _Request, parsed: object, head: str, where: dict) -> FeedError:
    """400: v2 names a fixed reason (export-v2-page.ts:94-112), v1 names none (feed-http.ts:60)."""
    if request.resource == "v1":
        return ContractError(f"{head}（bad_request）：参数不被接受；v1 的 400 不带原因，最可能是 as_of 不在 30 分钟窗口内（{_CLOCK_HINT}）", **where)
    reason = _word(parsed, "reason", REASON_WORDS)
    if reason == "as_of":
        return AsOfExpiredError(f"{head}（as_of）：as_of 不在 RealShort 的 30 分钟窗口内（{_CLOCK_HINT}）", **where)
    return ContractError(f"{head}（{reason or 'bad_request'}）：参数不被接受", **where)


def classify(request: _Request, reply: _Reply, parsed: object) -> FeedError:
    """A non-200 response as an error (rs:src/lib/pick/feed-http.ts:6-14); only fixed words from the body are used."""
    status, word = reply.status, _word(parsed, "error", ERROR_WORDS)
    where = {"status": status, "resource": request.resource}
    head = f"RealShort {request.label} 返回 HTTP {status}"
    if 300 <= status < 400:
        return ConfigError(f"{head}：被部署保护拦下或地址不对（检查 bypass；不跟随跳转）", **where)
    if status in (401, 404):
        ours = _from_realshort(request, parsed, "unauthorized" if status == 401 else "not_found")
        return ConfigError(f"{head}：{_CONFIG_HINTS[status, ours]}", **where)
    if status == 400:
        return _bad_request(request, parsed, head, where)
    if status == 409 and word == "source_changed":
        return DriftError(f"{head}（source_changed）：来源在拉取途中变了", **where)
    if status == 503 and word == "source_busy":
        if request.phase == _MANIFEST:
            return BusyError(head, retry_after=_retry_after(reply.headers) or BUSY_DEFAULT_RETRY_AFTER, resource=request.resource)
        return DriftError(f"{head}（source_busy）：manifest 之后有来源在写，按漂移处理", **where)
    if status == 503 and word == "read_failed":
        return SourceReadError(f"{head}（read_failed）：RealShort 读库失败，重试 1 次后仍失败", **where)
    if status == 500 and word == "row_too_large":
        return _row_too_large(request, parsed, head)
    return FeedError(head, **where)


async def _read_capped(response: httpx.Response, limit: int, request: _Request) -> bytes:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            raise ContractError(f"RealShort {request.label} 的响应超过 {limit} 字节", status=response.status_code, resource=request.resource)
        chunks.append(chunk)
    return b"".join(chunks)


def _cursor(cursor: str | None) -> dict[str, str]:
    return {"cursor": cursor} if cursor is not None else {}


def _check_row_arguments(resource: str, manifest: Manifest, day: str | None, limit: int | None) -> None:
    if resource not in ROW_RESOURCES:
        raise ValueError("resource 不是 feed v2 的行资源")
    if resource == SERIES_RESOURCE:
        check_series_day(day, manifest.as_of)
    elif day is not None:
        raise ValueError(f"只有 {SERIES_RESOURCE} 带 day")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMITS[resource]):
        raise ValueError(f"{resource} 的 limit 必须在 1 到 {MAX_LIMITS[resource]} 之间")


class FeedClient:
    """One RealShort origin and its credentials; `async with FeedClient(...) as client`.

    clock, sleep and timer are injected (the schedule.py pattern); on_response sees every HTTP response's metrics,
    failed ones included, which is how the dry-run prints a line per request.
    """

    def __init__(
        self,
        *,
        base_url: str,
        export_token: str,
        feed_token: str | None = None,
        bypass: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = _utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timer: Callable[[], float] = time.perf_counter,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        on_response: Callable[[PageMetrics], None] | None = None,
        busy_budget: float = BUSY_BUDGET_SECONDS,
        read_retry_delay: float = READ_RETRY_DELAY_SECONDS,
        max_body_bytes: int = MAX_BODY_BYTES,
        request_seconds: float = REQUEST_SECONDS,
    ):
        self._origin = _origin(base_url)
        self._export_token = _secret(export_token, "export token")
        self._feed_token = None if feed_token is None else _secret(feed_token, "v1 token")
        self._bypass = None if bypass is None else _secret(bypass, "bypass")
        self._clock, self._sleep, self._timer = clock, sleep, timer
        self._on_response = on_response
        self._busy_budget, self._read_retry_delay, self._max_body_bytes = busy_budget, read_retry_delay, max_body_bytes
        self._request_seconds = request_seconds
        self._http = httpx.AsyncClient(base_url=self._origin, transport=transport, timeout=timeouts, follow_redirects=False)

    def __repr__(self) -> str:
        return f"FeedClient(base_url={self._origin!r}, v1={self.has_v1}, bypass={self._bypass is not None})"

    async def __aenter__(self) -> "FeedClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def has_v1(self) -> bool:
        return self._feed_token is not None

    async def manifest(self, as_of: datetime, *, busy_sleeps: tuple[int, ...] = ()) -> Manifest:
        """One manifest request at as_of; source_busy raises BusyError, which manifest_when_free waits out."""
        request = _Request(V2_PATH + "manifest", {"as_of": format_as_of(as_of)}, self._export_token, "manifest", 1, as_of, _MANIFEST)
        body, metrics = await self._fetch(request)
        return parse_manifest(body, as_of=as_of, metrics=metrics, busy_sleeps=busy_sleeps)

    async def manifest_when_free(self) -> Manifest:
        """Plan 5.2 step 3: sleep Retry-After on source_busy and choose a new as_of each time, 1200 seconds in all."""
        slept: tuple[int, ...] = ()
        while True:
            try:
                return await self.manifest(select_as_of(self._clock()), busy_sleeps=slept)
            except BusyError as busy:
                if sum(slept) + busy.retry_after > self._busy_budget:
                    message = f"RealShort manifest 一直 source_busy：已等 {sum(slept)} 秒，再等就超过 {self._busy_budget:g} 秒上限"
                    raise BusyTimeout(message, waited=sum(slept)) from None
                await self._sleep(busy.retry_after)
                slept = (*slept, busy.retry_after)

    def pages(self, resource: str, *, manifest: Manifest, day: str | None = None, limit: int | None = None) -> AsyncIterator[Page]:
        """Every page of one row resource at the manifest's as_of and fp, one at a time (plan 5.2 step 6).

        limit is this call's page size, else PAGE_LIMITS'. Arguments are checked here, before any request. The chain
        must end within row_cap + 1 pages: RealShort's 3 MB cut can make a page shorter than limit, but every page
        before the last has a row (export-v2-page.ts:223).
        """
        _check_row_arguments(resource, manifest, day, limit)
        page_limit = limit if limit is not None else PAGE_LIMITS[resource]
        sent = {"limit": str(page_limit)} if limit is not None or page_limit != MAX_LIMITS[resource] else {}

        def request(number: int, cursor: str | None) -> _Request:
            params = {"as_of": manifest.as_of_text, "fp": manifest.fingerprint, **_cursor(cursor), **sent}
            params = {**params, **({"day": day} if day is not None else {})}
            return _Request(V2_PATH + resource, params, self._export_token, resource, number, manifest.as_of, _PAGE, day)

        def check(body: object, number: int) -> str | None:
            return check_row_page(body, resource=resource, as_of_text=manifest.as_of_text, fp=manifest.fingerprint, limit=page_limit)

        return self._walk(resource, request, check, manifest.row_cap(resource, day) + 1)

    def v1_pages(self, manifest: Manifest) -> AsyncIterator[Page]:
        """v1 at the manifest's as_of and fp (plan 5.2 step 5): sync.py's checks plus capturedAt, fingerprint, sourceRevision."""
        if self._feed_token is None:
            raise ConfigError("没有 v1 token，不能拉 v1", resource="v1")
        token = self._feed_token

        def request(number: int, cursor: str | None) -> _Request:
            params = {"limit": str(V1_PAGE_LIMIT), **_cursor(cursor), "as_of": manifest.as_of_text, "fp": manifest.fingerprint}
            return _Request(V1_PATH, params, token, "v1", number, manifest.as_of, _PAGE)

        def check(body: object, number: int) -> str | None:
            return check_v1_page(body, manifest=manifest, first=number == 1)

        return self._walk("v1", request, check, V1_MAX_PAGES)

    async def _walk(
        self,
        resource: str,
        request: Callable[[int, str | None], _Request],
        check: Callable[[object, int], str | None],
        max_pages: int,
    ) -> AsyncIterator[Page]:
        cursor, seen = None, frozenset()
        for number in range(1, max_pages + 1):
            body, metrics = await self._fetch(request(number, cursor))
            next_cursor = check(body, number)
            if next_cursor is not None and next_cursor in seen:
                raise ContractError(f"RealShort {resource} 的游标第二次出现，翻页在绕圈", resource=resource)
            yield Page(body=body, metrics=metrics)
            if next_cursor is None:
                return
            seen, cursor = seen | {next_cursor}, next_cursor
        raise ContractError(f"RealShort {resource} 的页数超过上限 {max_pages}", resource=resource)

    async def _fetch(self, request: _Request) -> tuple[object, PageMetrics]:
        for attempt in range(1, READ_ATTEMPTS + 1):
            self._check_age(request)
            reply = await self._send(request)
            large = len(reply.content) > THREAD_PARSE_BYTES
            parsed = await asyncio.to_thread(_parse_json, reply.content) if large else _parse_json(reply.content)
            metrics = _metrics(request, reply, parsed, attempt)
            if self._on_response is not None:
                self._on_response(metrics)
            if reply.status == 200:
                if parsed is _NOT_JSON:
                    raise FeedError(f"RealShort {request.label} 返回 HTTP 200，但正文不是 JSON", status=200, resource=request.resource)
                return parsed, metrics
            error = classify(request, reply, parsed)
            if not isinstance(error, SourceReadError) or attempt == READ_ATTEMPTS:
                raise error
            await self._sleep(self._read_retry_delay)
        raise AssertionError("every attempt returns or raises")

    def _check_age(self, request: _Request) -> None:
        if self._clock() - request.as_of > AS_OF_MAX_AGE:
            minutes = AS_OF_MAX_AGE.total_seconds() / 60
            raise AsOfExpiredError(f"as_of 已过去超过 {minutes:g} 分钟，{request.label} 不再请求", resource=request.resource)

    async def _send(self, request: _Request) -> _Reply:
        headers = {"authorization": f"Bearer {request.token}", **({BYPASS_HEADER: self._bypass} if self._bypass else {})}
        started = self._timer()
        try:
            async with asyncio.timeout(self._request_seconds):
                async with self._http.stream("GET", request.path, params=request.params, headers=headers) as response:
                    content = await _read_capped(response, self._max_body_bytes, request)
                    # Compressed bytes off the socket; a body httpx already holds in memory never touches that counter.
                    wire = response.num_bytes_downloaded or len(content)
                    status, response_headers = response.status_code, response.headers
        except TimeoutError:
            raise FeedConnectionError(f"RealShort {request.label} 超过 {self._request_seconds:g} 秒没有收完", resource=request.resource) from None
        except httpx.HTTPError as exc:
            raise FeedConnectionError(f"RealShort {request.label} 连接失败：{type(exc).__name__}", resource=request.resource) from None
        return _Reply(status, response_headers, content, wire, started, round((self._timer() - started) * 1000, 1))


if __name__ == "__main__":
    from ggwork_pick.mirror.dry_run import main

    raise SystemExit(main())
