"""`python -m ggwork_pick.observe.admin <command> [args...]`: find the command module and run it (plan D35).

Commands are found by file name (cmd_import_legacy.py is `import-legacy`), and only the module being run is imported:
one command's heavy or broken imports never reach another, and nothing here pulls in the gateway. The module must
declare the NAME its file name implies and a callable main(argv) returning the exit status (None counts as 0).

Exit status: the command's own, or 2 for an unknown or malformed command (nothing ran), 1 for an unexpected error,
130 when interrupted (errors.ExitCode). A failure prints its class and SQLSTATE only, and what was typed is never
echoed: an argument can be a path or a DSN.
"""

import importlib
import pkgutil
import sys
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import TextIO

from ggwork_pick.observe.errors import ExitCode, Refused, describe_error, exit_code_for

PACKAGE = "ggwork_pick.observe.admin"
PROG = "python -m ggwork_pick.observe.admin"
PREFIX = "cmd_"
HELP_FLAGS = ("-h", "--help")


def command_name(module_name: str) -> str:
    """cmd_import_legacy -> import-legacy."""
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
    """A command's return value, or a SystemExit's code, as an exit status (Python exits 1 for a non-int code)."""
    if code is None:
        return int(ExitCode.OK)
    return code if isinstance(code, int) else int(ExitCode.FAILED)


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
    except (Exception, KeyboardInterrupt) as exc:
        print(f"{PROG} {name}: {describe_error(exc)}", file=err)
        return int(exit_code_for(exc))


if __name__ == "__main__":
    raise SystemExit(main())
