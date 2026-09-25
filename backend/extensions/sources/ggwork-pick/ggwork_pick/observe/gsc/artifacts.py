"""Where the hand-run GSC commands write, and how (plan TR-07; TR-05 keeps its stage 0 files in the same folder).

The probe report, the raw answers and the URL list carry the site's own data, so they live outside the repository, in
~/.gstack/projects/ggwork-deerflow/artifacts by default: a folder created here is 700 and every file 600. A run never
overwrites an earlier one: when any of its files already exists, the whole run takes the next free suffix (-2, -3, ...),
and each file is created with O_EXCL so that a race cannot overwrite either.
"""

import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

DEFAULT_DIR = "~/.gstack/projects/ggwork-deerflow/artifacts"
MAX_SUFFIX = 99


def run_paths(folder: Path, day: date, names: Sequence[str]) -> tuple[Path, ...]:
    """The paths of one run's files: each name is a template with {stamp}, which becomes the day, or the day and the
    first suffix none of the run's files is using yet."""
    stamp = day.strftime("%Y-%m-%d")
    for number in range(1, MAX_SUFFIX + 1):
        suffix = stamp if number == 1 else f"{stamp}-{number}"
        paths = tuple(folder / name.format(stamp=suffix) for name in names)
        if not any(path.exists() for path in paths):
            return paths
    raise FileExistsError(f"{folder} 里同一天已有 {MAX_SUFFIX} 次运行的文件")


def ensure_folder(folder: Path) -> Path:
    """The folder, created 700 (with its parents) when missing; an existing one is used as it is."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


def write_private(path: Path, data: bytes) -> Path:
    """Create the file 600 and write it; an existing file is never replaced."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)
    return path
