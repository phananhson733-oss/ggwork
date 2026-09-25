"""Encryption at rest for the Trends state and cookie jar (plan TR-04; design 4.3, 4.4; D18).

The key comes only from the environment: PICK_OBS_STATE_KEY holds one or more Fernet keys separated by commas, or
PICK_OBS_STATE_KEY_FILE names a file holding them (owned by this user, mode 600 or narrower). Setting both, or neither,
is refused (exit 2) before any state is read. The first key seals and every key opens (MultiFernet), so a rotation
prepends the new key, lets one run save (which re-seals under it), then drops the old key.

No key, plaintext or token ever goes into a message, a repr or a log line: errors name the variable and the key's
position, and are raised without the library error they replace.
"""

import errno
import os
import re
import stat
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from ggwork_pick.observe.errors import Refused, StateUnavailable

KEY_VARIABLE = "PICK_OBS_STATE_KEY"
KEY_FILE_VARIABLE = "PICK_OBS_STATE_KEY_FILE"
MAX_KEY_FILE_BYTES = 64 * 1024
WIDER_THAN_600 = 0o177  # any of these bits set: executable, or reachable by group or others
_KEY_SEPARATORS = re.compile(r"[\s,]+")
_OPEN_REASONS = {errno.ENOENT: "不存在", errno.ELOOP: "是符号链接（须指向文件本身）", errno.EACCES: "没有读权限"}


class PrivateFileRefused(Exception):
    """A private file cannot be trusted or read; the message is a reason, never the file's content."""


@dataclass(frozen=True)
class Opened:
    data: bytes = field(repr=False)
    key_index: int  # 0 is the sealing key; anything else means the content predates a rotation


class StateCipher:
    """Seals with the first key, opens with any of them. Its repr counts the keys and shows none."""

    __slots__ = ("_fernets", "_multi")

    def __init__(self, keys: Sequence[str | bytes]):
        if not keys:
            raise Refused("状态密钥为空")
        fernets = []
        for position, key in enumerate(keys, 1):
            try:
                fernets.append(Fernet(key))
            except (TypeError, ValueError):  # binascii.Error is a ValueError
                raise Refused(f"状态密钥第 {position} 个不是合法的 Fernet 密钥（32 字节的 urlsafe base64）") from None
        self._fernets = tuple(fernets)
        self._multi = MultiFernet(fernets)

    @property
    def key_count(self) -> int:
        return len(self._fernets)

    def seal(self, data: bytes) -> bytes:
        return self._multi.encrypt(data)

    def open(self, token: bytes) -> Opened:
        """The plaintext and the position of the key that opened it; StateUnavailable when no key does."""
        for index, fernet in enumerate(self._fernets):
            try:
                return Opened(fernet.decrypt(token), index)
            except InvalidToken:
                continue
        raise StateUnavailable("密文用已配置的任何一个状态密钥都解不开：密钥不对，或内容已损坏")

    def __repr__(self) -> str:
        return f"StateCipher(keys={len(self._fernets)})"


def parse_keys(text: str) -> tuple[str, ...]:
    """Keys separated by commas, spaces or newlines; blanks ignored."""
    return tuple(part for part in _KEY_SEPARATORS.split(text) if part)


def load_cipher(environ: Mapping[str, str] | None = None) -> StateCipher:
    """The cipher from PICK_OBS_STATE_KEY or PICK_OBS_STATE_KEY_FILE (exactly one of them); Refused otherwise."""
    env = os.environ if environ is None else environ
    inline = env.get(KEY_VARIABLE, "").strip()
    file_name = env.get(KEY_FILE_VARIABLE, "").strip()
    if inline and file_name:
        raise Refused(f"{KEY_VARIABLE} 与 {KEY_FILE_VARIABLE} 只能设一个")
    if not inline and not file_name:
        raise Refused(f"缺少状态密钥：设 {KEY_VARIABLE}，或设 {KEY_FILE_VARIABLE} 指向权限为 600 的密钥文件")
    text = inline or _read_key_file(Path(file_name))
    keys = parse_keys(text)
    if not keys:
        raise Refused(f"{KEY_FILE_VARIABLE} 指向的文件里没有密钥")
    return StateCipher(keys)


def _read_key_file(path: Path) -> str:
    try:
        data = read_private_file(path, MAX_KEY_FILE_BYTES)
    except PrivateFileRefused as refused:
        raise Refused(f"{KEY_FILE_VARIABLE} 指向的文件{refused}") from None
    try:
        return data.decode("ascii")
    except UnicodeDecodeError:
        raise Refused(f"{KEY_FILE_VARIABLE} 指向的文件含有非 ASCII 字符") from None


def read_private_file(path: Path, max_bytes: int) -> bytes:
    """The bytes of a regular file this user owns, mode 600 or narrower, not a symlink, at most max_bytes long.

    The checks run on the open descriptor, so the file checked is the file read. O_NONBLOCK: a FIFO in the file's place
    is refused at once as not a regular file instead of hanging the run until something writes to it (it changes
    nothing for a regular file). Meant to be the observe package's one reader of private files, so that "600" means the
    same everywhere: the state key and the state file read through it, and the GSC key file is to as well."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise PrivateFileRefused(_OPEN_REASONS.get(exc.errno, "打不开")) from None
    try:
        info = os.fstat(fd)
        _check_private(info)
        if info.st_size > max_bytes:
            raise PrivateFileRefused(f"超过 {max_bytes} 字节的上限")
        return _read_all(fd, max_bytes)
    finally:
        os.close(fd)


def _check_private(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise PrivateFileRefused("不是普通文件")
    if info.st_uid != os.geteuid():
        raise PrivateFileRefused("不属于当前用户")
    if info.st_mode & WIDER_THAN_600:
        raise PrivateFileRefused("权限宽于 600（chmod 600 后重试）")


def _read_all(fd: int, max_bytes: int) -> bytes:
    chunks, size = [], 0
    while chunk := os.read(fd, 65536):
        size += len(chunk)
        if size > max_bytes:
            raise PrivateFileRefused(f"超过 {max_bytes} 字节的上限")
        chunks.append(chunk)
    return b"".join(chunks)
