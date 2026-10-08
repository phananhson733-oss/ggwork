"""Feedback scheduler is opt-in and uses the same coalesced owner-scoped refresh entry."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_schedule_uses_fixed_owner_and_continues_after_failure():
    from ggwork_pick.feedback.schedule import run_feedback_schedule

    service = SimpleNamespace(
        owner_id="alice", enabled=True, stopping=False, refresh=AsyncMock(side_effect=[ValueError("synthetic failure"), SimpleNamespace(status="ok")])
    )
    slept = []

    async def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_feedback_schedule(service, sleep=sleep)
    assert service.refresh.await_count == 2
    assert all(call.args == ("alice",) and call.kwargs["trigger"] == "scheduled" for call in service.refresh.await_args_list)
    assert slept == [3600, 3600]


@pytest.mark.asyncio
async def test_disabled_schedule_does_not_start_a_refresh():
    from ggwork_pick.feedback.schedule import run_feedback_schedule

    service = SimpleNamespace(owner_id="alice", enabled=False, stopping=False, refresh=AsyncMock())
    await run_feedback_schedule(service)
    service.refresh.assert_not_called()
