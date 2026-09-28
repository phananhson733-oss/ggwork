"""TR-15 with TR-34: the deploy order in packaging.md (S5, S6 and every later cron deploy) replayed against the deploy
guard (plan section 10 S5, S6; D41; observe-runbook/deploy-guard.md).

The order is the one sh block in packaging.md that runs scripts/pick-deploy-guard.py. Each guard line runs TR-34's
real guard over deploy_guard_fakes' checkout, git and database; each scripts/pick-railway-settings.py apply line must
parse as that script's apply for pick-obs-trends and writes its file into the service's settings (Railway no longer
reads a config path); each `railway up` line must be exactly the command the latest guard pass printed, for the commit
it passed, deploying the settings the latest apply wrote; the block ends with the one record line, committed and
pushed to main. The first time the block runs as written
(progress.md has no cron:trends record yet); every later deploy runs it without --first-record.

Skipped while TR-34's guard is not in the tree (the TR-15 branch alone); it runs once the two are integrated.
"""

import importlib.util
import re
import shlex
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from railway_helpers import ROOT

fakes = pytest.importorskip("deploy_guard_fakes", reason="TR-34's deploy guard is not in this tree")

RUNBOOK = ROOT / "docs/pick-workbench/observe-runbook/packaging.md"
GUARD = "scripts/pick-deploy-guard.py"
SETTINGS = "scripts/pick-railway-settings.py"
SERVICE = "pick-obs-trends"
FIRST = "--first-record"
CHECKOUT = "<检出>"
SELFCHECK_CONFIG = "/deploy/pick-obs/trends/selfcheck/railway.toml"
CRON_CONFIG = "/deploy/pick-obs/trends/railway.toml"
RECORD_PREFIX = "- `pick-deploy-guard target="
FENCE = re.compile(r"^[ \t]*```sh\n(.*?)^[ \t]*```", re.MULTILINE | re.DOTALL)
# The fake main moves one commit per record pushed: SHA_MAIN, then SHA_NEW, then one more.
LATER = "6" * 40
HISTORY = (*fakes.HISTORY, LATER)
PUSHED = dict(zip(HISTORY, HISTORY[1:], strict=False))


@dataclass(frozen=True)
class Step:
    kind: str  # guard, config, deploy or record
    text: str


def _settings_parser():
    spec = importlib.util.spec_from_file_location("pick_railway_settings", ROOT / SETTINGS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._parser()


def _config(line: str) -> str:
    """The file an apply line writes into the service: the line parses as the script's own apply, for this service."""
    args = _settings_parser().parse_args(shlex.split(line.split(SETTINGS, 1)[1]))
    assert args.command == "apply" and args.service == SERVICE, line
    return str(args.toml)


def _step(line: str) -> Step | None:
    comment = line.startswith("#")
    if not comment and GUARD in line:
        return Step("guard", line)
    if not comment and "railway up" in line:
        return Step("deploy", line)
    if not comment and f"{SETTINGS} apply" in line:
        return Step("config", _config(line))
    if comment and "记录行" in line:
        return Step("record", line)
    return None


def procedure() -> tuple[Step, ...]:
    """The steps of the one sh block in packaging.md that runs the guard; other comment lines are prose."""
    blocks = [block for block in FENCE.findall(RUNBOOK.read_text(encoding="utf-8")) if GUARD in block]
    assert len(blocks) == 1, "packaging.md holds the deploy order in one sh block"
    return tuple(step for line in blocks[0].splitlines() if (step := _step(line.strip())) is not None)


@dataclass(frozen=True)
class Checkout:
    """The deploy checkout as the block leaves it: its HEAD (always the fetched main), the file the latest apply wrote
    into the service's settings, the output of the latest guard pass and the commit it passed, what was deployed and
    the record pushed."""

    head: str = fakes.SHA_MAIN
    config: str | None = None
    passed_for: str | None = None
    printed: str = ""
    deployed: tuple[tuple[str, str], ...] = ()  # (commit, applied file) per `railway up`
    recorded: str | None = None


def _guard(state: Checkout, line: str, root: Path, env: dict[str, str]) -> Checkout:
    argv = shlex.split(line.split(GUARD, 1)[1])
    repo = fakes.FakeRepo(root, head=state.head, tracking_main=state.head, fetched_main=state.head, history=HISTORY)
    code, out, err = fakes.run_guard(argv, repo, env=env)
    assert code == 0, f"{line}: {err}"
    return replace(state, passed_for=state.head, printed=out)


def _deploy(state: Checkout, line: str, root: Path) -> Checkout:
    assert state.passed_for == state.head, "a deploy of a commit no guard run passed"
    printed = [text.strip() for text in state.printed.splitlines() if "railway up" in text]
    assert [text.replace(shlex.quote(str(root)), CHECKOUT) for text in printed] == [line]
    assert state.config is not None, "the service's settings are applied before the deploy"
    return replace(state, deployed=(*state.deployed, (state.head, state.config)))


def _record(state: Checkout, root: Path) -> Checkout:
    """The line the latest guard pass printed, appended to progress.md, committed and pushed: main moves on."""
    line = next(text for text in state.printed.splitlines() if text.startswith(RECORD_PREFIX))
    progress = root / fakes.PROGRESS
    progress.write_text(progress.read_text(encoding="utf-8") + line + "\n", encoding="utf-8")
    return replace(state, head=PUSHED[state.head], recorded=line)


def replay(steps: tuple[Step, ...], root: Path, env: dict[str, str], state: Checkout) -> Checkout:
    for step in steps:
        if step.kind == "guard":
            state = _guard(state, step.text, root, env)
        elif step.kind == "config":
            state = replace(state, config=step.text)
        elif step.kind == "deploy":
            state = _deploy(state, step.text, root)
        else:
            state = _record(state, root)
    return state


def later(steps: tuple[Step, ...]) -> tuple[Step, ...]:
    """The block as every deploy after the first runs it: the guard without --first-record."""
    return tuple(replace(step, text=step.text.replace(f" {FIRST}", "")) if step.kind == "guard" else step for step in steps)


def with_rerun(steps: tuple[Step, ...]) -> tuple[Step, ...]:
    """The guard rerun, exactly as the round began, right before the last deploy (the check took long)."""
    last = max(index for index, step in enumerate(steps) if step.kind == "deploy")
    first = next(step for step in steps if step.kind == "guard")
    return (*steps[:last], first, *steps[last:])


@pytest.fixture
def checkout(tmp_path):
    return fakes.make_root(tmp_path), fakes.dsn_environment(tmp_path)


def test_block_deploys_the_selfcheck_then_the_cron():
    """The guard first, with --first-record as written (S5); the self-check settings applied and deployed, then the
    cron settings; both files exist; one record, last, after both deploys."""
    steps = procedure()
    kinds = [step.kind for step in steps]
    assert kinds[0] == "guard" and FIRST in steps[0].text.split()
    assert all(" cron trends" in step.text for step in steps if step.kind == "guard")
    configs = [step.text for step in steps if step.kind == "config"]
    assert configs == [SELFCHECK_CONFIG, CRON_CONFIG]
    assert all((ROOT / config.lstrip("/")).is_file() for config in configs)
    assert kinds.count("deploy") == 2 and kinds.count("record") == 1 and kinds[-1] == "record"


def test_first_deploy_passes_the_guard(checkout):
    """S5 and S6 as written, on a progress.md without a cron:trends record: both deploys build the commit the guard
    passed, the service ends on the cron config, and the one record names that commit."""
    root, env = checkout
    done = replay(procedure(), root, env, Checkout())
    assert done.deployed == ((fakes.SHA_MAIN, SELFCHECK_CONFIG), (fakes.SHA_MAIN, CRON_CONFIG))
    assert f"target=cron:trends commit={fakes.SHA_MAIN}" in done.recorded


def test_later_deploys_pass_the_guard(checkout):
    """Section 7: after the first record is on main, every redeploy (S6 redone) runs the block without --first-record
    from the newer main, and records once more."""
    root, env = checkout
    first = replay(procedure(), root, env, Checkout())
    done = replay(later(procedure()), root, env, replace(first, deployed=()))
    assert done.deployed == ((fakes.SHA_NEW, SELFCHECK_CONFIG), (fakes.SHA_NEW, CRON_CONFIG))
    assert f"commit={fakes.SHA_NEW}" in done.recorded


def test_a_rerun_before_the_record_passes(checkout):
    """The runbook's rule for a check that took long: rerun the guard before the second deploy exactly as the round
    began. The record is not on main yet, so the first round still passes --first-record, and a later round does not."""
    root, env = checkout
    first = replay(with_rerun(procedure()), root, env, Checkout())
    assert [commit for commit, _ in first.deployed] == [fakes.SHA_MAIN] * 2
    done = replay(with_rerun(later(procedure())), root, env, replace(first, deployed=()))
    assert [commit for commit, _ in done.deployed] == [fakes.SHA_NEW] * 2
