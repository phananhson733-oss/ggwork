"""Only the selected ReelShort comparator and its declared source operands."""

from decimal import Decimal

KEYS = {"rr", "d1", "d7", "dp1", "dp7", "promoters", "publish", "bill", "eff", "gsc", "clicks"}


def _number(value):
    return format(Decimal(str(value)), "f") if type(value) in (int, float) else None


def selected_metric(row, key, reference):
    if key not in KEYS:
        return None
    metric = {
        "key": key,
        "value": None,
        "current": None,
        "baseline": None,
        "denominator": None,
        "comparison_days": None,
        "unit": "source_cents",
        "scope": "upstream_platform_rolling_30d",
        "observed_at": row.get("synced_at"),
        "baseline_at": None,
        "verified": row.get("metrics_valid"),
        "reference": reference,
    }
    if key in {"d1", "d7", "dp1", "dp7"}:
        days = 1 if key.endswith("1") else 7
        people = key.startswith("dp")
        current = row.get("promoters_cnt" if people else "rr")
        baseline = row.get(f"s{days}_p" if people else f"s{days}_rr")
        comparable = row.get("metrics_valid") is True and row.get(f"p{days}" if people else f"rr{days}") is not None
        metric.update(
            current=_number(current),
            baseline=_number(baseline) if comparable else None,
            comparison_days=days,
            baseline_at=row.get(f"baseline{days}_at"),
            unit="people" if people else "source_cents",
            scope="change_in_promoters" if people else "change_in_platform_rolling_30d",
            verified=comparable,
        )
        if comparable and current is not None and baseline is not None:
            metric["value"] = format(Decimal(str(current)) - Decimal(str(baseline)), "f")
    elif key == "eff":
        metric.update(
            current=_number(row.get("rr")), denominator=row.get("promoters_cnt"), unit="source_cents_per_promoter", scope="platform_metric_per_promoter"
        )
        # Retain exact operands; never certify a rounded ratio as an exact source scalar.
    elif key == "publish":
        metric.update(value=row.get("publish_at"), unit="timestamp", scope="publication_date", verified=None)
    else:
        column, unit, scope = {
            "rr": ("rr", "source_cents", "upstream_platform_rolling_30d"),
            "promoters": ("promoters_cnt", "people", "upstream_promoters"),
            "bill": ("bill_rank", "rank", "source_bill_rank"),
            "gsc": ("search_impressions", "impressions", "site_search_impressions"),
            "clicks": ("clicks7", "clicks", "site_outbound_7d"),
        }[key]
        metric.update(value=_number(row.get(column)), current=_number(row.get(column)), unit=unit, scope=scope)
        if key in {"bill", "gsc", "clicks"}:
            metric["verified"] = None
        if key == "gsc":
            metric["observed_at"] = row.get("search_data_at")
        if key == "clicks":
            metric["observed_at"] = row.get("last_click_on")
    if key in {"rr", "promoters", "eff", "d1", "d7", "dp1", "dp7"} and row.get("metrics_valid") is not True:
        metric.update(value=None, current=None, baseline=None, denominator=None, verified=row.get("metrics_valid"))
    return metric
