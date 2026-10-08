"""Manual, owner-fenced attribution. No inferred or external writes are permitted."""

import hashlib
import json
from uuid import uuid4

from sqlalchemy import insert, select, update

from ggwork_pick.completion_contracts import PlanLink
from ggwork_pick.feedback.mapping import canonical_identity
from ggwork_pick.feedback.review import ReviewService
from ggwork_pick.models import feedback_plan_link_commands as commands
from ggwork_pick.models import feedback_plan_links as links
from ggwork_pick.models import feedback_scopes
from ggwork_pick.planning import PlanConflict, PlanningService
from ggwork_pick.query_reader import QueryFailure
from ggwork_pick.repository import _fits, stamp


def source_basis(data, post, version):
    drama = data.dramas.get(post.drama_record_id)
    identity = canonical_identity(drama.catalog_identity) if drama and drama.catalog_status == "已确认" else None
    return {
        "releases": [version["release_basis"][ref.record_id] for ref in data.evidence[post.post_key] if ref.source_lane == "posts"],
        "post_key": post.post_key,
        "channel": post.channel,
        "account_id": post.account_id,
        "drama_record_id": post.drama_record_id,
        "identity": identity,
        "language": drama.language if drama else None,
        "theater": drama.theater if drama else None,
        "published_at": stamp(post.published_at) if post.published_at else None,
        "release_refs": sorted(f"{ref.table_id}:{ref.record_id}" for ref in data.evidence[post.post_key] if ref.source_lane == "posts"),
    }


def compatible(row, basis):
    if basis["identity"] and row["identity"] != basis["identity"]:
        return False
    if row["channel"] and basis["channel"] != "unknown" and row["channel"] != basis["channel"]:
        return False
    if basis["language"] and row["language"] and basis["language"] != row["language"]:
        return False
    if basis["theater"] and row["theater"] and basis["theater"].casefold() != row["theater"].casefold():
        return False
    # Plan account is free text, source account is a linked record ID. Their names
    # are not an identity bridge; explicit manual confirmation preserves both.
    return True


class PlanLinkService:
    def __init__(self, repository):
        self.repository = repository
        self.owner = repository.owner_id
        self.plans = PlanningService(repository)
        self.review = ReviewService(repository)

    async def _stored(self, session):
        return [dict(row) for row in (await session.execute(select(links).where(links.c.owner_id == self.owner))).mappings()]

    async def _command(self, session, body):
        digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        previous = (
            (await session.execute(select(commands).where(commands.c.owner_id == self.owner, commands.c.request_id == body.request_id))).mappings().first()
        )
        if previous is not None and previous["payload_hash"] != digest:
            raise QueryFailure("link_conflict", "重复关联请求的内容不同，原关联保持不变")
        return digest, previous

    async def _plan_row(self, session, body):
        plan = await self.plans._view(session, body.plan_id)
        if plan["version"] != body.expected_plan_version:
            raise PlanConflict(plan["version"])
        row = next((r for r in plan["rows"] if r["row_id"] == body.row_id), None)
        if row is None:
            raise LookupError("计划行不可用")
        return plan, row

    async def create(self, body):
        # Retrying a completed command returns its immutable receipt even if its
        # source/plan has since changed; new commands must revalidate everything.
        async with self.repository._write() as session:
            _, previous = await self._command(session, body)
            if previous is not None:
                return previous["receipt_json"]
            _, row = await self._plan_row(session, body)
        version, data = await self.review.dataset(body.feedback_version_id)
        post = next((p for p in data.posts if p.post_key == body.post_key), None)
        if post is None:
            raise LookupError("反馈帖子不可用")
        basis = source_basis(data, post, version)
        if not compatible(row, basis):
            raise QueryFailure("link_conflict", "帖子与计划行的已知身份或渠道不一致，无法确认")
        latest, current_data = await self.review.dataset()
        current_post = next((p for p in current_data.posts if p.post_key == body.post_key), None) if current_data else None
        if current_post is None or source_basis(current_data, current_post, latest) != basis:
            raise QueryFailure("version_conflict", "帖子来源证据已变化，请读取当前记录后重新确认")
        item = self.review.post(data, post, version, {})
        basis = {**basis, "plan_row": row}
        async with self.repository._write() as session:
            digest, previous = await self._command(session, body)
            if previous is not None:
                return previous["receipt_json"]
            _, current_row = await self._plan_row(session, body)
            if current_row != row:
                raise QueryFailure("version_conflict", "计划行已变化，请重新确认")
            # Same scope-row lock order as source publication: its current pointer
            # cannot change between this check and the private receipt commit.
            current_version = await session.scalar(
                select(feedback_scopes.c.current_version_id).where(feedback_scopes.c.owner_id == self.owner).with_for_update()
            )
            if current_version != latest["id"]:
                raise QueryFailure("version_conflict", "反馈来源已更新，请重新读取后确认")
            existing = await self._stored(session)
            old = next((entry for entry in existing if entry["post_key"] == body.post_key), None)
            if old is not None and (old["plan_id"], old["row_id"]) != (body.plan_id, body.row_id):
                raise QueryFailure("link_conflict", "这个帖子已经关联其他计划行，原关联保持不变")
            for entry in existing:
                if entry["post_key"] != body.post_key and set(entry["basis_json"]["release_refs"]) & set(basis["release_refs"]):
                    raise QueryFailure("link_conflict", "此来源发布记录已有不同帖子身份的关联，需要核对原记录")
            receipt = PlanLink(
                id=uuid4().hex,
                plan_id=body.plan_id,
                row_id=body.row_id,
                plan_version=body.expected_plan_version,
                feedback_version_id=body.feedback_version_id,
                post_key=body.post_key,
                method="manual",
                status="confirmed",
                evidence_refs=[*item.evidence_refs[:99], f"plan:{body.plan_id}:v{body.expected_plan_version}:{body.row_id}"],
                created_at=stamp(),
            ).model_dump(mode="json")
            if old is not None and old["basis_json"] == basis and old["receipt_json"]["plan_version"] == body.expected_plan_version:
                receipt = old["receipt_json"]
            elif old is not None:
                await session.execute(
                    update(links).where(links.c.owner_id == self.owner, links.c.post_key == body.post_key).values(receipt_json=receipt, basis_json=basis)
                )
            else:
                await session.execute(
                    insert(links).values(
                        **_fits(
                            links,
                            dict(owner_id=self.owner, post_key=body.post_key, plan_id=body.plan_id, row_id=body.row_id, receipt_json=receipt, basis_json=basis),
                        )
                    )
                )
            await session.execute(
                insert(commands).values(
                    **_fits(
                        commands,
                        dict(owner_id=self.owner, request_id=body.request_id, payload_hash=digest, receipt_json=receipt, basis_json=basis, created_at=stamp()),
                    )
                )
            )
            return receipt

    async def projected(self, data=None, version=None):
        async with self.repository.session_factory() as session:
            stored = await self._stored(session)
            plans = {}
            for entry in stored:
                if entry["plan_id"] not in plans:
                    try:
                        plans[entry["plan_id"]] = await self.plans._view(session, entry["plan_id"])
                    except LookupError:
                        plans[entry["plan_id"]] = None
        output = {}
        for entry in stored:
            receipt = entry["receipt_json"]
            basis = entry["basis_json"]
            plan = plans[entry["plan_id"]]
            post = next((p for p in data.posts if p.post_key == entry["post_key"]), None) if data else None
            current = source_basis(data, post, version) if post else None
            status = "confirmed"
            if current is None or plan is None or plan["version"] != receipt["plan_version"]:
                status = "needs_review"
            if current is not None and current != {k: v for k, v in basis.items() if k != "plan_row"}:
                status = (
                    "conflict"
                    if any(current[k] != basis[k] for k in ("channel", "account_id", "drama_record_id", "identity", "language", "theater"))
                    else "needs_review"
                )
            output[entry["post_key"]] = {**receipt, "status": status}
        return output

    async def list(self, plan_id, *, source_enabled):
        await self.plans.get(plan_id)
        version, data = await self.review.dataset() if source_enabled else (None, None)
        return {"items": [item for item in (await self.projected(data, version)).values() if item["plan_id"] == plan_id]}
