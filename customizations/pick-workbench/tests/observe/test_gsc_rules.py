"""TR-09: gsc-rules-v1, the labels, their formal bars and the two-layer admission of one row (design 5.6, 5.8).

A row is formal only when the round has a formal window, the site layer is usable, no stale slice is touched and both
windows' two lower bounds agree; otherwise every hit is descriptive and the reasons say why. Each hit carries its
condition text and raw counts (None: not observed, never 0). No label ever gives a cause (counterexample 12); the
quality note beside a 7-day row is only a note (D38).
"""

import json
import subprocess
import sys
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest
from gsc_builders import D0, D1, H_C, MISSING, W0, W1, compare, formal_cutoff

from ggwork_pick.observe import versions
from ggwork_pick.observe.contract import UNOBSERVED_GSC, RulesRef
from ggwork_pick.observe.contract_rows import StateRow
from ggwork_pick.observe.gsc import coverage, cutoff, quality, rules

QUERY = rules.QueryEvidence(pt_date=date(2026, 9, 24), query="the alphas bride full movie")
FORBIDDEN_ZERO = ("0 曝光", "零曝光", "为零", "没有曝光")
CAUSAL = ("需求", "驱动", "因为", "导致", "原因是", "demand", "driven")


def _pair(window_a, window_b, det0, det1, flt0=MISSING, flt1=MISSING, **kw) -> rules.MetricWindows:
    return rules.MetricWindows(compare(window_a, det0, flt0, **kw), compare(window_b, det1, flt1, **kw))


def imp24(det0, det1, flt0=MISSING, flt1=MISSING, **kw) -> rules.MetricWindows:
    return _pair(W0, W1, det0, det1, flt0, flt1, **kw)


def imp7(det0, det1, flt0=MISSING, flt1=MISSING, **kw) -> rules.MetricWindows:
    return _pair(D0, D1, det0, det1, flt0, flt1, **kw)


def same(det0, det1) -> rules.MetricWindows:
    return imp24(det0, det1, det0, det1)


def inputs24(**overrides) -> rules.Inputs24h:
    base = {"scope": "USA", "impressions": same(325, 82), "clicks": same(10, 3), "cutoff": formal_cutoff(), "site": "usable"}
    return rules.Inputs24h(**{**base, **overrides})


def inputs7(**overrides) -> rules.Inputs7d:
    base = {"scope": "ALL", "impressions": imp7(300, 150, 300, 150), "site": "usable"}
    return rules.Inputs7d(**{**base, **overrides})


def labels(judgment: rules.Judgment) -> dict:
    return {hit.label: hit for hit in judgment.labels}


def texts(judgment: rules.Judgment) -> list[str]:
    return [hit.condition for hit in judgment.labels] + list(judgment.narrative) + list(rules.count_lines(judgment))


def test_label_thresholds():
    ratio = rules.judge_24h(inputs24(impressions=same(150, 100)))
    assert (ratio.state, ratio.admission, ratio.flags, ratio.reasons) == ("surge", "formal", (), ())
    hit = labels(ratio)["surge"]
    assert (hit.formal, hit.condition, hit.counts) == (True, "W0 对 W−1 ≥ +50%，W−1 ≥ 20", {"w0": 150, "w_minus_1": 100})
    assert "surge" not in labels(rules.judge_24h(inputs24(impressions=same(149, 100))))
    assert labels(rules.judge_24h(inputs24(impressions=same(2000, 1500))))["surge"].condition == "W0 ≥ 2000，W−1 ≥ 20"
    assert labels(rules.judge_24h(inputs24(impressions=same(3000, 1000))))["surge"].condition == "W0 ≥ 2000、W0 对 W−1 ≥ +50%，W−1 ≥ 20"
    assert "surge" not in labels(rules.judge_24h(inputs24(impressions=same(1999, 1500))))

    small = rules.judge_24h(inputs24(impressions=same(60, 12)))
    assert (small.state, small.admission) == ("present", "formal")
    assert (labels(small)["surge"].formal, labels(small)["surge"].condition) == (False, "W0 对 W−1 ≥ +50%，W−1 < 20（小基数曝光上升，只作描述）")
    assert labels(rules.judge_24h(inputs24(impressions=same(2500, None))))["surge"].condition.startswith("W0 ≥ 2000，W−1 在 GSC 返回的数据里未观测到")

    ctr = rules.judge_24h(inputs24(impressions=same(1000, 900), clicks=same(50, 10)))
    assert (labels(ctr)["high_ctr"].formal, labels(ctr)["high_ctr"].condition) == (True, "W0 点击 ≥ 50 且 CTR ≥ 5%")
    assert labels(ctr)["high_ctr"].counts == {"clicks": 50, "impressions": 1000} and ctr.state == "high_ctr"
    assert "high_ctr" not in labels(rules.judge_24h(inputs24(impressions=same(1000, 900), clicks=same(49, 10))))
    assert "high_ctr" not in labels(rules.judge_24h(inputs24(impressions=same(1001, 900), clicks=same(50, 10))))
    unchecked_clicks = rules.judge_24h(inputs24(impressions=same(1000, 900), clicks=imp24(50, 10)))
    assert labels(unchecked_clicks)["high_ctr"].formal is False and unchecked_clicks.admission == "formal"

    both = rules.judge_24h(inputs24(impressions=same(1000, 600), clicks=same(60, 10)))
    assert both.state == "surge" and {k for k, v in labels(both).items() if v.formal} == {"surge", "high_ctr"}


def test_rank_push_thresholds():
    def push(position, *, scope="ALL", impressions=same(400, 380), query=QUERY):
        return rules.judge_24h(inputs24(scope=scope, impressions=impressions, position_w0=position, position_w_minus_1=9.0, query=query))

    for position in (4.0, 20.0, 11.5):
        hit = labels(push(position))["rank_push"]
        assert hit.formal and hit.counts == {"impressions": 400}
        assert hit.condition == "加权排名 4–20，Q 里出现剧名加意图词；W0 的曝光 ≥ 50，Q 与 W0 同一个 PT 日（只作全站证据）"
    assert all("rank_push" not in labels(push(position)) for position in (3.9, 20.1, None))
    assert "rank_push" not in labels(push(6.0, scope="USA"))  # site-wide evidence only, never a country's
    assert "rank_push" not in labels(push(6.0, query=None))
    assert labels(push(6.0, impressions=same(49, 40)))["rank_push"].formal is False
    other_day = rules.QueryEvidence(pt_date=date(2026, 9, 22), query=QUERY.query)
    assert labels(push(6.0, query=other_day))["rank_push"].formal is False
    assert push(6.0).metrics()["rank_push_query"] == QUERY.query


def test_rising_thresholds():
    hit = labels(rules.judge_7d(inputs7(impressions=imp7(100, 66, 100, 66))))["rising"]
    assert (hit.formal, hit.condition, hit.counts) == (True, "W0 ≥ 100、比值 ≥ 1.5、增量 ≥ 30", {"w0": 100, "w_minus_1": 66})
    assert "rising" not in labels(rules.judge_7d(inputs7(impressions=imp7(100, 67, 100, 67))))
    assert "rising" not in labels(rules.judge_7d(inputs7(impressions=imp7(99, 50, 99, 50))))
    assert labels(rules.judge_7d(inputs7(impressions=imp7(120, None, 120, None))))["rising"].counts == {"w0": 120, "w_minus_1": None}
    low = replace(rules.GSC_RULES_V1, rising_min_w0=10)
    assert "rising" not in labels(rules.judge_7d(inputs7(impressions=imp7(45, 20, 45, 20)), low))  # increment 25 < 30
    assert "rising" in labels(rules.judge_7d(inputs7(impressions=imp7(50, 20, 50, 20)), low))


def test_from_zero_requires_no_rows():
    """A W−1 row valued 0 is an observation: it is not 'not observed'."""
    zero_row = rules.judge_24h(inputs24(impressions=imp24(44, 0, 44, 0)))
    assert "from_zero" not in labels(zero_row) and zero_row.metrics()["impressions"]["w_minus_1"]["x_det_rows"] == 1
    no_row = rules.judge_24h(inputs24(impressions=imp24(44, None, 44, None)))
    assert labels(no_row)["from_zero"].formal and no_row.state == "from_zero"
    assert labels(no_row)["from_zero"].condition == "W−1 两边都没有行（基线未观测到），W0 一致且 ≥ 20"
    assert labels(no_row)["from_zero"].counts == {"w0": 44, "w_minus_1": None}
    assert "from_zero" not in labels(rules.judge_24h(inputs24(impressions=imp24(19, None, 19, None))))


def test_from_zero_needs_v_agreement():
    """Counterexample 16: W−1 lost one page's rows and W0 shows 20 impressions; without Vh agreement, no formal label."""
    unchecked = rules.judge_24h(inputs24(impressions=imp24(20, None)))
    hit = labels(unchecked)["from_zero"]
    assert (hit.formal, hit.condition) == (False, "W−1 明细没有行，W0 ≥ 20（新出现，基线未核对）")
    assert (unchecked.state, unchecked.admission, unchecked.pending) == ("present", "descriptive", ("from_zero",))
    assert "filter_missing" in unchecked.reasons

    lost_page = rules.judge_24h(inputs24(impressions=imp24(20, None, 20, 15)))
    assert "from_zero" not in labels(lost_page)
    assert (lost_page.state, lost_page.admission, lost_page.flags) == ("present", "descriptive", ("detail_gap",))

    agreed = rules.judge_24h(inputs24(impressions=imp24(20, None, 21, None)))
    assert (agreed.state, labels(agreed)["from_zero"].counts) == ("from_zero", {"w0": 21, "w_minus_1": None})

    w0_disagrees = rules.judge_24h(inputs24(impressions=imp24(20, None, 40, None)))
    assert labels(w0_disagrees)["from_zero"].condition == "W−1 两边都没有行（基线未观测到），W0 ≥ 20（W0 两份下界不一致，只作描述）"
    assert (w0_disagrees.admission, labels(w0_disagrees)["from_zero"].formal) == ("descriptive", False)


def test_per_identity_gap_blocks():
    """Counterexample 22: the site gap is 3%, but this drama's W−1 pages are all missing from the detail."""
    hours = tuple(W1.start + timedelta(hours=n) for n in range(48))
    site = coverage.site_admission_24h(
        (W0, W1), a_prime=coverage.HourlyTotals("fetched", dict.fromkeys(hours, 1000)), a2_all=None, c_by_hour=dict.fromkeys(hours, 970), c_by_day={}
    )
    assert site.admission == "usable"
    got = rules.judge_24h(inputs24(impressions=imp24(120, None, 125, 90), site=site.admission))
    assert (got.admission, got.state, got.flags) == ("descriptive", "present", ("detail_gap",))
    assert not any(hit.formal for hit in got.labels) and "from_zero" not in labels(got)


def test_consistency_country_scoped_rows():
    usa = rules.judge_24h(inputs24(scope="USA", impressions=imp24(325, 82, 330, 80)))
    gbr = rules.judge_24h(inputs24(scope="GBR", impressions=imp24(325, 82, 200, 80)))
    assert (usa.admission, usa.state) == ("formal", "surge")
    assert (gbr.admission, gbr.state, gbr.flags) == ("descriptive", "present", ("detail_gap",))


def test_site_layer_blocks_rows():
    """Counterexamples 5 and 17 at the row: a site layer that is not usable leaves only descriptive labels."""
    for site, flag in (("gap_exceeded", "gap_exceeded"), ("unverifiable", "unverifiable")):
        got = rules.judge_24h(inputs24(site=site))
        assert (got.admission, got.state, got.flags) == ("descriptive", "present", (flag,))
        assert f"site_{site}" in got.reasons and labels(got)["surge"].formal is False
        assert rules.judge_7d(inputs7(site=site)).admission == "descriptive"


def test_unobserved_not_zero():
    """Counterexample 26: filter and detail both have no row in W−1; the text says not observed, never 0."""
    got = rules.judge_24h(inputs24(impressions=imp24(44, None, 44, None)))
    lines = rules.count_lines(got)
    assert f"W−1 的曝光：{UNOBSERVED_GSC}" in lines and "W0 的曝光：44" in lines
    assert not any(line.startswith("W−1 的曝光：") and line.endswith("0") for line in lines)
    assert rules.count_text(None) == UNOBSERVED_GSC and rules.count_text(0) == "0"
    for text in texts(got):
        assert not any(term in text for term in FORBIDDEN_ZERO), text


def test_unobserved_stored_null():
    """Premise 1: the judgment stores None with row_count 0 for an unobserved window, never the 0 of sum([])."""
    got = rules.judge_24h(inputs24(impressions=imp24(44, None, 44, None)))
    stored = json.loads(json.dumps(got.metrics()))
    assert stored["impressions"]["w_minus_1"] == {
        "x_det": None, "x_det_rows": 0, "x_flt": None, "x_flt_rows": 0, "admitted": True, "value": None, "reason": None
    }  # fmt: skip
    assert labels(got)["from_zero"].counts["w_minus_1"] is None
    unchecked = rules.judge_24h(inputs24(impressions=imp24(44, None)))
    assert unchecked.metrics()["impressions"]["w_minus_1"]["x_flt"] is None and unchecked.metrics()["impressions"]["w_minus_1"]["x_flt_rows"] is None


def test_7d_needs_vd_14_days():
    """Counterexample 27: the earlier 7 days lie beyond the hourly data; a formal 7-day label needs Vd over all 14 PT days."""
    formal = rules.judge_7d(inputs7(impressions=imp7(300, 150, 300, 150)))
    assert (formal.state, formal.admission) == ("rising", "formal")

    w0_only = rules.judge_7d(inputs7(impressions=imp7(300, 150, 300)))
    assert (w0_only.state, w0_only.admission, labels(w0_only)["rising"].formal) == ("present", "descriptive", False)
    assert "filter_missing" in w0_only.reasons and w0_only.pending == ("rising",)

    shifted = cutoff.daily_windows(D1.days[-1] - timedelta(days=1))[0]
    short = rules.judge_7d(inputs7(impressions=rules.MetricWindows(compare(D0, 300, 300), compare(D1, 150, 150, flt_window=shifted))))
    assert (short.admission, short.reasons) == ("descriptive", ("window_mismatch",))
    stale_vd = rules.judge_7d(inputs7(impressions=imp7(300, 150, 300, 150, current=False)))
    assert (stale_vd.admission, stale_vd.reasons) == ("descriptive", ("vd_invalid",))


def test_stale_slice_descriptive_only():
    """Counterexample 28: a window touching a stale slice (new version truncated, old one still active) is descriptive."""
    slices = (cutoff.CSlice(date(2026, 9, 22), None), cutoff.CSlice(date(2026, 9, 23), None, True), cutoff.CSlice(date(2026, 9, 24), H_C))
    stale_cut = cutoff.common_cutoff(a_watermark=H_C, a_prime_watermark=None, c_slices=slices)
    got = rules.judge_24h(inputs24(cutoff=stale_cut))
    assert (got.admission, got.state, got.flags) == ("descriptive", "present", ("stale_slice",))
    assert "stale_slice" in got.reasons and not any(hit.formal for hit in got.labels)

    seven = rules.judge_7d(inputs7(stale_days=(D0.days[2],)))
    assert (seven.admission, seven.state, seven.flags, seven.reasons) == ("descriptive", "present", ("stale_slice",), ("stale_slice",))
    with pytest.raises(ValueError):
        rules.judge_7d(inputs7(stale_days=(date(2026, 1, 1),)))


def test_no_causal_label():
    """Counterexample 12: the query mix changed, the average position did not, impressions rose: no cause is given."""
    flat = rules.judge_24h(inputs24(position_w0=6.0, position_w_minus_1=6.4))
    assert flat.narrative == ("曝光上升", "未伴随排名改善", "原因未定")
    better = rules.judge_24h(inputs24(position_w0=6.5, position_w_minus_1=9.0))
    assert better.narrative == ("曝光上升", "伴随平均排名改善 ≥2 位", "原因未定")
    assert rules.judge_24h(inputs24(position_w0=None, position_w_minus_1=9.0)).narrative[1] == "未伴随排名改善"
    assert rules.judge_7d(inputs7()).narrative == ("曝光上升", "未伴随排名改善", "原因未定")
    quiet = rules.judge_24h(inputs24(impressions=same(100, 100)))
    assert (quiet.labels, quiet.narrative) == ((), ())
    for judgment in (flat, better, rules.judge_24h(inputs24(impressions=imp24(44, None, 44, None)))):
        for text in texts(judgment):
            assert not any(term in text for term in CAUSAL), text


def test_quality_note_not_gate():
    base = rules.judge_7d(inputs7())
    passed = quality.quality_notes({"x": quality.rate_test((30, 42, 35, 50, 44, 38, 61), (20, 25, 18, 30, 22, 27, 24))})["x"]
    failed = quality.quality_notes({"x": quality.rate_test((12, 15, 9, 14, 11, 13, 16), (10, 12, 11, 9, 13, 10, 12))})["x"]
    untested = quality.quality_notes({"x": quality.rate_test((30,) * 7, (None,) * 7)})["x"]
    assert (passed.bh_passed, failed.bh_passed, untested.tested) == (True, False, False)
    for note in (passed, failed, untested):
        noted = base.with_quality_note(note)
        assert (noted.labels, noted.state, noted.admission, noted.flags, noted.pending) == (base.labels, base.state, base.admission, base.flags, base.pending)
        assert noted.quality_note is note and noted.row_fields()["quality_note"] == note.model_dump()
    with pytest.raises(ValueError):
        rules.judge_24h(inputs24()).with_quality_note(passed)


def test_mapping_changed_blocks_rising_labels():
    got = rules.judge_24h(inputs24(impressions=same(1000, 600), clicks=same(60, 10), flags=("mapping_changed",)))
    assert labels(got)["surge"].formal is False and labels(got)["high_ctr"].formal is True
    assert (got.state, got.admission, got.flags) == ("high_ctr", "formal", ("mapping_changed",))
    assert "mapping_changed" in got.reasons
    seven = rules.judge_7d(inputs7(flags=("mapping_changed", "short_history")))
    assert (seven.state, seven.flags) == ("present", ("mapping_changed", "short_history"))
    with pytest.raises(ValueError):
        rules.judge_24h(inputs24(flags=("detail_gap",)))  # computed here, never passed in


def test_pending_names_labels_awaiting_verification():
    before = rules.judge_24h(inputs24(impressions=imp24(325, 82), clicks=imp24(10, 3)))
    assert (before.admission, before.pending) == ("descriptive", ("surge",))
    assert "filter_missing" in before.reasons and not any(hit.formal for hit in before.labels)
    after = rules.judge_24h(inputs24(impressions=same(325, 82), clicks=same(10, 3)))
    assert (after.admission, after.state, after.pending) == ("formal", "surge", ("surge",))
    both = rules.judge_24h(inputs24(impressions=imp24(325, 82), clicks=imp24(60, 10)))
    assert both.pending == ("surge", "high_ctr")
    assert rules.judge_24h(inputs24(impressions=imp24(100, 100), clicks=imp24(1, 1))).pending == ()


def test_wants_filter_only_when_the_filter_is_all_that_is_missing():
    assert rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(325, 82), clicks=imp24(10, 3))))
    assert rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(325, 82, 325, 82, current=False))))  # last round's Vh
    assert rules.wants_filter(rules.judge_7d(inputs7(impressions=imp7(300, 150, 300, 150, current=False))))  # Vd no longer valid
    assert not rules.wants_filter(rules.judge_24h(inputs24()))  # admitted: the round's Vh already answered
    assert not rules.wants_filter(rules.judge_24h(inputs24(clicks=same(60, 10), flags=("mapping_changed",))))
    assert not rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(325, 82), clicks=imp24(10, 3), site="gap_exceeded")))
    carried = cutoff.common_cutoff(a_watermark=None, a_prime_watermark=None, c_slices=(), previous_cutoff=H_C)
    assert not rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(325, 82), clicks=imp24(10, 3), cutoff=carried)))
    assert not rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(100, 100), clicks=imp24(1, 1))))
    assert not rules.wants_filter(rules.judge_24h(inputs24(impressions=imp24(325, 82, 200, 82))))  # detail_gap: asked, disagreed
    assert not rules.wants_filter(rules.judge_7d(inputs7(stale_days=(D0.days[1],))))


def _state_row(judgment: rules.Judgment) -> StateRow:
    base = {
        "row_id": 1, "set_id": "3f2e1d0c9b8a7f6e5d4c3b2a1f0e9d8c", "channel": "gsc", "mode": "shadow",
        "identity": '["realshort","UkVFTFNIT1JUOjY1MGExYjJjM2Q0ZTVmNmE3YjhjOWQwZQ","en"]', "title": "The Alpha's Bride",
        "language": "en", "theater": "ReelShort", "confirmation": None, "tier": None, "correspondence": None, "id_evidence": None,
        "ambiguity": None, "carried_over": False, "stale": False, "latest_block_end": None, "paste_row": None, "created_at": "2026-09-25T03:31:40.000000+00:00",
    }  # fmt: skip
    return StateRow.model_validate(json.loads(json.dumps({**base, **judgment.row_fields()})))


def test_judgments_fit_the_state_row_contract():
    note = quality.quality_notes({"x": quality.rate_test((30, 42, 35, 50, 44, 38, 61), (20, 25, 18, 30, 22, 27, 24))})["x"]
    judgments = (
        rules.judge_24h(inputs24()),
        rules.judge_24h(inputs24(impressions=same(60, 12))),
        rules.judge_24h(inputs24(impressions=imp24(44, None, 44, None))),
        rules.judge_24h(inputs24(impressions=imp24(20, None))),
        rules.judge_24h(inputs24(site="unverifiable", flags=("migration_suspect",))),
        rules.judge_24h(inputs24(scope="ALL", position_w0=6.0, position_w_minus_1=9.0, query=QUERY)),
        rules.judge_7d(inputs7()).with_quality_note(note),
        rules.judge_7d(inputs7(stale_days=(D0.days[0],))),
    )
    for judgment in judgments:
        row = _state_row(judgment)
        assert (row.state, row.admission, row.scope, row.window_kind) == (judgment.state, judgment.admission, judgment.scope, judgment.window_kind)
        assert tuple(row.labels) == judgment.labels and row.window_end == judgment.row_fields()["window_end"]
    assert _state_row(judgments[0]).window_end == "2026-09-24T19:00:00.000000+00:00"
    assert _state_row(judgments[6]).window_end == "2026-09-24T07:00:00.000000+00:00"  # the end of PT day 23 September


def test_rules_version_and_params_are_frozen():
    assert rules.RULES[versions.GSC_RULES_VERSION] is rules.GSC_RULES_V1
    params = rules.GSC_RULES_V1.as_params()
    assert json.loads(json.dumps(params)) == params
    assert RulesRef(version=versions.GSC_RULES_VERSION, params=params).params["surge_min_base"] == 20
    assert rules.params_of(versions.GSC_RULES_VERSION) is rules.GSC_RULES_V1
    with pytest.raises(KeyError):
        rules.params_of("gsc-rules-v0")
    stricter = replace(rules.GSC_RULES_V1, surge_min_base=100)
    assert labels(rules.judge_24h(inputs24(), stricter))["surge"].formal is False


def test_judge_refuses_mismatched_inputs():
    with pytest.raises(ValueError):
        rules.judge_24h(inputs24(impressions=imp7(300, 150, 300, 150)))
    with pytest.raises(ValueError):
        rules.judge_7d(inputs7(impressions=same(300, 150)))
    carried = cutoff.common_cutoff(a_watermark=None, a_prime_watermark=None, c_slices=(), previous_cutoff=None)
    with pytest.raises(ValueError):
        rules.judge_24h(inputs24(cutoff=carried))
    with pytest.raises(ValueError):
        rules.judge_24h(inputs24(scope="usa"))


SOURCE = Path(__file__).resolve().parents[2]
ROOT = Path(__file__).resolve().parents[4]
HEAVY = ("deerflow.runtime", "deerflow.config.app_config", "fastapi", "alembic", "langgraph", "dotenv", "sqlalchemy", "httpx")


@pytest.mark.parametrize("module", ["cutoff", "pageset", "coverage", "quality", "rules"])
def test_gsc_rule_modules_are_light(module):
    """The gsc cron imports these (plan D7): pure functions, no database, HTTP or gateway modules."""
    paths = [str(SOURCE), str(ROOT / "backend/packages/extension-api"), str(ROOT / "backend/packages/harness")]
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps(paths)}\n"
        f"importlib.import_module('ggwork_pick.observe.gsc.{module}')\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    loaded = json.loads(done.stdout.strip().splitlines()[-1])
    assert [name for name in loaded if any(name == heavy or name.startswith(heavy + ".") for heavy in HEAVY)] == []
