"""Shared by the simplified radar's tests (the stable task source, its nights and the gateway's table): catalog payloads
with ranked board signals, and on PostgreSQL a published mirror version with rs_ids and the revenue series.

The payloads are DramaInput-shaped like trends_session_helpers.drama; the mirror rows are written as the table owner,
the observer gets what a publish grants it (USAGE on the version's schema, SELECT on its rs_ids), nothing more.
"""

from datetime import date, datetime

from obs_db_helpers import execute, is_postgres
from trends_session_helpers import SOURCE, identity

BOARD_ISSUE = date(2026, 9, 24)


def board_drama(
    index: int,
    *,
    title: str,
    boards: dict[str, int] | None = None,
    issue: date = BOARD_ISSUE,
    language: str = "en",
    theater: str = "DramaBox",
    extra_signals: list[dict] | None = None,
) -> dict:
    """A catalog payload holding a rank on each board in `boards` (kind -> rank) on `issue`."""
    signals = [
        {"kind": kind, "source_ref": f"{kind}:{index}", "observed_at": f"{issue:%Y-%m-%d}", "label": "", "rank": rank, "grade": "", "note": ""}
        for kind, rank in (boards or {}).items()
    ]
    return {
        "source": SOURCE,
        "source_id": f"sid{index:04d}",
        "language": language,
        "title": title,
        "theater": theater,
        "tags": [],
        "listed_at": None,
        "availability": "active",
        "signals": [*signals, *(extra_signals or [])],
        "channel_rules": {},
    }


def board_identity(index: int, language: str = "en") -> str:
    return identity(f"sid{index:04d}", language)


async def seed_mirror(
    url: str, rows: list[dict], *, version: int = 1, as_of: datetime, latest_snapshot: date | None, observer: str | None = None, status: str = "published"
) -> str:
    """A mirror version holding rs_ids `rows` (id, canonical, locale, title) and each row's series (days, revenue);
    returns the version's schema. PostgreSQL only."""
    assert is_postgres(url)
    schema = f"pickm_v{version:06d}"
    await execute(
        url,
        "insert into pick_mirror.versions (id, schema_name, status, as_of, fingerprint, latest_snapshot, created_at, published_at)"
        " values (:id, :s, :st, :a, :f, :l, :a, :a)",
        id=version,
        s=schema,
        st=status,
        a=as_of,
        f="ab" * 32,
        l=latest_snapshot,
    )
    await execute(url, f"create schema {schema}")
    await execute(
        url,
        f"create table {schema}.rs_ids (id text not null, canonical_id text, locale text not null, slug text not null, title text not null,"
        " chapter_count integer not null, pay_start integer not null, is_public_canonical boolean not null)",
    )
    for row in rows:
        canonical = row.get("canonical", row["id"])
        await execute(
            url,
            f"insert into {schema}.rs_ids values (:id, :c, :l, :slug, :t, 60, 10, :pub)",
            id=row["id"],
            c=canonical,
            l=row.get("locale", "en"),
            slug=f"{row['id']}-slug",
            t=row["title"],
            pub=canonical == row["id"],
        )
        days, revenue = zip(*row["series"], strict=True) if row.get("series") else ((), ())
        await execute(
            url,
            "insert into pick_mirror.series (drama_id, days, revenue_cents, promoters, updated_at) values (:id, :d, :r, :p, :a)"
            " on conflict (drama_id) do update set days = excluded.days, revenue_cents = excluded.revenue_cents, promoters = excluded.promoters",
            id=row["id"],
            d=list(days),
            r=[float(value) for value in revenue],
            p=[1] * len(days),
            a=as_of,
        )
    if observer is not None:
        await execute(url, f'grant usage on schema {schema} to "{observer}"')
        await execute(url, f'grant select on {schema}.rs_ids to "{observer}"')
    return schema
