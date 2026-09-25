"""Plan section 10's rollout steps and the rollback matrix, read against what this tree ships (G3: seam 4, 6;
layer P2-3, P2-4, P3-1 and the matrix's database dimension; plan section 14.3).

The plan and the runbooks are what an operator follows at S0-S13; these tests keep the words executable:
- S4 imports only modules this stage ships (observe.read is TR-26's and moves to S11);
- S5 deploys the self-check config first and names the variables the trends service reads;
- the deploy source is the guard's remote, not this checkout's origin (upstream DeerFlow);
- deploy-guard.md sends a cron deploy through packaging.md's two-config procedure;
- the rollback matrix carries the database dimension, in the plan and in rollback-matrix.md alike, and the four-cell
  check names tests that exist;
- section 14.3 disposes of every finding of both G3 reviews;
- the workflow runs on every file outside the package these tests read.
"""

import ast
import importlib.util
import re

import yaml
from railway_helpers import ROOT

from ggwork_pick.observe.db import DATABASE_URL_VARIABLE
from ggwork_pick.observe.trends import __main__ as trends_entry
from ggwork_pick.observe.trends.egress import ECHO_ENV
from ggwork_pick.observe.trends.settings import GRANULARITY_VARIABLE, ROUTE_VARIABLE

PLAN_PATH = "docs/plans/2026-09-25-trends-radar-impl-plan.md"
API_TEST_PATH = "frontend/tests/unit/core/pick/api.test.ts"
PLAN = ROOT / PLAN_PATH
RUNBOOKS = ROOT / "docs/pick-workbench/observe-runbook"
GUARD = ROOT / "scripts/pick-deploy-guard.py"
API_TEST = ROOT / API_TEST_PATH
WORKFLOW = ROOT / ".github/workflows/pick-workbench-tests.yml"
# What these tests read beyond customizations/pick-workbench: a change to any of them runs the workflow.
READ_PATHS = frozenset({PLAN_PATH, API_TEST_PATH, "docs/pick-workbench/observe-runbook/**", "scripts/pick-deploy-guard.py"})

SELFCHECK_CONFIG = "/deploy/pick-obs/trends/selfcheck/railway.toml"
CRON_CONFIG = "/deploy/pick-obs/trends/railway.toml"
# S5 may name the old wrong egress name only to call it wrong; the pacing variable comes with G3's pacing fix, and
# S5 names it ahead of the trends entry reading it (the plan says the implementation decides).
WRONG_EGRESS = "PICK_OBS_EGRESS_URL"
PENDING = frozenset({"PICK_OBS_TRENDS_PACE"})
READ = frozenset({*trends_entry.TRENDS_VARIABLES, DATABASE_URL_VARIABLE, "PICK_DB_SIZE_CAP_BYTES"})
# Layer review, "the rollback matrix should state the database": each combination and whether it is allowed.
MATRIX_CELLS = {
    "F1 × G0 × DB0006": "是",
    "F1 × M0 × DB0007": "是",
    "任意前端 × G0 × DB0007": "否",
    "回滚到 M0": "否",
    "恢复 F0": "否",
    "0007 downgrade": "否",
    "停 cron、关开关、保留 DB0007": "是",
}
FOUR_CELLS = ("F1 x old card", "F1 x new card", "F1 x mixed session", "F1 x stored snapshots")
FINDINGS = (
    *(f"接缝 {number}" for number in range(1, 7)),
    *(f"分层 P2-{number}" for number in range(1, 5)),
    "分层 P3-1",
    "分层 P3-2",
)


def _between(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head + len(start))]


def _plan_section(number: int) -> str:
    return _between(PLAN.read_text(encoding="utf-8"), f"\n## {number}. ", f"\n## {number + 1}. ")


def _row(text: str, key: str) -> str:
    (line,) = [line for line in text.splitlines() if line.startswith(f"| {key} |")]
    return line


def _table_after(text: str, marker: str) -> list[list[str]]:
    lines = text[text.index(marker) :].splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("|"))
    rows = []
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        rows.append([cell.strip() for cell in line.strip("|").split("|")])
    return rows


def _guard_default_remote() -> str:
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    (value,) = [node.value.value for node in tree.body if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "DEFAULT_REMOTE"]
    return value


def test_s4_imports_only_modules_this_stage_ships():
    """S4 runs before batch 3: every ggwork_pick module it names is in the package now (seam 4, layer P2-3);
    observe.read, TR-26's, is checked at S11."""
    section = _plan_section(10)
    modules = set(re.findall(r"ggwork_pick(?:\.\w+)+", _row(section, "S4")))
    assert modules, "S4 no longer checks any import"
    assert {module for module in modules if importlib.util.find_spec(module) is None} == set()
    assert "ggwork_pick.observe.read" in _row(section, "S11")


def test_s5_deploys_the_selfcheck_config_first():
    """packaging.md's two-config procedure (seam 6, layer P3-1): the first deploy is the self-check, then the cron."""
    s5 = _row(_plan_section(10), "S5")
    assert SELFCHECK_CONFIG in s5 and CRON_CONFIG in s5
    assert s5.index(SELFCHECK_CONFIG) < s5.index(CRON_CONFIG)


def test_s5_names_the_variables_the_trends_service_reads():
    """S5 names the egress, route and granularity variables under the code's names; any other name is the old wrong
    egress name, marked as wrong, or the pacing variable G3's fix adds."""
    s5 = _row(_plan_section(10), "S5")
    named = set(re.findall(r"`(PICK_[A-Z_]+)", s5))
    assert {ECHO_ENV, ROUTE_VARIABLE, GRANULARITY_VARIABLE} <= named
    assert named - READ <= {WRONG_EGRESS, *PENDING}
    assert s5.count(f"`{WRONG_EGRESS}`") == s5.count(f"`{WRONG_EGRESS}` 是错名")


def test_deploy_source_is_the_guards_remote():
    """The guard fetches its default remote's main; this checkout's origin is upstream DeerFlow (layer P3-1)."""
    plan = PLAN.read_text(encoding="utf-8")
    places = {
        "section 10": _plan_section(10),
        "D41": _row(_plan_section(3), "D41"),
        "TR-34": _between(plan, "### TR-34 ", "\n### "),
        "deploy-guard.md": (RUNBOOKS / "deploy-guard.md").read_text(encoding="utf-8"),
        "rollback-matrix.md": (RUNBOOKS / "rollback-matrix.md").read_text(encoding="utf-8"),
    }
    assert [name for name, text in places.items() if "origin/main" in text] == []
    remote = _guard_default_remote()
    assert all(f"{remote}/main" in places[name] for name in ("section 10", "D41", "TR-34"))


def test_deploy_guard_sends_a_cron_deploy_through_packaging():
    """deploy-guard.md's cron line points at packaging.md's self-check-then-cron order (seam 6), not a manual trigger."""
    text = (RUNBOOKS / "deploy-guard.md").read_text(encoding="utf-8")
    cron = next(line for line in _between(text, "## 各模式通过之后", "\n## ").splitlines() if line.startswith("- **cron**"))
    assert "packaging.md" in cron and SELFCHECK_CONFIG in cron and CRON_CONFIG in cron


def test_rollback_matrix_states_the_database():
    """Both copies of the matrix list the same combinations, with the database, and the layer review's verdicts."""
    plan = _table_after(_plan_section(10), "**回滚矩阵**")
    runbook = _table_after((RUNBOOKS / "rollback-matrix.md").read_text(encoding="utf-8"), "## 逐格表")
    assert [row[:2] for row in plan] == [row[:2] for row in runbook]
    for rows in (plan, runbook):
        verdicts = {key: [row[2].lstrip("*") for row in rows if key in row[1]] for key in MATRIX_CELLS}
        assert {key: [verdict[:1] for verdict in found] for key, found in verdicts.items()} == {key: [first] for key, first in MATRIX_CELLS.items()}


def test_four_cell_check_names_tests_that_exist():
    """rollback-matrix.md's four-cell check (layer P2-4) names the frontend's four cases and test files that exist."""
    section = _between((RUNBOOKS / "rollback-matrix.md").read_text(encoding="utf-8"), "## 四格验证", "\n## ")
    assert set(re.findall(r"「(F1 x [^」]+)」", section)) == set(FOUR_CELLS)
    cases = API_TEST.read_text(encoding="utf-8")
    assert [name for name in FOUR_CELLS if f'it("{name}:' not in cases] == []
    files = set(re.findall(r"`([\w.-]+\.(?:py|test\.ts))`", section))
    assert files
    missing = [name for name in files if not any((ROOT / base).rglob(name) for base in ("customizations/pick-workbench/tests", "frontend/tests"))]
    assert missing == []


def test_s0_reruns_on_the_merged_sha():
    """Seam, S0: the managed copy is refreshed after merging main and the checks rerun on the final common SHA."""
    s0 = _row(_plan_section(10), "S0")
    assert "deerflow extensions upgrade" in s0 and "test_managed_copy" in s0 and "c095fe4" in s0


def test_section_14_3_disposes_of_every_g3_finding():
    """Plan 14.3 lists each numbered finding of both G3 reviews, and 12.4's G3 row points at it."""
    plan = PLAN.read_text(encoding="utf-8")
    disposal = plan[plan.index("### 14.3 G3 处置") :]
    assert [finding for finding in FINDINGS if finding not in disposal] == []
    assert "14.3" in _row(_plan_section(12), "G3")


def test_ci_runs_on_the_files_these_tests_read():
    """An edit to the plan or the frontend's four cases alone still runs these tests."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert {event: sorted(READ_PATHS - set(triggers[event]["paths"])) for event in ("push", "pull_request")} == {"push": [], "pull_request": []}
