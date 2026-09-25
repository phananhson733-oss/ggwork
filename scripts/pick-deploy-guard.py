#!/usr/bin/env python3
"""Deploy guard for the pick workbench: run it before every gateway, cron or frontend
deploy (trends radar plan D41, section 10).

From the checkout to deploy, with the backend environment (psycopg, postgres extra):
    backend/.venv/bin/python scripts/pick-deploy-guard.py gateway [--first-record]
    backend/.venv/bin/python scripts/pick-deploy-guard.py cron {trends,gsc} [...]
    backend/.venv/bin/python scripts/pick-deploy-guard.py frontend [--out DIR] [...]
Common options: --remote (default ggwork: this repository's origin is upstream
DeerFlow), --repo (default: the checkout holding this script).

Every mode refuses a dirty tree or an untracked .env* file (ignored ones included: a
CLI that uploads the directory takes them along), fetches the remote's main and refuses
a HEAD other than it, and refuses when the last production commit progress.md records
for the target is not an ancestor of HEAD: a rollback is a revert commit on main, never
a deploy from an older checkout. gateway and cron then read the production migration
head as pick_observer, the DSN coming from the mode-600 file PICK_OBS_DSN_FILE, and
refuse a head unknown to the chain this checkout ships: the revision files of the
managed copy the image installs, which must match the source's. A cron also needs its
deploy/pick-obs/<service>/railway.toml and a production head equal to the chain's head
and not before MIN_MIGRATION_HEAD: a new migration reaches production through the
gateway first (D5). frontend exports `git archive <HEAD> frontend` into a new directory.

The guard never runs railway or vercel: it prints the next step, the commit and the line
to append to progress.md once the deploy is verified. No DSN, password or database
message is ever printed; errors name their class and SQLSTATE.
Exit status: 0 passed, 1 a check could not run (git or the database failed), 2 refused,
130 interrupted.
"""

import argparse
import ast
import errno
import os
import re
import shlex
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_INTERRUPTED = 0, 1, 2, 130

DEFAULT_REPO = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "ggwork"
BRANCH = "main"
CRON_SERVICES = ("trends", "gsc")
TARGETS = ("gateway", "frontend", *(f"cron:{service}" for service in CRON_SERVICES))
RAILWAY_SERVICE_PREFIX = "pick-obs-"
FRONTEND = "frontend"

# The Dockerfile installs the managed copy: its chain is the one the image knows.
SOURCE_VERSIONS = Path("customizations/pick-workbench/ggwork_pick/migrations/versions")
MANAGED_PACKAGE = Path("backend/extensions/sources/ggwork-pick/ggwork_pick")
MANAGED_VERSIONS = MANAGED_PACKAGE / "migrations" / "versions"
MANAGED_OBSERVE_VERSIONS = MANAGED_PACKAGE / "observe" / "versions.py"
MIN_HEAD_NAME = "MIN_MIGRATION_HEAD"
PROGRESS = Path("docs/pick-workbench/progress.md")

DSN_FILE_ENV = "PICK_OBS_DSN_FILE"
SSL_MODE_ENV = "PGSSLMODE"
OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"  # the variable migration 0007 reads
# TR-12's role, which may read the version table in the schema the host's tables live
# in (ggwork_pick.mirror.connection.SEARCH_PATH).
DEFAULT_OBSERVER_ROLE = "pick_observer"
VERSION_TABLE = "deerflow.ggwp_alembic_version"
VERSION_QUERY = f"SELECT version_num FROM {VERSION_TABLE} ORDER BY version_num"
APPLICATION_NAME = "ggwp-deploy-guard"
CONNECT_TIMEOUT_SECONDS = 15
# A gateway migrating right now holds the version table: wait no longer than this.
STATEMENT_TIMEOUT = "15s"
MAX_DSN_BYTES = 64 * 1024
# ggwork_pick.observe.crypto's rule for private files: no exec bit, nothing for others.
WIDER_THAN_600 = 0o177
PRIVATE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
SQLSTATE_HINTS = {
    "42501": "observer 缺版本表的 SELECT：按 TR-12 补授权（gateway 里跑 regrant）",
    "42P01": "库里没有 deerflow.ggwp_alembic_version：核对 DSN 连的是不是生产库",
    "28P01": "observer 的口令不对：核对 DSN 文件",
}

GIT_TIMEOUT_SECONDS = 120
STATUS_ARGS = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
# Every untracked .env*, ignored ones included (no --exclude-standard); a dependency's
# own files under node_modules or .venv are not ours.
ENV_FILES_ARGS = (
    *("ls-files", "--others", "-z", "--", ":(glob)**/.env*"),
    *(":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**"),
)
RECORD_PREFIX = "pick-deploy-guard"
RECORD_MARK = f"{RECORD_PREFIX} target="
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SHOWN_ENTRIES = 10
NEXT = "下一步（守卫不执行）："
_REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TOKEN = re.compile(r"\s*([a-z_]+)=([^\s`]+)")
_POSTGRES_SCHEME = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://", re.IGNORECASE)
_OPEN_REASONS = {
    errno.ENOENT: "不存在",
    errno.ELOOP: "是符号链接",
    errno.EACCES: "读不了",
}
_MISSING = object()


class Refused(Exception):
    """A check failed: do not deploy. The message never carries a secret."""


class CheckFailed(Exception):
    """A check could not run. The message names the step, never a secret."""


class GitFailed(CheckFailed):
    def __init__(self, command: str, status: int):
        super().__init__(f"git {command} 失败（退出码 {status}），手动执行它看原因")


@dataclass(frozen=True)
class ProdState:
    role: str
    versions: tuple[str, ...]


@dataclass(frozen=True)
class Record:
    target: str
    commit: str
    at: datetime
    line: int


@dataclass(frozen=True)
class Request:
    mode: str  # gateway, cron or frontend
    remote: str
    first_record: bool
    service: str | None = None
    out: Path | None = None

    @property
    def target(self) -> str:
        return f"cron:{self.service}" if self.mode == "cron" else self.mode


@dataclass(frozen=True)
class Passed:
    request: Request
    root: Path
    commit: str
    at: datetime
    chain: tuple[str, ...] = ()
    prod_head: str | None = None
    export_dir: Path | None = None


class Repo(Protocol):
    """What the guard asks git. GitRepo answers from a checkout, tests from fields."""

    root: Path

    def status_entries(self) -> Sequence[str]: ...
    def untracked_env_files(self) -> Sequence[str]: ...
    def fetch(self, remote: str, branch: str) -> None: ...
    def commit_of(self, ref: str) -> str | None: ...
    def is_ancestor(self, older: str, newer: str) -> bool: ...
    def archive(self, commit: str, path: str, dest: Path) -> None: ...


ProdReader = Callable[..., ProdState]


def _git_env() -> dict[str, str]:
    # A fetch that needs credentials fails instead of prompting, and status takes no
    # optional lock in a repository other sessions share.
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def _command(cwd: Path, *args: str) -> list[str]:
    return ["git", "-C", str(cwd), *args]


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    command, env = _command(cwd, *args), _git_env()
    return subprocess.run(
        command, capture_output=True, env=env, timeout=GIT_TIMEOUT_SECONDS
    )


def _checked(cwd: Path, *args: str) -> bytes:
    # git's stderr can quote a remote URL with its token: only the status is shown.
    done = _git(cwd, *args)
    if done.returncode != 0:
        raise GitFailed(args[0], done.returncode)
    return done.stdout


def _split(output: bytes) -> tuple[str, ...]:
    return tuple(
        part.decode("utf-8", "replace") for part in output.split(b"\0") if part
    )


class GitRepo:
    """The checkout at `root`, asked through the git command line."""

    def __init__(self, root: Path):
        self.root = root

    @classmethod
    def open(cls, path: Path) -> "GitRepo":
        top = _checked(path, "rev-parse", "--show-toplevel")
        return cls(Path(top.decode().strip()))

    def status_entries(self) -> tuple[str, ...]:
        return _split(_checked(self.root, *STATUS_ARGS))

    def untracked_env_files(self) -> tuple[str, ...]:
        return _split(_checked(self.root, *ENV_FILES_ARGS))

    def fetch(self, remote: str, branch: str) -> None:
        refspec = f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}"
        _checked(self.root, "fetch", "--quiet", "--no-tags", remote, refspec)

    def commit_of(self, ref: str) -> str | None:
        done = _git(self.root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        return done.stdout.decode().strip() if done.returncode == 0 else None

    def is_ancestor(self, older: str, newer: str) -> bool:
        status = _git(self.root, "merge-base", "--is-ancestor", older, newer).returncode
        if status not in (0, 1):
            raise GitFailed("merge-base", status)
        return status == 0

    def archive(self, commit: str, path: str, dest: Path) -> None:
        command = _command(self.root, "archive", "--format=tar", commit, "--", path)
        pipe, quiet = subprocess.PIPE, subprocess.DEVNULL
        with subprocess.Popen(
            command, stdout=pipe, stderr=quiet, env=_git_env()
        ) as git:
            try:
                with tarfile.open(fileobj=git.stdout, mode="r|") as archive:
                    archive.extractall(dest, filter="data")
                broken = False
            except tarfile.TarError:
                broken = True
            git.stdout.read()  # the padding after the end-of-archive blocks
            status = git.wait(timeout=GIT_TIMEOUT_SECONDS)
        if status != 0:
            raise GitFailed("archive", status)
        if broken:
            raise CheckFailed("git archive 的输出读不成 tar 包")


def _literal(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return _MISSING


def _assigned(node: ast.stmt) -> tuple[str, object] | None:
    match node:
        case ast.Assign(targets=[ast.Name(id=name)], value=value):
            return name, _literal(value)
        case ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
            return name, _literal(value)
    return None


def _assignments(path: Path) -> dict[str, object]:
    """A file's module-level `NAME = literal` assignments, parsed and never run."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.name)
    return dict(pair for pair in map(_assigned, tree.body) if pair is not None)


def read_constant(path: Path, name: str) -> str:
    if not path.is_file():
        raise Refused(f"找不到 {path}")
    value = _assignments(path).get(name, _MISSING)
    if not isinstance(value, str) or not value:
        raise Refused(f"{path.name} 里没有字符串常量 {name}")
    return value


def read_revision(path: Path) -> tuple[str, str | None]:
    values = _assignments(path)
    revision = values.get("revision", _MISSING)
    down = values.get("down_revision", _MISSING)
    if not isinstance(revision, str) or not revision:
        raise Refused(f"迁移文件 {path.name} 没有 revision 字符串")
    if down is _MISSING:
        raise Refused(f"迁移文件 {path.name} 没有 down_revision")
    if down is not None and not isinstance(down, str):
        raise Refused(f"迁移文件 {path.name} 的 down_revision 是多个：链不支持合并点")
    return revision, down


def _repeated(values: Sequence[str | None]) -> str:
    return "、".join(
        sorted({v for v in values if v is not None and values.count(v) > 1})
    )


def _linear(links: tuple[tuple[str, str | None], ...]) -> tuple[str, ...]:
    """The revisions from base to head; Refused unless they form one unbranched line."""
    revisions = [revision for revision, _ in links]
    if repeated := _repeated(revisions):
        raise Refused(f"迁移修订 {repeated} 出现了不止一次")
    bases = [revision for revision, down in links if down is None]
    if len(bases) != 1:
        raise Refused(
            f"迁移链须恰好有一个起点（down_revision 为 None），现有 {len(bases)} 个"
        )
    dangling = [
        f"{rev} → {down}" for rev, down in links if down not in (None, *revisions)
    ]
    if dangling:
        raise Refused(f"迁移的上一个修订不在链里：{'、'.join(sorted(dangling))}")
    if forks := _repeated([down for _, down in links]):
        raise Refused(f"迁移链在 {forks} 处分叉")
    following = {down: revision for revision, down in links}
    chain = (bases[0],)
    while chain[-1] in following:
        chain = (*chain, following[chain[-1]])
    if len(chain) != len(links):
        raise Refused("迁移链有环：有修订从起点走不到")
    return chain


def read_chain(directory: Path) -> tuple[str, ...]:
    """An Alembic versions directory's chain, from each revision and down_revision."""
    if not directory.is_dir():
        raise Refused(f"找不到迁移目录 {directory}")
    files = sorted(p for p in directory.glob("*.py") if not p.name.startswith("__"))
    if not files:
        raise Refused(f"迁移目录 {directory} 里没有迁移文件")
    return _linear(tuple(read_revision(path) for path in files))


def _span(chain: tuple[str, ...]) -> str:
    return f"{chain[0]} → {chain[-1]}"


def local_chain(root: Path) -> tuple[str, ...]:
    shipped = read_chain(root / MANAGED_VERSIONS)
    source = read_chain(root / SOURCE_VERSIONS)
    if shipped != source:
        raise Refused(
            f"托管副本的迁移链（{_span(shipped)}）与源码的（{_span(source)}）不一致，"
            "而镜像装的是托管副本：先按计划 D21 刷新托管副本并合进 main"
        )
    return shipped


def _tokens(text: str) -> dict[str, str]:
    """The leading key=value words of text, up to the first word that is not one."""
    pairs, position = (), 0
    while (match := _TOKEN.match(text, position)) is not None:
        pairs, position = (*pairs, (match.group(1), match.group(2))), match.end()
    return dict(pairs)


def _parse_record(line: str, number: int) -> Record:
    fields = _tokens(line[line.index(RECORD_MARK) + len(RECORD_PREFIX) :])
    target, commit = fields.get("target"), fields.get("commit", "")
    malformed = (
        f"{PROGRESS.name} 第 {number} 行的守卫记录格式不对：照守卫打印的原样追加"
    )
    if target not in TARGETS or not _COMMIT.fullmatch(commit):
        raise Refused(malformed)
    try:
        at = datetime.strptime(fields.get("at", ""), STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        raise Refused(malformed) from None
    return Record(target=target, commit=commit, at=at, line=number)


def parse_records(text: str) -> tuple[Record, ...]:
    """The guard's records in text, in file order; prose naming the script is not."""
    lines = enumerate(text.splitlines(), 1)
    return tuple(_parse_record(line, n) for n, line in lines if RECORD_MARK in line)


def record_text(passed: Passed) -> str:
    fields = (("target", passed.request.target), ("commit", passed.commit))
    if passed.prod_head is not None:
        heads = (("prod_head", passed.prod_head), ("chain_head", passed.chain[-1]))
        fields = (*fields, *heads)
    stamp = passed.at.astimezone(UTC).strftime(STAMP_FORMAT)
    words = (f"{key}={value}" for key, value in (*fields, ("at", stamp)))
    return " ".join((RECORD_PREFIX, *words))


def _private_problem(info: os.stat_result) -> str | None:
    if not stat.S_ISREG(info.st_mode):
        return "不是普通文件"
    if info.st_uid != os.geteuid():
        return "不属于当前用户"
    if info.st_mode & WIDER_THAN_600:
        return "权限宽于 600（chmod 600 后重试）"
    return None


def read_private_file(path: Path) -> bytes:
    """A regular file this user owns, mode 600 or narrower, not a symlink: checked on
    the descriptor that reads it. O_NONBLOCK: a FIFO is refused, not waited on."""
    try:
        fd = os.open(path, PRIVATE_OPEN_FLAGS)
    except OSError as exc:
        reason = _OPEN_REASONS.get(exc.errno, "打不开")
        raise Refused(f"{DSN_FILE_ENV} 指向的文件{reason}") from None
    try:
        if problem := _private_problem(os.fstat(fd)):
            raise Refused(f"{DSN_FILE_ENV} 指向的文件{problem}")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(MAX_DSN_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_DSN_BYTES:
        raise Refused(f"{DSN_FILE_ENV} 指向的文件超过 {MAX_DSN_BYTES} 字节")
    return data


def libpq_url(text: str) -> str:
    """postgresql:// for a postgres URL with or without a SQLAlchemy driver suffix."""
    match = _POSTGRES_SCHEME.match(text)
    if match is None:
        raise Refused(f"{DSN_FILE_ENV} 指向的文件不是 PostgreSQL 连接 URL")
    return "postgresql://" + text[match.end() :]


def read_dsn(environ: Mapping[str, str]) -> str:
    name = environ.get(DSN_FILE_ENV, "").strip()
    if not name:
        raise Refused(f"缺少环境变量 {DSN_FILE_ENV}：指向存 observer DSN 的 600 文件")
    try:
        text = read_private_file(Path(name)).decode("utf-8").strip()
    except UnicodeDecodeError:
        raise Refused(f"{DSN_FILE_ENV} 指向的文件不是 UTF-8 文本") from None
    if not text or "\n" in text or "\r" in text:
        raise Refused(f"{DSN_FILE_ENV} 指向的文件应只有一行连接串")
    return libpq_url(text)


def read_prod_state(dsn: str, *, sslmode: str) -> ProdState:
    """The role the DSN logs in as and the version rows, in one READ ONLY transaction
    that is rolled back."""
    import psycopg  # the backend environment's postgres extra; gateway and cron only

    options = {"connect_timeout": CONNECT_TIMEOUT_SECONDS, "sslmode": sslmode}
    with psycopg.connect(dsn, application_name=APPLICATION_NAME, **options) as conn:
        conn.read_only = True
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        role = conn.execute("SELECT current_user").fetchone()[0]
        versions = tuple(row[0] for row in conn.execute(VERSION_QUERY))
        conn.rollback()
    return ProdState(role=role, versions=versions)


def production_head(environ: Mapping[str, str], read_prod: ProdReader) -> str:
    dsn = read_dsn(environ)
    sslmode = environ.get(SSL_MODE_ENV, "").strip()
    if not sslmode:
        raise Refused(f"缺少环境变量 {SSL_MODE_ENV}：生产用 require 或更严")
    role = environ.get(OBSERVER_ROLE_ENV, "").strip() or DEFAULT_OBSERVER_ROLE
    state = read_prod(dsn, sslmode=sslmode)
    if state.role != role:
        raise Refused(f"{DSN_FILE_ENV} 连上的角色不是 {role}：守卫只用 observer 的 DSN")
    if not state.versions:
        raise Refused("生产库的 ggwp_alembic_version 是空的：先查清生产库的迁移状态")
    if len(state.versions) > 1:
        count = len(state.versions)
        raise Refused(
            f"生产库的 ggwp_alembic_version 有多行（{count} 行）：链只能有一个头"
        )
    return state.versions[0]


def _listed(entries: Sequence[str]) -> str:
    shown = "、".join(entries[:SHOWN_ENTRIES])
    return f"{shown}……" if len(entries) > SHOWN_ENTRIES else shown


def check_worktree(repo: Repo) -> None:
    if dirty := tuple(repo.status_entries()):
        raise Refused(
            f"工作区不干净（{len(dirty)} 项）：{_listed(dirty)}。"
            "提交、还原，或换一个干净的检出再部署"
        )
    if env_files := tuple(repo.untracked_env_files()):
        raise Refused(
            f"检出里有未跟踪的 .env* 文件（gitignore 的也算）：{_listed(env_files)}。"
            "部署 CLI 可能连 gitignore 的文件一起上传（Vercel 就会），移走后再部署"
        )


def verified_head(repo: Repo, remote: str) -> str:
    repo.fetch(remote, BRANCH)
    main = f"{remote}/{BRANCH}"
    head, fetched = repo.commit_of("HEAD"), repo.commit_of(f"refs/remotes/{main}")
    if head is None or fetched is None:
        raise CheckFailed(f"读不到 HEAD 或 {main} 的提交")
    if head != fetched:
        raise Refused(
            f"HEAD {head[:12]} 不等于刚取回的 {main} {fetched[:12]}："
            f"只从 {main} 的干净检出部署（git switch --detach {main}）"
        )
    return head


def _records_for(repo: Repo, target: str) -> list[Record]:
    path = repo.root / PROGRESS
    if not path.is_file():
        raise Refused(f"找不到 {PROGRESS}")
    records = parse_records(path.read_text(encoding="utf-8"))
    return [record for record in records if record.target == target]


def check_record(repo: Repo, request: Request, commit: str) -> None:
    """The last production commit on record for the target is HEAD or its ancestor."""
    target = request.target
    records = _records_for(repo, target)
    if not records and not request.first_record:
        raise Refused(
            f"{PROGRESS} 里还没有 {target} 的守卫记录："
            "第一次经守卫部署这个目标时带 --first-record"
        )
    if records and request.first_record:
        raise Refused(f"{PROGRESS} 里已有 {target} 的守卫记录，不能再带 --first-record")
    if not records:
        return
    last = max(records, key=lambda record: (record.at, record.line))
    known = repo.commit_of(last.commit)
    if known is None or not repo.is_ancestor(known, commit):
        raise Refused(
            f"{PROGRESS.name} 第 {last.line} 行记录的 {target} 生产提交 "
            f"{last.commit[:12]} 不是 HEAD 的祖先：从这里部署会回退生产，"
            "或丢掉只在别处的改动。回滚只用 main 上的 revert 提交（计划第 10 节）"
        )


def check_cron_config(root: Path, service: str) -> None:
    config = Path("deploy") / "pick-obs" / service / "railway.toml"
    if not (root / config).is_file():
        raise Refused(
            f"找不到 {config}：Railway 服务 {RAILWAY_SERVICE_PREFIX}{service} 的配置"
            "路径指向它，缺了会读到根目录 railway.toml 的 gateway 启动命令"
        )


def _check_cron_head(root: Path, chain: tuple[str, ...], prod_head: str) -> None:
    minimum = read_constant(root / MANAGED_OBSERVE_VERSIONS, MIN_HEAD_NAME)
    if minimum not in chain:
        raise Refused(f"{MIN_HEAD_NAME} {minimum} 不在本检出的迁移链里")
    if chain.index(prod_head) < chain.index(minimum):
        raise Refused(
            f"cron 要求生产迁移头不早于 {minimum}（现在是 {prod_head}）："
            f"先用守卫部署带 {minimum} 的 gateway（计划第 10 节 S3）"
        )
    if prod_head != chain[-1]:
        raise Refused(
            f"本检出的迁移链头 {chain[-1]} 比生产的 {prod_head} 新：先用守卫部署 "
            f"gateway 把生产升到 {chain[-1]}，再部署两个 cron（计划 D5）"
        )


def check_prod_head(
    request: Request, root: Path, chain: tuple[str, ...], head: str
) -> None:
    if head not in chain:
        raise Refused(
            f"生产迁移头 {head} 不在本检出的迁移链里（{_span(chain)}）：这份代码认不出"
            "生产库，gateway 会加载扩展失败而 /health/ready 照样通过，cron 的自检会"
            "拒跑（设计 3.8、计划 D5）。先把带这个迁移的提交合进 "
            f"{request.remote}/{BRANCH}"
        )
    if request.mode == "cron":
        _check_cron_head(root, chain, head)


def _fresh_directory(out: Path | None, commit: str) -> Path:
    if out is None:
        return Path(tempfile.mkdtemp(prefix=f"pick-frontend-{commit[:12]}-"))
    try:
        out.mkdir(mode=0o700)
    except FileExistsError:
        raise Refused(f"导出目录 {out} 已存在：守卫只往新建的目录里导出") from None
    except FileNotFoundError:
        raise Refused(f"导出目录 {out} 的上级目录不存在") from None
    out.chmod(0o700)
    return out


def export_frontend(repo: Repo, commit: str, out: Path | None) -> Path:
    dest = _fresh_directory(out, commit)
    repo.archive(commit, FRONTEND, dest)
    if not (dest / FRONTEND / "package.json").is_file():
        raise CheckFailed(f"导出目录里没有 {FRONTEND}/package.json")
    return dest


@dataclass(frozen=True)
class Sources:
    """Where run() reads production and the clock from; the tests pass fakes."""

    read_prod: ProdReader
    environ: Mapping[str, str]
    now: Callable[[], datetime]


def run(request: Request, repo: Repo, sources: Sources) -> Passed:
    """Every check, the local ones first: nothing is fetched for a dirty tree, and
    the database is read only after every local check has passed."""
    if not _REMOTE_NAME.fullmatch(request.remote):
        raise Refused(
            "远端名只能由字母、数字、点、下划线和连字符组成，且不以连字符开头"
        )
    check_worktree(repo)
    commit = verified_head(repo, request.remote)
    check_record(repo, request, commit)
    if request.mode == FRONTEND:
        export_dir = export_frontend(repo, commit, request.out)
        return Passed(request, repo.root, commit, sources.now(), export_dir=export_dir)
    chain = local_chain(repo.root)
    if request.mode == "cron":
        check_cron_config(repo.root, request.service)
    head = production_head(sources.environ, sources.read_prod)
    check_prod_head(request, repo.root, chain, head)
    return Passed(
        request, repo.root, commit, sources.now(), chain=chain, prod_head=head
    )


def _notes(passed: Passed) -> tuple[str, ...]:
    if passed.prod_head is None:
        return ()
    old, new, chain = passed.prod_head, passed.chain[-1], passed.chain
    heads = (
        f"- 生产迁移头：{old}；本检出的迁移链：{_span(chain)}（{len(chain)} 个修订）"
    )
    if old == new:
        return (heads,)
    upgrade = (
        f"- 本次部署会把生产迁移头从 {old} 升到 {new}。gateway 上线并核对之后，"
        "两个 cron 都要从同一提交经守卫重部署（计划 D5）"
    )
    return (heads, upgrade)


def next_steps(passed: Passed) -> tuple[str, ...]:
    request, root = passed.request, shlex.quote(str(passed.root))
    if request.mode == "gateway":
        return (NEXT, f"  cd {root} && railway up --detach")
    if request.mode == "cron":
        service = f"{RAILWAY_SERVICE_PREFIX}{request.service}"
        return (NEXT, f"  cd {root} && railway up --detach --service {service}")
    export = shlex.quote(str(passed.export_dir))
    project = "<已 vercel link 的检出>/.vercel/project.json"
    return (
        f"已导出：{passed.export_dir}/{FRONTEND}（git archive {passed.commit[:12]}）",
        NEXT,
        f"  1. mkdir -p {export}/.vercel && cp {project} {export}/.vercel/",
        f"  2. cd {export} && vercel deploy --prod",
        "  只放 .vercel/project.json，别的 gitignore 文件都不拷（supabase.md P3-6）",
    )


def render(passed: Passed) -> str:
    main = f"{passed.request.remote}/{BRANCH}"
    lines = (
        f"部署守卫通过：{passed.request.target}",
        f"- 检出：{passed.root}",
        f"- 提交：{passed.commit}（等于刚取回的 {main}）",
        *_notes(passed),
        "",
        f"部署并核对之后，把这一行追加进 {PROGRESS}，随提交推到 {main}：",
        f"- `{record_text(passed)}`",
        "",
        *next_steps(passed),
    )
    return "\n".join(lines)


def _sqlstate(exc: BaseException) -> str | None:
    seen, pending = set(), [exc]
    while pending:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        state = getattr(current, "sqlstate", None) or getattr(current, "pgcode", None)
        if isinstance(state, str) and state:
            return state
        pending.extend((current.__cause__, current.__context__))
    return None


def describe(exc: BaseException) -> str:
    """An error's class and SQLSTATE; the guard's own CheckFailed keeps its message."""
    name = type(exc).__name__
    text = f"{name}：{exc}" if isinstance(exc, CheckFailed) else name
    state = _sqlstate(exc)
    if state is None:
        return text
    hint = SQLSTATE_HINTS.get(state)
    return f"{text}（SQLSTATE {state}）" + (f"：{hint}" if hint else "")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    remote_help = "部署来源的远端（默认 ggwork；本仓库的 origin 是上游 DeerFlow）"
    common.add_argument("--remote", default=DEFAULT_REMOTE, help=remote_help)
    repo_help = "要部署的检出（默认本脚本所在的检出）"
    common.add_argument("--repo", type=Path, default=DEFAULT_REPO, help=repo_help)
    first_help = "progress.md 里还没有这个目标的守卫记录时才用；已有记录时会被拒绝"
    common.add_argument("--first-record", action="store_true", help=first_help)
    description = "选剧工作台部署守卫（计划 D41）：部署前逐项核对，自己不部署。"
    parser = argparse.ArgumentParser(
        prog="pick-deploy-guard.py", description=description
    )
    modes = parser.add_subparsers(dest="mode", required=True)
    modes.add_parser("gateway", parents=[common], help="部署 gateway 之前")
    cron = modes.add_parser("cron", parents=[common], help="部署观测 cron 之前")
    cron.add_argument("service", choices=CRON_SERVICES)
    frontend = modes.add_parser(FRONTEND, parents=[common], help="部署前端之前")
    out_help = "导出到这个新目录（须不存在；默认在临时目录里新建）"
    frontend.add_argument("--out", type=Path, help=out_help)
    return parser


def _request(args: argparse.Namespace) -> Request:
    service, out = getattr(args, "service", None), getattr(args, "out", None)
    return Request(args.mode, args.remote, args.first_record, service, out)


def main(
    argv: Sequence[str] | None = None,
    *,
    repo: Repo | None = None,
    read_prod: ProdReader | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    now: Callable[[], datetime] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    request, out, err = _request(args), out or sys.stdout, err or sys.stderr
    env = os.environ if environ is None else environ
    sources = Sources(
        read_prod or read_prod_state, env, now or (lambda: datetime.now(UTC))
    )
    try:
        checkout = GitRepo.open(args.repo) if repo is None else repo
        passed = run(request, checkout, sources)
    except Refused as exc:
        print(f"部署守卫拒绝（{request.target}）：{exc}", file=err)
        return EXIT_REFUSED
    except KeyboardInterrupt:
        print(f"部署守卫被中断（{request.target}）；重跑是安全的", file=err)
        return EXIT_INTERRUPTED
    except (
        Exception
    ) as exc:  # it may quote a DSN or a remote URL: only its class is shown
        print(f"部署守卫出错（{request.target}）：{describe(exc)}", file=err)
        return EXIT_FAILED
    print(render(passed), file=out)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
