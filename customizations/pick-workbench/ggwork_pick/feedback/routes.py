"""Minimal authenticated feedback status; never return raw manifests or finance records."""

import asyncio

from fastapi import HTTPException, Request
from pydantic import ValidationError
from sqlalchemy.exc import DBAPIError

from ggwork_pick.feedback.contracts import TABLE_BY_ID


def register_feedback_routes(router, service, repository):
    def allowed(request):
        owner = repository(request).owner_id
        feedback = service.feedback
        if feedback is None or not feedback.enabled:
            return owner, None
        try:
            feedback.repository(owner)
        except PermissionError:
            raise HTTPException(403, "当前用户未获反馈来源授权") from None
        return owner, feedback

    @router.get("/feedback/status")
    async def feedback_status(request: Request):
        owner, feedback = allowed(request)
        if feedback is None:
            return {"enabled": False, "configured": False, "current": None, "running": None, "last_run": None, "last_verified_at": None, "lease_expired": False}
        state = await feedback.repository(owner).status()
        current = state["current"]
        if current:
            tables = current["manifest_json"]["tables"]
            current = {
                "id": current["id"],
                "scan_started_at": current["scan_started_at"],
                "scan_completed_at": current["scan_completed_at"],
                "source_quality": "partial"
                if any(table["source_quality"] == "partial" for table in tables)
                else "unknown"
                if any(table["source_quality"] == "unknown" for table in tables)
                else "complete",
                "tables": [
                    {
                        "table_id": table["table_id"],
                        "name": TABLE_BY_ID[table["table_id"]].name,
                        "complete": table["complete"],
                        "source_quality": table["source_quality"],
                    }
                    for table in tables
                ],
            }

        def public_run(run):
            return {key: run[key] for key in ("id", "status", "trigger", "started_at", "finished_at", "version_id", "error_code")} if run else None

        return {
            "enabled": True,
            "configured": True,
            "current": current,
            "last_verified_at": state["last_verified_at"],
            "running": public_run(state["running"]),
            "last_run": public_run(state["last_run"]),
            "lease_expired": state["lease_expired"],
        }

    @router.post("/feedback/sync", status_code=202)
    async def feedback_sync(request: Request):
        owner, feedback = allowed(request)
        if feedback is None:
            raise HTTPException(409, "反馈同步未启用")
        outcome = await feedback.refresh(owner, wait_seconds=0.05, trigger="manual")
        return {"status": outcome.status, "run_id": outcome.run_id, "error_code": outcome.error_code}

    from fastapi import Query

    from ggwork_pick.completion_contracts import Channel, CompletionError, ReviewPosts, ReviewQuery
    from ggwork_pick.feedback.review import ReviewService, unavailable
    from ggwork_pick.query_reader import QueryFailure

    @router.get("/feedback/posts", response_model=ReviewPosts)
    async def review_posts(
        request: Request,
        feedback_version_id: str | None = Query(None, min_length=1, max_length=64),
        account_id: str | None = Query(None, min_length=1, max_length=128),
        channel: Channel | None = None,
        language: str | None = Query(None, max_length=40),
        published_from: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        published_to: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        repo = repository(request)
        try:
            query = ReviewQuery(
                feedback_version_id=feedback_version_id,
                account_id=account_id,
                channel=channel,
                language=language,
                published_from=published_from,
                published_to=published_to,
                offset=offset,
                limit=limit,
            )
        except ValidationError:
            raise HTTPException(422, CompletionError(code="invalid_query", message="复盘筛选条件无效", retryable=False).model_dump()) from None
        feedback = service.feedback
        if feedback is None or not feedback.enabled:
            return unavailable("disabled", "反馈来源未启用")
        try:
            feedback.repository(repo.owner_id)
        except PermissionError:
            return unavailable("auth_required", "当前账号没有反馈来源读取权限")
        try:
            async with asyncio.timeout(10):
                return await ReviewService(repo).posts(query)
        except TimeoutError:
            raise HTTPException(504, CompletionError(code="query_timeout", message="反馈读取超时，请重试", retryable=True).model_dump()) from None
        except (DBAPIError, ValidationError):
            raise HTTPException(503, CompletionError(code="source_unavailable", message="反馈记录暂不可读取", retryable=True).model_dump()) from None
        except LookupError:
            raise HTTPException(404, CompletionError(code="not_found", message="反馈版本不可用", retryable=False).model_dump()) from None
        except QueryFailure as exc:
            raise HTTPException(422, CompletionError(code=exc.code, message=str(exc), retryable=exc.retryable).model_dump()) from None

    from ggwork_pick.completion_contracts import PlanLink, PlanLinkCommand, PlanLinkList
    from ggwork_pick.feedback.plan_links import PlanLinkService

    async def linked_answer(work):
        try:
            async with asyncio.timeout(10):
                return await work
        except TimeoutError:
            raise HTTPException(504, CompletionError(code="query_timeout", message="关联核对超时，请保留原操作重试", retryable=True).model_dump()) from None
        except (DBAPIError, ValidationError):
            raise HTTPException(
                503, CompletionError(code="source_unavailable", message="关联依据暂不可读取，请保留原操作重试", retryable=True).model_dump()
            ) from None
        except LookupError:
            raise HTTPException(404, CompletionError(code="not_found", message="计划或反馈记录不可用", retryable=False).model_dump()) from None
        except QueryFailure as exc:
            raise HTTPException(
                {"version_conflict": 409, "link_conflict": 409, "query_timeout": 504, "source_unavailable": 503}.get(exc.code, 422),
                CompletionError(code=exc.code, message=str(exc), retryable=exc.retryable, current_version=getattr(exc, "current_version", None)).model_dump(),
            ) from None

    @router.post("/feedback/plan-links", response_model=PlanLink)
    async def create_plan_link(body: PlanLinkCommand, request: Request):
        links = PlanLinkService(repository(request))

        async def operation():
            previous = await links.retry(body)
            if previous is not None:
                return previous
            _, feedback = allowed(request)
            if feedback is None:
                raise QueryFailure("link_conflict", "反馈来源未启用，无法确认关联")
            return await links.create(body)

        return await linked_answer(operation())

    @router.get("/feedback/plan-links", response_model=PlanLinkList)
    async def list_plan_links(plan_id: str, request: Request):
        repo = repository(request)
        feedback = service.feedback
        enabled = bool(feedback and feedback.enabled)
        if enabled:
            try:
                feedback.repository(repo.owner_id)
            except PermissionError:
                enabled = False
        return await linked_answer(PlanLinkService(repo).list(plan_id, source_enabled=enabled))
