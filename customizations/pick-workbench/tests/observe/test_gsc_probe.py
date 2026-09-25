"""TR-07: the GSC probe P1-P7, its parsing and summaries, and the command that writes the report (plan TR-07; design 5.1-5.5).

Only parsing and summaries are tested here (plan TR-07): every answer comes from tests/observe/gsc_sim.py over
httpx.MockTransport, and no request reaches Google. The real run waits for U1 and U2 (a service account the property's
Owner has added); these tests pin what the probe concludes from each kind of answer, so that the real run's report can
be trusted to say supported, not supported or fallback for each of the seven items.
"""

import io
import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from gsc_fake import TOKEN_URL, FakeGoogle, env, google_error, key_lines, new_key, oauth_error, pkcs8
from gsc_sim import BLOG, EN, HOST, ID_38000, ID_OLD, NOW, SPECS, WWW_PLAY, SimGoogle, Spec, facts_from, rules

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode
from ggwork_pick.observe.gsc import query as gsc_query
from ggwork_pick.observe.gsc.client import GscClient, load_config
from ggwork_pick.observe.gsc.probe import PROBES, ProbeContext, ProbeLog, RecordingTransport, run_probe
from ggwork_pick.observe.gsc.probe_pages import filler_regex, search_limit

ALL = tuple(PROBES)
RESOLUTION = 64
SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench


@pytest.fixture(scope="module")
def key():
    return new_key()


def sim(key, specs=SPECS, **changes) -> SimGoogle:
    return SimGoogle(key.public_key(), facts_from(specs), rules(**changes))


async def probe(key, google: FakeGoogle):
    log = ProbeLog()
    clock = ManualClock(NOW)
    transport = RecordingTransport(google.transport(), log)
    async with GscClient(load_config(env(key)), transport=transport, clock=clock, on_response=log.on_response) as gsc:
        result = await run_probe(ProbeContext(gsc=gsc, clock=clock, site_host=HOST, pause_seconds=0.0), log)
    return result, log


def verdicts(result) -> dict[str, str]:
    return {finding.probe: finding.verdict for finding in result.findings}


# ---- the whole probe --------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_on_a_site_that_supports_everything(key):
    google = sim(key)
    result, log = await probe(key, google)
    assert tuple(finding.probe for finding in result.findings) == ALL == ("P1", "P2", "P3", "P4", "P5", "P6", "P7")
    assert verdicts(result) == dict.fromkeys(ALL, "支持") and result.decided
    backfill = result.backfill
    assert backfill["a_prime_supported"] is True and backfill["a_prime_aggregation"] == "byPage"
    assert backfill["c_needs_country_split"] is False and backfill["c_split_consistent"] is True and backfill["c_shape"] == "hour,page,country"
    assert (backfill["regex_partial_match"], backfill["regex_anchor_honored"], backfill["regex_re2_only"]) == (True, True, True)
    assert backfill["regex_max_length_ok"] == 4096 and 4096 < backfill["regex_min_length_rejected"] <= 4096 + RESOLUTION
    assert backfill["regex_chunk_length_suggested"] == 3686  # 90% of the longest accepted
    assert backfill["vh_supported"] is True and backfill["vd_supported"] == {"all": True, "final": True}
    assert backfill["vh_windows"] == backfill["vh_windows_agree"] == 5  # usa, phl, gbr, bgr (the borrowed old pages) and ALL
    assert backfill["vh_cells"] == backfill["vh_cells_agree"] > 0
    assert backfill["vd_windows"] == backfill["vd_windows_agree"] == 5 and backfill["vd_cells"] == backfill["vd_cells_agree"] == 12
    assert (backfill["vh_detail_truncated"], backfill["vd_detail_truncated"]) == (False, False)
    assert backfill["regex_legacy_exact"] is True and backfill["regex_page_set_exact"] is True
    assert backfill["metadata_spelling"] == "snake_case" and backfill["watermark_lag_hours"] == 9.4
    assert backfill["watermark"] == "2026-09-25T03:00:00-07:00" and backfill["c_spanning_day_has_watermark"] is True
    assert (backfill["daily_first_incomplete_date"], backfill["final_first_incomplete_date"]) == ("2026-09-25", "2026-09-23")
    assert backfill["final_latest_day"] == "2026-09-22" and backfill["a2_aggregation"] == {"all": "byPage", "final": "byPage"}
    assert backfill["final_missing_days"] == ["2026-09-23", "2026-09-24"] and backfill["max_final_vs_all_percent"] == 0.0
    assert backfill["c_truncated"] is False and backfill["detail_gap_percent_total"] == 0.0 and backfill["detail_gap_skipped_days"] == []
    assert backfill["site_host"] == HOST and backfill["new_page_hosts"] == [HOST] and backfill["new_page_shape_mismatch"] == 0
    assert backfill["slug_percent_encoded"] is True and backfill["percent_escape_case"] == "upper"
    kinds = {"new_drama": 3, "home": 1, "blog": 1, "legacy_detail": 1, "legacy_id_query": 2, "legacy_video_play": 1}
    assert backfill["url_kinds"] == kinds  # QB was last seen 60 days ago, outside P7's seven days
    # Vd is compared with the detail as D fetches it: [date,page,country] per PT day, never filtered on the page.
    details = [body for body in google.bodies if body["dimensions"] == ["date", "page", "country"]]
    assert [(body["startDate"], body["endDate"]) for body in details] == [(day, day) for day in ("2026-09-24", "2026-09-23", "2026-09-22")]
    assert all("dimensionFilterGroups" not in body and body["dataState"] == "all" for body in details)
    # Every request is a real searchAnalytics.query shape: rowLimit and dataState always sent, nothing paged.
    assert google.bodies and all(body["rowLimit"] == 25000 and "dataState" in body and "startRow" not in body for body in google.bodies)
    assert len(google.bodies) <= 50 and len(google.token_calls) == 1
    assert len(log.exchanges) == len(google.bodies) and {exchange.step for exchange in log.exchanges} == set(ALL)


@pytest.mark.asyncio
async def test_p1_forbidden_stops_before_anything_else(key):
    denied = google_error(403, "PERMISSION_DENIED", "User does not have sufficient permission for site 'sc-domain:dramashortstv.com'.", reason="forbidden")
    google = FakeGoogle(key.public_key(), query_replies=[denied])
    result, _ = await probe(key, google)
    assert verdicts(result) == {"P1": "不支持", **dict.fromkeys(ALL[1:], "未测")} and not result.decided
    p1 = result.finding("P1")
    assert "Owner" in p1.conclusion and "U2" in p1.conclusion
    assert ("Google 原文", "User does not have sufficient permission for site 'sc-domain:dramashortstv.com'.") in p1.facts
    assert len(google.query_calls) == 1
    assert all("P1" in finding.conclusion for finding in result.findings[1:])


@pytest.mark.asyncio
async def test_token_rejected_is_a_p1_failure_with_zero_queries(key):
    google = FakeGoogle(key.public_key(), token_replies=[oauth_error(400, "invalid_grant", "Invalid JWT Signature.")])
    result, _ = await probe(key, google)
    assert verdicts(result)["P1"] == "不支持" and "时钟" in result.finding("P1").conclusion
    assert len(google.query_calls) == 0 and set(verdicts(result).values()) == {"不支持", "未测"}


class TokenFails(FakeGoogle):
    """The token endpoint without an answer: a 503, or no answer at all."""

    def __init__(self, public_key, failure: str):
        super().__init__(public_key)
        self.failure = failure

    def handle(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) != TOKEN_URL:
            return super().handle(request)
        self.calls = (*self.calls, request)
        if self.failure == "timeout":
            raise httpx.ConnectTimeout("no answer", request=request)
        return google_error(503, "UNAVAILABLE", "The service is currently unavailable.", reason="backendError")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure, kind", [("503", "server_error"), ("timeout", "timeout")])
async def test_a_transient_token_failure_leaves_p1_undecided(key, failure, kind):
    """Network trouble at the token endpoint says nothing about U1 or U2: P1 is undecided (run again), never 不支持."""
    google = TokenFails(key.public_key(), failure)
    result, _ = await probe(key, google)
    assert verdicts(result) == {"P1": "未定", **dict.fromkeys(ALL[1:], "未测")} and not result.decided
    p1 = result.finding("P1")
    assert kind in p1.conclusion and "再跑一次" in p1.conclusion and "被拒" not in p1.conclusion
    assert len(google.query_calls) == 0


@pytest.mark.asyncio
async def test_quota_error_stops_the_probe_and_keeps_googles_body(key):
    result, log = await probe(key, sim(key, quota_after=3))
    found = verdicts(result)
    assert (found["P1"], found["P7"], found["P3"]) == ("支持", "支持", "未定")  # P1, P7, then P3's second request hit the quota
    assert {found[probe_id] for probe_id in ("P2", "P4", "P5", "P6")} == {"未测"} and not result.decided
    assert "quota_short" in result.finding("P3").conclusion
    (refused,) = [exchange for exchange in log.exchanges if exchange.status == 429]
    assert refused.step == "P3" and "RESOURCE_EXHAUSTED" in refused.response_text  # the real body, for TR-06's fixtures


# ---- P2: A' ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", ["ignored", "rejected"])
async def test_p2_hourly_by_page_unsupported_falls_back_to_daily_totals(key, behaviour):
    result, _ = await probe(key, sim(key, hourly_by_page=behaviour))
    p2 = result.finding("P2")
    assert p2.verdict == "退路" and "A″" in p2.conclusion
    assert result.backfill["a_prime_supported"] is False
    assert result.backfill["a_prime_aggregation"] == ("byProperty" if behaviour == "ignored" else None)


@pytest.mark.asyncio
async def test_p2_measures_the_hourly_detail_gap(key):
    result, _ = await probe(key, sim(key, detail_hidden_pages=frozenset({BLOG})))
    # BLOG's 5 impressions an hour are in A' and not in C: 5 of 185 in each of the 7 complete hours (watermark 03:00 PDT).
    assert result.backfill["detail_gap_hours"] == 7
    assert result.backfill["detail_gap_percent_max"] == result.backfill["detail_gap_percent_total"] == round(500 / 185, 1)
    (table,) = [table for table in result.finding("P2").tables if table.title.startswith("逐小时")]
    assert len(table.rows) == 7 and all(row[-1] == "2.7%" for row in table.rows)


# ---- P3: C -----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p3_a_full_slice_splits_by_country(key, monkeypatch):
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 40)  # yesterday's C has 78 rows, today's 39; each country has at most 30
    result, _ = await probe(key, sim(key))
    p3 = result.finding("P3")
    assert p3.verdict == "支持" and "按国家拆分" in p3.conclusion and "满额的整日切片：昨天（40 行）" in p3.conclusion
    assert result.backfill["c_needs_country_split"] is True and result.backfill["c_split_truncated"] == 0
    (split,) = [table for table in p3.tables if table.title.startswith("按国家拆分")]
    assert [row[0] for row in split.rows] == ["usa", "phl", "bgr"] and all(row[2] == "否" for row in split.rows)
    # A full C lacks rows by construction: P2 leaves that day out of the detail gap instead of reading truncation as a gap.
    assert result.backfill["detail_gap_skipped_days"] == ["2026-09-24"] and result.backfill["detail_gap_hours"] == 1
    assert result.backfill["detail_gap_percent_total"] == 0.0 and "满额" in result.finding("P2").conclusion


@pytest.mark.asyncio
async def test_p3_still_full_after_the_split_is_unsupported(key, monkeypatch):
    """One country of one day is a subset of [hour,page] for that day: when the split is still full, so is the design's
    fallback, and recommending it would hand TR-21 a shape truncated every round."""
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 10)
    google = sim(key)
    result, _ = await probe(key, google)
    p3 = result.finding("P3")
    assert p3.verdict == "不支持" and result.backfill["c_split_truncated"] > 0
    assert "退到" not in p3.conclusion and "更细的拆分" in p3.conclusion and "G2" in p3.conclusion
    assert result.backfill["c_hour_page_truncated"] is True  # sent once, to show the fallback is full too
    (whole,) = [table for table in p3.tables if table.title.startswith("C 的整日切片")]
    assert [row[:3] for row in whole.rows if row[0].startswith("[hour,page]")] == [("[hour,page] 2026-09-24", "10", "是")]
    assert any(body["dimensions"] == ["hour", "page"] for body in google.bodies)


@pytest.mark.asyncio
async def test_p3_rejected_shape_with_a_full_fallback_is_unsupported(key, monkeypatch):
    """The design's fallback is only a fallback when it fits: a full [hour,page] is truncated every round too."""
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 10)
    result, _ = await probe(key, sim(key, c_shape="rejected"))
    p3 = result.finding("P3")
    assert p3.verdict == "不支持" and result.backfill["c_shape"] == "hour,page" and result.backfill["c_truncated"] is True
    assert "退到" not in p3.conclusion and "G2" in p3.conclusion


@pytest.mark.asyncio
async def test_p3_a_split_that_disagrees_leads_the_conclusion(key):
    result, _ = await probe(key, sim(key, country_filter_extra=1))
    p3 = result.finding("P3")
    assert p3.conclusion.startswith("按国家拆分的结果与整片有出入") and result.backfill["c_split_consistent"] is False


@pytest.mark.asyncio
async def test_p3_rejected_shape_falls_back_to_hour_page(key):
    result, _ = await probe(key, sim(key, c_shape="rejected"))
    p3 = result.finding("P3")
    assert p3.verdict == "退路" and result.backfill["c_shape"] == "hour,page" and "日级国家" in p3.conclusion


# ---- P4: includingRegex, Vh and Vd -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p4_regex_semantics_and_length_limit(key):
    result, _ = await probe(key, sim(key, regex_limit=3000))
    p4, backfill = result.finding("P4"), result.backfill
    assert p4.verdict == "支持" and "必须锚定" in p4.conclusion and "RE2" in p4.conclusion
    assert 3000 - RESOLUTION <= backfill["regex_max_length_ok"] <= 3000 < backfill["regex_min_length_rejected"] <= 3000 + RESOLUTION
    assert backfill["regex_page_set_exact"] is True  # the D25 regex returned exactly the pages page_set.contains accepts


@pytest.mark.asyncio
async def test_p4_full_match_mode_is_reported(key):
    result, _ = await probe(key, sim(key, regex_mode="full"))
    assert result.backfill["regex_partial_match"] is False and result.backfill["regex_anchor_honored"] is True
    assert "整串" in result.finding("P4").conclusion


@pytest.mark.asyncio
async def test_p4_legacy_raw_strings_are_matched_verbatim(key):
    """The legacy half of D25: the most seen ?id= page and the most seen percent-encoded old page, borrowed into the
    seed's page set, come back from includingRegex as exactly those raw strings."""
    google = sim(key)
    result, _ = await probe(key, google)
    p4 = result.finding("P4")
    assert result.backfill["regex_legacy_exact"] is True and "旧页原串按原串精确匹配" in p4.conclusion
    assert ("借来测正则的旧页原串", f"{ID_38000}；{WWW_PLAY}") in p4.facts
    legacy_only = [body for body in google.bodies if "38000" in _expression(body) and "/drama/" not in _expression(body)]
    assert (
        len(legacy_only) == 1
        and _expression(legacy_only[0])
        == "^(?:" + r"https://dramashortstv\.com/en\?id=38000|" + r"https://www\.dramashortstv\.com/bg/video-play/38000/" + WWW_PLAY.split("38000/")[1] + ")$"
    )


def _expression(body: dict) -> str:
    groups = body.get("dimensionFilterGroups") or [{"filters": [{"expression": ""}]}]
    return groups[0]["filters"][0]["expression"]


@pytest.mark.asyncio
async def test_p4_regex_on_decoded_urls_is_unsupported(key):
    """GSC matching the decoded text would miss every percent-encoded old page the snapshot keys by its raw string."""
    result, _ = await probe(key, sim(key, regex_subject="decoded"))
    p4, backfill = result.finding("P4"), result.backfill
    assert p4.verdict == "不支持" and backfill["regex_legacy_exact"] is False and backfill["regex_page_set_exact"] is False
    assert "旧页原串没有按原串匹配" in p4.conclusion and WWW_PLAY[:40] in p4.conclusion


@pytest.mark.asyncio
async def test_p4_partial_match_without_anchors_is_unsupported(key):
    result, _ = await probe(key, sim(key, anchors="ignored"))
    p4, backfill = result.finding("P4"), result.backfill
    assert (backfill["regex_partial_match"], backfill["regex_anchor_honored"]) == (True, False)
    assert p4.verdict == "不支持" and "^ 不生效" in p4.conclusion and p4.conclusion.index("^ 不生效") < p4.conclusion.index("includingRegex")


@pytest.mark.asyncio
async def test_p4_vh_and_vd_count_a_page_missing_from_the_detail(key):
    """Counterexample 22's shape, seen from the probe: the identity's old slug is missing from the unfiltered detail (C
    and D), and both filter requests still count it; the probe reports the disagreement and does not hide it."""
    old_a, old_b = f"https://{HOST}/en/drama/old-title-{ID_OLD}", f"https://{HOST}/en/drama/new-title-{ID_OLD}"
    specs = (*SPECS, Spec(old_b, "usa", "new title", 50, 500), Spec(old_a, "usa", "old title", 10, 300))
    result, _ = await probe(key, sim(key, specs, detail_hidden_pages=frozenset({old_a})))
    backfill = result.backfill
    assert backfill["vh_seed_book_id"] == ID_OLD
    # usa disagrees, bgr (the borrowed old pages) agrees, ALL disagrees; the hourly cells are the auxiliary table.
    assert (backfill["vh_windows"], backfill["vh_windows_agree"]) == (3, 1) and (backfill["vh_cells"], backfill["vh_cells_agree"]) == (12, 6)
    assert (backfill["vd_windows"], backfill["vd_windows_agree"]) == (3, 1) and backfill["vd_cells_agree"] < backfill["vd_cells"] == 6
    p4 = result.finding("P4")
    assert "不一致" in p4.conclusion and p4.verdict == "支持"
    assert any(table.title.startswith("Vd 与 [date,page,country] 明细（未过滤）") for table in p4.tables)


@pytest.mark.asyncio
async def test_p4_vh_shape_rejected(key):
    result, _ = await probe(key, sim(key, vh_shape="rejected"))
    assert result.finding("P4").verdict == "不支持" and result.backfill["vh_supported"] is False


@pytest.mark.asyncio
async def test_p4_vd_final_refused_is_a_fallback(key):
    result, _ = await probe(key, sim(key, vd_final="rejected"))
    p4 = result.finding("P4")
    assert p4.verdict == "退路" and "只能用 all" in p4.conclusion
    assert result.backfill["vd_supported"] == {"all": True, "final": False} and result.decided


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probe_id, failing",
    [
        ("P4", lambda body: body["dimensions"] == ["hour", "country"]),  # Vh
        ("P2", lambda body: body.get("aggregationType") == "byPage" and body["dimensions"] == ["hour"]),  # A'
        ("P6", lambda body: body["dimensions"] == ["date"] and body.get("aggregationType") == "byPage"),  # A''
        ("P7", lambda body: body["dimensions"] == ["page"] and "dimensionFilterGroups" not in body),  # the page list
        ("P3", lambda body: body["dimensions"] == ["hour", "page", "country"] and "dimensionFilterGroups" in body),  # a country split
    ],
)
async def test_a_transient_failure_leaves_only_its_item_undecided(key, probe_id, failing):
    result, _ = await probe(key, sim(key, server_error_when=failing))
    found = verdicts(result)
    assert found[probe_id] == "未定" and "server_error" in result.finding(probe_id).conclusion and not result.decided
    assert found["P1"] == "支持"  # a 5xx never stops the run the way a quota error does


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limit, max_ok, min_fail",
    [(5000, 4992, 5056), (3000, 2944, 3008), (500, 448, 512), (100_000, 32768, None), (4096, 4096, 4160)],
)
async def test_search_limit_ladder_then_bisection(limit, max_ok, min_fail):
    tried = []

    async def accepts(length: int) -> bool:
        tried.append(length)
        return length <= limit

    found = await search_limit(accepts, resolution=RESOLUTION)
    assert (found.max_ok, found.min_fail, found.aborted) == (max_ok, min_fail, False)
    assert tuple(length for length, _ in found.attempts) == tuple(tried) and len(tried) <= 14


@pytest.mark.asyncio
async def test_search_limit_stops_on_an_unclear_answer():
    async def accepts(length: int) -> bool | None:
        return None if length > 2048 else True

    found = await search_limit(accepts, resolution=RESOLUTION)
    assert found.aborted and found.max_ok == 2048 and found.min_fail is None


def test_filler_regex_has_the_exact_length_and_matches_nothing_real():
    for length in (7, 64, 1000, 4096, 32768):
        pattern = filler_regex(HOST, length)
        assert len(pattern) == length and pattern.startswith("^(?:") and pattern.endswith(")$")
    import re

    assert re.search(filler_regex(HOST, 4096), EN) is None


# ---- P5, P6, P7 ------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_p5_camel_case_spelling_and_a_missing_spanning_watermark(key):
    result, _ = await probe(key, sim(key, spelling="camel", c_spanning_watermark=False))
    assert result.backfill["metadata_spelling"] == "camelCase"
    assert result.backfill["c_spanning_day_has_watermark"] is False
    p5 = result.finding("P5")
    assert p5.verdict == "支持" and "watermark_absent" in p5.conclusion


@pytest.mark.asyncio
async def test_p6_final_lags_and_revises(key):
    result, _ = await probe(key, sim(key, final_extra=1))
    p6 = result.finding("P6")
    assert p6.verdict == "支持" and result.backfill["final_missing_days"] == ["2026-09-23", "2026-09-24"]
    assert result.backfill["max_final_vs_all_percent"] > 0
    (days,) = p6.tables
    assert len(days.rows) == 16 and days.rows[-1][2] == "无行"  # the 24th: all has rows, final none


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes, words",
    [({"daily_by_page": "ignored"}, "byProperty"), ({"final_lag_days": 20}, "16 个 PT 日都没有 final 行")],
)
async def test_p6_without_a_by_page_total_or_any_final_row_is_unsupported(key, changes, words):
    result, _ = await probe(key, sim(key, **changes))
    p6 = result.finding("P6")
    assert p6.verdict == "不支持" and words in p6.conclusion


@pytest.mark.asyncio
async def test_p7_new_pages_on_two_hosts_and_with_a_query_string(key):
    specs = (*SPECS, Spec(EN.replace(HOST, "www." + HOST), "usa", "x", 1, 5), Spec(EN + "?utm_source=feed", "usa", "y", 1, 5))
    result, _ = await probe(key, sim(key, specs))
    p7 = result.finding("P7")
    assert p7.verdict == "退路" and result.backfill["new_page_hosts"] == [HOST, "www." + HOST]
    assert result.backfill["new_page_shape_mismatch"] == 2 and "查询串" in p7.conclusion


# ---- the command --------------------------------------------------------------------------------------------------------


async def run_command(key, google, out_dir, *extra):
    from ggwork_pick.observe.admin.cmd_gsc_probe import execute

    out = io.StringIO()
    code = await execute(["--out-dir", str(out_dir), "--pause", "0", *extra], environ=env(key), transport=google.transport(), clock=ManualClock(NOW), out=out)
    return code, out.getvalue()


@pytest.mark.asyncio
async def test_command_writes_the_report_json_and_raw_answers(key, tmp_path):
    google = sim(key)
    out_dir = tmp_path / "artifacts"
    code, printed = await run_command(key, google, out_dir)
    assert code == ExitCode.OK
    names = sorted(path.name for path in out_dir.iterdir())
    assert names == ["gsc-probe-2026-09-25-raw.jsonl", "gsc-probe-2026-09-25.json", "gsc-probe-2026-09-25.md"]
    assert stat.S_IMODE(out_dir.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in out_dir.iterdir())
    report = (out_dir / "gsc-probe-2026-09-25.md").read_text(encoding="utf-8")
    for probe_id in ALL:
        assert f"## {probe_id} " in report
    assert report.count("**结论：支持**") == 7 and "sc-domain:dramashortstv.com" in report
    document = json.loads((out_dir / "gsc-probe-2026-09-25.json").read_text(encoding="utf-8"))
    assert document["decided"] is True and document["backfill"]["regex_max_length_ok"] == 4096
    assert {item["probe"] for item in document["findings"]} == set(ALL)
    raw = [json.loads(line) for line in (out_dir / "gsc-probe-2026-09-25-raw.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(raw) == len(google.bodies) and raw[0]["request"] == google.bodies[0] and raw[0]["status"] == 200
    assert "gsc-probe-2026-09-25.md" in printed


@pytest.mark.asyncio
async def test_command_names_its_files_by_the_pt_day(key, tmp_path):
    """03:00 UTC on the 26th is still the 25th in PT, the day GSC's data counts in: the files and the title say the 25th."""
    from ggwork_pick.observe.admin.cmd_gsc_probe import execute

    late = datetime(2026, 9, 26, 3, 0, tzinfo=UTC)
    code = await execute(
        ["--out-dir", str(tmp_path), "--pause", "0"], environ=env(key), transport=sim(key).transport(), clock=ManualClock(late), out=io.StringIO()
    )
    assert code == ExitCode.OK
    assert sorted(path.name for path in tmp_path.iterdir()) == ["gsc-probe-2026-09-25-raw.jsonl", "gsc-probe-2026-09-25.json", "gsc-probe-2026-09-25.md"]
    assert (tmp_path / "gsc-probe-2026-09-25.md").read_text(encoding="utf-8").startswith("# GSC 实测报告（TR-07）2026-09-25（PT 日）")


@pytest.mark.asyncio
async def test_command_output_carries_no_secret(key, tmp_path):
    google = sim(key)
    await run_command(key, google, tmp_path)
    written = "".join(path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    assert "ya29." not in written and "authorization" not in written.lower()
    assert not any(line in written for line in key_lines(pkcs8(key)))
    assert not any(assertion in written for assertion in google.assertions) and "eyJ" not in written


@pytest.mark.asyncio
async def test_command_never_overwrites_an_earlier_run(key, tmp_path):
    await run_command(key, sim(key), tmp_path)
    first = (tmp_path / "gsc-probe-2026-09-25.md").read_bytes()
    code, _ = await run_command(key, sim(key, hourly_by_page="rejected"), tmp_path)
    assert code == ExitCode.OK
    assert (tmp_path / "gsc-probe-2026-09-25.md").read_bytes() == first
    assert "**结论：退路**" in (tmp_path / "gsc-probe-2026-09-25-2.md").read_text(encoding="utf-8")
    assert (tmp_path / "gsc-probe-2026-09-25-2.json").exists() and (tmp_path / "gsc-probe-2026-09-25-2-raw.jsonl").exists()


@pytest.mark.asyncio
async def test_command_exits_1_while_any_item_is_undecided(key, tmp_path):
    code, printed = await run_command(key, sim(key, quota_after=3), tmp_path)
    assert code == ExitCode.FAILED and "未定" in printed
    assert (tmp_path / "gsc-probe-2026-09-25.md").exists()  # the partial report is still written, for the error bodies


@pytest.mark.asyncio
async def test_command_without_raw_answers(key, tmp_path):
    await run_command(key, sim(key), tmp_path, "--no-raw")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["gsc-probe-2026-09-25.json", "gsc-probe-2026-09-25.md"]


def test_command_refuses_without_credentials_before_any_request(tmp_path, monkeypatch, capsys):
    from ggwork_pick.observe.admin.__main__ import main

    for name in ("PICK_GSC_SA_EMAIL", "PICK_GSC_SA_PRIVATE_KEY", "PICK_GSC_SA_FILE", "PICK_GSC_SITE_URL"):
        monkeypatch.delenv(name, raising=False)

    def no_http(*args, **kwargs):
        raise AssertionError("no request may leave before the configuration is checked")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_http)
    assert main(["gsc-probe", "--out-dir", str(tmp_path / "out")]) == ExitCode.REFUSED
    assert "PICK_GSC_SITE_URL" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_recording_transport_passes_a_compressed_answer_through_once():
    """Google compresses its answers; the body the client parses and the body the raw file keeps are both the JSON."""
    import asyncio
    import gzip

    log = ProbeLog()
    body = json.dumps({"rows": [{"keys": ["x"], "clicks": 1, "impressions": 2, "ctr": 0.5, "position": 1.0}]}).encode()

    def answer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-encoding": "gzip", "content-type": "application/json"}, content=gzip.compress(body))

    async def exercise():
        transport = RecordingTransport(httpx.MockTransport(answer), log)
        async with httpx.AsyncClient(transport=transport) as client:
            reply = await client.post("https://searchconsole.googleapis.com/webmasters/v3/sites/x/searchAnalytics/query", json={"a": 1})
            return reply.content

    assert asyncio.run(exercise()) == body
    (kept,) = log.exchanges
    assert kept.response_text == body.decode() and kept.as_json()["response"]["rows"][0]["keys"] == ["x"]


def test_recording_transport_never_keeps_the_token_exchange():
    log = ProbeLog()

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.secret", "expires_in": 3599})
        return httpx.Response(200, json={"rows": []})

    async def exercise():
        transport = RecordingTransport(httpx.MockTransport(answer), log)
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("https://oauth2.googleapis.com/token", data={"assertion": "eyJ.secret"})
            reply = await client.post("https://searchconsole.googleapis.com/webmasters/v3/sites/x/searchAnalytics/query", json={"a": 1})
            assert reply.json() == {"rows": []}

    import asyncio

    asyncio.run(exercise())
    (kept,) = log.exchanges
    assert kept.request == {"a": 1} and "secret" not in repr(log.exchanges)


# ---- the runbook and the import weight ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_backfilled_value_says_where_it_goes(key, monkeypatch):
    """The report's backfill table names a consumer for every value any branch of the probe produces."""
    from ggwork_pick.observe.gsc.probe_report import BACKFILL_USE

    produced = set()
    for changes in ({}, {"hourly_by_page": "rejected"}, {"c_shape": "rejected"}, {"vh_shape": "rejected"}, {"daily_by_page": "ignored"}):
        result, _ = await probe(key, sim(key, **changes))
        produced |= set(result.backfill)
    monkeypatch.setattr(gsc_query, "ROW_LIMIT", 10)
    result, _ = await probe(key, sim(key))
    produced |= set(result.backfill)
    assert produced <= set(BACKFILL_USE), produced - set(BACKFILL_USE)


def test_probe_runbook_matches_the_code():
    """docs/pick-workbench/observe-runbook/gsc-probe.md names every item, verdict, file, option and default the code uses."""
    from ggwork_pick.observe.admin import cmd_gsc_export_urls, cmd_gsc_probe
    from ggwork_pick.observe.gsc import artifacts, export_urls, probe_pages
    from ggwork_pick.observe.gsc.probe import VERDICTS

    text = (SOURCE.parents[1] / "docs/pick-workbench/observe-runbook/gsc-probe.md").read_text(encoding="utf-8")
    names = (*cmd_gsc_probe.NAMES, cmd_gsc_probe.RAW_NAME, *cmd_gsc_export_urls.LIST_NAMES, cmd_gsc_export_urls.CANDIDATES_NAME)
    for name in names:
        assert f"`{name.format(stamp='<date>')}`" in text, name
    for probe_id in ALL:
        assert f"| {probe_id} " in text
    for verdict in VERDICTS:
        assert f"**{verdict}**" in text
    parsers = (cmd_gsc_probe._parser(), cmd_gsc_export_urls._parser())
    options = {option for parser in parsers for action in parser._actions for option in action.option_strings if option.startswith("--") and option != "--help"}
    assert all(f"`{option}`" in text for option in options), sorted(option for option in options if f"`{option}`" not in text)
    numbers = {
        f"`{artifacts.DEFAULT_DIR}`": True,
        f"| `--days` | `{export_urls.DEFAULT_DAYS}` |": True,
        f"| `--candidate-days` | `{export_urls.DEFAULT_CANDIDATE_DAYS}` |": True,
        f"| `--max-requests` | `{cmd_gsc_export_urls.DEFAULT_MAX_REQUESTS}` |": True,
        f"| `--candidate-max-requests` | `{cmd_gsc_export_urls.DEFAULT_CANDIDATE_MAX_REQUESTS}` |": True,
        "代码完成、实测待 U1/U2": True,
        "`<date>` 是运行开始时的 PT 日期": True,
        f"| `--candidates-shown` | `{cmd_gsc_export_urls.DEFAULT_CANDIDATES_SHOWN}` |": True,
        "1024 起翻倍到 32768": probe_pages.LADDER == (1024, 2048, 4096, 8192, 16384, 32768),
        "二分到 64 字符以内": probe_pages.RESOLUTION == 64,
        "实测上限的 90%": probe_pages.CHUNK_MARGIN_PERCENT == 90,
        "前 5 个国家": export_urls.TOP_COUNTRIES == 5,
    }
    assert {phrase: phrase in text and holds for phrase, holds in numbers.items()} == dict.fromkeys(numbers, True)


def test_gsc_commands_import_light():
    """Both commands run from the gateway image or this machine; neither drags in the gateway runtime (plan D1, D7)."""
    heavy = (
        "deerflow",
        "fastapi",
        "alembic",
        "langgraph",
        "langchain",
        "dotenv",
        "sqlalchemy",
        "ggwork_pick.context",
        "ggwork_pick.routes",
        "ggwork_pick.service",
    )
    for module in ("ggwork_pick.observe.admin.cmd_gsc_probe", "ggwork_pick.observe.admin.cmd_gsc_export_urls"):
        code = (
            "import importlib, json, sys\n"
            f"sys.path[:0] = {json.dumps([str(SOURCE)])}\n"
            f"importlib.import_module({module!r})\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
        assert done.returncode == 0, done.stderr[-2000:]
        modules = json.loads(done.stdout.strip().splitlines()[-1])
        assert [name for name in modules if any(name == prefix or name.startswith(prefix + ".") for prefix in heavy)] == []
