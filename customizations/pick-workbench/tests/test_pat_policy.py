"""Which routes of this extension a personal access token may reach.

The host's PAT policy (backend/app/gateway/auth/pat.py) names pick routes by method and path, because none of them
carries a permission decorator. Both tests read the mounted router rather than a hand-kept list: a route added here is
closed to tokens until the policy names it, and a renamed route leaves a dead rule that fails the first test.
"""

import re

from fastapi.routing import APIRoute

from ggwork_pick.routes import build_router
from ggwork_pick.service import PickService

# The read surface a programmatic client gets: the common query (a POST that only reads) and the data status.
READABLE = {("POST", "/api/pick/query"), ("GET", "/api/pick/sync")}


def _mounted(tmp_path) -> list[tuple[str, str]]:
    router = build_router(PickService(tmp_path / "files"))
    return sorted((method, route.path) for route in router.routes if isinstance(route, APIRoute) for method in route.methods - {"HEAD", "OPTIONS"})


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x1", path)


def test_a_pick_read_token_reaches_exactly_the_two_read_routes(tmp_path):
    from app.gateway.auth.pat import PAT_PICK_READ_SCOPE, is_pat_allowed_route

    mounted = _mounted(tmp_path)
    assert READABLE <= set(mounted)
    scopes = frozenset({PAT_PICK_READ_SCOPE})
    assert {(method, path) for method, path in mounted if is_pat_allowed_route(method, _concrete(path), scopes)} == READABLE


def test_no_pick_route_is_reachable_without_the_pick_scope(tmp_path):
    from app.gateway.auth.pat import PAT_ALLOWED_SCOPES, is_pat_allowed_route

    mounted = _mounted(tmp_path)
    assert len(mounted) > len(READABLE)
    for method, path in mounted:
        assert not is_pat_allowed_route(method, _concrete(path)), f"{method} {path}"
        assert not is_pat_allowed_route(method, _concrete(path), PAT_ALLOWED_SCOPES), f"{method} {path}"
