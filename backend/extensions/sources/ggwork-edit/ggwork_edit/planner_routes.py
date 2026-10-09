"""Device-authenticated transcript submission, using the existing bearer boundary."""

from fastapi import APIRouter, HTTPException, Request

from ggwork_edit.planner import PlannerUnavailable, PlanRequest
from ggwork_edit.repository import ConflictError
from ggwork_edit.routes import EditingRoute, owner


def build_planner_router(service, planner):
    router = APIRouter(prefix="/api/editing/worker", route_class=EditingRoute)

    @router.post("/devices/{device_id}/tasks/{task_id}/plan")
    async def plan(device_id: str, task_id: str, payload: PlanRequest, request: Request):
        repo = service.repository(owner(request, device_id=device_id))
        if planner is None or not service.hook_available:
            raise HTTPException(503, "Text planner is not configured")
        try:
            if planner.model_name:
                user = getattr(request.state, "user", None)
                if user is None or str(user.id) != repo.owner:
                    raise PermissionError("Authenticated owner required")
                from ggwork_edit.capability import require_profile

                task = await repo.get_worker_task(device_id, task_id)
                await require_profile(
                    {"user_id": str(user.id), "user_role": user.system_role, "is_internal": False}, task["requirements"]["profile"], planner.model_name
                )
            return await planner.plan(repo, device_id, task_id, payload)
        except PlannerUnavailable:
            raise HTTPException(502, {"code": "planner_unavailable", "retry": "explicit"}) from None
        except ConflictError:
            raise
        except ValueError:
            # Never echo provider/transcript payloads in validation errors.
            raise HTTPException(422, "Transcript or model plan failed strict validation") from None

    return router
