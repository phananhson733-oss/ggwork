"""TR-15: the pick-obs-trends cron as Railway builds and starts it (plan TR-15, D5, D6, D21, section 10 S5 and S6;
design 3.1, 6.3; counterexample 15).

The cron runs the gateway's image with its own config-as-code file and nothing else of its own. These tests read what
Railway and Docker read (deploy/pick-obs/trends/railway.toml and its self-check twin, the root railway.toml,
docker/Dockerfile.pick-gateway, .dockerignore), the CI workflow that runs them and the runbook's variable table, then
run the start command's own argv through the real __main__ in a fresh interpreter, on both dialects, with every HTTP
transport refused.
"""

import io
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from obs_db_helpers import is_postgres, migrated, rows, runtime_row
from trends_session_helpers import EVE, TARGET, at, recent_catalog, trends_env, write_controls

from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.cron import parse_args
from ggwork_pick.observe.db import DATABASE_URL_VARIABLE
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.selfcheck import package_digest
from ggwork_pick.observe.trends import __main__ as trends_entry
from ggwork_pick.observe.trends import budget
from ggwork_pick.observe.versions import COLLECTOR_VERSION

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
ROOT = Path(__file__).resolve().parents[4]
EXTENSION_API = ROOT / "backend/packages/extension-api"
HARNESS = ROOT / "backend/packages/harness"
MANAGED = Path("backend/extensions/sources/ggwork-pick")
TRENDS_CONFIG = Path("deploy/pick-obs/trends/railway.toml")
SELFCHECK_CONFIG = Path("deploy/pick-obs/trends/selfcheck/railway.toml")
ROOT_CONFIG = ROOT / "railway.toml"
DOCKERFILE = ROOT / "docker/Dockerfile.pick-gateway"
DOCKERIGNORE = ROOT / ".dockerignore"
WORKFLOW = ROOT / ".github/workflows/pick-workbench-tests.yml"
RUNBOOK = ROOT / "docs/pick-workbench/observe-runbook/packaging.md"

MODULE = "ggwork_pick.observe.trends"
BUILD = {"builder": "DOCKERFILE", "dockerfilePath": "docker/Dockerfile.pick-gateway"}
START = '/bin/sh -c "cd /app/backend && exec python -m ggwork_pick.observe.trends run"'
SELFCHECK_START = '/bin/sh -c "cd /app/backend && exec python -m ggwork_pick.observe.trends --selfcheck-only"'
SCHEDULE = "*/30 20-23,0-1 * * *"
GATEWAY_CMD = 'CMD ["sh", "-c", "cd backend && python -m app.gateway.pick_entrypoint"]'
RAILWAY_MIN_INTERVAL = timedelta(minutes=5)  # Railway runs a cron at most every 5 minutes
TRIGGER_INTERVAL = timedelta(minutes=30)

GUARD_SCRIPTS = ("scripts/pick-deploy-guard.py", "scripts/_pick_deploy_guard_readers.py")  # TR-34's
CI_PATHS = ("deploy/pick-obs/**", "railway.toml", ".dockerignore", "docs/pick-workbench/observe-runbook/**", *GUARD_SCRIPTS)

# What the runbook's variable table may name: what the trends entry reads, the DSN, the TLS mode asyncpg reads, and
# PICK_DB_SIZE_CAP_BYTES, which plan S5 lists for TR-20's check before publishing (nothing reads it before TR-20).
READ = frozenset({*trends_entry.TRENDS_VARIABLES, DATABASE_URL_VARIABLE, "PGSSLMODE", "PICK_DB_SIZE_CAP_BYTES"})
REQUIRED = frozenset(
    {DATABASE_URL_VARIABLE, "PGSSLMODE", "PICK_OBS_STATE_KEY", "PICK_OBS_EXPECTED_COLLECTOR", "PICK_OBS_EXPECTED_ROLE", "PICK_OBS_TRENDS_MODE"}
)


def _toml(relative: Path) -> dict:
    return tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))


def _final_stage() -> list[str]:
    """The Dockerfile's instructions after its last FROM: the image both the gateway and the crons run."""
    lines = [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    last_from = max(index for index, line in enumerate(lines) if line.startswith("FROM "))
    return [line for line in lines[last_from + 1 :] if line and not line.startswith("#")]


def _module_argv(command: str) -> tuple[str, list[str]]:
    """The directory and the `python -m` argv a start command runs: `/bin/sh -c "cd <dir> && exec python -m ..."`."""
    shell, flag, script = shlex.split(command)
    assert (shell, flag) == ("/bin/sh", "-c")  # Railway runs a start command without a shell: no cd, no &&
    words = shlex.split(script)
    assert words[0] == "cd" and words[2:6] == ["&&", "exec", "python", "-m"], words
    return words[1], words[6:]


# ---- the config files ---------------------------------------------------------------------------------------------


def test_trends_railway_config_pinned():
    """Field by field (plan TR-15): the gateway's build, the cron's own start command, no restart (the next trigger
    resumes), no healthcheck (nothing listens), and the UTC schedule. Any other key fails."""
    config = _toml(TRENDS_CONFIG)
    assert config == {"build": BUILD, "deploy": {"startCommand": START, "restartPolicyType": "NEVER", "cronSchedule": SCHEDULE}}
    assert config["build"] == tomllib.loads(ROOT_CONFIG.read_text(encoding="utf-8"))["build"]
    assert "healthcheckPath" not in config["deploy"] and "healthcheckTimeout" not in config["deploy"]


def test_selfcheck_config_pinned():
    """S6's one-off run: the same image and program as the cron, --selfcheck-only in place of run, and no schedule,
    so Railway runs it once when it is deployed; it never restarts."""
    config = _toml(SELFCHECK_CONFIG)
    assert config == {"build": BUILD, "deploy": {"startCommand": SELFCHECK_START, "restartPolicyType": "NEVER"}}
    cron_dir, cron_argv = _module_argv(START)
    assert _module_argv(SELFCHECK_START) == (cron_dir, [*cron_argv[:-1], "--selfcheck-only"])


def test_root_railway_untouched():
    """The gateway's config (the file a service without its own config path reads) stays as it is."""
    assert tomllib.loads(ROOT_CONFIG.read_text(encoding="utf-8")) == {
        "build": BUILD,
        "deploy": {
            "healthcheckPath": "/health/ready",
            "healthcheckTimeout": 180,
            "restartPolicyType": "ON_FAILURE",
            "restartPolicyMaxRetries": 3,
            "numReplicas": 1,
        },
    }


def test_dockerfile_cmd_untouched():
    """The image's CMD stays the gateway's (backend/tests/test_pick_cloud_entrypoint.py pins it too), and there is no
    ENTRYPOINT: a cron's startCommand replaces the CMD whole instead of being passed to an entry point as arguments."""
    stage = _final_stage()
    assert [line for line in stage if line.startswith("CMD")] == [GATEWAY_CMD]
    assert [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines() if line.strip().startswith("ENTRYPOINT")] == []


def test_start_command_runs_in_the_image():
    """The start command's directory is where the image keeps the backend, `python` there is the backend's virtualenv
    (the one ggwork-pick is installed into), and the module it runs has a __main__."""
    directory, argv = _module_argv(START)
    assert (directory, argv) == ("/app/backend", [MODULE, "run"])
    stage = _final_stage()
    assert "WORKDIR /app" in stage and "COPY --from=builder /app/backend ./backend" in stage
    assert 'ENV PATH="/app/backend/.venv/bin:$PATH"' in stage
    assert (SOURCE / MODULE.replace(".", "/") / "__main__.py").is_file()


# ---- the schedule against the session modes (design 3.1, 6.3; plan section 9) --------------------------------------


def _field(spec: str, low: int, high: int) -> set[int]:
    """One cron field: `*`, `*/n`, `a`, `a-b`, `a-b/n`, comma separated."""
    values = set()
    for part in spec.split(","):
        body, _, step = part.partition("/")
        first, _, last = body.partition("-")
        start, end = (low, high) if body == "*" else (int(first), int(last or first))
        assert low <= start <= end <= high, spec
        values |= set(range(start, end + 1, int(step or 1)))
    return values


def _night(schedule: str) -> list[datetime]:
    """The schedule's triggers on the night of TARGET, in order: from 02:00 on EVE to 02:00 on TARGET (D23)."""
    minute, hour, *rest = schedule.split()
    assert rest == ["*", "*", "*"], schedule
    times = [time(h, m) for h in _field(hour, 0, 23) for m in _field(minute, 0, 59)]
    return sorted(datetime.combine(EVE if moment >= budget.PUBLISH_CUTOFF else TARGET, moment, UTC) for moment in times)


def test_cron_schedule_fits_the_session_modes():
    """Every mode starts on a trigger, no trigger falls at or after the 01:45 deadline, a session that dies is picked
    up within 30 minutes, and the only trigger before every start is 20:00, which exits 0 without doing anything."""
    night = _night(_toml(TRENDS_CONFIG)["deploy"]["cronSchedule"])
    assert all(later - earlier >= RAILWAY_MIN_INTERVAL for earlier, later in zip(night, night[1:], strict=False))
    windows = {name: mode.window(TARGET) for name, mode in budget.MODES.items()}
    for name, (start, deadline) in windows.items():
        assert start in night, name
        inside = [moment for moment in night if moment >= start]
        assert all(moment < deadline for moment in night), name
        assert all(later - earlier <= TRIGGER_INTERVAL for earlier, later in zip(inside, inside[1:], strict=False)), name
        assert deadline - inside[-1] <= TRIGGER_INTERVAL, name
    earliest = min(start for start, _ in windows.values())
    assert [moment.time() for moment in night if moment < earliest] == [time(20, 0)]


# ---- what reaches Railway and the image ------------------------------------------------------------------------------


def _git_ignored(paths: list[str]) -> list[str]:
    """The paths some ignore rule matches, tracked or not (`railway up` and hatchling apply the patterns)."""
    try:
        done = subprocess.run(
            ["git", "-C", str(ROOT), "check-ignore", "--no-index", "--stdin"], input="\n".join(paths), capture_output=True, text=True, timeout=60
        )
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if done.returncode not in (0, 1):
        pytest.skip("not a git checkout")
    return done.stdout.split()


def test_railway_up_uploads_the_configs():
    """`railway up` leaves out what .gitignore matches (observe-runbook/deploy-guard.md); an ignored config would
    never reach Railway, and the service would fall back to the root railway.toml."""
    assert _git_ignored([str(TRENDS_CONFIG), str(SELFCHECK_CONFIG)]) == []


def _docker_rule(pattern: str) -> re.Pattern:
    """A .dockerignore pattern: anchored at the context root, `**` spans directories, `*`, `?` and `[...]` stay in
    one."""
    parts = re.split(r"(\*\*/|\*\*|\*|\?|\[[^\]]*\])", pattern.strip("/"))
    wild = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}

    def piece(part: str) -> str:
        if part.startswith("[") and part.endswith("]") and len(part) > 2:
            return "[^" + part[2:] if part.startswith("[!") else part
        return wild.get(part, re.escape(part))

    return re.compile("".join(piece(part) for part in parts))


def _docker_excluded(path: str, rules: list[tuple[bool, re.Pattern]]) -> bool:
    """Docker's order: the last rule matching the path or one of its parent directories decides."""
    pieces = path.split("/")
    candidates = ["/".join(pieces[: index + 1]) for index in range(len(pieces))]
    excluded = False
    for include, rule in rules:
        if any(rule.fullmatch(candidate) for candidate in candidates):
            excluded = not include
    return excluded


def _packaged_paths() -> list[str]:
    """Where each shipped source file sits in the managed copy the image installs (test_managed_copy keeps the two
    equal), data files such as trends/canary_controls.json and the migrations included."""
    shipped = [path for path in (SOURCE / "ggwork_pick").rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    return sorted(str(MANAGED / path.relative_to(SOURCE)) for path in [*shipped, SOURCE / "pyproject.toml"])


def test_packaged_files_reach_the_image():
    """The builder copies backend/ and builds ggwork-pick from the managed copy (design 3.1): every shipped file must
    survive .dockerignore (which drops tests/, *.md, lib/ and more elsewhere) and the ignore rules hatchling applies."""
    lines = [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()]
    rules = [(line.startswith("!"), _docker_rule(line.removeprefix("!"))) for line in lines if line and not line.startswith("#")]
    paths = _packaged_paths()
    assert any(path.endswith("ggwork_pick/observe/trends/__main__.py") for path in paths)
    assert [path for path in paths if _docker_excluded(path, rules)] == []
    assert _docker_excluded("backend/.venv/bin/python", rules)  # the matcher does exclude what the file excludes
    assert _git_ignored(paths) == []


# ---- CI --------------------------------------------------------------------------------------------------------------


def test_ci_runs_on_the_deploy_files():
    """A change to any file these tests or TR-34's guard tests read runs the workflow, and the guard's scripts are
    linted with their own (default) ruff settings, skipped with a warning while they are not in the tree."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    for event in ("push", "pull_request"):
        assert set(CI_PATHS) <= set(triggers[event]["paths"]), event
    steps = workflow["jobs"]["pick-workbench-tests"]["steps"]
    lint = next(step for step in steps if step.get("name") == "Lint pick deploy guard")
    assert all(script in lint["run"] for script in GUARD_SCRIPTS)
    assert "ruff check" in lint["run"] and "ruff format --check" in lint["run"] and "::warning::" in lint["run"]
    tests = next(index for index, step in enumerate(steps) if step.get("name", "").startswith("Run pick workbench tests"))
    assert steps.index(lint) < tests


# ---- the runbook -----------------------------------------------------------------------------------------------------

_VARIABLE_ROW = re.compile(r"^\| `((?:PICK|PG)[A-Z0-9_]*)` \|", re.MULTILINE)


def test_runbook_variables_are_the_ones_the_service_reads():
    """S5's table in packaging.md names only variables the trends service reads (plan S5 wrote PICK_OBS_EGRESS_URL
    for the code's PICK_OBS_EGRESS_ECHO_URL), and every one a run needs."""
    named = set(_VARIABLE_ROW.findall(RUNBOOK.read_text(encoding="utf-8")))
    assert named - READ == set()
    assert REQUIRED <= named


# ---- the entry point as Railway starts it ------------------------------------------------------------------------------


def _entry():
    return trends_entry.TrendsCron(ManualClock(at(EVE, 12)), random_source(1), None, None, None).entry()


@pytest.mark.asyncio
async def test_trends_entrypoint_argv(capsys):
    """The argv after `python -m ggwork_pick.observe.trends` in the start command parses to one run; with
    --selfcheck-only (S6) to the check alone; `status` and no argument parse too. A wrong word exits 2 and is not
    echoed, before anything reads the environment."""
    _, cron_argv = _module_argv(START)
    _, check_argv = _module_argv(SELFCHECK_START)
    argv = cron_argv[1:]
    parsed = {
        "cron": parse_args(_entry(), argv),
        "check": parse_args(_entry(), check_argv[1:]),
        "bare": parse_args(_entry(), []),
        "status": parse_args(_entry(), ["status"]),
    }
    assert {name: (args.command, args.selfcheck_only) for name, args in parsed.items()} == {
        "cron": ("run", False),
        "check": ("run", True),
        "bare": ("run", False),
        "status": ("status", False),
    }
    out, err = io.StringIO(), io.StringIO()
    assert await trends_entry.amain(["rerun-secret-word"], environ={}, out=out, err=err) == ExitCode.REFUSED
    captured = capsys.readouterr()
    assert "usage:" in captured.err and "rerun-secret-word" not in captured.err + captured.out + err.getvalue()


RUNNER = """
import json, runpy, sys
from datetime import datetime
from pathlib import Path

paths, controls, night, argv = json.loads(sys.argv[1])
sys.path[:0] = paths
import httpx


def refuse(*args, **kwargs):
    raise AssertionError("an HTTP request from the self-check")


async def refuse_async(*args, **kwargs):
    refuse()


httpx.HTTPTransport.handle_request = refuse
httpx.AsyncHTTPTransport.handle_async_request = refuse_async
from ggwork_pick.observe import clock
from ggwork_pick.observe.trends import canary

canary.DEFAULT_CONTROLS_PATH = Path(controls)  # TR-05 owns the packaged list; the test brings its own copy
clock.SystemClock = lambda: clock.ManualClock(datetime.fromisoformat(night))  # inside a session's window
sys.argv = ["-m", *argv]
runpy.run_module("ggwork_pick.observe.trends", run_name="__main__", alter_sys=True)  # what `python -m` does
"""


def _run_as_railway(argv: list[str], env: dict[str, str], controls: Path, cwd: Path) -> subprocess.CompletedProcess:
    """The real __main__ in a fresh isolated interpreter that sees this checkout first, as `python -m` runs it. Its
    clock reads 22:10 UTC, inside every mode's window: an argv that fell through to `run` would take the lease."""
    base = {name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if name in os.environ}
    payload = json.dumps([[str(SOURCE), str(EXTENSION_API), str(HARNESS)], str(controls), at(EVE, 22, 10).isoformat(), argv])
    command = [sys.executable, "-I", "-c", RUNNER, payload]
    return subprocess.run(command, env=base | env, cwd=cwd, capture_output=True, text=True, timeout=120)


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


def _report(line: str) -> dict[str, str]:
    prefix = "[pick-obs] selfcheck ok: "
    assert line.startswith(prefix), line
    return dict(field.split("=", 1) for field in line.removeprefix(prefix).split(" "))


@pytest.mark.asyncio
async def test_trends_entrypoint_argv_selfcheck_only(obs_url, tmp_path):
    """S6: the self-check config's argv through the real __main__ checks the configuration, runs the self-check,
    prints the one line S6 reads and exits 0: no HTTP, no lease, no batch. The package digest it prints is what
    package_digest() gives on the source checkout, the comparison the runbook makes."""
    controls = write_controls(tmp_path, recent_catalog(2))
    env = trends_env(obs_url)
    _, argv = _module_argv(SELFCHECK_START)
    done = _run_as_railway(argv[1:], env, controls, tmp_path)
    assert done.returncode == ExitCode.OK, done.stderr[-2000:]
    lines = done.stdout.splitlines()
    assert len(lines) == 1
    report = _report(lines[0])
    heads = await rows(obs_url, "select version_num from ggwp_alembic_version")
    role = env.get("PICK_OBS_EXPECTED_ROLE", "-") if is_postgres(obs_url) else "-"
    expected = {"collector": COLLECTOR_VERSION, "head": heads[0]["version_num"], "role": role}
    assert {key: report[key] for key in expected} == expected
    assert report["package"] == package_digest(SOURCE / "ggwork_pick") and report["at"] == str(SOURCE / "ggwork_pick")
    runtime = await runtime_row(obs_url)
    assert (runtime["lease_owner"], runtime["lease_generation"]) == (None, 0)
    assert await rows(obs_url, "select id from ggwp_obs_batches") == []
    assert "不读" not in done.stderr  # every variable set is one the service reads
    assert env[DATABASE_URL_VARIABLE] not in done.stdout + done.stderr and env["PICK_OBS_STATE_KEY"] not in done.stdout + done.stderr


@pytest.mark.asyncio
async def test_trends_entrypoint_argv_selfcheck_only_refuses_a_bad_image(obs_url, tmp_path):
    """Counterexample 15 at S6: an image whose collector version is not the one the service expects exits 2 with the
    variable's name, not its value, and without a report line."""
    controls = write_controls(tmp_path, recent_catalog(2))
    env = trends_env(obs_url, PICK_OBS_EXPECTED_COLLECTOR="obs-collector-v0-secret")
    _, argv = _module_argv(SELFCHECK_START)
    done = _run_as_railway(argv[1:], env, controls, tmp_path)
    assert done.returncode == ExitCode.REFUSED and done.stdout == ""
    assert "PICK_OBS_EXPECTED_COLLECTOR" in done.stderr and "v0-secret" not in done.stderr
