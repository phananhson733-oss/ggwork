-- The tables of one mirror version, pickm_vNNNNNN (plan 3.3; implementation note P2-3).
--
-- Columns, their order and nullability are RESOURCE_COLUMNS in contracts.py (RealShort 816ca2e, export-v2-map.ts
-- RESOURCE_SPECS); tests/mirror/test_mirror_ddl.py fails when the two differ. Types: text and day are text (days stay
-- YYYY-MM-DD text, as in RealShort), ts is timestamptz, bool boolean, int integer and float double precision (U3),
-- text[] text[], json jsonb. meta holds the manifest's keys verbatim (U2).
--
-- Only CREATE TABLE, and nothing else: versions.create_version runs this file in one transaction right after CREATE
-- SCHEMA, with __SCHEMA__ replaced by a name already checked against ^pickm_v[0-9]{6}$. Primary keys and indexes come
-- after COPY, in writer.finalize_version (U4).

CREATE TABLE __SCHEMA__.catalog_rows (
    row_key text NOT NULL,
    platform text NOT NULL,
    source_table text NOT NULL,
    title text NOT NULL,
    title_cn text NOT NULL,
    lang text NOT NULL,
    kind text NOT NULL,
    origin text NOT NULL,
    tags text NOT NULL,
    listed_on text,
    episodes integer,
    pay_start integer,
    youtube boolean NOT NULL,
    merged_rows integer NOT NULL,
    off_on text,
    reoff_note text NOT NULL,
    title_key text NOT NULL,
    in_site_ids text[] NOT NULL,
    legacy_only boolean NOT NULL,
    site_other boolean NOT NULL,
    has_signal boolean NOT NULL,
    latest_evidence_on text,
    imported_at timestamptz NOT NULL,
    has_pan boolean NOT NULL
);

CREATE TABLE __SCHEMA__.catalog_signals (
    row_key text NOT NULL,
    kind text NOT NULL,
    ord integer NOT NULL,
    evidence_on text,
    rank integer,
    grade text NOT NULL,
    note text NOT NULL,
    payload jsonb NOT NULL
);

CREATE TABLE __SCHEMA__.catalog_posted (
    sd text NOT NULL,
    feishu_record text NOT NULL,
    title text NOT NULL,
    title_key text NOT NULL,
    lang text NOT NULL,
    platform text NOT NULL,
    life text NOT NULL,
    scheduled boolean NOT NULL,
    online_on text,
    why text NOT NULL,
    note text NOT NULL,
    archived boolean NOT NULL,
    post_count integer NOT NULL,
    last_post_on text,
    views_total integer NOT NULL,
    sources text[] NOT NULL,
    cats text[] NOT NULL,
    who text[] NOT NULL,
    accounts text[] NOT NULL,
    created_on text,
    updated_on text,
    first_post_on text,
    metric_at text,
    sched_count integer NOT NULL,
    views_count integer NOT NULL,
    posts jsonb NOT NULL,
    row_keys text[] NOT NULL,
    drama_ids text[] NOT NULL,
    imported_at timestamptz NOT NULL
);

CREATE TABLE __SCHEMA__.catalog_accounts (
    id text NOT NULL,
    name text NOT NULL,
    url text NOT NULL,
    grp text NOT NULL,
    form text NOT NULL,
    niche text NOT NULL,
    status text NOT NULL,
    fans integer,
    as_of text,
    imported_at timestamptz NOT NULL
);

CREATE TABLE __SCHEMA__.rs_rows (
    row_key text NOT NULL,
    platform text NOT NULL,
    source_table text NOT NULL,
    title text NOT NULL,
    title_cn text NOT NULL,
    lang text NOT NULL,
    kind text NOT NULL,
    origin text NOT NULL,
    tags text NOT NULL,
    listed_on text,
    episodes integer,
    pay_start integer,
    youtube boolean NOT NULL,
    merged_rows integer NOT NULL,
    off_on text,
    reoff_note text NOT NULL,
    title_key text NOT NULL,
    in_site_ids text[] NOT NULL,
    legacy_only boolean NOT NULL,
    site_other boolean NOT NULL,
    has_signal boolean NOT NULL,
    latest_evidence_on text,
    has_pan boolean NOT NULL,
    rs_clk boolean NOT NULL,
    rs_bill boolean NOT NULL,
    rs_gsc boolean NOT NULL,
    rs_clk_on text,
    rs_bill_on text,
    rs_gsc_on text,
    drama_id text NOT NULL,
    locale text NOT NULL,
    slug text NOT NULL,
    publish_at timestamptz,
    chapter_count integer NOT NULL,
    pay_start_raw integer NOT NULL,
    rr double precision NOT NULL,
    promoters_cnt integer NOT NULL,
    metrics_valid boolean,
    synced_at timestamptz,
    search_impressions integer NOT NULL,
    search_data_at timestamptz,
    detail_synced_at timestamptz,
    tag_list text[] NOT NULL,
    description text NOT NULL,
    baseline1_at timestamptz,
    baseline7_at timestamptz,
    baseline15_at timestamptz,
    rr1 double precision,
    p1 integer,
    rr7 double precision,
    p7 integer,
    rr15 double precision,
    p15 integer,
    s1_rr double precision,
    s1_p integer,
    s7_rr double precision,
    s7_p integer,
    clicks7 integer NOT NULL,
    last_click_on text,
    bill_orders integer NOT NULL,
    last_bill_on text,
    bill_rank integer
);

CREATE TABLE __SCHEMA__.rs_ids (
    id text NOT NULL,
    canonical_id text,
    locale text NOT NULL,
    slug text NOT NULL,
    title text NOT NULL,
    chapter_count integer NOT NULL,
    pay_start integer NOT NULL,
    is_public_canonical boolean NOT NULL
);

CREATE TABLE __SCHEMA__.rs_clicks14 (
    drama_id text NOT NULL,
    day text NOT NULL,
    human integer NOT NULL,
    bot integer NOT NULL
);

CREATE TABLE __SCHEMA__.rs_bill_orders (
    bill_date text NOT NULL,
    book_id text NOT NULL,
    promotion_type text NOT NULL,
    canonical_id text,
    book_title text NOT NULL,
    order_cnt integer NOT NULL,
    source_rows integer NOT NULL,
    same_day_clicks integer NOT NULL
);

CREATE TABLE __SCHEMA__.meta (
    key text NOT NULL,
    value jsonb NOT NULL
);
