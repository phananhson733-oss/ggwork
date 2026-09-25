"""`python -m ggwork_pick.observe.admin gsc-probe`: run the GSC probe P1-P7 once and write its report (plan TR-07).

Reads the credentials and the property the way the gsc service does (gsc/client.load_config: PICK_GSC_SITE_URL, and
PICK_GSC_SA_FILE on this machine or PICK_GSC_SA_EMAIL with PICK_GSC_SA_PRIVATE_KEY), and refuses with exit 2 before
any request when they are missing or unusable. Writes gsc-probe-<date>.md, gsc-probe-<date>.json and, unless --no-raw,
gsc-probe-<date>-raw.jsonl to --out-dir; a second run the same day takes the suffix -2, never overwriting. Exit 0 when
all seven items have a clear verdict, 1 when any is still undecided or untested (the files are written either way).
See docs/pick-workbench/observe-runbook/gsc-probe.md.
"""

import asyncio
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
from ggwork_pick.observe.gsc.artifacts import ensure_folder, run_paths, write_private
from ggwork_pick.observe.gsc.client import GscClient, GscConfig, load_config
from ggwork_pick.observe.gsc.command import add_common, out_folder
from ggwork_pick.observe.gsc.probe import ProbeContext, ProbeLog, ProbeResult, RecordingTransport, run_probe
from ggwork_pick.observe.gsc.probe_report import findings_json, raw_jsonl, render_markdown
from ggwork_pick.observe.gsc.urls import site_host_of

NAME = "gsc-probe"
DESCRIPTION = "GSC 实测 P1–P7（TR-07）：一次跑完七项，写报告、结论 JSON 与原始回答"
NAMES = ("gsc-probe-{stamp}.md", "gsc-probe-{stamp}.json")
RAW_NAME = "gsc-probe-{stamp}-raw.jsonl"


def _parser():
    parser = add_common(command_parser(NAME, DESCRIPTION))
    parser.add_argument("--no-raw", action="store_true", help="不保存原始回答（默认保存，含请求体与响应正文，不含任何请求头）")
    return parser


def _write(folder: Path, result: ProbeResult, log: ProbeLog, config: GscConfig, raw: bool) -> tuple[Path, ...]:
    ensure_folder(folder)
    paths = run_paths(folder, result.started_at.date(), (*NAMES, *((RAW_NAME,) if raw else ())))
    meta = {"site_url": config.site_url, "account": config.account.email}
    contents = (
        render_markdown(result, log, **meta).encode("utf-8"),
        (json.dumps(findings_json(result, log, **meta), ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        *((raw_jsonl(log),) if raw else ()),
    )
    return tuple(write_private(path, content) for path, content in zip(paths, contents))


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
    clock, out, log = clock or SystemClock(), out or sys.stdout, ProbeLog()
    recording = RecordingTransport(transport or httpx.AsyncHTTPTransport(), log)
    async with GscClient(config, transport=recording, clock=clock, on_response=log.on_response) as gsc:
        context = ProbeContext(gsc=gsc, clock=clock, site_host=site_host_of(config.site_url), pause_seconds=options.pause)
        result = await run_probe(context, log)
    paths = _write(out_folder(options.out_dir), result, log, config, not options.no_raw)
    for finding in result.findings:
        print(f"{finding.probe} {finding.verdict}：{finding.conclusion}", file=out)
    print("写出：" + "、".join(str(path) for path in paths), file=out)
    return int(ExitCode.OK if result.decided else ExitCode.FAILED)


def main(argv: Sequence[str]) -> int:
    return asyncio.run(execute(argv, environ=os.environ))
