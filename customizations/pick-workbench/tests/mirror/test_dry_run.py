"""python -m ggwork_pick.mirror.client --dry-run: the P1-6 measurement (plan 1486-1496; P2-2a tests 15, 18, 19).

In process through dry_run.main with the RealShort double on httpx.MockTransport, and once for real: a subprocess
running the -m entry against the double on a loopback http.server, with no database, no app config and no DEER_FLOW_*.
"""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fake_realshort import BYPASS, EXPORT_TOKEN, FEED_TOKEN, ROW_SENTINEL, Clock, FakeRealShort, busy, serve, v2_error

from ggwork_pick.mirror import dry_run

EXTENSION_ROOT = Path(__file__).resolve().parents[2]
EXTENSION_API = Path(__file__).resolve().parents[4] / "backend/packages/extension-api"
BASE = "https://realshort.test"
# The brief's P2-2a page line: attempt (the run, also printed as run), the eight metrics, retried (the read_failed retry).
METRIC_KEYS = {"attempt", "resource", "page", "status", "elapsed_ms", "bytes", "wire_bytes", "rows", "retry_after", "retried"}
SECRETS = (EXPORT_TOKEN, FEED_TOKEN, BYPASS, ROW_SENTINEL)


@pytest.fixture
def files(tmp_path):
    folder = tmp_path / "secrets"
    folder.mkdir()
    paths = {"token": folder / "export", "v1": folder / "feed", "bypass": folder / "bypass"}
    for key, value in (("token", EXPORT_TOKEN), ("v1", FEED_TOKEN), ("bypass", BYPASS)):
        paths[key].write_text(value + "\n")
    return paths


def run(capsys, fake, clock, *extra, env=None, files=None, bypass=True):
    argv = ["--dry-run", "--base-url", BASE, *extra]
    if files is not None:
        argv = [*argv, "--token-file", str(files["token"]), *(["--bypass-header-file", str(files["bypass"])] if bypass else [])]
    code = dry_run.main(argv, env=env or {}, transport=fake.transport(), clock=clock, sleep=clock.sleep, timer=clock.timer)
    out, err = capsys.readouterr()
    lines = [json.loads(line) for line in out.splitlines()]
    return code, lines, out + err


def world(**options):
    clock = Clock()
    return FakeRealShort(now=clock, bypass=BYPASS, **options), clock


def summary_of(lines):
    *pages, summary = lines
    assert summary["summary"] is True and all("summary" not in line for line in pages)
    return pages, summary


def test_dry_run_prints_metrics_not_rows(capsys, files):
    fake, clock = world(sizes={"catalog_rows": 3}, page_rows={"catalog_rows": 2}, v1_rows=3)
    code, lines, text = run(capsys, fake, clock, env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    assert code == 0, lines[-1]
    pages, summary = summary_of(lines)
    assert all(METRIC_KEYS <= set(line) and line["run"] == line["attempt"] == 1 and line["retried"] is False for line in pages)
    assert [(p["resource"], p["page"]) for p in pages][:4] == [("manifest", 1), ("v1", 1), ("catalog_rows", 1), ("catalog_rows", 2)]
    assert [p.get("day") for p in pages if p["resource"] == "rs_series_day"] == ["2026-09-23"]
    for secret in SECRETS:
        assert secret not in text
    measured = {"bytes": 0, "wire_bytes": 0, "max_bytes": 0, "max_elapsed_ms": 0, "sum_elapsed_ms": 0}
    assert summary["resources"]["catalog_rows"] | measured == {
        "pages": 2,
        "rows": 3,
        **measured,
        "expected_rows": 3,
        "rows_match": True,
    }
    assert set(summary["resources"]) == set(dry_run.COUNTED_RESOURCES)
    assert summary["series_days"]["2026-09-23"]["rows"] == 3 and summary["series_days"]["2026-09-23"]["rows_match"]
    assert summary["v1"]["rows"] == summary["v1"]["total"] == 3 and summary["v1"]["rows_match_total"] is True
    assert summary["manifest"]["bytes"] > 0 and "elapsed_ms" in summary["manifest"]
    assert summary["as_of"] == "2026-09-23T12:32:00.000Z" and summary["source_revision_null"] is False
    assert summary["scrub"] == {} and summary["warnings"] == [] and summary["failed_gates"] == [] and summary["ok"] is True
    counters = ("drift_409", "busy_503", "manifest_busy_503", "busy_wait_seconds", "read_failed_503", "read_failed_retries", "as_of_expired", "reruns")
    assert summary["retries"] == {**dict.fromkeys(counters, 0), "causes": []}
    assert {c.headers.get("x-vercel-protection-bypass") for c in fake.calls} == {BYPASS}


def test_dry_run_reports_warning_codes_and_a_null_revision_fails_its_gate(capsys, files):
    # Warnings and non-title scrub hits are reported only; a null sourceRevision is a gate since P1-6 (2026-09-24).
    warning = {"code": "catalog_import_incomplete", "source": "pick_catalog", "status": "failed", "attemptedAt": f"{ROW_SENTINEL}-when"}
    fake, clock = world(sha=None, warnings=[warning], scrub={"catalog_rows.reoff_note": 3, "rs_rows.tag_list[*]": 1})
    code, lines, text = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["failed_gates"] == ["source_revision"]
    assert summary["warnings"] == ["catalog_import_incomplete"] and summary["source_revision_null"] is True
    assert summary["scrub"] == {"catalog_rows.reoff_note": 3, "rs_rows.tag_list[*]": 1} and summary["gates"]["title_scrub"]["ok"] is True
    assert ROW_SENTINEL not in text and "pick_catalog" not in text


def test_dry_run_skips_v1_without_token(capsys, files):
    fake, clock = world()
    code, lines, _ = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and summary["v1"]["skipped"] is True and "PICK_REALSHORT_FEED_TOKEN" in summary["v1"]["reason"]
    assert [c for c in fake.calls if c.resource == "v1"] == []


@pytest.mark.parametrize(
    "path",
    ["catalog_rows.title", "rs_rows.title_cn", "rs_rows.description", "rs_ids.title", "catalog_posted.title", "rs_bill_orders.book_title"],
)
def test_title_scrub_hits_fail_and_block_67(capsys, files, path):
    # Critique 1.2: a title-like hit blocks merging realshort#67, not only P2 (plan 1494).
    fake, clock = world(scrub={path: 2, "catalog_rows.reoff_note": 3})
    code, lines, _ = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    gate = summary["gates"]["title_scrub"]
    assert code == 1 and summary["ok"] is False and "title_scrub" in summary["failed_gates"]
    assert gate == {"ok": False, "hits": {path: 2}, "blocks": "阻断 #67 合并"}


def test_the_six_title_fields_that_block_67_are_pinned():
    # The brief's P2-2a and P1 step 5: exactly these six meta.scrub fields; "*" stands for any row resource's name.
    fields = ("*.title", "*.title_cn", "*.description", "rs_ids.title", "catalog_posted.title", "rs_bill_orders.book_title")
    assert dry_run.TITLE_SCRUB_FIELDS == fields
    from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS

    # Each one names a real text column: meta.scrub counts toExportRow's hits as "<resource>.<column>".
    text_columns = {(resource, c.name) for resource, columns in RESOURCE_COLUMNS.items() for c in columns if c.type == "text"}
    for field in fields:
        resource, _, column = field.partition(".")
        assert any(c == column and resource in ("*", r) for r, c in text_columns), field


@pytest.mark.parametrize(
    "path",
    ["catalog_posted.posts[*].md.title", "rs_rows.tag_list[*]", "catalog_rows.reoff_note", "catalog_rows.book_title", "manifest.title", "title"],
)
def test_other_scrub_fields_do_not_block_67(capsys, files, path):
    fake, clock = world(scrub={path: 2})
    code, lines, _ = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and summary["gates"]["title_scrub"] == {"ok": True, "hits": {}} and summary["scrub"] == {path: 2}


PAN = "资源 https://pan.baidu.com/s/1AbCdEf 提取码：ab12"
PAN_PIECES = ("pan.baidu.com", "1AbCdEf", "ab12")


def _plant_pan(fake):
    """A pan fragment on one leaf each of a v1 row, the v1 rules, a v2 row and manifest.meta, and on exempt fields."""
    key, row = fake.v1[0]
    signal = {"kind": "kd", "label": "x", "source_ref": PAN, "observed_at": PAN, "rank": None, "grade": "", "note": "x"}
    posted = {**row["posted"], "last_post_on": PAN}
    fake.v1[0] = (key, {**row, "title": PAN, "source_id": PAN, "detail_url": PAN, "listed_at": PAN, "signals": [signal], "posted": posted})
    fake.data["catalog_rows"][0] = {**fake.data["catalog_rows"][0], "reoff_note": PAN, "in_site_ids": [PAN], "listed_on": PAN}
    original_v1 = fake._v1_page

    def v1_page(cursor, limit, as_of):
        status, headers, body = original_v1(cursor, limit, as_of)
        page = json.loads(body)
        return status, headers, json.dumps({**page, "rules": f"# 规则\n{PAN}"} if page["rules"] else page).encode()

    fake._v1_page = v1_page

    def meta_pan(page):
        page["rows"][0]["meta"]["rules"]["postedPoolUrl"] = PAN
        return page

    _patch_manifest(fake, meta_pan)


def test_dry_run_scan_reports_paths_only(capsys, files):
    # The brief's P2-2a test 24 (U20, U45, U52): paths and counts, never the value; any hit exits non-zero.
    fake, clock = world()
    _plant_pan(fake)
    code, lines, text = run(capsys, fake, clock, "--scan", env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["ok"] is False and summary["failed_gates"] == ["pan_scan"]
    hits = {"v1.rows[*].title": 1, "v1.rules": 1, "catalog_rows.reoff_note": 1, "manifest.meta.rules.postedPoolUrl": 1}
    # Exempt fields (v1 source_id, detail_url, listed_at, signals[*].source_ref / observed_at, posted.last_post_on;
    # v2 in_site_ids and listed_on) hold the same fragment and count nothing.
    assert summary["scan"]["hits"] == hits and summary["scan"]["total"] == 4
    assert summary["gates"]["pan_scan"] == {"ok": False, "paths": 4, "hits": 4}
    for piece in PAN_PIECES:
        assert piece not in text


def test_dry_run_scan_without_hits_exits_0(capsys, files):
    fake, clock = world()
    code, lines, _ = run(capsys, fake, clock, "--scan", env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and summary["ok"] is True and summary["scan"]["hits"] == {} and summary["scan"]["total"] == 0
    assert summary["gates"]["pan_scan"] == {"ok": True, "paths": 0, "hits": 0}


def test_dry_run_scan_never_prints_a_key_that_holds_a_pan_link(capsys, files):
    # meta.rules.ruleHints takes any key (rec(SCALAR), finding 7): a pan link used as a key passes the contract, and
    # its value hits. The path shows where, the key itself is masked.
    link = "https://pan.baidu.com/s/1AbCdEf"
    fake, clock = world()
    _patch_manifest(fake, lambda page: (page["rows"][0]["meta"]["rules"].update(ruleHints={link: PAN}), page)[1])
    code, lines, text = run(capsys, fake, clock, "--scan", env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["scan"]["hits"] == {"manifest.meta.rules.ruleHints.<非常规键名>": 1}
    for piece in PAN_PIECES:
        assert piece not in text


def test_scan_without_v1_fails_the_pan_scan_gate(capsys, files):
    # P1 step 7 wants every path at 0, v1.rules included (U45): a --scan that never read v1 cannot vouch for it, so the
    # gate fails and names what it did not scan, even when the v2 pages and manifest.meta are clean.
    fake, clock = world()
    code, lines, _ = run(capsys, fake, clock, "--scan", files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["ok"] is False and summary["failed_gates"] == ["pan_scan"] and summary["v1"]["skipped"] is True
    assert summary["scan"]["hits"] == {} and [c for c in fake.calls if c.resource == "v1"] == []
    gate = summary["gates"]["pan_scan"]
    assert gate["ok"] is False and gate["unscanned"] == ["v1.rows", "v1.rules"] and (gate["paths"], gate["hits"]) == (0, 0)
    assert "--v1-token-file" in gate["reason"]


def test_scan_without_v1_still_reports_what_it_found(capsys, files):
    fake, clock = world()
    _plant_pan(fake)
    code, lines, text = run(capsys, fake, clock, "--scan", files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["scan"]["hits"] == {"catalog_rows.reoff_note": 1, "manifest.meta.rules.postedPoolUrl": 1}
    assert summary["gates"]["pan_scan"]["unscanned"] == ["v1.rows", "v1.rules"] and summary["gates"]["pan_scan"]["hits"] == 2
    for piece in PAN_PIECES:
        assert piece not in text


def test_without_scan_nothing_is_scanned(capsys, files, monkeypatch):
    from ggwork_pick.mirror import pan

    def refuse(*args):
        raise AssertionError("scanned without --scan")

    for name in ("scan_v1_page", "scan_v2_page", "scan_manifest_meta"):
        monkeypatch.setattr(pan, name, refuse)
    fake, clock = world()
    _plant_pan(fake)
    code, lines, _ = run(capsys, fake, clock, env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and "scan" not in summary and "pan_scan" not in summary["gates"]


def test_scan_runs_off_the_event_loop(capsys, files, monkeypatch):
    # 0.3: re and unicodedata hold the GIL; the gateway has one event loop, so every page's scan runs in a worker thread.
    import threading

    from ggwork_pick.mirror import pan

    threads = []

    def spying(function):
        def spy(*args):
            threads.append(threading.current_thread() is threading.main_thread())
            return function(*args)

        return spy

    for name in ("scan_v1_page", "scan_v2_page", "scan_manifest_meta"):
        monkeypatch.setattr(pan, name, spying(getattr(pan, name)))
    fake, clock = world()
    code, lines, _ = run(capsys, fake, clock, "--scan", env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    pages, _ = summary_of(lines)
    assert code == 0 and len(threads) == len(pages) and not any(threads)


def test_scan_time_is_kept_out_of_run_ms(capsys, files, monkeypatch):
    # run_ms measures RealShort for the run_time gate; the scan's worker-thread time is reported apart, as scan.elapsed_ms.
    from ggwork_pick.mirror import pan

    v1 = {"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}
    fake, clock = world()
    _slow(fake, clock, 1, {"rs_ids"})
    code, lines, _ = run(capsys, fake, clock, env=v1, files=files)
    _, plain = summary_of(lines)
    assert code == 0 and plain["run_ms"] == 1000.0

    fake, clock = world()
    _slow(fake, clock, 1, {"rs_ids"})
    scanners = {name: getattr(pan, name) for name in ("scan_v2_page", "scan_manifest_meta")}

    def slowed(name, seconds):
        def scan(*args):
            clock.advance(seconds)
            return scanners[name](*args)

        return scan

    monkeypatch.setattr(pan, "scan_v2_page", slowed("scan_v2_page", 2))
    monkeypatch.setattr(pan, "scan_manifest_meta", slowed("scan_manifest_meta", 3))
    code, lines, _ = run(capsys, fake, clock, "--scan", env=v1, files=files)
    pages, summary = summary_of(lines)
    v2_pages = len([p for p in pages if p["resource"] not in ("manifest", "v1")])
    assert code == 0 and v2_pages > 1 and summary["scan"]["elapsed_ms"] == 3000.0 + 2000.0 * v2_pages
    assert summary["run_ms"] == plain["run_ms"] and summary["gates"]["run_time"]["run_ms"] == 1000.0


def test_row_count_mismatch_fails(capsys, files):
    fake, clock = world()
    fake.counts = {**{name: len(rows) for name, rows in fake.data.items()}, "rs_ids": 3}
    code, lines, _ = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 1 and summary["gates"]["row_counts"] == {"ok": False, "mismatched": ["rs_ids"]}
    assert summary["resources"]["rs_ids"]["rows_match"] is False and summary["resources"]["rs_ids"]["expected_rows"] == 3


def test_series_day_count_mismatch_fails(capsys, files):
    fake, clock = world(series={"2026-09-23": 2})
    original = fake._manifest

    def more_promised(as_of):
        status, headers, body = original(as_of)
        page = json.loads(body)
        page["rows"][0]["snapshotDays"] = [{"day": "2026-09-23", "rows": 3}]
        return status, headers, json.dumps(page).encode()

    fake._manifest = more_promised
    code, lines, _ = run(capsys, fake, clock, files=files)
    assert code == 1 and lines[-1]["gates"]["row_counts"]["mismatched"] == ["rs_series_day@2026-09-23"]


def _slow(fake, clock, seconds, resources):
    original = fake._rows

    def slow(name, params, as_of):
        clock.advance(seconds if name in resources else 0)
        return original(name, params, as_of)

    fake._rows = slow


def test_slow_page_fails_the_page_time_gate(capsys, files):
    fake, clock = world()
    _slow(fake, clock, 15, {"rs_ids"})
    code, lines, _ = run(capsys, fake, clock, files=files)
    gate = lines[-1]["gates"]["page_time"]
    assert code == 1 and gate["ok"] is False and gate["worst_ms"] == 15000.0 and gate["worst"] == "rs_ids"
    assert lines[-1]["gates"]["run_time"]["ok"] is True


def _patch_manifest(fake, change):
    original = fake._manifest

    def patched(as_of):
        status, headers, body = original(as_of)
        return status, headers, json.dumps(change(json.loads(body))).encode()

    fake._manifest = patched


def test_a_slow_manifest_is_judged_by_manifest_time_not_page_time(capsys, files):
    # The manifest runs thirteen queries at once (rs:src/lib/pick/export-v2.ts:455-469): P1-6 measured 22-25 s, and the
    # user gave it its own 45-second gate; 15 s, a row page's limit, passes (test_mirror_dry_run_gates.py has the rest).
    fake, clock = world()
    _patch_manifest(fake, lambda page: (clock.advance(15), page)[1])
    code, lines, _ = run(capsys, fake, clock, files=files)
    gates = lines[-1]["gates"]
    assert code == 0 and gates["page_time"]["ok"] is True and gates["page_time"]["worst"] != "manifest"
    assert gates["manifest_time"] == {"ok": True, "limit_ms": 45000, "elapsed_ms": 15000.0}
    assert lines[-1]["manifest"]["elapsed_ms"] == 15000.0


def test_large_manifest_fails_the_page_bytes_gate(capsys, files):
    fake, clock = world()

    def pad(page):
        # meta.rules.ruleHints is rec(SCALAR) in MANIFEST_SHAPE: any key, a string value; the manifest stays in contract.
        page["rows"][0]["meta"]["rules"]["ruleHints"] = {"pad": "x" * 3_000_000}
        return page

    _patch_manifest(fake, pad)
    code, lines, text = run(capsys, fake, clock, files=files)
    gate = lines[-1]["gates"]["page_bytes"]
    assert code == 1 and gate["ok"] is False and gate["worst"] == "manifest" and gate["worst_bytes"] > 3_000_000
    assert "xxxxxxxx" not in text


def test_a_page_of_exactly_3000000_bytes_fails(capsys, files):
    # Plan 1491: under 3,000,000 bytes; exactly that many is not under.
    fake, clock = world()
    original = fake._rows

    def exact(name, params, as_of):
        status, headers, body = original(name, params, as_of)
        if name != "rs_rows":
            return status, headers, body
        page = json.loads(body)
        page["rows"][0]["description"] = ""
        short = len(json.dumps(page, separators=(",", ":")).encode())
        page["rows"][0]["description"] = "x" * (3_000_000 - short)
        return status, headers, json.dumps(page, separators=(",", ":")).encode()

    fake._rows = exact
    code, lines, _ = run(capsys, fake, clock, files=files)
    gate = lines[-1]["gates"]["page_bytes"]
    assert code == 1 and gate == {"ok": False, "limit_bytes": 3_000_000, "worst_bytes": 3_000_000, "worst": "rs_rows"}


def test_a_run_of_exactly_three_minutes_fails(capsys, files):
    # Eighteen row pages of ten seconds each: every page passes, the run is exactly 180 seconds and is not under.
    fake, clock = world(sizes={"catalog_rows": 10}, page_rows={"catalog_rows": 1})
    _slow(fake, clock, 10, set(dry_run.COUNTED_RESOURCES) | {"rs_series_day"})
    code, lines, _ = run(capsys, fake, clock, files=files)
    gates = lines[-1]["gates"]
    assert len([c for c in fake.calls if c.resource != "manifest"]) == 18
    assert code == 1 and gates["page_time"]["ok"] is True
    assert gates["run_time"] == {"ok": False, "limit_ms": 180_000, "run_ms": 180_000.0}


def test_long_run_fails_the_run_time_gate(capsys, files):
    fake, clock = world(sizes={"catalog_rows": 6}, page_rows={"catalog_rows": 1})
    _slow(fake, clock, 14.9, set(dry_run.COUNTED_RESOURCES) | {"rs_series_day"})
    code, lines, _ = run(capsys, fake, clock, files=files)
    gates = lines[-1]["gates"]
    assert code == 1 and gates["page_time"]["ok"] is True and gates["run_time"]["ok"] is False
    assert gates["run_time"]["run_ms"] > 180000


def test_large_page_fails_the_page_bytes_gate(capsys, files):
    fake, clock = world()
    original = fake._rows

    def large(name, params, as_of):
        status, headers, body = original(name, params, as_of)
        if name != "rs_rows":
            return status, headers, body
        page = json.loads(body)
        page["rows"][0]["description"] = "x" * 3_000_000
        return status, headers, json.dumps(page).encode()

    fake._rows = large
    code, lines, text = run(capsys, fake, clock, files=files)
    gate = lines[-1]["gates"]["page_bytes"]
    assert code == 1 and gate["ok"] is False and gate["worst"] == "rs_rows" and gate["worst_bytes"] > 3_000_000
    assert "xxxxxxxx" not in text


def test_drift_reruns_and_counts(capsys, files):
    def intercept(call):
        if call.resource == "manifest" and call.n == 1:
            return busy()
        if call.resource == "catalog_rows" and call.n == 1:
            return v2_error(409, "source_changed")
        if call.resource == "rs_ids" and call.n == 1:
            return busy()
        return None

    fake, clock = world(intercept=intercept)
    code, lines, _ = run(capsys, fake, clock, files=files)
    pages, summary = summary_of(lines)
    assert code == 0 and summary["ok"] is True
    assert summary["retries"] == {
        "drift_409": 1,
        "busy_503": 2,
        "manifest_busy_503": 1,
        "busy_wait_seconds": 60,
        "read_failed_503": 0,
        "read_failed_retries": 0,
        "as_of_expired": 0,
        "reruns": 2,
        "causes": [
            {"run": 1, "cause": "drift_409", "side": "v2", "resource": "catalog_rows"},
            {"run": 2, "cause": "drift_busy_503", "side": "v2", "resource": "rs_ids"},
        ],
    }
    assert clock.sleeps == [60, 90, 90]
    assert {line["run"] for line in pages} == {1, 2, 3} and all(line["attempt"] == line["run"] for line in pages)
    assert [(p["resource"], p["status"], p["run"]) for p in pages if p["status"] != 200] == [
        ("manifest", 503, 1),
        ("catalog_rows", 409, 1),
        ("rs_ids", 503, 2),
    ]
    # Three reruns' worth of waiting (60 + 90 + 90 seconds from 12:34:56) moved as_of to 12:36.
    assert summary["as_of"] == "2026-09-23T12:36:00.000Z"


def test_drift_gives_up_after_two_reruns(capsys, files):
    # The brief's P2-2a dry-run: start over from the manifest at most twice; the third 409 exits non-zero (test 23).
    assert dry_run.RERUNS == 2
    fake, clock = world(intercept=lambda call: v2_error(409, "source_changed") if call.resource == "catalog_rows" else None)
    code, lines, text = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 3 and summary["ok"] is False and summary["error_type"] == "DriftError"
    assert summary["retries"]["drift_409"] == 3 and summary["retries"]["reruns"] == 2 and summary["runs"] == 3
    assert "409" in summary["error"] and EXPORT_TOKEN not in text


def test_read_failed_retry_is_marked_on_its_line_and_counted(capsys, files):
    fake, clock = world(intercept=lambda call: v2_error(503, "read_failed") if call.resource == "rs_ids" and call.n == 1 else None)
    code, lines, _ = run(capsys, fake, clock, files=files)
    pages, summary = summary_of(lines)
    assert code == 0 and summary["ok"] is True
    assert [(p["status"], p["retried"]) for p in pages if p["resource"] == "rs_ids"] == [(503, False), (200, True)]
    assert (summary["retries"]["read_failed_503"], summary["retries"]["read_failed_retries"]) == (1, 1) and clock.sleeps == [5]


def test_read_failed_twice_is_two_responses_one_retry_and_a_fetch_failure(capsys, files):
    # read_failed_503 counts responses, read_failed_retries the repeated requests: they part when the retry fails too.
    fake, clock = world(intercept=lambda call: v2_error(503, "read_failed") if call.resource == "rs_ids" else None)
    code, lines, _ = run(capsys, fake, clock, files=files)
    pages, summary = summary_of(lines)
    assert code == 3 and summary["ok"] is False and summary["error_type"] == "SourceReadError"
    assert [(p["status"], p["retried"]) for p in pages if p["resource"] == "rs_ids"] == [(503, False), (503, True)]
    assert (summary["retries"]["read_failed_503"], summary["retries"]["read_failed_retries"]) == (2, 1) and clock.sleeps == [5]
    assert summary["retries"]["reruns"] == 0 and summary["retries"]["causes"] == []


def test_rerun_causes_name_the_echo_and_the_side(capsys, files):
    # v1 answering for another build (plan 5.2 step 5) is drift without any 409; RealShort's 400 as_of is expiry.
    reply = v2_error(400, "bad_request", reason="as_of")
    fake, clock = world(intercept=lambda call: reply if call.resource == "rs_ids" and call.n == 1 else None)
    original, served = fake._v1_page, []

    def v1_page(cursor, limit, as_of):
        status, headers, body = original(cursor, limit, as_of)
        served.append(cursor)
        page = json.loads(body)
        return status, headers, json.dumps({**page, "sourceRevision": "sha-other"} if len(served) == 1 else page).encode()

    fake._v1_page = v1_page
    code, lines, _ = run(capsys, fake, clock, env={"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and summary["retries"]["drift_409"] == 0 and summary["retries"]["as_of_expired"] == 1
    assert summary["retries"]["causes"] == [
        {"run": 1, "cause": "drift_echo", "side": "v1", "resource": "v1"},
        {"run": 2, "cause": "as_of_expired_400", "side": "v2", "resource": "rs_ids"},
    ]


def test_local_as_of_expiry_is_its_own_cause(capsys, files):
    fake, clock = world()
    original = fake._rows

    def stall_once(name, params, as_of):
        clock.advance(26 * 60 if name == "catalog_rows" and len([c for c in fake.calls if c.resource == name]) == 1 else 0)
        return original(name, params, as_of)

    fake._rows = stall_once
    code, lines, _ = run(capsys, fake, clock, files=files)
    causes = lines[-1]["retries"]["causes"]
    assert causes == [{"run": 1, "cause": "as_of_expired_local", "side": "v2", "resource": "catalog_signals"}]
    # The gates judge the pull that finished: run 2, whose pages were quick.
    assert code == 0 and lines[-1]["runs"] == 2 and lines[-1]["gates"]["page_time"]["ok"] is True


def test_as_of_expiry_reruns_without_waiting(capsys, files):
    reply = v2_error(400, "bad_request", reason="as_of")
    fake, clock = world(intercept=lambda call: reply if call.resource == "rs_ids" and call.n == 1 else None)
    code, lines, _ = run(capsys, fake, clock, files=files)
    _, summary = summary_of(lines)
    assert code == 0 and summary["retries"]["as_of_expired"] == 1 and summary["retries"]["reruns"] == 1 and clock.sleeps == []


def test_busy_timeout_and_errors_exit_3_with_a_safe_summary(capsys, files):
    fake, clock = world(intercept=lambda call: busy() if call.resource == "manifest" else None)
    code, lines, _ = run(capsys, fake, clock, files=files)
    assert code == 3 and lines[-1]["error_type"] == "BusyTimeout" and lines[-1]["retries"]["busy_wait_seconds"] == 1200


def test_snapshot_day_outside_the_window_is_a_fetch_failure(capsys, files):
    fake, clock = world(series={"2026-01-01": 1, "2026-09-23": 1})
    code, lines, _ = run(capsys, fake, clock, "--series-days", "2", files=files)
    assert code == 3 and lines[-1]["error_type"] == "ContractError"


def test_unexpected_errors_exit_4_naming_only_the_class(capsys, files, monkeypatch):
    async def broken(client, manifest, options):
        raise KeyError(f"{ROW_SENTINEL}-{EXPORT_TOKEN}")
        yield  # an async generator, as walk is

    monkeypatch.setattr(dry_run, "walk", broken)
    fake, clock = world()
    code, lines, text = run(capsys, fake, clock, files=files)
    assert code == 4 and lines[-1]["summary"] is True and lines[-1]["ok"] is False and lines[-1]["error_type"] == "KeyError"
    assert lines[-1]["where"].startswith("test_dry_run.py:")
    assert ROW_SENTINEL not in text and EXPORT_TOKEN not in text


def test_wrong_token_exits_3_without_the_token(capsys, files):
    fake, clock = world(export_token="the-real-token")
    code, lines, text = run(capsys, fake, clock, files=files)
    assert code == 3 and lines[-1]["error_type"] == "ConfigError" and "401" in lines[-1]["error"]
    assert "the-real-token" not in text and EXPORT_TOKEN not in text


def test_blocked_by_deployment_protection_without_bypass(capsys, files):
    fake, clock = world()
    code, lines, text = run(capsys, fake, clock, files=files, bypass=False)
    assert code == 3 and lines[-1]["error_type"] == "ConfigError" and "bypass" in lines[-1]["error"]
    assert fake.raw_paths == ["/api/pick-feed/v2/manifest"]


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([], ["2026-09-23"]),
        (["--series-days", "2"], ["2026-09-22", "2026-09-23"]),
        (["--series-days", "0"], []),
        (["--series-days", "9"], ["2026-09-22", "2026-09-23"]),
    ],
)
def test_series_days(capsys, files, days, expected):
    fake, clock = world()
    code, lines, _ = run(capsys, fake, clock, *days, files=files)
    assert code == 0
    assert [c.params["day"] for c in fake.calls if c.resource == "rs_series_day"] == expected
    assert sorted(lines[-1]["series_days"]) == expected


def test_limit_override(capsys, files):
    fake, clock = world(sizes={"rs_rows": 3})
    code, lines, _ = run(capsys, fake, clock, "--limit", "rs_rows=1", "--limit", "rs_series_day=2", files=files)
    assert code == 0
    assert {c.params.get("limit") for c in fake.calls if c.resource == "rs_rows"} == {"1"}
    assert {c.params.get("limit") for c in fake.calls if c.resource == "rs_series_day"} == {"2"}
    assert all("limit" not in c.params for c in fake.calls if c.resource == "rs_ids")
    assert lines[-1]["resources"]["rs_rows"]["pages"] == 3


def test_limit_takes_several_items_after_one_flag(capsys, files):
    fake, clock = world(sizes={"rs_rows": 3})
    code, lines, _ = run(capsys, fake, clock, "--limit", "rs_rows=1", "rs_series_day=2", files=files)
    assert code == 0 and lines[-1]["resources"]["rs_rows"]["pages"] == 3
    assert {c.params.get("limit") for c in fake.calls if c.resource == "rs_series_day"} == {"2"}


@pytest.mark.parametrize(
    "extra",
    [["--limit", "nope=3"], ["--limit", "rs_rows=0"], ["--limit", "rs_rows=2001"], ["--limit", "rs_rows"], ["--series-days", "94"], ["--series-days", "-1"]],
)
def test_bad_options_are_usage_errors(capsys, files, extra):
    fake, clock = world()
    with pytest.raises(SystemExit) as caught:
        dry_run.parse_args(["--dry-run", "--base-url", BASE, *extra])
    assert caught.value.code == 2
    assert fake.calls == []
    capsys.readouterr()


@pytest.mark.parametrize(
    "flag",
    [["--bypass-header", "PLAINTEXT-SECRET"], ["--bypass-header=PLAINTEXT-SECRET"], ["--bypass", "PLAINTEXT-SECRET"], ["--token", "PLAINTEXT-SECRET"]],
)
def test_bypass_only_from_file(capsys, files, flag):
    fake, clock = world()
    code, _, text = run(capsys, fake, clock, *flag, files=files)
    assert code == 2 and "PLAINTEXT-SECRET" not in text and fake.raw_paths == []
    assert "不认识的参数" in text


def test_usage_errors_do_not_echo_values(capsys):
    code = dry_run.main(["--dry-run", "--base-url", BASE, "--series-days", "PLAINTEXT-SECRET"], env={})
    assert code == 2 and "PLAINTEXT-SECRET" not in "".join(capsys.readouterr())


@pytest.mark.parametrize("flag", ["--dry-run=PLAINTEXT-SECRET", "--help=PLAINTEXT-SECRET", "-h=PLAINTEXT-SECRET"])
def test_flags_without_values_do_not_echo_what_was_attached(capsys, flag):
    # argparse: "argument --dry-run: ignored explicit argument 'PLAINTEXT-SECRET'" (Python 3.12).
    code = dry_run.main([flag, "--base-url", BASE], env={})
    text = "".join(capsys.readouterr())
    assert code == 2 and "PLAINTEXT-SECRET" not in text and "参数错误" in text


def test_unknown_arguments_point_at_help(capsys):
    code = dry_run.main(["--dry-run", "--base-url", BASE, "stray"], env={})
    text = "".join(capsys.readouterr())
    assert code == 2 and "不认识的参数" in text and "--help" in text and "stray" not in text


def test_dry_run_flag_is_required(capsys, files):
    fake, clock = world()
    code = dry_run.main(["--base-url", BASE, "--token-file", str(files["token"])], env={}, transport=fake.transport())
    assert code == 2 and fake.raw_paths == []
    capsys.readouterr()


def test_tokens_from_files_or_environment(capsys, files, tmp_path):
    fake, clock = world()
    env = {"PICK_REALSHORT_EXPORT_TOKEN": "env-export-token", "PICK_REALSHORT_FEED_TOKEN": "env-feed-token"}
    code, _, _ = run(capsys, fake, clock, "--v1-token-file", str(files["v1"]), env=env, files=files)
    assert code == 0
    assert {c.headers["authorization"] for c in fake.calls} == {f"Bearer {EXPORT_TOKEN}", f"Bearer {FEED_TOKEN}"}

    fake, clock = world(export_token="env-export-token", feed_token="env-feed-token")
    code = dry_run.main(
        ["--dry-run", "--base-url", BASE, "--bypass-header-file", str(files["bypass"])],
        env=env,
        transport=fake.transport(),
        clock=clock,
        sleep=clock.sleep,
        timer=clock.timer,
    )
    assert code == 0 and {c.headers["authorization"] for c in fake.calls} == {"Bearer env-export-token", "Bearer env-feed-token"}
    capsys.readouterr()


@pytest.mark.parametrize("problem", ["missing", "empty", "unreadable"])
def test_secret_files_must_be_readable_and_non_empty(capsys, files, tmp_path, problem):
    target = tmp_path / "nothing-here"
    if problem == "empty":
        target.write_text("\n")
    if problem == "unreadable":
        target.mkdir()
    code = dry_run.main(["--dry-run", "--base-url", BASE, "--token-file", str(target)], env={"PICK_REALSHORT_EXPORT_TOKEN": "fallback"})
    err = capsys.readouterr().err
    assert code == 2 and "fallback" not in err


def test_missing_export_token_names_the_variable(capsys):
    code = dry_run.main(["--dry-run", "--base-url", BASE], env={})
    assert code == 2 and "PICK_REALSHORT_EXPORT_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("base", ["https://realshort.test/api/pick-feed", "https://exa\u00e9mple..test"])
def test_bad_base_url_is_a_usage_error(capsys, files, base):
    # The second one passes urlsplit and fails only in httpx: still exit 2 with a line, never a traceback and exit 1.
    code = dry_run.main(["--dry-run", "--base-url", base, "--token-file", str(files["token"])], env={})
    err = capsys.readouterr().err
    assert code == 2 and "base URL" in err and "Traceback" not in err


ENTRY_MODULES = ("ggwork_pick.mirror.client", "ggwork_pick.mirror.dry_run")


@pytest.mark.parametrize(
    ("module", "extra"),
    [(ENTRY_MODULES[0], []), (ENTRY_MODULES[0], ["--scan"]), (ENTRY_MODULES[1], [])],
    ids=["plain", "scan", "dry-run-module"],
)
def test_dry_run_runs_without_db_or_config(tmp_path, files, module, extra):
    """The real entry point in a clean process: python -m runs ggwork_pick/__init__.py first (critique 2, CLI entry).
    python -m ggwork_pick.mirror.dry_run is the same entry, not a silent exit 0."""
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    fake = FakeRealShort(bypass=BYPASS, series={yesterday: 2}, sizes={"catalog_rows": 3}, page_rows={"catalog_rows": 2}, compress=True)
    server, base = serve(fake)
    work, home = tmp_path / "cwd", tmp_path / "home"
    work.mkdir()
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "PYTHONPATH": os.pathsep.join([str(EXTENSION_ROOT), str(EXTENSION_API)]),
        "PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN,
    }
    command = [sys.executable, "-m", module, "--dry-run", "--base-url", base]
    command = [*command, "--bypass-header-file", str(files["bypass"]), "--token-file", str(files["token"]), *extra]
    try:
        result = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-3000:]
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    pages, summary = summary_of(lines)
    assert summary["ok"] is True and summary["resources"]["catalog_rows"]["pages"] == 2 and summary["v1"]["rows"] == 3
    # With --scan the packaged pan_rules.json loads in the clean process and the scan finds nothing in the double's rows.
    assert ("scan" in summary) == bool(extra) and summary.get("scan", {"total": 0})["total"] == 0
    assert all(METRIC_KEYS <= set(line) and line["wire_bytes"] > 0 for line in pages)
    assert [(line["bytes"], line["wire_bytes"]) for line in pages] == [(body, sent) for _, body, sent in fake.wire]
    assert pages[0]["resource"] == "manifest" and pages[0]["wire_bytes"] < pages[0]["bytes"]
    for secret in SECRETS:
        assert secret not in result.stdout + result.stderr
    assert "RuntimeWarning" not in result.stderr
    assert list(work.iterdir()) == []
    assert {c.headers.get("x-vercel-protection-bypass") for c in fake.calls} == {BYPASS}


@pytest.mark.parametrize("module", ENTRY_MODULES)
def test_either_module_without_arguments_is_a_usage_error(tmp_path, module):
    """No arguments: exit 2 with the usage on stderr, never a silent exit 0, and nothing read or written."""
    work = tmp_path / "cwd"
    work.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "PYTHONPATH": os.pathsep.join([str(EXTENSION_ROOT), str(EXTENSION_API)])}
    result = subprocess.run([sys.executable, "-m", module], cwd=work, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 2 and result.stdout == ""
    assert "--dry-run" in result.stderr and "--base-url" in result.stderr
    assert "Traceback" not in result.stderr and "RuntimeWarning" not in result.stderr
    assert list(work.iterdir()) == []
