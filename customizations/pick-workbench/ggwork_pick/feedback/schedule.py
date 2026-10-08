"""Opt-in hourly refresh, sharing the same durable lease with manual and Agent requests."""

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_feedback_schedule(service, *, sleep=asyncio.sleep):
    while service.enabled and not service.stopping:
        try:
            await service.refresh(service.owner_id, wait_seconds=0.05, trigger="scheduled")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Feedback scheduled refresh failed: %s", type(exc).__name__)
        await sleep(3600)
