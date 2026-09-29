"""What the read-only lark_cli tool may run (docs/pick-workbench/lark-personal-auth.md section 3).

The model supplies an argv list. Only the chosen Feishu domains and lark-cli's own guides are open, never the
commands that manage configuration, authorization, profiles, updates or background daemons; the flags that
confirm high-risk writes, switch profile or print a request are refused; and no value may point at a local file
(`@file`, `-` for stdin, absolute or home paths, `..`), since lark-cli would read it from the Gateway's disk.
Whether a command only reads is lark-cli's own answer: the `Risk:` line of its `--help`.
"""

import re
from dataclasses import dataclass
from itertools import takewhile

# Documents and messages (decided 2026-09-29). Calendar, mail, approval and the rest stay closed.
DOMAINS = frozenset({"docs", "wiki", "drive", "sheets", "base", "markdown", "slides", "mindnotes", "whiteboard", "im"})
GUIDES = frozenset({"schema", "skills", "help"})
SKILL_ACTIONS = frozenset({"list", "read"})
DENIED_FLAGS = frozenset({"--yes", "--profile", "--dry-run"})
JQ_FLAGS = frozenset({"--jq", "-q"})
HELP_FLAGS = frozenset({"--help", "-h"})
MAX_ARGS = 40
MAX_ARG_CHARS = 2000

_PARENT = re.compile(r"(^|[/\\])\.\.([/\\]|$)")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FLAG_NAME = re.compile(r"--?[A-Za-z0-9][A-Za-z0-9-]*")
_NUMBER = re.compile(r"-\d+(\.\d+)?")
_USAGE = re.compile(r"^Usage:\n\s+lark-cli ([^\n\[]+)", re.MULTILINE)
_RISK = re.compile(r"^Risk:\s*(read|write|high-risk-write)\s*$", re.MULTILINE)


class LarkRefused(ValueError):
    """A command the tool will not run; the message goes back to the model."""


@dataclass(frozen=True)
class Command:
    args: tuple[str, ...]
    # The leading positionals: the command group and, for most commands, the subcommand.
    path: tuple[str, ...]
    # lark-cli's own help, schema and skill text: no credentials and no risk lookup.
    guide: bool


def check_args(raw: object) -> Command:
    if not isinstance(raw, list | tuple):
        raise LarkRefused('argv 必须是参数列表，例如 ["docs", "+fetch", "--doc", "<链接>"]')
    args = list(raw[1:] if raw and raw[0] == "lark-cli" else raw)
    if not 0 < len(args) <= MAX_ARGS:
        raise LarkRefused(f"参数个数必须在 1–{MAX_ARGS} 之间")
    for arg in args:
        _check_text(arg)
    path = tuple(takewhile(lambda arg: not arg.startswith("-"), args))
    if not path:
        raise LarkRefused("第一个参数必须是命令组，例如 docs、wiki、im 或 skills")
    group = path[0]
    if group not in DOMAINS | GUIDES:
        raise LarkRefused(f"命令组 {group!r} 不开放；可用：{', '.join(sorted(DOMAINS | GUIDES))}")
    if group == "skills" and (len(path) < 2 or path[1] not in SKILL_ACTIONS):
        raise LarkRefused('skills 只能 list 或 read，例如 ["skills", "read", "lark-doc"]')
    _check_flags_and_values(args)
    return Command(args=tuple(args), path=path, guide=group in GUIDES or any(arg in HELP_FLAGS for arg in args))


def _check_text(arg: object) -> None:
    if not isinstance(arg, str) or not arg:
        raise LarkRefused("每个参数都必须是非空字符串")
    if len(arg) > MAX_ARG_CHARS:
        raise LarkRefused(f"单个参数不能超过 {MAX_ARG_CHARS} 个字符")
    if _CONTROL.search(arg):
        raise LarkRefused("参数里不能有换行或控制字符")


def _check_flags_and_values(args: list[str]) -> None:
    jq_value = False
    for arg in args:
        if jq_value:
            jq_value = False  # a jq expression reads only lark-cli's JSON output
            continue
        if arg.startswith("-") and arg != "-":
            name, equals, value = arg.partition("=")
            # lark-cli also takes a dash-led token as the previous flag's value (`--doc -/x`), so a dash-led
            # token must be a flag name or a number, never a value that happens to start with a dash.
            if not (_FLAG_NAME.fullmatch(name) or (not equals and _NUMBER.fullmatch(arg))):
                raise LarkRefused(f"参数 {arg[:40]!r} 既不是参数名也不是数字")
            if name in DENIED_FLAGS:
                raise LarkRefused(f"不支持参数 {name}：本工具只读，不确认高风险写入、不切换配置、不打印请求")
            if name in JQ_FLAGS:
                jq_value = not equals
            elif equals:
                _check_value(value)
            continue
        _check_value(arg)


def _check_value(value: str) -> None:
    if value == "-" or value[:1] in ("@", "/", "~", "\\") or value.lower().startswith("file:") or _PARENT.search(value):
        raise LarkRefused(f"不支持本地文件（{value[:40]!r}）：只接受飞书链接、token 和普通文本")


def risk_from_help(path: tuple[str, ...], text: str) -> str:
    """The risk lark-cli declares for ``path``, from ``lark-cli <path> --help``.

    The help must describe a command under ``path`` (an unknown subcommand prints its group's help instead) and
    carry a Risk label; anything else is refused rather than guessed.
    """
    usage = _USAGE.search(text)
    resolved = tuple(usage.group(1).split()) if usage else ()
    risk = _RISK.search(text)
    if len(resolved) < 2 or path[: len(resolved)] != resolved or risk is None:
        raise LarkRefused(f'无法确认 `lark-cli {" ".join(path)}` 是只读命令；先用 ["{path[0]}", "--help"] 查看可用命令')
    return risk.group(1)
