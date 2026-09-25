"""TR-07: the 90-day page URL list for RealShort's legacy export, and the positive-control candidates for stage 0.

The list is TR-08's input (design 5.5): every raw URL GSC returned in the window, never decoded, deduplicated only when
two strings are identical, with its clicks and impressions summed over the requests it came from. Requests are split by
month, a full month by country, and a full country by halving its days, so that every answer used is short of rowLimit;
what cannot be split any further is named in the manifest and the list is marked incomplete. All answers come from
tests/observe/gsc_sim.py over httpx.MockTransport; tests lower query.ROW_LIMIT to see truncation without 25,000 rows.
"""

import hashlib
import io
import json
import stat
from collections import defaultdict
from datetime import date, datetime, timedelta

import httpx
import pytest
from gsc_fake import env, new_key
from gsc_sim import BG, EN, ES, HOST, ID_BG, ID_EN, ID_QB, NOW, PT, QB, SPECS, Fact, SimGoogle, facts_from, rules

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.gsc import export_urls
from ggwork_pick.observe.gsc import query as gsc_query
from ggwork_pick.observe.gsc.client import GscClient, GscRequestError, load_config
from ggwork_pick.observe.gsc.pacer import BudgetExhausted, Pacer

END = date(2026, 9, 24)  # yesterday in PT at NOW
START = END - timedelta(days=89)


@pytest.fixture(scope="module")
def key():
    return new_key()


def truth(facts, start=START, end=END) -> dict[str, tuple[int, int]]:
    sums: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for fact in facts:
        if start <= fact.day <= end:
            sums[fact.page][0] += fact.clicks
            sums[fact.page][1] += fact.impressions
    return {page: (clicks, shown) for page, (clicks, shown) in sums.items()}


async def export(key, facts, *, max_requests=500, **changes):
    google = SimGoogle(key.public_key(), facts, rules(**changes))
    clock = ManualClock(NOW)
    async with GscClient(load_config(env(key)), transport=google.transport(), clock=clock) as gsc:
        pacer = Pacer(clock, pause_seconds=0.0, max_requests=max_requests)
        found = await export_urls.export_page_urls(gsc, pacer, end=END, days=90, data_state="all")
    return found, google


def daily(page: str, country: str, first: date, last: date, clicks=1, impressions=10) -> list[Fact]:
    days = ((first + timedelta(days=offset)) for offset in range((last - first).days + 1))
    return [Fact(datetime(day.year, day.month, day.day, 12, tzinfo=PT), page, country, "q", clicks, impressions) for day in days]


# ---- the URL list ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_export_sums_each_identical_string_and_keeps_variants_apart(key):
    variants = (BG.replace("%D0%B2", "%d0%b2"), EN + "?utm_source=feed", EN.replace(HOST, "www." + HOST))
    facts = (*facts_from(SPECS), *(fact for url in variants for fact in daily(url, "usa", START, END, impressions=3)))
    found, google = await export(key, facts)
    assert {row.raw_url: (row.clicks, row.impressions) for row in found.rows} == truth(facts)
    assert all(variant in {row.raw_url for row in found.rows} for variant in (*variants, EN, BG))  # never decoded or merged
    assert [row.raw_url for row in found.rows] == sorted(row.raw_url for row in found.rows)
    # The window is split by calendar month: 27-30 June, July, August, 1-24 September; nothing was full.
    assert [(leaf.start, leaf.end, leaf.country) for leaf in found.fetch.leaves] == [
        (date(2026, 6, 27), date(2026, 6, 30), None),
        (date(2026, 7, 1), date(2026, 7, 31), None),
        (date(2026, 8, 1), date(2026, 8, 31), None),
        (date(2026, 9, 1), date(2026, 9, 24), None),
    ]
    assert found.fetch.complete and all(body["dimensions"] == ["page"] and body["dataState"] == "all" for body in google.bodies)


@pytest.mark.asyncio
async def test_export_splits_a_full_month_by_country_then_by_days(key, monkeypatch):
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 4)
    september = (date(2026, 9, 1), END)
    facts = [
        *(fact for n in range(3) for fact in daily(f"https://{HOST}/usa-{n}", "usa", *september)),
        *(fact for n in range(3) for fact in daily(f"https://{HOST}/phl-{n}", "phl", *september)),
        *(fact for n in range(2) for fact in daily(f"https://{HOST}/gbr-early-{n}", "gbr", date(2026, 9, 1), date(2026, 9, 12))),
        *(fact for n in range(3) for fact in daily(f"https://{HOST}/gbr-late-{n}", "gbr", date(2026, 9, 13), END)),
    ]
    found, google = await export(key, facts)
    assert {row.raw_url: (row.clicks, row.impressions) for row in found.rows} == truth(facts)
    used = [leaf for leaf in found.fetch.leaves if leaf.used]
    assert found.fetch.complete and all(not leaf.truncated for leaf in used)
    assert {(leaf.start, leaf.end, leaf.country) for leaf in used if leaf.country == "gbr"} == {
        (date(2026, 9, 1), date(2026, 9, 12), "gbr"),
        (date(2026, 9, 13), END, "gbr"),
    }
    superseded = [leaf for leaf in found.fetch.leaves if not leaf.used]
    assert {(leaf.start, leaf.end, leaf.country) for leaf in superseded} == {(date(2026, 9, 1), END, None), (date(2026, 9, 1), END, "gbr")}
    filters = [body["dimensionFilterGroups"][0]["filters"][0] for body in google.bodies if "dimensionFilterGroups" in body]
    assert {(item["dimension"], item["operator"]) for item in filters} == {("country", "equals")}
    assert any(body["dimensions"] == ["country"] for body in google.bodies)  # the month's countries, listed first


@pytest.mark.asyncio
async def test_export_names_what_cannot_be_split_further(key, monkeypatch):
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 4)
    day = date(2026, 9, 20)
    facts = [fact for n in range(4) for fact in daily(f"https://{HOST}/deu-{n}", "deu", day, day)]
    found, _ = await export(key, facts)
    assert not found.fetch.complete
    (stuck,) = found.fetch.incomplete
    assert (stuck.start, stuck.end, stuck.country, stuck.truncated, stuck.used) == (day, day, "deu", True, True)
    assert len(found.rows) == 4  # kept, and the manifest says the list is incomplete
    manifest = export_urls.manifest(found, site_url="sc-domain:dramashortstv.com", generated_at=NOW, file_name="x.jsonl", digest="0" * 64)
    assert manifest["complete"] is False and manifest["incomplete_leaves"] == [{"start": "2026-09-20", "end": "2026-09-20", "country": "deu", "rows": 4}]


@pytest.mark.asyncio
async def test_export_file_is_deterministic_and_the_manifest_hashes_it(key):
    first, _ = await export(key, facts_from(SPECS))
    second, _ = await export(key, facts_from(SPECS))
    data = export_urls.jsonl_bytes(first.rows)
    assert data == export_urls.jsonl_bytes(second.rows)
    lines = [json.loads(line) for line in data.decode("utf-8").splitlines()]
    assert lines[0].keys() == {"raw_url", "clicks", "impressions"} and len(lines) == len(first.rows)
    digest = hashlib.sha256(data).hexdigest()
    manifest = export_urls.manifest(first, site_url="sc-domain:dramashortstv.com", generated_at=NOW, file_name="gsc-urls.jsonl", digest=digest)
    assert manifest["sha256"] == digest and manifest["file"] == "gsc-urls.jsonl"
    assert manifest["window"] == {"start": "2026-06-27", "end": "2026-09-24", "days": 90, "timezone": "America/Los_Angeles"}
    assert (manifest["data_state"], manifest["dimensions"], manifest["search_type"]) == ("all", ["page"], "web")
    assert manifest["rows"] == len(first.rows) and manifest["impressions"] == sum(row.impressions for row in first.rows)
    assert manifest["complete"] is True and manifest["site_url"] == "sc-domain:dramashortstv.com" and manifest["requests"] == 4


@pytest.mark.asyncio
async def test_export_stops_at_the_request_budget(key, monkeypatch):
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 4)
    with pytest.raises(BudgetExhausted) as stopped:
        await export(key, facts_from(SPECS), max_requests=3)
    assert stopped.value.exit_code == ExitCode.FAILED


@pytest.mark.asyncio
async def test_pacer_sleeps_between_requests():
    clock = ManualClock(NOW)
    pacer = Pacer(clock, pause_seconds=1.5, max_requests=2)
    await pacer.before_request()
    await pacer.before_request()
    assert clock.sleeps == (1.5,) and pacer.requests == 2  # no pause before the first request
    with pytest.raises(BudgetExhausted):
        await pacer.before_request()


# ---- positive controls for stage 0 -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_positive_control_candidates_are_exact_title_queries(key):
    google = SimGoogle(key.public_key(), facts_from(SPECS), rules())
    clock = ManualClock(NOW)
    async with GscClient(load_config(env(key)), transport=google.transport(), clock=clock) as gsc:
        pacer = Pacer(clock, pause_seconds=0.0, max_requests=100)
        pages = await export_urls.export_page_urls(gsc, pacer, end=END, days=90, data_state="all")
        found = await export_urls.positive_control_candidates(gsc, pacer, end=END, days=28, pages=pages.rows)
    keys = [candidate.title_key for candidate in found.candidates]
    # QB's page was seen once, 60 days back; its exact title is searched now and lands on the home page: still a candidate.
    assert keys == ["the-billionaires-secret-wife", "великият-и-могъщ-джин", "amor-en-la-oficina", "romance-lessons-with-my-quarterback"]
    top = found.candidates[0]
    assert top.queries == ("the billionaire's secret wife",)
    assert [(share.country, share.trends_geo) for share in top.countries] == [("usa", "US"), ("phl", None), ("gbr", "GB")]
    assert [(page.locale, page.book_id, page.raw_url) for page in top.pages] == [("en", ID_EN, EN)]
    assert found.candidates[1].pages[0].book_id == ID_BG and found.candidates[3].pages[0].raw_url == QB
    assert found.candidates[3].pages[0].book_id == ID_QB
    samples = 3 * 6 + 25  # days 1-3 back carry six hours each, days 4-28 back one
    assert (top.impressions, top.clicks) == ((40 + 30 + 6) * samples, (3 + 2 + 0) * samples)
    assert all(body["dimensions"] in (["page"], ["query", "country"]) for body in google.bodies)
    document = export_urls.candidates_json(found, site_url="sc-domain:dramashortstv.com", generated_at=NOW, limit=2)
    assert [item["title_key"] for item in document["candidates"]] == keys[:2] and document["total_candidates"] == 4
    assert document["window"]["start"] == "2026-08-28" and document["dimensions"] == ["query", "country"]
    assert ES in {page["raw_url"] for page in export_urls.candidates_json(found, site_url="x", generated_at=NOW)["candidates"][2]["pages"]}


# ---- the command --------------------------------------------------------------------------------------------------------


async def run_command(key, google, out_dir, *extra):
    from ggwork_pick.observe.admin.cmd_gsc_export_urls import execute

    out = io.StringIO()
    code = await execute(["--out-dir", str(out_dir), "--pause", "0", *extra], environ=env(key), transport=google.transport(), clock=ManualClock(NOW), out=out)
    return code, out.getvalue()


@pytest.mark.asyncio
async def test_command_writes_list_manifest_and_candidates(key, tmp_path):
    google = SimGoogle(key.public_key(), facts_from(SPECS), rules())
    code, printed = await run_command(key, google, tmp_path / "artifacts")
    assert code == ExitCode.OK
    folder = tmp_path / "artifacts"
    names = sorted(path.name for path in folder.iterdir())
    assert names == ["gsc-positive-controls-2026-09-25.json", "gsc-urls-2026-09-25.jsonl", "gsc-urls-2026-09-25.manifest.json"]
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in folder.iterdir()) and stat.S_IMODE(folder.stat().st_mode) == 0o700
    data = (folder / "gsc-urls-2026-09-25.jsonl").read_bytes()
    manifest = json.loads((folder / "gsc-urls-2026-09-25.manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(data).hexdigest() and manifest["file"] == "gsc-urls-2026-09-25.jsonl"
    assert manifest["window"]["end"] == "2026-09-24"
    candidates = json.loads((folder / "gsc-positive-controls-2026-09-25.json").read_text(encoding="utf-8"))
    assert candidates["candidates"][0]["title_key"] == "the-billionaires-secret-wife"
    assert manifest["sha256"] in printed and "gsc-urls-2026-09-25.jsonl" in printed


@pytest.mark.asyncio
async def test_command_without_candidates_and_with_final(key, tmp_path):
    google = SimGoogle(key.public_key(), facts_from(SPECS), rules())
    code, _ = await run_command(key, google, tmp_path, "--no-candidates", "--data-state", "final", "--days", "30")
    assert code == ExitCode.OK
    assert sorted(path.name for path in tmp_path.iterdir()) == ["gsc-urls-2026-09-25.jsonl", "gsc-urls-2026-09-25.manifest.json"]
    manifest = json.loads((tmp_path / "gsc-urls-2026-09-25.manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_state"] == "final" and manifest["window"] == {
        "start": "2026-08-26",
        "end": "2026-09-24",
        "days": 30,
        "timezone": "America/Los_Angeles",
    }
    assert {body["dataState"] for body in google.bodies} == {"final"}


@pytest.mark.asyncio
async def test_command_writes_nothing_when_a_request_fails(key, tmp_path):
    class Failing(SimGoogle):
        def handle(self, request):
            if request.url.host == "searchconsole.googleapis.com" and len(self.bodies) >= 2:
                self.calls = (*self.calls, request)
                return httpx.Response(503, json={"error": {"code": 503, "message": "Backend Error", "status": "UNAVAILABLE"}})
            return super().handle(request)

    google = Failing(key.public_key(), facts_from(SPECS), rules())
    with pytest.raises(GscRequestError):
        await run_command(key, google, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_command_refuses_without_credentials(tmp_path, monkeypatch, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    for name in ("PICK_GSC_SA_EMAIL", "PICK_GSC_SA_PRIVATE_KEY", "PICK_GSC_SA_FILE", "PICK_GSC_SITE_URL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["gsc-export-urls", "--out-dir", str(tmp_path / "out")]) == ExitCode.REFUSED
    assert "PICK_GSC_SITE_URL" in capsys.readouterr().err and not (tmp_path / "out").exists()
    assert main(["gsc-export-urls", "--days", "0"]) == ExitCode.REFUSED  # usage error, value not echoed
