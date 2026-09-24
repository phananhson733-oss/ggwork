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
thread.

This module is the entry point: GateResult, Findings, GateError and the gate names come from gate_result.py, and all of
them are importable from here.
"""

import asyncio
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from functools import reduce
from types import MappingProxyType

from ggwork_pick.contracts import UNSTORABLE_TEXT
from ggwork_pick.mirror import pan
from ggwork_pick.mirror.contracts import COUNTED_RESOURCES, RESOURCE_KEYS, ROW_RESOURCES
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
    shown_key,
)
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
    "STATEMENT_TIMEOUT",
    "V1_CONSISTENCY",
    "V1_TEXT",
    "Findings",
    "GateError",
    "GateResult",
    "MirrorTextScan",
    "V1Row",
    "V1Scan",
    "mirror_text_gate",
    "scan_mirror_meta",
    "scan_mirror_page",
    "scan_v1_page",
    "scanned_mirror_page",
    "scanned_v1_page",
    "v1_row_id",
    "v1_text_gate",
    "v2_row_id",
]

V1_ROWS_AT = "v1.rows[*]"

_NUL = re.compile("\x00")
_SURROGATE = re.compile(f"[{chr(0xD800)}-{chr(0xDFFF)}]")


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
