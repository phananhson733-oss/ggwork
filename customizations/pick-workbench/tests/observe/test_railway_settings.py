"""TR-15 after Railway retired config as code: scripts/pick-railway-settings.py writes a pick-obs cron's railway.toml
into the service's own settings and checks them (observe-runbook/packaging.md section 3).

Railway no longer reads a service's railway.toml and refuses to set a config path (2026-09-28, S5). The two files under
deploy/pick-obs/trends/ stay the single statement of what the service runs: these tests pin how the script maps them to
serviceInstanceUpdate and the Dockerfile variable, what it compares, and that it never prints a variable's value other
than the Dockerfile path. The railway CLI is replaced by a recorder; nothing here reaches Railway.
"""

import importlib.util
import io
import json
import subprocess
from pathlib import Path

import pytest
from railway_helpers import ROOT

SCRIPT = ROOT / "scripts" / "pick-railway-settings.py"
if not SCRIPT.is_file():
    pytest.skip("scripts/pick-railway-settings.py is not in this tree", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("pick_railway_settings", SCRIPT)
settings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(settings)

CRON = ROOT / "deploy/pick-obs/trends/railway.toml"
SELFCHECK = ROOT / "deploy/pick-obs/trends/selfcheck/railway.toml"
DOCKERFILE = "docker/Dockerfile.pick-gateway"
PROJECT, ENVIRONMENT, SERVICE, SERVICE_ID = "proj-1", "env-1", "pick-obs-trends", "svc-9"
SECRET = "postgresql://pick_observer.x:do-not-print@host:5432/postgres"
ARGS = ("-p", PROJECT, "-e", ENVIRONMENT, "-s", SERVICE)


def _instance(expected, **changes):
    fields = {
        "startCommand": expected.start_command,
        "restartPolicyType": expected.restart_policy,
        "cronSchedule": expected.cron_schedule,
        "healthcheckPath": None,
    }
    return {**fields, **changes}


class Recorder:
    """The railway CLI: answers the queries the script sends, records every call, fails when told to."""

    def __init__(self, instance, variables, manifest=None, fail_on=None, raw=None, fail_message="railway exited 1"):
        self.instance, self.variables, self.manifest, self.fail_on = instance, variables, manifest, fail_on
        self.raw, self.fail_message = raw or {}, fail_message
        self.calls = []

    def __call__(self, argv):
        self.calls = [*self.calls, tuple(argv)]
        joined = " ".join(argv)
        if self.fail_on and self.fail_on in joined:
            raise settings.RailwayError(self.fail_message)
        answer = next((text for marker, text in self.raw.items() if marker in joined), None)
        if answer is not None:
            return answer
        if argv[:2] == ("variable", "list"):
            return json.dumps(self.variables)
        if argv[:2] == ("variable", "set"):
            return ""
        document = argv[argv.index("api") + 1] if "api" in argv else ""
        if "project(" in document:
            return json.dumps({"data": {"project": {"services": {"edges": [{"node": {"id": SERVICE_ID, "name": SERVICE}}]}}}})
        if "serviceInstanceUpdate" in document:
            return json.dumps({"data": {"serviceInstanceUpdate": True}})
        if "serviceInstance(" in document:
            return json.dumps({"data": {"serviceInstance": self.instance}})
        if "deployment(" in document:
            return json.dumps({"data": {"deployment": {"meta": {"serviceManifest": self.manifest}}}})
        raise AssertionError(f"unexpected railway call: {argv}")


def _run(argv, recorder):
    out, err = io.StringIO(), io.StringIO()
    code = settings.main(list(argv), run=recorder, out=out, err=err)
    return code, out.getvalue(), err.getvalue()


def test_the_cron_file_is_the_cron():
    expected = settings.load(CRON)
    assert expected.cron_schedule == "*/30 17-23,0-1 * * *"
    assert expected.restart_policy == "NEVER"
    assert expected.dockerfile == DOCKERFILE
    assert "ggwork_pick.observe.trends run" in expected.start_command


def test_the_selfcheck_file_has_no_cron():
    expected = settings.load(SELFCHECK)
    assert expected.cron_schedule is None
    assert expected.restart_policy == "NEVER"
    assert "--selfcheck-only" in expected.start_command and "preflight" in expected.start_command


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ('[build]\nbuilder = "RAILPACK"\ndockerfilePath = "d"\n[deploy]\nstartCommand = "x"\nrestartPolicyType = "NEVER"\n', "DOCKERFILE"),
        ('[build]\nbuilder = "DOCKERFILE"\n[deploy]\nstartCommand = "x"\nrestartPolicyType = "NEVER"\n', "dockerfilePath"),
        ('[build]\nbuilder = "DOCKERFILE"\ndockerfilePath = "d"\n[deploy]\nrestartPolicyType = "NEVER"\n', "startCommand"),
        ('[build]\nbuilder = "DOCKERFILE"\ndockerfilePath = "d"\n[deploy]\nstartCommand = "x"\n', "restartPolicyType"),
        (
            '[build]\nbuilder = "DOCKERFILE"\ndockerfilePath = "d"\n[deploy]\nstartCommand = "x"\nrestartPolicyType = "NEVER"\nhealthcheckPath = "/h"\n',
            "healthcheckPath",
        ),
    ],
)
def test_load_refuses_what_it_cannot_write(tmp_path, text, match):
    path = tmp_path / "railway.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(settings.SettingsError, match=match):
        settings.load(path)


def test_update_input_writes_every_field_and_clears_the_rest():
    selfcheck = settings.update_input(settings.load(SELFCHECK))
    assert selfcheck == {
        "startCommand": settings.load(SELFCHECK).start_command,
        "restartPolicyType": "NEVER",
        "cronSchedule": None,
        "healthcheckPath": None,
    }
    assert settings.update_input(settings.load(CRON))["cronSchedule"] == "*/30 17-23,0-1 * * *"


def test_no_differences_when_the_service_matches():
    expected = settings.load(CRON)
    assert settings.differences(expected, _instance(expected), {settings.DOCKERFILE_VARIABLE: DOCKERFILE}) == ()


@pytest.mark.parametrize(
    ("changes", "variables", "named"),
    [
        ({"startCommand": None}, {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, "startCommand"),
        ({"restartPolicyType": "ON_FAILURE"}, {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, "restartPolicyType"),
        ({"cronSchedule": None}, {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, "cronSchedule"),
        ({"healthcheckPath": "/health/ready"}, {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, "healthcheckPath"),
        ({}, {}, settings.DOCKERFILE_VARIABLE),
        ({}, {settings.DOCKERFILE_VARIABLE: "Dockerfile"}, settings.DOCKERFILE_VARIABLE),
    ],
)
def test_each_difference_is_named(changes, variables, named):
    expected = settings.load(CRON)
    found = settings.differences(expected, _instance(expected, **changes), variables)
    assert len(found) == 1 and named in found[0]


def test_the_deployment_manifest_is_compared_too():
    expected = settings.load(SELFCHECK)
    manifest = {
        "build": {"builder": "DOCKERFILE", "dockerfilePath": DOCKERFILE},
        "deploy": {"startCommand": expected.start_command, "restartPolicyType": "NEVER", "cronSchedule": None, "healthcheckPath": None},
    }
    matching = _instance(expected)
    variables = {settings.DOCKERFILE_VARIABLE: DOCKERFILE}
    assert settings.differences(expected, matching, variables, manifest) == ()
    ran_the_gateway = {**manifest, "deploy": {**manifest["deploy"], "startCommand": None}}
    assert any("deployment startCommand" in line for line in settings.differences(expected, matching, variables, ran_the_gateway))
    railpack = {**manifest, "build": {"builder": "RAILPACK", "dockerfilePath": None}}
    assert len(settings.differences(expected, matching, variables, railpack)) == 2


def test_plan_prints_the_update_and_the_variable():
    code, out, _ = _run(("plan", str(SELFCHECK)), Recorder(None, {}))
    assert code == 0
    planned = json.loads(out)
    assert planned["serviceInstanceUpdate"]["restartPolicyType"] == "NEVER"
    assert planned["variables"] == {settings.DOCKERFILE_VARIABLE: DOCKERFILE}


def test_apply_writes_without_deploying_then_checks():
    expected = settings.load(SELFCHECK)
    recorder = Recorder(_instance(expected), {settings.DOCKERFILE_VARIABLE: DOCKERFILE, "PICK_DATABASE_URL": SECRET})
    code, out, err = _run(("apply", str(SELFCHECK), *ARGS), recorder)
    assert code == 0, err
    update = next(call for call in recorder.calls if "serviceInstanceUpdate" in " ".join(call))
    variables = json.loads(update[update.index("--variables") + 1])
    assert variables == {"sid": SERVICE_ID, "eid": ENVIRONMENT, "input": settings.update_input(expected)}
    set_variable = next(call for call in recorder.calls if call[:2] == ("variable", "set"))
    assert f"{settings.DOCKERFILE_VARIABLE}={DOCKERFILE}" in set_variable and "--skip-deploys" in set_variable
    assert all("up" not in call and "redeploy" not in call for call in recorder.calls)
    assert "agree" in out and SECRET not in out + err


def test_check_reports_differences_without_printing_secrets():
    expected = settings.load(CRON)
    recorder = Recorder(_instance(expected, cronSchedule=None), {"PICK_DATABASE_URL": SECRET})
    code, out, err = _run(("check", str(CRON), *ARGS), recorder)
    assert code == 1
    assert "cronSchedule" in out and settings.DOCKERFILE_VARIABLE in out
    assert SECRET not in out + err and "do-not-print" not in out + err
    assert not any(call[:2] == ("variable", "set") or "serviceInstanceUpdate" in " ".join(call) for call in recorder.calls)


def test_check_with_a_deployment_reads_its_manifest():
    expected = settings.load(SELFCHECK)
    manifest = {"build": {"builder": "DOCKERFILE", "dockerfilePath": DOCKERFILE}, "deploy": {"startCommand": None}}
    recorder = Recorder(_instance(expected), {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, manifest)
    code, out, _ = _run(("check", str(SELFCHECK), *ARGS, "--deployment", "dep-1"), recorder)
    assert code == 1 and "deployment startCommand" in out


def test_every_railway_call_names_the_project_and_environment():
    expected = settings.load(SELFCHECK)
    recorder = Recorder(_instance(expected), {settings.DOCKERFILE_VARIABLE: DOCKERFILE})
    _run(("apply", str(SELFCHECK), *ARGS), recorder)
    for call in recorder.calls:
        if call[0] == "variable":
            assert ("-p", PROJECT) == call[call.index("-p") : call.index("-p") + 2]
            assert ("-e", ENVIRONMENT) == call[call.index("-e") : call.index("-e") + 2]
            assert ("-s", SERVICE) == call[call.index("-s") : call.index("-s") + 2]


def test_a_failed_railway_call_exits_2():
    expected = settings.load(SELFCHECK)
    recorder = Recorder(_instance(expected), {}, fail_on="serviceInstanceUpdate")
    code, _, err = _run(("apply", str(SELFCHECK), *ARGS), recorder)
    assert code == 2 and "railway" in err


def test_an_unknown_service_exits_2():
    expected = settings.load(SELFCHECK)
    code, _, err = _run(("check", str(SELFCHECK), "-p", PROJECT, "-e", ENVIRONMENT, "-s", "nope"), Recorder(_instance(expected), {}))
    assert code == 2 and "nope" in err


def test_a_missing_file_exits_2(tmp_path):
    code, _, err = _run(("plan", str(tmp_path / "absent.toml")), Recorder(None, {}))
    assert code == 2 and "absent.toml" in err


def test_usage_errors_exit_2():
    with pytest.raises(SystemExit) as stopped:
        settings.main(["apply", str(SELFCHECK)], run=Recorder(None, {}), out=io.StringIO(), err=io.StringIO())
    assert stopped.value.code == 2


def test_the_script_is_path_independent():
    """Run from anywhere: the file argument may be the runbook's absolute-looking /deploy/... path or a real path."""
    assert settings.resolve(Path("/deploy/pick-obs/trends/railway.toml"), ROOT) == CRON
    assert settings.resolve(CRON, ROOT) == CRON


# ---- failure paths: exit 2, no traceback, no variable value ------------------------------------------------------


def _completed(code, stdout="", stderr=""):
    return subprocess.CompletedProcess(["railway"], code, stdout=stdout, stderr=stderr)


def test_railway_cli_reports_stderr_never_stdout(monkeypatch):
    """A failed listing may have written every variable to stdout before it failed: only stderr reaches the message."""
    listing = json.dumps({"PICK_DATABASE_URL": SECRET})
    monkeypatch.setattr(settings.subprocess, "run", lambda *a, **k: _completed(1, stdout=listing))
    with pytest.raises(settings.RailwayError) as failed:
        settings.railway_cli(("variable", "list", "--json"))
    assert SECRET not in str(failed.value) and "do-not-print" not in str(failed.value)
    monkeypatch.setattr(settings.subprocess, "run", lambda *a, **k: _completed(1, stdout=listing, stderr="Unauthorized\nmore"))
    with pytest.raises(settings.RailwayError, match="Unauthorized"):
        settings.railway_cli(("variable", "list", "--json"))


def test_railway_cli_returns_stdout_on_success(monkeypatch):
    seen = {}

    def fake(argv, **kwargs):
        seen.update(argv=argv, timeout=kwargs["timeout"])
        return _completed(0, stdout="{}")

    monkeypatch.setattr(settings.subprocess, "run", fake)
    assert settings.railway_cli(("api", "q")) == "{}"
    assert seen == {"argv": ["railway", "api", "q"], "timeout": settings.RAILWAY_TIMEOUT_SECONDS}


@pytest.mark.parametrize("raised", [subprocess.TimeoutExpired("railway", 60), FileNotFoundError("railway")])
def test_railway_cli_turns_a_hang_or_a_missing_binary_into_railway_error(monkeypatch, raised):
    def fake(*args, **kwargs):
        raise raised

    monkeypatch.setattr(settings.subprocess, "run", fake)
    with pytest.raises(settings.RailwayError, match=type(raised).__name__):
        settings.railway_cli(("api", "q"))


def test_a_failed_listing_withholds_what_railway_said():
    """Even a message the CLI built from its own output is replaced: the listing is the one call that carries secrets."""
    expected = settings.load(SELFCHECK)
    recorder = Recorder(_instance(expected), {}, fail_on="variable list", fail_message=f"railway variable exited 1: {SECRET}")
    code, out, err = _run(("check", str(SELFCHECK), *ARGS), recorder)
    assert code == 2 and "withheld" in err
    assert SECRET not in out + err and "do-not-print" not in out + err


@pytest.mark.parametrize(
    ("command", "raw", "named"),
    [
        ("check", {"project(": ""}, "not JSON"),
        ("check", {"project(": "Unauthorized"}, "not JSON"),
        ("check", {"project(": "[]"}, "unexpected"),
        ("check", {"project(": json.dumps({"data": {"project": None}})}, "project"),
        ("check", {"project(": json.dumps({"errors": ["Not Authorized"]})}, "Not Authorized"),
        ("check", {"project(": json.dumps({"errors": [{"message": "Project not found"}]})}, "Project not found"),
        ("check", {"project(": json.dumps({"other": 1})}, "data"),
        ("check", {"project(": json.dumps({"data": {"project": {"services": {"edges": "x"}}}})}, "edges"),
        ("check", {"serviceInstance(": json.dumps({"data": {"serviceInstance": None}})}, "serviceInstance"),
        ("check", {"variable list": "[]"}, "variable list"),
        ("check", {"variable list": "not json"}, "withheld"),
        ("deployment", {"deployment(": json.dumps({"data": {"deployment": None}})}, "deployment"),
        ("apply", {"serviceInstanceUpdate": json.dumps({"data": {"serviceInstanceUpdate": False}})}, "serviceInstanceUpdate"),
    ],
)
def test_an_unexpected_answer_exits_2_without_a_traceback(command, raw, named):
    """A typo'd project or deployment id, an expired login or a changed API: exit 2 with a message, never a traceback."""
    expected = settings.load(SELFCHECK)
    argv = {
        "check": ("check", str(SELFCHECK), *ARGS),
        "deployment": ("check", str(SELFCHECK), *ARGS, "--deployment", "typo"),
        "apply": ("apply", str(SELFCHECK), *ARGS),
    }[command]
    recorder = Recorder(_instance(expected), {settings.DOCKERFILE_VARIABLE: DOCKERFILE}, {}, raw=raw)
    code, _, err = _run(argv, recorder)
    assert code == 2 and named in err, err


def test_a_half_written_apply_says_so():
    """The settings were written and the Dockerfile variable was not: the message says which, and to apply again."""
    expected = settings.load(SELFCHECK)
    recorder = Recorder(_instance(expected), {}, fail_on="variable set")
    code, _, err = _run(("apply", str(SELFCHECK), *ARGS), recorder)
    assert code == 2 and "were written" in err and settings.DOCKERFILE_VARIABLE in err and "apply again" in err
    assert any("serviceInstanceUpdate" in " ".join(call) for call in recorder.calls)
