"""What the two hand-run GSC commands share: bounded option types and the output folder option (plan TR-07, D35).

The commands themselves are admin/cmd_gsc_probe.py and admin/cmd_gsc_export_urls.py. A type error goes through
admin.args.CommandParser, which never echoes the value typed.
"""

import argparse
from collections.abc import Callable
from pathlib import Path

from ggwork_pick.observe.gsc.artifacts import DEFAULT_DIR

MAX_PAUSE_SECONDS = 60.0


def bounded_int(low: int, high: int) -> Callable[[str], int]:
    def parse(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"要一个 {low} 到 {high} 之间的整数") from None
        if not low <= value <= high:
            raise argparse.ArgumentTypeError(f"要一个 {low} 到 {high} 之间的整数")
        return value

    return parse


def pause_seconds(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"要 0 到 {MAX_PAUSE_SECONDS:g} 秒") from None
    if not 0 <= value <= MAX_PAUSE_SECONDS:  # also refuses NaN
        raise argparse.ArgumentTypeError(f"要 0 到 {MAX_PAUSE_SECONDS:g} 秒")
    return value


def add_common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--out-dir", default=DEFAULT_DIR, help="输出目录，不进仓库（默认 %(default)s；不存在就新建为 700）")
    parser.add_argument("--pause", type=pause_seconds, default=1.0, help="两次请求之间停几秒，每站配额与 RealShort 共用（默认 %(default)s）")
    return parser


def out_folder(text: str) -> Path:
    return Path(text).expanduser()
