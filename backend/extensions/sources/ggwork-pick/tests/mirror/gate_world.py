"""A small, consistent world for the gate tests (P2-4): a version's eight tables, the manifest RealShort would send for
them and the v1 pages of the same moment.

Every value is made up. The manifest's counts and control totals (COUNTS to LEDGER) are counted by hand from the rows
below, not by the code under test, so a gate that counts differently from RealShort fails the baseline. Worlds are never
changed in place: with_table, with_row, with_manifest, with_v1 and the others return new ones.
"""

import base64
from dataclasses import dataclass, replace
from types import MappingProxyType

from mirror_rows import MANIFEST, TABLES, parsed, synthetic_row, version_args

PAN = "资源 https://pan.baidu.com/s/1AbCdEf 提取码：ab12"
REPLACEMENT = "[网盘信息已移除]"
IMPORTED = "2026-09-22T03:10:05.000Z"
IMPORTED_LAST = "2026-09-22T03:10:06.500Z"
POSTED_EARLIER = "2026-09-22T03:09:00.000Z"
POSTED_LAST = "2026-09-22T03:11:00.000Z"
RULES = "# 选剧规则\n\n采集时间：2026-09-23T10:15:00.000Z\n\n只推荐带信号、未下架的剧。\n"
REF = "https://example.test/admin/pick?tab=row&row="

# (row_key, has_signal, off_on, imported_at): c-1 and c-2 are candidates; c-3 has no signal; c-4 is delisted.
CATALOG_ROWS = (("c-1", True, None, IMPORTED), ("c-2", True, None, IMPORTED_LAST), ("c-3", False, None, IMPORTED), ("c-4", True, "2026-09-01", IMPORTED))
# (row_key, kind, ord): zz is no basis RealShort knows, so v1 leaves it out (queries-shared.ts:183). c-1 has two kd
# signals, as real rows do (catalog-tables.sql:45): rankCounts counts it once (queries-rank.ts:106, count DISTINCT).
SIGNALS = (("c-1", "kd", 0), ("c-1", "kw", 1), ("c-1", "zz", 2), ("c-2", "sm", 0), ("c-4", "kd", 0), ("c-1", "kd", 3))
# (sd, row_keys, drama_ids, post_count, sched_count, views_total, metric_at, imported_at). SD-1 is posted and has more
# scheduled, SD-4 is posted and has none: each STATE_WHERE's post_count = 0 (queries-posted.ts:140-145) matters.
POSTED = (
    ("SD-1", ["c-1"], [], 2, 1, 100, "2026-09-20", POSTED_LAST),
    ("SD-2", [], ["d-1"], 0, 1, 0, None, POSTED_EARLIER),
    ("SD-3", [], [], 0, 0, 7, "2026-09-18", POSTED_EARLIER),
    ("SD-4", [], [], 1, 0, 3, "2026-09-19", POSTED_EARLIER),
)
# drama_id -> has_signal, (rs_clk, rs_bill, rs_gsc), clicks7, bill_orders, search_impressions, promoters_cnt, rr1, p1, rr7, p7.
# has_signal is candidateFilter (queries-reelshort.ts:77). Each of loadRsCounts' filters (observe/queries.ts:533-570)
# counts differently from its neighbours: d-3 and d-4 have promoters_cnt 0, d-4 is a candidate by its bill alone, d-2 has p1 and
# no rr1, and clicks7, bill_orders and search_impressions are set on different rows.
RS_ROWS = {
    "d-1": (True, (True, True, True), 3, 4, 5, 2, 1.5, 2, 3.0, None),
    "d-2": (True, (True, False, False), 1, 0, 0, 1, None, 1, 2.0, None),
    "d-3": (False, (False, False, False), 0, 0, 0, 0, None, None, 1.0, None),
    "d-4": (True, (False, True, False), 0, 7, 0, 0, None, None, None, None),
    "d-5": (True, (True, False, False), 2, 0, 0, 4, None, None, None, None),
}
# rs_ids is every dramas row (export-v2.ts:430), so also d-6, a sibling that is no canonical row.
RS_IDS = (*RS_ROWS, "d-6")
# (bill_date, book_id, promotion_type, order_cnt, source_rows): book-x is in no rs_ids row, which RealShort allows.
BILLS = (("2026-09-20", "d-1", "cps", 5, 2), ("2026-09-21", "book-x", "cps", 1, 1))

# Counted by hand from the rows above.
COUNTS = {"catalog_rows": 4, "catalog_signals": 6, "catalog_posted": 4, "catalog_accounts": 2, "rs_rows": 5, "rs_ids": 6, "rs_clicks14": 2, "rs_bill_orders": 2}
FRESHNESS = {
    "importedAt": IMPORTED_LAST,
    "rows": 4,
    "withSignal": 3,
    "signals": 6,
    "posted": 4,
    "rsCanonical": 5,
    "rsCandidates": 4,
    "rsSyncedAt": "2026-09-23T09:00:00.000Z",
}
RS_COUNTS = {"all": 5, "cand": 4, "growthD1": 1, "growthD7": 3, "growthDp1": 2, "growthDp7": 0, "pc": 3, "clk": 3, "gsc": 1, "bill": 2, "ledger": 3}
RANK_COUNTS = {"kd": 2, "kw": 1, "sm": 1, "rs_rr": 5, "rs_growth": 3, "rs_cand": 4, "rs_pc": 3, "rs_clk": 3, "rs_gsc": 1, "rs_bill": 2, "rs_ledger": 3}
POSTED_STATS = {"total": 4, "pubCount": 2, "postsSum": 3, "viewsSum": 110, "metricAt": "2026-09-20", "importedAt": POSTED_LAST, "accountCount": 2}
POSTED_STATES = {"pub": 2, "sched": 1, "none": 1, "nomatch": 2}
LEDGER = {"rows": 3, "orders": 6}
# What the v1 feed says of each candidate: its signal kinds in order and its posted records.
V1_CANDIDATES = {
    "c-1": (("kd", "kw", "kd"), ("SD-1",)),
    "c-2": (("sm",), ()),
    "reelshort-d-1": (("clk", "bill", "gsc"), ("SD-2",)),
    "reelshort-d-2": (("clk",), ()),
    "reelshort-d-4": (("bill",), ()),
    "reelshort-d-5": (("clk",), ()),
}


def b64url(row_key: str) -> str:
    """source_id as RealShort writes it: Buffer.from(rowKey).toString("base64url"), no padding (feed-map.ts:316)."""
    return base64.urlsafe_b64encode(row_key.encode("utf-8")).rstrip(b"=").decode("ascii")


def replaced(value: dict, path: tuple, new) -> dict:
    """A copy of value with the item at path set to new; value itself is left as it was."""
    head, *rest = path
    return {**value, head: replaced(value[head], tuple(rest), new) if rest else new}


def removed(value: dict, path: tuple) -> dict:
    head, *rest = path
    if rest:
        return {**value, head: removed(value[head], tuple(rest))}
    return {key: item for key, item in value.items() if key != head}


def _tables() -> dict:
    return {
        "catalog_rows": tuple(
            synthetic_row("catalog_rows", n, row_key=key, has_signal=signal, off_on=off, imported_at=at)
            for n, (key, signal, off, at) in enumerate(CATALOG_ROWS, 1)
        ),
        "catalog_signals": tuple(synthetic_row("catalog_signals", n, row_key=key, kind=kind, ord=o) for n, (key, kind, o) in enumerate(SIGNALS, 1)),
        "catalog_posted": tuple(posted_row(n, *spec) for n, spec in enumerate(POSTED, 1)),
        "catalog_accounts": tuple(synthetic_row("catalog_accounts", n, id=f"a-{n}") for n in (1, 2)),
        "rs_rows": tuple(rs_row(n, drama_id, *spec) for n, (drama_id, spec) in enumerate(RS_ROWS.items(), 1)),
        "rs_ids": tuple(synthetic_row("rs_ids", n, id=drama_id) for n, drama_id in enumerate(RS_IDS, 1)),
        "rs_clicks14": (synthetic_row("rs_clicks14", 1, drama_id="d-1", day="2026-09-20"), synthetic_row("rs_clicks14", 2, drama_id="d-2", day="2026-09-21")),
        "rs_bill_orders": tuple(bill_row(n, *spec) for n, spec in enumerate(BILLS, 1)),
    }


def posted_row(n: int, sd: str, row_keys, drama_ids, post_count: int, sched_count: int, views: int, metric_at, imported_at: str) -> dict:
    return synthetic_row(
        "catalog_posted",
        n,
        sd=sd,
        row_keys=list(row_keys),
        drama_ids=list(drama_ids),
        post_count=post_count,
        sched_count=sched_count,
        views_total=views,
        metric_at=metric_at,
        imported_at=imported_at,
    )


def rs_row(n: int, drama_id: str, signal: bool, flags, clicks: int, bills: int, impressions: int, promoters: int, rr1, p1, rr7, p7) -> dict:
    clk, bill, gsc = flags
    return synthetic_row(
        "rs_rows",
        n,
        nulls=True,
        row_key=f"reelshort-{drama_id}",
        drama_id=drama_id,
        has_signal=signal,
        rs_clk=clk,
        rs_bill=bill,
        rs_gsc=gsc,
        clicks7=clicks,
        bill_orders=bills,
        search_impressions=impressions,
        promoters_cnt=promoters,
        rr1=rr1,
        p1=p1,
        rr7=rr7,
        p7=p7,
    )


def bill_row(n: int, bill_date: str, book_id: str, promotion_type: str, orders: int, source_rows: int) -> dict:
    return synthetic_row("rs_bill_orders", n, bill_date=bill_date, book_id=book_id, promotion_type=promotion_type, order_cnt=orders, source_rows=source_rows)


def signal_row(n: int, row_key: str, kind: str, ord_: int, **overrides) -> dict:
    return synthetic_row("catalog_signals", n, row_key=row_key, kind=kind, ord=ord_, **overrides)


def v1_row(row_key: str, kinds=(), records=(), **overrides) -> dict:
    """One raw v1 row as toFeedRow shapes it (feed-map.ts:303-329), for a candidate with these kinds and records."""
    signals = [
        {"kind": kind, "label": f"{kind} 的名字", "source_ref": REF + row_key, "observed_at": None, "rank": None, "grade": "", "note": ""} for kind in kinds
    ]
    posted = {"matched": bool(records), "records": list(records), "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []}
    row = {
        "source": "realshort-pick",
        "source_id": b64url(row_key),
        "language": "英语",
        "title": f"{row_key} 的剧名",
        "theater": "ShortMax",
        "tags": ["甜宠"],
        "listed_at": None,
        "availability": "unknown",
        "signals": signals,
        "channel_rules": {"youtube": "unknown"},
        "detail_url": REF + row_key,
        "posted": posted,
    }
    return {**row, **overrides}


def _manifest() -> dict:
    meta = MANIFEST["meta"]
    control = {**meta["control"], "rankCounts": RANK_COUNTS, "postedStats": POSTED_STATS, "postedStates": POSTED_STATES, "ledger": LEDGER}
    return {**MANIFEST, "counts": COUNTS, "meta": {**meta, "freshness": FRESHNESS, "rsCounts": RS_COUNTS, "control": control}}


@dataclass(frozen=True)
class World:
    tables: MappingProxyType
    manifest: dict
    v1_rows: tuple

    def v1_pages(self, *, per_page: int = 3, rules: str | None = RULES) -> tuple[dict, ...]:
        """The v1 pull as page bodies: rules and the total on the first page only, like feed.ts:56-72."""
        chunks = [self.v1_rows[start : start + per_page] for start in range(0, max(len(self.v1_rows), 1), per_page)]
        first = {"total": len(self.v1_rows), "rules": rules, "scope": "全部候选", "freshness": {}}
        return tuple(
            {"ok": True, "version": "pick-feed-v1", "rows": list(chunk), "nextCursor": None, **(first if index == 0 else {})}
            for index, chunk in enumerate(chunks)
        )


def baseline() -> World:
    rows = tuple(v1_row(key, kinds, records) for key, (kinds, records) in V1_CANDIDATES.items())
    return World(tables=MappingProxyType(_tables()), manifest=_manifest(), v1_rows=rows)


def with_table(world: World, table: str, rows) -> World:
    return replace(world, tables=MappingProxyType({**world.tables, table: tuple(rows)}))


def with_row(world: World, table: str, index: int, **changes) -> World:
    rows = [row if n != index else {**row, **changes} for n, row in enumerate(world.tables[table])]
    return with_table(world, table, rows)


def with_manifest(world: World, path: tuple, value) -> World:
    return replace(world, manifest=replaced(world.manifest, path, value))


def without_manifest_key(world: World, path: tuple) -> World:
    return replace(world, manifest=removed(world.manifest, path))


def with_v1(world: World, rows) -> World:
    return replace(world, v1_rows=tuple(rows))


def with_v1_row(world: World, index: int, **changes) -> World:
    return with_v1(world, [row if n != index else {**row, **changes} for n, row in enumerate(world.v1_rows)])


def with_counts(world: World, **counts) -> World:
    """The manifest's counts and the version's size together: create_version records the manifest's counts."""
    return with_manifest(world, ("counts",), {**world.manifest["counts"], **counts})


def empty_posted(world: World) -> World:
    """The world with no posted records at all, and a manifest that says so as RealShort would (sums 0, maxima null)."""
    stats = {"total": 0, "pubCount": 0, "postsSum": 0, "viewsSum": 0, "metricAt": None, "importedAt": None, "accountCount": 2}
    world = with_counts(with_table(world, "catalog_posted", ()), catalog_posted=0)
    world = with_manifest(world, ("meta", "freshness", "posted"), 0)
    world = with_manifest(world, ("meta", "control", "postedStats"), stats)
    world = with_manifest(world, ("meta", "control", "postedStates"), {"pub": 0, "sched": 0, "none": 0, "nomatch": 0})
    return with_v1(world, [v1_row(key, kinds, ()) for key, (kinds, _) in V1_CANDIDATES.items()])


async def build(conn, world: World):
    """The world's version, written and finalized the way a run builds one (P2-3)."""
    from ggwork_pick.mirror.versions import create_version
    from ggwork_pick.mirror.writer import copy_rows, finalize_version, write_meta

    version = await create_version(conn, **version_args(counts=world.manifest["counts"]))
    for table in TABLES:
        await copy_rows(conn, version.schema_name, table, parsed(table, list(world.tables[table])))
    await write_meta(conn, version.schema_name, world.manifest)
    await finalize_version(conn, version.schema_name)
    return version


def v1_scan(world: World, **pages):
    from ggwork_pick.mirror.gates import V1Scan, scan_v1_page

    scan = V1Scan()
    for body in world.v1_pages(**pages):
        scan = scan_v1_page(scan, body)
    return scan


def text_scan(world: World):
    from ggwork_pick.mirror.gates import MirrorTextScan, scan_mirror_meta, scan_mirror_page

    scan = scan_mirror_meta(MirrorTextScan(), world.manifest["meta"])
    for table in TABLES:
        scan = scan_mirror_page(scan, table, list(world.tables[table]))
    return scan
