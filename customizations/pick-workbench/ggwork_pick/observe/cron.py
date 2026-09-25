"""What every pick-obs cron entry shares (plan TR-14, TR-21; D5, D35): the command line, the read-only commands and the
guard around them. `python -m ggwork_pick.observe.trends` builds a CronEntry and hands it to cron_main; TR-21's gsc
entry does the same with its own check and run.

- `run` (the default): one trigger of the channel (CronEntry.run).
- `status`: a read-only look at the channel (cron_status.py), as ggwp-obs-admin, no lease taken.
- a channel's own read-only commands (CronEntry.commands): the trends entry's `preflight`, tonight's task list in
  figures without a request (trends/preflight.py).
- `--selfcheck-only`: the channel's configuration check (CronEntry.check: what a run checks before it reads or sends
  anything), then the start-up self-check (selfcheck.selfcheck_only), and nothing else. S6 runs it once after the
  deploy, so a bad mode, control list, state key or egress URL shows then, not at the first night's trigger.

Before any of it, every PICK_OBS_* variable the channel does not read is named on stderr with the closest name it
does read (the name only, never a value): a variable set under a wrong name, such as plan S5's PICK_OBS_EGRESS_URL for
the code's PICK_OBS_EGRESS_ECHO_URL, would otherwise be ignored without a word. It is a warning; the command goes on.

Exit status (errors.ExitCode): 0 done or nothing to do; 1 failed part-way (the next trigger resumes); 2 refused before
any HTTP (a usage error, the configuration, the self-check); 3 the state or the runtime row cannot be read; 130
interrupted. A failure prints its class and SQLSTATE and our own value-free message, never an argument, a DSN or a
response; a usage error prints the usage and a fixed line (admin.args.CommandParser).
"""

import difflib
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TextIO

from ggwork_pick.observe.admin.args import CommandParser
from ggwork_pick.observe.cron_status import print_status
from ggwork_pick.observe.errors import ExitCode, describe_error, exit_code_for
from ggwork_pick.observe.selfcheck import selfcheck_only

COMMANDS = ("run", "status")
VARIABLE_PREFIX = "PICK_OBS_"
# selfcheck's EXPECTED_COLLECTOR_VARIABLE and EXPECTED_ROLE_VARIABLE, by name: a collector takes only selfcheck_only
# from that door (test_write_paths); test_selfcheck_variables_named keeps the two in step.
SELFCHECK_VARIABLES = frozenset({"PICK_OBS_EXPECTED_COLLECTOR", "PICK_OBS_EXPECTED_ROLE"})

Check = Callable[[Mapping[str, str]], Awaitable[None]]
Run = Callable[[Mapping[str, str]], Awaitable[int]]
Command = Callable[[Mapping[str, str], TextIO], Awaitable[int]]  # a channel's own read-only command: its exit status


@dataclass(frozen=True)
class CronEntry:
    """One cron service's entry: its channel, how it names itself, what it reads from the environment, its
    configuration check (Refused on a bad value) and its trigger."""

    channel: str
    prog: str
    description: str
    variables: frozenset[str]  # the PICK_OBS_* names the channel reads, the self-check's included
    check: Check
    run: Run
    commands: Mapping[str, Command] = MappingProxyType({})


def parse_args(entry: CronEntry, argv: Sequence[str]):
    parser = CommandParser(prog=entry.prog, description=entry.description)
    help_text = "run：跑一次触发（默认）；status：只读查看" + "".join(f"；{name}：只读，见手册" for name in entry.commands)
    parser.add_argument("command", nargs="?", default="run", choices=(*COMMANDS, *entry.commands), help=help_text)
    parser.add_argument("--selfcheck-only", action="store_true", help="只校验配置并做启动自检，不取租约、不发请求")
    return parser.parse_args(list(argv))


def unknown_variables(environ: Mapping[str, str], known: frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(name for name in environ if name.startswith(VARIABLE_PREFIX) and name not in known))


def _warn_unknown(entry: CronEntry, environ: Mapping[str, str], err: TextIO) -> None:
    for name in unknown_variables(environ, entry.variables):
        close = difflib.get_close_matches(name, sorted(entry.variables), n=1)
        hint = f"（是不是 {close[0]}？）" if close else ""
        print(f"{entry.prog}: 环境变量 {name} 这个服务不读，不会生效{hint}", file=err)


async def _dispatch(entry: CronEntry, args, environ: Mapping[str, str], out: TextIO) -> int:
    if args.selfcheck_only:
        await entry.check(environ)
        print((await selfcheck_only(entry.channel, environ=environ)).line(), file=out)
        return int(ExitCode.OK)
    if args.command == "status":
        return await print_status(entry.channel, environ, out)
    if args.command in entry.commands:
        return await entry.commands[args.command](environ, out)
    return await entry.run(environ)


async def cron_main(
    entry: CronEntry, argv: Sequence[str], *, environ: Mapping[str, str] | None = None, out: TextIO | None = None, err: TextIO | None = None
) -> int:
    """Parse, warn about unread variables, dispatch; any failure becomes its exit status and one value-free line."""
    env = os.environ if environ is None else environ
    out, err = out or sys.stdout, err or sys.stderr
    try:
        args = parse_args(entry, argv)
    except SystemExit as exc:  # --help, or a usage error that printed the usage
        return int(exc.code or 0)
    _warn_unknown(entry, env, err)
    try:
        return await _dispatch(entry, args, env, out)
    except (Exception, KeyboardInterrupt, BaseExceptionGroup) as exc:  # a group can hold a KeyboardInterrupt
        print(f"{entry.prog}: {describe_error(exc)}", file=err)
        return int(exit_code_for(exc))
