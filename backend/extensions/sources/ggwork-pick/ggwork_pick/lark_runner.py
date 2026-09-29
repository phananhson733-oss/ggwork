"""Runs one lark-cli command without handing it the Gateway (docs/pick-workbench/lark-personal-auth.md section 3).

Each run gets a fresh scratch directory: an empty working directory, its own HOME and TMPDIR, and a private copy of
the user's credential tree taken under that user's credential lock. lark-cli sees a listed environment only and, in
the image, runs as the unprivileged user named by DEER_FLOW_LARK_CLI_RUN_AS, so it cannot read the Gateway's /proc
environment, DEER_FLOW_HOME or other users' credential trees. One lark-cli process runs at a time, across threads
and worker processes, and whatever the lark user left running is killed when the run ends, so no other user's copy
exists while a run is in flight. What lark-cli changed in its copy (a refreshed token, a cache) is copied back before
the lock is released, unless the copy then holds anything but plain files and directories.
"""

import fcntl
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
from collections.abc import Iterator
from contextlib import contextmanager, suppress
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
MAX_RISK_CACHE = 512
PASSTHROUGH_ENV = ("TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY")
SCRATCH_DIRS = ("work", "home", "tmp", "config", "data")
QUEUE_TIMEOUT = "飞书命令排队超时，请稍后再试"

_VERSION = re.compile(r"\d+\.\d+\.\d+")
_POLL_SECONDS = 0.05
_ONE_AT_A_TIME = threading.Lock()
_CACHE_LOCK = threading.Lock()
_RISK_CACHE: dict[tuple[str, ...], str] = {}
_BINARY_CACHE: dict[str, str] = {}


class LarkUnavailable(RuntimeError):
    """The tool cannot run here: no usable binary, no way to run lark-cli as the lark user, or a broken tree."""


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


def resolve_binary() -> str:
    """The Gateway's lark-cli; with a pinned image, only the pinned release."""
    with _CACHE_LOCK:
        cached = _BINARY_CACHE.get("path")
    if cached:
        return cached
    probe = lark_cli.probe_lark_cli()
    if not probe.available or not probe.path:
        raise LarkUnavailable(f"网关没有可用的 lark-cli：{probe.error or '未安装'}")
    pinned = lark_cli.pinned_lark_cli_version()
    reported = _VERSION.search(probe.version or "")
    if pinned and (reported is None or reported.group(0) != pinned.removeprefix("v")):
        raise LarkUnavailable(f"镜像里的 lark-cli 版本（{probe.version}）不是固定的 {pinned}")
    with _CACHE_LOCK:
        _BINARY_CACHE["path"] = probe.path
    return probe.path


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


def run_guide(args: tuple[str, ...], *, timeout: float = HELP_TIMEOUT_SECONDS) -> Completed:
    """lark-cli's own help, schema and skill text, with empty credential directories."""
    binary, owner = resolve_binary(), run_as()
    deadline = time.monotonic() + timeout
    with _slot(deadline), _failures_as_unavailable("lark-cli 无法运行"), _scratch() as scratch:
        _hand_over(scratch, owner)
        return _execute(binary, args, scratch, owner, deadline)


def run_for_user(user_id: str, args: tuple[str, ...], *, timeout: float = TIMEOUT_SECONDS) -> Completed:
    """One command with a private copy of ``user_id``'s credentials; keeps what lark-cli refreshed in it."""
    binary, owner = resolve_binary(), run_as()
    deadline = time.monotonic() + timeout
    with _slot(deadline), _failures_as_unavailable("飞书凭据或 lark-cli 无法使用"), lark_cli.lark_credential_lock(user_id):
        lark_cli.ensure_lark_cli_credential_tree(user_id)
        real = {"config": lark_cli.lark_cli_config_dir(user_id), "data": lark_cli.lark_cli_data_dir(user_id)}
        before = {name: lark_credentials.snapshot(path) for name, path in real.items()}
        with _scratch() as scratch:
            for name, path in real.items():
                lark_credentials.copy_tree(path, scratch / name)
            _hand_over(scratch, owner)
            completed = _execute(binary, args, scratch, owner, deadline)
            _keep_changes(user_id, scratch, real, before)
    return completed


def command_risk(path: tuple[str, ...], *, timeout: float = HELP_TIMEOUT_SECONDS) -> str:
    """The Risk label lark-cli's help gives ``path``, looked up once per process (the binary is fixed)."""
    with _CACHE_LOCK:
        cached = _RISK_CACHE.get(path)
    if cached:
        return cached
    completed = run_guide((*path, "--help"), timeout=timeout)
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
def _slot(deadline: float) -> Iterator[None]:
    """One lark-cli process at a time, across threads (the lock) and Gateway worker processes (the file lock)."""
    if not _ONE_AT_A_TIME.acquire(timeout=max(0.0, deadline - time.monotonic())):
        raise TimeoutError(QUEUE_TIMEOUT)
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
                raise TimeoutError(QUEUE_TIMEOUT) from None
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
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": timeout,
        "check": False,
        "start_new_session": True,
    }
    if owner is not None:
        options.update(user=owner.uid, group=owner.gid, extra_groups=[], umask=0o077)
    try:
        result = subprocess.run([binary, *args], **options)  # noqa: S603 - argv list, no shell, checked by lark_policy
    except subprocess.TimeoutExpired:
        return Completed(124, "", f"lark-cli 运行超时（{timeout:.0f} 秒），已终止")
    finally:
        _reap(owner)
    stdout, cut_out = _cap(result.stdout or "")
    stderr, cut_err = _cap(result.stderr or "")
    return Completed(result.returncode, stdout, stderr, cut_out or cut_err)


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
