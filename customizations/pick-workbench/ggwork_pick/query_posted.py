"""Posted-ledger rows keep publication, schedule and unmatched states distinct."""

from ggwork_pick.query_service import wire

STATES = {
    "pub": "post_count > 0",
    "sched": "post_count = 0 AND sched_count > 0",
    "none": "post_count = 0 AND sched_count = 0",
    "nomatch": "cardinality(row_keys) = 0 AND cardinality(drama_ids) = 0",
}


async def posted_page(conn, req, rules):
    args, clauses = [], []

    def bind(value):
        args.append(value)
        return f"${len(args)}"

    if req.query:
        value = bind("%" + req.query + "%")
        fields = ["title", "sd", "why", "note", "platform", "lang", "life", *[f"array_to_string({k},' ')" for k in ("sources", "accounts", "cats", "who")]]
        clauses.append("(" + " OR ".join(f"{field} ILIKE {value}" for field in fields) + ")")
    if req.account:
        clauses.append(f"{bind(req.account)} = ANY(accounts)")
    if req.language is not None:
        clauses.append(f"lang = {bind(req.language)}")
    if req.theater:
        clauses.append(f"platform = {bind(req.theater)}")
    if req.published_from:
        clauses.append(f"last_post_on >= {bind(req.published_from)}")
    if req.published_to:
        clauses.append(f"first_post_on <= {bind(req.published_to)}")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    facets = {key: await conn.fetchval(f"SELECT count(*) FILTER(WHERE {state}) FROM catalog_posted{where}", *args) for key, state in STATES.items()}
    total = await conn.fetchval("SELECT count(*) FROM catalog_posted")
    matched = facets[req.posted_state] if req.posted_state else await conn.fetchval(f"SELECT count(*) FROM catalog_posted{where}", *args)
    if req.posted_state:
        where += (" AND " if clauses else " WHERE ") + STATES[req.posted_state]
    rows = [
        wire(dict(r))
        for r in await conn.fetch(
            f"SELECT * FROM catalog_posted{where} ORDER BY last_post_on DESC NULLS LAST,created_on DESC NULLS LAST,sd "
            f"LIMIT ${len(args) + 1} OFFSET ${len(args) + 2}",
            *args,
            req.limit,
            req.offset,
        )
    ]
    keys = list(dict.fromkeys(k for r in rows for k in r["row_keys"]))
    ids = list(dict.fromkeys(k for r in rows for k in r["drama_ids"]))
    board = {
        "row_keys": [r["sd"] for r in rows],
        "posted": rows,
        "rules": rules,
        "catalog_rows": [wire(dict(r)) for r in await conn.fetch("SELECT * FROM catalog_rows WHERE row_key=ANY($1::text[]) ORDER BY row_key", keys)],
        "rs_ids": [wire(dict(r)) for r in await conn.fetch("SELECT * FROM rs_ids WHERE id=ANY($1::text[]) ORDER BY id", ids)],
        "accounts": [wire(dict(r)) for r in await conn.fetch("SELECT * FROM catalog_accounts ORDER BY grp,name,id")],
    }
    return board, total, matched, {"posted_states": facets}


def publication_truth(records, req):
    """Missing joins/detail fields are unknown; archived public posts remain public."""
    if not records or req.channel:
        return "unknown", False
    if not req.account and not req.published_from and not req.published_to:
        return ("posted" if any(r["post_count"] > 0 for r in records) else "not_posted"), True
    complete, found = True, False
    for record in records:
        posts = record["posts"]
        published = [p for p in posts if p.get("st") in {"已回填", "已公开"}]
        if len(published) != record["post_count"] or len(posts) != record["post_count"] + record["sched_count"]:
            complete = False
        for post in published:
            if req.account and not post.get("acct"):
                complete = False
                continue
            if req.account and post["acct"] != req.account:
                continue
            if (req.published_from or req.published_to) and not post.get("d"):
                complete = False
                continue
            if req.published_from and post["d"] < req.published_from:
                continue
            if req.published_to and post["d"] > req.published_to:
                continue
            found = True
    return ("posted" if found else "not_posted" if complete else "unknown"), complete
