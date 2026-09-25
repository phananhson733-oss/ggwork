"""`python -m ggwork_pick.observe.trends [run|status] [--selfcheck-only]`: the pick-obs-trends cron (plan TR-14, TR-15).

- `run` (the default): one trigger of the nightly session (run.py). The cron fires every 30 minutes from 20:00 to 01:30
  UTC; a trigger before the mode's start or after the 01:45 deadline does nothing and exits 0.
- `status`: a read-only look at the channel (cli_status.py), no lease taken.
- `--selfcheck-only`: the start-up self-check and nothing else (selfcheck.selfcheck_only; S6 reads its line).

Configuration is checked before anything is read or sent, and a bad one exits 2: the mode and the other settings
(settings.py), the canary's control list (canary.py; the default is the package's trends/canary_controls.json), the
state key (crypto.load_cipher) and the egress echo URL (egress.py, off unless set). The stable mode's task source is
TR-18's WatchTaskSource: until it is registered here, stable is refused.

Exit status (errors.ExitCode): 0 done or nothing to do; 1 failed part-way (a lease another process holds, a lock wait
that ran out, a lost lease): the next trigger resumes; 2 refused before any HTTP; 3 the state or runtime row cannot be
read; 130 interrupted. A failure prints its class and SQLSTATE and our own value-free message, never an argument, a
DSN or a response.
"""

import argparse
import asyncio
import logging
import os
import random
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

import httpx

from ggwork_pick.observe.clock import Clock, SystemClock, random_source
from ggwork_pick.observe.crypto import load_cipher
from ggwork_pick.observe.errors import ExitCode, Refused, describe_error, exit_code_for
from ggwork_pick.observe.selfcheck import selfcheck_only
from ggwork_pick.observe.trends import pacing
from ggwork_pick.observe.trends.canary import CanaryTaskSource, load_controls
from ggwork_pick.observe.trends.cli_status import print_status
from ggwork_pick.observe.trends.egress import egress_from_env
from ggwork_pick.observe.trends.run import TaskSource, Wiring, run_trends
from ggwork_pick.observe.trends.settings import Settings, settings_from

PROG = "python -m ggwork_pick.observe.trends"
COMMANDS = ("run", "status")


class _Parser(argparse.ArgumentParser):
    """Usage errors print the usage, never what was typed (admin/args.py's rule)."""

    def error(self, message: str) -> NoReturn:
        del message
        self.print_usage(sys.stderr)
        self.exit(int(ExitCode.REFUSED), f"{self.prog}: 参数有误（为免泄露，不回显输入的值）；用 --help 查看用法\n")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = _Parser(prog=PROG, description="选剧观测雷达的 Trends 采集（pick-obs-trends cron）")
    parser.add_argument("command", nargs="?", default="run", choices=COMMANDS, help="run：跑一次触发（默认）；status：只读查看")
    parser.add_argument("--selfcheck-only", action="store_true", help="只做启动自检，不取租约、不发请求")
    return parser.parse_args(list(argv))


def source_for(settings: Settings, controls_path: Path | None) -> TaskSource:
    """The mode's task source: the canary's control list and fresh titles; stable waits for TR-18."""
    if settings.canary:
        return CanaryTaskSource(load_controls(controls_path), granularities=settings.granularities, related=settings.related)
    raise Refused("stable 模式的任务来源是 TR-18 的 WatchTaskSource，尚未接入：金丝雀结束、TR-18 部署之后再切 stable（计划第 9 节）")


async def run_command(
    environ: Mapping[str, str],
    *,
    clock: Clock,
    rng: random.Random,
    transport: httpx.AsyncBaseTransport | None,
    pacer: pacing.Pacer | None,
    controls_path: Path | None,
) -> int:
    settings = settings_from(environ)
    source = source_for(settings, controls_path)
    cipher = load_cipher(environ)
    async with egress_from_env(environ, clock=clock) as egress:
        wiring = Wiring(clock=clock, rng=rng, transport=transport, pacer=pacer, egress=egress if egress.enabled else None)
        return int(await run_trends(settings, source, cipher=cipher, environ=environ, wiring=wiring))


async def amain(
    argv: Sequence[str],
    *,
    environ: Mapping[str, str] | None = None,
    clock: Clock | None = None,
    rng: random.Random | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    pacer: pacing.Pacer | None = None,
    controls_path: Path | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """The entry point with its parts injectable (tests pass a ManualClock, a MockTransport, a no-op pacer)."""
    env = os.environ if environ is None else environ
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = parse_args(argv)
    except SystemExit as exc:  # --help, or a usage error that printed the usage
        return int(exc.code or 0)
    try:
        if args.selfcheck_only:
            print((await selfcheck_only("trends", environ=env)).line(), file=out)
            return int(ExitCode.OK)
        if args.command == "status":
            return await print_status(env, out)
        wired_clock = clock or SystemClock()
        return await run_command(env, clock=wired_clock, rng=rng or random_source(), transport=transport, pacer=pacer, controls_path=controls_path)
    except (Exception, KeyboardInterrupt, BaseExceptionGroup) as exc:  # a group can hold a KeyboardInterrupt
        print(f"{PROG}: {describe_error(exc)}", file=err)
        return int(exit_code_for(exc))


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    return asyncio.run(amain(sys.argv[1:] if argv is None else argv))


if __name__ == "__main__":
    raise SystemExit(main())
