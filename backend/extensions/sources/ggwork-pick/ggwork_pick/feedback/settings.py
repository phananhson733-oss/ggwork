"""Operator configuration only; financial feedback is not a shared catalogue source."""

import os
from dataclasses import dataclass

from ggwork_pick.feedback.repository import FeedbackRepository


@dataclass(frozen=True)
class FeedbackSettings:
    enabled: bool = False
    owner_id: str = ""
    scheduled: bool = False

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        enabled = env.get("PICK_FEEDBACK_ENABLED") == "1"
        owner = env.get("PICK_FEEDBACK_OWNER_ID", "").strip()
        if enabled:
            FeedbackRepository(None, owner)
        return cls(enabled, owner, enabled and env.get("PICK_FEEDBACK_SCHEDULE_ENABLED") == "1")
