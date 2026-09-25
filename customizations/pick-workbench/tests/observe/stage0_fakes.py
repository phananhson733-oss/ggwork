"""Synthetic stage 0 inputs for the TR-05 tests: a control list shaped like the real one, and result lines as the day
runner writes them (plan TR-05). Every title here is made up; nothing is read from the artifacts directory."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

HOUR = 3600
REQUESTED_AT = datetime(2026, 9, 26, 3, 20, tzinfo=UTC)
LAST_HOUR = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)  # the newest hourly point: the partial one
LAST_DAY = datetime(2026, 9, 26, 0, 0, tzinfo=UTC)  # the newest daily point: today, partial


def identity(n: int, language: str = "en") -> str:
    return json.dumps(["realshort-pick", f"c3ludGhldGljLXJvdy0{n:02d}", language], separators=(",", ":"))


def _control(cid: str, group: str, kind: str, term: str, geo: str, *, related: bool = False, n: int | None = None) -> dict:
    entry = {"id": cid, "group": group, "kind": kind, "term": term, "geo": geo, "related": related}
    return {**entry, "identity": identity(n)} if n is not None else entry


def controls_document(*, positives: int = 16, regional_per_geo: int = 3, related_positives: int = 8) -> dict:
    """A control list with the real list's shape: 16 positives, 2 generic and 2 delisted negatives, 3 regional dramas
    for each of BG, DE, FR, IT, 5 market series and 4 seeds."""
    geos = ("US", "BG", "FR", "MX", "PL", "RO", "DE", "TW")
    pos = [
        _control(f"pos-{i + 1:02d}", "positive", "exact_title", f"moonlit vow {i + 1}", geos[i % len(geos)], related=i < related_positives, n=i + 1)
        for i in range(positives)
    ]
    neg = [
        _control("neg-01", "negative", "generic", "scandal", "US", related=True, n=40),
        _control("neg-02", "negative", "generic", "cinderella", "BR", related=True, n=41),
        _control("neg-03", "negative", "delisted", "the coffin heiress returns", "US", n=42),
        _control("neg-04", "negative", "delisted", "my trapped first life", "US", n=43),
    ]
    reg = [
        _control(f"reg-{geo.lower()}-{k + 1}", "regional", "recent", f"regional {geo.lower()} drama {k + 1}", geo, n=50 + 10 * j + k)
        for j, geo in enumerate(("BG", "DE", "FR", "IT"))
        for k in range(regional_per_geo)
    ]
    mkt = [_control(f"mkt-{geo.lower()}", "market", "market", f"short drama {geo.lower()}", geo) for geo in ("US", "BG", "DE", "FR", "IT")]
    seeds = [
        _control(f"seed-{k + 1:02d}", "seed", "seed", term, "US", related=True) for k, term in enumerate(("reelshort", "dramabox", "reelshort", "shortmax"))
    ]
    return {
        "format": "trends-stage0-controls-v1",
        "built_at": "2026-09-25T10:00:00+00:00",
        "provenance": {"gsc_age": "synthetic"},
        "controls": [*pos, *neg, *reg, *mkt, *seeds],
        "manual": {"status": "pending", "files": []},
    }


# ---- result lines --------------------------------------------------------------------------------------------------


def hourly_values(nonzero: int, *, points: int = 169, partial_value: int = 50) -> list[int]:
    """`points` hourly values whose last complete 144 hold exactly `nonzero` non-zero hours (spread out), then a partial
    point. Hours before the window are all 100, so a count over the wrong window comes out different."""
    window = [0] * 144
    step = 144 / nonzero if nonzero else 0
    for k in range(nonzero):
        window[int(k * step)] = 7 + (k % 5)
    head = [100] * (points - 1 - 144)
    return [*head, *window, partial_value]


def daily_values(nonzero: int, *, points: int = 31, partial_value: int = 60) -> list[int]:
    """30 complete days holding `nonzero` non-zero days, then today's partial point."""
    window = [0] * 30
    step = 30 / nonzero if nonzero else 0
    for k in range(nonzero):
        window[int(k * step)] = 20 + k
    head = [100] * (points - 1 - 30)
    return [*head, *window, partial_value]


def timeline(term: str, values: Sequence[int], *, granularity: str, partial_last: bool = True, status: str | None = None) -> dict:
    step, last = (HOUR, LAST_HOUR) if granularity == "H" else (24 * HOUR, LAST_DAY)
    start = int(last.timestamp()) - step * (len(values) - 1)
    times = [str(start + step * k) for k in range(len(values))]
    partial = [None] * len(values)
    if partial_last:
        partial[-1] = True
    line_status = status or ("ok" if any(values) else "ok_zero")
    line = {"term": term, "status": line_status, "time": times, "value": list(values), "isPartial": partial, "hasData": [True] * len(values)}
    return {"status": line_status, "lines": [line]}


def result_line(
    control: dict,
    granularity: str,
    values: Sequence[int] | None,
    *,
    day: int = 1,
    status: str | None = None,
    related: dict | None = None,
    user_type: str | None = "USER_TYPE_LEGIT_USER",
    method: str = "GET",
    repeat_of: str | None = None,
    requested_at: datetime = REQUESTED_AT,
    unit: str | None = None,
) -> dict:
    """One unit's result as the runner writes it. values None: a failed unit (status says how)."""
    key = unit or f"{control['id']}-{granularity.lower()}"
    series = timeline(control["term"], values, granularity=granularity) if values is not None else None
    final = status or (series["status"] if series is not None else "rate_limited")
    return {
        "unit": key,
        "control": control["id"],
        "group": control["group"],
        "kind": control["kind"],
        "granularity": granularity,
        "term": control["term"],
        "geo": control["geo"],
        "method": method,
        "repeat_of": repeat_of,
        "day": day,
        "target_date": (requested_at + timedelta(hours=22)).date().isoformat(),
        "requested_at": requested_at.isoformat(),
        "attempts": 1,
        "status": final,
        "stopped_by": None if values is not None else final,
        "reason": None,
        "user_type": user_type,
        "timeline": series if series is not None else {"status": final, "lines": None},
        "related": related,
        "requests": [],
    }


def related_ok(*, breakout: bool = False) -> dict:
    top = [{"query": "moonlit vow episode 1", "value": 100, "formattedValue": "100"}]
    rising = [{"query": "moonlit vow full", "value": 5000 if breakout else 250, "formattedValue": "Breakout" if breakout else "+250%"}]
    return {"status": "ok", "widget_missing": False, "top": top, "rising": rising}


def related_missing() -> dict:
    return {"status": "no_data", "widget_missing": True, "top": None, "rising": None}


def seed_line(control: dict, *, day: int, related: dict, user_type: str | None = "USER_TYPE_LEGIT_USER") -> dict:
    line = result_line(control, "H", None, day=day, status=related["status"], related=related, user_type=user_type, unit=f"{control['id']}-r")
    return {**line, "timeline": None, "stopped_by": None}
