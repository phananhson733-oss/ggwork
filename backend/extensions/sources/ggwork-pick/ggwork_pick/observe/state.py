"""The persisted Trends runtime state, and the file store that keeps it for local runs (plan TR-04; design 4.3, 4.4).

Design 4.3: when the state cannot be read, the day does not run. Otherwise a crash and restart would come back with a
new cookie jar at full speed, the amplification that got the scripted package rate limited. So every failure to read
the state is StateUnavailable (exit 3), a missing file included: only an explicit --init-state creates the file, and
never over one that exists.

RuntimeState is immutable, and a restart keeps the pause (design 4.3). The pacing, breaker and budget sections belong
to the TR-03 state machines: each serialises its own state to a JSON object and reads it back, and this module only
freezes, stores and returns it (None: never saved, the machine starts fresh). A section refuses what JSON or PostgreSQL
text cannot hold, so the file store and TR-13's database store refuse the same states. A runner reads a section back
through section(name, decode) with the machine's own decoder: whatever that decoder refuses is StateUnavailable
(exit 3), like any other state that cannot be read.

One pause, one source: the breaker section decides (TR-03's BreakerDay.paused_until, read through breaker.ready_at).
paused_until here is its plain copy, like the runtime row's paused_until column in TR-13: the runner sets it from the
breaker on every save, so is_paused() and a status command can refuse before any section is decoded; where the two
disagree, the breaker wins.

StateStore is what a runner reads and writes through: FileStateStore here (the local stage 0 runs: one encrypted JSON
file), DbStateStore on the ggwp_obs_runtime row in TR-13 (the canary and production, D17).
"""

import fcntl
import json
import logging
import math
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from ggwork_pick.observe.crypto import PrivateFileRefused, StateCipher, load_cipher, read_private_file
from ggwork_pick.observe.errors import Refused, StateUnavailable
from ggwork_pick.observe.trends.cookies import CookieJar

logger = logging.getLogger(__name__)

STATE_FORMAT = 1
MAX_STATE_BYTES = 1024 * 1024
STATE_DIR_NAME = ".ggwork-obs"
STATE_FILE_NAME = "trends-state.json"
DIR_MODE = 0o700
WIDER_THAN_700 = 0o077
TEMP_SUFFIX = ".tmp"
LOCK_SUFFIX = ".lock"
SECTIONS = ("pacing", "breaker", "budget")
_FIELDS = frozenset({"format", "paused_until", *SECTIONS, "cookie_jar"})
_NUL = chr(0)
_EXISTS = "状态文件已存在，不能重新初始化；去掉 --init-state 再运行"
_BUSY = "另一个进程正在用同一个状态文件（锁被占用）：本进程停止，一个请求都还没发"
# What a section's decoder raises on content it refuses (TR-03's raise ValueError; a lookup or a wrong type in between).
_DECODE_FAILURES = (ValueError, TypeError, LookupError, AttributeError)


def default_state_path() -> Path:
    """~/.ggwork-obs/trends-state.json; the directory is created 700 by --init-state."""
    return Path.home() / STATE_DIR_NAME / STATE_FILE_NAME


def _check_storable(text: str) -> None:
    if _NUL in text or any(0xD800 <= ord(char) <= 0xDFFF for char in text):
        raise ValueError("文本含 NUL 或孤立代理项：PostgreSQL 存不下")


def freeze_json(value: object) -> object:
    """A read-only deep copy of a JSON value: objects become mappingproxies, arrays tuples."""
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 数值须有限")
        return value
    if isinstance(value, str):
        _check_storable(value)
        return value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise ValueError("JSON 对象的键须是字符串")
        for key in value:
            _check_storable(key)
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(freeze_json(item) for item in value)
    raise ValueError(f"{type(value).__name__} 不是 JSON 值")


def thaw_json(value: object) -> object:
    """freeze_json undone: plain dicts and lists, ready for json.dumps."""
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value


def _aware_utc(moment: object, what: str) -> datetime:
    if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{what} 须是带时区的时刻")
    return moment.astimezone(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds")  # repository.stamp()'s form: always six fractional digits


def _parse_stamp(text: object, what: str) -> datetime:
    if not isinstance(text, str):
        raise ValueError(f"{what} 须是时间文本")
    return _aware_utc(datetime.fromisoformat(text), what)


@dataclass(frozen=True)
class RuntimeState:
    paused_until: datetime | None = None
    pacing: Mapping[str, object] | None = None
    breaker: Mapping[str, object] | None = None
    budget: Mapping[str, object] | None = None
    cookie_jar: CookieJar | None = None

    def __post_init__(self) -> None:
        if self.paused_until is not None:
            object.__setattr__(self, "paused_until", _aware_utc(self.paused_until, "paused_until"))
        for name in SECTIONS:
            section = getattr(self, name)
            if section is not None and not isinstance(section, Mapping):
                raise ValueError(f"{name} 须是 JSON 对象")
            if section is not None:
                object.__setattr__(self, name, freeze_json(section))
        if self.cookie_jar is not None and not isinstance(self.cookie_jar, CookieJar):
            raise ValueError("cookie_jar 须是 CookieJar")

    def is_paused(self, now: datetime) -> bool:
        """Whether nothing may be sent at `now`: a restart inside a breaker pause stays inside it (design 4.3)."""
        return self.paused_until is not None and _aware_utc(now, "now") < self.paused_until

    def section[T](self, name: str, decode: Callable[[dict], T]) -> T | None:
        """Section `name` read back by its machine's decoder (TR-03's from_dict), which gets plain JSON data; None when
        it was never saved. Content the decoder refuses is StateUnavailable: the day does not run on a guessed state."""
        if name not in SECTIONS:
            raise ValueError(f"没有 {name} 这个分区；分区是 {', '.join(SECTIONS)}")
        stored = getattr(self, name)
        if stored is None:
            return None
        try:
            return decode(thaw_json(stored))
        except _DECODE_FAILURES:
            raise StateUnavailable(f"状态里的 {name} 分区读不回来：内容不合该状态机的格式（已损坏，或由另一个版本写成）") from None

    def to_document(self) -> dict:
        """The state as JSON data. It carries the cookie values, so it is only ever stored encrypted."""
        return {
            "format": STATE_FORMAT,
            "paused_until": _stamp(self.paused_until) if self.paused_until is not None else None,
            **{name: thaw_json(getattr(self, name)) for name in SECTIONS},
            "cookie_jar": self.cookie_jar.to_document() if self.cookie_jar is not None else None,
        }

    @classmethod
    def from_document(cls, document: object) -> "RuntimeState":
        """The state a document describes; ValueError for any other shape or format version."""
        if not isinstance(document, Mapping) or set(document) != _FIELDS:
            raise ValueError("状态的字段不对")
        if type(document["format"]) is not int or document["format"] != STATE_FORMAT:
            raise ValueError("状态的格式版本不认识")
        paused = document["paused_until"]
        jar = document["cookie_jar"]
        return cls(
            paused_until=_parse_stamp(paused, "paused_until") if paused is not None else None,
            **{name: document[name] for name in SECTIONS},
            cookie_jar=CookieJar.from_document(jar) if jar is not None else None,
        )


class StateStore(Protocol):
    async def load(self) -> RuntimeState:
        """The persisted state; StateUnavailable when it cannot be read. Never a fresh state in its place."""
        ...

    async def save(self, state: RuntimeState) -> None:
        """Persist `state`; StateUnavailable when that fails or another writer got there first."""
        ...


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _sync_directory(directory: Path) -> None:
    """Make a rename durable. A filesystem that cannot sync a directory still renamed atomically."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError as exc:
        logger.debug("[pick-obs] the state directory cannot be opened to sync it: %s", type(exc).__name__)
        return
    try:
        os.fsync(fd)
    except OSError as exc:
        logger.debug("[pick-obs] the state directory cannot be synced on this filesystem: %s", type(exc).__name__)
    finally:
        os.close(fd)


def _check_directory(directory: Path) -> None:
    """The state's directory is this user's and nobody else's: otherwise others could swap the file under us."""
    try:
        info = os.lstat(directory)
    except OSError:
        raise StateUnavailable("状态目录不存在或读不了") from None
    if not stat.S_ISDIR(info.st_mode):
        raise StateUnavailable("状态目录不是目录（或是符号链接）")
    if info.st_uid != os.geteuid():
        raise StateUnavailable("状态目录不属于当前用户")
    if info.st_mode & WIDER_THAN_700:
        raise StateUnavailable("状态目录权限宽于 700（别人能替换状态文件；chmod 700 后重试）")


def _lock(path: Path) -> int:
    """An exclusive flock on the lock file beside the state, taken without waiting; StateUnavailable when another
    process holds it. The descriptor is the lock: closing it, or the process ending however it ends, releases it."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    except OSError as exc:
        raise StateUnavailable("状态锁文件打不开（是符号链接，或没有权限）") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise StateUnavailable("状态锁文件不是当前用户的普通文件")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise StateUnavailable(_BUSY) from None
    except OSError as exc:
        os.close(fd)
        raise StateUnavailable("状态锁取不到（锁文件读不了，或这个文件系统不支持 flock）") from exc
    except BaseException:
        os.close(fd)
        raise
    return fd


def _make_private_directory(directory: Path) -> None:
    if directory.exists():
        return
    try:
        directory.mkdir(mode=DIR_MODE, parents=True)
        os.chmod(directory, DIR_MODE)  # mkdir's mode passes through the umask
    except OSError as exc:
        raise StateUnavailable("状态目录建不了（没有权限，或上级路径不是目录）") from exc


class FileStateStore:
    """One encrypted JSON file (mode 600, in a 700 directory), replaced atomically on every save.

    One process at a time. load() and initialize() take an exclusive flock on `.<name>.lock` beside the file and hold
    it until close() or the process ends; a second run on the same file is refused at load, before it has sent
    anything (StateUnavailable, exit 3). A load that fails lets the lock go. Second guard, for a file replaced by
    something that takes no lock: save() checks the file still holds what this store last read or wrote. A state that
    would seal to more than load() reads (MAX_STATE_BYTES) is refused and the old file stays. The files are a few
    kilobytes on a local disk, so the I/O runs inline."""

    def __init__(self, path: str | os.PathLike, cipher: StateCipher):
        self._path = Path(path)
        self._cipher = cipher
        self._seen: bytes | None = None  # the ciphertext this store last read or wrote
        self._lock_fd: int | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def lock_path(self) -> Path:
        return self._path.with_name(f".{self._path.name}{LOCK_SUFFIX}")

    def __repr__(self) -> str:
        return f"FileStateStore(path={str(self._path)!r}, cipher={self._cipher!r})"

    def close(self) -> None:
        """Let the file go: another run may load it now, and this store saves nothing more until it loads again."""
        fd, self._lock_fd, self._seen = self._lock_fd, None, None
        if fd is not None:
            os.close(fd)

    def __del__(self) -> None:
        if getattr(self, "_lock_fd", None) is not None:
            self.close()

    async def load(self) -> RuntimeState:
        try:
            self._require_file()
            self._acquire()
            token = self._read()
            state = self._decode(token)
        except BaseException:
            self.close()
            raise
        self._seen = token
        return state

    async def save(self, state: RuntimeState) -> None:
        if self._seen is None:
            raise RuntimeError("FileStateStore.save() before load() or initialize(): it cannot tell what it would overwrite")
        if self._read() != self._seen:
            raise StateUnavailable("状态文件在本进程读取之后被改过：有别的程序替换了它，本进程停止")
        token = self._seal(state)
        self._write(token, replace_existing=True)
        self._seen = token

    async def initialize(self, state: RuntimeState | None = None) -> RuntimeState:
        """Create the state file (and its 700 directory) holding `state`, a fresh state by default; Refused when the
        file exists already: --init-state never starts afresh over a state that is there."""
        fresh = RuntimeState() if state is None else state
        _make_private_directory(self._path.parent)
        _check_directory(self._path.parent)
        if os.path.lexists(self._path):
            raise Refused(_EXISTS)
        try:
            self._acquire()
            token = self._seal(fresh)
            self._write(token, replace_existing=False)
        except BaseException:
            self.close()
            raise
        self._seen = token
        logger.info("[pick-obs] trends state file created")
        return fresh

    def _acquire(self) -> None:
        if self._lock_fd is None:
            self._lock_fd = _lock(self.lock_path)

    def _require_file(self) -> None:
        if not os.path.lexists(self._path):
            raise StateUnavailable("状态文件不存在；首次运行须显式带 --init-state 创建（从不自动新建）")
        _check_directory(self._path.parent)

    def _read(self) -> bytes:
        self._require_file()
        try:
            return read_private_file(self._path, MAX_STATE_BYTES)
        except PrivateFileRefused as refused:
            raise StateUnavailable(f"状态文件{refused}") from None

    def _decode(self, token: bytes) -> RuntimeState:
        opened = self._cipher.open(token)
        try:
            state = RuntimeState.from_document(json.loads(opened.data.decode("utf-8")))
        except (TypeError, ValueError):  # JSON, UTF-8 and date parsing errors are ValueErrors
            raise StateUnavailable("状态文件解开后内容不合格式：已损坏，或由更新的版本写成") from None
        if opened.key_index:
            logger.warning(
                "[pick-obs] the trends state was sealed with key #%d of %d; the next save re-seals it with key #1",
                opened.key_index + 1,
                self._cipher.key_count,
            )
        return state

    def _seal(self, state: RuntimeState) -> bytes:
        """The sealed state, refused (nothing written) when load() could not read it back."""
        plain = json.dumps(state.to_document(), sort_keys=True, allow_nan=False, separators=(",", ":"))
        token = self._cipher.seal(plain.encode("ascii"))
        if len(token) > MAX_STATE_BYTES:
            raise StateUnavailable(f"状态加密后 {len(token)} 字节，超过 {MAX_STATE_BYTES} 字节的上限：没有写入，状态文件保持原样")
        return token

    def _write(self, token: bytes, *, replace_existing: bool) -> None:
        """Write a temp file beside the state, then rename it over the state (or link it in, when creating: that
        never clobbers). Interrupted anywhere, the old file stays whole."""
        directory = self._path.parent
        temp = None
        try:
            fd, temp = tempfile.mkstemp(prefix=f".{self._path.name}.", suffix=TEMP_SUFFIX, dir=directory)
            try:
                _write_all(fd, token)
                os.fsync(fd)
            finally:
                os.close(fd)
            (os.replace if replace_existing else os.link)(temp, self._path)
        except FileExistsError:
            raise Refused(_EXISTS) from None
        except OSError as exc:
            raise StateUnavailable("状态文件写不进去（磁盘满、没有权限，或目录已变）") from exc
        finally:
            if temp is not None:
                _discard(temp)
        _sync_directory(directory)
        self._remove_leftovers()

    def _remove_leftovers(self) -> None:
        """Temp files a killed process left behind; this process holds the lock, so none is in use."""
        prefix = f".{self._path.name}."
        try:
            with os.scandir(self._path.parent) as entries:
                leftovers = tuple(entry.path for entry in entries if entry.name.startswith(prefix) and entry.name.endswith(TEMP_SUFFIX))
            for leftover in leftovers:
                _discard(leftover)
        except OSError as exc:  # the save itself is done; a leftover only takes a few bytes
            logger.warning("[pick-obs] leftover state temp files not removed: %s", type(exc).__name__)


async def open_state(store: FileStateStore, *, init_state: bool) -> RuntimeState:
    """The state a local run starts from: --init-state creates the file (and refuses when one exists); without it a
    missing file is StateUnavailable, and the day does not run."""
    return await store.initialize() if init_state else await store.load()


def file_state_store(path: str | os.PathLike | None = None, *, environ: Mapping[str, str] | None = None) -> FileStateStore:
    """The store at `path` (default ~/.ggwork-obs/trends-state.json) with the key from the environment. Without a key
    this is Refused (exit 2) before the state is read or created, so a run without a key never sends anything."""
    cipher = load_cipher(environ)
    return FileStateStore(default_state_path() if path is None else Path(path).expanduser(), cipher)
