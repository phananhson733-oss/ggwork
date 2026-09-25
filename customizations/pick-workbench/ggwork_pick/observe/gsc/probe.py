"""The GSC probe: seven measurements that settle what design 5.2 left "to be measured" (plan TR-07; design 5.1-5.5).

P1 is the access check of design 5.1: one real searchAnalytics.query under the Restricted permission, which also proves
that the property's Owner added the service account (U2). P2 to P7 measure what TR-21 and TR-23a are built on: whether
hourly data takes byPage (A'), how big a day of [hour,page,country] is and what splitting it by country does (C), how
includingRegex matches and how long it may be, with the Vh and Vd shapes (P4), the metadata watermarks of A and C (P5),
final against all over 16 PT days (P6), and how GSC spells page URLs (P7, the host of the D25 regex).

Each item ends in one verdict: 支持, 不支持 or 退路 (the fallback the design names), or 未定 when an answer that would
settle it never came (a quota error or a failure worth one more run), or 未测 when the run stopped before it. P1 failing
stops the run: nothing else can be learned without access. A quota error stops it too, and its body is kept, since
TR-06's quota fixtures are to be replaced with real ones. The order is P1, P7, P3, P2, P4, P5, P6: P2 compares A' with
P3's C, and P4 takes its seed pages from C and P7.

Every request goes through GscClient one at a time, with a pause between them (the quota is RealShort's too, design
5.1). RecordingTransport keeps the query answers (request body, status, response body) for the raw file: never the
token exchange and never a header, so neither the assertion nor the access token can reach a file.
"""

import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from itertools import groupby
from types import MappingProxyType
from typing import Literal, Protocol, TypeVar, get_args

import httpx

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.gsc.client import GscRequestError, GscRequestMetrics
from ggwork_pick.observe.gsc.cutoff import pt_date_of
from ggwork_pick.observe.gsc.query import GscQuery, GscResponse

Verdict = Literal["支持", "不支持", "退路", "未定", "未测"]
VERDICTS = get_args(Verdict)
DECIDED = frozenset({"支持", "不支持", "退路"})
PROBES = MappingProxyType(
    {
        "P1": "Restricted 权限下的真实查询（接入验收，兼证 U2 由 Owner 完成）",
        "P2": "小时数据加 byPage（A′）",
        "P3": "[hour,page,country] 的行数、截断、配额与按国家拆分（C）",
        "P4": "includingRegex 的语法与长度上限，Vh、Vd 两种形态",
        "P5": "A 与 C 在不同 PT 日上的 metadata 水位与滞后",
        "P6": "A″ 用 final 与 all 覆盖 16 个 PT 日",
        "P7": "页面 URL 的主机与路径写法（D25 正则的主机部分）",
    }
)
ORDER = ("P1", "P7", "P3", "P2", "P4", "P5", "P6")
QUERY_HOST = "searchconsole.googleapis.com"
STOP_KINDS = frozenset({"unauthorized", "forbidden", "token_rejected"})
MAX_RAW_BYTES = 32_000_000
T, K = TypeVar("T"), TypeVar("K")


@dataclass(frozen=True, slots=True)
class Table:
    title: str
    header: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class Finding:
    probe: str
    verdict: Verdict
    conclusion: str
    facts: tuple[tuple[str, str], ...] = ()
    tables: tuple[Table, ...] = ()
    backfill: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self):
        if self.probe not in PROBES or self.verdict not in VERDICTS:
            raise ValueError(f"unknown probe {self.probe!r} or verdict {self.verdict!r}")
        object.__setattr__(self, "backfill", MappingProxyType(dict(self.backfill)))


@dataclass(frozen=True, slots=True)
class ProbeResult:
    findings: tuple[Finding, ...]  # P1..P7 in that order
    started_at: datetime
    finished_at: datetime

    def finding(self, probe: str) -> Finding:
        return next(finding for finding in self.findings if finding.probe == probe)

    @property
    def decided(self) -> bool:
        return all(finding.verdict in DECIDED for finding in self.findings)

    @property
    def backfill(self) -> Mapping[str, object]:
        """Every finding's values for TR-21 and TR-23a, in one mapping (the keys never overlap)."""
        return MappingProxyType({name: value for finding in self.findings for name, value in finding.backfill.items()})


# ---- what the run records ----------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Exchange:
    """One searchAnalytics.query as it went over the wire, for the raw file."""

    seq: int
    step: str | None
    request: object  # the JSON body sent
    status: int
    response_text: str = field(repr=False)

    def as_json(self) -> dict:
        try:
            response: object = json.loads(self.response_text)
        except ValueError:
            response = self.response_text
        return {"seq": self.seq, "step": self.step, "request": self.request, "status": self.status, "response": response}


@dataclass(frozen=True, slots=True)
class Metered:
    step: str | None
    metrics: GscRequestMetrics


class ProbeLog:
    """What the run sent and got, tagged with the probe in progress: the client's metrics and the raw query answers."""

    def __init__(self):
        self._step: str | None = None
        self._metered: tuple[Metered, ...] = ()
        self._exchanges: tuple[Exchange, ...] = ()

    @property
    def metered(self) -> tuple[Metered, ...]:
        return self._metered

    @property
    def exchanges(self) -> tuple[Exchange, ...]:
        return self._exchanges

    def begin(self, step: str) -> None:
        self._step = step

    def on_response(self, metrics: GscRequestMetrics) -> None:
        self._metered = (*self._metered, Metered(self._step, metrics))

    def exchange(self, request: object, status: int, text: str) -> None:
        self._exchanges = (*self._exchanges, Exchange(len(self._exchanges) + 1, self._step, request, status, text))


async def _decoded_capped(response: httpx.Response, limit: int) -> bytes:
    """The body with its Content-Encoding undone, read no further than one byte past `limit`: a body the client would
    refuse as too large still reaches it too large."""
    chunks, size = [], 0
    try:
        async for chunk in response.aiter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > limit:
                break
    finally:
        await response.aclose()
    return b"".join(chunks)


class RecordingTransport(httpx.AsyncBaseTransport):
    """Passes every request on; keeps the query answers only (never the token endpoint, never a header).

    A query answer is read here, decoded once, and handed on without its Content-Encoding and Content-Length, so the
    client parses exactly the text the raw file keeps."""

    def __init__(self, inner: httpx.AsyncBaseTransport, log: ProbeLog):
        self._inner, self._log = inner, log

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.host != QUERY_HOST:
            return response
        content = await _decoded_capped(response, MAX_RAW_BYTES)
        try:
            body: object = json.loads(request.content)
        except ValueError:
            body = None
        self._log.exchange(body, response.status_code, content[:MAX_RAW_BYTES].decode("utf-8", errors="replace"))
        kept = [(name, value) for name, value in response.headers.multi_items() if name.lower() not in ("content-encoding", "content-length")]
        return httpx.Response(response.status_code, headers=kept, content=content, extensions=response.extensions)

    async def aclose(self) -> None:
        await self._inner.aclose()


# ---- asking -----------------------------------------------------------------------------------------------------------


class QueryClient(Protocol):
    async def query(self, query: GscQuery) -> GscResponse: ...


@dataclass(frozen=True, slots=True)
class ProbeContext:
    gsc: QueryClient
    clock: Clock
    site_host: str
    pause_seconds: float = 1.0
    started: datetime | None = None  # run_probe sets it, so every probe counts days from one instant

    @property
    def today(self) -> date:
        """Today in PT, the day GSC's dates count in, as of the start of the run."""
        return pt_date_of(self.started or self.clock.now())

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)


@dataclass(frozen=True, slots=True)
class Answer:
    query: GscQuery
    response: GscResponse | None = None
    error: GscRequestError | None = None

    @property
    def ok(self) -> bool:
        return self.response is not None


class ProbeStopped(Exception):
    """An answer after which nothing more can be learned this run: a quota error, or access that is gone."""

    def __init__(self, error: GscRequestError):
        super().__init__(error.kind)
        self.error = error


async def ask(ctx: ProbeContext, query: GscQuery) -> Answer:
    """One request, after the pause; a failure is an Answer too, unless it stops the run."""
    if ctx.pause_seconds:
        await ctx.clock.sleep(ctx.pause_seconds)
    try:
        return Answer(query, response=await ctx.gsc.query(query))
    except GscRequestError as error:
        if error.is_quota or error.endpoint == "token" or error.kind in STOP_KINDS:
            raise ProbeStopped(error) from None
        return Answer(query, error=error)


def error_facts(error: GscRequestError) -> tuple[tuple[str, str], ...]:
    """What a failed request tells the reader: its kind, status, reason words and Google's own text."""
    return (
        ("错误种类", error.kind),
        ("HTTP 状态", str(error.status) if error.status is not None else "无回答"),
        ("原因词", "、".join(error.reasons) or "无"),
        ("Google 原文", error.detail or "无"),
    )


def error_text(error: GscRequestError) -> str:
    return f"{error.kind}：{error}"


# An answer that never came, or came garbled, settles nothing: run again. A 400 or a 404 is GSC's answer.
TRANSIENT_KINDS = frozenset({"server_error", "timeout", "connection", "http_error", "malformed", "too_large", "redirect"})


def failure_verdict(error: GscRequestError) -> Verdict:
    return "未定" if error.kind in TRANSIENT_KINDS else "不支持"


def failed(probe: str, what: str, error: GscRequestError, **backfill: object) -> Finding:
    """The finding of a probe whose deciding request failed without stopping the run."""
    verdict = failure_verdict(error)
    advice = "再跑一次" if verdict == "未定" else "GSC 明确拒绝"
    return Finding(probe, verdict, f"{what}失败（{error_text(error)}），{advice}", facts=error_facts(error), backfill=backfill)


def filters(dimension: str, operator: str, expression: str) -> tuple[dict, ...]:
    """One dimensionFilterGroup holding one filter, in GSC's own shape."""
    return ({"groupType": "and", "filters": [{"dimension": dimension, "operator": operator, "expression": expression}]},)


def impressions(rows) -> int:
    return sum(row.impressions for row in rows)


def sum_by(items: Iterable[T], key: Callable[[T], K], value: Callable[[T], int]) -> dict[K, int]:
    """value summed per key, without mutating anything along the way."""
    ordered = sorted(items, key=key)
    return {name: sum(value(item) for item in group) for name, group in groupby(ordered, key=key)}


def yes_no(flag: bool | None) -> str:
    return "—" if flag is None else ("是" if flag else "否")


def percent(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part * 100 / whole, 1)


# ---- the run ----------------------------------------------------------------------------------------------------------

Step = Callable[[ProbeContext, Mapping[str, object]], Awaitable[tuple[Finding, Mapping[str, object]]]]


def _not_run(probe: str, reason: str) -> Finding:
    return Finding(probe, "未测", f"未测：{reason}")


def _stopped(probe: str, error: GscRequestError) -> Finding:
    verdict: Verdict = "不支持" if probe == "P1" and not error.is_quota else "未定"
    return Finding(probe, verdict, f"请求被拒，本次运行停止（{error_text(error)}）", facts=error_facts(error))


def _steps() -> Mapping[str, Step]:
    from ggwork_pick.observe.gsc import probe_checks, probe_pages

    return {
        "P1": probe_checks.p1_access,
        "P2": probe_checks.p2_hourly_by_page,
        "P3": probe_checks.p3_detail,
        "P4": probe_pages.p4_regex,
        "P5": probe_checks.p5_watermarks,
        "P6": probe_checks.p6_daily_totals,
        "P7": probe_pages.p7_page_urls,
    }


async def run_probe(ctx: ProbeContext, log: ProbeLog) -> ProbeResult:
    """P1..P7 in dependency order; every item gets a Finding, whatever happens to the others."""
    started, steps = ctx.clock.now(), _steps()
    ctx = replace(ctx, started=started)
    findings: dict[str, Finding] = {}
    carry: Mapping[str, object] = MappingProxyType({})
    halted: str | None = None
    for probe in ORDER:
        if halted is not None:
            findings = {**findings, probe: _not_run(probe, halted)}
            continue
        log.begin(probe)
        try:
            finding, more = await steps[probe](ctx, carry)
        except ProbeStopped as stop:
            finding, more = _stopped(probe, stop.error), {}
            halted = f"{probe} 的请求被拒（{stop.error.kind}），本次运行在那里停止"
        findings, carry = {**findings, probe: finding}, MappingProxyType({**carry, **more})
        if probe == "P1" and finding.verdict != "支持" and halted is None:
            halted = "P1 未通过，没有访问权限时其余各项无从测起"
    return ProbeResult(tuple(findings[probe] for probe in PROBES), started, ctx.clock.now())
