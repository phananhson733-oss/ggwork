"""Owner-specific Skill switches, backed by the host's existing Skill storage."""

import asyncio

from deerflow.skills.storage import get_or_new_user_skill_storage
from fastapi import APIRouter, HTTPException, Request

from ggwork_edit.capability import SKILLS
from ggwork_edit.contracts import Settings
from ggwork_edit.routes import EditingRoute, owner


def build_capability_router():
    router = APIRouter(prefix="/api/editing", route_class=EditingRoute)

    @router.post("/skills/{name}")
    async def toggle(name: str, payload: Settings, request: Request):
        user_id = owner(request)
        if name not in SKILLS.values():
            raise HTTPException(404, "Unknown editing Skill")
        storage = get_or_new_user_skill_storage(user_id)
        skills = await asyncio.to_thread(storage.load_skills, enabled_only=False)
        if not any(skill.name == name for skill in skills):
            raise HTTPException(404, "Editing Skill not installed")
        await asyncio.to_thread(storage.set_skill_enabled_state, name, payload.skill_enabled)
        return {"name": name, "skill_enabled": payload.skill_enabled}

    return router
