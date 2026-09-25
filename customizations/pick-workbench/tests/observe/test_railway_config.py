"""TR-15: the pick-obs-trends cron as Railway builds and starts it (plan TR-15, D5, D6, D21, section 10 S5, S6 and
S6a; design 3.1, 6.3; counterexample 15).

The cron runs the gateway's image with its own config-as-code file and nothing else of its own. These tests read what
Railway and Docker read (deploy/pick-obs/trends/railway.toml and its self-check twin, the root railway.toml,
docker/Dockerfile.pick-gateway, .dockerignore), the CI workflow that runs them and the runbook's variable table, then
run the self-check twin's start command the way Railway does, through /bin/sh, each step through the real __main__ in
a fresh interpreter, on both dialects, with every HTTP transport refused: the self-check, then tonight's preflight
(S6a), in the service's own container with the service's own variables. What TR-21's gsc tests reuse is in
railway_helpers; test_cron_deploy_procedure replays the runbook's deploy order against TR-34's guard.
"""

import io
import json
import re
import tomllib
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from obs_db_helpers import is_postgres, migrated, rows, runtime_row
from railway_helpers import (
    BUILD,
    DOCKERFILE,
    ROOT,
    ROOT_CONFIG,
    SOURCE,
    cron_field,
    docker_excluded,
    dockerignore_rules,
    final_stage,
    git_ignored,
    module_argv,
    packaged_paths,
    railway_toml,
    run_start_command,
    selfcheck_report,
    start_steps,
    tracked,
)
from trends_session_helpers import EVE, TARGET, at, drama, recent_catalog, seed_catalog, trends_env, write_controls

from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.cron import parse_args
from ggwork_pick.observe.db import DATABASE_URL_VARIABLE
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.selfcheck import package_digest
from ggwork_pick.observe.trends import __main__ as trends_entry
from ggwork_pick.observe.trends import budget, capacity
from ggwork_pick.observe.versions import COLLECTOR_VERSION

TRENDS_CONFIG = Path("deploy/pick-obs/trends/railway.toml")
SELFCHECK_CONFIG = Path("deploy/pick-obs/trends/selfcheck/railway.toml")
WORKFLOW = ROOT / ".github/workflows/pick-workbench-tests.yml"
RUNBOOK = ROOT / "docs/pick-workbench/observe-runbook/packaging.md"

MODULE = "ggwork_pick.observe.trends"
START = '/bin/sh -c "cd /app/backend && exec python -m ggwork_pick.observe.trends run"'
# S6 and S6a in one run: the self-check, then, only when it passed, tonight's preflight; the container exits with the
# preflight's status.
SELFCHECK_START = '/bin/sh -c "cd /app/backend && python -m ggwork_pick.observe.trends --selfcheck-only && exec python -m ggwork_pick.observe.trends preflight"'
SCHEDULE = "*/30 17-23,0-1 * * *"
GATEWAY_CMD = 'CMD ["sh", "-c", "cd backend && python -m app.gateway.pick_entrypoint"]'
RAILWAY_MIN_INTERVAL = timedelta(minutes=5)  # Railway runs a cron at most every 5 minutes
TRIGGER_INTERVAL = timedelta(minutes=30)

# UTC hours no trigger falls in and no session runs through: when the runbook deploys the cron (packaging.md section 7).
# It ends at the night's first trigger, half an hour before the earliest start (stable's 17:30 since G3).
DEPLOY_WINDOW = (time(2, 0), time(17, 0))

GUARD_SCRIPTS = ("scripts/pick-deploy-guard.py", "scripts/_pick_deploy_guard_readers.py")  # TR-34's
# Every ignore file at every level: hatchling in the image drops what the first .gitignore above the managed copy
# matches (backend/.gitignore), git check-ignore reads the whole stack, and `railway up` reads .railwayignore as well.
IGNORE_FILES = ("**/.gitignore", "**/.railwayignore")
CI_PATHS = ("deploy/pick-obs/**", "railway.toml", ".dockerignore", *IGNORE_FILES, "docs/pick-workbench/observe-runbook/**", *GUARD_SCRIPTS)

# What the runbook's variable table may name: what the trends entry reads, the DSN, the TLS mode asyncpg reads, and
# PICK_DB_SIZE_CAP_BYTES, which plan S5 lists for TR-20's check before publishing (nothing reads it before TR-20).
READ = frozenset({*trends_entry.TRENDS_VARIABLES, DATABASE_URL_VARIABLE, "PGSSLMODE", "PICK_DB_SIZE_CAP_BYTES"})
REQUIRED = frozenset(
    {DATABASE_URL_VARIABLE, "PGSSLMODE", "PICK_OBS_STATE_KEY", "PICK_OBS_EXPECTED_COLLECTOR", "PICK_OBS_EXPECTED_ROLE", "PICK_OBS_TRENDS_MODE"}
)


# ---- the config files ---------------------------------------------------------------------------------------------


def test_trends_railway_config_pinned():
    """Field by field (plan TR-15): the gateway's build, the cron's own start command, no restart (the next trigger
    resumes), no healthcheck (nothing listens), and the UTC schedule. Any other key fails."""
    config = railway_toml(TRENDS_CONFIG)
    assert config == {"build": BUILD, "deploy": {"startCommand": START, "restartPolicyType": "NEVER", "cronSchedule": SCHEDULE}}
    assert config["build"] == tomllib.loads(ROOT_CONFIG.read_text(encoding="utf-8"))["build"]
    assert "healthcheckPath" not in config["deploy"] and "healthcheckTimeout" not in config["deploy"]


def test_selfcheck_config_pinned():
    """S6's one-off run: the same image and program as the cron and no schedule, so Railway runs it once when it is
    deployed; it never restarts. Two steps in place of run: --selfcheck-only, then, only when it exited 0, tonight's
    preflight (S6a), whose status the container exits with. The cron has no running container to `railway ssh` into,
    and a `railway run` on the operator's machine would bring the state key there; this runs the preflight where the
    service's variables already are."""
    config = railway_toml(SELFCHECK_CONFIG)
    assert config == {"build": BUILD, "deploy": {"startCommand": SELFCHECK_START, "restartPolicyType": "NEVER"}}
    cron_dir, cron_argv = module_argv(START)
    program = cron_argv[:-1]
    assert start_steps(SELFCHECK_START) == (cron_dir, [[*program, "--selfcheck-only"], [*program, "preflight"]])


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
    stage = final_stage()
    assert [line for line in stage if line.startswith("CMD")] == [GATEWAY_CMD]
    assert [line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines() if line.strip().startswith("ENTRYPOINT")] == []


def test_start_command_runs_in_the_image():
    """The start command's directory is where the image keeps the backend, `python` there is the backend's virtualenv
    (the one ggwork-pick is installed into), and the module it runs has a __main__."""
    directory, argv = module_argv(START)
    assert (directory, argv) == ("/app/backend", [MODULE, "run"])
    stage = final_stage()
    assert "WORKDIR /app" in stage and "COPY --from=builder /app/backend ./backend" in stage
    assert 'ENV PATH="/app/backend/.venv/bin:$PATH"' in stage
    assert (SOURCE / MODULE.replace(".", "/") / "__main__.py").is_file()


# ---- the schedule against the session modes (design 3.1, 6.3; plan section 9) --------------------------------------


def _night(schedule: str) -> list[datetime]:
    """The schedule's triggers on the night of TARGET, in order: from 02:00 on EVE to 02:00 on TARGET (D23)."""
    minute, hour, *rest = schedule.split()
    assert rest == ["*", "*", "*"], schedule
    times = [time(h, m) for h in cron_field(hour, 0, 23) for m in cron_field(minute, 0, 59)]
    return sorted(datetime.combine(EVE if moment >= budget.PUBLISH_CUTOFF else TARGET, moment, UTC) for moment in times)


def test_cron_schedule_fits_the_session_modes():
    """Every mode starts on a trigger, no trigger falls at or after the 01:45 deadline, a session that dies is picked
    up within 30 minutes (capacity.py prices that wait as capacity.TRIGGER_INTERVAL), and the only trigger before the
    earliest start is the one half an hour before it, which exits 0 without doing anything. The runbook's deploy window
    runs from 02:00 to that first trigger: no trigger in it, and every session is over before it opens. (G3 moved the
    starts earlier: the schedule that began at 20:00 never reached stable's 17:30 or canary2's 18:30.)"""
    night = _night(railway_toml(TRENDS_CONFIG)["deploy"]["cronSchedule"])
    assert all(later - earlier >= RAILWAY_MIN_INTERVAL for earlier, later in zip(night, night[1:], strict=False))
    assert capacity.TRIGGER_INTERVAL == TRIGGER_INTERVAL and all(moment.minute in (0, 30) for moment in night)
    windows = {name: mode.window(TARGET) for name, mode in budget.MODES.items()}
    for name, (start, deadline) in windows.items():
        assert start in night, name
        inside = [moment for moment in night if moment >= start]
        assert all(moment < deadline for moment in night), name
        assert all(later - earlier <= TRIGGER_INTERVAL for earlier, later in zip(inside, inside[1:], strict=False)), name
        assert deadline - inside[-1] <= TRIGGER_INTERVAL, name
    earliest = min(start for start, _ in windows.values())
    assert [moment for moment in night if moment < earliest] == [earliest - TRIGGER_INTERVAL]
    quiet_from, quiet_until = DEPLOY_WINDOW
    assert night[0].time() == quiet_until
    assert not [moment for moment in night if quiet_from <= moment.time() < quiet_until]
    assert all(deadline <= datetime.combine(TARGET, quiet_from, UTC) for _, deadline in windows.values())
    text = RUNBOOK.read_text(encoding="utf-8")
    assert f"UTC {quiet_from:%H:%M}–{quiet_until:%H:%M}" in text and "UTC 02:00–20:00" not in text


# ---- what reaches Railway and the image ------------------------------------------------------------------------------


def test_railway_up_uploads_the_configs():
    """`railway up` leaves out what .gitignore matches (observe-runbook/deploy-guard.md); an ignored config would
    never reach Railway, and the service would fall back to the root railway.toml. It reads .railwayignore too: the
    repository has none, and one added must be checked against the configs here."""
    assert git_ignored([str(TRENDS_CONFIG), str(SELFCHECK_CONFIG)]) == []
    assert tracked(":(glob)**/.railwayignore") == []


def test_packaged_files_reach_the_image():
    """The builder copies backend/ and builds ggwork-pick from the managed copy (design 3.1): every shipped file must
    survive .dockerignore (which drops tests/, *.md, lib/ and more elsewhere) and the ignore rules hatchling applies."""
    rules = dockerignore_rules()
    paths = packaged_paths()
    assert any(path.endswith("ggwork_pick/observe/trends/__main__.py") for path in paths)
    assert [path for path in paths if docker_excluded(path, rules)] == []
    assert docker_excluded("backend/.venv/bin/python", rules)  # the matcher does exclude what the file excludes
    assert git_ignored(paths) == []


# ---- CI --------------------------------------------------------------------------------------------------------------


def test_ci_runs_on_the_deploy_files():
    """A change to any file these tests or TR-34's guard tests read runs the workflow, ignore files at any level
    included, and the guard's scripts are linted with their own (default) ruff settings, before the tests. The step
    names both scripts outright: one renamed or gone fails it (TR-15 lands only through feat/trends-radar, which has
    TR-34's scripts)."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    for event in ("push", "pull_request"):
        assert set(CI_PATHS) <= set(triggers[event]["paths"]), event
    steps = workflow["jobs"]["pick-workbench-tests"]["steps"]
    lint = next(step for step in steps if step.get("name") == "Lint pick deploy guard")
    scripts = " ".join(GUARD_SCRIPTS)
    assert lint["run"].splitlines() == [f"backend/.venv/bin/ruff check {scripts}", f"backend/.venv/bin/ruff format --check {scripts}"]
    tests = next(index for index, step in enumerate(steps) if step.get("name", "").startswith("Run pick workbench tests"))
    assert steps.index(lint) < tests


# ---- the runbook -----------------------------------------------------------------------------------------------------

_VARIABLE_ROW = re.compile(r"^\| `((?:PICK|PG)[A-Z0-9_]*)` \|", re.MULTILINE)


def test_runbook_never_prints_a_generated_secret():
    """A command in the runbook that makes a secret (the state key) sends it to the clipboard, not the terminal: a
    printed key stays in the scrollback and, run in an agent's session, in its transcript."""
    lines = [line for line in RUNBOOK.read_text(encoding="utf-8").splitlines() if "generate_key" in line]
    assert lines and all("| pbcopy" in line for line in lines), lines


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
    """The argv after `python -m ggwork_pick.observe.trends` in the start command parses to one run; the self-check
    config's two steps to the check alone (S6) and to `preflight` (S6a); `status` and no argument parse too. A wrong
    word exits 2 and is not echoed, before anything reads the environment."""
    _, cron_argv = module_argv(START)
    _, (check_argv, preflight_argv) = start_steps(SELFCHECK_START)
    argv = cron_argv[1:]
    parsed = {
        "cron": parse_args(_entry(), argv),
        "check": parse_args(_entry(), check_argv[1:]),
        "preflight": parse_args(_entry(), preflight_argv[1:]),
        "bare": parse_args(_entry(), []),
        "status": parse_args(_entry(), ["status"]),
    }
    assert {name: (args.command, args.selfcheck_only) for name, args in parsed.items()} == {
        "cron": ("run", False),
        "check": ("run", True),
        "preflight": ("preflight", False),
        "bare": ("run", False),
        "status": ("status", False),
    }
    out, err = io.StringIO(), io.StringIO()
    assert await trends_entry.amain(["rerun-secret-word"], environ={}, out=out, err=err) == ExitCode.REFUSED
    captured = capsys.readouterr()
    assert "usage:" in captured.err and "rerun-secret-word" not in captured.err + captured.out + err.getvalue()


CONTROLS_DEFAULT = ("ggwork_pick.observe.trends.canary", "DEFAULT_CONTROLS_PATH")  # TR-05 owns the packaged list


def _selfcheck_as_railway(env: dict[str, str], controls: Path, cwd: Path):
    """The self-check config's start command, read from the file Railway reads, run as Railway runs it
    (railway_helpers.run_start_command: /bin/sh, then each step through the real __main__), with the test's own control
    list. The clock reads 22:10 UTC, inside every mode's window: an argv that fell through to `run` would take the
    lease."""
    command = railway_toml(SELFCHECK_CONFIG)["deploy"]["startCommand"]
    return run_start_command(command, env, cwd, now=at(EVE, 22, 10), overrides=[(*CONTROLS_DEFAULT, controls)])


async def _canary_payload(url: str, tmp_path: Path) -> Path:
    """A published shared catalog batch with enough recent titles for canary1's plan, and four positive controls in
    it: tonight is a canary night (admission.py)."""
    catalog = recent_catalog(150)
    await seed_catalog(url, catalog)
    return write_controls(tmp_path, catalog[:4])


def _preflight(line: str) -> dict:
    return json.loads(line)["preflight"]


@pytest_asyncio.fixture
async def obs_url(pick_db_url, tmp_path):
    return await migrated(pick_db_url, tmp_path)


@pytest.mark.asyncio
async def test_trends_entrypoint_argv_selfcheck_only(obs_url, tmp_path):
    """S6 and S6a in one deploy: the self-check config's start command checks the configuration, runs the self-check,
    prints the line S6 reads, then tonight's preflight line S6a reads (the target date the evening's session feeds,
    the pace, the payload gate's figures) and exits 0: no HTTP, no lease, no batch. The package digest it prints is
    what package_digest() gives on the source checkout, the comparison the runbook makes."""
    controls = await _canary_payload(obs_url, tmp_path)
    env = trends_env(obs_url)
    done = _selfcheck_as_railway(env, controls, tmp_path)
    assert done.returncode == ExitCode.OK, done.stderr[-2000:]
    lines = done.stdout.splitlines()
    assert len(lines) == 2
    tonight = _preflight(lines[1])
    assert (tonight["target_date"], tonight["mode"], tonight["reasons"], tonight["refused_by"]) == (f"{TARGET:%Y-%m-%d}", "canary1", [], [])
    assert tonight["planned_requests"] >= tonight["min_requests"] and tonight["controls"]["positive"] == {"listed": 4, "matched": 4}
    report = selfcheck_report(lines[0])
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
async def test_selfcheck_config_exits_2_when_tonight_is_not_a_canary(obs_url, tmp_path):
    """S6a: the image is right (the self-check line is printed) but tonight's task list is no canary's payload: every
    drama older than 14 days, no positive control in the batch. The preflight line says why and the deploy exits 2,
    before any night has sent a request or written a row."""
    await seed_catalog(obs_url, [drama(index, listed_at=EVE - timedelta(days=30)) for index in range(40)])
    controls = write_controls(tmp_path, [drama(900 + index) for index in range(4)])
    done = _selfcheck_as_railway(trends_env(obs_url), controls, tmp_path)
    assert done.returncode == ExitCode.REFUSED, done.stderr[-2000:]
    check, preflight = done.stdout.splitlines()
    tonight = _preflight(preflight)
    assert selfcheck_report(check)["collector"] == COLLECTOR_VERSION
    assert len(tonight["reasons"]) == 2 and tonight["recent_dramas"] == 0 and tonight["controls"]["positive"]["matched"] == 0
    assert await rows(obs_url, "select id from ggwp_obs_batches") == [] and (await runtime_row(obs_url))["lease_generation"] == 0


@pytest.mark.asyncio
async def test_trends_entrypoint_argv_selfcheck_only_refuses_a_bad_image(obs_url, tmp_path):
    """Counterexample 15 at S6: an image whose collector version is not the one the service expects exits 2 with the
    variable's name, not its value, without a report line, and the preflight never runs."""
    controls = await _canary_payload(obs_url, tmp_path)
    env = trends_env(obs_url, PICK_OBS_EXPECTED_COLLECTOR="obs-collector-v0-secret")
    done = _selfcheck_as_railway(env, controls, tmp_path)
    assert done.returncode == ExitCode.REFUSED and done.stdout == ""
    assert "PICK_OBS_EXPECTED_COLLECTOR" in done.stderr and "v0-secret" not in done.stderr
