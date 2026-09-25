"""Shared by the TR-14 session tests: a shared catalog batch in the database, the canary's own control list (a copy of
TR-05's format, never its file), the cron's environment, and one trigger through the real __main__.

Everything runs on a ManualClock (the night passes in seconds) against trends_fake_google.FakeGoogle: no request
leaves the process. Rows are read back through a separate test engine (obs_db_helpers.rows), never the code under test.
"""

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from cryptography.fernet import Fernet
from obs_db_helpers import as_json, collector_env, execute, rows

from ggwork_pick.observe.clock import ManualClock, random_source
from ggwork_pick.observe.trends import __main__ as entry

SOURCE = "realshort-pick"
TARGET = date(2026, 9, 26)  # the target date of a night from 2026-09-25 20:30 to 2026-09-26 01:45 UTC
EVE = TARGET - timedelta(days=1)
LANGUAGES = ("en", "es", "de", "fr", "it", "pt")


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), UTC)


def identity(source_id: str, language: str) -> str:
    return json.dumps([SOURCE, source_id, language], separators=(",", ":"))


def drama(index: int, *, language: str = "en", listed_at: date | None = None, title: str | None = None, signal_on: date | None = None) -> dict:
    """A DramaInput-shaped payload, as the importer stores it in ggwp_drama_versions."""
    signals = [{"kind": "kd", "source_ref": f"kd:{index}", "observed_at": f"{signal_on:%Y-%m-%d}", "label": "", "grade": "", "note": ""}] if signal_on else []
    return {
        "source": SOURCE,
        "source_id": f"sid{index:04d}",
        "language": language,
        "title": title or f"synthetic drama {index}",
        "theater": "ReelShort",
        "tags": [],
        "listed_at": f"{listed_at:%Y-%m-%d}" if listed_at else None,
        "availability": "active",
        "signals": signals,
        "channel_rules": {},
    }


def identity_of(payload: dict) -> str:
    return identity(payload["source_id"], payload["language"])


async def seed_catalog(url: str, payloads: list[dict], *, batch_id: str = "cat-1", published_at: datetime | None = None) -> str:
    """A published shared catalog batch holding `payloads` (the observer reads it, never writes it)."""
    published = (published_at or at(EVE, 12)).isoformat(timespec="microseconds")
    await execute(
        url,
        "insert into ggwp_import_batches (id, owner_id, kind, content_hash, raw_blob_path, status, source_as_of, created_at, published_at, validation_json)"
        " values (:id, 'system:shared', 'catalog', :hash, :path, 'published', null, :at, :at, '{}')",
        id=batch_id,
        hash=batch_id.ljust(64, "0")[:64],
        path=f"blobs/{batch_id}.json",
        at=published,
    )
    for payload in payloads:
        await execute(
            url,
            "insert into ggwp_drama_versions (batch_id, identity, payload_json) values (:b, :i, :p)",
            b=batch_id,
            i=identity_of(payload),
            p=json.dumps(payload, ensure_ascii=False),
        )
    return batch_id


def recent_catalog(count: int, *, target: date = TARGET, languages: tuple[str, ...] = LANGUAGES) -> list[dict]:
    """`count` dramas listed within 14 days of `target`, spread over the six languages, newest first by index."""
    return [drama(index, language=languages[index % len(languages)], listed_at=target - timedelta(days=index % 14)) for index in range(count)]


# One market phrase for every geo the canary queries: the controls' US and DE, and market-map-v1's first-round geos of
# the six languages (canary.missing_market_geos); a list without one of them is refused.
MARKET = (
    ("WW", "short drama"),
    ("US", "short drama"),
    ("ES", "drama corto"),
    ("MX", "drama corto"),
    ("DE", "Kurzdrama"),
    ("FR", "drama court"),
    ("IT", "drama breve"),
    ("BR", "drama curto"),
)


def write_controls(tmp_path: Path, payloads: list[dict], *, market: list[dict] | None = None, name: str = "controls.json") -> Path:
    """Our own copy of TR-05's format: identity keys, geo and group; market phrases per geo."""
    controls = [{"identity": identity_of(payload), "geo": "US" if payload["language"] == "en" else "DE", "group": "positive"} for payload in payloads]
    document = {
        "format": "trends-canary-controls-v1",
        "note": "test copy",
        "controls": controls,
        "market": market if market is not None else [{"geo": geo, "term": term} for geo, term in MARKET],
    }
    path = tmp_path / name
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def trends_env(url: str, *, mode: str = "canary1", key: str | None = None, **extra: str | None) -> dict[str, str]:
    env = collector_env(url, PICK_OBS_TRENDS_MODE=mode, PICK_OBS_STATE_KEY=key or Fernet.generate_key().decode(), **extra)
    return env


async def trigger(env, clock: ManualClock, google, *, controls: Path, pacer=None, seed: int = 7, argv=("run",), out=None, err=None) -> int:
    """One cron trigger through the real entry point."""
    return await entry.amain(
        list(argv),
        environ=env,
        clock=clock,
        rng=random_source(seed),
        transport=google.transport(),
        pacer=pacer,
        controls_path=controls,
        out=out,
        err=err,
    )


async def batches(url: str) -> list[dict]:
    found = await rows(url, "select * from ggwp_obs_batches where channel = 'trends' order by target_date")
    for row in found:
        for column in ("plan_json", "summary_json", "status_codes_json"):
            row[column] = as_json(row[column])
    return found


async def request_rows(url: str) -> list[dict]:
    return await rows(url, "select * from ggwp_obs_requests where channel = 'trends' order by id")


async def raw_rows(url: str) -> list[dict]:
    found = await rows(url, "select * from ggwp_obs_raw order by id")
    for row in found:
        row["params_json"], row["data_json"] = as_json(row["params_json"]), as_json(row["data_json"])
    return found


async def budget_requests(url: str, day: date = TARGET) -> int:
    found = await rows(url, "select requests from ggwp_obs_budget where channel = 'trends' and budget_day = :d", d=f"{day:%Y-%m-%d}")
    return found[0]["requests"] if found else 0
