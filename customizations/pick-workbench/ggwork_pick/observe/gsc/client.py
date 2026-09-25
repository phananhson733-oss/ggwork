"""The GSC client: an access token for the service account, then searchAnalytics.query (design 5.1; plan TR-06).

Configuration comes from the environment: the property from PICK_GSC_SITE_URL, the account as gsc/auth.py reads it.
One token serves every request of a round until five minutes before it expires; a 401 gets one fresh token and one
retry. Nothing else is retried here: the quota is shared with RealShort (design 5.1), so what to do after a quota error,
a 5xx or a timeout is the round's decision (TR-21), made from GscRequestError.kind.

Every failure is a GscRequestError and never a response, so a failed request produces no number (design 5.3). Its
message names the request and a fixed hint; Google's own text is kept in `detail` for the probe (TR-07) and is never
part of the message. Every HTTP request, failed ones included, is logged and passed to on_response once, which is how
a round counts its requests and quota errors. The transport is injectable (the mirror/client.py pattern); redirects
are never followed. The quota bodies this classifies are built from Google's documented error envelope; TR-07 records
the real ones and the fixtures in tests/observe/test_gsc_client.py are replaced with them.
"""

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal, get_args
from urllib.parse import quote

import httpx

from ggwork_pick.observe.clock import Clock, SystemClock
from ggwork_pick.observe.errors import ObserveFailure
from ggwork_pick.observe.gsc.auth import TOKEN_ENDPOINT, GscConfigError, ServiceAccount, load_service_account, token_request_form
from ggwork_pick.observe.gsc.query import GscQuery, GscResponse, ResponseShapeError, parse_response, query_body

logger = logging.getLogger(__name__)

SITE_ENV = "PICK_GSC_SITE_URL"
API_BASE = "https://searchconsole.googleapis.com/webmasters/v3/sites"
DEFAULT_TIMEOUTS = httpx.Timeout(60.0, connect=10.0)
REQUEST_SECONDS = 90.0  # one request in all; 25,000 rows of [hour,page,country] take a while
MAX_BODY_BYTES = 32_000_000  # 25,000 rows of long URLs are under 10 MB
TOKEN_REFRESH_MARGIN_SECONDS = 300
DEFAULT_TOKEN_SECONDS = 3600
DETAIL_CHARS = 500
TOKEN_LABEL = "OAuth token 交换"

Endpoint = Literal["token", "query"]
ErrorKind = Literal[
    "quota_short", "quota_long", "forbidden", "unauthorized", "token_rejected", "bad_request", "not_found", "redirect", "server_error",
    "http_error", "timeout", "connection", "too_large", "malformed",
]  # fmt: skip
ERROR_KINDS = get_args(ErrorKind)
QUOTA_KINDS = frozenset({"quota_short", "quota_long"})

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = rf"(?:{_LABEL}\.)+[a-z]{{2,63}}"
_DOMAIN_PROPERTY = re.compile(rf"^sc-domain:{_HOST}$")
_PREFIX_PROPERTY = re.compile(rf"^https?://{_HOST}(?::[0-9]{{1,5}})?/(?:[^\s?#]*/)?$")
_SITE_HINT = "网域属性写 `sc-domain:dramashortstv.com`，URL 前缀属性才写 `https://dramashortstv.com/`（以 / 结尾，全小写）"
_TOKEN = re.compile(r"^[\x21-\x7e]{1,4096}$")
_WORD = re.compile(r"^[A-Za-z_]{1,64}$")
_LIMIT = re.compile(r"^[\w./ -]{1,200}$")
_SECONDS = re.compile(r"^[0-9]{1,6}$")

# Quota: 429, RESOURCE_EXHAUSTED, or a usage-limit reason on a 403. Long-term (per day) when a reason or the text says
# so; anything else, an unexplained 429 included, is short-term: the next round tries again with one request.
_QUOTA_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "dailyLimitExceededUnreg", "RATE_LIMIT_EXCEEDED"}
)
_LONG_REASONS = frozenset({"dailyLimitExceeded", "dailyLimitExceededUnreg"})
_LONG_WORDS = ("long-term", "long term", "per day", "perday", "daily")
_BY_STATUS = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found"}
# rs:src/lib/gsc-api.ts:106-117: the permission text is also what a wrong property name gets.
_FORBIDDEN = (
    "没有这个属性的权限（User does not have sufficient permission）。属性名写错时 GSC 也这么回，看着却像没授权："
    f"先核对 {SITE_ENV}，{_SITE_HINT}；再核对服务账号是否已由属性 Owner 在 Search Console 里加成用户（U2）"
)
_HINTS = {
    "quota_short": "短期配额用完（每站配额与 RealShort 共用）：本轮不再发请求，下一轮再试",
    "quota_long": "长期（按天）配额用完：今天剩下的轮次都不再发请求",
    "forbidden": _FORBIDDEN,
    "unauthorized": "GSC 不认 access token：换新 token 重试一次后仍是 401",
    "bad_request": "参数不被接受：维度组合、日期或过滤写法不对；includingRegex 超长或不合 RE2 语法也回 400",
    "not_found": f"地址不对：核对 {SITE_ENV} 与接口路径",
    "redirect": "被重定向（不跟随）：不是 GSC API 的正常回答",
    "server_error": "GSC 服务端错误：这次请求没有数据，稍后重取由本轮决定",
    "http_error": "不认识的 HTTP 状态",
}
# OAuth error words at the token endpoint (rs gsc-api.ts:96-104 carries them out for the same reason).
_OAUTH_HINTS = {
    "invalid_grant": "私钥损坏、密钥已在 GCP 删除或停用，或本机时钟偏移（assertion 的 iat、exp 对不上）",
    "unauthorized_client": "RealShort 的经验：多半是这个 GCP 项目没有启用 Search Console API",
    "invalid_client": "服务账号不存在或已删除：核对邮箱与私钥是不是同一个服务账号",
    "invalid_scope": "scope 不被接受",
    "access_denied": "服务账号被拒绝",
}


class GscRequestError(ObserveFailure):
    """A GSC request that produced no data. kind is one of ERROR_KINDS; detail is Google's own text (cut), for a probe
    to print, and never part of the message; reasons are the fixed reason words Google sent."""

    def __init__(
        self,
        message: str,
        *,
        kind: ErrorKind,
        endpoint: Endpoint,
        status: int | None = None,
        retry_after: int | None = None,
        detail: str | None = None,
        reasons: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.kind, self.endpoint, self.status = kind, endpoint, status
        self.retry_after, self.detail, self.reasons = retry_after, detail, reasons

    @property
    def is_quota(self) -> bool:
        return self.kind in QUOTA_KINDS


@dataclass(frozen=True)
class GscRequestMetrics:
    """One HTTP request as the round counts it: token or query, the status (None when no answer came), the rows of a
    query that succeeded, and the error kind of one that failed."""

    endpoint: Endpoint
    label: str
    status: int | None
    elapsed_ms: float
    bytes: int
    rows: int | None = None
    truncated: bool | None = None
    error: ErrorKind | None = None


@dataclass(frozen=True)
class GscConfig:
    site_url: str
    account: ServiceAccount


def site_url_from(environ: Mapping[str, str]) -> str:
    """The property, as GSC names it. A wrong name gets a 403 that reads like a missing grant, so it is checked here."""
    site = environ.get(SITE_ENV, "").strip()
    if not site:
        raise GscConfigError(f"缺少 {SITE_ENV}。{_SITE_HINT}")
    if not (_DOMAIN_PROPERTY.fullmatch(site) or _PREFIX_PROPERTY.fullmatch(site)):
        raise GscConfigError(f"{SITE_ENV} 写 GSC 里那个属性的标识：{_SITE_HINT}")
    return site


def load_config(environ: Mapping[str, str] | None = None) -> GscConfig:
    """Everything the client needs from the environment, checked before any request (GscConfigError exits 2)."""
    env = os.environ if environ is None else environ
    return GscConfig(site_url=site_url_from(env), account=load_service_account(env))


# ---- classification ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _GoogleError:
    word: str | None = None  # error.status, such as RESOURCE_EXHAUSTED
    reasons: tuple[str, ...] = ()  # errors[].reason and details[].reason
    limits: tuple[str, ...] = ()  # details[].metadata.quota_limit and quota_metric
    message: str | None = None


def _json(content: bytes) -> object:
    try:
        return json.loads(content)
    except (ValueError, RecursionError):
        return None


def _dicts(value: object) -> tuple[dict, ...]:
    return tuple(item for item in value if isinstance(item, dict)) if isinstance(value, list) else ()


def _google_error(content: bytes) -> _GoogleError:
    parsed = _json(content)
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict):
        return _GoogleError()
    entries = (*_dicts(error.get("errors")), *_dicts(error.get("details")))
    reasons = tuple(entry["reason"] for entry in entries if isinstance(entry.get("reason"), str) and _WORD.fullmatch(entry["reason"]))
    metadata = tuple(entry["metadata"] for entry in entries if isinstance(entry.get("metadata"), dict))
    limits = tuple(
        item[name] for item in metadata for name in ("quota_limit", "quota_metric") if isinstance(item.get(name), str) and _LIMIT.fullmatch(item[name])
    )
    word = error.get("status") if isinstance(error.get("status"), str) and _WORD.fullmatch(error["status"]) else None
    message = error["message"][:DETAIL_CHARS] if isinstance(error.get("message"), str) else None
    return _GoogleError(word=word, reasons=reasons, limits=limits, message=message)


def _kind(status: int, google: _GoogleError) -> ErrorKind:
    if status == 429 or google.word == "RESOURCE_EXHAUSTED" or _QUOTA_REASONS & set(google.reasons):
        text = " ".join((google.message or "", *google.limits)).lower()
        long_term = bool(_LONG_REASONS & set(google.reasons)) or any(word in text for word in _LONG_WORDS)
        return "quota_long" if long_term else "quota_short"
    if 300 <= status < 400:
        return "redirect"
    if status >= 500:
        return "server_error"
    return _BY_STATUS.get(status, "http_error")


def _retry_after(headers: Mapping[str, str]) -> int | None:
    value = headers.get("retry-after", "")
    return int(value) if _SECONDS.fullmatch(value) else None


def classify_failure(endpoint: Endpoint, label: str, status: int, headers: Mapping[str, str], content: bytes) -> GscRequestError:
    """A non-200 answer as a GscRequestError; only fixed words and a fixed hint reach the message."""
    google = _google_error(content)
    kind = _kind(status, google)
    return GscRequestError(
        f"GSC {label} 返回 HTTP {status}（{kind}）：{_HINTS[kind]}",
        kind=kind,
        endpoint=endpoint,
        status=status,
        retry_after=_retry_after(headers),
        detail=google.message,
        reasons=google.reasons,
    )


def _token_failure(status: int, headers: Mapping[str, str], content: bytes) -> GscRequestError:
    parsed = _json(content)
    word = parsed.get("error") if isinstance(parsed, dict) else None
    if status not in (400, 401, 403) or not isinstance(word, str) or not _WORD.fullmatch(word):
        return classify_failure("token", TOKEN_LABEL, status, headers, content)
    description = parsed.get("error_description")
    hint = _OAUTH_HINTS.get(word, "Google 拒绝换发 token")
    return GscRequestError(
        f"GSC {TOKEN_LABEL} 返回 HTTP {status}（{word}）：{hint}",
        kind="token_rejected",
        endpoint="token",
        status=status,
        detail=description[:DETAIL_CHARS] if isinstance(description, str) else None,
        reasons=(word,),
    )


def _token_value(content: bytes) -> tuple[str, int]:
    """access_token and its lifetime from a 200, or a malformed failure."""
    parsed = _json(content)
    fields = parsed if isinstance(parsed, dict) else {}
    token, lifetime = fields.get("access_token"), fields.get("expires_in", DEFAULT_TOKEN_SECONDS)
    lifetime_ok = isinstance(lifetime, int) and not isinstance(lifetime, bool) and 0 < lifetime <= 86_400
    if not isinstance(token, str) or not _TOKEN.fullmatch(token) or not lifetime_ok:
        raise GscRequestError(f"GSC {TOKEN_LABEL} 返回 HTTP 200，但正文里没有可用的 access_token 与 expires_in", kind="malformed", endpoint="token", status=200)
    return token, lifetime


# ---- the client -------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Token:
    value: str = field(repr=False)
    refresh_at: float  # monotonic seconds


@dataclass(frozen=True)
class _Reply:
    status: int
    headers: httpx.Headers
    content: bytes = field(repr=False)
    started: float


async def _read_capped(response: httpx.Response, limit: int) -> bytes | None:
    """The body, or None once it passes `limit` bytes."""
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


class GscClient:
    """One property and one service account; `async with GscClient(load_config()) as gsc: await gsc.query(...)`."""

    def __init__(
        self,
        config: GscConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
        timeouts: httpx.Timeout = DEFAULT_TIMEOUTS,
        request_seconds: float = REQUEST_SECONDS,
        max_body_bytes: int = MAX_BODY_BYTES,
        on_response: Callable[[GscRequestMetrics], None] | None = None,
    ):
        self._site, self._account = config.site_url, config.account
        self._query_url = f"{API_BASE}/{quote(config.site_url, safe='')}/searchAnalytics/query"
        self._clock = clock if clock is not None else SystemClock()
        self._request_seconds, self._max_body_bytes, self._on_response = request_seconds, max_body_bytes, on_response
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(transport=transport, timeout=timeouts, follow_redirects=False)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None, **options) -> "GscClient":
        return cls(load_config(environ), **options)

    def __repr__(self) -> str:
        return f"GscClient(site={self._site!r}, account={self._account.email!r})"

    async def __aenter__(self) -> "GscClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def query(self, query: GscQuery) -> GscResponse:
        """One searchAnalytics.query: a GscResponse (fetched or truncated), or GscRequestError."""
        body = query_body(query)
        reply, token = await self._post_query(query.label, body)
        if reply.status == 401:
            self._report(reply, "query", query.label, error="unauthorized")
            self._forget(token)
            reply, _ = await self._post_query(query.label, body)
        if reply.status != 200:
            raise self._failed(reply, "query", query.label, classify_failure("query", query.label, reply.status, reply.headers, reply.content))
        try:
            response = parse_response(query, _json(reply.content), fetched_at=self._clock.now())
        except ResponseShapeError as bad:
            error = GscRequestError(f"GSC {query.label} 返回 HTTP 200，但正文不合约定：{bad}", kind="malformed", endpoint="query", status=200)
            raise self._failed(reply, "query", query.label, error) from None
        self._report(reply, "query", query.label, rows=len(response.rows), truncated=response.truncated)
        return response

    async def _post_query(self, label: str, body: dict) -> tuple[_Reply, str]:
        token = await self._access_token()
        reply = await self._send("query", label, self._query_url, json=body, headers={"authorization": f"Bearer {token}"})
        return reply, token

    def _forget(self, token: str) -> None:
        if self._token is not None and self._token.value == token:
            self._token = None

    async def _access_token(self) -> str:
        async with self._token_lock:
            current = self._token
            if current is None or self._clock.monotonic() >= current.refresh_at:
                current = await self._fetch_token()
                self._token = current
            return current.value

    async def _fetch_token(self) -> _Token:
        assertion = self._account.sign_assertion(self._clock.now())
        reply = await self._send("token", TOKEN_LABEL, TOKEN_ENDPOINT, data=token_request_form(assertion))
        if reply.status != 200:
            raise self._failed(reply, "token", TOKEN_LABEL, _token_failure(reply.status, reply.headers, reply.content))
        try:
            value, lifetime = _token_value(reply.content)
        except GscRequestError as bad:
            raise self._failed(reply, "token", TOKEN_LABEL, bad) from None
        self._report(reply, "token", TOKEN_LABEL)
        return _Token(value=value, refresh_at=reply.started + lifetime - TOKEN_REFRESH_MARGIN_SECONDS)

    async def _send(self, endpoint: Endpoint, label: str, url: str, **request) -> _Reply:
        started = self._clock.monotonic()
        try:
            async with asyncio.timeout(self._request_seconds):
                async with self._http.stream("POST", url, **request) as response:
                    content = await _read_capped(response, self._max_body_bytes)
                    status, headers = response.status_code, response.headers
        except (TimeoutError, httpx.TimeoutException):
            error = GscRequestError(f"GSC {label} 超过 {self._request_seconds:g} 秒没有收完", kind="timeout", endpoint=endpoint)
            raise self._failed(_Reply(0, httpx.Headers(), b"", started), endpoint, label, error) from None
        except httpx.HTTPError as exc:
            error = GscRequestError(f"GSC {label} 连接失败：{type(exc).__name__}", kind="connection", endpoint=endpoint)
            raise self._failed(_Reply(0, httpx.Headers(), b"", started), endpoint, label, error) from None
        if content is None:
            error = GscRequestError(f"GSC {label} 的响应超过 {self._max_body_bytes} 字节", kind="too_large", endpoint=endpoint, status=status)
            raise self._failed(_Reply(status, headers, b"", started), endpoint, label, error)
        return _Reply(status, headers, content, started)

    def _failed(self, reply: _Reply, endpoint: Endpoint, label: str, error: GscRequestError) -> GscRequestError:
        self._report(reply, endpoint, label, error=error.kind)
        return error

    def _report(self, reply: _Reply, endpoint: Endpoint, label: str, *, rows=None, truncated=None, error: ErrorKind | None = None) -> None:
        status = reply.status or None  # 0: no answer came
        elapsed = round((self._clock.monotonic() - reply.started) * 1000, 1)
        metrics = GscRequestMetrics(endpoint, label, status, elapsed, len(reply.content), rows, truncated, error)
        logger.info("GSC %s：HTTP %s，%s 行，%s，%.0f ms", label, status or "-", "-" if rows is None else rows, error or "ok", elapsed)
        if self._on_response is not None:
            self._on_response(metrics)
