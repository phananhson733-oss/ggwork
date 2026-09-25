"""gsc-rules-v1's parameters (plan TR-09, D36, D38; premise 3; design 5.6, 5.8): the one place a GSC rule reads a
threshold from.

cutoff, coverage, quality and rules keep no copy of any of these: every function that applies one takes a
GscRulesParams from its caller, and a Comparison records the tolerance it was compared with so that judge_24h and
judge_7d refuse comparisons made with other parameters than their own. A GSC set freezes params_of(version).as_params()
into FrozenInputs.rules, and that is what judged it. A change of any value (the consistency rule of premise 3, tau,
D38's test) is a new version in RULES next to the old one, never an edit of GSC_RULES_V1 (versions.py).
"""

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from ggwork_pick.observe.versions import GSC_RULES_VERSION


@dataclass(frozen=True, slots=True)
class GscRulesParams:
    """The thresholds of one gsc-rules version. No defaults: a version spells out every value."""

    surge_min_w0: int
    surge_ratio_percent: int
    surge_min_base: int
    from_zero_min_w0: int
    high_ctr_min_clicks: int
    high_ctr_min_percent: int
    rank_push_best: int
    rank_push_worst: int
    rank_push_min_impressions: int
    rising_min_w0: int
    rising_ratio_percent: int
    rising_min_increment: int
    rank_improvement: int
    consistency_percent: int  # premise 3: |X_flt - X_det| <= max(percent% x max(X_flt, X_det), floor)
    consistency_floor: int
    site_gap_percent: int  # design 5.6: tau
    bh_q: float  # D38
    dispersion_dof: int  # D38: 14 daily values less the two window means

    def as_params(self) -> dict[str, Any]:
        return asdict(self)


# v9.5's values to start with (design 5.8), recalibrated after the shadow run as a new version.
GSC_RULES_V1 = GscRulesParams(
    surge_min_w0=2000,
    surge_ratio_percent=150,
    surge_min_base=20,
    from_zero_min_w0=20,
    high_ctr_min_clicks=50,
    high_ctr_min_percent=5,
    rank_push_best=4,
    rank_push_worst=20,
    rank_push_min_impressions=50,
    rising_min_w0=100,
    rising_ratio_percent=150,
    rising_min_increment=30,
    rank_improvement=2,
    consistency_percent=10,
    consistency_floor=3,
    site_gap_percent=5,
    bh_q=0.10,
    dispersion_dof=12,
)
RULES = MappingProxyType({GSC_RULES_VERSION: GSC_RULES_V1})


def params_of(version: str) -> GscRulesParams:
    """The parameters a set was judged with, by its recorded version (an unknown version raises KeyError)."""
    return RULES[version]
