"""Shared by test_deploy_guard.py and test_deploy_guard_real.py: the guard loaded by path, a fake checkout, git and database.

The fake checkout is a directory holding what the guard reads from a real one: the source migration chain copied into
both the source and the managed-copy places (in sync, whatever the managed copy of the branch under test holds),
observe/versions.py, docs/pick-workbench/progress.md with the given guard records, and the cron Railway configs.
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

SHA_OLD, SHA_PREV, SHA_MAIN, SHA_NEW, SHA_SIDE = ("1" * 40, "2" * 40, "3" * 40, "4" * 40, "5" * 40)
HISTORY = (SHA_OLD, SHA_PREV, SHA_MAIN, SHA_NEW)  # linear: each commit descends from those before it
PASSWORD = "S3cr3t-Pw0rd"
HOST = "db.secret-host.invalid"
SECRET_DSN = f"postgresql+asyncpg://pick_observer.projref:{PASSWORD}@{HOST}:5432/postgres"
NOW = datetime(2026, 9, 26, 20, 45, 12, tzinfo=UTC)
MODULE_NAME = "pick_deploy_guard"


def _repository_root() -> Path:
    """The checkout holding both scripts/ and the source extension, wherever this copy of the tests sits."""
    here = Path(__file__).resolve()
    return next(parent for parent in here.parents if (parent / "scripts").is_dir() and (parent / SOURCE_VERSIONS).is_dir())


ROOT = _repository_root()


@functools.cache
def load_guard():
    """scripts/pick-deploy-guard.py as a module (its file name is not an identifier)."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, ROOT / "scripts" / "pick-deploy-guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module  # dataclasses look their module up while the file executes
    spec.loader.exec_module(module)
    return module


def record_line(target: str, commit: str, at: str = "2026-09-25T20:45:00Z") -> str:
    return f"- `pick-deploy-guard target={target} commit={commit} prod_head=0007 chain_head=0007 at={at}`"


def migration(revision: str, down: str | None) -> str:
    return f'"""Synthetic."""\n\nrevision = "{revision}"\ndown_revision = {down!r}\nbranch_labels = None\n'


def make_root(tmp_path: Path, *, records: tuple[str, ...] = (), services: tuple[str, ...] = ("trends", "gsc")) -> Path:
    root = tmp_path / "checkout"
    for place in (SOURCE_VERSIONS, MANAGED_VERSIONS):
        shutil.copytree(ROOT / SOURCE_VERSIONS, root / place, ignore=shutil.ignore_patterns("__pycache__"))
    for place in (SOURCE_OBSERVE_VERSIONS, MANAGED_OBSERVE_VERSIONS):
        (root / place).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / SOURCE_OBSERVE_VERSIONS, root / place)
    (root / PROGRESS).parent.mkdir(parents=True)
    (root / PROGRESS).write_text("# 当前状态\n\n" + "".join(f"{line}\n" for line in records), encoding="utf-8")
    for service in services:
        config = root / "deploy" / "pick-obs" / service / "railway.toml"
        config.parent.mkdir(parents=True)
        config.write_text('[deploy]\nrestartPolicyType = "NEVER"\n')
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
    role: str = "pick_observer"
    versions: tuple[str, ...] = ("0007",)
    error: BaseException | None = None
    calls: list = field(default_factory=list)

    def __call__(self, dsn, *, sslmode):
        self.calls.append((dsn, sslmode))
        if self.error is not None:
            raise self.error
        return load_guard().ProdState(role=self.role, versions=self.versions)


def dsn_file(directory: Path, content: str, mode: int = 0o600, name: str = "observer.dsn") -> str:
    path = directory / name
    path.write_text(content)
    path.chmod(mode)
    return str(path)


def dsn_environment(directory: Path, content: str = SECRET_DSN + "\n") -> dict[str, str]:
    return {"PICK_OBS_DSN_FILE": dsn_file(directory, content), "PGSSLMODE": "require"}


def run_guard(argv, repo, *, db=None, env=None):
    """(exit status, stdout, stderr) of one guard run at NOW."""
    out, err = io.StringIO(), io.StringIO()
    read_prod = FakeDb() if db is None else db
    code = load_guard().main(argv, repo=repo, read_prod=read_prod, environ=env or {}, out=out, err=err, now=lambda: NOW)
    return code, out.getvalue(), err.getvalue()
