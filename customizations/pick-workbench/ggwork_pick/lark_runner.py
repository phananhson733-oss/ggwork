"""Runs one lark-cli command without handing it the Gateway (docs/pick-workbench/lark-personal-auth.md section 3).

Each run gets a fresh scratch directory: an empty working directory, its own HOME and TMPDIR, and a private copy of
the user's credential tree taken under that user's credential lock. lark-cli sees a listed environment only and, in
the image, runs as the unprivileged user named by DEER_FLOW_LARK_CLI_RUN_AS, so it cannot read the Gateway's /proc
environment, DEER_FLOW_HOME or other users' credential trees. One lark-cli process runs at a time, across threads
and worker processes, and whatever the lark user left running is killed when the run ends, so no other user's copy
exists while a run is in flight. What lark-cli changed in its copy (a refreshed token, a cache) is copied back before
the lock is released, unless the copy then holds anything but plain files and directories.

A run takes its user's credential lock before the shared slot, and gives up on both when its time is up: that
user's authorization flow may hold their lock for a while, and nobody else's command waits behind it. Callers on
the event loop go through ``in_lark_thread``, a few threads of this module's own, so commands waiting their turn
queue there rather than on the default executor the rest of the Gateway shares (09-29 review).
"""

import asyncio
import contextvars
import fcntl
import functools
import logging
import os
import pwd
import re
import signal
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from deerflow.config.paths import get_paths
from deerflow.integrations import lark_cli

from ggwork_pick import lark_credentials
from ggwork_pick.lark_policy import LarkRefused, risk_from_help

logger = logging.getLogger(__name__)

RUN_AS_ENV = "DEER_FLOW_LARK_CLI_RUN_AS"
RUN_LOCK_FILE = ".lark-cli.run.lock"
TIMEOUT_SECONDS = 60
HELP_TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 40_000
# What is read of each output stream; the rest is drained and dropped. A character is at most 4 UTF-8 bytes, so a
# stream longer than this still decodes to more than MAX_OUTPUT_CHARS characters and is reported as truncated.
MAX_OUTPUT_BYTES = MAX_OUTPUT_CHARS * 4 + 4
MAX_RISK_CACHE = 512
# The same network settings the Gateway's own lark-cli calls pass (lark_cli._LARK_CLI_PASSTHROUGH_ENV), HOME aside.
PASSTHROUGH_ENV = ("TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy")
SCRATCH_DIRS = ("work", "home", "tmp", "config", "data")
QUEUE_TIMEOUT = "飞书命令排队超时，请稍后再试"
CREDENTIALS_BUSY = "该用户的飞书授权正在进行，请完成授权后再试"
# One lark-cli process runs at a time; a few threads let the next command wait for it, and one whose user is still
# authorizing wait for that user, without holding up the rest. Further commands wait in the executor's queue.
MAX_WORKER_THREADS = 4
THREAD_NAME_PREFIX = "lark-cli"

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_POLL_SECONDS = 0.05
# How long the pipes may stay open once lark-cli has exited (a leftover child holding them is killed by _reap).
_DRAIN_SECONDS = 2.0
_READ_CHUNK = 64 * 1024
_ONE_AT_A_TIME = threading.Lock()
_CACHE_LOCK = threading.Lock()
_RISK_CACHE: dict[tuple[str, ...], str] = {}
_BINARY_CACHE: dict[str, str] = {}
_EXECUTOR = ThreadPoolExecutor(max_workers=MAX_WORKER_THREADS, thread_name_prefix=THREAD_NAME_PREFIX)


class LarkUnavailable(RuntimeError):
    """The tool cannot run here: no usable binary, no way to run lark-cli as the lark user, or a broken tree."""


class LarkBusy(TimeoutError):
    """The run's time was up before its turn came; the message is written for the user."""


@dataclass(frozen=True)
class RunAs:
    uid: int
    gid: int


@dataclass(frozen=True)
class Completed:
    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False


@dataclass(frozen=True)
class FeedbackExport:
    completed: Completed
    records: str = ""
    manifest: str = ""


def resolve_binary(*, deadline: float | None = None) -> str:
    """The Gateway's lark-cli; with a pinned image, only the pinned release."""
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("本轮普通执行时间已结束")
    with _CACHE_LOCK:
        cached = _BINARY_CACHE.get("path")
    if cached:
        return cached
    probe = lark_cli.probe_lark_cli() if deadline is None else lark_cli.probe_lark_cli(process_runner=functools.partial(_probe_process, deadline=deadline))
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("本轮普通执行时间已结束")
    if not probe.available or not probe.path:
        raise LarkUnavailable(f"网关没有可用的 lark-cli：{probe.error or '未安装'}")
    pinned = lark_cli.pinned_lark_cli_version()
    reported = _VERSION.search(probe.version or "")
    if pinned and (reported is None or reported.group(0) != pinned.removeprefix("v")):
        raise LarkUnavailable(f"镜像里的 lark-cli 版本（{probe.version}）不是固定的 {pinned}")
    with _CACHE_LOCK:
        _BINARY_CACHE["path"] = probe.path
    return probe.path


def _probe_process(args, *, deadline, timeout, env, check, capture_output, text):
    """Adapt the host's version probe to this worker's bounded process runner."""
    deadline = min(deadline, time.monotonic() + timeout)
    return _run_process(
        args,
        timeout=max(0.0, deadline - time.monotonic()),
        deadline=deadline,
        env=env,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_as() -> RunAs | None:
    """The image's lark user. A pinned image must name one: lark-cli never runs as the Gateway there."""
    name = os.getenv(RUN_AS_ENV, "").strip()
    if not name:
        if lark_cli.pinned_lark_cli_version() is not None:
            raise LarkUnavailable(f"镜像固定了 lark-cli 却没有设置 {RUN_AS_ENV}，不以网关身份运行")
        return None
    try:
        entry = pwd.getpwnam(name)
    except KeyError:
        raise LarkUnavailable(f"镜像里没有用户 {name}（{RUN_AS_ENV}）") from None
    if entry.pw_uid == 0:
        raise LarkUnavailable(f"{RUN_AS_ENV} 不能是 root")
    if os.geteuid() != 0:
        raise LarkUnavailable(f"网关不是 root，无法切换到 {name} 运行 lark-cli")
    _require_private_home()
    return RunAs(entry.pw_uid, entry.pw_gid)


def _require_private_home() -> None:
    """The lark user needs nothing under DEER_FLOW_HOME; pick_entrypoint closes it to other users at startup."""
    home = get_paths().base_dir
    try:
        mode = stat.S_IMODE(home.stat().st_mode)
    except OSError as exc:
        raise LarkUnavailable(f"无法检查数据目录 {home}：{exc}") from exc
    if mode & 0o007:
        raise LarkUnavailable(f"数据目录 {home} 对其他用户开放（{mode:o}），不以 {RUN_AS_ENV} 用户运行 lark-cli")


def child_env(scratch: Path) -> dict[str, str]:
    passthrough = {name: os.environ[name] for name in PASSTHROUGH_ENV if name in os.environ}
    return {
        "PATH": lark_cli.LARK_CLI_MINIMAL_PATH,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        **passthrough,
        "HOME": str(scratch / "home"),
        "TMPDIR": str(scratch / "tmp"),
        "LARKSUITE_CLI_CONFIG_DIR": str(scratch / "config"),
        "LARKSUITE_CLI_DATA_DIR": str(scratch / "data"),
        "LARKSUITE_CLI_NO_UPDATE_NOTIFIER": "1",
        "LARKSUITE_CLI_NO_SKILLS_NOTIFIER": "1",
    }


async def in_lark_thread[T](func: Callable[..., T], /, *args) -> T:
    """``asyncio.to_thread`` on this module's own threads. A call cancelled before it starts never runs."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_EXECUTOR, functools.partial(contextvars.copy_context().run, func, *args))


def run_guide(args: tuple[str, ...], *, timeout: float = HELP_TIMEOUT_SECONDS, deadline: float | None = None) -> Completed:
    """lark-cli's own help, schema and skill text, with empty credential directories."""
    if deadline is not None:
        deadline = min(deadline, time.monotonic() + timeout)
    binary, owner = resolve_binary(deadline=deadline), run_as()
    if deadline is None:
        deadline = time.monotonic() + timeout
    with _failures_as_unavailable("lark-cli 无法运行"), _slot(deadline), _scratch() as scratch:
        _hand_over(scratch, owner)
        return _execute(binary, args, scratch, owner, deadline)


def run_for_user(user_id: str, args: tuple[str, ...], *, timeout: float = TIMEOUT_SECONDS, deadline: float | None = None) -> Completed:
    """One command with a private copy of ``user_id``'s credentials; keeps what lark-cli refreshed in it."""
    return _run_user(user_id, args, timeout=timeout, deadline=deadline)


def run_feedback_export(user_id: str, table_id: str, field_ids: tuple[str, ...], offset: int, *, timeout: float = TIMEOUT_SECONDS) -> FeedbackExport:
    """Server-only fixed Base export. The general model tool still cannot read local artifacts."""
    from ggwork_pick.feedback.contracts import BASE_TOKEN, TABLE_BY_ID

    if table_id not in TABLE_BY_ID or type(offset) is not int or offset < 0:
        raise ValueError("反馈表或分页参数无效")
    if not 1 <= len(field_ids) <= 200 or any(not re.fullmatch(r"fld[A-Za-z0-9]+", value) for value in field_ids):
        raise ValueError("反馈字段无效")
    if not user_id or user_id in ("default", "system:shared"):
        raise ValueError("缺少反馈用户身份")
    if command_risk(("base", "+record-list")) != "read":
        raise LarkUnavailable("无法核实反馈导出为只读")
    projection = tuple(argument for field_id in field_ids for argument in ("--field-id", field_id))
    args = (
        "base",
        "+record-list",
        "--base-token",
        BASE_TOKEN,
        "--table-id",
        table_id,
        *projection,
        "--offset",
        str(offset),
        "--limit",
        "2000",
        "--format",
        "ndjson",
        "--output",
        "feedback.ndjson",
        "--as",
        "user",
    )
    return _run_user(user_id, args, timeout=timeout, export=True)


def _read_feedback_file(work: Path, name: str) -> str:
    if not stat.S_ISDIR(work.lstat().st_mode):
        raise LarkUnavailable("反馈导出目录无效")
    # The child has been reaped. Still refuse symlinks/devices and bound reads before parsing any contents.
    descriptor = os.open(work / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        cap = 8 * 1024 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_size > cap:
            raise LarkUnavailable("反馈导出不是有界普通文件")
        data = stream.read(cap + 1)
        if len(data) > cap:
            raise LarkUnavailable("反馈导出超过容量限制")
        return data.decode("utf-8")


def _run_user(user_id: str, args: tuple[str, ...], *, timeout: float, export: bool = False, deadline: float | None = None):
    if deadline is not None:
        deadline = min(deadline, time.monotonic() + timeout)
    binary, owner = resolve_binary(deadline=deadline), run_as()
    if deadline is None:
        deadline = time.monotonic() + timeout
    with _failures_as_unavailable("飞书凭据或 lark-cli 无法使用"), _user_lock(user_id, deadline), _slot(deadline):
        lark_cli.ensure_lark_cli_credential_tree(user_id)
        real = {"config": lark_cli.lark_cli_config_dir(user_id), "data": lark_cli.lark_cli_data_dir(user_id)}
        before = {name: lark_credentials.snapshot(path) for name, path in real.items()}
        with _scratch() as scratch:
            for name, path in real.items():
                lark_credentials.copy_tree(path, scratch / name)
            _hand_over(scratch, owner)
            completed = _execute(binary, args, scratch, owner, deadline)
            _keep_changes(user_id, scratch, real, before)
            if export:
                if completed.exit_code != 0 or completed.truncated:
                    return FeedbackExport(completed)
                work = scratch / "work"
                rows = _read_feedback_file(work, "feedback.ndjson")
                # Accept only these two fixed suffix conventions, pending live CLI verification. Reject
                # ambiguity rather than follow a path supplied in stdout; none come from model input.
                candidates = [name for name in ("feedback.manifest.json", "feedback.ndjson.manifest.json") if (work / name).exists()]
                if len(candidates) != 1:
                    raise LarkUnavailable("反馈导出清单缺失或不唯一")
                return FeedbackExport(completed, rows, _read_feedback_file(work, candidates[0]))
    return completed


def command_risk(path: tuple[str, ...], *, timeout: float = HELP_TIMEOUT_SECONDS, deadline: float | None = None) -> str:
    """The Risk label lark-cli's help gives ``path``, looked up once per process (the binary is fixed)."""
    with _CACHE_LOCK:
        cached = _RISK_CACHE.get(path)
    if cached:
        return cached
    completed = run_guide((*path, "--help"), timeout=timeout, deadline=deadline)
    if completed.exit_code != 0:
        detail = (completed.stderr or completed.stdout).strip()[:200]
        raise LarkRefused(f"无法确认 `lark-cli {' '.join(path)}` 是只读命令：{detail or f'退出码 {completed.exit_code}'}")
    risk = risk_from_help(path, completed.stdout)
    with _CACHE_LOCK:
        if len(_RISK_CACHE) >= MAX_RISK_CACHE:
            _RISK_CACHE.clear()
        _RISK_CACHE[path] = risk
    return risk


@contextmanager
def _user_lock(user_id: str, deadline: float) -> Iterator[None]:
    """The user's credential lock, shared with their authorization flow; waiting for it ends at ``deadline``."""
    with ExitStack() as stack:
        try:
            stack.enter_context(lark_cli.lark_credential_lock(user_id, deadline=deadline))
        except TimeoutError:
            raise LarkBusy(CREDENTIALS_BUSY) from None
        yield


@contextmanager
def _slot(deadline: float) -> Iterator[None]:
    """One lark-cli process at a time, across threads (the lock) and Gateway worker processes (the file lock)."""
    if not _ONE_AT_A_TIME.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise LarkBusy(QUEUE_TIMEOUT)
    try:
        path = get_paths().base_dir / RUN_LOCK_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as handle:
            _flock_until(handle, deadline)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)
    finally:
        _ONE_AT_A_TIME.release()


def _flock_until(handle, deadline: float) -> None:
    while True:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LarkBusy(QUEUE_TIMEOUT) from None
            time.sleep(_POLL_SECONDS)


@contextmanager
def _failures_as_unavailable(what: str) -> Iterator[None]:
    """A broken credential tree or a lark-cli that cannot start is an answer for the model, not a crash."""
    try:
        yield
    except (LarkUnavailable, TimeoutError):
        raise
    except (OSError, ValueError) as exc:
        raise LarkUnavailable(f"{what}：{exc}") from exc


@contextmanager
def _scratch() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="lark-run-") as raw:
        scratch = Path(raw)
        for name in SCRATCH_DIRS:
            (scratch / name).mkdir(mode=0o700)
        yield scratch


def _hand_over(scratch: Path, owner: RunAs | None) -> None:
    if owner is None:
        return
    for current, _dirs, files in os.walk(scratch, followlinks=False):
        os.chown(current, owner.uid, owner.gid, follow_symlinks=False)
        for name in files:
            os.chown(Path(current) / name, owner.uid, owner.gid, follow_symlinks=False)


def _execute(binary: str, args: tuple[str, ...], scratch: Path, owner: RunAs | None, deadline: float) -> Completed:
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        return Completed(124, "", "本轮剩余时间不足，没有运行 lark-cli")
    options = {
        "cwd": scratch / "work",
        "env": child_env(scratch),
        "stdin": subprocess.DEVNULL,
        "timeout": timeout,
        "deadline": deadline,
        "start_new_session": True,
    }
    if owner is not None:
        options.update(user=owner.uid, group=owner.gid, extra_groups=[], umask=0o077)
    try:
        result = _run_process([binary, *args], **options)
    except subprocess.TimeoutExpired:
        return Completed(124, "", f"lark-cli 运行超时（{timeout:.0f} 秒），已终止")
    finally:
        _reap(owner)
    stdout, cut_out = _cap(result.stdout or "")
    stderr, cut_err = _cap(result.stderr or "")
    return Completed(result.returncode, stdout, stderr, cut_out or cut_err)


def _run_process(args: list[str], *, timeout: float, deadline: float | None = None, **options) -> subprocess.CompletedProcess[str]:
    """``subprocess.run(capture_output=True)`` that keeps at most MAX_OUTPUT_BYTES of each stream.

    The rest is read and dropped, so lark-cli neither fills the Gateway's memory nor stalls on a full pipe, and its
    exit code stays its own. Output is decoded as UTF-8, invalid bytes replaced. On timeout the process group is
    killed and TimeoutExpired raised, as ``subprocess.run`` would.
    """
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
    if timeout <= 0:
        raise subprocess.TimeoutExpired(args, timeout)
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)  # noqa: S603 - argv list, no shell, checked by lark_policy
    out, err = _Capped(process.stdout), _Capped(process.stderr)
    completed = False
    try:
        remaining = timeout if deadline is None else max(0.0, min(timeout, deadline - time.monotonic()))
        returncode = process.wait(timeout=remaining)
        drained = time.monotonic() + _DRAIN_SECONDS
        if deadline is not None:
            drained = min(drained, deadline)
        stdout, stderr = out.text(until=drained), err.text(until=drained)
        if deadline is not None and (not out.finished or not err.finished):
            # A successful parent can leave children holding its output pipes.
            # Partial output after the deadline is not a successful probe/read.
            raise subprocess.TimeoutExpired(args, timeout)
        completed = True
        return subprocess.CompletedProcess(args, returncode, stdout, stderr)
    finally:
        if deadline is not None or not completed:
            # Deadline-bound commands own their entire process group, including
            # children whose parent exited successfully or closed its pipes.
            _kill(process, group=bool(options.get("start_new_session")))
            cleanup_until = time.monotonic() + _DRAIN_SECONDS
            try:
                process.wait(timeout=max(0.0, cleanup_until - time.monotonic()))
            finally:
                # One shared bounded cleanup budget for reap and both drains.
                out.text(until=cleanup_until)
                err.text(until=cleanup_until)


class _Capped:
    """One output pipe, read to its end on a thread of its own; the first MAX_OUTPUT_BYTES are kept."""

    def __init__(self, stream) -> None:
        self._stream = stream
        self._kept = bytearray()
        self._thread = threading.Thread(target=self._drain, name=f"{THREAD_NAME_PREFIX}-pipe", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        with self._stream:
            while chunk := self._stream.read1(_READ_CHUNK):
                room = MAX_OUTPUT_BYTES - len(self._kept)
                if room > 0:
                    self._kept += chunk[:room]

    @property
    def finished(self) -> bool:
        return not self._thread.is_alive()

    def text(self, *, until: float) -> str:
        self._thread.join(max(0.0, until - time.monotonic()))
        return bytes(self._kept).decode("utf-8", errors="replace")


def _kill(process: subprocess.Popen, *, group: bool) -> None:
    with suppress(ProcessLookupError, PermissionError):
        if group:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()


def _reap(owner: RunAs | None, proc: Path = Path("/proc")) -> None:
    """Kill whatever the lark user left running (a child's children, a daemon): nothing of it outlives a run."""
    if owner is None or not proc.is_dir():
        return
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        uids = next((line.split()[1:] for line in status.splitlines() if line.startswith("Uid:")), [])
        if str(owner.uid) in uids:
            with suppress(ProcessLookupError, PermissionError):
                os.kill(int(entry.name), signal.SIGKILL)


def _cap(text: str) -> tuple[str, bool]:
    return (text, False) if len(text) <= MAX_OUTPUT_CHARS else (text[:MAX_OUTPUT_CHARS], True)


def _keep_changes(user_id: str, scratch: Path, real: dict[str, Path], before: dict[str, dict[str, bytes]]) -> None:
    """Keep what lark-cli changed in its copy; on any doubt keep the previous credentials."""
    changed: list[str] = []
    try:
        after = {name: lark_credentials.snapshot(scratch / name) for name in real}
        for name, path in real.items():
            if after[name] != before[name]:
                lark_credentials.install(path, scratch / name)
                changed.append(name)
    except (OSError, lark_credentials.UnsafeTree) as exc:
        logger.warning("Keeping the previous lark-cli credentials of user %s: %s", user_id, exc)
    if changed:
        lark_cli.ensure_lark_cli_credential_tree(user_id)
