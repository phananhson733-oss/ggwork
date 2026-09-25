"""What scripts/pick-deploy-guard.py reads, and how reading fails: git, the migration
files, the observer's DSN file and the production database. The rules that judge the
answers live in the script; this module sits next to it (sys.path[0] when it runs).

Nothing here prints. Exception messages never carry a DSN, a password, a database
message, a remote URL or git's stderr; describe() shows a foreign error by its class
and SQLSTATE only.
"""

import ast
import errno
import os
import re
import stat
import subprocess
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class Refused(Exception):
    """A check failed: do not deploy. The message never carries a secret."""


class CheckFailed(Exception):
    """A check could not run. The message names the step, never a secret."""


class GitFailed(CheckFailed):
    def __init__(self, command: str, status: int):
        super().__init__(f"git {command} 失败（退出码 {status}），手动执行它看原因")


# ---------------------------------------------------------------- git

GIT_TIMEOUT_SECONDS = 120
STATUS_ARGS = ("status", "--porcelain=v1", "-z", "--untracked-files=all")
# Every untracked .env*, ignored ones included (no --exclude-standard); a dependency's
# own files under node_modules or .venv are not ours.
ENV_FILES_ARGS = (
    *("ls-files", "--others", "-z", "--", ":(glob)**/.env*"),
    *(":(exclude,glob)**/node_modules/**", ":(exclude,glob)**/.venv/**"),
)


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

    def remote_url(self, remote: str) -> str:
        """The fetch URL, insteadOf expanded. It may hold a token: compare, never show."""
        url = _checked(self.root, "remote", "get-url", remote)
        return url.decode("utf-8", "replace").strip()

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


# A scheme URL up to its host (user and token included), or scp-like `user@host:`.
_URL_PREFIX = re.compile(r"[a-z][a-z0-9+.-]*://[^/]*|[^/:]*:", re.IGNORECASE)


def repository_of(url: str) -> tuple[str, ...]:
    """The last two path segments of a remote URL, lower-cased, without `.git`: owner
    and repository for https://[user[:token]@]host/owner/repo.git, ssh://git@host/...,
    git@host:owner/repo.git, or a local path ending in owner/repo.git."""
    prefix = _URL_PREFIX.match(url)
    path = url[prefix.end() :] if prefix else url
    last = tuple(part.lower() for part in path.strip().split("/") if part)[-2:]
    return (*last[:-1], last[-1].removesuffix(".git")) if last else ()


# ---------------------------------------------------------------- migration files

_MISSING = object()


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


# ---------------------------------------------------------------- the DSN file

DSN_FILE_ENV = "PICK_OBS_DSN_FILE"
MAX_DSN_BYTES = 64 * 1024
# ggwork_pick.observe.crypto's rule for private files: no exec bit, nothing for others.
WIDER_THAN_600 = 0o177
PRIVATE_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_POSTGRES_SCHEME = re.compile(r"postgres(?:ql)?(?:\+[a-z0-9_]+)?://", re.IGNORECASE)
_OPEN_REASONS = {
    errno.ENOENT: "不存在",
    errno.ELOOP: "是符号链接",
    errno.EACCES: "读不了",
}


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


# ---------------------------------------------------------------- the database

# The schema the host's tables live in (ggwork_pick.mirror.connection.SEARCH_PATH).
VERSION_SCHEMA = "deerflow"
VERSION_TABLE = f"{VERSION_SCHEMA}.ggwp_alembic_version"
VERSION_QUERY = f"SELECT version_num FROM {VERSION_TABLE} ORDER BY version_num"
# The observe tables exist from migration MIN_MIGRATION_HEAD on (observe/versions.py).
# Any role may read the catalog: it tells production before that migration from
# production after it when the version table itself may not be read.
OBSERVE_TABLES = "ggwp\\_obs\\_%"
# CASE keeps the table lookup behind the schema check: without USAGE on the schema it
# would raise 42501 instead of answering false. Constants only, nothing from outside.
PROBE_QUERY = (
    "SELECT current_user,"
    f" CASE WHEN has_schema_privilege('{VERSION_SCHEMA}', 'USAGE')"
    f" THEN has_table_privilege('{VERSION_TABLE}', 'SELECT') ELSE false END,"
    " EXISTS (SELECT FROM pg_catalog.pg_tables"
    f" WHERE schemaname = '{VERSION_SCHEMA}' AND tablename LIKE '{OBSERVE_TABLES}')"
)
APPLICATION_NAME = "ggwp-deploy-guard"
CONNECT_TIMEOUT_SECONDS = 15
# A gateway migrating right now holds the version table: wait no longer than this.
STATEMENT_TIMEOUT = "15s"


@dataclass(frozen=True)
class ProdState:
    role: str
    versions: tuple[str, ...] | None  # None: the role may not read the version table
    observe_tables: bool


def read_prod_state(dsn: str, *, sslmode: str) -> ProdState:
    """The role the DSN logs in as, whether it may read the version table and its
    rows, and whether the observe tables exist: one READ ONLY transaction, rolled
    back."""
    import psycopg  # the backend environment's postgres extra; gateway and cron only

    options = {"connect_timeout": CONNECT_TIMEOUT_SECONDS, "sslmode": sslmode}
    with psycopg.connect(dsn, application_name=APPLICATION_NAME, **options) as conn:
        conn.read_only = True
        conn.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'")
        role, readable, observe_tables = conn.execute(PROBE_QUERY).fetchone()
        rows = conn.execute(VERSION_QUERY) if readable else None
        versions = None if rows is None else tuple(row[0] for row in rows)
        conn.rollback()
    return ProdState(role=role, versions=versions, observe_tables=observe_tables)


SQLSTATE_HINTS = {
    "42501": (
        "observer 缺权限（库的 CONNECT、deerflow 的 USAGE 等）：生产还没有观测表"
        "（早于 MIN_MIGRATION_HEAD）时核对 S2 的 bootstrap-observer.sql（TR-12）；"
        "之后在 gateway 里跑 regrant"
    ),
    "42P01": "库里没有 deerflow.ggwp_alembic_version：核对 DSN 连的是不是生产库",
    "3F000": "库里没有 deerflow schema：核对 DSN 连的是不是生产库",
    "28P01": "observer 的口令不对：核对 DSN 文件",
    "57014": (
        f"读版本表超过 {STATEMENT_TIMEOUT}：gateway 可能正在迁移、锁着版本表，"
        "等它部署完再重跑守卫"
    ),
}


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
