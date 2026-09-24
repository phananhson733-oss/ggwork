"""docs/pick-workbench/mirror-dry-run.md, the P1-6 handbook, against the dry-run it describes (no database needed).

The handbook is read once, on the day of the measurement, by someone who cannot ask the code: every output field the
dry-run prints, every exit code, gate threshold and title field it names, and every flag in its command come from
dry_run itself here, so a change on either side fails a test instead of misleading that reader.
"""

import asyncio
import io
import json
import re
from pathlib import Path

import pytest
from fake_realshort import BYPASS, EXPORT_TOKEN, FEED_TOKEN, Clock, FakeRealShort

from ggwork_pick.mirror import dry_run
from ggwork_pick.mirror.errors import FeedError
from ggwork_pick.mirror.feed_shape import PageMetrics

DOC = Path(__file__).resolve().parents[4] / "docs/pick-workbench/mirror-dry-run.md"
ENTRY = "PYTHONPATH=customizations/pick-workbench backend/.venv/bin/python -m ggwork_pick.mirror.client --dry-run"
EXIT_CODES = {dry_run.EXIT_OK, dry_run.EXIT_GATES, dry_run.EXIT_USAGE, dry_run.EXIT_FETCH, dry_run.EXIT_INTERNAL}
_EXIT_ROW = re.compile(r"^\| *([0-9]+) *\|", re.MULTILINE)
_FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")
_URL = re.compile(r"https?://[^\s)`'\"]+")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{24,}")


@pytest.fixture(scope="module")
def doc() -> str:
    return DOC.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _full_run_summary(tmp_path, *, v1: bool = True) -> dict:
    """A clean --scan run against the RealShort double: with v1, every key a passing summary carries; without, the
    pan_scan gate's keys for a v1 it could not scan."""
    folder = tmp_path / ("secrets" if v1 else "secrets-no-v1")
    folder.mkdir()
    (folder / "export").write_text(EXPORT_TOKEN)
    (folder / "bypass").write_text(BYPASS)
    clock = Clock()
    fake = FakeRealShort(now=clock, bypass=BYPASS)
    out = io.StringIO()
    argv = ["--dry-run", "--base-url", "https://realshort.test", "--token-file", str(folder / "export")]
    argv = [*argv, "--bypass-header-file", str(folder / "bypass"), "--scan"]
    options = dry_run.parse_args(argv)
    secrets = dry_run.load_secrets(options, {dry_run.FEED_TOKEN_ENV: FEED_TOKEN} if v1 else {})
    code = asyncio.run(dry_run.dry_run(options, secrets, transport=fake.transport(), clock=clock, sleep=clock.sleep, timer=clock.timer, out=out))
    assert code == (dry_run.EXIT_OK if v1 else dry_run.EXIT_GATES)
    return json.loads(out.getvalue().splitlines()[-1])


def _summary_keys(summary: dict) -> set[str]:
    nested = ("retries", "gates", "scan", "v1", "manifest")
    keys = set(summary) - {"summary"}
    keys |= {key for name in nested for key in summary[name]}
    keys |= {key for stats in summary["resources"].values() for key in stats}
    keys |= {key for gate in summary["gates"].values() for key in gate}
    return keys


def _page_line_keys() -> set[str]:
    metrics = PageMetrics("rs_series_day", 1, 503, 1.0, 1, 1, None, 5, attempt=2, error="read_failed", day="2026-09-23")
    return {"attempt", "run", *metrics.line()}


def _failure_keys() -> set[str]:
    failed = dry_run.Outcome(runs=1, error=FeedError("x"))
    keys = set(dry_run.failure_summary(failed, dry_run.Recorder(io.StringIO())))
    return (keys | set(dry_run.internal_summary(ValueError()))) - {"summary"}


def test_the_handbook_names_every_output_field(doc, tmp_path):
    output = _section(doc, "输出")
    keys = _page_line_keys() | _summary_keys(_full_run_summary(tmp_path)) | set(_full_run_summary(tmp_path, v1=False)["gates"]["pan_scan"])
    missing = sorted(key for key in keys | _failure_keys() if f"`{key}`" not in output)
    assert not missing, f"输出一节没写到这些字段：{missing}"


def test_the_handbook_exit_codes_are_the_dry_runs(doc):
    rows = [int(code) for code in _EXIT_ROW.findall(_section(doc, "退出码"))]
    assert sorted(rows) == sorted(EXIT_CODES) and len(rows) == len(set(rows))


def test_the_handbook_thresholds_and_title_fields_are_the_dry_runs(doc):
    thresholds = _section(doc, "门槛")
    assert f"{dry_run.PAGE_MS_LIMIT // 1000} 秒" in thresholds
    assert f"{dry_run.PAGE_BYTES_LIMIT:,} 字节" in thresholds
    assert f"{dry_run.RUN_MS_LIMIT // 60_000} 分钟" in thresholds
    assert all(f"`{field}`" in thresholds for field in dry_run.TITLE_SCRUB_FIELDS)
    assert dry_run.BLOCKS_67 in thresholds
    assert f"最多重来 {dry_run.RERUNS} 次" in doc


def test_the_handbook_command_uses_only_the_dry_runs_flags(doc):
    options = {option for action in dry_run.build_parser()._actions for option in action.option_strings}
    command = _section(doc, "命令")
    assert ENTRY in " ".join(command.replace("\\\n", " ").split())
    flags = set(_FLAG.findall(command))
    assert flags <= options, f"命令一节有 dry-run 不认的参数：{sorted(flags - options)}"
    assert {"--base-url", "--bypass-header-file", "--token-file", "--v1-token-file", "--scan", "--limit"} <= flags


def test_the_pre_check_bodies_are_realshorts_gate_answers(doc):
    # rs:src/lib/pick/feed-http.ts gate(): v2 answers {ok:false, version, error}; the curl pre-check tells them from a
    # deployment-protection page by that body, since both may be a 401.
    from ggwork_pick.mirror.client import ERROR_WORDS
    from ggwork_pick.mirror.contracts import EXPORT_VERSION

    for word in ("unauthorized", "not_found"):
        assert word in ERROR_WORDS
        assert json.dumps({"ok": False, "version": EXPORT_VERSION, "error": word}, separators=(",", ":")) in doc


def test_the_handbook_holds_placeholders_not_values(doc):
    assert not _HEX_RUN.search(doc), "手册里不应有像 token 的十六进制串"
    real = [url for url in _URL.findall(doc) if "<" not in url]
    assert not real, f"手册里的地址都应是占位符：{real}"
    assert "Bearer <" in doc or "Bearer" not in doc
