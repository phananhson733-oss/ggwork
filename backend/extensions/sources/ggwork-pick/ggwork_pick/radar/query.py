"""Historical filters and CSV, ported from the accepted local pilot."""

import csv
import io
import re
from datetime import date
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel

from .snapshot import SIGNALS, Snapshot


class Filters(BaseModel):
    search: str = ""
    platform: str = ""
    tier: str = ""
    series_status: str = ""
    series_end: str = ""
    tab: str = "all"
    sort: str | None = None


def filters(
    search: str = Query("", max_length=200),
    platform: Literal["", "ReelShort", "DramaBox", "FlareFlow"] = "",
    tier: Literal["", "S", "A", "B", "C", "unrated"] = "",
    series_status: Literal[
        "",
        "historical_data",
        "historical_zero",
        "missing_series",
        "fallback_unverified",
        "invalid_series",
    ] = "",
    series_end: str = "",
    tab: Literal["all", "signals"] = "all",
    sort: Literal["score_desc", "title_asc", "series_end_desc"] | None = None,
):
    if series_end:
        try:
            if date.fromisoformat(series_end).isoformat() != series_end:
                raise ValueError("noncanonical date")
        except ValueError:
            raise HTTPException(422, "series_end must be an ISO date") from None
    return Filters(
        search=search.strip(),
        platform=platform,
        tier=tier,
        series_status=series_status,
        series_end=series_end,
        tab=tab,
        sort=sort,
    )


def filtered_rows(snapshot: Snapshot, f: Filters):
    rows = []
    for row in snapshot.rows:
        pilot = row["pilot"]
        if f.search and f.search.casefold() not in ((row["original_title"] or "") + " " + (row["clean_title"] or "")).casefold():
            continue
        if f.platform and f.platform not in (row["platforms"] or []):
            continue
        if f.tier and (pilot["tier"] or "unrated") != f.tier:
            continue
        if f.series_status and row["series_status"] != f.series_status:
            continue
        if f.series_end and row["series_end"] != f.series_end:
            continue
        if f.tab == "signals" and pilot["prediction_label"] not in SIGNALS:
            continue
        rows.append(row)
    # Sort copies, never the shared rows. The last tie-break is always numeric ID.
    rows.sort(key=lambda r: r["id"])
    if f.sort == "title_asc":
        rows.sort(
            key=lambda r: (
                r["original_title"] is None,
                (r["original_title"] or "").casefold(),
            )
        )
    elif f.sort == "series_end_desc":
        rows.sort(key=lambda r: r["series_end"] or "", reverse=True)
    else:
        rows.sort(
            key=lambda r: r["pilot"]["score"] if r["pilot"]["score"] is not None else -1,
            reverse=True,
        )
        if f.sort is None and f.tab == "signals":
            rows.sort(
                key=lambda r: r["pilot"]["breakout_score"] if r["pilot"]["breakout_score"] is not None else -1,
                reverse=True,
            )
    return rows


def csv_text(value):
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value)
    if re.match(r"^\s*[=+\-@]|^[\t\r\n]", text):
        text = "'" + text
    return text


def export_rows(rows, snapshot_id):
    fields = [
        "id",
        "original_title",
        "platforms",
        "series_status",
        "provenance",
        "series_start",
        "series_end",
        "cache_written_at",
        "rules_version",
        "pilot_score",
        "pilot_tier",
        "prediction_label",
        "avg_heat",
        "peak_heat",
        "growth_pct",
        "growth_state",
        "heat_score",
        "momentum_score",
        "platform_score",
        "promo_score",
        "velocity_24h",
        "acceleration",
        "climb_days",
        "original_score",
        "legacy_replay_score",
        "record_errors",
        "snapshot_id",
    ]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for r in rows:
        p = r["pilot"]
        data = {k: r.get(k) for k in fields}
        data.update(
            {
                k: p.get(k)
                for k in (
                    "rules_version",
                    "prediction_label",
                    "avg_heat",
                    "peak_heat",
                    "growth_pct",
                    "growth_state",
                    "heat_score",
                    "momentum_score",
                    "platform_score",
                    "promo_score",
                    "velocity_24h",
                    "acceleration",
                    "climb_days",
                )
            }
        )
        data.update(
            platforms=", ".join(r["platforms"] or []),
            pilot_score=p["score"],
            pilot_tier=p["tier"],
            original_score=(r["original"] or {}).get("score"),
            legacy_replay_score=(r["legacy_replay"] or {}).get("score"),
            record_errors="; ".join(e["field"] + ":" + e["code"] for e in r["record_errors"]),
            snapshot_id=snapshot_id,
        )
        writer.writerow({k: csv_text(v) for k, v in data.items()})
    return output.getvalue().encode("utf-8-sig")
