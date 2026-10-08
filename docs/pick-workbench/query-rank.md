# Pinned rank query helper

`ggwork_pick.query_rank.query_rank` ports the existing board rank readers onto a caller-owned asyncpg connection. The Gateway service owns authenticated access, version pinning, read-only transactions, validated search path, filter validation, bulk decoration and exception translation. This helper never opens a connection, chooses a schema, writes data, or reads the latest candidate cache.

The 21 supported ranks are `kd`, `kw`, `qc`, `qr`, `sm`, `smd`, `mg`, `fh`, `sh`, `gh`, `gn`, `ghh`, `dbn`, `rs_rr`, `rs_growth`, `rs_cand`, `rs_pc`, `rs_clk`, `rs_gsc`, `rs_bill`, and `rs_ledger`.

```python
result = await query_rank(
    conn, request,
    rules=resolved_rules,
    meta={"as_of": pinned_as_of, **pinned_meta},
    deadline=parent_absolute_deadline,
)
```

`deadline` is absolute event-loop monotonic time. All SQL shares the smaller of that deadline and the request's remaining helper budget; the caller retains the enclosing budget for pool acquisition, version resolution and response serialization. An elapsed deadline raises `TimeoutError`. Driver failures propagate to the service's error translator.

`RankQueryResult` contains ordered `row_keys`, exact `total` and `matched`, global `ranks`/`grades` facets, `actual_period`, `period_options`, and `has_more`. Typed `rank_rows` preserve the chosen signal and daily rank/note. Typed `bill_rows` preserve ledger identities, canonical mapping, fallback titles, locale, order count, source-row count and same-day clicks. `bill_totals` is aggregated over the complete ledger, independently of page size. A ledger page can contain repeated or missing canonical drama IDs, so its returned count is `len(bill_rows)`, not `len(row_keys)`.

- Theater rank membership retains off-shelf history. Daily ranks use the selected history cell; weekly ranks identify a week by its start date and choose the signal with greatest weeks, then ordinal. Grade ranks use SSS, SS, S, A, B, C, D. PostgreSQL supplies null ordering, text collation and stable identity ties.
- Period resolution reports `latest`, `exact`, `label`, `ambiguous`, or `missing`. A missing/ambiguous request visibly identifies the actual fallback period. No readable period returns `actual_period=None`; the service translates this to `period_missing`.
- ReelShort numeric increment/efficiency ordering uses PostgreSQL `numeric`, preserving decimal ties from the previous reader. Comparable growth membership uses verified `rr1`/`rr7`/`p1`/`p7`, while the sort uses raw `s1_*`/`s7_*` values. Buckets use the pinned UTC day.
- Growth has an explicit top-50 rank window. `rank_limit=50`, `legacy_total=None`, and exact `matched` retain all comparable matches. Common pages stay inside the window. The legacy UI asks for `limit=50, offset=0`; its `hasMore` is derived from `matched > returned`, whereas helper `has_more` indicates another page inside the window.
- The legacy ledger requests 200 merged rows. The common endpoint supports explicit offset/limit and returns full totals separately.
- Only rank-specific filters are applied: theater period/grade; ReelShort query/locale/bucket/sort. The service must reject unsupported common filters rather than claim they were applied. Current theater eligibility rules do not rewrite historical source facts.

Validation uses synthetic temporary PostgreSQL databases. `tests/test_query_rank.py` checks literal behavioral examples and executes `frontend/tests/unit/server/pick-board/query-rank-parity.integration.test.ts` against the same committed fixture through the original TypeScript loaders. The differential cases cover all 21 ranks, 11 ReelShort sorts, five buckets, historical/fallback periods, grade filtering, sibling IDs, accent/case ties, true pagination, more than 50 growth rows and more than 200 ledger rows. This proves local query equivalence; authenticated HTTP adapters and browser cutover have separate integration gates.
