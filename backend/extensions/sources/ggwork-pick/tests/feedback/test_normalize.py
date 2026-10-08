"""Identity and metric interpretation must not invent attribution."""

from decimal import Decimal

import pytest

from .fakes import operating_rows, snapshot_from_rows


def test_post_latest_observation_preserves_unknown_and_measured_zero():
    from ggwork_pick.feedback.normalize import normalize

    data = normalize(snapshot_from_rows(operating_rows()))
    assert len(data.posts) == 3
    by_key = {post.post_key: post for post in data.posts}
    assert by_key["tiktok:post-0"].views == 150
    assert by_key["tiktok:post-1"].views is None
    assert by_key["tiktok:post-2"].views == 0
    assert by_key["tiktok:post-0"].drama_record_id == "drama-a"


def test_account_revenue_and_title_only_manual_revenue_do_not_become_drama_income():
    from ggwork_pick.feedback.normalize import normalize

    data = normalize(snapshot_from_rows(operating_rows()))
    assert len(data.revenue) == 2
    assert all(item.drama_record_id is None for item in data.revenue)
    assert all(item.amount == Decimal("12.34") for item in data.revenue)
    assert {item.source_lane for item in data.revenue} == {"cps_auto", "cps_manual"}
    assert not any(item.record_id == "revenue-summary" for item in data.revenue)


def test_same_post_linked_to_two_dramas_is_ambiguous_not_double_counted():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"].append({**rows["posts"][0], "record_id": "release-other", "剧": [{"id": "drama-b"}]})
    data = normalize(snapshot_from_rows(rows))
    assert len(data.posts) == 3
    assert next(post for post in data.posts if post.post_key == "tiktok:post-0").drama_record_id is None
    assert "ambiguous_post_drama" in data.warnings


def test_renamed_field_keeps_verified_semantics():
    from ggwork_pick.feedback.normalize import normalize

    snapshot = snapshot_from_rows(operating_rows())
    field = next(field for table in snapshot.tables for field in table.fields if field.name == "剧分类")
    field.name = "剧情标签"
    data = normalize(snapshot)
    assert data.dramas["drama-a"].tags == ["狼人", "复仇"]


@pytest.mark.parametrize("relation", ["post", "observation", "revenue"])
def test_unresolved_competing_reference_cannot_be_discarded_to_confirm_a_match(relation):
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    if relation == "post":
        rows["posts"][0]["剧"].append({"id": "missing-drama"})
    elif relation == "observation":
        for row in rows["observations"][:2]:
            row["关联发布记录"].append({"id": "missing-release"})
    else:
        rows["cps_auto"][0].update({"数据粒度": ["剧目级"], "剧名": [{"id": "drama-a"}, {"id": "missing-drama"}]})
    data = normalize(snapshot_from_rows(rows))
    post = next(post for post in data.posts if post.post_key == "tiktok:post-0")
    if relation == "post":
        assert post.drama_record_id is None
    elif relation == "observation":
        assert post.views is None
    else:
        assert data.revenue[0].drama_record_id is None
    assert data.warnings


def test_reverse_observation_link_and_actual_observed_publication_date():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0].pop("实际发布时间")
    rows["posts"][0]["采集记录"] = [{"id": "ob-old"}, {"id": "ob-new"}]
    for row in rows["observations"][:2]:
        row.pop("关联发布记录")
        row["发布时间"] = "2026-10-01T12:00:00+08:00"
    data = normalize(snapshot_from_rows(rows))
    post = next(post for post in data.posts if post.post_key == "tiktok:post-0")
    assert post.views == 150
    assert post.published_at is not None


def test_revenue_external_id_requires_matching_platform_and_drama_grain():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0]["剧ID（RS Boost）"] = "source-drama-1"
    rows["cps_auto"][0].update({"合作方": ["RSBoost"], "来源剧目ID": "source-drama-1", "剧场": ["ReelShort"], "数据粒度": ["剧目级"]})
    assert normalize(snapshot_from_rows(rows)).revenue[0].drama_record_id == "drama-a"
    rows["cps_auto"][0]["剧场"] = ["AnotherTheater"]
    assert normalize(snapshot_from_rows(rows)).revenue[0].drama_record_id is None


def test_external_id_namespace_must_not_cross_partners():
    from ggwork_pick.feedback.normalize import normalize

    data = operating_rows()
    data["posts"][0]["剧ID（RS Boost）"] = "external-a"
    data["cps_auto"][0].update({"合作方": ["AnotherPartner"], "来源剧目ID": "external-a", "剧场": ["ReelShort"], "数据粒度": ["剧目级"]})
    assert normalize(snapshot_from_rows(data)).revenue[0].drama_record_id is None


def test_rs_post_revenue_remains_unverified_and_negative_orders_are_unknown():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0]["RS收益"] = "0.5"
    rows["cps_auto"][0]["订单数"] = -1
    data = normalize(snapshot_from_rows(rows))
    rs = next(item for item in data.revenue if item.source_lane == "post_rs")
    assert rs.amount == Decimal("0.5") and rs.currency == "UNKNOWN"
    assert rs.drama_record_id is None
    assert next(item for item in data.revenue if item.metric == "orders").amount is None


def test_latest_per_metric_uses_explicit_unknown_but_not_absent_columns():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["observations"][0]["点赞"] = 7
    rows["observations"][1]["评论"] = 4
    data = normalize(snapshot_from_rows(rows))
    post = next(post for post in data.posts if post.post_key == "tiktok:post-0")
    assert post.likes == 7
    assert post.comments == 4
    rows["observations"][1]["点赞"] = None
    data = normalize(snapshot_from_rows(rows))
    assert next(post for post in data.posts if post.post_key == "tiktok:post-0").likes is None


def test_observation_platform_conflict_cannot_attach_via_relation():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    for row in rows["observations"][:2]:
        row["视频链接"] = "https://youtube.com/watch?v=post-0"
    data = normalize(snapshot_from_rows(rows))
    assert next(post for post in data.posts if post.post_key == "tiktok:post-0").views is None


def test_source_missing_status_and_pending_publication_invalidate_stale_values():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["observations"][1]["缺失字段"] = ["播放量"]
    rows["observations"][3]["采集状态"] = ["失败"]
    rows["posts"][1]["发布状态"] = ["待发布"]
    data = normalize(snapshot_from_rows(rows))
    by_key = {p.post_key: p for p in data.posts}
    assert by_key["tiktok:post-0"].views is None
    assert by_key["tiktok:post-2"].views is None
    assert by_key["tiktok:post-1"].published_at is None
    assert "source_metrics_explicitly_missing" in data.warnings
    assert "observation_status_invalid" in data.warnings
    assert "publication_not_confirmed_public" in data.warnings


def test_malformed_video_url_is_unknown_and_does_not_abort_snapshot():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0]["视频链接"] = "https://[bad"
    data = normalize(snapshot_from_rows(rows))
    assert any(post.channel == "unknown" and post.record_id == "release-0" for post in data.posts)
    assert "invalid_video_url" in data.warnings


def test_channel_recognizes_feishu_markdown_link_without_fetching():
    from ggwork_pick.feedback.normalize import channel

    assert channel({"视频链接": "[视频](https://www.tiktok.com/@synthetic/video/123)"}) == "tiktok"
    assert channel({"视频链接": "[https://tiktok.com](https://example.org/video)"}) == "unknown"
    assert channel({"视频链接": "[broken](https://[)"}) == "unknown"
    assert channel({"视频链接": "[TikTok](https://tiktok.com/video/1) [YouTube](https://youtube.com/watch?v=2)"}) == "unknown"


def test_verified_native_statuses_and_favorites_missing_alias():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0]["发布状态"] = "已回填"
    rows["observations"][1].update({"采集状态": ["complete"], "收藏": 9})
    post = next(p for p in normalize(snapshot_from_rows(rows)).posts if p.post_key == "tiktok:post-0")
    assert post.views == 150 and post.saves == 9
    rows["observations"][1].update({"采集状态": ["partial"], "缺失字段": ["favorites"]})
    post = next(p for p in normalize(snapshot_from_rows(rows)).posts if p.post_key == "tiktok:post-0")
    assert post.views == 150 and post.saves is None


@pytest.mark.parametrize("conflict", [None, "missing", "different_id", "different_channel"])
def test_missing_publication_id_can_use_only_unambiguous_linked_observation(conflict):
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0].pop("Post ID")
    rows["posts"][0]["采集记录"] = [{"id": "ob-old"}, {"id": "ob-new"}]
    if conflict == "missing":
        rows["posts"][0]["采集记录"].append({"id": "unknown-observation"})
    elif conflict == "different_id":
        rows["observations"][0]["Post ID"] = "other-post"
    elif conflict == "different_channel":
        rows["observations"][0]["视频链接"] = "https://youtube.com/watch?v=post-0"
    data = normalize(snapshot_from_rows(rows))
    posts = [p for p in data.posts if p.post_key == "tiktok:post-0"]
    if conflict is None:
        assert len(posts) == 1 and posts[0].views == 150
        assert posts[0].drama_record_id == "drama-a"
    else:
        assert posts == []


@pytest.mark.parametrize("url", [None, "https://[bad"])
def test_unknown_platform_same_post_id_preserves_separate_uncertain_publications(url):
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"] = [{**rows["posts"][0], "record_id": f"release-{i}", "视频链接": url, "账号": [{"id": f"account-{i}"}]} for i in range(2)]
    rows["observations"] = [{**rows["observations"][i], "关联发布记录": [{"id": f"release-{i}"}], "播放量": (i + 1) * 100} for i in range(2)]
    data = normalize(snapshot_from_rows(rows))
    assert len(data.posts) == 2
    assert {post.views for post in data.posts} == {100, 200}
    assert len({post.post_key for post in data.posts}) == 2
    assert "unknown_publication_identity" in data.warnings
    assert all(ref.attribution != "confirmed" for post in data.posts for ref in data.evidence[post.post_key])


@pytest.mark.parametrize("relation", ["forward", "reverse"])
@pytest.mark.parametrize("conflict", [None, "post_id", "channel", "missing_link"])
def test_missing_channel_with_existing_post_id_requires_consistent_linked_identity(relation, conflict):
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"][0].pop("视频链接")
    if relation == "forward":
        rows["posts"][0]["采集记录"] = [{"id": "ob-old"}, {"id": "ob-new"}]
        for row in rows["observations"][:2]:
            row.pop("关联发布记录")
    for row in rows["observations"][:2]:
        row["视频链接"] = "https://tiktok.com/@synthetic/video/post-0"
    if conflict == "post_id":
        rows["observations"][0]["Post ID"] = "different-post"
    elif conflict == "channel":
        rows["observations"][0]["视频链接"] = "https://youtube.com/watch?v=post-0"
    elif conflict == "missing_link":
        rows["posts"][0].setdefault("采集记录", []).append({"id": "missing-observation"})
    data = normalize(snapshot_from_rows(rows))
    post = next(post for post in data.posts if post.record_id == "release-0")
    if conflict is None:
        assert post.channel == "tiktok" and post.post_key == "tiktok:post-0"
        assert post.views == 150
        assert "publication_identity_from_observation_link" in data.warnings
    else:
        assert post.channel == "unknown"
        assert post.views is None
        assert "unknown_publication_identity" in data.warnings


def test_known_platform_duplicates_still_use_latest_observation_once():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"].append({**rows["posts"][0], "record_id": "duplicate-release"})
    data = normalize(snapshot_from_rows(rows))
    assert len(data.posts) == 3
    assert next(post for post in data.posts if post.post_key == "tiktok:post-0").views == 150


@pytest.mark.parametrize("competing", ["missing-release", "release-1", "duplicate-release"])
def test_channel_recovery_validates_all_supporting_observation_publication_links(competing):
    from ggwork_pick.feedback.analytics import analyze_feedback
    from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    if competing == "duplicate-release":
        rows["posts"].append({**rows["posts"][0], "record_id": competing})
    rows["posts"][0].pop("视频链接")
    for observation in rows["observations"][:2]:
        observation["视频链接"] = "https://tiktok.com/@synthetic/video/post-0"
        observation["关联发布记录"].append({"id": competing})
    snapshot = snapshot_from_rows(rows)
    data = normalize(snapshot)
    post = next(post for post in data.posts if post.record_id == "release-0")
    result = analyze_feedback(snapshot, FeedbackAnalysisQuery(published_from="2026-10-01", published_to="2026-10-07"), "v")
    if competing == "duplicate-release":
        assert post.channel == "tiktok" and post.views == 150
        assert result.coverage.posts == 3
    else:
        assert post.channel == "unknown" and post.views is None
        assert all(ref.attribution != "confirmed" for ref in data.evidence[post.post_key])
        assert result.coverage.posts == 2
        assert result.query_scope["unknown_identity_publications_excluded"] == 1


@pytest.mark.parametrize("competing", ["conflicting", "unidentified", "duplicate"])
def test_channel_recovery_checks_competing_forward_publication_links(competing):
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    other = {**rows["posts"][0], "record_id": "other-release", "采集记录": [{"id": "ob-old"}, {"id": "ob-new"}]}
    if competing == "conflicting":
        other["Post ID"] = "other-post"
    elif competing == "unidentified":
        other.pop("Post ID")
        other["视频链接"] = "https://youtube.com/watch?v=other-post"
    rows["posts"].append(other)
    rows["posts"][0].pop("视频链接")
    rows["posts"][0]["采集记录"] = [{"id": "ob-old"}, {"id": "ob-new"}]
    for observation in rows["observations"][:2]:
        observation.pop("关联发布记录")
        observation["视频链接"] = "https://tiktok.com/@synthetic/video/post-0"
    data = normalize(snapshot_from_rows(rows))
    post = next(post for post in data.posts if post.record_id == "release-0")
    if competing == "duplicate":
        assert post.channel == "tiktok" and post.views == 150
    else:
        assert post.channel == "unknown" and post.views is None
        assert all(ref.attribution != "confirmed" for ref in data.evidence[post.post_key])


def test_observation_matching_preserves_forward_links_to_unidentified_publications():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["posts"].append(
        {"record_id": "unidentified-release", "视频链接": "https://youtube.com/watch?v=other-post", "采集记录": [{"id": "ob-old"}, {"id": "ob-new"}]}
    )
    for observation in rows["observations"][:2]:
        observation["视频链接"] = "https://tiktok.com/@synthetic/video/post-0"
    data = normalize(snapshot_from_rows(rows))
    assert next(post for post in data.posts if post.post_key == "tiktok:post-0").views is None
    assert "unresolved_observation_links" in data.warnings


def test_cps_evidence_carries_normalized_grain_attribution_and_source_calendar_date():
    from ggwork_pick.feedback.normalize import normalize

    rows = operating_rows()
    rows["cps_auto"].append(
        {**rows["cps_auto"][0], "record_id": "confirmed-income", "剧名": [{"id": "drama-a"}], "数据粒度": ["剧目级"], "日期": "2026-10-02T17:00:00Z"}
    )
    rows["cps_manual"][0].pop("日期")
    data = normalize(snapshot_from_rows(rows))
    for observation in data.revenue:
        ref = data.evidence[f"{observation.source_lane}:{observation.record_id}"][0]
        assert ref.grain == observation.grain
        assert ref.attribution == observation.attribution
        assert ref.metric_as_of == (str(observation.metric_on) if observation.metric_on else None)
    confirmed = data.evidence["cps_auto:confirmed-income"][0]
    assert (confirmed.grain, confirmed.attribution, confirmed.metric_as_of) == ("drama", "confirmed", "2026-10-03")
    assert data.evidence["cps_auto:revenue-account"][0].attribution == "unmatched"
    assert data.evidence["cps_manual:revenue-manual"][0].attribution == "unmatched"
