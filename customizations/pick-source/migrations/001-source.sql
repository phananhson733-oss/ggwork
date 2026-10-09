--
-- PostgreSQL database dump
--


-- Dumped from database version 17.10 (Homebrew)
-- Dumped by pg_dump version 18.3


--
-- Name: pick_source; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA pick_source;




--
-- Name: catalog_accounts; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.catalog_accounts (
    id text NOT NULL,
    name text DEFAULT ''::text NOT NULL,
    url text DEFAULT ''::text NOT NULL,
    grp text DEFAULT ''::text NOT NULL,
    form text DEFAULT ''::text NOT NULL,
    niche text DEFAULT ''::text NOT NULL,
    status text DEFAULT ''::text NOT NULL,
    fans integer,
    as_of text,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalog_posted; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.catalog_posted (
    sd text NOT NULL,
    feishu_record text DEFAULT ''::text NOT NULL,
    title text NOT NULL,
    title_key text DEFAULT ''::text NOT NULL,
    lang text DEFAULT ''::text NOT NULL,
    platform text DEFAULT ''::text NOT NULL,
    life text DEFAULT ''::text NOT NULL,
    scheduled boolean DEFAULT false NOT NULL,
    online_on text,
    why text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    post_count integer DEFAULT 0 NOT NULL,
    last_post_on text,
    views_total integer DEFAULT 0 NOT NULL,
    posts jsonb DEFAULT '[]'::jsonb NOT NULL,
    row_keys text[] DEFAULT '{}'::text[] NOT NULL,
    drama_ids text[] DEFAULT '{}'::text[] NOT NULL,
    imported_at timestamp with time zone DEFAULT now() NOT NULL,
    sources text[] DEFAULT '{}'::text[] NOT NULL,
    cats text[] DEFAULT '{}'::text[] NOT NULL,
    who text[] DEFAULT '{}'::text[] NOT NULL,
    accounts text[] DEFAULT '{}'::text[] NOT NULL,
    created_on text,
    updated_on text,
    first_post_on text,
    metric_at text,
    sched_count integer DEFAULT 0 NOT NULL,
    views_count integer DEFAULT 0 NOT NULL
);


--
-- Name: catalog_rows; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.catalog_rows (
    row_key text NOT NULL,
    platform text NOT NULL,
    source_table text DEFAULT ''::text NOT NULL,
    title text NOT NULL,
    title_cn text DEFAULT ''::text NOT NULL,
    creator text DEFAULT ''::text NOT NULL,
    lang text DEFAULT ''::text NOT NULL,
    kind text DEFAULT ''::text NOT NULL,
    origin text DEFAULT ''::text NOT NULL,
    tags text DEFAULT ''::text NOT NULL,
    listed_on text,
    pan_url text DEFAULT ''::text NOT NULL,
    pan_pw text DEFAULT ''::text NOT NULL,
    episodes integer,
    pay_start integer,
    youtube boolean DEFAULT false NOT NULL,
    merged_rows integer DEFAULT 1 NOT NULL,
    off_on text,
    reoff_note text DEFAULT ''::text NOT NULL,
    title_key text DEFAULT ''::text NOT NULL,
    in_site_ids text[] DEFAULT '{}'::text[] NOT NULL,
    legacy_only boolean DEFAULT false NOT NULL,
    site_other boolean DEFAULT false NOT NULL,
    has_signal boolean DEFAULT false NOT NULL,
    latest_evidence_on text,
    imported_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: catalog_signals; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.catalog_signals (
    row_key text NOT NULL,
    kind text NOT NULL,
    ord integer DEFAULT 0 NOT NULL,
    evidence_on text,
    rank integer,
    grade text DEFAULT ''::text NOT NULL,
    note text DEFAULT ''::text NOT NULL,
    payload jsonb DEFAULT '{}'::jsonb NOT NULL
);


--
-- Name: chapters; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.chapters (
    id text NOT NULL,
    drama_id text NOT NULL,
    serial integer NOT NULL,
    video_pic text DEFAULT ''::text NOT NULL,
    iframe_src text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: cps_bill_daily; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.cps_bill_daily (
    bill_date text NOT NULL,
    book_id text NOT NULL,
    promotion_type text DEFAULT ''::text NOT NULL,
    promotion_value text DEFAULT ''::text NOT NULL,
    book_title text DEFAULT ''::text NOT NULL,
    order_cnt integer DEFAULT 0 NOT NULL,
    revenue_usd numeric(14,4) DEFAULT 0 NOT NULL,
    fetched_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: drama_observations; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.drama_observations (
    observed_on text NOT NULL,
    drama_id text NOT NULL,
    recent_revenue_cents numeric(14,2) DEFAULT 0 NOT NULL,
    promoters_cnt integer DEFAULT 0 NOT NULL,
    chapter_count integer DEFAULT 0 NOT NULL,
    pay_start integer DEFAULT 0 NOT NULL,
    captured_at timestamp with time zone DEFAULT now(),
    metrics_valid boolean
);


--
-- Name: dramas; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.dramas (
    id text NOT NULL,
    slug text NOT NULL,
    locale text NOT NULL,
    lang_cn text DEFAULT ''::text NOT NULL,
    title text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    cover_url text DEFAULT ''::text NOT NULL,
    book_type integer DEFAULT 0 NOT NULL,
    is_dub boolean DEFAULT false NOT NULL,
    is_valid boolean DEFAULT false NOT NULL,
    pay_start integer DEFAULT 0 NOT NULL,
    chapter_count integer DEFAULT 0 NOT NULL,
    tags text[] DEFAULT '{}'::text[] NOT NULL,
    publish_at timestamp with time zone,
    promoters_cnt integer DEFAULT 0 NOT NULL,
    recent_revenue_cents numeric(14,2) DEFAULT '0'::numeric NOT NULL,
    book_promotion_link text DEFAULT ''::text NOT NULL,
    app_promotion_link text DEFAULT ''::text NOT NULL,
    promotion_code text DEFAULT ''::text NOT NULL,
    group_key text NOT NULL,
    detail_synced_at timestamp with time zone,
    synced_at timestamp with time zone DEFAULT now() NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    title_key text DEFAULT ''::text NOT NULL,
    search_impressions integer DEFAULT 0 NOT NULL,
    search_data_at timestamp with time zone,
    metrics_valid boolean
);


--
-- Name: legacy_urls; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.legacy_urls (
    legacy_id integer NOT NULL,
    locale text NOT NULL,
    title_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: observe_sources; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.observe_sources (
    source text NOT NULL,
    status text NOT NULL,
    attempted_at timestamp with time zone NOT NULL,
    attempt_id text NOT NULL,
    completed_at timestamp with time zone,
    details jsonb DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT observe_sources_source_check CHECK ((source = ANY (ARRAY['catalog'::text, 'snapshot'::text, 'bill'::text, 'gsc'::text, 'pick_catalog'::text]))),
    CONSTRAINT observe_sources_status_check CHECK ((status = ANY (ARRAY['running'::text, 'success'::text, 'failed'::text])))
);


--
-- Name: outbound_clicks; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.outbound_clicks (
    id integer NOT NULL,
    drama_id text NOT NULL,
    locale text DEFAULT ''::text NOT NULL,
    serial integer,
    target text NOT NULL,
    placement text DEFAULT ''::text NOT NULL,
    referer text,
    user_agent text,
    country text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: outbound_clicks_id_seq; Type: SEQUENCE; Schema: pick_source; Owner: -
--

CREATE SEQUENCE pick_source.outbound_clicks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: outbound_clicks_id_seq; Type: SEQUENCE OWNED BY; Schema: pick_source; Owner: -
--

ALTER SEQUENCE pick_source.outbound_clicks_id_seq OWNED BY pick_source.outbound_clicks.id;


--
-- Name: sync_runs; Type: TABLE; Schema: pick_source; Owner: -
--

CREATE TABLE pick_source.sync_runs (
    id integer NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    status text DEFAULT 'running'::text NOT NULL,
    dramas_upserted integer DEFAULT 0 NOT NULL,
    chapters_upserted integer DEFAULT 0 NOT NULL,
    skipped_unknown_lang integer DEFAULT 0 NOT NULL,
    error text
);


--
-- Name: sync_runs_id_seq; Type: SEQUENCE; Schema: pick_source; Owner: -
--

CREATE SEQUENCE pick_source.sync_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sync_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: pick_source; Owner: -
--

ALTER SEQUENCE pick_source.sync_runs_id_seq OWNED BY pick_source.sync_runs.id;


--
-- Name: outbound_clicks id; Type: DEFAULT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.outbound_clicks ALTER COLUMN id SET DEFAULT nextval('pick_source.outbound_clicks_id_seq'::regclass);


--
-- Name: sync_runs id; Type: DEFAULT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.sync_runs ALTER COLUMN id SET DEFAULT nextval('pick_source.sync_runs_id_seq'::regclass);


--
-- Name: catalog_accounts catalog_accounts_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.catalog_accounts
    ADD CONSTRAINT catalog_accounts_pkey PRIMARY KEY (id);


--
-- Name: catalog_posted catalog_posted_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.catalog_posted
    ADD CONSTRAINT catalog_posted_pkey PRIMARY KEY (sd);


--
-- Name: catalog_rows catalog_rows_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.catalog_rows
    ADD CONSTRAINT catalog_rows_pkey PRIMARY KEY (row_key);


--
-- Name: chapters chapters_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.chapters
    ADD CONSTRAINT chapters_pkey PRIMARY KEY (id);


--
-- Name: dramas dramas_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.dramas
    ADD CONSTRAINT dramas_pkey PRIMARY KEY (id);


--
-- Name: legacy_urls legacy_urls_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.legacy_urls
    ADD CONSTRAINT legacy_urls_pkey PRIMARY KEY (legacy_id);


--
-- Name: observe_sources observe_sources_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.observe_sources
    ADD CONSTRAINT observe_sources_pkey PRIMARY KEY (source);


--
-- Name: outbound_clicks outbound_clicks_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.outbound_clicks
    ADD CONSTRAINT outbound_clicks_pkey PRIMARY KEY (id);


--
-- Name: sync_runs sync_runs_pkey; Type: CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.sync_runs
    ADD CONSTRAINT sync_runs_pkey PRIMARY KEY (id);


--
-- Name: catalog_rows_evidence_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX catalog_rows_evidence_idx ON pick_source.catalog_rows USING btree (has_signal, latest_evidence_on);


--
-- Name: catalog_rows_platform_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX catalog_rows_platform_idx ON pick_source.catalog_rows USING btree (platform, lang);


--
-- Name: catalog_rows_title_key_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX catalog_rows_title_key_idx ON pick_source.catalog_rows USING btree (title_key);


--
-- Name: catalog_signals_kind_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX catalog_signals_kind_idx ON pick_source.catalog_signals USING btree (kind, evidence_on);


--
-- Name: catalog_signals_pk; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE UNIQUE INDEX catalog_signals_pk ON pick_source.catalog_signals USING btree (row_key, kind, ord);


--
-- Name: chapters_drama_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX chapters_drama_idx ON pick_source.chapters USING btree (drama_id);


--
-- Name: chapters_drama_serial_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE UNIQUE INDEX chapters_drama_serial_idx ON pick_source.chapters USING btree (drama_id, serial);


--
-- Name: clicks_created_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX clicks_created_idx ON pick_source.outbound_clicks USING btree (created_at);


--
-- Name: clicks_drama_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX clicks_drama_idx ON pick_source.outbound_clicks USING btree (drama_id);


--
-- Name: cps_bill_daily_book_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX cps_bill_daily_book_idx ON pick_source.cps_bill_daily USING btree (book_id);


--
-- Name: cps_bill_daily_pk; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE UNIQUE INDEX cps_bill_daily_pk ON pick_source.cps_bill_daily USING btree (bill_date, book_id, promotion_type, promotion_value);


--
-- Name: drama_observations_drama_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX drama_observations_drama_idx ON pick_source.drama_observations USING btree (drama_id, observed_on);


--
-- Name: drama_observations_pk; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE UNIQUE INDEX drama_observations_pk ON pick_source.drama_observations USING btree (observed_on, drama_id);


--
-- Name: dramas_group_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_group_idx ON pick_source.dramas USING btree (group_key);


--
-- Name: dramas_locale_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_locale_idx ON pick_source.dramas USING btree (locale);


--
-- Name: dramas_locale_slug_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE UNIQUE INDEX dramas_locale_slug_idx ON pick_source.dramas USING btree (locale, slug);


--
-- Name: dramas_locale_title_key_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_locale_title_key_idx ON pick_source.dramas USING btree (locale, title_key);


--
-- Name: dramas_publish_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_publish_idx ON pick_source.dramas USING btree (publish_at);


--
-- Name: dramas_search_impressions_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_search_impressions_idx ON pick_source.dramas USING btree (locale, search_impressions) WHERE (search_impressions > 0);


--
-- Name: dramas_sitemap_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_sitemap_idx ON pick_source.dramas USING btree (locale, id, slug, pay_start, chapter_count, updated_at) WHERE (detail_synced_at IS NOT NULL);


--
-- Name: dramas_valid_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX dramas_valid_idx ON pick_source.dramas USING btree (is_valid);


--
-- Name: legacy_locale_key_idx; Type: INDEX; Schema: pick_source; Owner: -
--

CREATE INDEX legacy_locale_key_idx ON pick_source.legacy_urls USING btree (locale, title_key);


--
-- Name: chapters chapters_drama_id_dramas_id_fk; Type: FK CONSTRAINT; Schema: pick_source; Owner: -
--

ALTER TABLE ONLY pick_source.chapters
    ADD CONSTRAINT chapters_drama_id_dramas_id_fk FOREIGN KEY (drama_id) REFERENCES pick_source.dramas(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--
