#!/usr/bin/env python3
"""Deploy guard for the pick workbench: run it before every gateway, cron or frontend
deploy (trends radar plan D41, section 10). The runbook is
docs/pick-workbench/observe-runbook/deploy-guard.md.

From the checkout to deploy, with the backend environment (psycopg, postgres extra):
    backend/.venv/bin/python scripts/pick-deploy-guard.py gateway [--first-record]
    backend/.venv/bin/python scripts/pick-deploy-guard.py cron {trends,gsc} [...]
    backend/.venv/bin/python scripts/pick-deploy-guard.py frontend [--out DIR] [...]
Common options: --remote (default ggwork: this repository's origin is upstream
DeerFlow), --repo (default: the checkout holding this script).

Every mode refuses a dirty tree or an untracked .env* file (ignored ones included), a
remote other than the shared repository, a HEAD other than the main just fetched from
it, and a HEAD that does not descend from the last production commit progress.md
records for the target: a rollback is a revert commit on main. gateway and cron then
read the production migration head as pick_observer (DSN in the mode-600 file
PICK_OBS_DSN_FILE, PGSSLMODE require or stricter) and refuse a head the chain this
checkout ships does not know. A cron also needs its deploy/pick-obs/<service>/
railway.toml and a production head equal to the chain's head, not before
MIN_MIGRATION_HEAD: a new migration reaches production through the gateway first (D5).
frontend exports `git archive <HEAD> frontend` into a new directory.

The guard never runs railway or vercel: it prints the next step, the commit and the line
to append to progress.md once the deploy is verified. No DSN, password, remote URL or
database message is ever printed; errors name their class and SQLSTATE.
Exit status: 0 passed, 1 a check could not run (git or the database failed), 2 refused,
130 interrupted.
"""

import argparse
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, TextIO

import _pick_deploy_guard_readers as readers
from _pick_deploy_guard_readers import (
    CheckFailed,
    GitRepo,
    ProdState,
    Refused,
    describe,
    read_chain,
    read_constant,
    read_dsn,
    read_prod_state,
    repository_of,
)

EXIT_OK, EXIT_FAILED, EXIT_REFUSED, EXIT_INTERRUPTED = 0, 1, 2, 130

DEFAULT_REPO = Path(__file__).resolve().parents[1]
DEFAULT_REMOTE = "ggwork"
# progress.md: the code lives in this repository's main. Only its path is compared.
SHARED_REPOSITORY = ("phananhson733-oss", "ggwork")
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

SSL_MODE_ENV = "PGSSLMODE"
# The guard reaches production over the internet. Only a caller of main() (a test
# against a local cluster) may widen this; the command line cannot.
SECURE_SSL_MODES = frozenset({"require", "verify-ca", "verify-full"})
SSL_RULE = "只接受 require、verify-ca、verify-full（守卫经公网连生产库）"
OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"  # the variable migration 0007 reads
DEFAULT_OBSERVER_ROLE = "pick_observer"  # TR-12's role
REGRANT = "python -m ggwork_pick.observe.admin regrant"

RECORD_PREFIX = "pick-deploy-guard"
RECORD_MARK = f"{RECORD_PREFIX} target="
STAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
SHOWN_ENTRIES = 10
NEXT = "下一步（守卫不执行）："
AT_ONCE = "守卫通过后立即部署；隔久了先重跑守卫（这段时间里另一会话可能已经部署）"
_REMOTE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_COMMIT = re.compile(r"[0-9a-f]{40}")
_TOKEN = re.compile(r"\s*([a-z_]+)=([^\s`]+)")


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
    def remote_url(self, remote: str) -> str: ...
    def fetch(self, remote: str, branch: str) -> None: ...
    def commit_of(self, ref: str) -> str | None: ...
    def is_ancestor(self, older: str, newer: str) -> bool: ...
    def archive(self, commit: str, path: str, dest: Path) -> None: ...


ProdReader = Callable[..., ProdState]


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


def _unreadable(role: str, observe_tables: bool) -> CheckFailed:
    """The role may not read the version table: the fix depends on the stage."""
    if observe_tables:
        return CheckFailed(
            f"{role} 读不了 {readers.VERSION_TABLE}，而生产已有观测表（不早于 "
            f"{MIN_HEAD_NAME}）：在 gateway 容器里补授权（{REGRANT}）后重跑守卫"
        )
    return CheckFailed(
        f"{role} 读不了 {readers.VERSION_TABLE}，而生产还没有观测表（早于 "
        f"{MIN_HEAD_NAME}，即 S3 之前）：这条 SELECT 应由 S2 的 bootstrap-observer.sql "
        "授予（TR-12 接缝），此时 regrant 也帮不上。按 deploy-guard.md「observer "
        "读不了版本表」以 postgres 身份补授后重跑守卫"
    )


def _sslmode(environ: Mapping[str, str], allowed: frozenset[str]) -> str:
    sslmode = environ.get(SSL_MODE_ENV, "").strip()
    if not sslmode:
        raise Refused(f"缺少环境变量 {SSL_MODE_ENV}：{SSL_RULE}")
    if sslmode not in allowed:
        raise Refused(f"{SSL_MODE_ENV} 的取值太弱或拼错了：{SSL_RULE}")
    return sslmode


def production_head(
    environ: Mapping[str, str], read_prod: ProdReader, ssl_modes: frozenset[str]
) -> str:
    dsn = read_dsn(environ)
    sslmode = _sslmode(environ, ssl_modes)
    role = environ.get(OBSERVER_ROLE_ENV, "").strip() or DEFAULT_OBSERVER_ROLE
    state = read_prod(dsn, sslmode=sslmode)
    if state.role != role:
        raise Refused(
            f"{readers.DSN_FILE_ENV} 连上的角色不是 {role}：守卫只用 observer 的 DSN"
        )
    if state.versions is None:
        raise _unreadable(role, state.observe_tables)
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
            "移走后再部署：Vercel CLI 会连 gitignore 的文件一起上传"
        )


def check_remote(repo: Repo, remote: str) -> None:
    """The remote is the shared repository: a fork's main is not the main both
    sessions deploy from. The URL may hold a token, so it is never shown."""
    if repository_of(repo.remote_url(remote)) != SHARED_REPOSITORY:
        shared = "/".join(SHARED_REPOSITORY)
        raise Refused(
            f"远端 {remote} 指向的不是 {shared}（只比较 URL 路径的最后两段，"
            "URL 本身不打印）：只从共享仓库的 main 部署"
        )


def verified_head(repo: Repo, remote: str) -> str:
    check_remote(repo, remote)
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
    ssl_modes: frozenset[str] = SECURE_SSL_MODES


def run(request: Request, repo: Repo, sources: Sources) -> Passed:
    """Every check, the local ones first: nothing is fetched for a dirty tree or a
    foreign remote, and the database is read only after every local check passed."""
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
    head = production_head(sources.environ, sources.read_prod, sources.ssl_modes)
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
    # The record line pushed after this deploy moves main: the crons follow from the
    # newest main, which the cron guard requires, not from this very commit.
    main = f"{passed.request.remote}/{BRANCH}"
    upgrade = (
        f"- 本次部署会把生产迁移头从 {old} 升到 {new}。gateway 上线并核对之后，"
        f"从最新的 {main} 经守卫重部署已经建好的 cron 服务（还没建的不用管；计划 D5）"
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
        f"  {AT_ONCE}",
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    remote_help = (
        "部署来源的远端，须指向共享仓库（默认 ggwork；origin 是上游 DeerFlow）"
    )
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


def _sources(
    read_prod: ProdReader | None,
    environ: Mapping[str, str],
    now: Callable[[], datetime] | None,
    ssl_modes: frozenset[str] | None,
) -> Sources:
    return Sources(
        read_prod=read_prod or read_prod_state,
        environ=environ,
        now=now or (lambda: datetime.now(UTC)),
        ssl_modes=SECURE_SSL_MODES if ssl_modes is None else ssl_modes,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    repo: Repo | None = None,
    read_prod: ProdReader | None = None,
    environ: Mapping[str, str] | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    now: Callable[[], datetime] | None = None,
    ssl_modes: frozenset[str] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    request, out, err = _request(args), out or sys.stdout, err or sys.stderr
    env = os.environ if environ is None else environ
    sources = _sources(read_prod, env, now, ssl_modes)
    try:
        checkout = GitRepo.open(args.repo) if repo is None else repo
        passed = run(request, checkout, sources)
    except Refused as exc:
        print(f"部署守卫拒绝（{request.target}）：{exc}", file=err)
        return EXIT_REFUSED
    except KeyboardInterrupt:
        print(f"部署守卫被中断（{request.target}）；重跑是安全的", file=err)
        return EXIT_INTERRUPTED
    except Exception as exc:  # it may quote a DSN or a remote URL: only its class
        print(f"部署守卫出错（{request.target}）：{describe(exc)}", file=err)
        return EXIT_FAILED
    print(render(passed), file=out)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
