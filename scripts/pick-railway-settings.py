#!/usr/bin/env python3
"""Railway service settings for a pick-obs cron, taken from its railway.toml (trends radar TR-15; runbook
docs/pick-workbench/observe-runbook/packaging.md section 3).

Railway retired config as code: it no longer reads a service's railway.toml, and setting a config path is refused
("Config as Code (railway.json / railway.toml) is deprecated", 2026-09-28). The files under deploy/pick-obs/ stay the
single statement of what each cron runs; this script writes a file into the service's own settings and checks them.

    backend/.venv/bin/python scripts/pick-railway-settings.py plan  <toml>
    backend/.venv/bin/python scripts/pick-railway-settings.py apply <toml> -p <project> -e <environment> -s <service>
    backend/.venv/bin/python scripts/pick-railway-settings.py check <toml> -p <project> -e <environment> -s <service>
        [--deployment <id>]

apply sets startCommand, restartPolicyType, cronSchedule and healthcheckPath (cleared) with serviceInstanceUpdate, and
the build's Dockerfile as the service variable RAILWAY_DOCKERFILE_PATH (the gateway's way), without a deploy; then it
checks. check compares the service's settings, and with --deployment the manifest that deployment ran with, to the
file. Exit 0 when they agree, 1 when they differ (each difference printed), 2 on a usage error, a file it cannot
write faithfully or a failed railway call. <toml> may be the runbook's /deploy/... path. Needs the railway CLI, logged
in; the only variable value it keeps or prints is the Dockerfile path.
"""

import argparse
import json
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

CHECKOUT = Path(__file__).resolve().parents[1]
DOCKERFILE_VARIABLE = "RAILWAY_DOCKERFILE_PATH"
BUILD_KEYS = frozenset({"builder", "dockerfilePath"})
DEPLOY_KEYS = frozenset({"startCommand", "restartPolicyType", "cronSchedule"})
SETTINGS = ("startCommand", "restartPolicyType", "cronSchedule", "healthcheckPath")
RAILWAY_TIMEOUT_SECONDS = 60

SERVICES = (
    "query($pid:String!){ project(id:$pid){ services{ edges{ node{ id name } } } } }"
)
INSTANCE = (
    "query($sid:String!,$eid:String!){ serviceInstance(serviceId:$sid, environmentId:$eid)"
    "{ startCommand restartPolicyType cronSchedule healthcheckPath } }"
)
UPDATE = (
    "mutation($sid:String!,$eid:String!,$input:ServiceInstanceUpdateInput!)"
    "{ serviceInstanceUpdate(serviceId:$sid, environmentId:$eid, input:$input) }"
)
DEPLOYMENT = "query($id:String!){ deployment(id:$id){ meta } }"

Run = Callable[[Sequence[str]], str]


class SettingsError(Exception):
    """A file this script cannot write faithfully, or a service it cannot find."""


class RailwayError(Exception):
    """A railway call that failed or answered unexpectedly; the message carries no variable value."""


@dataclass(frozen=True)
class Expected:
    start_command: str
    restart_policy: str
    cron_schedule: str | None
    dockerfile: str


def resolve(path: Path, checkout: Path = CHECKOUT) -> Path:
    """The runbook writes Railway's repository-rooted /deploy/... paths; a real file path is taken as it is."""
    if path.is_file() or not str(path).startswith("/deploy/"):
        return path
    return checkout / str(path).lstrip("/")


def load(path: Path) -> Expected:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SettingsError(f"cannot read {path}: {exc.strerror}") from None
    build, deploy = data.get("build", {}), data.get("deploy", {})
    extra = sorted({*(set(build) - BUILD_KEYS), *(set(deploy) - DEPLOY_KEYS)})
    if extra:
        raise SettingsError(
            f"{path}: this script cannot write {', '.join(extra)} into the service settings"
        )
    if build.get("builder") != "DOCKERFILE":
        raise SettingsError(
            f"{path}: build.builder must be DOCKERFILE (the image the gateway runs)"
        )
    for section, key in (
        ("build", "dockerfilePath"),
        ("deploy", "startCommand"),
        ("deploy", "restartPolicyType"),
    ):
        if not data.get(section, {}).get(key):
            raise SettingsError(f"{path}: {section}.{key} is missing")
    return Expected(
        deploy["startCommand"],
        deploy["restartPolicyType"],
        deploy.get("cronSchedule"),
        build["dockerfilePath"],
    )


def update_input(expected: Expected) -> dict:
    """Every field written, so a setting left from an earlier file (a cron, a healthcheck) does not survive."""
    return {
        "startCommand": expected.start_command,
        "restartPolicyType": expected.restart_policy,
        "cronSchedule": expected.cron_schedule,
        "healthcheckPath": None,
    }


def _compare(prefix: str, wanted: Mapping, found: Mapping) -> tuple[str, ...]:
    return tuple(
        f"{prefix} {key}: expected {value!r}, found {found.get(key)!r}"
        for key, value in wanted.items()
        if found.get(key) != value
    )


def differences(
    expected: Expected,
    instance: Mapping,
    variables: Mapping,
    manifest: Mapping | None = None,
) -> tuple[str, ...]:
    found = _compare("service", update_input(expected), instance)
    found += _compare(
        "service variable", {DOCKERFILE_VARIABLE: expected.dockerfile}, variables
    )
    if manifest is not None:
        build = {"builder": "DOCKERFILE", "dockerfilePath": expected.dockerfile}
        found += _compare("deployment", build, manifest.get("build") or {})
        found += _compare(
            "deployment", update_input(expected), manifest.get("deploy") or {}
        )
    return found


def _dig(answer: object, keys: Sequence[str]) -> object:
    """The value under keys in a railway api answer; a missing or null step is a RailwayError naming the path."""
    value = answer
    for depth, key in enumerate(keys, start=1):
        if not isinstance(value, dict) or value.get(key) is None:
            raise RailwayError(
                f"railway api: no {'.'.join(keys[:depth])} in the answer"
            )
        value = value[key]
    return value


def railway_cli(argv: Sequence[str]) -> str:
    try:
        done = subprocess.run(
            ["railway", *argv],
            capture_output=True,
            text=True,
            timeout=RAILWAY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RailwayError(f"railway {argv[0]}: {type(exc).__name__}") from None
    if done.returncode != 0:
        # stderr only: a listing that failed part way may have written every variable to stdout.
        first = (done.stderr or "").strip().splitlines()[:1]
        raise RailwayError(
            f"railway {argv[0]} exited {done.returncode}: "
            + (first[0][:200] if first else "(nothing on stderr)")
        )
    return done.stdout


@dataclass(frozen=True)
class Railway:
    run: Run
    project: str
    environment: str
    service: str

    def _api(self, document: str, variables: Mapping, path: Sequence[str]) -> object:
        raw = self.run(
            ("api", document, "--variables", json.dumps(variables), "--compact")
        )
        try:
            answer = json.loads(raw)
        except json.JSONDecodeError:
            raise RailwayError("railway api: the answer is not JSON") from None
        if not isinstance(answer, dict):
            raise RailwayError("railway api: unexpected answer")
        errors = answer.get("errors")
        if errors:
            listed = errors if isinstance(errors, list) else [errors]
            raise RailwayError(
                "railway api: "
                + "; ".join(
                    str(error.get("message", "") if isinstance(error, dict) else error)
                    for error in listed
                )
            )
        return _dig(answer, ("data", *path))

    def _target(self) -> tuple[str, ...]:
        return ("-p", self.project, "-e", self.environment, "-s", self.service)

    def service_id(self) -> str:
        edges = self._api(
            SERVICES, {"pid": self.project}, ("project", "services", "edges")
        )
        if not isinstance(edges, list):
            raise RailwayError("railway api: project.services.edges is not a list")
        nodes = [edge.get("node") or {} for edge in edges if isinstance(edge, dict)]
        ids = [node.get("id") for node in nodes if node.get("name") == self.service]
        if len(ids) != 1 or not ids[0]:
            raise SettingsError(
                f"no single service named {self.service} in project {self.project}"
            )
        return ids[0]

    def instance(self, service_id: str) -> dict:
        found = self._api(
            INSTANCE, {"sid": service_id, "eid": self.environment}, ("serviceInstance",)
        )
        if not isinstance(found, dict):
            raise RailwayError("railway api: serviceInstance is not an object")
        return found

    def dockerfile_variable(self) -> dict:
        """Only the Dockerfile variable is kept: the listing carries every value, secrets included, so
        nothing railway said about it (its output or its error) is passed on."""
        try:
            listed = json.loads(
                self.run(("variable", "list", *self._target(), "--json")) or "{}"
            )
        except (RailwayError, json.JSONDecodeError):
            raise RailwayError(
                "railway variable list failed; what it said is withheld"
                " (it carries every variable's value)"
            ) from None
        if not isinstance(listed, dict):
            raise RailwayError("railway variable list: unexpected answer")
        return (
            {DOCKERFILE_VARIABLE: listed[DOCKERFILE_VARIABLE]}
            if DOCKERFILE_VARIABLE in listed
            else {}
        )

    def manifest(self, deployment: str) -> dict:
        found = self._api(DEPLOYMENT, {"id": deployment}, ("deployment",))
        meta = (found.get("meta") if isinstance(found, dict) else None) or {}
        return meta.get("serviceManifest") or {}

    def write(self, service_id: str, expected: Expected) -> None:
        written = self._api(
            UPDATE,
            {
                "sid": service_id,
                "eid": self.environment,
                "input": update_input(expected),
            },
            ("serviceInstanceUpdate",),
        )
        if written is not True:
            raise RailwayError("railway api: serviceInstanceUpdate did not return true")
        try:
            self.run(
                (
                    "variable",
                    "set",
                    f"{DOCKERFILE_VARIABLE}={expected.dockerfile}",
                    "--skip-deploys",
                    *self._target(),
                )
            )
        except RailwayError as exc:
            raise RailwayError(
                f"{exc}; the service settings were written but {DOCKERFILE_VARIABLE} "
                "was not: run apply again"
            ) from None


def _check(
    railway: Railway,
    service_id: str,
    expected: Expected,
    path: Path,
    deployment: str | None,
    out: TextIO,
) -> int:
    manifest = railway.manifest(deployment) if deployment else None
    found = differences(
        expected, railway.instance(service_id), railway.dockerfile_variable(), manifest
    )
    for line in found:
        print(line, file=out)
    what = (
        f"{railway.service} and deployment {deployment}"
        if deployment
        else railway.service
    )
    print(
        f"{len(found)} difference(s) between {what} and {path}"
        if found
        else f"Railway settings of {what} agree with {path}",
        file=out,
    )
    return 1 if found else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pick-railway-settings", description=__doc__.split("\n\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("plan").add_argument("toml", type=Path)
    for name in ("apply", "check"):
        command = commands.add_parser(name)
        command.add_argument("toml", type=Path)
        for flag, dest in (("-p", "project"), ("-e", "environment"), ("-s", "service")):
            command.add_argument(flag, dest=dest, required=True)
    commands.choices["check"].add_argument("--deployment")
    return parser


def main(
    argv: Sequence[str],
    run: Run = railway_cli,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
) -> int:
    args = _parser().parse_args(list(argv))
    path = resolve(args.toml)
    try:
        expected = load(path)
        if args.command == "plan":
            print(
                json.dumps(
                    {
                        "serviceInstanceUpdate": update_input(expected),
                        "variables": {DOCKERFILE_VARIABLE: expected.dockerfile},
                    },
                    indent=2,
                ),
                file=out,
            )
            return 0
        railway = Railway(run, args.project, args.environment, args.service)
        service_id = railway.service_id()
        if args.command == "apply":
            railway.write(service_id, expected)
        return _check(
            railway, service_id, expected, path, getattr(args, "deployment", None), out
        )
    except (SettingsError, RailwayError) as exc:
        print(f"pick-railway-settings: {exc}", file=err)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
