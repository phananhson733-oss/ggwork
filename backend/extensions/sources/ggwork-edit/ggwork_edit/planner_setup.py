"""Initialize configured host models only after host configuration is available."""

import logging

from ggwork_edit.planner import TextPlanner


class ConfiguredPlanner:
    def __init__(self, service, model_name):
        self.service = service
        self.model_name = model_name
        service.planner_model = model_name
        service.execution_profiles = self.execution_profiles
        self.planner = None

    async def start(self, deps):
        if not self.model_name:
            return
        from deerflow.models import create_chat_model

        # Exact named host provider; credentials stay inside the Gateway.
        try:
            model = create_chat_model(self.model_name, attach_tracing=False)
        except Exception as exc:
            # History and unrelated extension routes remain usable without a planner.
            logging.getLogger(__name__).warning("Editing planner unavailable: %s", type(exc).__name__)
            return
        self.planner = TextPlanner(model, model_name=self.model_name)
        self.service.hook_available = True

    async def stop(self):
        self.service.hook_available = False
        self.planner = None

    async def plan(self, *args):
        if self.planner is None:
            raise ValueError("Text planner is not configured")
        return await self.planner.plan(*args)

    async def execution_profiles(self, owner):
        if self.planner is None:
            return set()
        from app.gateway.deps import get_local_provider

        from ggwork_edit.capability import SKILLS, admitted_skills, allowed

        user = await get_local_provider().get_user(owner)
        if user is None or str(user.id) != owner:
            return set()
        context = {"user_id": owner, "user_role": user.system_role, "is_internal": False, "oauth_provider": user.oauth_provider, "oauth_id": user.oauth_id}
        if not allowed(context, "model", self.model_name):
            return set()
        names = {skill.name for skill in await admitted_skills(context)}
        return {profile for profile, name in SKILLS.items() if name in names}
