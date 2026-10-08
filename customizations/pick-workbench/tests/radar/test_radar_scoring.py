import json

import pytest
from test_radar_snapshot import record


def scorer():
    from ggwork_pick.radar.scorer import score_row

    return score_row


def test_shared_formula_high_and_zero():
    row = record([100] * 30)
    row.update(avg_heat=100, peak_heat=100, momentum=100, platform_count=3)
    row["promo_tags"] = ["Hot"]
    result = scorer()(row, json.loads(row["timeline_data"]))
    assert result["score"] == 100
    assert result["tier"] == "S"
    row.update(avg_heat=0, peak_heat=0, momentum=0)
    result = scorer()(row, [{"value": 0}] * 30)
    assert result["score"] == 50  # Preserved formula: platform and promotion still contribute.
    assert result["tier"] == "B"
    assert result["breakout_score"] == 0


@pytest.mark.parametrize(
    "threshold,tier,below,platform_count,tags,base",
    [
        (75, "S", "A", 3, ["Hot"], 50),
        (60, "A", "B", 3, ["Hot"], 50),
        (45, "B", "C", 2, [], 34.75),
    ],
)
def test_exact_tier_thresholds_and_rounding(threshold, tier, below, platform_count, tags, base):
    for delta in [-0.1, 0, 0.1]:
        heat = (threshold + delta - base) / 0.35
        row = record([heat] * 30)
        row.update(
            avg_heat=heat,
            peak_heat=heat,
            momentum=0,
            platform_count=platform_count,
            promo_tags=tags,
            has_promo_tag=bool(tags),
        )
        result = scorer()(row, json.loads(row["timeline_data"]))
        assert result["score"] == pytest.approx(threshold + delta)
        assert result["tier"] == (below if delta < 0 else tier)
