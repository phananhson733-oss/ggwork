"""TR-34: the deploy guard scripts/pick-deploy-guard.py (plan D41, section 10; design 3.8), with a fake git and a fake database.

Every deploy of the gateway, a cron or the frontend runs the guard first. It refuses a dirty tree or an untracked .env*,
a HEAD other than the main just fetched, a production commit on record that HEAD does not descend from, and (gateway,
cron) a production migration head this checkout's chain does not know, read as the observer from a mode-600 DSN file.
test_deploy_guard_real.py runs the same guard over real git repositories and PostgreSQL.
[反例 14]: rolling the gateway back below the production head, or deploying from a checkout production has moved past.
"""

import io
import os
from pathlib import Path

import pytest
from deploy_guard_fakes import (
    HOST,
    PASSWORD,
    ROOT,
    SECRET_DSN,
    SHA_MAIN,
    SHA_NEW,
    SHA_OLD,
    SHA_PREV,
    SHA_SIDE,
    SOURCE_OBSERVE_VERSIONS,
    SOURCE_VERSIONS,
    FakeDb,
    FakeRepo,
    add_revision,
    dsn_environment,
    dsn_file,
    load_guard,
    make_root,
    migration,
    record_line,
    run_guard,
)


@pytest.fixture(scope="module")
def guard():
    return load_guard()


@pytest.fixture
def dsn_env(tmp_path):
    return dsn_environment(tmp_path)


def test_refuses_dirty_tree(tmp_path, dsn_env):
    repo, db = FakeRepo(make_root(tmp_path), dirty=(" M backend/app/gateway/app.py", "?? notes.txt")), FakeDb()
    code, out, err = run_guard(["gateway", "--first-record"], repo, db=db, env=dsn_env)
    assert code == 2 and out == ""
    assert "工作区不干净" in err and "backend/app/gateway/app.py" in err
    assert not [call for call in repo.calls if call[0] == "fetch"] and db.calls == []  # local checks come first


def test_refuses_untracked_env_file(tmp_path, dsn_env):
    """Ignored files count too: a CLI that uploads the directory takes them along (supabase.md P3-6)."""
    repo, db = FakeRepo(make_root(tmp_path), env_files=("frontend/.env.local",)), FakeDb()
    code, _, err = run_guard(["gateway", "--first-record"], repo, db=db, env=dsn_env)
    assert code == 2 and "frontend/.env.local" in err and db.calls == []


@pytest.mark.parametrize(
    ("head", "tracking", "fetched"),
    [
        (SHA_MAIN, SHA_MAIN, SHA_NEW),  # main moved on since the last fetch: the stale ref alone would pass
        (SHA_SIDE, SHA_MAIN, SHA_MAIN),  # a local commit not on main
        (SHA_PREV, SHA_PREV, SHA_MAIN),  # an old checkout
    ],
)
def test_refuses_not_origin_main(tmp_path, dsn_env, head, tracking, fetched):
    repo = FakeRepo(make_root(tmp_path), head=head, tracking_main=tracking, fetched_main=fetched)
    db = FakeDb()
    code, _, err = run_guard(["gateway", "--first-record"], repo, db=db, env=dsn_env)
    assert code == 2 and "ggwork/main" in err and db.calls == []
    assert ("fetch", "ggwork", "main") in repo.calls


def test_remote_is_configurable(tmp_path, dsn_env):
    repo = FakeRepo(make_root(tmp_path), remote="origin")
    code, out, _ = run_guard(["gateway", "--first-record", "--remote", "origin"], repo, env=dsn_env)
    assert code == 0 and "origin/main" in out and ("fetch", "origin", "main") in repo.calls


def test_fetch_failure_is_an_error(guard, tmp_path, dsn_env):
    repo = FakeRepo(make_root(tmp_path), fetch_error=guard.GitFailed("fetch", 128))
    code, out, err = run_guard(["gateway", "--first-record"], repo, env=dsn_env)
    assert code == 1 and out == "" and "git fetch" in err


@pytest.mark.parametrize("head", ["0008", "0006a", "9999"])
def test_refuses_unknown_prod_head(tmp_path, dsn_env, head):
    """[反例 14] A checkout whose chain does not know the production head would fail to load the extension."""
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=FakeDb(versions=(head,)), env=dsn_env)
    assert code == 2 and out == ""
    assert f"生产迁移头 {head}" in err and "0001 → 0007" in err


def test_accepts_known_head(guard, tmp_path, dsn_env):
    repo, db = FakeRepo(make_root(tmp_path, records=(record_line("gateway", SHA_PREV),))), FakeDb(versions=("0007",))
    code, out, err = run_guard(["gateway"], repo, db=db, env=dsn_env)
    assert (code, err) == (0, "")
    line = f"pick-deploy-guard target=gateway commit={SHA_MAIN} prod_head=0007 chain_head=0007 at=2026-09-26T20:45:12Z"
    assert line in out and "railway up --detach" in out and "vercel" not in out
    assert [record.commit for record in guard.parse_records(out)] == [SHA_MAIN]  # the printed line reads back
    assert ("is_ancestor", SHA_PREV, SHA_MAIN) in repo.calls


def test_gateway_upgrade_reminds_to_redeploy_crons(tmp_path, dsn_env):
    code, out, _ = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=FakeDb(versions=("0006",)), env=dsn_env)
    assert code == 0 and "prod_head=0006 chain_head=0007" in out
    assert "从 0006 升到 0007" in out and "两个 cron" in out


def test_chain_is_read_from_the_files(guard, tmp_path, dsn_env):
    """Nothing hardcodes the chain: a revision file added to both copies makes its revision known."""
    root = make_root(tmp_path)
    add_revision(root, "0008", "0007")
    code, out, _ = run_guard(["gateway", "--first-record"], FakeRepo(root), db=FakeDb(versions=("0008",)), env=dsn_env)
    assert code == 0 and "chain_head=0008" in out
    assert guard.read_chain(root / SOURCE_VERSIONS) == ("0001", "0002", "0003", "0004", "0005", "0006", "0007", "0008")


def test_real_chain_reads_linear(guard):
    chain = guard.read_chain(ROOT / SOURCE_VERSIONS)
    assert chain[0] == "0001" and len(chain) == len(set(chain)) and "0007" in chain
    assert guard.read_constant(ROOT / SOURCE_OBSERVE_VERSIONS, "MIN_MIGRATION_HEAD") == "0007"


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        ({"a": ("0001", None), "b": ("0002", "0001"), "c": ("0003", "0001")}, "分叉"),
        ({"a": ("0001", None), "b": ("0001", None)}, "不止一次"),
        ({"a": ("0001", None), "b": ("0003", "0002")}, "不在链里"),
        ({"a": ("0001", None), "b": ("0002", None)}, "起点"),
    ],
)
def test_refuses_broken_chain(guard, tmp_path, files, reason):
    for name, (revision, down) in files.items():
        (tmp_path / f"{name}.py").write_text(migration(revision, down))
    with pytest.raises(guard.Refused, match=reason):
        guard.read_chain(tmp_path)


def test_refuses_merge_revision_and_missing_revision(guard, tmp_path):
    (tmp_path / "a.py").write_text('revision = "0001"\ndown_revision = ("0000", "0000b")\n')
    with pytest.raises(guard.Refused, match="合并点"):
        guard.read_chain(tmp_path)
    (tmp_path / "a.py").write_text("down_revision = None\n")
    with pytest.raises(guard.Refused, match="没有 revision"):
        guard.read_chain(tmp_path)


def test_annotated_revisions_are_read(guard, tmp_path):
    (tmp_path / "a.py").write_text('revision: str = "0001"\ndown_revision: str | None = None\n')
    (tmp_path / "b.py").write_text('revision: str = "0002"\ndown_revision: str | None = "0001"\n')
    assert guard.read_chain(tmp_path) == ("0001", "0002")


def test_refuses_source_and_managed_chain_mismatch(tmp_path, dsn_env):
    root = make_root(tmp_path)
    add_revision(root, "0008", "0007", places=(SOURCE_VERSIONS,))
    db = FakeDb()
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(root), db=db, env=dsn_env)
    assert code == 2 and "托管副本" in err and db.calls == []


@pytest.mark.parametrize(
    "recorded",
    [
        SHA_NEW,  # production already runs a descendant of HEAD: deploying HEAD rolls it back
        SHA_SIDE,  # production runs a commit main never had (deployed from a branch)
        "6" * 40,  # a commit this checkout does not have at all
    ],
)
def test_refuses_non_ancestor_prod_commit(tmp_path, dsn_env, recorded):
    """[反例 14] Rolling back is a revert commit on main, never a deploy from an older checkout (plan section 10)."""
    records = (record_line("gateway", SHA_OLD, "2026-09-24T20:45:00Z"), record_line("gateway", recorded))
    db = FakeDb()
    code, _, err = run_guard(["gateway"], FakeRepo(make_root(tmp_path, records=records)), db=db, env=dsn_env)
    assert code == 2 and recorded[:12] in err and "祖先" in err and db.calls == []


def test_latest_record_of_the_same_target_decides(tmp_path, dsn_env):
    records = (
        record_line("gateway", SHA_PREV, "2026-09-25T20:45:00Z"),
        record_line("gateway", SHA_SIDE, "2026-09-24T20:45:00Z"),  # older, though written later in the file
        record_line("cron:trends", SHA_NEW, "2026-09-26T01:00:00Z"),  # another target's record
        "- 部署守卫 `scripts/pick-deploy-guard.py` 的用法见手册。",  # prose naming the script is not a record
    )
    repo = FakeRepo(make_root(tmp_path, records=records))
    code, _, _ = run_guard(["gateway"], repo, env=dsn_env)
    assert code == 0 and [call for call in repo.calls if call[0] == "is_ancestor"] == [("is_ancestor", SHA_PREV, SHA_MAIN)]


def test_first_record_flag(tmp_path, dsn_env):
    code, _, err = run_guard(["gateway"], FakeRepo(make_root(tmp_path)), env=dsn_env)
    assert code == 2 and "--first-record" in err
    root = make_root(tmp_path / "second", records=(record_line("gateway", SHA_PREV),))
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(root), env=dsn_env)
    assert code == 2 and "已有" in err


@pytest.mark.parametrize(
    "line",
    [
        "- `pick-deploy-guard target=gateway commit=abc123 at=2026-09-25T20:45:00Z`",
        "- `pick-deploy-guard target=gateway commit=" + "a" * 40 + "`",
        "- `pick-deploy-guard target=gatewy commit=" + "a" * 40 + " at=2026-09-25T20:45:00Z`",
        "- `pick-deploy-guard target=gateway commit=" + "a" * 40 + " at=2026-09-25 20:45`",
    ],
)
def test_refuses_malformed_record(tmp_path, dsn_env, line):
    code, _, err = run_guard(["gateway"], FakeRepo(make_root(tmp_path, records=(line,))), env=dsn_env)
    assert code == 2 and "progress.md 第 3 行" in err


def test_cron_accepts_current_head(tmp_path, dsn_env):
    repo = FakeRepo(make_root(tmp_path, records=(record_line("cron:trends", SHA_PREV),)))
    code, out, _ = run_guard(["cron", "trends"], repo, env=dsn_env)
    assert code == 0 and f"target=cron:trends commit={SHA_MAIN}" in out
    assert "railway up --detach --service pick-obs-trends" in out


@pytest.mark.parametrize(("prod", "extra", "reason"), [("0006", (), "不早于 0007"), ("0007", ("0008",), "先用守卫部署 gateway")])
def test_cron_refuses_head_behind(tmp_path, dsn_env, prod, extra, reason):
    root = make_root(tmp_path)
    for revision in extra:
        add_revision(root, revision, "0007")
    code, _, err = run_guard(["cron", "gsc", "--first-record"], FakeRepo(root), db=FakeDb(versions=(prod,)), env=dsn_env)
    assert code == 2 and reason in err


def test_cron_requires_its_railway_config(tmp_path, dsn_env):
    db = FakeDb()
    repo = FakeRepo(make_root(tmp_path, services=("trends",)))
    code, _, err = run_guard(["cron", "gsc", "--first-record"], repo, db=db, env=dsn_env)
    assert code == 2 and "deploy/pick-obs/gsc/railway.toml" in err and db.calls == []


@pytest.mark.parametrize(
    ("role", "versions", "reason"),
    [("postgres", ("0007",), "不是 pick_observer"), ("pick_observer", (), "空的"), ("pick_observer", ("0006", "0007"), "多行")],
)
def test_refuses_odd_production_answers(tmp_path, dsn_env, role, versions, reason):
    db = FakeDb(role=role, versions=versions)
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=db, env=dsn_env)
    assert code == 2 and reason in err and role not in err.replace("pick_observer", "")


def test_observer_role_name_is_configurable(tmp_path, dsn_env):
    env = {**dsn_env, "PICK_OBS_OBSERVER_ROLE": "pick_observer_test"}
    code, _, _ = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=FakeDb(role="pick_observer_test"), env=env)
    assert code == 0


def test_dsn_reaches_the_reader_as_a_libpq_url(tmp_path, dsn_env):
    db = FakeDb()
    assert run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=db, env=dsn_env)[0] == 0
    assert db.calls == [(SECRET_DSN.replace("postgresql+asyncpg://", "postgresql://"), "require")]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda tmp: {"PGSSLMODE": "require"}, "PICK_OBS_DSN_FILE"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, SECRET_DSN)}, "PGSSLMODE"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, SECRET_DSN, 0o644), "PGSSLMODE": "require"}, "600"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, SECRET_DSN, 0o700), "PGSSLMODE": "require"}, "600"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": str(tmp / "missing"), "PGSSLMODE": "require"}, "不存在"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, ""), "PGSSLMODE": "require"}, "一行"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, SECRET_DSN + "\n" + SECRET_DSN), "PGSSLMODE": "require"}, "一行"),
        (lambda tmp: {"PICK_OBS_DSN_FILE": dsn_file(tmp, f"mysql://u:{PASSWORD}@h/db"), "PGSSLMODE": "require"}, "PostgreSQL"),
    ],
)
def test_refuses_unusable_dsn_file(tmp_path, setup, reason):
    env, db = setup(tmp_path), FakeDb()
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=db, env=env)
    assert code == 2 and reason in err and db.calls == []
    assert PASSWORD not in out + err


def test_refuses_dsn_symlink(tmp_path):
    link = tmp_path / "link"
    link.symlink_to(dsn_file(tmp_path, SECRET_DSN))
    env = {"PICK_OBS_DSN_FILE": str(link), "PGSSLMODE": "require"}
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), env=env)
    assert code == 2 and "符号链接" in err


def test_dsn_file_rule_matches_the_observe_package(guard):
    """'600' means the same here as for the state key and the GSC key (crypto.read_private_file)."""
    from ggwork_pick.observe.crypto import WIDER_THAN_600

    assert guard.WIDER_THAN_600 == WIDER_THAN_600


class _DbError(Exception):
    sqlstate = "42501"


@pytest.mark.parametrize("error", [RuntimeError(f"could not connect to {SECRET_DSN}"), _DbError(f'denied; host "{HOST}" password {PASSWORD}')])
def test_never_prints_dsn(tmp_path, dsn_env, error):
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=FakeDb(error=error), env=dsn_env)
    assert code == 1 and out == "" and type(error).__name__ in err
    for secret in (SECRET_DSN, PASSWORD, HOST, "projref"):
        assert secret not in err
    if isinstance(error, _DbError):
        assert "SQLSTATE 42501" in err and "regrant" in err


def test_never_prints_dsn_on_success(tmp_path, dsn_env):
    code, out, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), env=dsn_env)
    assert code == 0
    for secret in (SECRET_DSN, PASSWORD, HOST, "projref", dsn_env["PICK_OBS_DSN_FILE"]):
        assert secret not in out + err


def test_frontend_exports_main_and_prints_the_next_step(tmp_path):
    repo, db, out_dir = FakeRepo(make_root(tmp_path)), FakeDb(), tmp_path / "export"
    code, out, err = run_guard(["frontend", "--first-record", "--out", str(out_dir)], repo, db=db)
    assert (code, err) == (0, "")
    assert ("archive", SHA_MAIN, "frontend") in repo.calls and (out_dir / "frontend" / "package.json").is_file()
    assert out_dir.stat().st_mode & 0o777 == 0o700
    assert f"pick-deploy-guard target=frontend commit={SHA_MAIN} at=2026-09-26T20:45:12Z" in out
    assert "vercel deploy --prod" in out and ".vercel/project.json" in out and str(out_dir) in out
    assert db.calls == []  # the frontend reads no database


def test_frontend_default_export_is_a_fresh_directory(tmp_path, monkeypatch):
    exports = tmp_path / "exports"
    exports.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(exports))
    code, out, _ = run_guard(["frontend", "--first-record"], FakeRepo(make_root(tmp_path)))
    made = list(exports.iterdir())
    assert code == 0 and len(made) == 1 and made[0].name.startswith(f"pick-frontend-{SHA_MAIN[:12]}-")
    assert (made[0] / "frontend" / "package.json").is_file() and str(made[0]) in out


def test_frontend_refuses_an_existing_directory(tmp_path):
    (tmp_path / "export").mkdir()
    repo = FakeRepo(make_root(tmp_path))
    code, _, err = run_guard(["frontend", "--first-record", "--out", str(tmp_path / "export")], repo)
    assert code == 2 and "已存在" in err and not [call for call in repo.calls if call[0] == "archive"]


def test_frontend_checks_its_own_record(tmp_path):
    root = make_root(tmp_path, records=(record_line("frontend", SHA_NEW),))
    code, _, err = run_guard(["frontend", "--out", str(tmp_path / "export")], FakeRepo(root))
    assert code == 2 and "祖先" in err and not (tmp_path / "export").exists()


def test_usage_errors(guard, tmp_path):
    with pytest.raises(SystemExit) as exited:
        guard.main(["cron", "discovery"], repo=FakeRepo(make_root(tmp_path)), environ={}, err=io.StringIO())
    assert exited.value.code == 2
    code, _, err = run_guard(["gateway", "--first-record", "--remote=-upload-pack"], FakeRepo(make_root(tmp_path / "second")))
    assert code == 2 and "远端名" in err


def test_interrupt_exits_130(tmp_path, dsn_env):
    db = FakeDb(error=KeyboardInterrupt())
    assert run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=db, env=dsn_env)[0] == 130


def test_os_environ_is_the_default(guard, tmp_path, monkeypatch):
    monkeypatch.delenv("PICK_OBS_DSN_FILE", raising=False)
    out, err = io.StringIO(), io.StringIO()
    code = guard.main(["gateway", "--first-record"], repo=FakeRepo(make_root(tmp_path)), read_prod=FakeDb(), out=out, err=err)
    assert code == 2 and "PICK_OBS_DSN_FILE" in err.getvalue()


def test_default_repo_is_this_checkout(guard):
    assert guard.DEFAULT_REPO == ROOT and Path(guard.__file__).parent.name == "scripts"


def test_refuses_cycle_and_missing_migrations(guard, tmp_path):
    links = {"a": ("0001", None), "b": ("0002", "0003"), "c": ("0003", "0002")}
    for name, (revision, down) in links.items():
        (tmp_path / f"{name}.py").write_text(migration(revision, down))
    with pytest.raises(guard.Refused, match="有环"):
        guard.read_chain(tmp_path)
    with pytest.raises(guard.Refused, match="找不到迁移目录"):
        guard.read_chain(tmp_path / "missing")
    (tmp_path / "empty").mkdir()
    with pytest.raises(guard.Refused, match="没有迁移文件"):
        guard.read_chain(tmp_path / "empty")


def _fifo(path: Path) -> None:
    os.mkfifo(path, 0o600)  # O_NONBLOCK: refused at once, never waited on


def _directory(path: Path) -> None:
    path.mkdir(mode=0o700)


def _not_utf8(path: Path) -> None:
    path.write_bytes(b"postgresql://\xff\xfe@h/db")
    path.chmod(0o600)


def _too_big(path: Path) -> None:
    path.write_text("postgresql://" + "x" * (64 * 1024))
    path.chmod(0o600)


@pytest.mark.parametrize(("make", "reason"), [(_fifo, "不是普通文件"), (_directory, "不是普通文件"), (_not_utf8, "UTF-8"), (_too_big, "超过")])
def test_refuses_dsn_path_that_is_not_a_small_text_file(tmp_path, make, reason):
    make(tmp_path / "dsn")
    env, db = {"PICK_OBS_DSN_FILE": str(tmp_path / "dsn"), "PGSSLMODE": "require"}, FakeDb()
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(make_root(tmp_path)), db=db, env=env)
    assert code == 2 and reason in err and db.calls == []


def test_refuses_without_progress_md(tmp_path, dsn_env):
    root = make_root(tmp_path)
    (root / "docs" / "pick-workbench" / "progress.md").unlink()
    code, _, err = run_guard(["gateway", "--first-record"], FakeRepo(root), env=dsn_env)
    assert code == 2 and "找不到 docs/pick-workbench/progress.md" in err


def test_frontend_export_failures(tmp_path):
    code, _, err = run_guard(["frontend", "--first-record", "--out", str(tmp_path / "no" / "export")], FakeRepo(make_root(tmp_path)))
    assert code == 2 and "上级目录不存在" in err

    class EmptyArchive(FakeRepo):
        def archive(self, commit, path, dest):
            self.calls.append(("archive", commit, path))

    code, out, err = run_guard(["frontend", "--first-record", "--out", str(tmp_path / "export")], EmptyArchive(make_root(tmp_path / "second")))
    assert code == 1 and out == "" and "package.json" in err
