"""Egress IP probing for the Trends requests (design 4.4; plan D20), off unless an echo service is configured.

The design wants each request's egress IP for correlating limits with addresses. Asking an echo service is itself a
request to a third party, so it waits for U13's approval: until PICK_OBS_EGRESS_ECHO_URL is set, nothing is measured,
nothing is sent, and every request row records NULL. Once set, the IP is measured at the session's first request,
again after every 20 requests and after each breaker trip (the executor calls invalidate()), and every request row
carries the latest reading with the time it was taken. A failed measurement keeps the previous reading and its time,
and waits for the next due point rather than asking again on the next request.

The echo request goes through its own client: never the Trends jar's cookies, never its UA.
"""

import asyncio
import ipaddress
import json
from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urlsplit

import httpx

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.trends.source import EgressReading

ECHO_ENV = "PICK_OBS_EGRESS_ECHO_URL"
REMEASURE_EVERY = 20  # requests between two measurements (D20)
ECHO_TIMEOUTS = httpx.Timeout(10.0, connect=5.0)
ECHO_SECONDS = 15.0
ECHO_USER_AGENT = "ggwork-obs-egress/1"
MAX_ECHO_BYTES = 1024


def refuse_all_cookies() -> CookieJar:
    """A cookie jar that stores nothing, for an httpx client whose cookies are managed by hand (or not at all)."""
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def _echo_url(text: str) -> str:
    try:
        parts = urlsplit(text)
        valid = parts.scheme == "https" and bool(parts.hostname) and parts.username is None and parts.password is None
    except ValueError:
        valid = False
    if not valid:
        raise Refused(f"{ECHO_ENV} 须是 https:// 地址，带主机名，不带账号")
    return text


async def _small_body(response: httpx.Response) -> bytes | None:
    """The whole body, or None past MAX_ECHO_BYTES: an echo answer is an address, nothing more."""
    body = b""
    async for chunk in response.aiter_bytes():
        body += chunk
        if len(body) > MAX_ECHO_BYTES:
            return None
    return body


def _ip_of(body: bytes) -> str | None:
    """The address an echo service answered with: plain text, or JSON {"ip": ...}."""
    text = body.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return None
        text = parsed.get("ip", "") if isinstance(parsed, dict) and isinstance(parsed.get("ip"), str) else ""
    try:
        return str(ipaddress.ip_address(text.strip()))
    except ValueError:
        return None


class EgressProbe:
    """D20's probe; `async with EgressProbe(...)`. With echo_url None it is disabled and never sends anything."""

    def __init__(
        self,
        echo_url: str | None,
        *,
        clock: Clock,
        transport: httpx.AsyncBaseTransport | None = None,
        every: int = REMEASURE_EVERY,
        timeouts: httpx.Timeout = ECHO_TIMEOUTS,
    ):
        if every < 1:
            raise ValueError("every is at least 1 request")
        self._url = _echo_url(echo_url) if echo_url is not None else None
        self._clock, self._every = clock, every
        self._reading, self._since, self._due = EgressReading(), 0, True
        self._http = None
        if self._url is not None:
            self._http = httpx.AsyncClient(transport=transport, timeout=timeouts, follow_redirects=False, cookies=refuse_all_cookies())

    def __repr__(self) -> str:
        return f"EgressProbe(enabled={self.enabled})"

    async def __aenter__(self) -> "EgressProbe":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()

    @property
    def enabled(self) -> bool:
        return self._url is not None

    @property
    def reading(self) -> EgressReading:
        return self._reading

    def invalidate(self) -> None:
        """Measure again before the next request: the executor calls this after a breaker trip."""
        self._due = True

    async def before_request(self) -> EgressReading:
        """The reading to record with the request about to go out, measuring first when one is due."""
        if self._url is None:
            return EgressReading()
        if self._due or self._since >= self._every:
            self._reading = await self._measure()
            self._due, self._since = False, 0
        self._since += 1
        return self._reading

    async def _measure(self) -> EgressReading:
        at = self._clock.now()
        try:
            async with asyncio.timeout(ECHO_SECONDS):
                async with self._http.stream("GET", self._url, headers={"user-agent": ECHO_USER_AGENT}) as response:
                    body = await _small_body(response) if response.status_code == 200 else None
        except (TimeoutError, httpx.HTTPError):
            return self._reading
        ip = _ip_of(body) if body is not None else None
        return EgressReading(ip=ip, measured_at=at) if ip is not None else self._reading


def egress_from_env(environ: Mapping[str, str], *, clock: Clock, transport: httpx.AsyncBaseTransport | None = None) -> EgressProbe:
    """The probe as configured: disabled while PICK_OBS_EGRESS_ECHO_URL is unset or blank (D20, U13)."""
    value = environ.get(ECHO_ENV, "").strip()
    return EgressProbe(value or None, clock=clock, transport=transport)
