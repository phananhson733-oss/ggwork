"""The pause and the request budget of a hand-run GSC command: the probe and the URL export (plan TR-07; design 5.1).

The per-site quota is shared with RealShort (design 5.1), so a command run by hand goes one request at a time, pauses
between requests, and stops at a budget instead of splitting without end. The pause runs on the injected clock, so a
test on a ManualClock never waits.
"""

from ggwork_pick.observe.clock import Clock
from ggwork_pick.observe.errors import ObserveFailure


class BudgetExhausted(ObserveFailure):
    """The command reached its request budget; what it fetched so far is not written (exit 1)."""


class Pacer:
    """Call before_request() before every request: it pauses after the first and refuses past max_requests."""

    def __init__(self, clock: Clock, *, pause_seconds: float, max_requests: int | None = None):
        if pause_seconds < 0 or (max_requests is not None and max_requests < 1):
            raise ValueError("pause_seconds is at least 0 and max_requests at least 1")
        self._clock, self._pause, self._max = clock, pause_seconds, max_requests
        self._requests = 0

    @property
    def requests(self) -> int:
        return self._requests

    async def before_request(self) -> None:
        if self._max is not None and self._requests >= self._max:
            raise BudgetExhausted(f"已发 {self._requests} 次请求，到了本次的上限 {self._max}：没有写出任何文件；调大 --max-requests 或缩短窗口再跑")
        if self._requests and self._pause:
            await self._clock.sleep(self._pause)
        self._requests += 1
