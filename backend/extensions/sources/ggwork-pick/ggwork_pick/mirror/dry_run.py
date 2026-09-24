"""`python -m ggwork_pick.mirror.client --dry-run`: one full pull, measured and thrown away (plan P1-6, 1486-1496).

stdout gets one JSON line per HTTP response (metrics only: PageMetrics.line() plus the run number, printed as both
attempt and run) and a summary line last. Nothing is written to disk; no database or app config is read. Tokens come from files or the environment, the
deployment-protection bypass only from a file: a secret on the command line ends up in shell history and `ps`.

Exit status: 0 every gate passed, 1 a gate failed, 2 usage, 3 the pull itself failed, 4 an unexpected error (a bug,
or a shape no check caught; the summary names its class and the innermost frame, never its message).

Page iteration (walk) and bookkeeping (Tally) are kept apart. --scan also runs the Python pan scrub (mirror/pan.py) over
every page it reads, in a worker thread, and reports only paths and counts: v1 rows by v1's exemptions, the v1 rules
Markdown as v1.rules (U45), each v2 row by its resource's exemptions, manifest.meta as finalizeManifest scrubs it. Any
hit fails the pan_scan gate (U20, U52), and so does a --scan without a v1 token: P1 step 7 counts v1.rules too. In the
same thread every v2 page (rs_series_day included) and the manifest go through the strict models the mirror writes
from (contracts.parse_page, contracts.parse_manifest): a failure never stops the pull, and the contract gate reports,
per resource, how many pages failed and the first failing field path, never a value. Its thread time is kept out of
run_ms, which measures RealShort.

P1-6 (2026-09-24) set the gates as they are: page_time over the row pages only, the manifest under its own
manifest_time, and a null sourceRevision a gate (source_revision) rather than a line to read.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
import traceback
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, TextIO

from ggwork_pick.mirror import contracts, pan
from ggwork_pick.mirror.client import COUNTED_RESOURCES, MAX_LIMITS, ROW_RESOURCES, SERIES_RESOURCE, FeedClient, Manifest, Page, PageMetrics
from ggwork_pick.mirror.errors import AsOfExpiredError, BusyTimeout, ConfigError, DriftError, FeedError
from ggwork_pick.mirror.feed_shape import SERIES_DAY_SPAN

PROG = "python -m ggwork_pick.mirror.client"
EXPORT_TOKEN_ENV = "PICK_REALSHORT_EXPORT_TOKEN"
FEED_TOKEN_ENV = "PICK_REALSHORT_FEED_TOKEN"
# Gates of plan 1490-1496: every row page under 15 seconds, every page under 3,000,000 bytes, the whole pull under 3
# minutes. The manifest's time has a gate of its own: P1-6 measured 22.7-25.5 s for its thirteen queries at once
# (rs:src/lib/pick/export-v2.ts:455-469), and the user took that with a 45-second gate (2026-09-24).
PAGE_MS_LIMIT = 15_000
MANIFEST_MS_LIMIT = 45_000
PAGE_BYTES_LIMIT = 3_000_000
RUN_MS_LIMIT = 180_000
SOURCE_REVISION_NULL = (
    "manifest 的 sourceRevision 为 null：这个 RealShort 部署没有 VERCEL_GIT_COMMIT_SHA（rs:src/lib/pick/export-v2.ts:77），fingerprint 里也就没有构建版本"
)
WHOLE_RECORD = "整条记录"  # a contract failure at a page's or a row's top level, as PageContractError words it
# A hit on one of these meta.scrub fields means RealShort's scrub rewrote drama names or descriptions: merging
# realshort#67 would ship that (critique 1.2; the brief's P2-2a and P1 step 5). meta.scrub keys are "<resource>.<column>"
# (toExportRow's hits); "*" stands for any one row resource, nothing deeper.
TITLE_SCRUB_FIELDS = ("*.title", "*.title_cn", "*.description", "rs_ids.title", "catalog_posted.title", "rs_bill_orders.book_title")
BLOCKS_67 = "阻断 #67 合并"
# What --scan cannot vouch for without a v1 token (P1 step 7, U45): the pan_scan gate then fails and names them.
V1_SCAN_PATHS = ("v1.rows", "v1.rules")
V1_UNSCANNED = f"没有 v1 token，v1 行与 v1.rules 没扫；--scan 要连 v1 一起扫（--v1-token-file 或 {FEED_TOKEN_ENV}）"
# RealShort redeploys often and the fingerprint carries the commit SHA (critique 1.3): the brief's P2-2a starts over
# from the manifest at most twice, so a third drift ends the dry-run.
RERUNS = 2
DRIFT_BACKOFF_SECONDS = 90  # plan 5.2 step 3
EXIT_OK, EXIT_GATES, EXIT_USAGE, EXIT_FETCH, EXIT_INTERNAL = 0, 1, 2, 3, 4
_NUMBER = re.compile(r"^[0-9]{1,6}$")
_QUOTED = re.compile("['\"]")  # argparse quotes the values it echoes (%r)


class UsageError(Exception):
    """A problem with the command line or a secret file; the message never holds a secret."""


@dataclass(frozen=True)
class Options:
    base_url: str
    bypass_file: Path | None
    token_file: Path | None
    v1_token_file: Path | None
    series_days: int
    limits: Mapping[str, int]
    scan: bool = False


@dataclass(frozen=True)
class Secrets:
    export_token: str = field(repr=False)
    feed_token: str | None = field(repr=False)
    bypass: str | None = field(repr=False)


def _safe_usage_error(message: str) -> str:
    """argparse quotes the offending value; a secret pasted by mistake must not reach the terminal.

    "argument --x: <detail>" keeps the flag, which comes from this parser, and the detail only when it quotes nothing:
    `--dry-run=<secret>` gives "ignored explicit argument '<secret>'", which is replaced.
    """
    if message.startswith("unrecognized arguments"):
        return "有不认识的参数，用 --help 查看用法（token 与 bypass 不收命令行明文，只收文件：--token-file、--v1-token-file、--bypass-header-file）"
    if message.startswith("argument "):
        flag, _, detail = message.partition(": ")
        return f"{flag}: {'取值不对，用 --help 查看用法' if _QUOTED.search(detail) else detail}"
    if message.startswith("the following arguments are required"):
        return message
    return "参数不对，用 --help 查看用法"


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: 参数错误：{_safe_usage_error(message)}\n")


def _series_days(text: str) -> int:
    if not _NUMBER.match(text) or int(text) > SERIES_DAY_SPAN:
        raise argparse.ArgumentTypeError(f"必须是 0 到 {SERIES_DAY_SPAN} 的整数")
    return int(text)


def _limit_item(text: str) -> tuple[str, int]:
    name, sep, value = text.partition("=")
    if not sep or name not in ROW_RESOURCES:
        raise argparse.ArgumentTypeError("写成 资源=条数，资源是 feed v2 的行资源之一")
    if not _NUMBER.match(value) or not 1 <= int(value) <= MAX_LIMITS[name]:
        raise argparse.ArgumentTypeError(f"{name} 的条数必须在 1 到 {MAX_LIMITS[name]} 之间")
    return name, int(value)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog=PROG, allow_abbrev=False, description="拉一遍 RealShort feed v2（和 v1），只输出每页耗时与字节，不落盘（方案 P1-6）。")
    parser.add_argument("--dry-run", action="store_true", required=True, help="唯一的模式：量一次完整拉取")
    parser.add_argument("--base-url", required=True, help="只写源站，如 https://xxx.vercel.app；路径由客户端拼")
    parser.add_argument("--bypass-header-file", type=Path, help="x-vercel-protection-bypass 的值所在文件；不收命令行明文")
    parser.add_argument("--token-file", type=Path, help=f"feed v2 token 文件；缺省读环境变量 {EXPORT_TOKEN_ENV}")
    parser.add_argument("--v1-token-file", type=Path, help=f"feed v1 token 文件；缺省读 {FEED_TOKEN_ENV}，都没有就跳过 v1")
    parser.add_argument("--series-days", type=_series_days, default=1, help="拉 snapshotDays 最后几天的 rs_series_day，缺省 1")
    parser.add_argument(
        "--limit", type=_limit_item, action="extend", nargs="+", default=[], metavar="资源=条数", help="覆盖页大小：--limit rs_rows=1000 rs_ids=5000，可重复"
    )
    parser.add_argument("--scan", action="store_true", help="在内存里用 Python 网盘清洗扫描每一页，只报路径与次数；有命中就以 1 退出")
    return parser


def parse_args(argv: Sequence[str] | None) -> Options:
    args = build_parser().parse_args(argv)
    limits = MappingProxyType(dict(args.limit))
    return Options(args.base_url, args.bypass_header_file, args.token_file, args.v1_token_file, args.series_days, limits, args.scan)


def _read_secret(path: Path, what: str) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"{what} 文件读不了：{type(exc).__name__}") from None
    if not text:
        raise UsageError(f"{what} 文件是空的")
    return text


def load_secrets(options: Options, env: Mapping[str, str]) -> Secrets:
    """A file wins over the environment; the bypass secret is only ever read from a file."""
    export = _read_secret(options.token_file, "--token-file") if options.token_file else env.get(EXPORT_TOKEN_ENV, "").strip()
    if not export:
        raise UsageError(f"缺少 feed v2 token：给 --token-file，或设置环境变量 {EXPORT_TOKEN_ENV}")
    feed = _read_secret(options.v1_token_file, "--v1-token-file") if options.v1_token_file else env.get(FEED_TOKEN_ENV, "").strip()
    bypass = _read_secret(options.bypass_file, "--bypass-header-file") if options.bypass_file else None
    return Secrets(export_token=export, feed_token=feed or None, bypass=bypass)


def _emit(out: TextIO, record: Mapping) -> None:
    out.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    out.flush()


class Recorder:
    """The client's on_response: prints each response's metrics as it happens and keeps them for the summary's counts.

    The dry-run's one stateful object, a sink: its lines change only through __call__, the run tag through start_run.
    """

    def __init__(self, out: TextIO):
        self._out = out
        self._lines: list[dict] = []
        self._run = 1

    def start_run(self, run: int) -> None:
        self._run = run

    def __call__(self, metrics: PageMetrics) -> None:
        line = {"attempt": self._run, **metrics.line(), "run": self._run}
        self._lines.append(line)
        _emit(self._out, line)

    def count(self, predicate: Callable[[Mapping], bool]) -> int:
        return sum(1 for line in self._lines if predicate(line))


def _is_409(line: Mapping) -> bool:
    return line["status"] == 409


def _is_busy(line: Mapping) -> bool:
    return line.get("error") == "source_busy"


def _is_manifest_busy(line: Mapping) -> bool:
    return _is_busy(line) and line["resource"] == "manifest"


def _is_read_failed(line: Mapping) -> bool:
    return line.get("error") == "read_failed"


def _is_retry(line: Mapping) -> bool:
    return line["retried"] is True


@dataclass(frozen=True)
class Stats:
    pages: int = 0
    rows: int = 0
    bytes: int = 0
    wire_bytes: int = 0
    max_bytes: int = 0
    max_elapsed_ms: float = 0.0
    sum_elapsed_ms: float = 0.0

    def add(self, metrics: PageMetrics) -> "Stats":
        return Stats(
            pages=self.pages + 1,
            rows=self.rows + (metrics.rows or 0),
            bytes=self.bytes + metrics.bytes,
            wire_bytes=self.wire_bytes + metrics.wire_bytes,
            max_bytes=max(self.max_bytes, metrics.bytes),
            max_elapsed_ms=max(self.max_elapsed_ms, metrics.elapsed_ms),
            sum_elapsed_ms=round(self.sum_elapsed_ms + metrics.elapsed_ms, 1),
        )

    def as_dict(self) -> dict:
        return {
            "pages": self.pages,
            "rows": self.rows,
            "bytes": self.bytes,
            "wire_bytes": self.wire_bytes,
            "max_bytes": self.max_bytes,
            "max_elapsed_ms": self.max_elapsed_ms,
            "sum_elapsed_ms": self.sum_elapsed_ms,
        }


Key = tuple[str, str | None]  # (resource or "v1", day for rs_series_day)


@dataclass(frozen=True)
class Tally:
    """Per-resource totals of one pull; add() returns a new Tally."""

    stats: Mapping[Key, Stats] = field(default_factory=lambda: MappingProxyType({}))
    v1_total: int | None = None

    def add(self, key: Key, page: Page) -> "Tally":
        stats = MappingProxyType({**self.stats, key: self.stats.get(key, Stats()).add(page.metrics)})
        first_v1 = key[0] == "v1" and page.metrics.page == 1
        return Tally(stats=stats, v1_total=page.body.get("total") if first_v1 else self.v1_total)

    def get(self, key: Key) -> Stats:
        return self.stats.get(key, Stats())


@dataclass(frozen=True)
class Found:
    """One page's pass in the worker thread: pan hits by path; for a v2 page or the manifest, the resource held to the
    strict contract and its first failing field path (None when it passed). v1 pages are not held to it."""

    hits: Mapping[str, int]
    checked: str | None = None
    miss: str | None = None


@dataclass(frozen=True)
class Miss:
    """A resource's pages that failed the strict contract: how many, and the first one's field path (never a value)."""

    failures: int
    first_path: str

    def as_dict(self) -> dict:
        return {"failures": self.failures, "first_path": self.first_path}


@dataclass(frozen=True)
class Scan:
    """--scan's hits by path (never a value), its contract checks, and the thread time they took, which run_ms leaves out."""

    hits: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    elapsed_ms: float = 0.0
    checked: int = 0
    misses: Mapping[str, Miss] = field(default_factory=lambda: MappingProxyType({}))

    def add(self, found: Found, elapsed_ms: float) -> "Scan":
        return Scan(
            hits=MappingProxyType(pan.add_hits(dict(self.hits), dict(found.hits))),
            elapsed_ms=round(self.elapsed_ms + elapsed_ms, 1),
            checked=self.checked + (found.checked is not None),
            misses=_with_miss(self.misses, found),
        )


def _with_miss(misses: Mapping[str, Miss], found: Found) -> Mapping[str, Miss]:
    if found.checked is None or found.miss is None:
        return misses
    before = misses.get(found.checked)
    miss = Miss(1, found.miss) if before is None else Miss(before.failures + 1, before.first_path)
    return MappingProxyType({**misses, found.checked: miss})


@dataclass(frozen=True)
class Pull:
    manifest: Manifest
    tally: Tally
    days: tuple[str, ...]
    run_ms: float
    scan: Scan | None = None


def series_days(manifest: Manifest, count: int) -> tuple[str, ...]:
    """The last `count` days of snapshotDays; the default 1 is latestSnapshot."""
    return tuple(list(manifest.snapshot_days)[-count:]) if count else ()


async def walk(client: FeedClient, manifest: Manifest, options: Options) -> AsyncIterator[tuple[Key, Page]]:
    """Every page of one pull in plan 5.2 order: v1 when there is a token, the eight counted resources, the series days."""
    if client.has_v1:
        async for page in client.v1_pages(manifest):
            yield ("v1", None), page
    for resource in COUNTED_RESOURCES:
        async for page in client.pages(resource, manifest=manifest, limit=options.limits.get(resource)):
            yield (resource, None), page
    for day in series_days(manifest, options.series_days):
        async for page in client.pages(SERIES_RESOURCE, manifest=manifest, day=day, limit=options.limits.get(SERIES_RESOURCE)):
            yield (SERIES_RESOURCE, day), page


@dataclass(frozen=True)
class Interruption:
    """A run that drift or an expired as_of ended: why, and on which feed (critique 1.3 wants the drift rate).

    cause: drift_409 (source_changed), drift_busy_503 (source_busy after the manifest), drift_echo (a v2 page echoing
    another fingerprint, a v1 page another capturedAt, fingerprint or build), as_of_expired_400 (RealShort refused the
    as_of), as_of_expired_local.
    """

    run: int
    cause: str
    side: str
    resource: str | None

    @classmethod
    def of(cls, run: int, exc: FeedError) -> "Interruption":
        if isinstance(exc, AsOfExpiredError):
            cause = "as_of_expired_400" if exc.status == 400 else "as_of_expired_local"
        else:
            cause = {409: "drift_409", 503: "drift_busy_503"}.get(exc.status, "drift_echo")
        return cls(run=run, cause=cause, side="v1" if exc.resource == "v1" else "v2", resource=exc.resource)


@dataclass(frozen=True)
class Outcome:
    """How the runs ended: the pull or the FeedError, how many runs it took, what cut the others short."""

    runs: int
    interruptions: tuple[Interruption, ...] = ()
    busy_wait_seconds: int = 0
    pull: Pull | None = None
    error: FeedError | None = None


def scan_page(key: Key, body: Mapping) -> dict[str, int]:
    """One page's hits: a v1 page with v1's exemptions and its rules apart, a v2 page with its resource's."""
    return pan.scan_v1_page(body) if key[0] == "v1" else pan.scan_v2_page(key[0], body)


def contract_miss(check: Callable[[], object]) -> str | None:
    """The first failing field path of a strict-contract parse, None when it passes; a PageContractError names no value."""
    try:
        check()
    except contracts.PageContractError as exc:
        return exc.path or WHOLE_RECORD
    return None


def inspect_page(key: Key, body: Mapping) -> Found:
    """One page for --scan, in the worker thread: its pan hits and, for a v2 page, the strict row models."""
    if key[0] == "v1":
        return Found(scan_page(key, body))
    resource = key[0]
    return Found(scan_page(key, body), resource, contract_miss(lambda: contracts.parse_page(resource, body)))


def inspect_manifest(manifest: Manifest) -> Found:
    """manifest.meta's pan hits and the manifest held to the strict contract once more (the client did it already, so a
    failure there has ended the pull with a ContractError before this runs)."""
    return Found(pan.scan_manifest_meta(manifest.meta), contracts.MANIFEST, contract_miss(lambda: contracts.parse_manifest(dict(manifest.row))))


async def _scanned(scan: Scan | None, timer, function, *args) -> Scan | None:
    """scan plus what function(*args) finds, run in a worker thread (0.3); None when --scan is off."""
    if scan is None:
        return None
    started = timer()
    found = await asyncio.to_thread(function, *args)
    return scan.add(found, (timer() - started) * 1000)


async def pull_once(client: FeedClient, options: Options, timer) -> tuple[int, Pull | FeedError]:
    """One run: (seconds it slept on a busy manifest, the pull or the FeedError that ended it).

    A manifest that fails some other way after busy waits reports 0: manifest_when_free does not return its waits.
    """
    try:
        manifest = await client.manifest_when_free()
    except BusyTimeout as exc:
        return exc.waited, exc
    except FeedError as exc:
        return 0, exc
    tally, scan = Tally(), await _scanned(Scan() if options.scan else None, timer, inspect_manifest, manifest)
    try:
        async for key, page in walk(client, manifest, options):
            tally = tally.add(key, page)
            scan = await _scanned(scan, timer, inspect_page, key, page.body)
    except FeedError as exc:
        return sum(manifest.busy_sleeps), exc
    # From the moment the successful manifest request went out: busy waits before it are not the pull's time, nor --scan's.
    run_ms = round((timer() - manifest.metrics.started) * 1000 - (scan.elapsed_ms if scan else 0), 1)
    return sum(manifest.busy_sleeps), Pull(manifest, tally, series_days(manifest, options.series_days), run_ms, scan)


async def pull_with_reruns(client: FeedClient, options: Options, recorder: Recorder, *, sleep, timer) -> Outcome:
    """Drift or an expired as_of starts the whole pull over with a new as_of, at most RERUNS times."""
    interruptions, waited = (), 0
    for run in range(1, RERUNS + 2):
        recorder.start_run(run)
        seconds, result = await pull_once(client, options, timer)
        waited = waited + seconds
        if isinstance(result, Pull):
            return Outcome(run, interruptions, waited, pull=result)
        if not isinstance(result, DriftError | AsOfExpiredError):
            return Outcome(run, interruptions, waited, error=result)
        interruptions = (*interruptions, Interruption.of(run, result))
        if run > RERUNS:
            return Outcome(run, interruptions, waited, error=result)
        if isinstance(result, DriftError):
            await sleep(DRIFT_BACKOFF_SECONDS)
    raise AssertionError("every run returns an outcome")


def retries(outcome: Outcome, recorder: Recorder) -> dict:
    """Response counts from the printed lines; causes are per interrupted run. manifest_busy_503 are waits, not drift;
    read_failed_retries are the requests repeated after a read_failed (each page once at most)."""
    return {
        "drift_409": recorder.count(_is_409),
        "busy_503": recorder.count(_is_busy),
        "manifest_busy_503": recorder.count(_is_manifest_busy),
        "busy_wait_seconds": outcome.busy_wait_seconds,
        "read_failed_503": recorder.count(_is_read_failed),
        "read_failed_retries": recorder.count(_is_retry),
        "as_of_expired": sum(item.cause.startswith("as_of_expired") for item in outcome.interruptions),
        "reruns": outcome.runs - 1,
        "causes": [asdict(item) for item in outcome.interruptions],
    }


def _resource_summary(stats: Stats, expected: int) -> dict:
    return {**stats.as_dict(), "expected_rows": expected, "rows_match": stats.rows == expected}


def _title_field(path: str) -> bool:
    resource, _, column = path.partition(".")
    return resource in ROW_RESOURCES and any(field in (path, f"*.{column}") for field in TITLE_SCRUB_FIELDS)


def _title_hits(scrub: Mapping[str, int]) -> dict[str, int]:
    return {path: count for path, count in scrub.items() if count > 0 and _title_field(path)}


def evaluate_gates(pull: Pull, resources: Mapping[str, dict], series: Mapping[str, dict], *, v1_enabled: bool) -> dict:
    """Plan 1490-1496 on the v2 pages and the manifest, with P1-6's changes (a manifest_time gate of its own, a null
    sourceRevision failing); v1 is reported, not gated (it is not the new endpoint), except that pan_scan needs v1 read:
    P1 step 7 wants every path at 0, v1.rules included."""
    mismatched = [name for name, s in resources.items() if not s["rows_match"]]
    mismatched = [*mismatched, *(f"{SERIES_RESOURCE}@{day}" for day, s in series.items() if not s["rows_match"])]
    hits = _title_hits(pull.manifest.meta["scrub"])
    return {
        **_time_and_size_gates(pull),
        "row_counts": {"ok": not mismatched, "mismatched": mismatched},
        "title_scrub": {"ok": not hits, "hits": hits, **({"blocks": BLOCKS_67} if hits else {})},
        "source_revision": {"ok": True} if pull.manifest.source_revision is not None else {"ok": False, "reason": SOURCE_REVISION_NULL},
        **(_scan_gates(pull.scan, v1_scanned=v1_enabled) if pull.scan is not None else {}),
    }


def _time_and_size_gates(pull: Pull) -> dict:
    """page_time over the row pages (v2 resources and rs_series_day), manifest_time apart; page_bytes over both."""
    manifest = pull.manifest.metrics
    rows = [(_label(key), s.max_elapsed_ms, s.max_bytes) for key, s in pull.tally.stats.items() if key[0] != "v1"]
    slowest = max(rows, key=lambda item: item[1], default=(None, 0.0, 0))
    largest = max([("manifest", manifest.elapsed_ms, manifest.bytes), *rows], key=lambda item: item[2])
    return {
        "page_time": {"ok": slowest[1] < PAGE_MS_LIMIT, "limit_ms": PAGE_MS_LIMIT, "worst_ms": slowest[1], "worst": slowest[0]},
        "manifest_time": {"ok": manifest.elapsed_ms < MANIFEST_MS_LIMIT, "limit_ms": MANIFEST_MS_LIMIT, "elapsed_ms": manifest.elapsed_ms},
        "page_bytes": {"ok": largest[2] < PAGE_BYTES_LIMIT, "limit_bytes": PAGE_BYTES_LIMIT, "worst_bytes": largest[2], "worst": largest[0]},
        "run_time": {"ok": pull.run_ms < RUN_MS_LIMIT, "limit_ms": RUN_MS_LIMIT, "run_ms": pull.run_ms},
    }


def _scan_gates(scan: Scan, *, v1_scanned: bool) -> dict:
    gate = {"ok": not scan.hits and v1_scanned, "paths": len(scan.hits), "hits": sum(scan.hits.values())}
    pan_scan = gate if v1_scanned else {**gate, "unscanned": list(V1_SCAN_PATHS), "reason": V1_UNSCANNED}
    failures = {resource: miss.as_dict() for resource, miss in sorted(scan.misses.items())}
    return {"pan_scan": pan_scan, "contract": {"ok": not failures, "pages": scan.checked, "failures": failures}}


def _scan_summary(scan: Scan | None) -> dict:
    if scan is None:
        return {}
    hits = dict(sorted(scan.hits.items()))
    return {"scan": {"hits": hits, "total": sum(hits.values()), "elapsed_ms": scan.elapsed_ms}}


def _label(key: Key) -> str:
    return key[0] if key[1] is None else f"{key[0]}@{key[1]}"


def _v1_summary(pull: Pull, enabled: bool) -> dict:
    if not enabled:
        return {"skipped": True, "reason": f"没有 v1 token（--v1-token-file 或 {FEED_TOKEN_ENV}），跳过 v1"}
    stats = pull.tally.get(("v1", None))
    return {**stats.as_dict(), "total": pull.tally.v1_total, "rows_match_total": stats.rows == pull.tally.v1_total}


def summarize(outcome: Outcome, recorder: Recorder, *, v1_enabled: bool) -> dict:
    pull = outcome.pull
    manifest = pull.manifest
    resources = {name: _resource_summary(pull.tally.get((name, None)), manifest.counts[name]) for name in COUNTED_RESOURCES}
    series = {day: _resource_summary(pull.tally.get((SERIES_RESOURCE, day)), manifest.row_cap(SERIES_RESOURCE, day)) for day in pull.days}
    gates = evaluate_gates(pull, resources, series, v1_enabled=v1_enabled)
    failed = [name for name, gate in gates.items() if not gate["ok"]]
    return {
        "summary": True,
        "ok": not failed,
        "failed_gates": failed,
        "as_of": manifest.as_of_text,
        "runs": outcome.runs,
        "retries": retries(outcome, recorder),
        "run_ms": pull.run_ms,
        "manifest": {key: manifest.metrics.line()[key] for key in ("elapsed_ms", "bytes", "wire_bytes")},
        "v1": _v1_summary(pull, v1_enabled),
        "resources": resources,
        "series_days": series,
        "scrub": dict(manifest.meta["scrub"]),
        "warnings": [warning["code"] for warning in manifest.meta["warnings"]],
        "source_revision_null": manifest.source_revision is None,
        **_scan_summary(pull.scan),
        "gates": gates,
    }


def failure_summary(outcome: Outcome, recorder: Recorder) -> dict:
    exc = outcome.error
    return {"summary": True, "ok": False, "error_type": type(exc).__name__, "error": str(exc), "runs": outcome.runs, "retries": retries(outcome, recorder)}


def internal_summary(exc: Exception) -> dict:
    """Not a FeedError: its class and innermost frame only, since a message from outside this package may quote a value."""
    frames = traceback.extract_tb(exc.__traceback__)
    where = f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"
    return {"summary": True, "ok": False, "error_type": type(exc).__name__, "error": "意外错误，不是 RealShort 的已知失败；按类名与位置排查", "where": where}


def _conclude(outcome: Outcome, recorder: Recorder, *, v1_enabled: bool) -> tuple[dict, int]:
    if outcome.error is not None:
        return failure_summary(outcome, recorder), EXIT_FETCH
    summary = summarize(outcome, recorder, v1_enabled=v1_enabled)
    return summary, EXIT_OK if summary["ok"] else EXIT_GATES


async def dry_run(options: Options, secrets: Secrets, *, transport=None, clock=None, sleep=None, timer=None, out: TextIO | None = None) -> int:
    out = out or sys.stdout
    sleep, timer = sleep or asyncio.sleep, timer or time.perf_counter
    recorder = Recorder(out)
    injected = {name: value for name, value in (("transport", transport), ("clock", clock)) if value is not None}
    client = FeedClient(
        base_url=options.base_url,
        export_token=secrets.export_token,
        feed_token=secrets.feed_token,
        bypass=secrets.bypass,
        sleep=sleep,
        timer=timer,
        on_response=recorder,
        **injected,
    )
    async with client:
        try:
            outcome = await pull_with_reruns(client, options, recorder, sleep=sleep, timer=timer)
            summary, code = _conclude(outcome, recorder, v1_enabled=client.has_v1)
        except Exception as exc:  # reported, not swallowed: exit 4 and a summary line, never the message
            summary, code = internal_summary(exc), EXIT_INTERNAL
    _emit(out, summary)
    return code


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None, transport=None, clock=None, sleep=None, timer=None) -> int:
    try:
        options = parse_args(argv)
    except SystemExit as exc:  # argparse printed a message without values (_Parser.error) or the help text
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE
    try:
        secrets = load_secrets(options, os.environ if env is None else env)
        return asyncio.run(dry_run(options, secrets, transport=transport, clock=clock, sleep=sleep, timer=timer))
    except (UsageError, ConfigError) as exc:  # ConfigError here comes from FeedClient(): a bad --base-url or secret text
        print(f"{PROG}: {exc}", file=sys.stderr)
        return EXIT_USAGE


# The same entry as python -m ggwork_pick.mirror.client (PROG): run as a module, this one used to exit 0 silently.
if __name__ == "__main__":
    raise SystemExit(main())
