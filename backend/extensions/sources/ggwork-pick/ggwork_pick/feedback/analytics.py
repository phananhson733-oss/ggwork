"""Deterministic feedback statistics over the complete requested population."""

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from ggwork_pick.feedback.contracts import FeedbackAnalysisQuery, FeedbackCoverage, FeedbackItem, FeedbackReply
from ggwork_pick.feedback.identity import bind_catalog
from ggwork_pick.feedback.normalize import METRICS, SOURCE_ZONE, normalize
from ggwork_pick.repository import stamp


def coverage(posts):
    measured = sum(post.views is not None for post in posts)
    return FeedbackCoverage(
        dramas=len({post.drama_record_id for post in posts if post.drama_record_id}),
        posts=len(posts),
        measured_posts=measured,
        missing_posts=len(posts) - measured,
        unmatched_posts=sum(post.drama_record_id is None for post in posts),
    )


def _item(data, key, posts, drama_ids, now, *, kind="cohort", include_revenue=True):
    metrics = {}
    warnings = set(data.warnings)
    if data.transform_version == "feedback-v2":
        warnings = {warning for warning in warnings if not _mapping_warning(warning)}
    evidence = [ref for post in posts for ref in data.evidence[post.post_key]]
    for drama_id in sorted(drama_ids):
        evidence.extend(data.evidence.get(f"dramas:{drama_id}", []))
    if data.transform_version == "feedback-v2":
        for source_key, resolution in data.revenue_resolutions.items():
            if drama_ids.intersection(resolution.related_drama_ids):
                warnings.update(resolution.warnings)
                evidence.extend(data.evidence.get(source_key, []))
    for metric in METRICS:
        values = [Decimal(getattr(post, metric)) for post in posts if getattr(post, metric) is not None]
        metrics[f"{metric}_total"] = str(sum(values)) if values else None
        metrics[f"{metric}_mean"] = str(sum(values) / len(values)) if values else None
        metrics[f"{metric}_median"] = str(median(values)) if values else None
        metrics[f"{metric}_measured_posts"] = len(values)
        metric_dates = [post.metric_dates[metric] for post in posts if post.metric_dates.get(metric)]
        metrics[f"{metric}_as_of_min"] = stamp(min(metric_dates)) if metric_dates else None
        metrics[f"{metric}_as_of_max"] = stamp(max(metric_dates)) if metric_dates else None
    ages = [(now - post.published_at).total_seconds() / 86400 for post in posts if post.published_at and now >= post.published_at]
    metrics["publication_age_days_min"] = str(min(ages)) if ages else None
    metrics["publication_age_days_max"] = str(max(ages)) if ages else None
    metrics["publication_age_unknown_posts"] = len(posts) - len(ages)
    metrics["publication_age_under_1_day_posts"] = sum(age < 1 for age in ages)
    metrics["publication_age_1_to_7_days_posts"] = sum(1 <= age < 7 for age in ages)
    metrics["publication_age_7_to_30_days_posts"] = sum(7 <= age < 30 for age in ages)
    metrics["publication_age_30_plus_days_posts"] = sum(age >= 30 for age in ages)
    dates = [post.observed_at for post in posts if post.observed_at]
    metrics["metric_as_of_min"] = stamp(min(dates)) if dates else None
    metrics["metric_as_of_max"] = stamp(max(dates)) if dates else None
    buckets = defaultdict(list)
    if not include_revenue:
        warnings.add("revenue_scope_unavailable")
    for observation in data.revenue if include_revenue else ():
        if observation.drama_record_id not in drama_ids or observation.attribution != "confirmed":
            continue
        buckets[(observation.source_lane, observation.grain, observation.currency, observation.metric, observation.amount_basis)].append(observation)
        evidence.extend(data.evidence.get(f"{observation.source_lane}:{observation.record_id}", []))
    revenue_dates = [row.metric_on for observations in buckets.values() for row in observations if row.metric_on]
    metrics["revenue_metric_on_min"] = str(min(revenue_dates)) if revenue_dates else None
    metrics["revenue_metric_on_max"] = str(max(revenue_dates)) if revenue_dates else None
    metrics["revenue_undated_records"] = sum(row.metric_on is None for observations in buckets.values() for row in observations)
    revenue = []
    for (lane, grain, currency, metric, basis), observations in sorted(buckets.items(), key=lambda item: str(item[0])):
        amounts = [row.amount for row in observations if row.amount is not None]
        can_total = metric == "orders" or (currency != "UNKNOWN" and (metric != "order_amount" or basis not in (None, "待确认")))
        if not can_total:
            warnings.add("revenue_amount_basis_or_currency_unknown")
        revenue.append(
            dict(
                source_lane=lane,
                grain=grain,
                currency=currency,
                metric=metric,
                amount=sum(amounts) if amounts and can_total else None,
                amount_basis=basis,
                records=len(observations),
                missing_records=len(observations) - len(amounts),
            )
        )
    if not posts or not any(post.views is not None for post in posts):
        warnings.add("no_measured_playback")
    if revenue:
        warnings.add("revenue_lanes_not_additive")
        warnings.add("revenue_dates_not_publication_window")
    warnings.add("cumulative_observations_not_fixed_age_comparison")
    return FeedbackItem(
        key=key,
        evidence_kind=kind,
        metrics=metrics,
        coverage=coverage(posts),
        revenue=revenue,
        evidence_refs=list({(ref.table_id, ref.record_id): ref for ref in evidence}.values()),
        warnings=sorted(warnings),
    )


def _reply(snapshot, version_id, freshness, verified_at, **kwargs):
    return FeedbackReply(
        status="ok",
        feedback_version_id=version_id,
        scan_started_at=snapshot.scan_started_at,
        scan_completed_at=snapshot.scan_completed_at,
        last_verified_at=verified_at or snapshot.scan_completed_at,
        freshness=freshness,
        source_quality=snapshot.source_quality,
        **kwargs,
    )


def analyze_feedback(snapshot, query: FeedbackAnalysisQuery, version_id: str, *, now=None, freshness="fresh_scan", verified_at=None):
    if query.group_by == "country":
        return FeedbackReply(status="unsupported_dimension", notice="当前没有实际观众或付费国家数据；语言及币种不能推断地区。")
    now = now or datetime.now(UTC)
    today = now.astimezone(SOURCE_ZONE).date()
    end = query.published_to or today
    start = query.published_from or end - timedelta(days=29)
    data = normalize(snapshot)
    posts = []
    for post in data.posts:
        if post.channel == "unknown":
            continue
        drama = data.dramas.get(post.drama_record_id)
        if not post.published_at or not start <= post.published_at.astimezone(SOURCE_ZONE).date() <= end:
            continue
        if query.language and (not drama or drama.language != query.language):
            continue
        if query.theater and (not drama or (drama.theater or "").casefold() != query.theater.casefold()):
            continue
        if query.tags and (not drama or not set(query.tags).issubset(drama.tags)):
            continue
        if query.channel and post.channel != query.channel or query.account_id and post.account_id != query.account_id:
            continue
        posts.append(post)
    groups = defaultdict(list)
    for post in posts:
        drama = data.dramas.get(post.drama_record_id)
        labels = drama.tags if drama and query.group_by == "genre" else [getattr(drama, query.group_by, None)]
        for label in set(labels or [None]):
            groups[label or "unknown"].append(post)
    include_revenue = query.account_id is None and query.channel is None
    items = [
        _item(data, key, group, {p.drama_record_id for p in group if p.drama_record_id}, now, include_revenue=include_revenue)
        for key, group in sorted(groups.items())
    ]
    group_context = {}
    for item in items[query.offset : query.offset + query.limit]:
        group = groups[item.key]
        context = {
            "account_ids": sorted({p.account_id for p in group if p.account_id}),
            "unknown_account_posts": sum(p.account_id is None for p in group),
            "channel_counts": dict(Counter(p.channel for p in group)),
        }
        context["account_count"] = len(context["account_ids"])
        for dimension in ("language", "theater"):
            context[f"{dimension}_counts"] = dict(Counter(getattr(data.dramas.get(p.drama_record_id), dimension, None) or "unknown" for p in group))
        if context["account_count"] > 1 or any(len(context[f"{dimension}_counts"]) > 1 for dimension in ("language", "theater", "channel")):
            item.warnings.append("mixed_context")
        group_context[item.key] = context
    scope = query.model_dump(mode="json") | {
        "published_from": str(start),
        "published_to": str(end),
        "timezone": "Asia/Shanghai",
        "group_context": group_context,
        "revenue_scope": "all_dates_of_matched_dramas" if include_revenue else "unavailable_for_account_or_channel_filter",
        "stop_refresh_after_days": 30,
        "population": "unique_posts_by_publication_date",
        "multi_label_groups_additive": False,
        "undated_publications_excluded": sum(p.published_at is None for p in data.posts),
        "unknown_identity_publications_excluded": sum(p.channel == "unknown" for p in data.posts),
        "unknown_identity_exclusion_scope": "source_snapshot_all_publications",
        "unattributed_revenue_observations_excluded": sum(row.attribution != "confirmed" for row in data.revenue),
    }
    if data.transform_version == "feedback-v2":
        scope.update(_mapping_scope(data))
    warnings = sorted(set(data.warnings) | {"genre_groups_non_additive", "missing_is_not_zero", "no_fixed_age_or_conversion_claim"})
    if not include_revenue:
        warnings.append("revenue_scope_unavailable")
    if start <= today <= end:
        warnings.append("current_day_incomplete")
    return _reply(
        snapshot,
        version_id,
        freshness,
        verified_at,
        query_scope=scope,
        coverage=coverage(posts),
        warnings=warnings,
        items=items[query.offset : query.offset + query.limit],
        total_groups=len(items),
        has_more=query.offset + query.limit < len(items),
    )


def drama_feedback(
    snapshot, catalog_rows: list[dict], version_id: str, *, now=None, freshness="fresh_scan", verified_at=None, candidate_identities: set[str] | None = None
):
    now = now or datetime.now(UTC)
    data = normalize(snapshot)
    bindings = bind_catalog(data, catalog_rows)
    items, selected = [], {}
    for identity, binding in bindings.items():
        if candidate_identities is not None and identity not in candidate_identities:
            continue
        drama_ids = set(binding.drama_record_ids)
        posts = [post for post in data.posts if post.drama_record_id in drama_ids and post.channel != "unknown"]
        selected.update({post.post_key: post for post in posts})
        item = _item(data, identity, posts, drama_ids, now, kind="direct" if binding.status == "confirmed" else "unknown")
        if data.transform_version == "feedback-v2":
            item.warnings = sorted(set(item.warnings) | set(binding.warnings))
            known_refs = {(ref.table_id, ref.record_id) for ref in item.evidence_refs}
            for drama_id in binding.evidence_drama_ids:
                item.evidence_refs.extend(ref for ref in data.evidence[f"dramas:{drama_id}"] if (ref.table_id, ref.record_id) not in known_refs)
        item.metrics["identity_method"] = binding.method
        item.metrics["identity_status"] = binding.status
        if binding.status != "confirmed":
            item.warnings.append("no_confirmed_match")
        items.append(item)
    mapping_scope = {}
    warnings = set(data.warnings)
    if data.transform_version == "feedback-v2":
        mapping_scope = _mapping_scope(data)
        mapping_scope["catalog_binding_counts"] = dict(Counter(binding.status for binding in bindings.values()))
        mapping_scope["catalog_binding_scope"] = "catalog_population_before_candidate_filter"
        warnings.update(warning for binding in bindings.values() for warning in binding.warnings)
    return _reply(
        snapshot,
        version_id,
        freshness,
        verified_at,
        items=items,
        coverage=coverage(list(selected.values())),
        query_scope={
            **mapping_scope,
            "population": "all_publications_of_confirmed_catalog_identities",
            "unknown_identity_publications_excluded": sum(p.channel == "unknown" for p in data.posts),
            "unknown_identity_exclusion_scope": "source_snapshot_all_publications",
        },
        warnings=sorted(warnings),
        total_groups=len(items),
    )


def _mapping_warning(warning):
    return warning.startswith(("master_identity_", "external_mapping_", "revenue_direct_link_", "revenue_grain_"))


def _mapping_scope(data):
    records = {(row.source_lane, row.record_id) for row in data.revenue}
    excluded = {(row.source_lane, row.record_id) for row in data.revenue if row.attribution != "confirmed"}
    return {
        "mapping_diagnostics_scope": "source_snapshot",
        "revenue_records": len(records),
        "attributed_revenue_records": len(records - excluded),
        "unattributed_revenue_records_excluded": len(excluded),
        "revenue_record_exclusion_scope": "source_snapshot_all_revenue_lanes",
        "unattributed_revenue_observations_excluded": sum(row.attribution != "confirmed" for row in data.revenue),
    }
