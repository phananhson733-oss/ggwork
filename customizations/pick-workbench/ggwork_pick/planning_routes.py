"""Authenticated plan-draft HTTP boundary; no model mutation registration."""

from fastapi import HTTPException, Query, Request

from ggwork_pick.completion_contracts import CompletionError, Plan, PlanCreate, PlanList, PlanUpdate
from ggwork_pick.planning import PlanningService
from ggwork_pick.query_reader import QueryFailure


def register_planning_routes(router, repository):
    async def answer(work):
        try:
            return await work
        except LookupError:
            raise HTTPException(404, CompletionError(code="not_found", message="排期或来源不存在", retryable=False).model_dump()) from None
        except QueryFailure as exc:
            raise HTTPException(
                409 if exc.code == "version_conflict" else 422,
                CompletionError(code=exc.code, message=str(exc), retryable=False, current_version=getattr(exc, "current_version", None)).model_dump(),
            ) from None

    @router.post("/plans", response_model=Plan)
    async def create_plan(body: PlanCreate, request: Request):
        return await answer(PlanningService(repository(request)).create(body))

    @router.get("/plans", response_model=PlanList)
    async def list_plans(request: Request, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100)):
        return await answer(PlanningService(repository(request)).list(offset, limit))

    @router.get("/plans/{plan_id}", response_model=Plan)
    async def get_plan(plan_id: str, request: Request):
        return await answer(PlanningService(repository(request)).get(plan_id))

    @router.patch("/plans/{plan_id}", response_model=Plan)
    async def update_plan(plan_id: str, body: PlanUpdate, request: Request):
        return await answer(PlanningService(repository(request)).update(plan_id, body))
