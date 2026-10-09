"""How the lark_cli tool runs lark-cli (docs/pick-workbench/lark-personal-auth.md section 3.3): an empty scratch
directory, a listed environment, a private copy of the user's credentials, the image's unprivileged user."""

import asyncio
import contextvars
import os
import stat
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.integrations import lark_cli

from ggwork_pick import lark_credentials, lark_runner
from ggwork_pick.lark_policy import LarkRefused

SECRETS = {"PICK_DATABASE_URL": "postgresql://u:secret@db/x", "AZURE_OPENAI_API_KEY": "sk-secret"}
ALLOWED_ENV = {
    "PATH",
    "LANG",
    "LC_ALL",
    "HOME",
    "TMPDIR",
    "LARKSUITE_CLI_CONFIG_DIR",
    "LARKSUITE_CLI_DATA_DIR",
    "LARKSUITE_CLI_NO_UPDATE_NOTIFIER",
    "LARKSUITE_CLI_NO_SKILLS_NOTIFIER",
}


def test_feedback_export_reads_bounded_artifacts_before_scratch_cleanup(monkeypatch):
    def export(config, _data):
        work = config.parent / "work"
        (work / "feedback.ndjson").write_text('{"record_id":"synthetic"}\n', encoding="utf-8")
        (work / "feedback.manifest.json").write_text('{"records_count":1,"has_more":false}', encoding="utf-8")

    fake = FakeCli(stdout='{"ok":true}', action=export)
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_args, **_kwargs: "read")
    _user_tree()
    result = lark_runner.run_feedback_export("alice", "tblHeWrgRPNshRdE", ("fldSynthetic",), 0)
    assert result.records == '{"record_id":"synthetic"}\n'
    assert '"records_count":1' in result.manifest
    assert result.completed.exit_code == 0
    (call,) = fake.calls
    assert "--as" in call.args and call.args[-1] == "user"
    assert "--view-id" not in call.args
    assert call.args[call.args.index("--limit") + 1] == "2000"
    assert not Path(call.kwargs["cwd"]).exists()


@pytest.mark.parametrize("kind", ["sheet", "base"])
def test_catalog_export_keeps_large_artifacts_and_reaps_real_descendants(monkeypatch, tmp_path, kind):
    """Native exports retain their larger limit under the completion process runner."""
    ready, survived, scratch_record = (tmp_path / name for name in ("child-ready", "survived", "scratch"))
    child = (
        "import os, time; from pathlib import Path; os.close(1); os.close(2); "
        f"Path({str(ready)!r}).write_text('ready'); time.sleep(1); Path({str(survived)!r}).write_text('late')"
    )
    binary = tmp_path / "synthetic-catalog-cli"
    binary.write_text(
        f"#!{sys.executable}\nimport subprocess, sys, time\nfrom pathlib import Path\n"
        f"Path({str(scratch_record)!r}).write_text(str(Path.cwd().parent))\n"
        "name = 'source.json' if sys.argv[1] == 'sheets' else 'source.ndjson'\n"
        "Path(name).write_text('x' * (9 * 1024 * 1024))\n"
        "if name.endswith('ndjson'): Path('source.ndjson.manifest.json').write_text('{}')\n"
        f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
        f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
        "print('{}')\n",
        encoding="utf-8",
    )
    binary.chmod(0o700)
    lark_runner._BINARY_CACHE["path"] = str(binary)
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_args, **_kwargs: "read")
    _user_tree()
    token, table = ("RRBAszuhOhNM8StMqRVcGknSnyf", "7ba1a7") if kind == "sheet" else ("OtnsbnRnwaLmnVsJByscTkFMntd", "tbl5Kzrhuz9B7LTE")
    result = lark_runner.run_catalog_export("alice", kind, token, table)
    assert result.completed.exit_code == 0 and not result.completed.truncated
    assert len(result.files["source.json" if kind == "sheet" else "source.ndjson"]) == 9 * 1024 * 1024
    if kind == "base":
        assert result.files["source.manifest.json"] == "{}"
    assert not Path(scratch_record.read_text()).exists()
    time.sleep(1.1)
    assert not survived.exists(), "export returned with an owned descendant still running"


def test_feedback_export_rejects_symlinks_without_reading_target(monkeypatch, tmp_path):
    private = tmp_path / "private"
    private.write_text("not an export", encoding="utf-8")

    def export(config, _data):
        work = config.parent / "work"
        (work / "feedback.ndjson").symlink_to(private)
        (work / "feedback.manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(lark_runner, "_run_process", FakeCli(stdout='{"ok":true}', action=export))
    monkeypatch.setattr(lark_runner, "command_risk", lambda *_args, **_kwargs: "read")
    _user_tree()
    with pytest.raises(lark_runner.LarkUnavailable):
        lark_runner.run_feedback_export("alice", "tblHeWrgRPNshRdE", ("fldSynthetic",), 0)


@pytest.mark.parametrize("table,fields,offset", [("tblForeign", ("fldA",), 0), ("tblHeWrgRPNshRdE", ("@secret",), 0), ("tblHeWrgRPNshRdE", ("fldA",), -1)])
def test_feedback_export_cannot_read_arbitrary_tables_or_paths(table, fields, offset):
    with pytest.raises(ValueError):
        lark_runner.run_feedback_export("alice", table, fields, offset)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(paths_module, "_paths", Paths(base_dir=tmp_path / "home"))
    monkeypatch.delenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, raising=False)
    monkeypatch.delenv(lark_runner.RUN_AS_ENV, raising=False)
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    lark_runner._RISK_CACHE.clear()
    lark_runner._BINARY_CACHE.clear()
    lark_runner._BINARY_CACHE["path"] = "/usr/local/bin/lark-cli"
    yield
    lark_runner._RISK_CACHE.clear()
    lark_runner._BINARY_CACHE.clear()


class FakeCli:
    """_run_process stand-in: records each call and lets a test act inside the scratch tree like lark-cli would."""

    def __init__(self, *, stdout="{}", exit_code=0, action=None):
        self.calls = []
        self.stdout = stdout
        self.exit_code = exit_code
        self.action = action

    def __call__(self, args, **kwargs):
        env = kwargs["env"]
        self.calls.append(
            SimpleNamespace(
                args=list(args),
                kwargs=kwargs,
                cwd_entries=sorted(os.listdir(kwargs["cwd"])),
                config_entries=sorted(os.listdir(env["LARKSUITE_CLI_CONFIG_DIR"])),
                env=dict(env),
            )
        )
        if self.action is not None:
            self.action(Path(env["LARKSUITE_CLI_CONFIG_DIR"]), Path(env["LARKSUITE_CLI_DATA_DIR"]))
        return subprocess.CompletedProcess(args, self.exit_code, self.stdout, "")


def _user_tree(user_id="alice"):
    lark_cli.ensure_lark_cli_credential_tree(user_id)
    config, data = lark_cli.lark_cli_config_dir(user_id), lark_cli.lark_cli_data_dir(user_id)
    (config / "config.json").write_text('{"apps": [{"appId": "cli_x", "appSecret": "s"}]}', encoding="utf-8")
    (data / "token.json").write_text('{"access": "old"}', encoding="utf-8")
    lark_cli.ensure_lark_cli_credential_tree(user_id)
    return config, data


def test_the_child_environment_holds_only_listed_variables(monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    (call,) = fake.calls
    assert set(call.env) <= ALLOWED_ENV | set(lark_runner.PASSTHROUGH_ENV)
    assert not set(SECRETS) & set(call.env)
    assert call.env["PATH"] == lark_cli.LARK_CLI_MINIMAL_PATH


@pytest.mark.parametrize("name", ["https_proxy", "http_proxy", "no_proxy", "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY"])
def test_proxy_settings_reach_lark_cli_in_either_case(monkeypatch, name):
    """09-29 review: the Gateway's own lark-cli calls pass the lowercase forms too (lark_cli._LARK_CLI_PASSTHROUGH_ENV)."""
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    monkeypatch.setenv(name, "http://proxy.internal:3128")
    _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    (call,) = fake.calls
    assert call.env[name] == "http://proxy.internal:3128"


def test_a_user_run_uses_a_private_copy_in_an_empty_scratch_directory(monkeypatch):
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    config, data = _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    (call,) = fake.calls
    assert call.args == ["/usr/local/bin/lark-cli", "docs", "+fetch", "--doc", "AbC"]
    assert call.cwd_entries == []
    scratch = Path(call.kwargs["cwd"]).parent
    for name in ("LARKSUITE_CLI_CONFIG_DIR", "LARKSUITE_CLI_DATA_DIR", "HOME", "TMPDIR"):
        assert Path(call.env[name]).parent == scratch
    assert Path(call.env["LARKSUITE_CLI_CONFIG_DIR"]) != config
    assert call.kwargs["stdin"] is subprocess.DEVNULL
    assert call.kwargs.get("shell", False) is False
    assert not scratch.exists()


def test_refreshed_tokens_and_new_cache_files_are_kept(monkeypatch):
    def refresh(config_dir, data_dir):
        assert (data_dir / "token.json").read_text(encoding="utf-8") == '{"access": "old"}'
        (data_dir / "token.json").write_text('{"access": "new"}', encoding="utf-8")
        (config_dir / "cache").mkdir()
        (config_dir / "cache" / "meta").write_text("m", encoding="utf-8")

    monkeypatch.setattr(lark_runner, "_run_process", FakeCli(action=refresh))
    config, data = _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    assert (data / "token.json").read_text(encoding="utf-8") == '{"access": "new"}'
    assert (config / "cache" / "meta").read_text(encoding="utf-8") == "m"
    assert stat.S_IMODE((data / "token.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((config / "cache").stat().st_mode) == 0o700


def test_unchanged_credentials_are_left_alone(monkeypatch):
    monkeypatch.setattr(lark_runner, "_run_process", FakeCli())
    _, data = _user_tree()
    token = data / "token.json"
    os.utime(token, (1_000_000, 1_000_000))

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    assert token.stat().st_mtime == 1_000_000


def test_a_symlink_left_in_the_scratch_tree_keeps_the_old_credentials(monkeypatch, tmp_path):
    outside = tmp_path / "gateway-secret"
    outside.write_text("secret", encoding="utf-8")

    def plant(_config_dir, data_dir):
        (data_dir / "token.json").unlink()
        (data_dir / "token.json").symlink_to(outside)

    monkeypatch.setattr(lark_runner, "_run_process", FakeCli(action=plant))
    _, data = _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    token = data / "token.json"
    assert not token.is_symlink() and token.read_text(encoding="utf-8") == '{"access": "old"}'


def test_a_user_run_takes_its_own_lock_before_the_shared_slot_both_bounded_by_the_run(monkeypatch):
    held = []

    @contextmanager
    def lock(user_id, *, deadline):
        held.append((user_id, deadline))
        yield

    @contextmanager
    def slot(deadline):
        held.append(("slot", deadline))
        yield

    fake = FakeCli(action=lambda *_: held.append("ran"))
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    monkeypatch.setattr(lark_cli, "lark_credential_lock", lock)
    monkeypatch.setattr(lark_runner, "_slot", slot)
    _user_tree()

    started = time.monotonic()
    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"), timeout=5)

    (user, deadline), (name, slot_deadline), ran = held
    assert (user, name, ran) == ("alice", "slot", "ran")
    assert deadline == slot_deadline and started + 5 <= deadline < started + 6


def test_an_authorization_in_progress_holds_up_only_that_user(monkeypatch):
    """09-29 review: a user's authorization flow holds their credential lock for up to 45 seconds. Their own command
    gives up when its time is up; nobody else's command waits behind it."""
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    _user_tree("alice")
    _user_tree("bob")
    authorizing, finished = threading.Event(), threading.Event()
    outcome = {}

    def authorize():
        with lark_cli.lark_credential_lock("alice"):
            authorizing.set()
            finished.wait(3)

    def alice():
        try:
            lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"), timeout=1.5)
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            outcome["alice"] = exc

    holder = threading.Thread(target=authorize, daemon=True)
    holder.start()
    assert authorizing.wait(5)
    waiting = threading.Thread(target=alice, daemon=True)
    waiting.start()
    time.sleep(0.2)
    lark_runner.run_for_user("bob", ("docs", "+fetch", "--doc", "AbC"), timeout=1)
    lark_runner.run_guide(("docs", "--help"), timeout=1)
    waiting.join(5)
    finished.set()
    holder.join(5)

    assert isinstance(outcome.get("alice"), lark_runner.LarkBusy) and lark_runner.CREDENTIALS_BUSY in str(outcome["alice"])
    assert len(fake.calls) == 2


@pytest.mark.parametrize("run", [lambda: lark_runner.run_guide(("docs", "--help")), lambda: lark_runner.run_for_user("alice", ("docs", "+fetch"))])
def test_a_run_lock_that_cannot_be_opened_is_unavailable(monkeypatch, run):
    monkeypatch.setattr(lark_runner, "_run_process", FakeCli())
    _user_tree()
    (paths_module.get_paths().base_dir / lark_runner.RUN_LOCK_FILE).mkdir(parents=True)
    with pytest.raises(lark_runner.LarkUnavailable):
        run()


@pytest.mark.asyncio
async def test_lark_work_runs_on_its_own_threads():
    names = await asyncio.gather(*(lark_runner.in_lark_thread(lambda: threading.current_thread().name) for _ in range(3)))
    assert all(name.startswith(lark_runner.THREAD_NAME_PREFIX) for name in names)


@pytest.mark.asyncio
async def test_queued_lark_work_waits_in_the_queue_not_on_threads():
    """09-29 review: each queued command used to park a thread of the default pool the whole Gateway shares."""
    release, guard = threading.Event(), threading.Lock()
    counts = {"running": 0, "peak": 0}

    def work():
        with guard:
            counts["running"] += 1
            counts["peak"] = max(counts["peak"], counts["running"])
        release.wait(5)
        with guard:
            counts["running"] -= 1

    tasks = [asyncio.ensure_future(lark_runner.in_lark_thread(work)) for _ in range(lark_runner.MAX_WORKER_THREADS + 3)]
    await asyncio.sleep(0.3)
    assert counts["peak"] == lark_runner.MAX_WORKER_THREADS
    release.set()
    await asyncio.gather(*tasks)


@pytest.mark.asyncio
async def test_a_call_cancelled_while_queued_never_runs():
    release, ran = threading.Event(), []
    blockers = [asyncio.ensure_future(lark_runner.in_lark_thread(release.wait, 5)) for _ in range(lark_runner.MAX_WORKER_THREADS)]
    await asyncio.sleep(0.2)
    queued = asyncio.ensure_future(lark_runner.in_lark_thread(ran.append, "late"))
    await asyncio.sleep(0.1)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    release.set()
    await asyncio.gather(*blockers)
    await asyncio.gather(lark_runner.in_lark_thread(time.sleep, 0.05))
    assert ran == []


@pytest.mark.asyncio
async def test_lark_work_sees_the_callers_context():
    request = contextvars.ContextVar("request")
    request.set("alice-turn")
    assert await lark_runner.in_lark_thread(request.get) == "alice-turn"


def test_guides_run_without_the_users_credentials(monkeypatch):
    fake = FakeCli(stdout="# lark-doc")
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    config, _ = _user_tree()

    completed = lark_runner.run_guide(("skills", "read", "lark-doc"))

    assert completed.stdout == "# lark-doc"
    (call,) = fake.calls
    assert call.config_entries == []
    assert Path(call.env["LARKSUITE_CLI_CONFIG_DIR"]) != config


def test_a_timeout_is_reported_as_exit_124(monkeypatch):
    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(lark_runner, "_run_process", slow)
    _user_tree()

    completed = lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    assert completed.exit_code == 124
    assert "超时" in completed.stderr


def test_output_is_decoded_leniently_and_capped(monkeypatch):
    fake = FakeCli(stdout="x" * (lark_runner.MAX_OUTPUT_CHARS + 10))
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    _user_tree()

    completed = lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    assert completed.truncated and len(completed.stdout) == lark_runner.MAX_OUTPUT_CHARS


def _python(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.mark.parametrize("char", ["x", "é", "你", "😀"])
def test_a_flood_of_output_is_kept_to_a_bounded_prefix(tmp_path, char):
    """09-29 review: output used to be read whole into memory before it was cut to MAX_OUTPUT_CHARS."""
    flood = 3 * lark_runner.MAX_OUTPUT_BYTES
    code = f"import sys; sys.stdout.write({char!r} * {flood}); sys.stdout.flush(); sys.stderr.write('e' * {flood}); sys.exit(3)"

    result = lark_runner._run_process(_python(code), cwd=tmp_path, timeout=30, start_new_session=True, stdin=subprocess.DEVNULL)

    assert result.returncode == 3
    for text in (result.stdout, result.stderr):
        assert lark_runner.MAX_OUTPUT_CHARS < len(text) and len(text.encode("utf-8", errors="replace")) <= lark_runner.MAX_OUTPUT_BYTES + 3


def test_a_capped_run_reports_the_real_exit_code_and_is_marked_truncated(monkeypatch, tmp_path):
    code = f"import sys; sys.stdout.write('你' * {3 * lark_runner.MAX_OUTPUT_BYTES})"
    with lark_runner._scratch() as scratch:
        completed = lark_runner._execute(sys.executable, ("-c", code), scratch, None, time.monotonic() + 30)
    assert completed.exit_code == 0 and completed.truncated
    assert len(completed.stdout) == lark_runner.MAX_OUTPUT_CHARS and set(completed.stdout) == {"你"}


def test_short_output_is_kept_whole(tmp_path):
    result = lark_runner._run_process(
        _python("import sys; print('é 你 😀'); print('warn', file=sys.stderr)"), cwd=tmp_path, timeout=30, start_new_session=True, stdin=subprocess.DEVNULL
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "é 你 😀\n", "warn\n")


def test_invalid_utf8_is_replaced_not_raised(tmp_path):
    result = lark_runner._run_process(
        _python("import sys; sys.stdout.buffer.write(b'ok \\xff\\xfe end')"), cwd=tmp_path, timeout=30, start_new_session=True, stdin=subprocess.DEVNULL
    )
    assert result.stdout == "ok \ufffd\ufffd end"


def test_a_timeout_kills_the_whole_process_group(tmp_path):
    marker = tmp_path / "survivor"
    # The child starts a grandchild that would outlive it and keep the pipes open.
    grandchild = f"import time; time.sleep(2); open({str(marker)!r}, 'w').close()"
    code = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(30)"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        lark_runner._run_process(_python(code), cwd=tmp_path, timeout=0.5, start_new_session=True, stdin=subprocess.DEVNULL)
    assert time.monotonic() - started < 5
    time.sleep(2.5)
    assert not marker.exists()


class Owner:
    def __init__(self):
        self.chowned = []

    def __call__(self, path, uid, gid, *, follow_symlinks=True):
        assert follow_symlinks is False
        self.chowned.append((Path(path), uid, gid))


def _as_root(monkeypatch):
    monkeypatch.setenv(lark_runner.RUN_AS_ENV, "larkrun")
    monkeypatch.setattr(lark_runner.pwd, "getpwnam", lambda name: SimpleNamespace(pw_uid=990, pw_gid=991) if name == "larkrun" else None)
    monkeypatch.setattr(lark_runner.os, "geteuid", lambda: 0)
    home = paths_module.get_paths().base_dir
    home.mkdir(parents=True, exist_ok=True)
    home.chmod(0o750)
    owner = Owner()
    monkeypatch.setattr(lark_runner.os, "chown", owner)
    return owner


def test_the_image_user_runs_lark_cli_with_its_own_group_and_no_others(monkeypatch):
    owner = _as_root(monkeypatch)
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    config, _ = _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    (call,) = fake.calls
    assert (call.kwargs["user"], call.kwargs["group"], call.kwargs["extra_groups"], call.kwargs["umask"]) == (990, 991, [], 0o077)
    scratch = Path(call.kwargs["cwd"]).parent
    assert owner.chowned and all(path == scratch or scratch in path.parents for path, _, _ in owner.chowned)
    assert {(uid, gid) for _, uid, gid in owner.chowned} == {(990, 991)}
    assert all(config not in path.parents for path, _, _ in owner.chowned)


def test_guides_also_run_as_the_image_user(monkeypatch):
    _as_root(monkeypatch)
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)

    lark_runner.run_guide(("docs", "--help"))

    assert fake.calls[0].kwargs["user"] == 990


def test_the_image_user_needs_a_root_gateway(monkeypatch):
    _as_root(monkeypatch)
    monkeypatch.setattr(lark_runner.os, "geteuid", lambda: 1000)

    with pytest.raises(lark_runner.LarkUnavailable, match="root"):
        lark_runner.run_guide(("docs", "--help"))


def test_an_unknown_image_user_is_unavailable(monkeypatch):
    monkeypatch.setenv(lark_runner.RUN_AS_ENV, "nobody-here")

    def missing(name):
        raise KeyError(name)

    monkeypatch.setattr(lark_runner.pwd, "getpwnam", missing)

    with pytest.raises(lark_runner.LarkUnavailable, match="nobody-here"):
        lark_runner.run_guide(("docs", "--help"))


@pytest.mark.parametrize("mode", [0o755, 0o751, 0o701])
def test_a_home_open_to_other_users_is_unavailable(monkeypatch, mode):
    _as_root(monkeypatch)
    paths_module.get_paths().base_dir.chmod(mode)

    with pytest.raises(lark_runner.LarkUnavailable, match="其他用户"):
        lark_runner.run_guide(("docs", "--help"))


def test_a_pinned_image_without_an_image_user_is_unavailable(monkeypatch):
    monkeypatch.setenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, "v1.0.96")

    with pytest.raises(lark_runner.LarkUnavailable, match=lark_runner.RUN_AS_ENV):
        lark_runner.run_guide(("docs", "--help"))


HELP = "Fetch\n\nRisk: read\n\nUsage:\n  lark-cli docs +fetch [flags]\n"


def test_risk_is_looked_up_once_per_command(monkeypatch):
    fake = FakeCli(stdout=HELP)
    monkeypatch.setattr(lark_runner, "_run_process", fake)

    assert [lark_runner.command_risk(("docs", "+fetch")) for _ in range(3)] == ["read"] * 3
    (call,) = fake.calls
    assert call.args == ["/usr/local/bin/lark-cli", "docs", "+fetch", "--help"]


def test_a_failed_help_lookup_is_a_refusal_and_is_not_cached(monkeypatch):
    fake = FakeCli(stdout="", exit_code=1)
    monkeypatch.setattr(lark_runner, "_run_process", fake)

    for _ in range(2):
        with pytest.raises(LarkRefused):
            lark_runner.command_risk(("docs", "+fetch"))
    assert len(fake.calls) == 2


@pytest.mark.parametrize(
    ("pin", "reported", "ok"),
    [("v1.0.96", "lark-cli version 1.0.96", True), ("v1.0.96", "lark-cli version 1.0.965", False), ("v1.0.96", "lark-cli version 1.0.65", False)],
)
def test_the_binary_must_be_the_pinned_release(monkeypatch, pin, reported, ok):
    lark_runner._BINARY_CACHE.clear()
    monkeypatch.setenv(lark_cli.LARK_CLI_PINNED_VERSION_ENV, pin)
    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=True, path="/usr/local/bin/lark-cli", version=reported))
    if ok:
        assert lark_runner.resolve_binary() == "/usr/local/bin/lark-cli"
    else:
        with pytest.raises(lark_runner.LarkUnavailable, match="v1.0.96"):
            lark_runner.resolve_binary()


def test_a_missing_binary_is_unavailable(monkeypatch):
    lark_runner._BINARY_CACHE.clear()
    monkeypatch.setattr(lark_cli, "probe_lark_cli", lambda: lark_cli.LarkCliProbe(available=False, error="not installed"))

    with pytest.raises(lark_runner.LarkUnavailable, match="lark-cli"):
        lark_runner.resolve_binary()


# ---- review fixes (2026-09-29): failures become answers, nothing outlives a run, the caller's budget holds ----


@pytest.mark.parametrize("error", [PermissionError("setuid"), FileNotFoundError("lark-cli"), OSError("exec format")])
def test_a_failure_to_start_lark_cli_is_unavailable(monkeypatch, error):
    def fail(args, **kwargs):
        raise error

    monkeypatch.setattr(lark_runner, "_run_process", fail)
    _user_tree()

    with pytest.raises(lark_runner.LarkUnavailable, match="lark-cli"):
        lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))
    with pytest.raises(lark_runner.LarkUnavailable):
        lark_runner.run_guide(("docs", "--help"))


def test_an_unsafe_real_credential_tree_is_unavailable(monkeypatch, tmp_path):
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    _, data = _user_tree()
    (data / "link").symlink_to(tmp_path)

    with pytest.raises(lark_runner.LarkUnavailable):
        lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))
    assert fake.calls == []


def test_a_failed_copy_back_keeps_the_previous_tokens_and_the_output(monkeypatch):
    def refresh(_config_dir, data_dir):
        (data_dir / "token.json").write_text('{"access": "new"}', encoding="utf-8")

    monkeypatch.setattr(lark_runner, "_run_process", FakeCli(stdout='{"ok": true}', action=refresh))
    _, data = _user_tree()
    real_copy = lark_credentials.copy_tree

    def copy_tree(source, target):
        if target.name.startswith(".data-new-"):
            raise OSError("disk full")
        real_copy(source, target)

    monkeypatch.setattr(lark_credentials, "copy_tree", copy_tree)

    completed = lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    assert completed.stdout == '{"ok": true}'
    assert (data / "token.json").read_text(encoding="utf-8") == '{"access": "old"}'
    assert [path.name for path in data.parent.iterdir() if path.name.startswith(".data-")] == []


def test_a_copy_that_cannot_take_the_old_trees_place_puts_the_old_tree_back(monkeypatch, tmp_path):
    directory, source = tmp_path / "root" / "data", tmp_path / "copy"
    directory.mkdir(parents=True)
    (directory / "token.json").write_text("old", encoding="utf-8")
    source.mkdir()
    (source / "token.json").write_text("new", encoding="utf-8")
    real_rename = Path.rename

    def rename(self, target):
        if self.name.startswith(".data-new-"):
            raise OSError("cross-device")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)

    with pytest.raises(OSError):
        lark_credentials.install(directory, source)
    assert (directory / "token.json").read_text(encoding="utf-8") == "old"
    assert sorted(path.name for path in directory.parent.iterdir()) == ["data"]


def test_leftover_processes_of_the_lark_user_are_killed(monkeypatch, tmp_path):
    proc = tmp_path / "proc"
    for pid, uid in ((101, 990), (102, 0), (103, 990)):
        (proc / str(pid)).mkdir(parents=True)
        (proc / str(pid) / "status").write_text(f"Name:\tx\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n", encoding="utf-8")
    (proc / "self").mkdir()
    killed = []
    monkeypatch.setattr(lark_runner.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    lark_runner._reap(lark_runner.RunAs(990, 991), proc=proc)

    assert sorted(killed) == [(101, lark_runner.signal.SIGKILL), (103, lark_runner.signal.SIGKILL)]


def test_every_run_ends_by_reaping_the_lark_user(monkeypatch):
    _as_root(monkeypatch)
    reaped = []
    monkeypatch.setattr(lark_runner, "_reap", lambda owner, proc=None: reaped.append(owner))

    def slow(args, **kwargs):
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(lark_runner, "_run_process", slow)
    _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))
    lark_runner.run_guide(("docs", "--help"))

    assert reaped == [lark_runner.RunAs(990, 991)] * 2


def test_the_run_is_bounded_by_the_callers_budget(monkeypatch):
    fake = FakeCli(stdout=HELP)
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    _user_tree()

    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"), timeout=5)
    lark_runner.run_guide(("docs", "--help"), timeout=4)
    lark_runner.command_risk(("docs", "+fetch"), timeout=3)
    lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"))

    budgets = [call.kwargs["timeout"] for call in fake.calls]
    assert 4 < budgets[0] <= 5 and 3 < budgets[1] <= 4 and 2 < budgets[2] <= 3
    assert lark_runner.TIMEOUT_SECONDS - 1 < budgets[3] <= lark_runner.TIMEOUT_SECONDS


def test_one_run_at_a_time_across_processes(monkeypatch):
    import fcntl

    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    lock = paths_module.get_paths().base_dir / lark_runner.RUN_LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as other_worker:
        fcntl.flock(other_worker, fcntl.LOCK_EX)
        with pytest.raises(lark_runner.LarkBusy, match=lark_runner.QUEUE_TIMEOUT):
            lark_runner.run_guide(("docs", "--help"), timeout=0.3)
    assert fake.calls == []
    lark_runner.run_guide(("docs", "--help"))
    assert len(fake.calls) == 1


@pytest.mark.parametrize("bounded", [True, False])
@pytest.mark.parametrize("kind", ["guide", "user"])
def test_cold_setup_consumes_absolute_budget_without_changing_default_callers(monkeypatch, bounded, kind):
    clock = [90.0]
    monkeypatch.setattr(lark_runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    lark_runner._BINARY_CACHE.clear()

    def probe(**kwargs):
        if bounded:
            assert callable(kwargs["process_runner"])
        clock[0] = 98.0
        return lark_cli.LarkCliProbe(available=True, path="/synthetic/lark", version="0.0.0")

    monkeypatch.setattr(lark_cli, "probe_lark_cli", probe)
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    options = {"timeout": 15, **({"deadline": 100.0} if bounded else {})}
    if kind == "guide":
        lark_runner.run_guide(("skills", "list"), **options)
    else:
        _user_tree("alice")
        lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"), **options)
    assert fake.calls[0].kwargs["timeout"] == (2 if bounded else 15)


@pytest.mark.parametrize("kind", ["guide", "user"])
def test_probe_that_finishes_late_cannot_start_command_setup(monkeypatch, kind):
    clock = [90.0]
    monkeypatch.setattr(lark_runner, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    lark_runner._BINARY_CACHE.clear()

    def probe(**kwargs):
        clock[0] = 101.0
        return lark_cli.LarkCliProbe(available=True, path="/synthetic/lark", version="0.0.0")

    monkeypatch.setattr(lark_cli, "probe_lark_cli", probe)
    fake = FakeCli()
    monkeypatch.setattr(lark_runner, "_run_process", fake)
    with pytest.raises(TimeoutError):
        if kind == "guide":
            lark_runner.run_guide(("skills", "list"), deadline=100.0)
        else:
            lark_runner.run_for_user("alice", ("docs", "+fetch", "--doc", "AbC"), deadline=100.0)
    assert not fake.calls


@pytest.mark.parametrize("deadline,expected", [(102.0, 2), (200.0, 5), (99.0, None)])
def test_cold_probe_shortens_five_second_cap_and_never_starts_when_expired(monkeypatch, deadline, expected):
    lark_runner._BINARY_CACHE.clear()
    monkeypatch.setattr(lark_runner, "time", SimpleNamespace(monotonic=lambda: 100.0))
    monkeypatch.setattr(lark_cli, "_resolve_lark_cli_path", lambda: "/synthetic/lark")
    calls = []

    def run(args, **kwargs):
        calls.append((kwargs["timeout"], kwargs["deadline"]))
        return subprocess.CompletedProcess(args, 0, "0.0.0", "")

    monkeypatch.setattr(lark_runner, "_run_process", run)
    if expected is None:
        with pytest.raises(TimeoutError):
            lark_runner.resolve_binary(deadline=deadline)
    else:
        assert lark_runner.resolve_binary(deadline=deadline) == "/synthetic/lark"
    assert calls == ([] if expected is None else [(expected, min(deadline, 105.0))])
