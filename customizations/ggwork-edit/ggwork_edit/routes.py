"""Thin HTTP adapters: identity comes exclusively from the host resolver."""

from deerflow_extension_api.auth import resolve_principal
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.routing import APIRoute

from ggwork_edit.contracts import Claim, CreateTask, DeviceHeartbeat, PrepareTask, RegisterDevice, RetryTask, Settings, VerifyManifest, WorkerReport
from ggwork_edit.repository import ConflictError


class EditingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except ConflictError as exc:
                raise HTTPException(409, str(exc)) from None
            except LookupError:
                raise HTTPException(404, "Not found") from None
            except PermissionError:
                raise HTTPException(403, "Not authorized") from None

        return handle


def owner(request, *, device_id=None):
    principal = resolve_principal(request)
    if principal is None or principal.is_internal or principal.user_id in ("default", "system:shared"):
        raise HTTPException(401, "Authentication required")
    subject = getattr(principal, "subject_id", None)
    if (device_id is None and subject is not None) or (device_id is not None and subject != device_id):
        raise HTTPException(403, "Wrong credential scope")
    return principal.user_id


def build_router(service):
    router = APIRouter(prefix="/api/editing", route_class=EditingRoute)

    @router.post("/tasks")
    async def create(payload: CreateTask, request: Request):
        return await service.repository(owner(request)).create_task(payload)

    @router.get("/tasks")
    async def listing(request: Request, limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        return await service.repository(owner(request)).list_tasks_page(limit=limit, offset=offset)

    @router.get("/tasks/{task_id}")
    async def detail(task_id: str, request: Request):
        return await service.repository(owner(request)).get_task(task_id)

    @router.post("/tasks/{task_id}/confirm-plan")
    async def confirm(task_id: str, request: Request):
        return await service.repository(owner(request)).confirm_plan(task_id)

    @router.post("/tasks/{task_id}/stop")
    async def stop(task_id: str, request: Request):
        return await service.repository(owner(request)).stop_task(task_id)

    @router.post("/tasks/{task_id}/retry")
    async def retry(task_id: str, payload: RetryTask, request: Request):
        return await service.repository(owner(request)).retry(task_id, payload)

    @router.post("/devices")
    async def register(payload: RegisterDevice, request: Request):
        return await service.repository(owner(request)).register_device(payload.name)

    @router.get("/devices")
    async def devices(request: Request):
        return {"items": await service.repository(owner(request)).devices()}

    @router.post("/devices/{device_id}/revoke")
    async def revoke(device_id: str, request: Request):
        return await service.repository(owner(request)).revoke_device(device_id)

    @router.post("/tasks/{task_id}/prepare")
    async def prepare(task_id: str, payload: PrepareTask, request: Request):
        return await service.repository(owner(request)).prepare(task_id, payload)

    @router.get("/capabilities")
    async def capabilities(request: Request):
        return await service.repository(owner(request)).capabilities()

    @router.post("/settings")
    async def settings(payload: Settings, request: Request):
        return await service.repository(owner(request)).set_enabled(payload.skill_enabled)

    return router


def build_worker_router(service):
    router = APIRouter(prefix="/api/editing/worker", route_class=EditingRoute)

    @router.get("/devices/{device_id}/preparations")
    async def preparations(device_id: str, request: Request):
        return {"items": await service.repository(owner(request, device_id=device_id)).preparations(device_id)}

    @router.get("/devices/{device_id}/tasks/{task_id}")
    async def task(device_id: str, task_id: str, request: Request):
        return await service.repository(owner(request, device_id=device_id)).get_worker_task(device_id, task_id)

    @router.post("/devices/{device_id}/tasks/{task_id}/discovery")
    async def discovery(device_id: str, task_id: str, payload: VerifyManifest, request: Request):
        return await service.repository(owner(request, device_id=device_id)).discover(device_id, task_id, payload.source_manifest)

    @router.post("/devices/{device_id}/tasks/{task_id}/report")
    async def report(device_id: str, task_id: str, payload: WorkerReport, request: Request):
        return await service.repository(owner(request, device_id=device_id)).report(device_id, task_id, payload)

    @router.post("/devices/{device_id}/heartbeat")
    async def heartbeat(device_id: str, payload: DeviceHeartbeat, request: Request):
        return await service.repository(owner(request, device_id=device_id)).device_heartbeat(device_id, payload)

    @router.post("/devices/{device_id}/tasks/{task_id}/manifest")
    async def manifest(device_id: str, task_id: str, payload: VerifyManifest, request: Request):
        return await service.repository(owner(request, device_id=device_id)).verify_manifest(device_id, task_id, payload.source_manifest)

    @router.post("/devices/{device_id}/claim")
    async def claim(device_id: str, payload: Claim, request: Request):
        return await service.repository(owner(request, device_id=device_id)).claim(device_id, payload.request_id)

    return router
