"""Shared set-up for the mirror client tests: a FeedClient wired to the RealShort double and its fake clock."""

from fake_realshort import EXPORT_TOKEN, FEED_TOKEN, Clock, FakeRealShort

from ggwork_pick.mirror.client import FeedClient

BASE = "https://realshort.test"


def make_client(fake: FakeRealShort, clock: Clock, **overrides) -> FeedClient:
    options = {
        "base_url": BASE,
        "export_token": EXPORT_TOKEN,
        "feed_token": FEED_TOKEN,
        "bypass": None,
        "transport": fake.transport(),
        "clock": clock,
        "sleep": clock.sleep,
        "timer": clock.timer,
    }
    return FeedClient(**{**options, **overrides})


def world(**fake_options) -> tuple[FakeRealShort, Clock]:
    clock = Clock()
    return FakeRealShort(now=clock, **fake_options), clock


async def collect(generator) -> list:
    return [page async for page in generator]


def only(calls, resource):
    return [call for call in calls if call.resource == resource]
