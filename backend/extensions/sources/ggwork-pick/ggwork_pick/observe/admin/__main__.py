"""`python -m ggwork_pick.observe.admin <command> [args...]`: find the command module and run it (plan D35).

Commands are found by file name (cmd_reset_disable.py is `reset-disable`), and only the module being run is imported:
one command's heavy or broken imports never reach another, and nothing here pulls in the gateway. The module must
declare the NAME its file name implies and a callable main(argv) returning the exit status (None counts as 0).

The command files that exist today are cmd_gsc_export_urls.py, cmd_gsc_probe.py, cmd_regrant.py and
cmd_reset_disable.py. cmd_import_legacy.py (`import-legacy`) is planned in TR-22 and not built; the tests use that name
in a throwaway package, only to exercise the file-name rule.

Exit status: the command's own (0-255; anything else counts as 1, since the OS keeps only the low byte and 256 would
read as success), or 2 for an unknown or malformed command (nothing ran), 1 for an unexpected error, 130 when
interrupted (errors.ExitCode); an exception group exits with its most severe member's status. A failure prints its
class and SQLSTATE, plus the value-free message of an ObserveFailure (errors.describe_error). What was typed is never
echoed, since an argument can be a path or a DSN: commands parse their arguments with args.command_parser, whose usage
errors print the usage and not the arguments.
"""

import importlib
import pkgutil
import sys
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TextIO

from ggwork_pick.observe.admin.args import PROG
from ggwork_pick.observe.errors import ExitCode, Refused, describe_error, exit_code_for

PACKAGE = "ggwork_pick.observe.admin"
PREFIX = "cmd_"
HELP_FLAGS = ("-h", "--help")
MAX_STATUS = 255  # POSIX keeps the low byte of an exit status


def command_name(module_name: str) -> str:
    """cmd_reset_disable -> reset-disable."""
    return module_name.removeprefix(PREFIX).replace("_", "-")


def discover(package: str = PACKAGE) -> Mapping[str, str]:
    """Command name -> module name, ordered by command name, from the package's cmd_*.py files; imports none of them."""
    path = importlib.import_module(package).__path__
    found = sorted(
        (command_name(info.name), f"{package}.{info.name}") for info in pkgutil.iter_modules(path) if info.name.startswith(PREFIX) and not info.ispkg
    )
    return MappingProxyType(dict(found))


def usage(commands: Sequence[str]) -> str:
    listed = "、".join(commands) if commands else "（暂无）"
    return f"用法：{PROG} <命令> [参数…]；可用命令：{listed}"


def _load(module_name: str, name: str) -> Callable[[list[str]], object]:
    module = importlib.import_module(module_name)
    entry = getattr(module, "main", None)
    if getattr(module, "NAME", None) != name or not callable(entry):
        raise Refused(f"命令模块 {module_name} 须导出 NAME = {name!r} 与可调用的 main(argv)")
    return entry


def _status(code: object) -> int:
    """A command's return value, or a SystemExit's code, as an exit status: None is 0, an int within 0-255 is itself,
    anything else is 1 (Python exits 1 for a non-int code; the OS would turn 256 into 0 and -1 into 255)."""
    if code is None:
        return int(ExitCode.OK)
    if isinstance(code, int) and 0 <= code <= MAX_STATUS:
        return int(code)
    return int(ExitCode.FAILED)


def main(argv: Sequence[str] | None = None, *, package: str = PACKAGE, out: TextIO | None = None, err: TextIO | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    out, err = out or sys.stdout, err or sys.stderr
    commands = discover(package)
    if args and args[0] in HELP_FLAGS:
        print(usage(list(commands)), file=out)
        return int(ExitCode.OK)
    if not args or args[0] not in commands:
        print(f"{PROG}: 未知或缺少命令。{usage(list(commands))}", file=err)
        return int(ExitCode.REFUSED)
    name, rest = args[0], args[1:]
    try:
        return _status(_load(commands[name], name)(rest))
    except SystemExit as exc:  # a command's own argparse --help or usage error
        return _status(exc.code)
    except (Exception, KeyboardInterrupt, BaseExceptionGroup) as exc:  # a group can hold a KeyboardInterrupt
        print(f"{PROG} {name}: {describe_error(exc)}", file=err)
        return int(exit_code_for(exc))


if __name__ == "__main__":
    raise SystemExit(main())
