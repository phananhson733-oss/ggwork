"""Mirror catalog SQL. Facets and pages share predicates; each facet drops its own dimension."""

from ggwork_pick.mirror.contracts import RESOURCE_COLUMNS

ROW_COLUMNS = ", ".join(c.name for c in RESOURCE_COLUMNS["catalog_rows"] if c.name != "imported_at")
UNION = f"""(SELECT {ROW_COLUMNS}, false AS rs_clk, false AS rs_bill, false AS rs_gsc,
 NULL::text AS drama_id FROM catalog_rows UNION ALL
 SELECT {ROW_COLUMNS}, rs_clk, rs_bill, rs_gsc, drama_id FROM rs_rows) rows"""
SOURCE_ID = "translate(rtrim(replace(encode(convert_to(rows.row_key,'UTF8'),'base64'), chr(10), ''),'='),'+/','-_')"
POSTED_KEYS = """SELECT k.row_key, bool_or(k.post_count > 0) AS published FROM (
 SELECT unnest(p.row_keys) AS row_key, p.post_count FROM catalog_posted p UNION ALL
 SELECT 'reelshort-' || unnest(p.drama_ids), p.post_count FROM catalog_posted p
 ) k WHERE k.row_key IS NOT NULL GROUP BY k.row_key"""
POSTED = {
    "pool": f"rows.row_key IN (SELECT pk.row_key FROM ({POSTED_KEYS}) pk)",
    "yes": f"rows.row_key IN (SELECT pk.row_key FROM ({POSTED_KEYS}) pk WHERE pk.published)",
    "no": f"rows.row_key NOT IN (SELECT pk.row_key FROM ({POSTED_KEYS}) pk WHERE pk.published)",
}
ORDER = {
    "listed": "rows.listed_on DESC NULLS LAST, rows.title ASC, rows.row_key ASC",
    "title": "rows.title ASC, rows.platform ASC, rows.row_key ASC",
    "evidence": "rows.latest_evidence_on DESC NULLS LAST, rows.listed_on DESC NULLS LAST, rows.title ASC, rows.platform ASC, rows.row_key ASC",
}


def predicates(req, rules, *, canonical, skip=None, excluded_keys=()):
    args, clauses = [], []

    def bind(value):
        args.append(value)
        return f"${len(args)}"

    if (req.scope == "candidate_pool" and not req.wide) or req.signal_only:
        clauses.append("rows.has_signal")
    if not req.with_off:
        clauses.append("rows.off_on IS NULL")
        if canonical:
            clauses.append(f"rows.row_key <> ALL({bind(canonical['delisted'])}::text[])")
    if skip != "platform":
        if req.theater:
            clauses.append(f"rows.platform = {bind(req.theater)}")
        elif req.in_use_only:
            clauses.append(f"rows.platform = ANY({bind(rules['inUse'])}::text[])")
    if req.language is not None and skip != "language":
        clauses.append(f"rows.lang = {bind(req.language)}")
    if req.source:
        clauses.append(f"rows.row_key = ANY({bind(canonical['sources'].get(req.source, []))}::text[])")
    if req.source_id:
        p = bind(req.source_id)
        clauses.append(f"(rows.row_key = {p} OR rows.drama_id = {p} OR {SOURCE_ID} = {p})")
    if req.query:
        like, exact = bind("%" + req.query + "%"), bind(req.query)
        clauses.append(f"(rows.title ILIKE {like} OR rows.title_cn ILIKE {like} OR rows.row_key = {exact} OR rows.drama_id = {exact} OR {SOURCE_ID} = {exact})")
    if req.signal_kind and skip != "basis":
        if req.signal_kind in {"clk", "bill", "gsc"}:
            clauses.append(f"rows.rs_{req.signal_kind}")
        else:
            clauses.append(f"EXISTS (SELECT 1 FROM catalog_signals s WHERE s.row_key=rows.row_key AND s.kind={bind(req.signal_kind)})")
    if req.hot_only:
        clauses.append(
            "EXISTS (SELECT 1 FROM catalog_signals s WHERE s.row_key=rows.row_key AND s.kind=ANY("
            "ARRAY['kd','kw','qc','qr','sm','smd','mg','fh','sh','gh','gn','ghh','dbn']))"
        )
    if req.posted_filter and skip != "posted":
        clauses.append(POSTED[req.posted_filter])
    if req.exclude_posted:
        if req.account or req.published_from or req.published_to:
            scope = ["e->>'st' IN ('已回填','已公开')"]
            if req.account:
                scope.append(f"e->>'acct' = {bind(req.account)}")
            if req.published_from:
                scope.append(f"e->>'d' >= {bind(req.published_from)}")
            if req.published_to:
                scope.append(f"e->>'d' <= {bind(req.published_to)}")
            scope_sql = " AND ".join(scope)
            clauses.append(
                f"rows.row_key NOT IN (SELECT unnest(p.row_keys) FROM catalog_posted p WHERE EXISTS "
                f"(SELECT 1 FROM jsonb_array_elements(p.posts) e WHERE {scope_sql}) UNION ALL "
                f"SELECT 'reelshort-' || unnest(p.drama_ids) FROM catalog_posted p WHERE EXISTS "
                f"(SELECT 1 FROM jsonb_array_elements(p.posts) e WHERE {scope_sql}))"
            )
        else:
            clauses.append(POSTED["no"])
    if req.youtube_ok:
        clauses.append(
            f"(rows.platform <> ALL({bind(rules['ytBlocked'])}::text[]) AND (rows.platform <> ALL({bind(rules['ytListOnly'])}::text[]) OR rows.youtube))"
        )
    if req.channel == "youtube":
        clauses.append(f"rows.platform <> ALL({bind(rules['ytBlocked'])}::text[])")
        if req.confirmed_eligible_only:
            clauses.append("rows.off_on IS NULL")
            clauses.append(f"rows.row_key = ANY({bind(canonical['eligible'])}::text[])")
        clauses.append(f"rows.row_key <> ALL({bind(canonical['denied'])}::text[])")
    if req.dated_only:
        clauses.append("rows.latest_evidence_on IS NOT NULL")
    if excluded_keys:
        clauses.append(f"rows.row_key <> ALL({bind(list(excluded_keys))}::text[])")
    for tag in req.tags:
        clauses.append(f"{bind(tag)} = ANY(regexp_split_to_array(rows.tags, '[,，、;；|/\\s]+'))")
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), args


async def catalog_page(conn, req, rules, *, canonical, excluded_keys=()):
    where, args = predicates(req, rules, excluded_keys=excluded_keys, canonical=canonical)
    total_req = req.model_copy(
        update={
            "query": None,
            "source": None,
            "source_id": None,
            "language": None,
            "theater": None,
            "channel": None,
            "tags": [],
            "signal_kind": None,
            "posted_filter": "",
            "exclude_posted": False,
            "youtube_ok": False,
            "dated_only": False,
            "in_use_only": False,
            "hot_only": False,
        }
    )
    universe, ua = predicates(total_req, rules, canonical=canonical)
    total = await conn.fetchval(f"SELECT count(*) FROM {UNION}{universe}", *ua)
    matched = await conn.fetchval(f"SELECT count(*) FROM {UNION}{where}", *args)
    sort = ORDER.get(req.order, ORDER["evidence"])
    page = await conn.fetch(
        f"SELECT rows.row_key FROM {UNION}{where} ORDER BY {sort} LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}", *args, req.limit, req.offset
    )
    facets = {}
    for dimension, column, name in [("platform", "platform", "platforms"), ("language", "lang", "languages")]:
        fw, fa = predicates(req, rules, skip=dimension, excluded_keys=excluded_keys, canonical=canonical)
        found = await conn.fetch(f"SELECT rows.{column} AS k,count(*)::int n FROM {UNION}{fw} GROUP BY rows.{column} ORDER BY n DESC,k ASC", *fa)
        facets[name] = {r["k"]: r["n"] for r in found}
        if dimension == "language":
            facets["language_order"] = [r["k"] for r in found]
    fw, fa = predicates(req, rules, skip="basis", excluded_keys=excluded_keys, canonical=canonical)
    bases = await conn.fetch(
        f"SELECT s.kind AS k,count(DISTINCT s.row_key)::int n FROM catalog_signals s JOIN {UNION} ON rows.row_key=s.row_key{fw} GROUP BY s.kind", *fa
    )
    facets["bases"] = {r["k"]: r["n"] for r in bases}
    for kind in ("clk", "bill", "gsc"):
        facets["bases"][kind] = await conn.fetchval(f"SELECT count(*) FILTER (WHERE rows.rs_{kind}) FROM {UNION}{fw}", *fa)
    fw, fa = predicates(req, rules, skip="posted", excluded_keys=excluded_keys, canonical=canonical)
    facets["posted"] = {kind: await conn.fetchval(f"SELECT count(*) FILTER (WHERE {clause}) FROM {UNION}{fw}", *fa) for kind, clause in POSTED.items()}
    return [r["row_key"] for r in page], total, matched, facets
