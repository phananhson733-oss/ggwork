"""The stable mode's task source for the simplified radar: each night our hottest dramas, one daily worldwide unit each
(simplified scope 2026-09-30, sections 3 and 6 item 1; user decisions 1 to 3).

Who is in, in this order, TARGET_DRAMAS at most:
1. the three ranked boards of the current shared catalog batch (payload_json.signals[]), each on its own latest issue
   and by its own rank: 鹊娱转化榜 qc, then 鹊娱收入榜 qr, then Kalos 日榜 kd. Ranks of different boards never mix.
2. short of TARGET_DRAMAS, ReelShort's canonical dramas by revenue on the snapshot day of the latest published mirror
   version. pick_mirror.series.revenue_cents is RealShort's rolling-30-day figure as of each day: it is read on one day,
   never summed or subtracted. Titles and languages come from that version's rs_ids.
A drama already in (the same identity: one language version of one drama) takes no second unit: its basis joins the
pick already in. Two identities never merge, even when their titles clean to the same term (scope sections 3 and 5: each
language title is its own row, nothing is merged automatically); each asks Google on its own. Every pick keeps its basis
(the board, its issue and the rank; or the revenue rank and day) in plan_json's notes under NOTES_KEY, which the
gateway's table reads (observe/trends_table.py).

The unit: the full title with punctuation and dub markers cleaned (search_term), worldwide (WW), daily (today 1-m),
the series only: two requests, explore and multiline. The entry runs this source only with GRANULARITY=D and ROUTE=a_only
(__main__.source_for). The observer reads all of it already (grants.py: the catalog batches, pick_mirror.versions and
series, each version's rs_ids); nothing new is granted. The revenue part is PostgreSQL only (SQLite has no mirror) and
optional: without a published version, a readable table or its right it is left out, the reason in the notes, and the
boards run alone. No pick at all is refused (exit 2): the night would measure nothing.
"""

import json
import re
import unicodedata
from base64 import urlsafe_b64encode
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, text

from ggwork_pick.models import drama_versions, import_batches
from ggwork_pick.observe.errors import Refused
from ggwork_pick.observe.lease import ReadStep
from ggwork_pick.observe.trends.canary import CATALOG_KIND, SHARED_OWNER, SourceUnits
from ggwork_pick.observe.trends.source import GEO_WORLDWIDE, MAX_TERM_LENGTH
from ggwork_pick.observe.trends.units import QueryUnit, unit_key

SOURCE_NAME = "top_dramas"
NOTES_KEY = "top_dramas"
TARGET_DRAMAS = 100  # scope section 3: N is 100, 200 requests; stable's plan leaves room for 165 (budget.plan_budget)
BOARDS = ("qc", "qr", "kd")  # 鹊娱转化榜, 鹊娱收入榜, Kalos 日榜: the ranked kinds of the catalog, in the order they are taken
REVENUE = "revenue"
GEO = GEO_WORLDWIDE
GRANULARITY = "D"
ITEM = "title"
RS_PLATFORM = "ReelShort"
# The catalog's spelling of a ReelShort row (identity_row_key_cases.json: source realshort-pick, source_id the row key
# reelshort-<id> in unpadded base64url), so a board drama that is the same ReelShort row meets it by identity.
RS_CATALOG_SOURCE = "realshort-pick"
RS_ROW_KEY_PREFIX = "reelshort-"
REVENUE_LOOKUP = 2 * TARGET_DRAMAS  # revenue ranks read at most: the same identity or term drops some
_VERSION_SCHEMA = re.compile(r"pickm_v[0-9]{6}")

# ---- the search term -------------------------------------------------------------------------------------------------

# Dub markers, as the catalog writes them in the six Euro-American languages and in Chinese. Latin ones are whole words
# (Dubai stays), and only inside brackets or at the end of the title, after a separator or a space.
_DUB = (
    r"(?:\b(?:english\s+)?(?:dub|dubs|dubbed|dubbing|doblado|doblada|doblaje|dublado|dublada|dublagem|doublé|doublée|doublage"
    r"|synchronisiert|synchronfassung|doppiato|doppiata|doppiaggio)\b(?:\s+version)?|配音版?|译制版?|譯製版?)"
)
_OPEN, _CLOSE = "([（【〔{", ")]）】〕}"
_INSIDE = rf"[^{re.escape(_OPEN + _CLOSE)}]*"
_BRACKETED_DUB = re.compile(rf"[{re.escape(_OPEN)}]{_INSIDE}{_DUB}{_INSIDE}[{re.escape(_CLOSE)}]", re.IGNORECASE)
_TRAILING_DUB = re.compile(rf"(?:\s*[-–—:|/·]\s*|\s+|^){_DUB}\s*$", re.IGNORECASE)
_APOSTROPHES = frozenset("'’ʼ‘`´")
_SPACES = re.compile(r"\s+")


def _without_dub_markers(title: str) -> str:
    cleaned = _BRACKETED_DUB.sub(" ", title)
    before = None
    while before != cleaned:  # "Title - English Dub (Dubbed)" has two
        before, cleaned = cleaned, _TRAILING_DUB.sub("", cleaned)
    return cleaned


def _kept(text: str, index: int) -> str:
    """One character of the term: punctuation and symbols become a space, except an apostrophe inside a word."""
    char = text[index]
    if char in _APOSTROPHES:
        inside = 0 < index < len(text) - 1 and text[index - 1].isalnum() and text[index + 1].isalnum()
        return "'" if inside else " "
    return " " if unicodedata.category(char)[0] in "PS" else char


def search_term(title: str) -> str | None:
    """What Trends is asked for a title: NFKC, dub markers out, punctuation and symbols to spaces (an apostrophe inside
    a word stays, as '), whitespace collapsed. None when nothing usable is left (scope section 3)."""
    if not isinstance(title, str):
        return None
    normal = _without_dub_markers(unicodedata.normalize("NFKC", title))
    term = _SPACES.sub(" ", "".join(_kept(normal, index) for index in range(len(normal)))).strip()
    return term if 0 < len(term) <= MAX_TERM_LENGTH and term.isprintable() else None


# ---- picks and their basis -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Basis:
    """Why a drama is in: a board, its issue (the day) and the rank; or revenue, its snapshot day and the rank."""

    kind: str
    board_date: date | None
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "board_date": _day_text(self.board_date), "rank": self.rank}


@dataclass(frozen=True)
class Candidate:
    identity: str
    title: str
    platform: str
    language: str
    basis: Basis


@dataclass(frozen=True)
class Pick:
    identity: str
    title: str
    platform: str
    language: str
    term: str
    basis: tuple[Basis, ...]

    def joined(self, basis: Basis) -> "Pick":
        return self if basis in self.basis else replace(self, basis=(*self.basis, basis))

    def to_note(self, order: int) -> dict[str, Any]:
        return {
            "order": order,
            "identity": self.identity,
            "title": self.title,
            "platform": self.platform,
            "language": self.language,
            "basis": [basis.to_dict() for basis in self.basis],
        }


def merged(candidates: Iterable[Candidate], *, target: int = TARGET_DRAMAS) -> tuple[tuple[Pick, ...], int]:
    """The picks in the candidates' order, at most `target`, and how many candidates had no usable title. A candidate
    whose identity is in already joins its basis to that pick, also once `target` is reached; other identities are
    picks of their own, whatever their term."""
    picks: dict[str, Pick] = {}
    unusable = 0
    for candidate in candidates:
        if candidate.identity in picks:
            picks = {**picks, candidate.identity: picks[candidate.identity].joined(candidate.basis)}
            continue
        term = search_term(candidate.title)
        if term is None:
            unusable += 1
        elif len(picks) < target:
            pick = Pick(candidate.identity, candidate.title.strip(), candidate.platform, candidate.language, term, (candidate.basis,))
            picks = {**picks, candidate.identity: pick}
    return tuple(picks.values()), unusable


def _day_text(value: date | None) -> str | None:
    return f"{value:%Y-%m-%d}" if value is not None else None


# ---- the boards ------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    kind: str
    day: date
    rank: int


@dataclass(frozen=True)
class CatalogEntry:
    identity: str
    title: str
    platform: str
    language: str
    signals: tuple[Signal, ...]


def _day(value: object) -> date | None:
    """A signal's observed_at: YYYY-MM-DD or an ISO time; anything else is no issue."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return datetime.fromisoformat(value).date() if len(value) > 10 else date.fromisoformat(value)
    except ValueError:
        return None


def _signal(value: object) -> Signal | None:
    if not isinstance(value, Mapping) or value.get("kind") not in BOARDS:
        return None
    rank, day = value.get("rank"), _day(value.get("observed_at"))
    return Signal(value["kind"], day, rank) if type(rank) is int and rank >= 0 and day is not None else None


def catalog_entry(identity: str, payload: object) -> CatalogEntry | None:
    """A shared catalog payload (DramaInput's shape) with its ranked board signals; None without a title or language."""
    if not isinstance(payload, Mapping) or not isinstance(payload.get("title"), str) or not isinstance(payload.get("language"), str):
        return None
    signals = payload.get("signals") if isinstance(payload.get("signals"), list) else []
    found = tuple(signal for value in signals if (signal := _signal(value)) is not None)
    theater = payload.get("theater") if isinstance(payload.get("theater"), str) else ""
    return CatalogEntry(identity, payload["title"], theater.strip(), payload["language"].strip().lower(), found)


@dataclass(frozen=True)
class Board:
    kind: str
    issue: date | None
    candidates: tuple[Candidate, ...]

    def note(self) -> dict[str, Any]:
        return {"kind": self.kind, "board_date": _day_text(self.issue), "listed": len(self.candidates)}


def board(entries: Sequence[CatalogEntry], kind: str) -> Board:
    """One board's latest issue (the newest day any drama holds a rank of it), its dramas by rank (the best rank a drama
    holds that day), ties by identity."""
    ranked = [(entry, signal) for entry in entries for signal in entry.signals if signal.kind == kind]
    if not ranked:
        return Board(kind, None, ())
    issue = max(signal.day for _, signal in ranked)
    best: dict[str, tuple[int, CatalogEntry]] = {}
    for entry, signal in ranked:
        if signal.day == issue and (entry.identity not in best or signal.rank < best[entry.identity][0]):
            best = {**best, entry.identity: (signal.rank, entry)}
    ordered = sorted(best.values(), key=lambda pair: (pair[0], pair[1].identity))
    return Board(
        kind, issue, tuple(Candidate(entry.identity, entry.title, entry.platform, entry.language, Basis(kind, issue, rank)) for rank, entry in ordered)
    )


async def shared_catalog(step: ReadStep) -> tuple[str | None, tuple[CatalogEntry, ...]]:
    """The current shared catalog batch (the newest published one, as canary.current_catalog and the gateway pick it)
    and its entries; (None, ()) when there is none."""
    latest = (
        select(import_batches.c.id)
        .where(import_batches.c.owner_id == SHARED_OWNER, import_batches.c.kind == CATALOG_KIND, import_batches.c.status == "published")
        .order_by(import_batches.c.published_at.desc(), import_batches.c.id.desc())
        .limit(1)
    )
    batch_id = (await step.execute(latest)).scalar()
    if batch_id is None:
        return None, ()
    found = await step.execute(select(drama_versions.c.identity, drama_versions.c.payload_json).where(drama_versions.c.batch_id == batch_id))
    entries = (catalog_entry(identity, json.loads(payload) if isinstance(payload, str) else payload) for identity, payload in found.all())
    return batch_id, tuple(entry for entry in entries if entry is not None)


# ---- ReelShort revenue -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Revenue:
    """The revenue fill: its candidates by rank, and what the notes say of it."""

    candidates: tuple[Candidate, ...] = ()
    version: int | None = None
    day: date | None = None
    reason: str | None = None  # why there is none: sqlite, no_version, unreadable

    def note(self, filled: int) -> dict[str, Any]:
        day = _day_text(self.day)
        return {"available": self.reason is None, "reason": self.reason, "mirror_version": self.version, "day": day, "filled": filled}


_LATEST_VERSION = text("SELECT id, schema_name, as_of, latest_snapshot FROM pick_mirror.versions WHERE status = 'published' ORDER BY id DESC LIMIT 1")
# Checked before reading, never by trying: a failed statement would abort the step's transaction, the batch's with it.
_SCHEMA_EXISTS = text("SELECT to_regnamespace(CAST(:s AS text)) IS NOT NULL")
_READABLE = text(
    "SELECT has_schema_privilege(to_regnamespace(CAST(:s AS text)), 'USAGE') AND CASE WHEN to_regclass(CAST(:t AS text)) IS NULL"
    " THEN false ELSE has_table_privilege(to_regclass(CAST(:t AS text)), 'SELECT') END"
)
_RANKED = """SELECT id, locale, title, rc FROM (
  SELECT i.id, i.locale, i.title, s.revenue_cents[array_position(s.days, CAST(:day AS date))] AS rc
    FROM {schema}.rs_ids i JOIN pick_mirror.series s ON s.drama_id = i.id
   WHERE i.is_public_canonical AND array_position(s.days, CAST(:day AS date)) IS NOT NULL) ranked
 WHERE rc > 0 ORDER BY rc DESC, id LIMIT :limit"""


async def _readable(step: ReadStep, schema: str, table: str) -> bool:
    if not (await step.execute(_SCHEMA_EXISTS, {"s": schema})).scalar():
        return False
    return bool((await step.execute(_READABLE, {"t": f"{schema}.{table}", "s": schema})).scalar())


def _snapshot_day(as_of: object, latest_snapshot: object) -> date:
    """The version's snapshot day: its latest_snapshot, never later than its as_of day (UTC), as the data page reads it."""
    moment = as_of if isinstance(as_of, datetime) else datetime.fromisoformat(str(as_of))
    as_of_day = moment.astimezone(UTC).date() if moment.tzinfo else moment.date()
    latest = latest_snapshot if isinstance(latest_snapshot, date) else (date.fromisoformat(str(latest_snapshot)) if latest_snapshot else None)
    return min(latest, as_of_day) if latest is not None else as_of_day


def rs_identity(drama_id: str, locale: str) -> str:
    source_id = urlsafe_b64encode(f"{RS_ROW_KEY_PREFIX}{drama_id}".encode()).decode("ascii").rstrip("=")
    return json.dumps([RS_CATALOG_SOURCE, source_id, locale], ensure_ascii=False, separators=(",", ":"))


async def revenue(step: ReadStep, *, limit: int = REVENUE_LOOKUP) -> Revenue:
    """ReelShort's canonical dramas by revenue on the latest published version's snapshot day, at most `limit`."""
    if step.dialect != "postgresql":
        return Revenue(reason="sqlite")
    if not (await _readable(step, "pick_mirror", "versions") and await _readable(step, "pick_mirror", "series")):
        return Revenue(reason="unreadable")
    version = (await step.execute(_LATEST_VERSION)).mappings().first()
    if version is None:
        return Revenue(reason="no_version")
    schema = version["schema_name"]
    if not _VERSION_SCHEMA.fullmatch(schema) or not await _readable(step, schema, "rs_ids"):
        return Revenue(version=version["id"], reason="unreadable")
    day = _snapshot_day(version["as_of"], version["latest_snapshot"])
    found = await step.execute(text(_RANKED.format(schema=schema)), {"day": day, "limit": limit})
    candidates = tuple(
        Candidate(rs_identity(row["id"], row["locale"].strip().lower()), row["title"], RS_PLATFORM, row["locale"].strip().lower(), Basis(REVENUE, day, rank))
        for rank, row in enumerate(found.mappings().all(), start=1)
    )
    return Revenue(candidates, version["id"], day)


# ---- the source ------------------------------------------------------------------------------------------------------


def query_unit(pick: Pick, order: int) -> QueryUnit:
    """A pick's unit; its priority is its order, so the session's truncation order keeps ours (units.ordered)."""
    key = unit_key("top", pick.identity, GEO, GRANULARITY)
    return QueryUnit(key, ITEM, GEO, (pick.term,), pick.term, GRANULARITY, order, identity=pick.identity)


class TopDramasTaskSource:
    """The stable mode's units for a target date: the day's picks, one daily worldwide unit each."""

    name = SOURCE_NAME

    def __init__(self, *, target: int = TARGET_DRAMAS):
        if type(target) is not int or target < 1:
            raise ValueError("target is a positive number of dramas")
        self._target = target

    async def units(self, step: ReadStep, *, target_date: date) -> SourceUnits:
        batch_id, entries = await shared_catalog(step)
        boards = tuple(board(entries, kind) for kind in BOARDS)
        from_boards = tuple(candidate for found in boards for candidate in found.candidates)
        picks, unusable = merged(from_boards, target=self._target)
        filled = Revenue(reason="not_needed")
        if len(picks) < self._target:
            filled = await revenue(step, limit=max(REVENUE_LOOKUP, 2 * self._target))
            picks, unusable = merged((*from_boards, *filled.candidates), target=self._target)
        if not picks:
            raise Refused("简化版任务来源一部剧都没取到：三个榜的最新一期与 ReelShort 收入补足都是空的（手册 trends-session.md「简化版」）")
        from_revenue = sum(1 for pick in picks if pick.basis[0].kind == REVENUE)
        units = tuple(query_unit(pick, order) for order, pick in enumerate(picks, start=1))
        notes = {
            "catalog_dramas": len(entries),
            NOTES_KEY: {
                "target": self._target,
                "picked": len(picks),
                "boards": [found.note() for found in boards],
                "revenue": filled.note(from_revenue),
                "unusable_titles": unusable,
                "picks": {unit.key: pick.to_note(order) for order, (unit, pick) in enumerate(zip(units, picks, strict=True), start=1)},
            },
        }
        return SourceUnits(units, batch_id, notes)
