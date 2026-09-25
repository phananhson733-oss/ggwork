"""`python -m ggwork_pick.observe.trends.stage0 <plan|run|report|fixtures>` (plan TR-05; runbook trends-stage0.md).

- plan: the two task lists from controls.json (day1.json, day2.json); refused for a day that has started.
- run --day N: that day only, through TR-03's machines and TR-04's file state; --dry-run lists what is left and sends
  nothing; --init-state creates the state file on the first run. Refused without a plan, with a plan written against
  another control list, or without PICK_OBS_STATE_KEY / PICK_OBS_STATE_KEY_FILE.
- report: the report from whatever days have run (an interim one with day 1 alone); --manual adds U5's exports.
- fixtures --day N: the day's captured answers as redacted fixtures, for a person to review.

Exit status as the observe commands: 0 done, 1 the day ran but left units uncovered (a rerun resumes), 2 refused before
any request, 3 the state file is missing or locked. Usage errors never echo what was typed.
"""

import argparse
import asyncio
import json
import os
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import httpx

from ggwork_pick.observe.admin.args import CommandParser
from ggwork_pick.observe.clock import Clock, SystemClock, random_source
from ggwork_pick.observe.errors import ExitCode, Refused, describe_error, exit_code_for
from ggwork_pick.observe.state import file_state_store
from ggwork_pick.observe.trends.stage0 import DAYS, Controls, DayPlan, build_plan, check_plan_matches, load_controls
from ggwork_pick.observe.trends.stage0_fixtures import write_fixtures
from ggwork_pick.observe.trends.stage0_metrics import DEFAULT_N
from ggwork_pick.observe.trends.stage0_report import DEFAULT_UTC_OFFSET_MINUTES, build_report, load_days, write_report
from ggwork_pick.observe.trends.stage0_run import ARTIFACTS_ROOT, DayRunner, Stage0Paths, dry_run_lines, load_results, write_private

PROG = "python -m ggwork_pick.observe.trends.stage0"
MAX_MANUAL_BYTES = 1024 * 1024


def _parser() -> CommandParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", type=Path, default=ARTIFACTS_ROOT, help="artifacts root (default: %(default)s)")
    parser = CommandParser(prog=PROG, description="Trends 阶段 0：生成任务清单、按天运行、出报告、导出脱敏夹具")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=CommandParser)
    commands.add_parser("plan", parents=[common], help="从 controls.json 生成两天的任务清单")
    run = commands.add_parser("run", parents=[common], help="只跑指定的一天")
    run.add_argument("--day", type=int, choices=DAYS, required=True)
    run.add_argument("--init-state", action="store_true", help="第一次运行时创建状态文件")
    run.add_argument("--dry-run", action="store_true", help="只列出待跑的单元，不发请求")
    report = commands.add_parser("report", parents=[common], help="出报告（只有第一天时是中期报告）")
    report.add_argument("--manual", type=Path, action="append", default=[], help="U5：浏览器导出的走势 CSV，可重复")
    report.add_argument("--n", type=int, default=DEFAULT_N, help="日级可见的非零日下限（默认 %(default)s）")
    report.add_argument("--utc-offset-minutes", type=int, default=DEFAULT_UTC_OFFSET_MINUTES, help="导出 CSV 的浏览器时区（默认 %(default)s）")
    fixtures = commands.add_parser("fixtures", parents=[common], help="把某一天抓到的回答导出成脱敏夹具")
    fixtures.add_argument("--day", type=int, choices=DAYS, required=True)
    return parser


# ---- inputs --------------------------------------------------------------------------------------------------------


def _controls(paths: Stage0Paths) -> Controls:
    if not paths.controls_file.exists():
        raise Refused(f"找不到对照清单 {paths.controls_file.name}：先在产物目录放好 controls.json")
    try:
        return load_controls(paths.controls_file)
    except ValueError as error:
        raise Refused(str(error)) from None


def _plan(paths: Stage0Paths, day: int, controls: Controls) -> DayPlan:
    path = paths.plan_file(day)
    if not path.exists():
        raise Refused(f"第 {day} 天的任务清单不存在：先运行 plan")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        plan = DayPlan.from_document(document)
        check_plan_matches(plan, document, controls)
    except ValueError as error:
        raise Refused(f"第 {day} 天的任务清单不能用（{error}）：重新运行 plan") from None
    return plan


# ---- commands ------------------------------------------------------------------------------------------------------


def _cmd_plan(paths: Stage0Paths, out: TextIO) -> int:
    controls = _controls(paths)
    started = [day for day in DAYS if load_results(paths.run_dir(day))]
    if started:
        raise Refused(f"第 {started[0]} 天已经开跑，任务清单不能再改；要重来，先把 runs/ 与 raw/ 移走")
    try:
        plans = build_plan(controls)
    except ValueError as error:
        raise Refused(str(error)) from None
    for plan in plans:
        write_private(paths.plan_file(plan.day), json.dumps(plan.to_document(controls), ensure_ascii=False, indent=1) + "\n")
    counts = "，".join(f"第 {plan.day} 天 {len(plan.units)} 个单元 {plan.http} 次请求" for plan in plans)
    print(f"已写任务清单：{counts}；两天合计 {sum(plan.http for plan in plans)} 次", file=out)
    return int(ExitCode.OK)


def _cmd_run(paths: Stage0Paths, args, out: TextIO, *, environ: Mapping[str, str], clock: Clock, transport, rng) -> int:
    controls = _controls(paths)
    plan = _plan(paths, args.day, controls)
    if args.dry_run:
        done = load_results(paths.run_dir(args.day))
        print("\n".join(dry_run_lines(plan, controls, lambda key: (done.get(key) or {}).get("status") is not None)), file=out)
        return int(ExitCode.OK)
    store = file_state_store(paths.state_file, environ=environ)
    runner = DayRunner(plan=plan, controls=controls, store=store, paths=paths, clock=clock, rng=rng, transport=transport, environ=environ, log=out)
    outcome = asyncio.run(runner.run(init_state=args.init_state))
    counts = f"发出 {outcome.requests} 次请求，覆盖 {len(outcome.covered)} 个单元，未覆盖 {len(outcome.uncovered)} 个"
    print(f"第 {args.day} 天（目标日 {outcome.target_date}）：{counts}", file=out)
    if outcome.extinguished:
        print(f"熔断熄火：{outcome.extinguished}（当天不再发请求）", file=out)
    return int(ExitCode.OK if not outcome.uncovered else ExitCode.FAILED)


def _manual_files(files: Sequence[Path]) -> list[tuple[str, str]]:
    found = []
    for path in files:
        if not path.is_file() or path.stat().st_size > MAX_MANUAL_BYTES:
            raise Refused(f"人工导出文件 {path.name} 不存在、不是文件或太大")
        found.append((path.name, path.read_text(encoding="utf-8-sig")))
    return found


def _cmd_report(paths: Stage0Paths, args, out: TextIO, *, clock: Clock) -> int:
    controls = _controls(paths)
    try:
        days = load_days(paths)
    except ValueError as error:
        raise Refused(f"任务清单不能用（{error}）") from None
    report = build_report(controls, days, generated_at=clock.now(), n=args.n, manual=_manual_files(args.manual), utc_offset_minutes=args.utc_offset_minutes)
    path = write_report(paths, report)
    print(f"已写{'中期' if report.interim else ''}报告：{path}", file=out)
    return int(ExitCode.OK)


def _cmd_fixtures(paths: Stage0Paths, args, out: TextIO) -> int:
    written = write_fixtures(paths, args.day, _controls(paths))
    print(f"已导出 {len(written)} 个脱敏夹具到 {paths.root / 'fixtures' / f'day{args.day}'}；人工看过再拷进 tests/fixtures/trends", file=out)
    return int(ExitCode.OK)


def _dispatch(args, out: TextIO, *, environ: Mapping[str, str], clock: Clock, transport, rng) -> int:
    paths = Stage0Paths(args.root.expanduser())
    if args.command == "plan":
        return _cmd_plan(paths, out)
    if args.command == "run":
        return _cmd_run(paths, args, out, environ=environ, clock=clock, transport=transport, rng=rng)
    if args.command == "report":
        return _cmd_report(paths, args, out, clock=clock)
    return _cmd_fixtures(paths, args, out)


def main(
    argv: Sequence[str],
    *,
    out: TextIO | None = None,
    err: TextIO | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    rng: random.Random | None = None,
) -> int:
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = _parser().parse_args(list(argv))
    except SystemExit as stop:
        return int(stop.code) if isinstance(stop.code, int) else int(ExitCode.REFUSED)
    try:
        return _dispatch(
            args, out, environ=os.environ if environ is None else environ, clock=clock or SystemClock(), transport=transport, rng=rng or random_source()
        )
    except Exception as error:  # noqa: BLE001 - every failure leaves as a value-free line and an exit status
        print(f"{PROG}: {describe_error(error)}", file=err)
        return int(exit_code_for(error))
