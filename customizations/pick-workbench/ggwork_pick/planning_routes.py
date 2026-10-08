"""Authenticated plan-draft HTTP boundary; no model mutation registration."""

import asyncio

from fastapi import HTTPException, Query, Request
from fastapi.responses import Response

from ggwork_pick.completion_contracts import (
    CompletionError,
    Plan,
    PlanCreate,
    PlanExport,
    PlanExportCommand,
    PlanList,
    PlanPreview,
    PlanUpdate,
    PlanVersionCommand,
)
from ggwork_pick.planning import PlanningService
from ggwork_pick.query_reader import QueryFailure


def register_planning_routes(router, repository, common_query):
    async def answer(work):
        try:
            return await work
        except LookupError:
            raise HTTPException(404, CompletionError(code="not_found", message="排期或来源不存在", retryable=False).model_dump()) from None
        except QueryFailure as exc:
            raise HTTPException(
                {"version_conflict": 409, "export_blocked": 409, "query_timeout": 504, "source_unavailable": 503, "version_gone": 410}.get(exc.code, 422),
                CompletionError(code=exc.code, message=str(exc), retryable=exc.retryable, current_version=getattr(exc, "current_version", None)).model_dump(),
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

    from ggwork_pick.planning_export import PlanningExportService

    async def confirmed(request, work):
        async def disconnected():
            while not await request.is_disconnected():
                await asyncio.sleep(0.05)

        running = asyncio.create_task(work)
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((running, watcher), return_when=asyncio.FIRST_COMPLETED)
            if running in done:
                return await running
            raise asyncio.CancelledError
        finally:
            for task in (running, watcher):
                if not task.done():
                    task.cancel()
            await asyncio.gather(running, watcher, return_exceptions=True)

    @router.post("/plans/{plan_id}/preview", response_model=PlanPreview)
    async def preview_plan(plan_id: str, body: PlanVersionCommand, request: Request):
        repo = repository(request)
        return await answer(confirmed(request, PlanningExportService(repo, common_query(repo)).preview(plan_id, body)))

    @router.post("/plans/{plan_id}/exports", response_model=PlanExport)
    async def export_plan(plan_id: str, body: PlanExportCommand, request: Request):
        repo = repository(request)
        return await answer(confirmed(request, PlanningExportService(repo, common_query(repo)).export(plan_id, body)))

    @router.get("/exports/{export_id}")
    async def download_export(export_id: str, request: Request):
        repo = repository(request)
        receipt, data = await answer(PlanningExportService(repo, common_query(repo)).download(export_id))
        return Response(
            data,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{receipt["filename"]}"',
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
