"""Read/validate once, then build the historical view without mutating evidence.

DB -> rows -> row validation -> legacy / eligible pilot -> in-memory view
          bad field -> typed reason + dependent null values (other rows survive)
File/schema/hash errors abort startup; programming errors are never swallowed.
"""

import hashlib
import json
import math
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .constants import DB_SHA256, RULES_VERSION, ZIP_SHA256
from .scorer import score_row
from .storage import get_db_connection

STATUSES = (
    "historical_data",
    "historical_zero",
    "fallback_unverified",
    "missing_series",
    "invalid_series",
)
LABELS = {
    "⚡ 24h突发引爆": "历史末段突增",
    "🚨 拐点起飞": "历史增速上扬",
    "📈 潜伏连涨": "历史持续爬升",
    "🔥 高位稳定": "历史高位平稳",
    "⏳ 正常平稳": "未命中增强信号",
}
SIGNALS = frozenset(("历史末段突增", "历史增速上扬", "历史持续爬升"))
SCORE_FIELDS = (
    "score",
    "heat_score",
    "momentum_score",
    "platform_score",
    "promo_score",
    "breakout_score",
    "velocity_24h",
    "acceleration",
    "climb_days",
)


def finite(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def valid_text(value):
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
        return True
    except UnicodeEncodeError:
        return False


def json_safe(value):
    if isinstance(value, str) and not valid_text(value):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    return value if value is None or isinstance(value, (str, bool, int, float)) else None


def safe_url(value):
    if not valid_text(value) or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() in ("https", "http") and parts.hostname and not parts.username and not parts.password:
            return value
    except ValueError:
        pass
    return None


def normalize_record(raw, original):
    errors = []

    def error(field, code):
        errors.append({"field": field, "code": code})

    def decode(field, expected):
        value = raw.get(field)
        if value is None or value == "":
            return expected()
        try:
            parsed = json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError, RecursionError):
            error(field, "invalid_json")
            return None
        if not isinstance(parsed, expected):
            error(field, "invalid_shape")
            return None
        return parsed

    metadata = {}
    for field in ("platforms", "genres", "promo_tags"):
        value = decode(field, list)
        if value is not None and not all(valid_text(x) for x in value):
            error(field, "invalid_members")
            value = None
        metadata[field] = value
    links = decode("platform_urls", dict)
    if links is not None and not all(valid_text(k) and valid_text(v) for k, v in links.items()):
        error("platform_urls", "invalid_members")
        links = None
    metadata["platform_urls"] = {k: u for k, v in (links or {}).items() if (u := safe_url(v))}
    timeline = decode("timeline_data", list)
    if timeline is not None:
        previous = None
        for point in timeline:
            if not isinstance(point, dict) or not isinstance(point.get("date"), str):
                error("timeline_data", "invalid_point")
                timeline = None
                break
            try:
                day = date.fromisoformat(point["date"])
                if day.isoformat() != point["date"]:
                    raise ValueError("not an ISO day")
            except ValueError:
                error("timeline_data", "invalid_date")
                timeline = None
                break
            if not finite(point.get("value")) or not 0 <= point["value"] <= 100:
                error("timeline_data", "invalid_value")
                timeline = None
                break
            if previous is not None and (day - previous).days != 1:
                error("timeline_data", "nonconsecutive_dates")
                timeline = None
                break
            previous = day
    if timeline is None:
        status = "invalid_series"
    elif not timeline:
        status = "fallback_unverified" if (raw.get("avg_heat"), raw.get("peak_heat"), raw.get("momentum")) == (5, 10, 0) else "missing_series"
    else:
        status = "historical_data" if any(p["value"] > 0 for p in timeline) else "historical_zero"
    # Copy only validated point fields; never send arbitrary nested source data.
    timeline = [{"date": p["date"], "value": p["value"]} for p in timeline] if timeline is not None else None
    platform_count, promo = raw.get("platform_count"), raw.get("has_promo_tag")
    formula_metadata_ok = True
    if not isinstance(platform_count, int) or isinstance(platform_count, bool) or platform_count < 1:
        error("platform_count", "invalid_count")
        formula_metadata_ok = False
    if promo not in (0, 1) or not isinstance(promo, (int, bool)):
        error("has_promo_tag", "invalid_flag")
        formula_metadata_ok = False
    if metadata["promo_tags"] is None:
        formula_metadata_ok = False
    formula_row = {
        **raw,
        "promo_tags": metadata["promo_tags"],
        "clean_title": raw.get("clean_title") if valid_text(raw.get("clean_title")) else None,
    }
    metrics_ok = True
    for field in ("avg_heat", "peak_heat", "momentum"):
        if not finite(raw.get(field)) or not ((-100 <= raw[field] <= 200) if field == "momentum" else (0 <= raw[field] <= 100)):
            metrics_ok = False
            error(field, "unavailable_or_invalid_number")
    legacy = score_row(formula_row, timeline) if formula_metadata_ok and metrics_ok and timeline is not None else None
    pilot = {k: None for k in SCORE_FIELDS}
    pilot.update(
        tier=None,
        tier_name=None,
        prediction_label="数据不足",
        growth_pct=None,
        growth_state="unavailable",
        avg_heat=None,
        peak_heat=None,
        momentum=None,
        rules_version=RULES_VERSION,
    )
    if formula_metadata_ok and timeline and len(timeline) >= 14:
        values = [p["value"] for p in timeline]
        recent = sum(values[-7:]) / 7
        prior = sum(values[-14:-7]) / 7
        growth = (recent - prior) / prior * 100 if prior > 0 else None
        # The reference clamps momentum; an unrepresentable display percentage
        # must still be null rather than leaking Infinity to JSON and CSV.
        overflow = growth is not None and not finite(growth)
        momentum = max(-100, min(200, round(growth if growth is not None else recent * 10, 1)))
        if overflow:
            error("growth_pct", "numeric_overflow")
            growth = None
        avg, peak = round(sum(values) / len(values), 1), round(max(values), 1)
        scored = score_row(
            {**formula_row, "avg_heat": avg, "peak_heat": peak, "momentum": momentum},
            timeline,
        )
        pilot.update(
            scored,
            avg_heat=avg,
            peak_heat=peak,
            momentum=momentum,
            growth_pct=round(growth, 1) if growth is not None else None,
            growth_state="unavailable" if overflow else ("percent" if growth is not None else ("from_zero" if recent > 0 else "zero_window")),
        )
        pilot["prediction_label"] = LABELS[scored["prediction_label"]]
    fetched = raw.get("fetched_at")
    try:
        fetched_iso = datetime.fromtimestamp(fetched, UTC).isoformat() if finite(fetched) else None
    except (ValueError, OverflowError, OSError):
        fetched_iso = None
    if fetched is not None and fetched_iso is None:
        error("fetched_at", "invalid_timestamp")

    def text(field):
        value = raw.get(field)
        if value is not None and not valid_text(value):
            error(field, "invalid_text")
            return None
        return value

    result = dict(
        id=raw["id"],
        clean_title=text("clean_title"),
        original_title=text("original_title"),
        synopsis=text("synopsis"),
        geo=text("geo"),
        timeframe=text("timeframe"),
        cover_url=safe_url(raw.get("cover_url")),
        episodes=raw.get("episodes") if finite(raw.get("episodes")) and raw["episodes"] > 0 else None,
        platform_count=platform_count if formula_metadata_ok else None,
        **metadata,
        series_status=status,
        series_start=timeline[0]["date"] if timeline else None,
        series_end=timeline[-1]["date"] if timeline else None,
        period_days=len(timeline) if timeline else None,
        timeline_data=timeline,
        recent_series=timeline[-7:] if timeline is not None else None,
        cache_written_at=fetched_iso,
        provenance="imported_unverified",
        record_errors=errors,
        original=json_safe(original),
        legacy_replay=legacy,
        pilot=pilot,
    )
    if original:
        for key, value in original.items():
            if key in SCORE_FIELDS and not finite(value):
                result["original"][key] = None
                error("original." + key, "invalid_number")
            elif (isinstance(value, str) and not valid_text(value)) or not (value is None or isinstance(value, (str, int, float, bool))):
                error("original." + key, "invalid_storage_type")
    return result


@dataclass
class Snapshot:
    rows: list
    manifest: dict
    stats: dict
    audit: dict

    def __post_init__(self):
        self.by_id = {row["id"]: row for row in self.rows}


def load_snapshot(path: Path, *, expected_hash=DB_SHA256):
    path = Path(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != expected_hash:
        raise ValueError("snapshot hash mismatch")
    try:
        with get_db_connection(path) as connection:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("SQLite integrity check failed")
            required = {
                "dramas": {
                    "id",
                    "clean_title",
                    "original_title",
                    "platforms",
                    "platform_count",
                    "has_promo_tag",
                    "promo_tags",
                    "genres",
                    "platform_urls",
                    "episodes",
                    "synopsis",
                    "cover_url",
                },
                "trends_cache": {
                    "clean_title",
                    "geo",
                    "timeframe",
                    "avg_heat",
                    "peak_heat",
                    "momentum",
                    "timeline_data",
                    "fetched_at",
                },
                "push_scores": {
                    "clean_title",
                    "score",
                    "tier",
                    "tier_name",
                    "heat_score",
                    "momentum_score",
                    "platform_score",
                    "promo_score",
                    "prediction_label",
                    "breakout_score",
                    "velocity_24h",
                    "acceleration",
                    "climb_days",
                },
            }
            schema = {t: [r["name"] for r in connection.execute(f"PRAGMA table_info({t})")] for t in required}
            if any(not fields.issubset(schema[t]) for t, fields in required.items()):
                raise ValueError("required snapshot schema missing")
            rows = [
                dict(r)
                for r in connection.execute("""SELECT d.*, t.geo, t.timeframe,
                t.avg_heat,t.peak_heat,t.momentum,t.timeline_data,t.fetched_at
                FROM dramas d LEFT JOIN trends_cache t ON d.clean_title=t.clean_title ORDER BY d.id""")
            ]
            originals = {r["clean_title"]: dict(r) for r in connection.execute("SELECT * FROM push_scores")}
    except sqlite3.DatabaseError as exc:
        raise ValueError("invalid SQLite snapshot") from exc
    normalized = [normalize_record(row, originals.get(row["clean_title"])) for row in rows]
    latest = max((r["series_end"] for r in normalized if r["series_end"]), default=None)
    mismatch = Counter()
    mismatch_details = []
    for row in normalized:
        row["is_older_window"] = row["series_end"] < latest if row["series_end"] and latest else None
        row["snapshot_id"] = digest
        diffs = {}
        if row["original"] and row["legacy_replay"]:
            for field, value in row["legacy_replay"].items():
                if field != "clean_title" and value != row["original"].get(field):
                    mismatch[field] += 1
                    diffs[field] = {
                        "original": row["original"].get(field),
                        "legacy_replay": value,
                    }
        row["score_differences"] = diffs
        if diffs:
            mismatch_details.append({"id": row["id"], "fields": diffs})
    writes = [r["cache_written_at"] for r in normalized if r["cache_written_at"]]
    manifest = dict(
        snapshot_id=digest,
        db_sha256=digest,
        source_zip_sha256=ZIP_SHA256,
        loaded_at=datetime.now(UTC).isoformat(),
        latest_cache_written_at=max(writes, default=None),
        catalog_total=len(rows),
        integrity_check="ok",
        schema_columns=schema,
        rules_version=RULES_VERSION,
    )
    states = {s: sum(r["series_status"] == s for r in normalized) for s in STATUSES}
    stats = dict(
        snapshot=manifest,
        catalog_total=len(rows),
        series_status_counts=states,
        with_series=states["historical_data"] + states["historical_zero"],
        without_series=states["fallback_unverified"] + states["missing_series"] + states["invalid_series"],
        scored=sum(r["pilot"]["score"] is not None for r in normalized),
        unrated=sum(r["pilot"]["score"] is None for r in normalized),
        pilot_tiers=dict(Counter(r["pilot"]["tier"] for r in normalized if r["pilot"]["tier"])),
        original_tiers=dict(Counter(r["original"]["tier"] for r in normalized if r["original"])),
        signals=sum(r["pilot"]["prediction_label"] in SIGNALS for r in normalized),
        latest_series_end=latest,
        series_end_dates=sorted({r["series_end"] for r in normalized if r["series_end"]}, reverse=True),
    )
    audit = dict(
        manifest=manifest,
        stats=stats,
        legacy_mismatches=dict(mismatch),
        legacy_mismatch_details=mismatch_details,
        record_errors=[{"id": r["id"], "errors": r["record_errors"]} for r in normalized if r["record_errors"]],
    )
    return Snapshot(normalized, manifest, stats, audit)
