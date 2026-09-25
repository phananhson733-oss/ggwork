"""The quality note beside a 7-day rising evaluation (plan TR-09, D38; design 5.8). A note, never a gate.

For each 7-day evaluation, the 7 daily values of W0 and of W-1 (a day without a row counts as 0 inside a window that has
rows at all):

- rate ratio RR = sum(W0) / sum(W-1); a baseline without rows or summing to 0 is not tested ("基线未观测，不检验"), and
  neither is a W0 without rows or summing to 0, whose logarithm does not exist;
- dispersion phi = max(1, sum over both windows of (x - mu)^2 / mu, divided by 12), mu the mean of the window x is in;
- z = ln RR / sqrt(phi x (1/sum(W0) + 1/sum(W-1))), one-sided p = erfc(z / sqrt 2) / 2;
- Benjamini-Hochberg over the family of every tested 7-day evaluation of one GSC set, q = 0.10.

Only the standard library's math. Which daily values: the detail's (D/E), the only per-day series a round keeps (Vd
rows hold window sums); the caller passes them.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import accumulate

from ggwork_pick.observe.contract_rows import QualityNote

DAYS = 7
DISPERSION_DOF = 12  # 14 daily values less the two window means
BH_Q = 0.10
NOTE_TESTED = "只作质量注记，不作门槛"
NOTE_NO_BASELINE = "基线未观测，不检验"
NOTE_NO_CURRENT = "本窗口未观测，不检验"


@dataclass(frozen=True, slots=True)
class RateTest:
    tested: bool
    rate_ratio: float | None
    dispersion: float | None
    z: float | None
    p_value: float | None
    note: str


def _counts(days: Sequence[int | None], name: str) -> tuple[int, ...] | None:
    """The window's 7 daily values with missing days as 0, or None when the window has no row at all."""
    if len(days) != DAYS:
        raise ValueError(f"{name} needs {DAYS} daily values, got {len(days)}")
    if any(day is not None and (not isinstance(day, int) or day < 0) for day in days):
        raise ValueError(f"{name} daily values are non-negative integers or None")
    return None if all(day is None for day in days) else tuple(day or 0 for day in days)


def _chi(values: tuple[int, ...]) -> float:
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / mean


def rate_test(w0_days: Sequence[int | None], w1_days: Sequence[int | None], *, dof: int = DISPERSION_DOF) -> RateTest:
    """D38's overdispersed rate test for one 7-day evaluation."""
    current, baseline = _counts(w0_days, "W0"), _counts(w1_days, "W-1")
    if baseline is None or sum(baseline) == 0:
        return RateTest(False, None, None, None, None, NOTE_NO_BASELINE)
    if current is None or sum(current) == 0:
        return RateTest(False, None, None, None, None, NOTE_NO_CURRENT)
    total0, total1 = sum(current), sum(baseline)
    ratio = total0 / total1
    dispersion = max(1.0, (_chi(current) + _chi(baseline)) / dof)
    z = math.log(ratio) / math.sqrt(dispersion * (1 / total0 + 1 / total1))
    return RateTest(True, ratio, dispersion, z, 0.5 * math.erfc(z / math.sqrt(2)), NOTE_TESTED)


def bh_adjust(p_values: Sequence[float]) -> tuple[float, ...]:
    """Benjamini-Hochberg adjusted p-values, in the order given: min over j >= rank of p_(j) x m / j, capped at 1."""
    m = len(p_values)
    order = sorted(range(m), key=lambda index: p_values[index])
    scaled = tuple(p_values[index] * m / rank for rank, index in enumerate(order, 1))
    # The running minimum from the largest rank down, starting at the cap 1.
    step_up = tuple(accumulate(reversed(scaled), min, initial=1.0))[1:][::-1]
    by_index = dict(zip(order, step_up))
    return tuple(by_index[index] for index in range(m))


def quality_notes[Key](tests: Mapping[Key, RateTest], *, q: float = BH_Q) -> dict[Key, QualityNote]:
    """The notes of one GSC set: pass every 7-day evaluation of that set, and only that set, in one call.

    The BH family is the tested ones among them; an untested evaluation gets a note with every number empty."""
    tested = tuple(key for key, test in tests.items() if test.tested)
    adjusted = dict(zip(tested, bh_adjust(tuple(tests[key].p_value for key in tested))))
    return {key: _note(test, adjusted.get(key), q) for key, test in tests.items()}


def _note(test: RateTest, adjusted: float | None, q: float) -> QualityNote:
    if not test.tested:
        return QualityNote(tested=False, rate_ratio=None, dispersion=None, z=None, p_value=None, bh_adjusted=None, bh_q=q, bh_passed=None, note=test.note)
    return QualityNote(
        tested=True,
        rate_ratio=test.rate_ratio,
        dispersion=test.dispersion,
        z=test.z,
        p_value=test.p_value,
        bh_adjusted=adjusted,
        bh_q=q,
        bh_passed=adjusted <= q,
        note=test.note,
    )
