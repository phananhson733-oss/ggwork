"""Plan section 10's rollout steps and the rollback matrix, read against what this tree ships (G3: seam 4, 6;
layer P2-3, P2-4, P3-1 and the matrix's database dimension; plan section 14.3).

The plan and the runbooks are what an operator follows at S0-S13; these tests keep the words executable:
- S4 imports only modules this stage ships, under either spelling (observe.read is TR-26's and moves to S11);
- S5 and S6 deploy the self-check config first and name the variables the trends service reads; S6 takes its deploy
  window from packaging.md section 7 instead of writing its own hours; S6a gates S7;
- the deploy source is the guard's remote, not this checkout's origin (upstream DeerFlow);
- deploy-guard.md sends a cron deploy through packaging.md's two-config procedure;
- the guard is written as a source check only, apart from the contract check, and S13 raises the floor to M1;
- the rollback matrix carries the database dimension, in the plan and in rollback-matrix.md alike, and the four-cell
  check's commands select exactly the tests it names, while the output it asks for names every case they select;
- section 14.3 disposes of every finding of both G3 reviews, names who does each, and its gates are prerequisites of
  the steps they gate;
- the workflow runs on every file outside the package these tests read.
"""

import ast
import importlib.util
import re
from itertools import takewhile

import yaml
from railway_helpers import ROOT
from test_railway_config import DEPLOY_WINDOW

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
GATEWAY_TESTS = "customizations/pick-workbench/tests"
# What these tests read beyond customizations/pick-workbench: a change to any of them runs the workflow.
READ_PATHS = frozenset({PLAN_PATH, API_TEST_PATH, "docs/pick-workbench/observe-runbook/**", "scripts/pick-deploy-guard.py"})

SELFCHECK_CONFIG = "/deploy/pick-obs/trends/selfcheck/railway.toml"
CRON_CONFIG = "/deploy/pick-obs/trends/railway.toml"
# S5 may name the old wrong egress name only to call it wrong; the pacing variable comes with G3's pacing fix, and
# S5 names it ahead of the trends entry reading it (the plan says the implementation decides).
WRONG_EGRESS = "PICK_OBS_EGRESS_URL"
PENDING = frozenset({"PICK_OBS_TRENDS_PACE"})
READ = frozenset({*trends_entry.TRENDS_VARIABLES, DATABASE_URL_VARIABLE, "PICK_DB_SIZE_CAP_BYTES"})
# A module the plan names, in full (ggwork_pick.observe.x) or under the package (observe.x).
MODULE_NAME = re.compile(r"(?<![\w.])(?:ggwork_pick|observe)(?:\.\w+)+")
MOVED_TO_S11 = "移到 S11"
# A clock-time range such as 02:00–20:00: section 10 leaves every deploy window to packaging.md section 7.
CLOCK_RANGE = re.compile(r"\d{1,2}:\d{2}\s*[–—-]\s*\d{1,2}:\d{2}")
S6_STEPS = "①②③④⑤"
SOURCE_ONLY = "只保证来源，不保证合同能力"
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
    *(f"两路的 {letter}" for letter in "ABCDE"),
)
# Who carries out a disposal in section 14.3: one of the three G3 fix groups, the integrator, or a task.
OWNERS = re.compile(r"G3 金丝雀修复|G3 D24 修复|G3 文档对齐|集成者|TR-\d+")
NO_ACTION = frozenset({"记录", "无需处置"})


def _between(text: str, start: str, end: str) -> str:
    head = text.index(start)
    return text[head : text.index(end, head + len(start))]


def _plan_section(number: int) -> str:
    return _between(PLAN.read_text(encoding="utf-8"), f"\n## {number}. ", f"\n## {number + 1}. ")


def _runbook(name: str) -> str:
    return (RUNBOOKS / name).read_text(encoding="utf-8")


def _row(text: str, key: str) -> str:
    (line,) = [line for line in text.splitlines() if line.startswith(f"| {key} |")]
    return line


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_after(text: str, marker: str) -> list[list[str]]:
    lines = text[text.index(marker) :].splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("|"))
    return [_cells(line) for line in takewhile(lambda line: line.startswith("|"), lines[start + 2 :])]


def _records(text: str, marker: str) -> list[dict[str, str]]:
    """The first table after marker, each row keyed by its header."""
    lines = text[text.index(marker) :].splitlines()
    start = next(index for index, line in enumerate(lines) if line.startswith("|"))
    header = _cells(lines[start])
    return [dict(zip(header, row, strict=True)) for row in _table_after(text, marker)]


def _code_after(text: str, marker: str) -> str:
    head = text.index("```sh\n", text.index(marker)) + len("```sh\n")
    return text[head : text.index("\n```", head)]


def _modules(text: str) -> set[str]:
    return {name if name.startswith("ggwork_pick.") else f"ggwork_pick.{name}" for name in MODULE_NAME.findall(text)}


def _ids(record: dict[str, str]) -> set[str]:
    return {part.strip() for part in record["编号"].split("、")}


def _four_cells() -> str:
    return _between(_runbook("rollback-matrix.md"), "## 四格验证", "\n## ")


def _selected_describe() -> str:
    """The one describe in api.test.ts that the frontend command's -t selects, up to the next describe."""
    (pattern,) = re.findall(r'-t "([^"]+)"', _four_cells())
    cases = API_TEST.read_text(encoding="utf-8")
    matching = [name for name in re.findall(r'^describe\("([^"]+)"', cases, re.MULTILINE) if pattern in name]
    assert len(matching) == 1 and matching[0].startswith(pattern)
    head = cases.index(f'describe("{matching[0]}"')
    later = re.search(r"^describe\(", cases[head + 1 :], re.MULTILINE)
    return cases[head : head + 1 + later.start()] if later else cases[head:]


def _rollback_rules() -> str:
    return _between(_plan_section(10), "**回滚规则**", "**回滚矩阵**").replace("**", "")


def _disposals() -> list[dict[str, str]]:
    plan = PLAN.read_text(encoding="utf-8")
    return _records(plan[plan.index("### 14.3 G3 处置") :], "| 编号 |")


def _guard_default_remote() -> str:
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    (value,) = [node.value.value for node in tree.body if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "DEFAULT_REMOTE"]
    return value


def test_s4_imports_only_modules_this_stage_ships():
    """S4 runs before batch 3: every module it names, in full or as observe.x, is in the package now (seam 4, layer
    P2-3); the only ones it may name otherwise are in the clause that moves them to S11, which checks them."""
    section = _plan_section(10)
    s11 = _row(section, "S11")
    clauses = re.split(r"[；。]", _row(section, "S4"))
    checked = set().union(*(_modules(clause) for clause in clauses if MOVED_TO_S11 not in clause))
    moved = set().union(*(_modules(clause) for clause in clauses if MOVED_TO_S11 in clause))
    assert checked, "S4 no longer checks any import"
    assert {module for module in checked if importlib.util.find_spec(module) is None} == set()
    assert "ggwork_pick.observe.read" in moved and all(module in s11 for module in moved)


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


def test_s6_deploys_the_selfcheck_then_the_cron():
    """S6's order (seam 6): guard once, deploy the self-check config, read its log, switch to the cron config from the
    same commit, then record the guard's line; never a manual trigger of the cron."""
    s6 = _row(_plan_section(10), "S6")
    assert [s6.count(step) for step in S6_STEPS] == [1] * len(S6_STEPS)
    assert [s6.index(step) for step in S6_STEPS] == sorted(s6.index(step) for step in S6_STEPS)
    steps = dict(zip(S6_STEPS, re.split(f"[{S6_STEPS}]", s6)[1:], strict=True))
    assert SELFCHECK_CONFIG in steps["②"] and "--selfcheck-only" in steps["②"]
    assert CRON_CONFIG in steps["④"] and "progress.md" in steps["⑤"]
    assert "手动触发" not in s6


def test_s6_takes_its_deploy_window_from_packaging():
    """G3 moves the session starts, and the deploy window with them (packaging.md section 7, pinned by
    test_railway_config.DEPLOY_WINDOW). Section 10 points there instead of writing hours of its own that go stale."""
    section = _plan_section(10)
    s6 = _row(section, "S6")
    assert "`packaging.md` 第 7 节" in s6 and "部署时段" in s6
    assert CLOCK_RANGE.findall(section) == []
    quiet_from, quiet_until = DEPLOY_WINDOW
    packaging_7 = _between(_runbook("packaging.md"), "\n## 7. ", "\n## 8. ")
    assert f"UTC {quiet_from:%H:%M}–{quiet_until:%H:%M}" in packaging_7


def test_s6a_gates_s7():
    """Seam 1: the no-HTTP preflight sits between S6 and S7, and S7 waits for it."""
    section = _plan_section(10)
    steps = [row[0] for row in _table_after(section, "| 步 |")]
    assert steps[steps.index("S6") : steps.index("S7") + 1] == ["S6", "S6a", "S7"]
    s6a = _row(section, "S6a")
    assert "preflight" in s6a and "不发 HTTP" in s6a
    assert "S6a 通过" in _row(section, "S7")


def test_deploy_source_is_the_guards_remote():
    """The guard fetches its default remote's main; this checkout's origin is upstream DeerFlow (layer P3-1)."""
    plan = PLAN.read_text(encoding="utf-8")
    places = {
        "section 10": _plan_section(10),
        "D41": _row(_plan_section(3), "D41"),
        "TR-34": _between(plan, "### TR-34 ", "\n### "),
        "deploy-guard.md": _runbook("deploy-guard.md"),
        "rollback-matrix.md": _runbook("rollback-matrix.md"),
    }
    assert [name for name, text in places.items() if "origin/main" in text] == []
    remote = _guard_default_remote()
    assert all(f"{remote}/main" in places[name] for name in ("section 10", "D41", "TR-34"))


def test_deploy_guard_sends_a_cron_deploy_through_packaging():
    """deploy-guard.md's cron line points at packaging.md's self-check-then-cron order (seam 6), not a manual trigger."""
    text = _runbook("deploy-guard.md")
    cron = next(line for line in _between(text, "## 各模式通过之后", "\n## ").splitlines() if line.startswith("- **cron**"))
    assert "packaging.md" in cron and SELFCHECK_CONFIG in cron and CRON_CONFIG in cron
    assert cron.index(SELFCHECK_CONFIG) < cron.index(CRON_CONFIG)


def test_source_and_contract_checks_are_written_apart():
    """Layer P2-4: the guard answers where a commit comes from, not whether it still reads every card; the plan's
    rollback rules, deploy-guard.md and rollback-matrix.md all say so."""
    assert SOURCE_ONLY in _rollback_rules() and SOURCE_ONLY in _runbook("deploy-guard.md").replace("**", "")
    split = {row[""]: row for row in _records(_runbook("rollback-matrix.md"), "## 来源检查与合同能力检查")}
    assert "合同能力" in split["不保证什么"]["来源检查"] and "来源" in split["不保证什么"]["合同能力检查"]


def test_s13_raises_the_floor_to_m1_under_the_four_cells():
    """Once S13 makes new cards, the gateway's floor is M1, checked like M0's: by the four cells, not by the guard."""
    s13 = _row(_plan_section(10), "S13")
    assert "M1" in s13 and "四格" in s13
    assert "S13 之后的 M1 下限按同样方法验证" in _rollback_rules()


def test_rollback_matrix_states_the_database():
    """Both copies of the matrix list the same combinations, with the database, and the layer review's verdicts."""
    plan = _table_after(_plan_section(10), "**回滚矩阵**")
    runbook = _table_after(_runbook("rollback-matrix.md"), "## 逐格表")
    assert [row[:2] for row in plan] == [row[:2] for row in runbook]
    for rows in (plan, runbook):
        verdicts = {key: [row[2].lstrip("*") for row in rows if key in row[1]] for key in MATRIX_CELLS}
        assert {key: [verdict[:1] for verdict in found] for key, found in verdicts.items()} == {key: [first] for key, first in MATRIX_CELLS.items()}


def test_four_cell_check_names_tests_that_exist():
    """rollback-matrix.md's four-cell check (layer P2-4) names the frontend's four cases and test files that exist."""
    section = _four_cells()
    assert set(re.findall(r"「(F1 x [^」]+)」", section)) == set(FOUR_CELLS)
    cases = API_TEST.read_text(encoding="utf-8")
    assert [name for name in FOUR_CELLS if f'it("{name}:' not in cases] == []
    files = set(re.findall(r"`([\w.-]+\.(?:py|test\.ts))`", section))
    assert files
    missing = [name for name in files if not any((ROOT / base).rglob(name) for base in ("customizations/pick-workbench/tests", "frontend/tests"))]
    assert missing == []


def test_four_cell_filter_selects_the_describe_holding_the_four_cases():
    """The frontend command's -t names the start of the one describe in api.test.ts that holds the four cases: renamed,
    it matches nothing, and rstest still exits 0 (the runbook says so), so CI has to catch it."""
    body = _selected_describe()
    assert [name for name in FOUR_CELLS if f'it("{name}:' not in body] == []


def test_four_cell_output_names_every_case_the_filter_selects():
    """-t selects the whole describe, so a case added beside the four cells (F1 x hot card, 2026-09-28) prints a row
    too: the runbook names every row, in the order rstest prints them, and the summary it asks for counts them."""
    cases = [name.partition(":")[0] for name in re.findall(r'^\s*it\("([^"]+)"', _selected_describe(), re.MULTILINE)]
    (expected,) = [line for line in _four_cells().splitlines() if "✓" in line]
    assert set(FOUR_CELLS) <= set(cases)
    assert re.findall(r"`(F1 x [^`]+)`", expected) == cases
    assert f"{len(cases)} 行 ✓" in expected
    assert set(re.findall(r"`Tests (\d+) passed", expected)) == {str(len(cases))}


def test_four_cell_gateway_command_shows_every_named_file():
    """The gateway column's files run under one command whose output shows each of them, with PostgreSQL on: without
    PICK_TEST_PG_URL their PostgreSQL half is skipped, and a skip is not a pass (layer P2-4)."""
    section = _four_cells()
    table = _records(section, "| 格 |")
    column = next(key for key in table[0] if key.startswith("gateway"))
    named = {name for row in table for name in re.findall(r"`(test_\w+\.py)`", row[column])}
    per_file = [line for line in _code_after(section, "**gateway 的命令**").splitlines() if "-v" in line.split()]
    assert named and len(per_file) == 1
    assert [name for name in sorted(named) if f"{GATEWAY_TESTS}/{name}" not in per_file[0]] == []
    assert "PICK_TEST_PG_URL=" in per_file[0] and "-rs" in per_file[0].split()
    assert "skipped" in _between(section, "**gateway 的命令**", "**cron 的部署**")


def test_four_cell_results_go_in_with_the_guard_record():
    """The guard refuses a dirty tree, so the four cells' results reach progress.md after the deploy, with the guard's
    line, not before the guard runs."""
    sentences = [sentence for sentence in re.split(r"[。；]", _four_cells()) if "progress.md" in sentence]
    assert sentences and any("之后" in sentence and "与守卫记录行一起" in sentence for sentence in sentences)


def test_s0_reruns_on_the_merged_sha():
    """Seam, S0: the managed copy is refreshed after merging main and the checks rerun on the final common SHA."""
    s0 = _row(_plan_section(10), "S0")
    assert "deerflow extensions upgrade" in s0 and "test_managed_copy" in s0 and "c095fe4" in s0
    assert "在最终共同 SHA 上重跑" in s0


def test_release_gates_follow_the_step_order():
    """The final common SHA exists only once S0 has merged into ggwork/main: S0 waits for the merge into
    feat/trends-radar, S1 for the rerun on that SHA (14.3's release rule and S1 alike)."""
    plan = PLAN.read_text(encoding="utf-8")
    rule = _between(plan[plan.index("### 14.3 G3 处置") :], "**放行口径**", "\n")
    sentences = re.split(r"[；。]", rule)
    assert [sentence for sentence in sentences if "才开始 S0" in sentence and "最终共同 SHA" in sentence] == []
    assert any("最终共同 SHA" in sentence and "才进 S1" in sentence for sentence in sentences)
    assert "最终共同 SHA" in _row(_plan_section(10), "S1")


def test_section_14_3_disposes_of_every_g3_finding():
    """Plan 14.3 lists each numbered finding of both G3 reviews in a row's first cell, and 12.4's G3 row points at
    it. A number that shows up only in some other row's text does not count."""
    listed = set().union(*(_ids(record) for record in _disposals()))
    assert [finding for finding in FINDINGS if finding not in listed] == []
    assert "14.3" in _row(_plan_section(12), "G3")


def test_every_g3_disposal_names_who_does_it():
    """A disposal nobody owns is not done (e.g. layer P3-2's packaging.md wording, the market phrases' freeze)."""
    acting = [record for record in _disposals() if record["处置"] not in NO_ACTION]
    assert acting and [record["编号"] for record in acting if not OWNERS.search(record["负责"])] == []


def test_14_3_gates_are_prerequisites_of_their_steps():
    """What 14.3 gates at S0 or S7 is in that step's prerequisites in section 10."""
    section = _plan_section(10)
    records = _disposals()
    assert any("S0 前" in record["门槛"] for record in records) and "第 14.3 节标「S0 前」" in _row(section, "S0")
    before_s7 = [record for record in records if "S7 前" in record["门槛"]]
    s7 = _row(section, "S7")
    assert before_s7 and [record["编号"] for record in before_s7 if not any(name in s7 for name in _ids(record))] == []


def test_window_spacing_after_a_start_change_is_disposed():
    """Layer A: on the first night after a start time changes, window_end moves by other than 24 hours; confirmed needs
    20-28 hours (design 4.9 item 6), so that night must not auto-confirm. TR-17 owns it, by G4."""
    (record,) = [record for record in _disposals() if "20–28 小时" in record["落点"]]
    assert "TR-17" in record["负责"] and "G4 前" in record["门槛"] and "改起跑时刻后的第一天" in record["门槛"]


def test_ci_runs_on_the_files_these_tests_read():
    """An edit to the plan or the frontend's four cases alone still runs these tests."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads a bare `on` as true
    assert {event: sorted(READ_PATHS - set(triggers[event]["paths"])) for event in ("push", "pull_request")} == {"push": [], "pull_request": []}
