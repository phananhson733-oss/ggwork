"""Minimal authenticated feedback status; never return raw manifests or finance records."""

from fastapi import HTTPException, Request

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
