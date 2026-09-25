"""Builders shared by the TR-09 GSC rule tests (plan TR-09; design 5.3-5.8).

One fixed round: A's watermark at 12:00 PDT on 24 September 2026, so H_c is 19:00 UTC that day, the 24-hour windows are
the two days before it and the 7-day windows end on the 23rd. Comparisons are always built through the real
per_identity_consistency, never as hand-made Comparison objects, so a test cannot assume an admission the rule would
not grant.
"""

from datetime import UTC, date, datetime

from ggwork_pick.observe.gsc import coverage, cutoff
from ggwork_pick.observe.gsc.params import GSC_RULES_V1 as V1

H_C = datetime(2026, 9, 24, 19, 0, tzinfo=UTC)  # 12:00 PDT on the 24th
ROUND = "a" * 32
PREVIOUS_ROUND = "b" * 32
W0, W1 = cutoff.hourly_windows(H_C)
LATEST_DAY = date(2026, 9, 23)
D0, D1 = cutoff.daily_windows(LATEST_DAY)
MISSING = object()


def value(amount: int | None) -> coverage.WindowValue:
    """None: the response had no row. An int: one row carrying it (0 included: a row valued 0 is an observation)."""
    return coverage.WindowValue.of(() if amount is None else (amount,))


def formal_cutoff() -> cutoff.Cutoff:
    """C slices for the 22nd to the 24th, the last one up to A's watermark: a formal 24-hour window."""
    slices = (
        cutoff.CSlice(date(2026, 9, 22), None),
        cutoff.CSlice(date(2026, 9, 23), None),
        cutoff.CSlice(date(2026, 9, 24), H_C),
    )
    return cutoff.common_cutoff(a_watermark=H_C, a_prime_watermark=None, c_slices=slices)


def states_of(window: cutoff.Window, state: str | None = None) -> tuple[str, ...]:
    if window.kind == "24h":
        return ("hourly_all",)
    return (state or "final",) * len(window.days)


def compare(window, det, flt=MISSING, *, status="fetched", current=True, flt_window=None, det_states=None, flt_states=None, params=V1):
    """One identity x country x window x metric: the detail sum det and, unless left out, the filter request's flt."""
    detail = coverage.DetailSide(window, det_states or states_of(window), value(det))
    if flt is MISSING:
        return coverage.per_identity_consistency(detail, None, params=params)
    kind = "vh" if window.kind == "24h" else "vd"
    shown = flt_window or window
    filtered = coverage.FilterSide(kind, shown, flt_states or states_of(shown), status, current, value(flt))
    return coverage.per_identity_consistency(detail, filtered, params=params)
