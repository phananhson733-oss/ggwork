"""Private copies of a user's lark-cli credential tree (docs/pick-workbench/lark-personal-auth.md section 3).

Only plain files and directories are copied, never through a link. A copy goes back into place whole: it is
written beside the tree first and swapped in by rename, so a failure leaves the previous tree as it was.
"""

import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path
from uuid import uuid4

MAX_CREDENTIAL_BYTES = 8 * 1024 * 1024


class UnsafeTree(ValueError):
    """A credential tree holds a link, a device or more data than a credential tree should."""


def snapshot(root: Path) -> dict[str, bytes]:
    """A digest per plain file and a marker per directory under ``root``; anything else is refused."""
    digests: dict[str, bytes] = {}
    total = 0
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            path = Path(current) / name
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                digests[relative + "/"] = b""
            elif stat.S_ISREG(info.st_mode):
                total += info.st_size
                if total > MAX_CREDENTIAL_BYTES:
                    raise UnsafeTree(f"more than {MAX_CREDENTIAL_BYTES} bytes")
                digests[relative] = hashlib.sha256(path.read_bytes()).digest()
            else:
                raise UnsafeTree(f"{relative} is not a plain file or directory")
    return digests


def copy_tree(source: Path, target: Path) -> None:
    """Copy plain files and directories (the caller has snapshotted ``source``), never following a link."""
    for current, dirs, files in os.walk(source, followlinks=False):
        relative = Path(current).relative_to(source)
        for name in dirs:
            (target / relative / name).mkdir(mode=0o700, exist_ok=True)
        for name in files:
            source_fd = os.open(Path(current) / name, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                target_fd = os.open(target / relative / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            except OSError:
                os.close(source_fd)
                raise
            with os.fdopen(source_fd, "rb") as src, os.fdopen(target_fd, "wb") as out:
                shutil.copyfileobj(src, out)


def install(directory: Path, source: Path) -> None:
    """Replace ``directory`` with a copy of ``source``; on failure ``directory`` is left as it was."""
    staging = Path(tempfile.mkdtemp(prefix=f".{directory.name}-new-", dir=directory.parent))
    retired = directory.with_name(f".{directory.name}-old-{uuid4().hex}")
    try:
        copy_tree(source, staging)
        directory.rename(retired)
        try:
            staging.rename(directory)
        except OSError:
            retired.rename(directory)
            raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    shutil.rmtree(retired, ignore_errors=True)
