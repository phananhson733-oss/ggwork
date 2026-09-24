"""The dry-run's gates as P1-6 left them (2026-09-24): manifest_time apart from page_time, source_revision, --scan's contract.

P1-6 measured the manifest at 22-25 s, over the 15 s a row page gets; the user took that, with the manifest's own gate at
45 s (manifest_time) and page_time over the row pages alone (the v2 resources and rs_series_day). A null sourceRevision
is a gate now, not a note. --scan also runs the strict models (contracts.parse_page, contracts.parse_manifest) over every
v2 page and the manifest in its worker thread: a failure never stops the pull, the summary counts it per resource with
the first failing field path (never a value), and the contract gate fails.
"""

import json
import threading

import pytest
from fake_realshort import BYPASS, EXPORT_TOKEN, FEED_TOKEN, ROW_SENTINEL, Clock, FakeRealShort

from ggwork_pick.mirror import dry_run

BASE = "https://realshort.test"
V1 = {"PICK_REALSHORT_FEED_TOKEN": FEED_TOKEN}
PLANTED = "SENTINEL-NOT-A-TITLE"


@pytest.fixture
def files(tmp_path):
    folder = tmp_path / "secrets"
    folder.mkdir()
    paths = {"token": folder / "export", "bypass": folder / "bypass"}
    for key, value in (("token", EXPORT_TOKEN), ("bypass", BYPASS)):
        paths[key].write_text(value + "\n")
    return paths


def _world(**options):
    clock = Clock()
    return FakeRealShort(now=clock, bypass=BYPASS, **options), clock


def _run(capsys, fake, clock, files, *extra, env=None):
    argv = ["--dry-run", "--base-url", BASE, "--token-file", str(files["token"]), "--bypass-header-file", str(files["bypass"]), *extra]
    code = dry_run.main(argv, env=env or {}, transport=fake.transport(), clock=clock, sleep=clock.sleep, timer=clock.timer)
    out, err = capsys.readouterr()
    lines = [json.loads(line) for line in out.splitlines()]
    return code, lines[:-1], lines[-1], out + err


def _slow_manifest(fake, clock, seconds: float) -> None:
    original = fake._manifest

    def slow(as_of):
        clock.advance(seconds)
        return original(as_of)

    fake._manifest = slow


def _slow_rows(fake, clock, seconds: float, resource: str) -> None:
    original = fake._rows

    def slow(name, params, as_of):
        clock.advance(seconds if name == resource else 0)
        return original(name, params, as_of)

    fake._rows = slow


def _break_rows(fake, resource: str, column: str) -> None:
    """Every page of `resource` comes with its first row's `column` a list: out of contract, fine for transport."""
    original = fake._rows

    def broken(name, params, as_of):
        status, headers, body = original(name, params, as_of)
        if name != resource:
            return status, headers, body
        page = json.loads(body)
        page["rows"][0][column] = [PLANTED]
        return status, headers, json.dumps(page).encode()

    fake._rows = broken


# ---- manifest_time and page_time ----


def test_a_manifest_under_45_seconds_passes_though_it_is_over_15(capsys, files):
    # P1-6: 22.7 and 25.5 s. The manifest has its own gate; page_time is the row pages'.
    fake, clock = _world()
    _slow_manifest(fake, clock, 30)
    code, _, summary, _ = _run(capsys, fake, clock, files)
    assert code == 0 and summary["failed_gates"] == []
    assert summary["gates"]["manifest_time"] == {"ok": True, "limit_ms": 45_000, "elapsed_ms": 30_000.0}
    assert summary["gates"]["page_time"]["ok"] is True and summary["gates"]["page_time"]["worst"] != "manifest"
    assert (dry_run.MANIFEST_MS_LIMIT, dry_run.PAGE_MS_LIMIT) == (45_000, 15_000)


def test_a_manifest_of_45_seconds_fails_manifest_time_only(capsys, files):
    fake, clock = _world()
    _slow_manifest(fake, clock, 45)
    code, _, summary, _ = _run(capsys, fake, clock, files)
    assert code == dry_run.EXIT_GATES and summary["failed_gates"] == ["manifest_time"]
    assert summary["gates"]["manifest_time"] == {"ok": False, "limit_ms": 45_000, "elapsed_ms": 45_000.0}
    assert summary["manifest"]["elapsed_ms"] == 45_000.0


def test_page_time_is_the_row_pages_rs_series_day_included(capsys, files):
    fake, clock = _world()
    _slow_manifest(fake, clock, 14)
    _slow_rows(fake, clock, 15, "rs_series_day")
    code, _, summary, _ = _run(capsys, fake, clock, files)
    assert code == dry_run.EXIT_GATES and summary["failed_gates"] == ["page_time"]
    assert summary["gates"]["page_time"] == {"ok": False, "limit_ms": 15_000, "worst_ms": 15_000.0, "worst": "rs_series_day@2026-09-23"}


# ---- source_revision ----


def test_a_null_source_revision_fails_its_gate(capsys, files):
    fake, clock = _world(sha=None)
    code, _, summary, _ = _run(capsys, fake, clock, files)
    assert code == dry_run.EXIT_GATES and summary["failed_gates"] == ["source_revision"] and summary["source_revision_null"] is True
    gate = summary["gates"]["source_revision"]
    assert gate["ok"] is False and "VERCEL_GIT_COMMIT_SHA" in gate["reason"]


def test_a_source_revision_passes_its_gate(capsys, files):
    fake, clock = _world()
    code, _, summary, _ = _run(capsys, fake, clock, files)
    assert code == 0 and summary["gates"]["source_revision"] == {"ok": True} and summary["source_revision_null"] is False


# ---- contract, with --scan ----


def test_scan_holds_every_v2_page_and_the_manifest_to_the_strict_contract(capsys, files, monkeypatch):
    from ggwork_pick.mirror import contracts

    seen = []

    def spying(name):
        function = getattr(contracts, name)

        def spy(*args):
            seen.append((name, args[0] if name == "parse_page" else "manifest", threading.current_thread() is threading.main_thread()))
            return function(*args)

        return spy

    for name in ("parse_page", "parse_manifest"):
        monkeypatch.setattr(contracts, name, spying(name))
    fake, clock = _world(sizes={"catalog_rows": 3}, page_rows={"catalog_rows": 2})
    code, pages, summary, _ = _run(capsys, fake, clock, files, "--scan", env=V1)
    v2_pages = [line["resource"] for line in pages if line["resource"] not in ("manifest", "v1")]
    assert code == 0 and summary["gates"]["contract"] == {"ok": True, "pages": len(v2_pages) + 1, "failures": {}}
    assert sorted(resource for name, resource, _ in seen if name == "parse_page") == sorted(v2_pages)
    assert "rs_series_day" in v2_pages and v2_pages.count("catalog_rows") == 2
    # The client parses the manifest once on its own (on the loop when it is small); --scan once more, in its thread.
    assert [on_loop for name, _, on_loop in seen if name == "parse_manifest"][1:] == [False]
    assert not any(on_loop for name, _, on_loop in seen if name == "parse_page")


def test_a_page_out_of_contract_is_counted_and_the_pull_goes_on(capsys, files):
    fake, clock = _world(sizes={"rs_rows": 3}, page_rows={"rs_rows": 2})
    _break_rows(fake, "rs_rows", "title")
    code, pages, summary, text = _run(capsys, fake, clock, files, "--scan", env=V1)
    assert code == dry_run.EXIT_GATES and summary["failed_gates"] == ["contract"]
    assert summary["gates"]["contract"]["failures"] == {"rs_rows": {"failures": 2, "first_path": "title"}}
    assert summary["gates"]["contract"]["ok"] is False
    # Every resource was still read to the end.
    assert summary["gates"]["row_counts"]["ok"] is True and [line["resource"] for line in pages][-1] == "rs_series_day"
    assert PLANTED not in text and ROW_SENTINEL not in text


def test_without_scan_the_rows_are_not_parsed(capsys, files, monkeypatch):
    from ggwork_pick.mirror import contracts

    def refuse(*args):
        raise AssertionError("parsed without --scan")

    monkeypatch.setattr(contracts, "parse_page", refuse)
    fake, clock = _world()
    _break_rows(fake, "rs_rows", "title")
    code, _, summary, _ = _run(capsys, fake, clock, files, env=V1)
    assert code == 0 and "contract" not in summary["gates"] and "scan" not in summary


def _break_by_page(fake, columns: dict[str, tuple[str, str]]) -> None:
    """A resource's first page (no cursor) comes with its first row's columns[resource][0] a list, a later page with
    columns[resource][1]: two pages out of contract at two different places."""
    original = fake._rows

    def broken(name, params, as_of):
        status, headers, body = original(name, params, as_of)
        if name not in columns:
            return status, headers, body
        page = json.loads(body)
        page["rows"][0][columns[name]["cursor" in params]] = [PLANTED]
        return status, headers, json.dumps(page).encode()

    fake._rows = broken


def test_contract_keeps_each_resources_first_failing_path(capsys, files):
    # rs_rows fails at title on its first page and at slug on its second: the first path is the one kept, and the count
    # goes on. catalog_rows fails on its one page. The failures come sorted by resource.
    fake, clock = _world(sizes={"rs_rows": 3}, page_rows={"rs_rows": 2})
    _break_by_page(fake, {"rs_rows": ("title", "slug"), "catalog_rows": ("title_cn", "title_cn")})
    code, pages, summary, text = _run(capsys, fake, clock, files, "--scan", env=V1)
    failures = summary["gates"]["contract"]["failures"]
    assert code == dry_run.EXIT_GATES and summary["failed_gates"] == ["contract"]
    assert failures == {"catalog_rows": {"failures": 1, "first_path": "title_cn"}, "rs_rows": {"failures": 2, "first_path": "title"}}
    assert list(failures) == ["catalog_rows", "rs_rows"] and [line["resource"] for line in pages].count("rs_rows") == 2
    assert PLANTED not in text and ROW_SENTINEL not in text


def test_a_failure_at_a_rows_top_level_is_named_as_the_whole_record():
    # A row that is not an object has no field path (PageContractError.path is empty). The client's transport check turns
    # such a page away before --scan sees it, so this holds the fallback on its own.
    found = dry_run.inspect_page(("rs_rows", None), {"rows": [[PLANTED]]})
    assert (found.checked, found.miss) == ("rs_rows", dry_run.WHOLE_RECORD) == ("rs_rows", "整条记录")
    assert dry_run.inspect_page(("v1", None), {"rows": [[PLANTED]]}).checked is None
    assert dry_run.contract_miss(lambda: None) is None
