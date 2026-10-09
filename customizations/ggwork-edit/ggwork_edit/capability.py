"""Editing capabilities use the host's owner Skill storage and role policy."""

import asyncio
import re

from deerflow.authz.principal import build_principal_from_context
from deerflow.authz.provider import AuthzDecision, AuthzRequest
from deerflow.authz.runtime import resolve_authorization_provider
from deerflow.config import get_app_config
from deerflow.skills.slash import resolve_slash_skill
from deerflow.skills.storage import get_or_new_user_skill_storage

SKILLS = {"highlight": "clip-highlight", "hook": "clip-hook"}
ALIASES = {f"$ggwork-edit/{name}": name for name in SKILLS.values()}


def allowed(context, resource, target, *, app_config=None):
    config = app_config or get_app_config()
    if not config.authorization.enabled:
        return True
    provider = resolve_authorization_provider(config.authorization)
    if provider is None:
        return False
    principal = build_principal_from_context(context, default_role=config.authorization.default_role)
    decision = provider.authorize(AuthzRequest(principal=principal, resource=resource, action="use" if resource == "model" else "activate", target=target))
    return isinstance(decision, AuthzDecision) and decision.allow


async def admitted_skills(context, *, app_config=None):
    owner = context.get("user_id")
    if not owner or owner in ("default", "system:shared"):
        return []
    config = app_config or get_app_config()
    storage = get_or_new_user_skill_storage(owner, app_config=config)
    skills = await asyncio.to_thread(storage.load_skills, enabled_only=True)
    # Public discovery alone is insufficient: apply the owner's saved switch too.
    return [s for s in skills if s.name in SKILLS.values() and storage.get_skill_enabled_state(s.name) and allowed(context, "skill", s.name, app_config=config)]


async def resolve_command(text, context, *, app_config=None):
    # Only registered literal aliases normalize into the shared slash parser.
    head = re.match(r"\S+", text)
    if head and head.group() in ALIASES:
        text = "/" + ALIASES[head.group()] + text[head.end() :]
    skills = await admitted_skills(context, app_config=app_config)
    return resolve_slash_skill(text, skills, available_skills={s.name for s in skills})


async def require_profile(context, profile, model_name=None, *, app_config=None):
    names = {s.name for s in await admitted_skills(context, app_config=app_config)}
    if SKILLS[profile] not in names:
        raise PermissionError("Editing Skill unavailable for this owner")
    if model_name and not allowed(context, "model", model_name, app_config=app_config):
        raise PermissionError("Planning model unavailable for this owner")
