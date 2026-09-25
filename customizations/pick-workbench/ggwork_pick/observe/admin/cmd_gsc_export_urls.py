"""`python -m ggwork_pick.observe.admin gsc-export-urls`: the 90-day page URL list for RealShort's legacy export, and
the positive-control candidates for stage 0 (plan TR-07, TR-08, TR-05).

Credentials as for gsc-probe (exit 2 before any request when they are missing). The window ends on the last PT day
before today. Writes gsc-urls-<date>.jsonl, gsc-urls-<date>.manifest.json and, unless --no-candidates,
gsc-positive-controls-<date>.json to --out-dir, never overwriting an earlier run. Nothing is written when a request
fails or the request budget runs out (exit 1). A list that could not be split short of rowLimit everywhere is written
and marked incomplete in its manifest, and the command exits 1 so that it is not handed on unread. See
docs/pick-workbench/observe-runbook/gsc-probe.md.
"""

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import httpx

from ggwork_pick.observe.admin.args import command_parser
from ggwork_pick.observe.clock import Clock, SystemClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.gsc import export_urls
from ggwork_pick.observe.gsc.artifacts import ensure_folder, run_paths, write_private
from ggwork_pick.observe.gsc.client import GscClient, load_config
from ggwork_pick.observe.gsc.command import add_common, bounded_int, out_folder
from ggwork_pick.observe.gsc.pacer import Pacer

NAME = "gsc-export-urls"
DESCRIPTION = "近 90 天 GSC 页面 URL 清单（给 RealShort 旧页解析导出）与阶段 0 正对照候选（TR-07）"
LIST_NAMES = ("gsc-urls-{stamp}.jsonl", "gsc-urls-{stamp}.manifest.json")
CANDIDATES_NAME = "gsc-positive-controls-{stamp}.json"
DEFAULT_MAX_REQUESTS = 400
DEFAULT_CANDIDATES_SHOWN = 60


def _parser():
    parser = add_common(command_parser(NAME, DESCRIPTION))
    days = bounded_int(1, export_urls.MAX_DAYS)
    parser.add_argument("--days", type=days, default=export_urls.DEFAULT_DAYS, help="清单窗口的 PT 日数，截止到昨天（默认 %(default)s）")
    parser.add_argument("--data-state", choices=("all", "final"), default="all", help="清单用的 dataState（默认 %(default)s）")
    parser.add_argument("--candidate-days", type=days, default=export_urls.DEFAULT_CANDIDATE_DAYS, help="正对照候选看的查询窗口（默认 %(default)s 天）")
    parser.add_argument("--candidates-shown", type=bounded_int(1, 1000), default=DEFAULT_CANDIDATES_SHOWN, help="候选文件里列出的条数（默认 %(default)s）")
    parser.add_argument("--no-candidates", action="store_true", help="只导清单，不取查询、不出正对照候选")
    parser.add_argument("--max-requests", type=bounded_int(1, 5000), default=DEFAULT_MAX_REQUESTS, help="请求上限，到了就停、什么都不写（默认 %(default)s）")
    return parser


def _dumps(document: dict) -> bytes:
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write(folder: Path, export, found, *, site_url: str, generated_at, shown: int) -> tuple[tuple[Path, ...], str]:
    ensure_folder(folder)
    names = (*LIST_NAMES, *((CANDIDATES_NAME,) if found is not None else ()))
    paths = run_paths(folder, generated_at.date(), names)
    data = export_urls.jsonl_bytes(export.rows)
    digest = hashlib.sha256(data).hexdigest()
    manifest = export_urls.manifest(export, site_url=site_url, generated_at=generated_at, file_name=paths[0].name, digest=digest)
    contents = (data, _dumps(manifest))
    if found is not None:
        contents = (*contents, _dumps(export_urls.candidates_json(found, site_url=site_url, generated_at=generated_at, limit=shown)))
    return tuple(write_private(path, content) for path, content in zip(paths, contents)), digest


async def execute(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str],
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
    out: TextIO | None = None,
) -> int:
    options = _parser().parse_args(list(argv))
    config = load_config(environ)  # GscConfigError (exit 2) before any request
    clock, out = clock or SystemClock(), out or sys.stdout
    started = clock.now()
    end = export_urls.default_end(started)
    async with GscClient(config, transport=transport, clock=clock) as gsc:
        pacer = Pacer(clock, pause_seconds=options.pause, max_requests=options.max_requests)
        export = await export_urls.export_page_urls(gsc, pacer, end=end, days=options.days, data_state=options.data_state)
        found = None
        if not options.no_candidates:
            found = await export_urls.positive_control_candidates(gsc, pacer, end=end, days=options.candidate_days, pages=export.rows)
    paths, digest = _write(out_folder(options.out_dir), export, found, site_url=config.site_url, generated_at=started, shown=options.candidates_shown)
    complete = export.fetch.complete and (found is None or found.fetch.complete)
    print(f"清单 {len(export.rows)} 条 URL，窗口 {export.start}…{export.end}（{export.data_state}），{pacer.requests} 次请求；sha256 {digest}", file=out)
    if found is not None:
        print(f"正对照候选 {len(found.candidates)} 部", file=out)
    if not complete:
        print("清单已写出但不完整：有切片拆到一天一国仍满额，见 manifest 的 incomplete_leaves", file=out)
    print("写出：" + "、".join(str(path) for path in paths), file=out)
    return int(ExitCode.OK if complete else ExitCode.FAILED)


def main(argv: Sequence[str]) -> int:
    return asyncio.run(execute(argv, environ=os.environ))
