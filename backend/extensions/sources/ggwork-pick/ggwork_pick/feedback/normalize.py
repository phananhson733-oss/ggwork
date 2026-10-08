"""Interpret source facts without inferring matches from drama titles or currency."""

import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field

from ggwork_pick.feedback.contracts import TABLE_BY_KEY, EvidenceRef, FeedbackSnapshot, PlaybackObservation, RevenueObservation
from ggwork_pick.feedback.mapping import ExternalRegistry, RevenueResolution, canonical_identity, exact_text
from ggwork_pick.repository import stamp

SOURCE_ZONE = ZoneInfo("Asia/Shanghai")
LANGUAGES = {
    "英语": "en",
    "西班牙语": "es",
    "韩语": "ko",
    "日语": "ja",
    "中文": "zh",
    "葡萄牙语": "pt",
    "德语": "de",
    "法语": "fr",
    "俄语": "ru",
    "印尼语": "id",
    "泰语": "th",
    "阿拉伯语": "ar",
    "越南语": "vi",
    "土耳其语": "tr",
}
METRICS = {"views": "播放量", "likes": "点赞", "comments": "评论", "saves": "收藏", "shares": "转发"}
MONEY = {
    "orders": "订单数",
    "order_amount": "订单金额",
    "refund": "退款金额",
    "commission": "分成收益",
    "advertising": "广告收益",
    "brokerage": "抽佣收益",
    "bonus": "奖金",
}


def text_value(value) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], str):
        return value[0].strip() or None
    return None


def links(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item["id"] for item in value if isinstance(item, dict) and isinstance(item.get("id"), str)]


def moment(value) -> datetime | None:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("/", "-"))
            return (parsed if parsed.tzinfo else parsed.replace(tzinfo=SOURCE_ZONE)).astimezone(UTC)
        if type(value) in (int, float):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
    except (ValueError, OverflowError, OSError):
        pass
    return None


def amount(value) -> Decimal | None:
    if value is None or isinstance(value, bool) or not isinstance(value, str | int | float):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def count(value) -> int | None:
    result = amount(value)
    return int(result) if result is not None and result >= 0 and result == result.to_integral_value() else None


def rows(snapshot: FeedbackSnapshot, key: str) -> list[dict]:
    table = next(table for table in snapshot.tables if table.table_id == TABLE_BY_KEY[key].table_id)
    names = {field.field_id: field.semantic_name or field.name for field in table.fields}
    return [{"record_id": record.record_id, **{names[field_id]: value for field_id, value in record.values.items()}} for record in table.records]


def channel(row) -> str:
    value = text_value(row.get("视频链接")) or ""
    markdown = re.fullmatch(r"\[[^\[\]\n]*\]\((https?://[^\s]+)\)", value)
    if markdown:
        value = markdown.group(1)
    try:
        host = (urlparse(value).hostname or "").lower()
    except ValueError:
        return "unknown"
    for domain, name in (("tiktok.com", "tiktok"), ("youtube.com", "youtube"), ("youtu.be", "youtube"), ("facebook.com", "facebook")):
        if host == domain or host.endswith("." + domain):
            return name
    return "unknown"


@dataclass(frozen=True)
class DramaFact:
    record_id: str
    sd: str | None
    title: str | None
    language: str | None
    theater: str | None
    tags: list[str]
    catalog_identity: str | None = None
    catalog_status: str | None = None


class PostFact(PlaybackObservation):
    publication_times: list[datetime] = Field(default_factory=list, exclude=True)
    channel: str = "unknown"
    metric_dates: dict[str, datetime | None] = {}


@dataclass(frozen=True)
class FeedbackDataset:
    dramas: dict[str, DramaFact]
    posts: list[PostFact]
    revenue: list[RevenueObservation]
    evidence: dict[str, list[EvidenceRef]]
    warnings: list[str]
    transform_version: str = "feedback-v1"
    revenue_resolutions: dict[str, RevenueResolution] = dataclass_field(default_factory=dict)


def normalize(snapshot: FeedbackSnapshot) -> FeedbackDataset:
    v2 = snapshot.transform_version == "feedback-v2"
    dramas = {}
    for row in rows(snapshot, "dramas"):
        language = text_value(row.get("语言"))
        tags = row.get("剧分类")
        tags = [item for item in tags if isinstance(item, str)] if isinstance(tags, list) else [tags] if isinstance(tags, str) and tags else []
        dramas[row["record_id"]] = DramaFact(
            row["record_id"],
            text_value(row.get("剧ID")),
            text_value(row.get("剧名")),
            LANGUAGES.get(language, language),
            text_value(row.get("平台")),
            tags,
            exact_text(row.get("选剧台剧集ID")) if v2 else None,
            text_value(row.get("选剧台对应状态")) if v2 else None,
        )
    grouped, by_release, external_ids, evidence, warnings = {}, {}, {}, {}, set()
    identities, observation_links, publication_links = {}, {}, {}
    source_publications = {row["record_id"]: row for row in rows(snapshot, "posts")}
    source_observations = {row["record_id"]: row for row in rows(snapshot, "observations")}
    for publication in source_publications.values():
        for observation in links(publication.get("采集记录")):
            publication_links.setdefault(observation, set()).add(publication["record_id"])
    for observation in source_observations.values():
        publication_links.setdefault(observation["record_id"], set()).update(links(observation.get("关联发布记录")))
    for observation, releases in publication_links.items():
        for release in releases:
            observation_links.setdefault(release, set()).add(observation)
    for row in rows(snapshot, "posts") + rows(snapshot, "observations"):
        try:
            urlparse(text_value(row.get("视频链接")) or "").hostname
        except ValueError:
            warnings.add("invalid_video_url")
    for row in rows(snapshot, "posts"):
        post_id = text_value(row.get("Post ID"))
        post_channel = channel(row)
        if not post_id or post_channel == "unknown":
            refs = observation_links.get(row["record_id"], set())
            if refs and refs.issubset(source_observations):
                linked = [source_observations[ref] for ref in refs]
                ids = {text_value(observation.get("Post ID")) for observation in linked}
                if post_id:
                    ids.add(post_id)
                channels = {channel(observation) for observation in linked} | {post_channel}
                channels.discard("unknown")
                if len(ids) == 1 and None not in ids and len(channels) == 1:
                    inferred_id, inferred_channel = next(iter(ids)), next(iter(channels))
                    competing_refs = {ref for observation in refs for ref in publication_links[observation]} - {row["record_id"]}
                    if competing_refs.issubset(source_publications) and all(
                        text_value(source_publications[ref].get("Post ID")) == inferred_id and channel(source_publications[ref]) == inferred_channel
                        for ref in competing_refs
                    ):
                        post_id, post_channel = inferred_id, inferred_channel
                        warnings.add("publication_identity_from_observation_link")
        if not post_id:
            warnings.add("unidentified_publications")
            continue
        key = f"{post_channel}:{post_id}"
        if post_channel == "unknown":
            key = f"unknown:{row['record_id']}"
            warnings.add("unknown_publication_identity")
        identities[key] = (post_channel, post_id)
        grouped.setdefault(key, []).append(row)
        by_release[row["record_id"]] = key
        drama_refs = set(links(row.get("剧")))
        external = text_value(row.get("剧ID（RS Boost）"))
        if len(drama_refs) == 1 and external:
            drama = dramas.get(next(iter(drama_refs)))
            if drama and drama.theater:
                external_ids.setdefault(("RSBoost", drama.theater.casefold(), external), set()).add(drama.record_id)
    observations = {}
    for row in rows(snapshot, "observations"):
        publication_refs = publication_links[row["record_id"]]
        if not publication_refs.issubset(by_release):
            warnings.add("unresolved_observation_links")
            continue
        matches = {by_release[ref] for ref in publication_refs}
        observed_id = text_value(row.get("Post ID"))
        direct_key = f"{channel(row)}:{observed_id}" if observed_id and channel(row) != "unknown" else None
        if direct_key in grouped:
            matches.add(direct_key)
        if len(matches) != 1:
            warnings.add("unmatched_observations")
            continue
        key = next(iter(matches))
        expected_channel, expected_id = identities[key]
        if (observed_id and expected_id != observed_id) or (channel(row) != "unknown" and channel(row) != expected_channel):
            warnings.add("conflicting_observation_identity")
            continue
        missing = row.get("缺失字段") or []
        status = text_value(row.get("采集状态"))
        invalid = status is not None and status.casefold() not in {"成功", "采集成功", "完整", "部分成功", "部分", "success", "complete", "ok", "partial"}
        if invalid:
            warnings.add("observation_status_invalid")
        elif status and status.casefold() in {"部分成功", "部分", "partial"}:
            warnings.add("observation_status_partial")
        if missing:
            warnings.add("source_metrics_explicitly_missing")
        for metric, field in METRICS.items():
            if invalid or field in missing or metric in missing or (metric == "saves" and "favorites" in missing):
                row[field] = None
        observations.setdefault(key, []).append(row)
    posts = []
    for key, releases in grouped.items():
        drama_ids = {ref for row in releases for ref in links(row.get("剧"))}
        if len(drama_ids) > 1 or not drama_ids.issubset(dramas):
            warnings.add("ambiguous_post_drama")
        drama_id = next(iter(drama_ids)) if len(drama_ids) == 1 and drama_ids.issubset(dramas) else None
        account_ids = {ref for row in releases for ref in links(row.get("账号"))}
        published = [value for row in releases if (value := moment(row.get("实际发布时间"))) is not None]
        observed = observations.get(key, [])
        published.extend(value for row in observed if (value := moment(row.get("发布时间"))) is not None)
        dated = [(moment(row.get("采集时间")) or moment(row.get("快照日期")), row) for row in observed]
        dated = [(at, row) for at, row in dated if at is not None]
        if len(dated) != len(observed):
            warnings.add("undated_observations")
        publication_states = {text_value(row.get("发布状态")) for row in releases} - {None}
        public = not publication_states or publication_states.issubset({"已发布", "发布成功", "已公开", "已回填", "published", "public"})
        if not public:
            warnings.add("publication_not_confirmed_public")
            published = []
        publication_times = sorted(set(published))
        if len(publication_times) > 1:
            warnings.add("conflicting_publication_times")
        latest_at = max((at for at, _row in dated), default=None)
        metrics, metric_dates, used_rows = {}, {}, {}
        for metric, field in METRICS.items():
            metric_at = max((at for at, row in dated if field in row), default=None)
            metric_latest = [row for at, row in dated if at == metric_at and field in row]
            metric_dates[metric] = metric_at
            used_rows.update({row["record_id"]: (metric_at, row) for row in metric_latest})
            values = {count(row.get(field)) for row in metric_latest}
            metrics[metric] = values.pop() if len(values) == 1 else None
            if len({str(row.get(field)) for row in metric_latest}) > 1:
                warnings.add("conflicting_observations")
        if not public:
            metrics = dict.fromkeys(METRICS)
        quality = (
            "complete"
            if all(value is not None for value in metrics.values())
            else "partial"
            if any(value is not None for value in metrics.values())
            else "unknown"
        )
        post = PostFact(
            post_key=key,
            record_id=releases[0]["record_id"],
            drama_record_id=drama_id,
            account_id=next(iter(account_ids)) if len(account_ids) == 1 else None,
            channel=key.split(":", 1)[0],
            published_at=min(published) if published else None,
            publication_times=publication_times,
            observed_at=latest_at,
            metric_dates=metric_dates,
            quality=quality,
            **metrics,
        )
        posts.append(post)
        evidence[key] = [
            EvidenceRef(
                table_id=TABLE_BY_KEY["posts"].table_id,
                record_id=row["record_id"],
                grain="post",
                attribution="confirmed" if drama_id and post.channel != "unknown" else "ambiguous",
                source_lane="posts",
            )
            for row in releases
        ]
        evidence[key].extend(
            EvidenceRef(
                table_id=TABLE_BY_KEY["observations"].table_id,
                record_id=row["record_id"],
                metric_as_of=stamp(metric_at),
                grain="post",
                attribution="confirmed" if drama_id and post.channel != "unknown" else "ambiguous",
                source_lane="observations",
            )
            for metric_at, row in used_rows.values()
        )
    revenue = []
    resolutions = {}
    registry_rows = rows(snapshot, "external_ids") if v2 else []
    registry = ExternalRegistry(registry_rows, dramas) if v2 else None
    if v2:
        for drama in dramas.values():
            if canonical_identity(drama.catalog_identity) is None:
                warnings.add("master_identity_invalid" if drama.catalog_identity else "master_identity_unconfirmed")
            elif drama.catalog_status != "已确认":
                warnings.add("master_identity_unconfirmed")
    for lane in ("cps_auto", "cps_manual"):
        for row in rows(snapshot, lane):
            grain = {"账号级": "account", "平台级": "platform", "平台合计": "platform", "剧目级": "drama", "单剧": "drama", "视频级": "post"}.get(
                text_value(row.get("数据粒度")), "unknown"
            )
            if registry is not None:
                resolution = registry.resolve(row, lane, grain)
                resolutions[f"{lane}:{row['record_id']}"] = resolution
                drama_id, attribution = resolution.drama_id, resolution.attribution
                warnings.update(resolution.warnings)
            else:
                refs = set(links(row.get("剧名")))
                external = text_value(row.get("来源剧目ID"))
                theater = text_value(row.get("剧场"))
                if external and theater:
                    refs |= external_ids.get(("RSBoost", theater.casefold(), external), set()) if text_value(row.get("合作方")) == "RSBoost" else set()
                if len(refs) > 1 or not refs.issubset(dramas):
                    warnings.add("ambiguous_revenue_drama")
                drama_id = next(iter(refs)) if len(refs) == 1 and refs.issubset(dramas) and grain == "drama" else None
                attribution = "confirmed" if drama_id else "ambiguous" if refs else "unmatched"
            metric_at = moment(row.get("日期"))
            for metric, name in MONEY.items():
                if metric == "orders" and lane == "cps_manual":
                    name = "订单数（单剧）"
                if name not in row:
                    continue
                revenue.append(
                    RevenueObservation(
                        record_id=row["record_id"],
                        source_lane=lane,
                        grain=grain,
                        currency=text_value(row.get("币种")) or "UNKNOWN",
                        metric=metric,
                        amount=Decimal(count(row[name]))
                        if metric == "orders" and count(row[name]) is not None
                        else None
                        if metric == "orders"
                        else amount(row[name]),
                        drama_record_id=drama_id,
                        metric_on=metric_at.astimezone(SOURCE_ZONE).date() if metric_at else None,
                        amount_basis=text_value(row.get("订单金额口径")),
                        attribution=attribution,
                    )
                )
    for row in rows(snapshot, "posts"):
        if "RS收益" in row:
            revenue.append(
                RevenueObservation(
                    record_id=row["record_id"],
                    source_lane="post_rs",
                    grain="post",
                    currency="UNKNOWN",
                    metric="commission",
                    amount=amount(row["RS收益"]),
                    attribution="unmatched",
                )
            )
            warnings.add("post_rs_currency_and_attribution_unverified")
    for lane in ("dramas", "cps_auto", "cps_manual"):
        for row in rows(snapshot, lane):
            evidence[f"{lane}:{row['record_id']}"] = [EvidenceRef(table_id=TABLE_BY_KEY[lane].table_id, record_id=row["record_id"], source_lane=lane)]
    for observation in revenue:
        if observation.source_lane == "post_rs":
            continue
        evidence[f"{observation.source_lane}:{observation.record_id}"] = [
            EvidenceRef(
                table_id=TABLE_BY_KEY[observation.source_lane].table_id,
                record_id=observation.record_id,
                source_lane=observation.source_lane,
                grain=observation.grain,
                attribution=observation.attribution,
                metric_as_of=str(observation.metric_on) if observation.metric_on else None,
            )
        ]
    if v2:
        for key, resolution in resolutions.items():
            if key not in evidence:
                continue
            evidence[key].extend(
                EvidenceRef(table_id=TABLE_BY_KEY["external_ids"].table_id, record_id=record_id, source_lane="external_ids", attribution=resolution.attribution)
                for record_id in resolution.mapping_ids
            )
            for drama_id in resolution.related_drama_ids:
                evidence[key].extend(evidence[f"dramas:{drama_id}"])
    source_records = {(table.table_id, record.record_id): record for table in snapshot.tables for record in table.records}
    for refs in evidence.values():
        for ref in refs:
            ref.field_ids = sorted(source_records[(ref.table_id, ref.record_id)].values)
    warnings.add("stop_refresh_after_30_days")
    if snapshot.source_quality != "complete":
        warnings.add(f"source_quality_{snapshot.source_quality}")
    return FeedbackDataset(dramas, sorted(posts, key=lambda post: post.post_key), revenue, evidence, sorted(warnings), snapshot.transform_version, resolutions)
