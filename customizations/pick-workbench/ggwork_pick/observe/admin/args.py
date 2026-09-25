"""The argument parser every observe command uses (plan D35).

argparse's own error() repeats what was typed ("invalid int value: '...'", "unrecognized arguments: ..."), and an
argument can be a path or a DSN. CommandParser prints the usage, which lists only what the command declares, and a
fixed line instead. Defaults show up in --help, so a default never comes from the environment or a file.
"""

import argparse
import sys
from typing import NoReturn

from ggwork_pick.observe.errors import ExitCode

PROG = "python -m ggwork_pick.observe.admin"


class CommandParser(argparse.ArgumentParser):
    """An ArgumentParser whose usage errors never echo the arguments."""

    def error(self, message: str) -> NoReturn:
        del message  # it quotes the offending value
        self.print_usage(sys.stderr)
        self.exit(int(ExitCode.REFUSED), f"{self.prog}: 参数有误（为免泄露，不回显输入的值）；用 --help 查看用法\n")


def command_parser(name: str, description: str | None = None) -> CommandParser:
    """The parser for command `name`, its usage reading `python -m ggwork_pick.observe.admin <name> ...`."""
    return CommandParser(prog=f"{PROG} {name}", description=description)
