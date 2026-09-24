"""`python -m ggwork_pick.mirror.client --dry-run`: one full pull, measured and thrown away (plan P1-6, 1486-1496).

stdout gets one JSON line per HTTP response (metrics only: PageMetrics.line() plus the run number) and a summary line
last. Nothing is written to disk; no database or app config is read. Tokens come from files or the environment, the
deployment-protection bypass only from a file: a secret on the command line ends up in shell history and `ps`.

Exit status: 0 every gate passed, 1 a gate failed, 2 usage, 3 the pull itself failed.

Page iteration (walk) and bookkeeping (Tally) are kept apart, so --scan (after P2-2b) can read the same pages.
"""

import argparse
import asyncio
import fnmatch
import json
import os
import re
import sys
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import NoReturn, TextIO

from ggwork_pick.mirror.client import COUNTED_RESOURCES, ROW_RESOURCES, SERIES_RESOURCE, FeedClient, Manifest, Page, PageMetrics
from ggwork_pick.mirror.errors import AsOfExpiredError, BusyTimeout, ConfigError, DriftError, FeedError
from ggwork_pick.mirror.feed_shape import MAX_LIMITS, SERIES_DAY_SPAN

PROG = "python -m ggwork_pick.mirror.client"
EXPORT_TOKEN_ENV = "PICK_REALSHORT_EXPORT_TOKEN"
FEED_TOKEN_ENV = "PICK_REALSHORT_FEED_TOKEN"
# Gates of plan 1490-1496: every page under 15 seconds and 3,000,000 bytes, the whole pull under 3 minutes.
PAGE_MS_LIMIT = 15_000
PAGE_BYTES_LIMIT = 3_000_000
RUN_MS_LIMIT = 180_000
# A title-like hit means RealShort's scrub rewrote drama names: merging realshort#67 would ship that (critique 1.2).
TITLE_SCRUB_PATTERNS = ("*.title", "*.title_cn", "*.description", "rs_ids.title", "catalog_posted.title", "*.book_title")
BLOCKS_67 = "阻断 #67 合并"
# RealShort redeploys often and the fingerprint carries the commit SHA (critique 1.3): start over up to three times.
RERUNS = 3
DRIFT_BACKOFF_SECONDS = 90  # plan 5.2 step 3
EXIT_OK, EXIT_GATES, EXIT_USAGE, EXIT_FETCH = 0, 1, 2, 3
_NUMBER = re.compile(r"^[0-9]{1,6}$")


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


@dataclass(frozen=True)
class Secrets:
    export_token: str = field(repr=False)
    feed_token: str | None = field(repr=False)
    bypass: str | None = field(repr=False)


def _safe_usage_error(message: str) -> str:
    """argparse quotes the offending value; a secret pasted by mistake must not reach the terminal."""
    if message.startswith("unrecognized arguments"):
        return "有不认识的参数（token 与 bypass 只收文件：--token-file、--v1-token-file、--bypass-header-file）"
    if message.startswith("argument "):
        return ": ".join(message.split(": ")[:2])
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
    parser.add_argument("--limit", type=_limit_item, action="append", default=[], metavar="资源=条数", help="覆盖某个资源的页大小，可重复")
    return parser


def parse_args(argv: Sequence[str] | None) -> Options:
    args = build_parser().parse_args(argv)
    return Options(args.base_url, args.bypass_header_file, args.token_file, args.v1_token_file, args.series_days, MappingProxyType(dict(args.limit)))


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
    """The client's on_response: prints each response's metrics as it happens and keeps them for the summary's counts."""

    def __init__(self, out: TextIO):
        self._out = out
        self.lines: list[dict] = []
        self.run = 1
        self.as_of_expired = 0
        self.busy_waited = 0

    def __call__(self, metrics: PageMetrics) -> None:
        line = {**metrics.line(), "run": self.run}
        self.lines.append(line)
        _emit(self._out, line)

    def retries(self) -> dict:
        return {
            "drift_409": sum(line["status"] == 409 for line in self.lines),
            "busy_503": sum(line.get("error") == "source_busy" for line in self.lines),
            "busy_wait_seconds": self.busy_waited,
            "read_failed_503": sum(line.get("error") == "read_failed" for line in self.lines),
            "as_of_expired": self.as_of_expired,
            "reruns": self.run - 1,
        }


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
class Pull:
    manifest: Manifest
    tally: Tally
    days: tuple[str, ...]
    run_ms: float


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


async def pull_once(client: FeedClient, options: Options, recorder: Recorder, timer) -> Pull:
    manifest = await client.manifest_when_free()
    recorder.busy_waited += sum(manifest.busy_sleeps)
    tally = Tally()
    async for key, page in walk(client, manifest, options):
        tally = tally.add(key, page)
    # From the moment the successful manifest request went out: busy waits before it are not the pull's time.
    return Pull(manifest, tally, series_days(manifest, options.series_days), round((timer() - manifest.metrics.started) * 1000, 1))


async def pull_with_reruns(client: FeedClient, options: Options, recorder: Recorder, *, sleep, timer) -> Pull:
    """Drift or an expired as_of starts the whole pull over with a new as_of, at most RERUNS times."""
    for run in range(1, RERUNS + 2):
        recorder.run = run
        try:
            return await pull_once(client, options, recorder, timer)
        except BusyTimeout as exc:
            recorder.busy_waited += exc.waited
            raise
        except AsOfExpiredError:
            recorder.as_of_expired += 1
            if run > RERUNS:
                raise
        except DriftError:
            if run > RERUNS:
                raise
            await sleep(DRIFT_BACKOFF_SECONDS)
    raise AssertionError("every run returns or raises")


def _resource_summary(stats: Stats, expected: int) -> dict:
    return {**stats.as_dict(), "expected_rows": expected, "rows_match": stats.rows == expected}


def _title_hits(scrub: Mapping[str, int]) -> dict[str, int]:
    return {path: count for path, count in scrub.items() if count > 0 and any(fnmatch.fnmatchcase(path, p) for p in TITLE_SCRUB_PATTERNS)}


def evaluate_gates(pull: Pull, resources: Mapping[str, dict], series: Mapping[str, dict]) -> dict:
    """Plan 1490-1496 on the v2 pages and the manifest; v1 is reported, not gated (it is not the new endpoint)."""
    manifest = pull.manifest.metrics
    measured = [("manifest", manifest.elapsed_ms, manifest.bytes)]
    measured = [*measured, *((_label(key), s.max_elapsed_ms, s.max_bytes) for key, s in pull.tally.stats.items() if key[0] != "v1")]
    slowest, largest = max(measured, key=lambda item: item[1]), max(measured, key=lambda item: item[2])
    mismatched = [name for name, s in resources.items() if not s["rows_match"]]
    mismatched = [*mismatched, *(f"{SERIES_RESOURCE}@{day}" for day, s in series.items() if not s["rows_match"])]
    hits = _title_hits(pull.manifest.meta["scrub"])
    return {
        "page_time": {"ok": slowest[1] < PAGE_MS_LIMIT, "limit_ms": PAGE_MS_LIMIT, "worst_ms": slowest[1], "worst": slowest[0]},
        "page_bytes": {"ok": largest[2] < PAGE_BYTES_LIMIT, "limit_bytes": PAGE_BYTES_LIMIT, "worst_bytes": largest[2], "worst": largest[0]},
        "run_time": {"ok": pull.run_ms < RUN_MS_LIMIT, "limit_ms": RUN_MS_LIMIT, "run_ms": pull.run_ms},
        "row_counts": {"ok": not mismatched, "mismatched": mismatched},
        "title_scrub": {"ok": not hits, "hits": hits, **({"blocks": BLOCKS_67} if hits else {})},
    }


def _label(key: Key) -> str:
    return key[0] if key[1] is None else f"{key[0]}@{key[1]}"


def _v1_summary(pull: Pull, enabled: bool) -> dict:
    if not enabled:
        return {"skipped": True, "reason": f"没有 v1 token（--v1-token-file 或 {FEED_TOKEN_ENV}），跳过 v1"}
    stats = pull.tally.get(("v1", None))
    return {**stats.as_dict(), "total": pull.tally.v1_total, "rows_match_total": stats.rows == pull.tally.v1_total}


def summarize(pull: Pull, recorder: Recorder, *, v1_enabled: bool) -> dict:
    manifest = pull.manifest
    resources = {name: _resource_summary(pull.tally.get((name, None)), manifest.counts[name]) for name in COUNTED_RESOURCES}
    series = {day: _resource_summary(pull.tally.get((SERIES_RESOURCE, day)), manifest.row_cap(SERIES_RESOURCE, day)) for day in pull.days}
    gates = evaluate_gates(pull, resources, series)
    failed = [name for name, gate in gates.items() if not gate["ok"]]
    return {
        "summary": True,
        "ok": not failed,
        "failed_gates": failed,
        "as_of": manifest.as_of_text,
        "runs": recorder.run,
        "retries": recorder.retries(),
        "run_ms": pull.run_ms,
        "manifest": {key: manifest.metrics.line()[key] for key in ("elapsed_ms", "bytes", "wire_bytes")},
        "v1": _v1_summary(pull, v1_enabled),
        "resources": resources,
        "series_days": series,
        "scrub": dict(manifest.meta["scrub"]),
        "warnings": [warning["code"] for warning in manifest.meta["warnings"]],
        "source_revision_null": manifest.source_revision is None,
        "gates": gates,
    }


def failure_summary(exc: FeedError, recorder: Recorder) -> dict:
    return {"summary": True, "ok": False, "error_type": type(exc).__name__, "error": str(exc), "runs": recorder.run, "retries": recorder.retries()}


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
            pull = await pull_with_reruns(client, options, recorder, sleep=sleep, timer=timer)
        except FeedError as exc:
            _emit(out, failure_summary(exc, recorder))
            return EXIT_FETCH
    summary = summarize(pull, recorder, v1_enabled=client.has_v1)
    _emit(out, summary)
    return EXIT_OK if summary["ok"] else EXIT_GATES


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
