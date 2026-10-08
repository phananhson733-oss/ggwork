"""Shared by test_deploy_guard.py and test_deploy_guard_real.py: the guard loaded by path, a fake checkout, git and database.

The fake checkout is a directory holding what the guard reads from a real one: a synthetic migration chain CHAIN in both
the source and the managed-copy places (in sync), an observe/versions.py naming MIN_HEAD, docs/pick-workbench/progress.md
with the given guard records, and the cron Railway configs. The chain is the fixture's own, never the repository's: a
migration another session merges (0008 and on) changes nothing here. make_real_root copies the repository's chain for
the one test about production as it is (the S3 seam with TR-12).
"""

import functools
import importlib.util
import io
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SOURCE_VERSIONS = Path("customizations/pick-workbench/ggwork_pick/migrations/versions")
MANAGED_VERSIONS = Path("backend/extensions/sources/ggwork-pick/ggwork_pick/migrations/versions")
SOURCE_OBSERVE_VERSIONS = Path("customizations/pick-workbench/ggwork_pick/observe/versions.py")
MANAGED_OBSERVE_VERSIONS = Path("backend/extensions/sources/ggwork-pick/ggwork_pick/observe/versions.py")
PROGRESS = Path("docs/pick-workbench/progress.md")

# The fixture's chain: seven revisions, the last one the oldest the observe tables exist in (like 0007 today).
CHAIN = tuple(f"{number:04d}" for number in range(1, 8))
CHAIN_HEAD = MIN_HEAD = CHAIN[-1]
BEFORE_MIN = CHAIN[CHAIN.index(MIN_HEAD) - 1]
NEW_REVISION = "0008"  # a revision the fixture's chain does not have
SPAN = f"{CHAIN[0]} → {CHAIN[-1]}"

SHA_OLD, SHA_PREV, SHA_MAIN, SHA_NEW, SHA_SIDE = ("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)
HISTORY = (SHA_OLD, SHA_PREV, SHA_MAIN, SHA_NEW)  # linear: each commit descends from those before it
PASSWORD = "S3cr3t-Pw0rd"
HOST = "db.secret-host.invalid"
SECRET_DSN = f"postgresql+asyncpg://pick_observer.projref:{PASSWORD}@{HOST}:5432/postgres"
TOKEN = "ghp_T0kenThatMustNotShow"
SHARED_URL = "https://github.com/phananhson733-oss/ggwork-deerflow.git"
NOW = datetime(2026, 9, 26, 20, 45, 12, tzinfo=UTC)
MODULE_NAME = "pick_deploy_guard"


def _repository_root() -> Path:
    """The checkout holding both scripts/ and the source extension, wherever this copy of the tests sits."""
    here = Path(__file__).resolve()
    return next(parent for parent in here.parents if (parent / "scripts").is_dir() and (parent / SOURCE_VERSIONS).is_dir())


ROOT = _repository_root()


@functools.cache
def load_guard():
    """scripts/pick-deploy-guard.py as a module (its file name is not an identifier).

    Run as a script, its directory is sys.path[0] and its helper module next to it imports; loaded by path it is not, so
    the directory goes at the end of sys.path, where it shadows nothing.
    """
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.append(scripts)
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ROOT / "scripts" / "pick-deploy-guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module  # dataclasses look their module up while the file executes
    spec.loader.exec_module(module)
    return module


def record_line(target: str, commit: str, at: str = "2026-09-25T20:45:00Z") -> str:
    return f"- `pick-deploy-guard target={target} commit={commit} prod_head={CHAIN_HEAD} chain_head={CHAIN_HEAD} at={at}`"


def migration(revision: str, down: str | None) -> str:
    return f'"""Synthetic."""\n\nrevision = "{revision}"\ndown_revision = {down!r}\nbranch_labels = None\n'


def _checkout(tmp_path: Path, records: tuple[str, ...], services: tuple[str, ...]) -> Path:
    root = tmp_path / "checkout"
    (root / PROGRESS).parent.mkdir(parents=True)
    (root / PROGRESS).write_text("# 当前状态\n\n" + "".join(f"{line}\n" for line in records), encoding="utf-8")
    for service in services:
        config = root / "deploy" / "pick-obs" / service / "railway.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[deploy]\nrestartPolicyType = "NEVER"\n')
    return root


def make_root(tmp_path: Path, *, records: tuple[str, ...] = (), services: tuple[str, ...] = ("trends", "gsc")) -> Path:
    root = _checkout(tmp_path, records, services)
    for place in (SOURCE_VERSIONS, MANAGED_VERSIONS):
        (root / place).mkdir(parents=True)
        for down, revision in zip((None, *CHAIN), CHAIN):
            (root / place / f"{revision}_synthetic.py").write_text(migration(revision, down))
    for place in (SOURCE_OBSERVE_VERSIONS, MANAGED_OBSERVE_VERSIONS):
        (root / place).parent.mkdir(parents=True, exist_ok=True)
        (root / place).write_text(f'"""Synthetic."""\n\nMIN_MIGRATION_HEAD = "{MIN_HEAD}"\n')
    return root


def make_real_root(tmp_path: Path, *, records: tuple[str, ...] = ()) -> Path:
    """A fake checkout shipping this repository's own chain and MIN_MIGRATION_HEAD (the source's, in both places)."""
    root = _checkout(tmp_path, records, ("trends", "gsc"))
    for place in (SOURCE_VERSIONS, MANAGED_VERSIONS):
        shutil.copytree(ROOT / SOURCE_VERSIONS, root / place, ignore=shutil.ignore_patterns("__pycache__"))
    for place in (SOURCE_OBSERVE_VERSIONS, MANAGED_OBSERVE_VERSIONS):
        (root / place).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / SOURCE_OBSERVE_VERSIONS, root / place)
    return root


def add_revision(root: Path, revision: str, down: str, *, places=(SOURCE_VERSIONS, MANAGED_VERSIONS)) -> None:
    for place in places:
        (root / place / f"{revision}_synthetic.py").write_text(migration(revision, down))


@dataclass
class FakeRepo:
    """What the guard asks git, answered from fields. `calls` records the questions in order."""

    root: Path
    head: str = SHA_MAIN
    remote: str = "ggwork"
    url: str = SHARED_URL  # what `git remote get-url <remote>` answers
    tracking_main: str | None = SHA_MAIN  # the remote-tracking ref before the fetch
    fetched_main: str = SHA_MAIN  # what the remote's main is now
    dirty: tuple[str, ...] = ()
    env_files: tuple[str, ...] = ()
    history: tuple[str, ...] = HISTORY
    fetch_error: Exception | None = None
    calls: list = field(default_factory=list)

    def status_entries(self):
        self.calls.append(("status",))
        return self.dirty

    def untracked_env_files(self):
        self.calls.append(("env",))
        return self.env_files

    def remote_url(self, remote):
        self.calls.append(("remote_url", remote))
        if remote != self.remote:
            raise load_guard().readers.GitFailed("remote", 2)
        return self.url

    def fetch(self, remote, branch):
        self.calls.append(("fetch", remote, branch))
        if self.fetch_error is not None:
            raise self.fetch_error
        if remote == self.remote:
            self.tracking_main = self.fetched_main

    def commit_of(self, ref):
        if ref == "HEAD":
            return self.head
        if ref == f"refs/remotes/{self.remote}/main":
            return self.tracking_main
        return ref if ref in (*self.history, SHA_SIDE) else None

    def is_ancestor(self, older, newer):
        self.calls.append(("is_ancestor", older, newer))
        return older in self.history and newer in self.history and self.history.index(older) <= self.history.index(newer)

    def archive(self, commit, path, dest):
        self.calls.append(("archive", commit, path))
        (dest / path).mkdir(parents=True)
        (dest / path / "package.json").write_text('{"name": "frontend"}')


@dataclass
class FakeDb:
    """read_prod_state's stand-in. versions=None: the role may not read the version table; observe_tables: whether the
    tables migration MIN_HEAD creates are there."""

    role: str = "pick_observer"
    versions: tuple[str, ...] | None = (CHAIN_HEAD,)
    observe_tables: bool = True
    error: BaseException | None = None
    calls: list = field(default_factory=list)

    def __call__(self, dsn, *, sslmode):
        self.calls.append((dsn, sslmode))
        if self.error is not None:
            raise self.error
        return load_guard().ProdState(role=self.role, versions=self.versions, observe_tables=self.observe_tables)


def dsn_file(directory: Path, content: str, mode: int = 0o600, name: str = "observer.dsn") -> str:
    path = directory / name
    path.write_text(content)
    path.chmod(mode)
    return str(path)


def dsn_environment(directory: Path, content: str = SECRET_DSN + "\n") -> dict[str, str]:
    return {"PICK_OBS_DSN_FILE": dsn_file(directory, content), "PGSSLMODE": "require"}


def run_guard(argv, repo, *, db=None, env=None, ssl_modes=None):
    """(exit status, stdout, stderr) of one guard run at NOW. ssl_modes: the PGSSLMODE values accepted (the guard's
    default when None); the local test cluster has no TLS, so the real-database tests pass {"disable"}."""
    out, err = io.StringIO(), io.StringIO()
    read_prod = FakeDb() if db is None else db
    extra = {} if ssl_modes is None else {"ssl_modes": ssl_modes}
    code = load_guard().main(argv, repo=repo, read_prod=read_prod, environ=env or {}, out=out, err=err, now=lambda: NOW, **extra)
    return code, out.getvalue(), err.getvalue()
