"""What the pick-obs crons' Railway tests share (TR-15 for trends, TR-21 for gsc): the files Railway and Docker read,
a start command taken apart into its steps, the ignore rules the image build and `railway up` apply, and a start
command run the way Railway runs it: through /bin/sh, each `python -m` step through the real __main__ in a fresh
interpreter with every HTTP transport refused.
"""

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench
ROOT = Path(__file__).resolve().parents[4]
EXTENSION_API = ROOT / "backend/packages/extension-api"
HARNESS = ROOT / "backend/packages/harness"
MANAGED = Path("backend/extensions/sources/ggwork-pick")
ROOT_CONFIG = ROOT / "railway.toml"
DOCKERFILE = ROOT / "docker/Dockerfile.pick-gateway"
DOCKERIGNORE = ROOT / ".dockerignore"

# The gateway's build, which every cron's config repeats: one image, only the start command differs.
BUILD = {"builder": "DOCKERFILE", "dockerfilePath": "docker/Dockerfile.pick-gateway"}
SELFCHECK_PREFIX = "[pick-obs] selfcheck ok: "


def railway_toml(relative: Path) -> dict:
    return tomllib.loads((ROOT / relative).read_text(encoding="utf-8"))


def final_stage() -> list[str]:
    """The Dockerfile's instructions after its last FROM: the image both the gateway and the crons run."""
    lines = [line.strip() for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()]
    last_from = max(index for index, line in enumerate(lines) if line.startswith("FROM "))
    return [line for line in lines[last_from + 1 :] if line and not line.startswith("#")]


def start_steps(command: str) -> tuple[str, list[list[str]]]:
    """The directory and the `python -m` argv of each step a start command runs:
    `/bin/sh -c "cd <dir> && [python -m ... && ]... exec python -m ..."`. A step runs only when the one before it
    exited 0; the last one replaces the shell, so its exit status is the container's."""
    shell, flag, script = shlex.split(command)
    assert (shell, flag) == ("/bin/sh", "-c")  # Railway runs a start command without a shell: no cd, no &&
    words = shlex.split(script)
    assert words[0] == "cd" and words[2] == "&&", words
    steps: list[list[str]] = [[]]
    for word in words[3:]:
        steps = [*steps, []] if word == "&&" else [*steps[:-1], [*steps[-1], word]]
    *leading, last = steps
    assert all(step[:2] == ["python", "-m"] for step in leading) and last[:3] == ["exec", "python", "-m"], words
    return words[1], [*(step[2:] for step in leading), last[3:]]


def module_argv(command: str) -> tuple[str, list[str]]:
    """The directory and the `python -m` argv of a one-step start command: `/bin/sh -c "cd <dir> && exec python -m ..."`."""
    directory, steps = start_steps(command)
    assert len(steps) == 1, steps
    return directory, steps[0]


def cron_field(spec: str, low: int, high: int) -> set[int]:
    """One cron field: `*`, `*/n`, `a`, `a-b`, `a-b/n`, comma separated."""
    values = set()
    for part in spec.split(","):
        body, _, step = part.partition("/")
        first, _, last = body.partition("-")
        start, end = (low, high) if body == "*" else (int(first), int(last or first))
        assert low <= start <= end <= high, spec
        values |= set(range(start, end + 1, int(step or 1)))
    return values


# ---- what reaches Railway and the image --------------------------------------------------------------------------


def git_ignored(paths: Sequence[str]) -> list[str]:
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


def tracked(pattern: str) -> list[str]:
    """The tracked files matching a git pathspec (a clean checkout, the only kind the guard deploys, has no others)."""
    try:
        done = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", pattern], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        pytest.skip("git is not installed")
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    return done.stdout.split()


def docker_rule(pattern: str) -> re.Pattern:
    """A .dockerignore pattern: anchored at the context root, `**` spans directories, `*`, `?` and `[...]` stay in
    one."""
    parts = re.split(r"(\*\*/|\*\*|\*|\?|\[[^\]]*\])", pattern.strip("/"))
    wild = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}

    def piece(part: str) -> str:
        if part.startswith("[") and part.endswith("]") and len(part) > 2:
            return "[^" + part[2:] if part.startswith("[!") else part
        return wild.get(part, re.escape(part))

    return re.compile("".join(piece(part) for part in parts))


def dockerignore_rules() -> list[tuple[bool, re.Pattern]]:
    """.dockerignore's rules in order, each (is an exception `!...`, pattern)."""
    lines = [line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()]
    return [(line.startswith("!"), docker_rule(line.removeprefix("!"))) for line in lines if line and not line.startswith("#")]


def docker_excluded(path: str, rules: Sequence[tuple[bool, re.Pattern]]) -> bool:
    """Docker's order: the last rule matching the path or one of its parent directories decides."""
    pieces = path.split("/")
    candidates = ["/".join(pieces[: index + 1]) for index in range(len(pieces))]
    excluded = False
    for include, rule in rules:
        if any(rule.fullmatch(candidate) for candidate in candidates):
            excluded = not include
    return excluded


def packaged_paths() -> list[str]:
    """Where each shipped source file sits in the managed copy the image installs (test_managed_copy keeps the two
    equal), data files such as trends/canary_controls.json and the migrations included."""
    shipped = [path for path in (SOURCE / "ggwork_pick").rglob("*") if path.is_file() and "__pycache__" not in path.parts]
    return sorted(str(MANAGED / path.relative_to(SOURCE)) for path in [*shipped, SOURCE / "pyproject.toml"])


# ---- the entry point as Railway starts it ------------------------------------------------------------------------

IMAGE_BACKEND = "/app/backend"  # where the image keeps the backend (test_start_command_runs_in_the_image)
RUNNER = """
import importlib, json, os, runpy, sys
from datetime import datetime
from pathlib import Path

paths, now, overrides = json.loads(os.environ["PICK_TEST_START"])
flag, module, *argv = sys.argv[1:]  # what the start command passed to `python`
assert flag == "-m", sys.argv
sys.path[:0] = paths
import httpx


def refuse(*args, **kwargs):
    raise AssertionError("an HTTP request where the test allows none")


async def refuse_async(*args, **kwargs):
    refuse()


httpx.HTTPTransport.handle_request = refuse
httpx.AsyncHTTPTransport.handle_async_request = refuse_async
from ggwork_pick.observe import clock

for owner, name, path in overrides:  # a packaged data file the test brings its own copy of
    setattr(importlib.import_module(owner), name, Path(path))
clock.SystemClock = lambda: clock.ManualClock(datetime.fromisoformat(now))
sys.argv = ["-m", *argv]
runpy.run_module(module, run_name="__main__", alter_sys=True)  # what `python -m` does
"""
# The `python` a start command finds on its PATH here: this interpreter, isolated, running RUNNER with the arguments
# the command gave it (`-m <module> <argv>`).
PYTHON_SHIM = '#!/bin/sh\nexec "$PICK_TEST_INTERPRETER" -I -c "$PICK_TEST_RUNNER" "$@"\n'


def run_start_command(
    command: str, env: dict[str, str], cwd: Path, *, now: datetime, overrides: Sequence[tuple[str, str, Path]] = ()
) -> subprocess.CompletedProcess:
    """A start command as Railway runs it: the whole of it through /bin/sh, `&&` and `exec` included, with `cwd` for
    the image's backend directory and, for `python`, a fresh isolated interpreter that sees this checkout first, its
    clock reading `now`, every HTTP transport refused, and runs the step's module through its real __main__. overrides:
    (module, attribute, path) set before each step's entry runs, e.g. a packaged data file's default path pointed at
    the test's own copy."""
    start_steps(command)  # a shape Railway can run
    _, _, script = shlex.split(command)
    prefix = f"cd {IMAGE_BACKEND} && "
    assert script.startswith(prefix), script
    local = f"cd {shlex.quote(str(cwd))} && {script.removeprefix(prefix)}"
    base = {name: os.environ[name] for name in ("PATH", "HOME", "TMPDIR", "SYSTEMROOT") if name in os.environ}
    paths = [str(SOURCE), str(EXTENSION_API), str(HARNESS)]
    payload = json.dumps([paths, now.isoformat(), [[owner, name, str(path)] for owner, name, path in overrides]])
    with tempfile.TemporaryDirectory() as shims:
        python = Path(shims) / "python"
        python.write_text(PYTHON_SHIM, encoding="utf-8")
        python.chmod(0o755)
        path = os.pathsep.join(part for part in (shims, base.get("PATH")) if part)
        runner = {"PATH": path, "PICK_TEST_INTERPRETER": sys.executable, "PICK_TEST_RUNNER": RUNNER, "PICK_TEST_START": payload}
        return subprocess.run(["/bin/sh", "-c", local], env=base | env | runner, cwd=cwd, capture_output=True, text=True, timeout=240)


def selfcheck_report(line: str) -> dict[str, str]:
    """The fields of the one line --selfcheck-only prints (S6 reads it)."""
    assert line.startswith(SELFCHECK_PREFIX), line
    return dict(field.split("=", 1) for field in line.removeprefix(SELFCHECK_PREFIX).split(" "))
