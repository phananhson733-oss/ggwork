from datetime import UTC, datetime

import pytest

from ggwork_pick.feedback.analytics import analyze_feedback, drama_feedback
from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery

from .fakes import operating_rows, snapshot_from_rows

NOW = datetime(2026, 10, 7, tzinfo=UTC)


def test_full_universe_coverage_before_group_pagination_and_unknown_zero():
    result = analyze_feedback(snapshot_from_rows(operating_rows()), FeedbackAnalysisQuery(limit=1), "version-a", now=NOW)
    assert result.coverage.posts == 3
    assert result.coverage.measured_posts == 2
    assert result.coverage.missing_posts == 1
    assert result.total_groups == 2 and result.has_more
    assert result.items[0].metrics["views_median"] == "75"
    assert result.items[0].metrics["views_mean"] == "75"
    assert result.items[0].evidence_refs
    assert all(ref.field_ids for ref in result.items[0].evidence_refs)


def test_country_unsupported_and_explicit_dates_filter():
    snapshot = snapshot_from_rows(operating_rows())
    assert analyze_feedback(snapshot, FeedbackAnalysisQuery(group_by="country"), "v", now=NOW).status == "unsupported_dimension"
    result = analyze_feedback(snapshot, FeedbackAnalysisQuery(published_from="2026-10-02"), "v", now=NOW)
    assert result.coverage.posts == 0


def test_candidate_unmatched_is_distinct_from_no_measurements():
    snapshot = snapshot_from_rows(operating_rows())
    rows = [{"identity": "a", "language": "en", "theater": "ReelShort", "posted": {"matched": True, "records": ["SD-A"]}}, {"identity": "b"}]
    result = drama_feedback(snapshot, rows, "v", now=NOW)
    assert result.items[0].evidence_kind == "direct"
    assert result.items[1].evidence_kind == "unknown"
    assert "no_confirmed_match" in result.items[1].warnings


def test_catalog_collision_is_checked_before_candidate_filter():
    rows = [{"identity": identity, "language": "en", "theater": "ReelShort", "posted": {"matched": True, "records": ["SD-A"]}} for identity in ("a", "b")]
    result = drama_feedback(snapshot_from_rows(operating_rows()), rows, "v", now=NOW, candidate_identities={"a"})
    assert len(result.items) == 1
    assert result.items[0].evidence_kind == "unknown"


def test_revenue_separates_lane_currency_basis_and_excludes_account_grain():
    rows = operating_rows()
    rows["cps_auto"] += [
        dict(
            record_id="income-" + currency,
            **{"剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "币种": [currency], "分成收益": "1.25", "订单数": 2, "日期": "2026-10-03"},
        )
        for currency in ("USD", "EUR")
    ]
    result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(), "v", now=NOW)
    breakdown = result.items[0].revenue
    assert len(breakdown) == 4
    assert {item.currency for item in breakdown} == {"USD", "EUR"}
    assert all(item.records == 1 for item in breakdown)


def test_missing_views_do_not_become_zero_and_unknown_currency_is_not_summed():
    rows = operating_rows()
    for row in rows["observations"]:
        row["播放量"] = None
    rows["cps_auto"][0].update({"剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "币种": None})
    result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(), "v", now=NOW)
    item = result.items[0]
    assert item.metrics["views_total"] is None
    assert item.coverage.missing_posts == 3
    assert item.revenue[0].amount is None


def test_all_filters_and_beijing_publication_window():
    rows = operating_rows()
    rows["posts"][0]["实际发布时间"] = "2026-09-30T17:00:00Z"
    snapshot = snapshot_from_rows(rows)
    result = analyze_feedback(
        snapshot,
        FeedbackAnalysisQuery(
            published_from="2026-10-01", published_to="2026-10-01", language="en", theater="ReelShort", account_id="account-a", channel="tiktok", tags=["复仇"]
        ),
        "v",
        now=NOW,
    )
    assert result.coverage.posts == 3
    assert analyze_feedback(snapshot, FeedbackAnalysisQuery(tags=["missing"]), "v", now=NOW).coverage.posts == 0
    assert analyze_feedback(snapshot, FeedbackAnalysisQuery(language="es"), "v", now=NOW).coverage.posts == 0


def test_large_population_pagination_composition_and_source_rules():
    from copy import deepcopy

    rows = operating_rows()
    template_post, template_observation = rows["posts"][0], rows["observations"][1]
    rows["posts"], rows["observations"] = [], []
    for i in range(25):
        post, observation = deepcopy(template_post), deepcopy(template_observation)
        post.update(
            record_id=f"release-{i}",
            **{"Post ID": f"post-{i}", "视频链接": f"https://tiktok.com/@synthetic/video/post-{i}", "账号": [{"id": f"account-{i % 2}"}]},
        )
        observation.update(record_id=f"ob-{i}", **{"Post ID": f"post-{i}", "关联发布记录": [{"id": f"release-{i}"}], "播放量": i})
        rows["posts"].append(post)
        rows["observations"].append(observation)
    snapshot = snapshot_from_rows(rows)
    first = analyze_feedback(snapshot, FeedbackAnalysisQuery(limit=1), "v", now=NOW)
    second = analyze_feedback(snapshot, FeedbackAnalysisQuery(limit=1, offset=1), "v", now=NOW)
    assert first.coverage.posts == second.coverage.posts == 25
    assert first.items[0].metrics["views_mean"] == second.items[0].metrics["views_mean"] == "12"
    assert first.items[0].coverage.posts == second.items[0].coverage.posts == 25
    context = first.query_scope["group_context"][first.items[0].key]
    assert context["account_count"] == 2
    assert context["channel_counts"] == {"tiktok": 25}
    assert "mixed_context" in first.items[0].warnings
    assert "current_day_incomplete" in first.warnings
    assert "stop_refresh_after_30_days" in first.warnings


def test_catalog_collision_outside_first_twenty_candidates_is_not_lost():
    rows = [
        {
            "identity": f"candidate-{i}",
            "language": "en",
            "theater": "ReelShort",
            "posted": {"matched": True, "records": ["SD-A"] if i in (0, 24) else [f"SD-MISSING-{i}"]},
        }
        for i in range(25)
    ]
    result = drama_feedback(snapshot_from_rows(operating_rows()), rows, "v", now=NOW, candidate_identities={"candidate-0"})
    assert len(result.items) == 1
    assert result.items[0].metrics["identity_status"] == "ambiguous"
    assert result.coverage.posts == 0


def test_account_or_channel_scope_cannot_claim_whole_drama_income():
    rows = operating_rows()
    rows["cps_auto"][0].update({"剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "账号ID": "account-b", "分成收益": "999"})
    for filters in ({"account_id": "account-a"}, {"channel": "tiktok"}):
        result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(**filters), "v", now=NOW)
        assert result.items and result.items[0].revenue == []
        assert "revenue_scope_unavailable" in result.warnings
        assert result.query_scope["revenue_scope"] == "unavailable_for_account_or_channel_filter"


def test_unknown_platform_publications_are_not_claimed_as_unique_post_statistics():
    rows = operating_rows()
    for post in rows["posts"][:2]:
        post.pop("视频链接")
        post["Post ID"] = "same-post"
    rows["observations"] = []
    result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(), "v", now=NOW)
    assert result.coverage.posts == 1
    assert result.query_scope["unknown_identity_publications_excluded"] == 2
    assert result.query_scope["unknown_identity_exclusion_scope"] == "source_snapshot_all_publications"
    assert "unknown_publication_identity" in result.warnings
    detail = drama_feedback(
        snapshot_from_rows(rows), [{"identity": "a", "language": "en", "theater": "ReelShort", "posted": {"matched": True, "records": ["SD-A"]}}], "v", now=NOW
    )
    assert detail.coverage.posts == 1
    assert detail.query_scope["unknown_identity_publications_excluded"] == 2
    assert detail.query_scope["unknown_identity_exclusion_scope"] == "source_snapshot_all_publications"


def test_confirmed_revenue_aggregate_and_evidence_agree_on_grain_attribution_and_date():
    from decimal import Decimal

    rows = operating_rows()
    rows["cps_auto"][0].update({"剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "日期": "2026-10-02T17:00:00Z"})
    result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(), "v", now=NOW)
    item = result.items[0]
    assert item.revenue[0].amount == Decimal("12.34")
    assert item.revenue[0].grain == "drama"
    ref = next(ref for ref in item.evidence_refs if ref.record_id == "revenue-account")
    assert (ref.grain, ref.attribution, ref.metric_as_of) == ("drama", "confirmed", "2026-10-03")
    assert item.metrics["revenue_metric_on_min"] == ref.metric_as_of


@pytest.mark.parametrize("orders", [12, 0, None])
def test_orders_do_not_require_currency_but_money_still_does(orders):
    from decimal import Decimal

    rows = operating_rows()
    rows["cps_auto"][0].update({"剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "订单数": orders})
    rows["cps_auto"][0].pop("币种")
    result = analyze_feedback(snapshot_from_rows(rows), FeedbackAnalysisQuery(), "v", now=NOW)
    revenue = {row.metric: row for row in result.items[0].revenue}
    count = revenue["orders"]
    assert count.amount == (Decimal(orders) if orders is not None else None)
    assert count.missing_records == (1 if orders is None else 0)
    assert (count.source_lane, count.grain, count.currency) == ("cps_auto", "drama", "UNKNOWN")
    assert revenue["commission"].amount is None
    assert revenue["commission"].missing_records == 0
