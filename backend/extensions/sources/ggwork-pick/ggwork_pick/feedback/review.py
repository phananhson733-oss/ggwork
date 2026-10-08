"""Owner-private published records and explicit plan attribution; sources are read-only."""

import asyncio
from datetime import date

from ggwork_pick.completion_contracts import PlanLink, ReviewPost, ReviewPosts
from ggwork_pick.feedback.identity import bind_catalog
from ggwork_pick.feedback.normalize import channel, links, moment, normalize, rows, text_value
from ggwork_pick.feedback.repository import FeedbackRepository
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.repository import stamp

OBSERVATION_DAYS = 7


def unavailable(status, warning):
    return ReviewPosts(status=status, feedback_version_id=None, scan_completed_at=None, items=[], total=None, next_offset=None, warnings=[warning])


class ReviewService:
    def __init__(self, repository):
        self.repository = repository
        self.feedback = FeedbackRepository(repository.session_factory, repository.owner_id)

    async def dataset(self, version_id=None):
        version = await self.feedback.version(version_id) if version_id else await self.feedback.current()
        if version is None:
            return None, None
        snapshot = await self.feedback.snapshot(version["id"])
        version["source_quality"] = snapshot.source_quality
        version["release_basis"] = {
            row["record_id"]: {
                "record_id": row["record_id"],
                "accounts": sorted(links(row.get("账号"))),
                "dramas": sorted(links(row.get("剧"))),
                "post_id": text_value(row.get("Post ID")),
                "channel": channel(row),
                "state": text_value(row.get("发布状态")),
                "published_at": stamp(at) if (at := moment(row.get("实际发布时间"))) else None,
            }
            for row in rows(snapshot, "posts")
        }
        return version, await asyncio.to_thread(normalize, snapshot)

    async def identities(self, data, *, pin=None, confirmed_links=None):
        pin = pin or await self.repository.current_pin()
        catalog = await self.repository.catalog_rows(pin.catalog_id) if pin.catalog_id else []
        confirmed_links = await self.feedback.confirmed_links() if confirmed_links is None else confirmed_links
        bindings = await asyncio.to_thread(bind_catalog, data, catalog, confirmed_links)
        inverse = {}
        for identity, binding in bindings.items():
            if binding.status == "confirmed":
                for ref in binding.drama_record_ids:
                    inverse.setdefault(ref, set()).add(identity)
        return {key: next(iter(values)) for key, values in inverse.items() if len(values) == 1}

    def post(self, data, post, version, identities):
        drama = data.dramas.get(post.drama_record_id)
        evidence = data.evidence[post.post_key]
        release_ids = {ref.record_id for ref in evidence if ref.source_lane == "posts"}
        # Display one actual observation timestamp. Older nonnull fields are not
        # silently mixed into the latest snapshot's comparison window.
        metrics = {key: getattr(post, key) if post.metric_dates.get(key) == post.observed_at else None for key in ("views", "likes", "comments")}
        published_at = post.published_at if len(post.publication_times) <= 1 else None
        observed_days = (post.observed_at - published_at).days if post.observed_at and published_at and post.observed_at >= published_at else None
        complete = observed_days is not None and observed_days >= OBSERVATION_DAYS and all(value is not None for value in metrics.values())
        return ReviewPost(
            post_key=post.post_key,
            account_id=post.account_id,
            channel=post.channel if post.channel in {"youtube", "tiktok", "facebook"} else None,
            identity=identities.get(post.drama_record_id),
            title=(drama.title or "未识别剧目")[:500] if drama else "未识别剧目",
            language=drama.language if drama else None,
            published_at=stamp(published_at) if published_at else None,
            observed_at=stamp(post.observed_at) if post.observed_at else None,
            observation_days=observed_days,
            requested_observation_days=OBSERVATION_DAYS,
            window_complete=complete,
            views=metrics["views"],
            likes=metrics["likes"],
            comments=metrics["comments"],
            revenue=[entry for entry in data.revenue if entry.source_lane == "post_rs" and entry.grain == "post" and entry.record_id in release_ids],
            link=None,
            evidence_refs=[f"feedback:{version['id']}:{ref.table_id}:{ref.record_id}" for ref in evidence][:100],
        )

    async def posts(self, query):
        try:
            for value in (query.published_from, query.published_to):
                if value:
                    date.fromisoformat(value)
            if query.published_from and query.published_to and query.published_from > query.published_to:
                raise ValueError
        except ValueError:
            raise QueryFailure("invalid_query", "发布日期范围无效") from None
        version, data = await self.dataset(query.feedback_version_id)
        if version is None:
            status = await self.feedback.status()
            auth = status["last_run"] and status["last_run"]["error_code"] == "auth_required"
            return unavailable("auth_required" if auth else "unavailable", "反馈来源尚无可读取版本")
        pin = await self.repository.current_pin()
        identities = await self.identities(data, pin=pin)
        from ggwork_pick.feedback.plan_links import PlanLinkService

        current_version, current_data = await self.dataset() if query.feedback_version_id else (version, data)
        items = []
        for post in data.posts:
            drama = data.dramas.get(post.drama_record_id)
            if query.account_id and post.account_id != query.account_id or query.channel and post.channel != query.channel:
                continue
            if query.language is not None and (drama.language if drama else None) != query.language:
                continue
            day = date.isoformat(post.published_at.date()) if post.published_at and len(post.publication_times) <= 1 else None
            if (query.published_from and (day is None or day < query.published_from)) or (query.published_to and (day is None or day > query.published_to)):
                continue
            items.append(post)
        # Equal publication timestamps use ascending stable post identities.
        items.sort(key=lambda item: item.post_key)
        items.sort(key=lambda item: stamp(item.published_at) if item.published_at else "", reverse=True)
        total = len(items)
        page = [self.post(data, post, version, identities) for post in items[query.offset : query.offset + query.limit]]
        current_identities = (
            identities if current_version and current_version["id"] == version["id"] else await self.identities(current_data, pin=pin) if current_data else {}
        )
        current_links = await PlanLinkService(self.repository).projected(
            current_data, current_version, post_keys=[item.post_key for item in page], identities=current_identities
        )
        for item in page:
            link = current_links.get(item.post_key)
            item.link = PlanLink.model_validate(link) if link else None
        return ReviewPosts(
            status="ok",
            feedback_version_id=version["id"],
            scan_completed_at=version["scan_completed_at"],
            items=page,
            total=total,
            next_offset=query.offset + len(page) if query.offset + len(page) < total else None,
            warnings=[
                *data.warnings,
                *(["source_quality_" + version["source_quality"]] if version["source_quality"] != "complete" else []),
                "指标为来源累计观测；7天仅表示观察时长，不是精确7日增量。",
                "帖子账号为反馈来源记录标识，尚无已验证的资料查询账号映射。",
            ],
        )
