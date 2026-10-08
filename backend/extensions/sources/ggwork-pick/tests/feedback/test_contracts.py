"""Synthetic feedback inputs: identity authority, null semantics and bounded queries."""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError


def test_feedback_source_inventory_is_exact_and_has_no_workflow():
    from ggwork_pick.feedback.contracts import BASE_TOKEN, TABLES

    assert BASE_TOKEN == "OtnsbnRnwaLmnVsJByscTkFMntd"
    assert len(TABLES) == 16
    assert len({table.table_id for table in TABLES}) == 16
    assert {table.key for table in TABLES} >= {"dramas", "posts", "observations", "cps_auto", "cps_manual", "commission_rules"}
    assert all(table.table_id.startswith("tbl") for table in TABLES)


@pytest.mark.parametrize("forbidden", [{"owner_id": "someone-else"}, {"base_token": "other"}, {"sql": "SELECT *"}, {"feedback_version_id": "foreign"}])
def test_model_cannot_choose_feedback_authority(forbidden):
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery

    with pytest.raises(ValidationError):
        FeedbackAnalysisQuery.model_validate(forbidden)


def test_analysis_validates_dates_and_preserves_explicit_scope():
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery

    query = FeedbackAnalysisQuery.model_validate({"published_from": "2026-10-01", "published_to": "2026-10-07", "language": "en", "group_by": "genre"})
    assert query.published_from == date(2026, 10, 1)
    assert query.language == "en"
    with pytest.raises(ValidationError):
        FeedbackAnalysisQuery.model_validate({"published_from": "2026-10-08", "published_to": "2026-10-07"})


def test_country_is_an_explicit_unsupported_query_not_a_language_alias():
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery

    query = FeedbackAnalysisQuery.model_validate({"group_by": "country"})
    assert query.group_by == "country"
    assert query.language is None


def test_observed_zero_and_missing_playback_remain_different():
    from ggwork_pick.feedback.contracts import PlaybackObservation

    missing = PlaybackObservation(post_key="tiktok:p1", record_id="synthetic-r1", views=None)
    zero = PlaybackObservation(post_key="tiktok:p2", record_id="synthetic-r2", views=0)
    assert missing.model_dump()["views"] is None
    assert zero.model_dump()["views"] == 0
    with pytest.raises(ValidationError):
        PlaybackObservation(post_key="tiktok:p3", record_id="synthetic-r3", views=True)


def test_money_uses_decimal_and_preserves_grain_currency_and_lane():
    from ggwork_pick.feedback.contracts import RevenueObservation

    item = RevenueObservation(record_id="synthetic-r1", source_lane="cps_manual", grain="account", currency="USD", amount="0.10", metric="commission")
    assert item.amount == Decimal("0.10")
    assert item.model_dump(mode="json")["amount"] == "0.10"
    assert item.grain == "account"
    assert item.drama_record_id is None
    with pytest.raises(ValidationError):
        RevenueObservation(record_id="synthetic-r2", source_lane="cps_auto", grain="drama", currency="USD", amount="NaN", metric="commission")


def test_feedback_input_uses_existing_unstorable_text_rules():
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery

    with pytest.raises(ValidationError):
        FeedbackAnalysisQuery.model_validate({"language": "en\x00"})


def test_detail_query_rejects_duplicate_or_unbounded_items():
    from ggwork_pick.feedback.contracts import FeedbackDetailQuery

    with pytest.raises(ValidationError):
        FeedbackDetailQuery(result_id="result-1", item_ids=["item-1", "item-1"])
    with pytest.raises(ValidationError):
        FeedbackDetailQuery(result_id="result-1", item_ids=[f"item-{i}" for i in range(21)])


def test_revenue_breakdown_is_structured_and_decimal_not_encoded_in_metrics():
    from ggwork_pick.feedback.contracts import FeedbackItem, RevenueAggregate

    aggregate = RevenueAggregate(source_lane="cps_auto", grain="drama", currency="USD", metric="orders", amount="3", records=2, missing_records=0)
    item = FeedbackItem(key="synthetic-drama", evidence_kind="direct", revenue=[aggregate])
    assert item.model_dump(mode="json")["revenue"][0]["amount"] == "3"
