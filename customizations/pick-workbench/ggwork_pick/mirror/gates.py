"""The gates before a mirror run publishes (plan 5.3; implementation note P2-4): each one is a GateResult(name, ok, detail).

Which side a failed gate stops (plan 2.5, 5.3, 5.5; CONSEQUENCES):
- "v1" (G2, v1_text): neither side publishes. The run fails the batches it staged; the previous pair stays current.
- "mirror" (G3-G9): the version is marked failed and dropped, and the staged v1 batches go out alone
  (publish_agent_only with reason "degraded:<gate name>").
G1, drift, is the client's DriftError and the run's to handle (U17): nothing here.

detail holds key paths, counts and row identifiers, never a value (plan 5.5, U37, U49): a v1 row is named by its
source_id, a version row by its primary key (G8, which pairs the two, by row_key), at most ROW_ID_CAP of them with the
total beside. details_json shows it to every signed-in user.

The text gates add up page by page before anything is stored, over the JSON exactly as RealShort sent it: scan_v1_page
for G2 (rows[*] with v1's exemptions, before DramaInput strips whitespace; the first page's rules Markdown on its own,
U45), scan_mirror_page before each v2 page's COPY and scan_mirror_meta over manifest.meta for G5. They are pure, return
a new scan and are CPU-bound (brief 0.3): the run awaits scanned_v1_page / scanned_mirror_page, which use a worker
thread. The other gates read the built version on the dedicated connection (run_mirror_gates): SELECTs only, each one
autocommitted with its own timeout, so the connection is idle again when publish_mirror_pair's GRANT comes.

This module is the entry point: GateResult, Findings, GateError and the gate names come from gate_result.py, G7 from
gate_totals.py, and all of them are importable from here.
"""

import asyncio
import base64
import binascii
import json
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from functools import reduce
from itertools import groupby
from operator import itemgetter
from types import MappingProxyType

from ggwork_pick.contracts import UNSTORABLE_TEXT
from ggwork_pick.mirror import pan
from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, FORBIDDEN_NAME, RESOURCE_KEYS, ROW_RESOURCES
from ggwork_pick.mirror.gate_result import (
    CONSEQUENCES,
    CONTROL_TOTALS,
    EMPTY_TABLES,
    FORBIDDEN_COLUMNS,
    MIRROR_GATES,
    MIRROR_TEXT,
    REFERENCES,
    ROW_COUNTS,
    ROW_ID_CAP,
    STATEMENT_TIMEOUT,
    V1_CONSISTENCY,
    V1_TEXT,
    Findings,
    GateError,
    GateResult,
    added,
    count_or_none,
    id_part,
    is_count,
    no_hits,
    read,
    shown_key,
    value_at,
)
from ggwork_pick.mirror.gate_totals import RS_FLAG_KINDS, RS_RANK_COUNTS, ControlTotals, control_totals, control_totals_gate, signal_kinds, theater_kinds
from ggwork_pick.mirror.versions import check_schema_name
from ggwork_pick.mirror.writer import TABLE_KEYS

__all__ = [
    "CONSEQUENCES",
    "CONTROL_TOTALS",
    "EMPTY_TABLES",
    "FORBIDDEN_COLUMNS",
    "MIRROR_GATES",
    "MIRROR_TEXT",
    "REFERENCES",
    "ROW_COUNTS",
    "ROW_ID_CAP",
    "RS_FLAG_KINDS",
    "RS_RANK_COUNTS",
    "STATEMENT_TIMEOUT",
    "V1_CONSISTENCY",
    "V1_TEXT",
    "AcceptEmpty",
    "Candidate",
    "ControlTotals",
    "Findings",
    "GateError",
    "GateResult",
    "MirrorGates",
    "MirrorTextScan",
    "V1Row",
    "V1Scan",
    "control_totals",
    "control_totals_gate",
    "empty_tables_gate",
    "forbidden_columns_gate",
    "mirror_candidates",
    "mirror_text_gate",
    "posted_pairs",
    "references_gate",
    "row_counts_gate",
    "row_key_of",
    "run_mirror_gates",
    "scan_mirror_meta",
    "scan_mirror_page",
    "scan_v1_page",
    "scanned_mirror_page",
    "scanned_v1_page",
    "signal_kinds",
    "table_counts",
    "theater_kinds",
    "v1_consistency_gate",
    "v1_row_id",
    "v1_text_gate",
    "v2_row_id",
]

SIGNAL_CUT = 50  # rs:src/lib/pick/feed-map.ts:323
RS_ROW_PREFIX = "reelshort-"  # rs:src/lib/pick/request.ts:195
V1_ROWS_AT = "v1.rows[*]"

_NUL = re.compile("\x00")
_SURROGATE = re.compile(f"[{chr(0xD800)}-{chr(0xDFFF)}]")
_BASE64URL = re.compile(r"[A-Za-z0-9_-]*")


def v2_row_id(resource: str, row) -> tuple:
    """A v2 row's identifier: the resource and its primary key (the version table's; the cursor key for rs_series_day)."""
    keys = TABLE_KEYS[resource].primary if resource in TABLE_KEYS else RESOURCE_KEYS[resource]
    values = row if isinstance(row, Mapping) else {}
    return (resource, *(id_part(values.get(key)) for key in keys))


def v1_row_id(row):
    """A v1 row's identifier: its source_id (U49)."""
    return id_part(row.get("source_id") if isinstance(row, Mapping) else None)


def _page_rows(body, what: str) -> list:
    rows = body.get("rows") if isinstance(body, Mapping) else None
    if not isinstance(rows, list):
        raise ValueError(f"{what} 页没有 rows 数组")
    return rows


# ---------------------------------------------------------------- G2: v1 text


@dataclass(frozen=True, slots=True)
class V1Row:
    """What G8 needs of one raw v1 row: its source_id, its signals' kinds and its posted records, as RealShort sent them."""

    source_id: object
    kinds: frozenset
    records: frozenset


@dataclass(frozen=True, slots=True)
class V1Scan:
    """The whole v1 pull so far (G2, and G8's rows). Built by scan_v1_page only, a new one per page."""

    pan_found: Findings = Findings()
    nul_found: Findings = Findings()
    surrogate_found: Findings = Findings()
    rules: Mapping[str, int] = field(default_factory=no_hits)
    rows: tuple[V1Row, ...] = ()
    pages: int = 0

    @property
    def row_hits(self) -> int:
        """Pan hits in rows[*]: details_json's scrub_hits.v1."""
        return self.pan_found.hits

    @property
    def rules_hits(self) -> int:
        """Pan hits in the rules Markdown: scrub_hits.v1_rules (U45)."""
        return sum(self.rules.values())


def _strings(value, at: str) -> Iterator[tuple[str, str]]:
    """(path, text) for every string in value, keys included; paths written like pan's, odd keys as ODD_KEY."""
    if isinstance(value, str):
        yield at, value
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _strings(item, f"{at}[*]")
    elif isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{at}.{shown_key(key)}"
            if isinstance(key, str):
                yield here, key
            yield from _strings(item, here)


def _unstorable(row) -> tuple[dict[str, int], dict[str, int]]:
    """(NUL hits, lone surrogate hits) by path over one row, exempt fields included: PostgreSQL stores neither anywhere."""
    found = [(path, text) for path, text in _strings(row, V1_ROWS_AT) if UNSTORABLE_TEXT.search(text)]
    nul = Counter(path for path, text in found if _NUL.search(text))
    surrogate = Counter(path for path, text in found if _SURROGATE.search(text))
    return dict(nul), dict(surrogate)


def _under(prefix: str, hits: Mapping[str, int]) -> dict[str, int]:
    return {f"{prefix}.{path}" if path else prefix: count for path, count in hits.items()}


def _v1_row(row) -> V1Row:
    values = row if isinstance(row, Mapping) else {}
    signals = values.get("signals") if isinstance(values.get("signals"), list) else []
    posted = values.get("posted") if isinstance(values.get("posted"), Mapping) else {}
    records = posted.get("records") if isinstance(posted.get("records"), list) else []
    kinds = frozenset(signal.get("kind") for signal in signals if isinstance(signal, Mapping) and isinstance(signal.get("kind"), str))
    return V1Row(values.get("source_id"), kinds, frozenset(record for record in records if isinstance(record, str)))


def _scan_v1_row(found: tuple[Findings, Findings, Findings], row) -> tuple[Findings, Findings, Findings]:
    pans, nuls, surrogates = found
    ident = v1_row_id(row)
    nul, surrogate = _unstorable(row)
    # pan.scan_value from the row root is scan_v1_row: top-level keys exempt by name, nested ones by path (feed-map.ts:328).
    return pans.add(_under(V1_ROWS_AT, pan.scan_value(row)), ident), nuls.add(nul, ident), surrogates.add(surrogate, ident)


def scan_v1_page(scan: V1Scan, body: Mapping) -> V1Scan:
    """One v1 page body as RealShort sent it, added to scan; pure and CPU-bound (use scanned_v1_page from the event loop).

    rows[*] is scanned for pan text with v1's exemptions and for NUL and lone surrogates everywhere; a rules string (the
    first page's) is scanned on its own as v1.rules (U45). Other page fields (scope, freshness) are not G2's.
    """
    rows = _page_rows(body, "feed v1")
    pans, nuls, surrogates = reduce(_scan_v1_row, rows, (Findings(), Findings(), Findings()))
    rules = body.get("rules")
    return V1Scan(
        pan_found=scan.pan_found.merge(pans),
        nul_found=scan.nul_found.merge(nuls),
        surrogate_found=scan.surrogate_found.merge(surrogates),
        rules=added(scan.rules, pan.scan_value(rules, "v1.rules")) if isinstance(rules, str) else scan.rules,
        rows=(*scan.rows, *(_v1_row(row) for row in rows)),
        pages=scan.pages + 1,
    )


async def scanned_v1_page(scan: V1Scan, body: Mapping) -> V1Scan:
    """scan_v1_page in a worker thread: a page is up to 1000 rows of pan scrubbing (brief 0.3)."""
    return await asyncio.to_thread(scan_v1_page, scan, body)


def v1_text_gate(scan: V1Scan) -> GateResult:
    """G2 over the whole v1 pull. Fails on NUL anywhere in rows[*] (PostgreSQL cannot store it; DramaInput would refuse
    the batch anyway), on a pan hit in rows[*], or on one in the rules Markdown (U45): consequence v1, neither side
    publishes. A lone surrogate is counted, never failed (U18): the import replaces it with ? as it always has."""
    if not scan.pages:
        return GateResult(V1_TEXT, False, {"unscanned": ["v1.rows"]})
    parts = {"pan": scan.pan_found, "nul": scan.nul_found, "surrogates": scan.surrogate_found}
    detail = {name: found.as_detail() for name, found in parts.items() if found.total}
    ok = not scan.pan_found.total and not scan.nul_found.total and not scan.rules
    return GateResult(V1_TEXT, ok, {**detail, **({"rules": dict(scan.rules)} if scan.rules else {})})


# ---------------------------------------------------------------- G5: mirror text


@dataclass(frozen=True, slots=True)
class MirrorTextScan:
    """The v2 pages and manifest.meta scanned so far (G5); scanned counts rows per resource, to prove every page was seen."""

    found: Findings = Findings()
    scanned: Mapping[str, int] = field(default_factory=no_hits)
    meta_scanned: bool = False


def _scan_v2_row(resource: str, found: Findings, row) -> Findings:
    hits = pan.scan_row(resource, row)
    return found.add(hits, v2_row_id(resource, row)) if hits else found


def scan_mirror_page(scan: MirrorTextScan, resource: str, rows: Sequence) -> MirrorTextScan:
    """One v2 page's rows, before their COPY, added to scan; pure and CPU-bound (use scanned_mirror_page).

    Every string leaf is scrubbed on its own with the resource's exemptions (pan.scan_row; brief section 2 item 2). A hit
    means RealShort's scrub and this port disagree, or RealShort missed one: the version must not go out.
    """
    if resource not in ROW_RESOURCES:
        raise ValueError(f"G5 只扫 feed v2 的行资源，没有 {shown_key(resource)}")
    found = reduce(lambda so_far, row: _scan_v2_row(resource, so_far, row), rows, scan.found)
    scanned = MappingProxyType({**scan.scanned, resource: scan.scanned.get(resource, 0) + len(rows)})
    return MirrorTextScan(found, scanned, scan.meta_scanned)


async def scanned_mirror_page(scan: MirrorTextScan, resource: str, rows: Sequence) -> MirrorTextScan:
    """scan_mirror_page in a worker thread (brief 0.3)."""
    return await asyncio.to_thread(scan_mirror_page, scan, resource, rows)


def scan_mirror_meta(scan: MirrorTextScan, meta: Mapping) -> MirrorTextScan:
    """manifest.meta as finalizeManifest scrubs it: every value but scrub (export-v2-map.ts:922-928), paths under manifest.meta."""
    return MirrorTextScan(scan.found.add(pan.scan_manifest_meta(meta), ("manifest",)), scan.scanned, True)


def mirror_text_gate(scan: MirrorTextScan, counts: Mapping[str, int]) -> GateResult:
    """G5: no pan hit in any v2 row or in manifest.meta. A scan that has not seen every counted row, or the meta, fails
    too: finding nothing in pages never scanned proves nothing. Consequence mirror."""
    unscanned = {
        resource: {"manifest": count_or_none(counts.get(resource)), "scanned": scan.scanned.get(resource, 0)}
        for resource in COUNTED_RESOURCES
        if not (is_count(counts.get(resource)) and counts.get(resource) == scan.scanned.get(resource, 0))
    }
    unscanned = unscanned if scan.meta_scanned else {**unscanned, "manifest.meta": {"manifest": 1, "scanned": 0}}
    detail = {**(scan.found.as_detail() if scan.found.total else {}), **({"unscanned": unscanned} if unscanned else {})}
    return GateResult(MIRROR_TEXT, not scan.found.total and not unscanned, detail)


# ---------------------------------------------------------------- G3: row counts


def _counts_sql(schema: str) -> str:
    return "SELECT " + ", ".join(f"(SELECT count(*) FROM {schema}.{table}) AS {table}" for table in COUNTED_RESOURCES)


async def table_counts(conn, schema_name: str, *, timeout: float = STATEMENT_TIMEOUT) -> Mapping[str, int]:
    """count(*) of the version's eight tables, in one statement."""
    row = await read(conn, ROW_COUNTS, "fetchrow", _counts_sql(check_schema_name(schema_name)), timeout=timeout)
    return MappingProxyType({table: row[table] for table in COUNTED_RESOURCES})


def row_counts_gate(measured: Mapping[str, int], counts: Mapping[str, object]) -> GateResult:
    """G3: each table holds manifest.counts rows (brief section 2 item 5: the eight tables, not rs_series_day)."""
    off = {
        table: {"manifest": count_or_none(counts.get(table)), "mirror": measured.get(table)}
        for table in COUNTED_RESOURCES
        if not (is_count(counts.get(table)) and counts.get(table) == measured.get(table))
    }
    return GateResult(ROW_COUNTS, not off, {"tables": off, "total": len(off)} if off else {})


# ---------------------------------------------------------------- G4: forbidden columns

_COLUMNS = "SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = $1 ORDER BY table_name, ordinal_position"


async def forbidden_columns_gate(conn, schema_name: str, *, timeout: float = STATEMENT_TIMEOUT) -> GateResult:
    """G4: no column of the version is named like a forbidden field (FORBIDDEN_NAME; has_pan and bill_rank are not)."""
    rows = await read(conn, FORBIDDEN_COLUMNS, "fetch", _COLUMNS, check_schema_name(schema_name), timeout=timeout)
    hits = Counter(f"{shown_key(row['table_name'])}.{shown_key(row['column_name'])}" for row in rows if FORBIDDEN_NAME.search(row["column_name"]))
    return GateResult(FORBIDDEN_COLUMNS, not hits, {"paths": dict(hits), "total": sum(hits.values())} if hits else {})


# ---------------------------------------------------------------- G6: references

# (path, table, its primary key, the rows with no referent): plan 787. A bill's book_id may miss rs_ids, like RealShort.
_REFERENCES = (
    ("catalog_signals.row_key", "catalog_signals", ("row_key", "kind", "ord"), "NOT EXISTS (SELECT 1 FROM {s}.catalog_rows r WHERE r.row_key = x.row_key)"),
    ("rs_rows.drama_id", "rs_rows", ("row_key",), "NOT EXISTS (SELECT 1 FROM {s}.rs_ids i WHERE i.id = x.drama_id)"),
    ("rs_clicks14.drama_id", "rs_clicks14", ("drama_id", "day"), "NOT EXISTS (SELECT 1 FROM {s}.rs_rows r WHERE r.drama_id = x.drama_id)"),
)


def _orphans_sql(schema: str, table: str, keys: tuple[str, ...], condition: str) -> str:
    listed = ", ".join(f"x.{key}" for key in keys)
    where = condition.format(s=schema)
    return f"SELECT {listed}, count(*) OVER () AS orphans FROM {schema}.{table} x WHERE {where} ORDER BY {listed} LIMIT {ROW_ID_CAP}"


def _orphans(path: str, table: str, keys: tuple[str, ...], rows: Sequence) -> Findings:
    if not rows:
        return Findings()
    total = rows[0]["orphans"]
    return Findings(MappingProxyType({path: total}), tuple((table, *(id_part(row[key]) for key in keys)) for row in rows), total)


async def references_gate(conn, schema_name: str, *, timeout: float = STATEMENT_TIMEOUT) -> GateResult:
    """G6: signals have their row, rs rows their id, clicks their rs row; the first rows without, by primary key."""
    schema = check_schema_name(schema_name)
    found = Findings()
    for path, table, keys, condition in _REFERENCES:
        rows = await read(conn, REFERENCES, "fetch", _orphans_sql(schema, table, keys, condition), timeout=timeout)
        found = found.merge(_orphans(path, table, keys, rows))
    return GateResult(REFERENCES, not found.total, found.as_detail() if found.total else {})


# ---------------------------------------------------------------- G8: the same as v1

# feed.ts:35 CANDIDATE over unionRows; loadSignalsFor's BASES filter and order (queries-shared.ts:180-183); the cut
# leaves room for no more than SIGNAL_CUT signals, so no more are read.
_CANDIDATES = """SELECT c.row_key, c.rs_clk, c.rs_bill, c.rs_gsc,
       ARRAY(SELECT s.kind FROM {s}.catalog_signals s
              WHERE s.row_key = c.row_key AND s.kind = ANY($1::text[]) ORDER BY s.ord, s.kind LIMIT {cut}) AS kinds
  FROM (SELECT row_key, false AS rs_clk, false AS rs_bill, false AS rs_gsc FROM {s}.catalog_rows WHERE has_signal AND off_on IS NULL
        UNION ALL
        SELECT row_key, rs_clk, rs_bill, rs_gsc FROM {s}.rs_rows WHERE has_signal AND off_on IS NULL) c"""
# loadPostedFor (queries-shared.ts:210-237): a record belongs to a row through row_keys, or to an rs row through drama_ids.
_POSTED_PAIRS = """SELECT k.row_key, p.sd FROM {s}.catalog_posted p CROSS JOIN LATERAL unnest(p.row_keys) AS k(row_key)
UNION ALL
SELECT $1 || k.id, p.sd FROM {s}.catalog_posted p CROSS JOIN LATERAL unnest(p.drama_ids) AS k(id)"""


@dataclass(frozen=True, slots=True)
class Candidate:
    """A row v1 must carry, with the signal kinds v1 gives it: BASES signals by ord, then the rs flags, the first 50."""

    row_key: str
    kinds: frozenset


def _kinds_of(row) -> frozenset:
    flags = tuple(kind for kind in RS_FLAG_KINDS if row[f"rs_{kind}"])
    return frozenset((*row["kinds"], *flags)[:SIGNAL_CUT])


async def mirror_candidates(conn, schema_name: str, bases: Sequence[str], *, timeout: float = STATEMENT_TIMEOUT) -> tuple[Candidate, ...]:
    statement = _CANDIDATES.format(s=check_schema_name(schema_name), cut=SIGNAL_CUT)
    rows = await read(conn, V1_CONSISTENCY, "fetch", statement, list(bases), timeout=timeout)
    return tuple(Candidate(row["row_key"], _kinds_of(row)) for row in rows)


async def posted_pairs(conn, schema_name: str, *, timeout: float = STATEMENT_TIMEOUT) -> tuple[tuple[str, str], ...]:
    """(row_key, sd) for every posted record and every row it belongs to."""
    rows = await read(conn, V1_CONSISTENCY, "fetch", _POSTED_PAIRS.format(s=check_schema_name(schema_name)), RS_ROW_PREFIX, timeout=timeout)
    return tuple((row["row_key"], row["sd"]) for row in rows)


def row_key_of(source_id) -> str | None:
    """The row_key a v1 source_id encodes (base64url of its UTF-8, no padding: feed-map.ts:316), or None."""
    if not isinstance(source_id, str) or not _BASE64URL.fullmatch(source_id):
        return None
    try:
        raw = base64.urlsafe_b64decode(source_id + "=" * (-len(source_id) % 4))
        row_key = raw.decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return row_key if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") == source_id else None


def _records_by_row(pairs: Iterable[tuple[str, str]], keys) -> dict[str, frozenset]:
    """Each candidate's sd set, every sd scrubbed: v1's posted.records are nested text and so scrubbed (feed-map.ts:268)."""
    wanted = sorted((row_key, sd) for row_key, sd in pairs if row_key in keys)
    return {row_key: frozenset(pan.scrub_text(sd)[0] for _, sd in group) for row_key, group in groupby(wanted, key=itemgetter(0))}


def _v1_difference(key: str | None, row: V1Row, mirror: Mapping, records: Mapping, seen: Counter) -> tuple[object, dict]:
    if key is None:
        return id_part(row.source_id), {"v1.source_id": 1}
    checks = (
        ("v1.duplicate", seen[key] > 1),
        ("rows.only_v1", key not in mirror),
        ("signals[*].kind", key in mirror and row.kinds != mirror[key]),
        ("posted.records", key in mirror and row.records != records.get(key, frozenset())),
    )
    return id_part(key), {path: 1 for path, differs in checks if differs}


def _differences(mirror: Mapping[str, frozenset], records: Mapping[str, frozenset], rows: Sequence[V1Row]) -> Iterator[tuple[object, dict]]:
    decoded = tuple((row_key_of(row.source_id), row) for row in rows)
    seen = Counter(key for key, _ in decoded if key is not None)
    yield from (_v1_difference(key, row, mirror, records, seen) for key, row in decoded)
    yield from ((id_part(key), {"rows.only_mirror": 1}) for key in sorted(mirror.keys() - seen.keys()))


def v1_consistency_gate(candidates: Sequence[Candidate], pairs: Iterable[tuple[str, str]], v1: V1Scan) -> GateResult:
    """G8: the version's candidates are v1's rows, with the same signal kinds and the same posted records (plan 2.5
    item 2). Rows are named by row_key; a source_id that decodes to none by itself. CPU-bound: run in a thread."""
    mirror = {candidate.row_key: candidate.kinds for candidate in candidates}
    records = _records_by_row(pairs, mirror.keys())
    found = reduce(lambda so_far, item: so_far.add(item[1], item[0]), _differences(mirror, records, v1.rows), Findings())
    return GateResult(V1_CONSISTENCY, not found.total, found.as_detail() if found.total else {})


# ---------------------------------------------------------------- G9: non-empty to empty

# One statement, one snapshot: the current version (the latest published_at, then id; plan 3.2) and the control row.
_EMPTY_READ = """SELECT (SELECT v.counts FROM pick_mirror.versions v WHERE v.status = 'published'
                          ORDER BY v.published_at DESC, v.id DESC LIMIT 1) AS counts,
       c.accept_empty_once, c.accept_empty_set_at
  FROM pick_mirror.control c WHERE c.id = 1"""


@dataclass(frozen=True, slots=True)
class AcceptEmpty:
    """What publish_mirror_pair takes as accept_empty_used and accept_empty_seen (P2-5a): whether G9 passed on
    accept_empty_once, and the accept_empty_set_at G9 read. The publish puts the flag back only when both still hold."""

    used: bool
    seen: datetime | None


def _stored_counts(value) -> dict[str, int]:
    counts = json.loads(value) if isinstance(value, str) else value
    return {table: n for table, n in counts.items() if is_count(n)} if isinstance(counts, Mapping) else {}


async def empty_tables_gate(conn, measured: Mapping[str, int], *, timeout: float = STATEMENT_TIMEOUT) -> tuple[GateResult, AcceptEmpty]:
    """G9: no table that had rows in the current version has none now, unless accept_empty_once lets this run through.

    Only reads: the flag goes back to false in the paired publish's transaction, and only if nobody set it again after
    this read (brief P2-5a step 5). Consequence mirror.
    """
    row = await read(conn, EMPTY_TABLES, "fetchrow", _EMPTY_READ, timeout=timeout)
    if row is None:
        raise GateError(EMPTY_TABLES, "pick_mirror.control 缺少那一行")
    current = _stored_counts(row["counts"])
    emptied = {table: {"current": current[table], "this_run": 0} for table in COUNTED_RESOURCES if current.get(table, 0) > 0 and measured.get(table) == 0}
    accepted = bool(emptied) and row["accept_empty_once"] is True
    detail = {"tables": emptied, "total": len(emptied), "accepted": accepted} if emptied else {}
    return GateResult(EMPTY_TABLES, not emptied or accepted, detail), AcceptEmpty(used=accepted, seen=row["accept_empty_set_at"])


# ---------------------------------------------------------------- the mirror gates together


@dataclass(frozen=True, slots=True)
class MirrorGates:
    """G3-G9 of one version, in plan order, with what the paired publish needs to consume an accept-empty."""

    results: tuple[GateResult, ...]
    accept_empty: AcceptEmpty
    measured: Mapping[str, int]

    def __post_init__(self):
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "measured", MappingProxyType(dict(self.measured)))

    @property
    def ok(self) -> bool:
        return all(result.ok for result in self.results)

    @property
    def first_failure(self) -> str | None:
        """The first failed gate's name, for publish_agent_only's reason degraded:<name>."""
        return next((result.name for result in self.results if not result.ok), None)

    def as_json(self) -> dict:
        return {result.name: result.as_json() for result in self.results}


async def run_mirror_gates(conn, *, schema_name: str, manifest: Mapping, v1: V1Scan, text: MirrorTextScan, timeout: float = STATEMENT_TIMEOUT) -> MirrorGates:
    """G3-G9 over a finalized version on the dedicated connection; every gate runs, so details_json shows them all.

    manifest is the manifest row as RealShort sent it (feed_shape.Manifest.row); v1 and text are the scans the run built
    page by page. A statement that fails is a GateError (a mirror-side failure); the schema name is checked first.
    """
    schema = check_schema_name(schema_name)
    bases = signal_kinds(manifest)
    counts = value_at(manifest, ("counts",))
    counts = counts if isinstance(counts, Mapping) else {}
    measured = await table_counts(conn, schema, timeout=timeout)
    forbidden = await forbidden_columns_gate(conn, schema, timeout=timeout)
    references = await references_gate(conn, schema, timeout=timeout)
    totals = await control_totals(conn, schema, timeout=timeout)
    candidates = await mirror_candidates(conn, schema, bases, timeout=timeout)
    pairs = await posted_pairs(conn, schema, timeout=timeout)
    consistency = await asyncio.to_thread(v1_consistency_gate, candidates, pairs, v1)
    empty, accept_empty = await empty_tables_gate(conn, measured, timeout=timeout)
    results = (
        row_counts_gate(measured, counts),
        forbidden,
        mirror_text_gate(text, counts),
        references,
        control_totals_gate(totals, manifest),
        consistency,
        empty,
    )
    return MirrorGates(results, accept_empty, measured)
