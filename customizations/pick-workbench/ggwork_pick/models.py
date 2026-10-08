"""Extension-private SQLAlchemy tables. Never register with the host metadata."""

from sqlalchemy import JSON, Boolean, CheckConstraint, Column, Float, Index, Integer, MetaData, String, Table, Text, UniqueConstraint, false, text

metadata = MetaData()

# Feedback is private to the authenticated owner, unlike the shared catalogue. Result evidence lives beside
# candidate snapshots so the existing strict item/result contract remains unchanged.
feedback_scopes = Table(
    "ggwp_feedback_scopes",
    metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("current_version_id", String(64)),
    Column("last_verified_at", String(40)),
    Column("lease_token", String(64)),
    Column("lease_until", String(40)),
)
feedback_versions = Table(
    "ggwp_feedback_versions",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("scan_started_at", String(40), nullable=False),
    Column("scan_completed_at", String(40), nullable=False),
    Column("published_at", String(40), nullable=False),
    Column("manifest_json", JSON, nullable=False),
    UniqueConstraint("owner_id", "content_hash", name="ggwp_feedback_version_content"),
)
feedback_records = Table(
    "ggwp_feedback_records",
    metadata,
    Column("version_id", String(64), primary_key=True),
    Column("table_id", String(128), primary_key=True),
    Column("record_id", String(128), primary_key=True),
    Column("values_json", JSON, nullable=False),
)
feedback_runs = Table(
    "ggwp_feedback_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("trigger", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("version_id", String(64)),
    Column("error_code", String(40)),
)
feedback_result_evidence = Table(
    "ggwp_feedback_result_evidence",
    metadata,
    Column("result_id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("version_id", String(64), nullable=False),
    Column("evidence_json", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
feedback_identity_links = Table(
    "ggwp_feedback_identity_links",
    metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("source_record_id", String(128), primary_key=True),
    Column("catalog_identity", String(512), nullable=False),
    Column("method", String(40), nullable=False),
    Column("evidence_json", JSON, nullable=False),
    Column("confirmed_at", String(40), nullable=False),
)

import_batches = Table(
    "ggwp_import_batches",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("kind", String(20), nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("raw_blob_path", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("source_as_of", String(40)),
    Column("created_at", String(40), nullable=False),
    Column("published_at", String(40)),
    Column("validation_json", JSON, nullable=False),
)
drama_versions = Table(
    "ggwp_drama_versions",
    metadata,
    Column("batch_id", String(64), primary_key=True),
    Column("identity", String(512), primary_key=True),
    Column("payload_json", JSON, nullable=False),
)
knowledge_versions = Table(
    "ggwp_knowledge_versions",
    metadata,
    Column("batch_id", String(64), primary_key=True),
    Column("document_id", String(128), primary_key=True),
    Column("content_hash", String(64), nullable=False),
    Column("title", String(500), nullable=False),
    Column("source_ref", Text, nullable=False),
    Column("text", Text, nullable=False),
    Column("metadata_json", JSON, nullable=False),
)
candidate_sets = Table(
    "ggwp_candidate_sets",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("thread_id", String(64), nullable=False),
    Column("run_id", String(128), nullable=False),
    Column("tool_call_id", String(128), nullable=False),
    Column("request_hash", String(64)),
    Column("parent_result_id", String(64)),
    Column("catalog_batch_id", String(64), nullable=False),
    Column("knowledge_batch_id", String(64)),
    Column("rule_version", String(64), nullable=False),
    Column("ranking_version", String(64), nullable=False),
    Column("conditions_json", JSON, nullable=False),
    Column("ordered_items_json", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
    # Migration 0005, null on rows from before the mirror: the identities the query left out (replay needs them), the
    # mirror version paired with its batch, and the data_as_of it froze.
    Column("excluded_json", JSON),
    Column("mirror_version", Integer),
    Column("data_as_of_json", JSON),
    # Migration 0007, null unless the query had observation conditions: the observation sets it pinned and what it froze of
    # them (D28). Never inside data_as_of_json.
    Column("trends_set_id", String(64)),
    Column("gsc_set_id", String(64)),
    Column("obs_as_of_json", JSON),
)
selections = Table(
    "ggwp_selections",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("identity", String(512), nullable=False),
    Column("source_result_id", String(64), nullable=False),
    Column("source_item_id", String(64), nullable=False),
    Column("snapshot_json", JSON, nullable=False),
    Column("note", Text, nullable=False),
    Column("state", String(20), nullable=False),
    Column("version", Integer, nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
)
selection_commands = Table(
    "ggwp_selection_commands",
    metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("request_id", String(128), primary_key=True),
    Column("payload_hash", String(64), nullable=False),
    Column("receipt_json", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
sync_runs = Table(
    "ggwp_sync_runs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("source", String(40), nullable=False),
    Column("trigger", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    Column("started_at", String(40), nullable=False),
    Column("finished_at", String(40)),
    Column("rows", Integer),
    Column("catalog_batch_id", String(64)),
    Column("knowledge_batch_id", String(64)),
    Column("source_as_of", String(40)),
    Column("error", Text),
    # Migration 0005: the mirror run's version, stage timings, gate results and fallback reason. Every signed-in user
    # reads it through /api/pick/sync, so only what is safe to show goes in.
    Column("details_json", JSON),
)
answer_checks = Table(
    "ggwp_answer_checks",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("thread_id", String(64), nullable=False),
    Column("run_id", String(128), nullable=False),
    # Null when the model message had no id; the UI then matches the note by run.
    Column("message_id", String(256)),
    Column("notes_json", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)


# ---- migration 0007: the observation radar (design 3.5; plan TR-11, D12, D13, D23, D26, D27, D34) ----------------------
# Time columns are String(40) in repository.stamp()'s form, dates YYYY-MM-DD in the same width. Autoincrement ids never repeat
# (AUTOINCREMENT on SQLite): a set, a decision version and an evidence source_ref name rows by them.


def _id() -> Column:
    return Column("id", Integer, primary_key=True, autoincrement=True)


def _at(name: str, *, nullable: bool = True) -> Column:
    return Column(name, String(40), nullable=nullable)


def _count(name: str) -> Column:
    return Column(name, Integer, nullable=False, server_default=text("0"))


def _flag(name: str) -> Column:
    return Column(name, Boolean, nullable=False, server_default=false())


obs_runtime = Table(
    "ggwp_obs_runtime",
    metadata,
    Column("channel", String(10), primary_key=True),
    Column("lease_owner", String(128)),
    _at("lease_until"),
    _count("lease_generation"),
    _count("breaker_level"),
    _at("paused_until"),
    _at("disabled_at"),
    Column("reset_by", String(128)),
    _at("reset_at"),
    Column("user_agent", String(500)),
    Column("cookie_jar", Text),  # ciphertext only (TR-04's crypto); never in a view
    _at("cookie_warmed_at"),
    Column("state_json", JSON),
    _at("updated_at"),
)
obs_budget = Table(
    "ggwp_obs_budget",
    metadata,
    Column("channel", String(10), primary_key=True),
    Column("budget_day", String(40), primary_key=True),  # Trends: the target_date; GSC: the UTC day (D23)
    Column("collect_mode", String(20)),
    Column("cap", Integer),
    _count("requests"),
    Column("requests_before_first_limit", Integer),
    _at("first_limited_at"),
    _count("breaker_trips"),
    _count("http_429"),
    _count("probe_failures"),
    _count("quota_errors"),
    _at("extinguished_at"),
    Column("extinguish_reason", String(40)),
    _at("updated_at"),
)
obs_batches = Table(
    "ggwp_obs_batches",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("channel", String(10), nullable=False),
    Column("mode", String(10), nullable=False),
    Column("collect_mode", String(20)),
    _at("target_date"),
    Column("round_id", String(64)),
    _at("window_end"),
    Column("collector_version", String(64), nullable=False),
    Column("lease_generation", Integer),
    _at("started_at", nullable=False),
    _at("finished_at"),
    Column("outcome", String(20), nullable=False),
    _count("requests"),
    Column("planned_units", Integer),
    Column("fetched_units", Integer),
    Column("coverage", Float),
    _count("breaker_events"),
    Column("plan_json", JSON),
    Column("summary_json", JSON),
    Column("status_codes_json", JSON, nullable=False),
    Column("published_set_id", String(64)),
)
obs_requests = Table(
    "ggwp_obs_requests",
    metadata,
    _id(),
    Column("channel", String(10), nullable=False),
    Column("batch_id", String(64)),
    Column("lease_generation", Integer),
    _at("budget_day"),
    Column("budget_item", String(20), nullable=False),
    Column("identity", String(512)),
    Column("geo", String(8)),
    Column("endpoint", String(40), nullable=False),
    _at("sent_at", nullable=False),
    Column("status_code", Integer),
    Column("latency_ms", Integer),
    Column("redirect_host", String(255)),
    Column("egress_ip", String(64)),
    _at("egress_measured_at"),
    Column("user_type", String(40)),
    Column("fetch_status", String(20)),
    Column("error", String(200)),
    sqlite_autoincrement=True,
)
obs_raw = Table(
    "ggwp_obs_raw",
    metadata,
    _id(),
    Column("batch_id", String(64), nullable=False),
    Column("request_id", Integer),
    Column("identity", String(512)),
    Column("geo", String(8), nullable=False),
    Column("property", String(10)),
    Column("line_role", String(20), nullable=False),
    Column("line_index", Integer),
    Column("time_range", String(20), nullable=False),
    Column("fetch_status", String(20), nullable=False),
    Column("user_type", String(40)),
    Column("params_json", JSON, nullable=False),
    Column("data_json", JSON),  # time/value/isPartial as returned; null when the fetch failed (never zeros)
    _at("fetched_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_watch = Table(
    "ggwp_obs_watch",
    metadata,
    _id(),
    Column("identity", String(512), nullable=False),
    Column("geo", String(8), nullable=False),
    Column("query_shape", String(20), nullable=False),
    Column("title", String(500), nullable=False),
    Column("language", String(40), nullable=False),
    Column("theater", String(100), nullable=False),
    Column("tier", String(1), nullable=False),
    Column("origin", String(40), nullable=False),
    Column("rules_version", String(64), nullable=False),
    Column("ambiguity", String(20), nullable=False),
    _at("ambiguity_checked_at"),
    Column("id_evidence", String(10), nullable=False),
    _flag("shared_title"),
    _flag("paused"),
    _at("first_seen_at", nullable=False),
    _at("promoted_at"),
    _at("expires_at"),
    Column("source_catalog_batch_id", String(64), nullable=False),
    _at("updated_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_discoveries = Table(
    "ggwp_obs_discoveries",
    metadata,
    _id(),
    Column("set_id", String(64), nullable=False),
    Column("mode", String(10), nullable=False),
    Column("geo", String(8), nullable=False),
    Column("seed", String(200), nullable=False),
    Column("property", String(10), nullable=False),
    Column("term", Text, nullable=False),
    Column("normalized_term", Text, nullable=False),
    Column("language", String(40)),
    Column("match_status", String(20), nullable=False),
    Column("route", String(20), nullable=False),
    Column("matched_identity", String(512)),
    Column("breakout", Boolean, nullable=False),
    _at("first_seen_at", nullable=False),
    Column("raw_id", Integer),
    Column("review_status", String(20)),
    _at("reviewed_at"),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_identity_alias = Table(
    "ggwp_obs_identity_alias",
    metadata,
    _id(),
    Column("alias_version", Integer, nullable=False),
    Column("old_identity", String(512), nullable=False),
    Column("new_identity", String(512), nullable=False),
    Column("status", String(20), nullable=False),
    Column("platform", String(100), nullable=False),
    Column("language", String(40), nullable=False),
    Column("old_title", String(500), nullable=False),
    Column("new_title", String(500), nullable=False),
    Column("evidence_json", JSON, nullable=False),
    Column("source_catalog_batch_id", String(64), nullable=False),
    Column("decisions_version", Integer),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_legacy = Table(
    "ggwp_obs_legacy",
    metadata,
    Column("snapshot_id", String(128), primary_key=True),
    Column("raw_url", String(2048), primary_key=True),  # GSC's URL as returned, never decoded or trimmed (design 5.5)
    Column("target_kind", String(20), nullable=False),
    Column("reason", String(100)),
    Column("locale", String(20)),
    Column("book_id", String(64)),
    Column("first_hop_book_id", String(64)),
    Column("via", String(100)),
    Column("landing_path", Text),
    Column("blog_path", Text),
    Column("hops_json", JSON),
    Column("realshort_commit", String(64), nullable=False),
    Column("resolver_version", String(128), nullable=False),
    _at("as_of", nullable=False),
    _at("imported_at", nullable=False),
)
gsc_slices = Table(
    "ggwp_gsc_slices",
    metadata,
    _id(),  # the slice version id frozen sets name (SliceRef.version_id)
    Column("dataset", String(4), nullable=False),
    _at("pt_date", nullable=False),
    Column("shape", String(20), nullable=False),
    Column("data_state", String(20), nullable=False),
    Column("status", String(20), nullable=False),
    _flag("active"),
    _flag("stale"),
    Column("round_id", String(64), nullable=False),
    Column("row_count", Integer),
    Column("first_incomplete_hour", String(40)),  # GSC's metadata strings, verbatim
    Column("first_incomplete_date", String(40)),
    _at("usable_until"),
    _flag("watermark_absent"),
    Column("details_json", JSON),
    _at("fetched_at", nullable=False),
    _at("activated_at"),
    sqlite_autoincrement=True,
)
gsc_hourly = Table(
    "ggwp_gsc_hourly",
    metadata,
    Column("slice_id", Integer, primary_key=True, autoincrement=False),
    Column("hour", String(40), primary_key=True),
    Column("page", String(2048), primary_key=True),
    Column("country", String(8), primary_key=True),  # as the API returns it (lower case, zzz for unknown)
    Column("impressions", Integer, nullable=False),
    Column("clicks", Integer, nullable=False),
    Column("position", Float),
)
gsc_daily = Table(
    "ggwp_gsc_daily",
    metadata,
    Column("slice_id", Integer, primary_key=True, autoincrement=False),
    Column("page", String(2048), primary_key=True),
    Column("country", String(8), primary_key=True),
    _at("pt_date", nullable=False),
    Column("data_state", String(20), nullable=False),
    Column("impressions", Integer, nullable=False),
    Column("clicks", Integer, nullable=False),
    Column("position", Float),
)
gsc_query_daily = Table(
    "ggwp_gsc_query_daily",
    metadata,
    Column("slice_id", Integer, primary_key=True, autoincrement=False),
    Column("page", String(2048), primary_key=True),
    Column("query", String(500), primary_key=True),  # part of the key: the pan redaction deletes the row (D18)
    _at("pt_date", nullable=False),
    Column("impressions", Integer, nullable=False),
    Column("clicks", Integer, nullable=False),
    Column("position", Float),
)
obs_sets = Table(
    "ggwp_obs_sets",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("channel", String(10), nullable=False),
    Column("mode", String(10), nullable=False),
    Column("status", String(20), nullable=False),
    Column("batch_id", String(64), nullable=False),
    _at("published_at", nullable=False),
    _at("as_of", nullable=False),
    Column("source_catalog_batch_id", String(64), nullable=False),
    Column("collector_version", String(64), nullable=False),
    Column("rules_version", String(64), nullable=False),
    Column("link_rules_version", String(64), nullable=False),
    Column("alias_version", Integer, nullable=False),
    Column("decisions_version", Integer, nullable=False),
    _at("target_date"),
    _at("window_end"),
    Column("round_id", String(64)),
    Column("frozen_inputs_json", JSON, nullable=False),
    Column("summary_json", JSON, nullable=False),
    _at("pruned_at"),
)
obs_states = Table(
    "ggwp_obs_states",
    metadata,
    _id(),  # StateRow.row_id
    Column("set_id", String(64), nullable=False),
    Column("channel", String(10), nullable=False),
    Column("mode", String(10), nullable=False),
    Column("identity", String(512), nullable=False),
    Column("title", String(500), nullable=False),
    Column("normalized_title", String(500)),  # Trends rows only: the correspondence key's title (D24); not in the view
    Column("language", String(40), nullable=False),
    Column("theater", String(100), nullable=False),
    Column("scope", String(8), nullable=False),
    Column("window_kind", String(8), nullable=False),
    Column("state", String(30), nullable=False),
    Column("confirmation", String(20)),
    Column("admission", String(20)),
    Column("labels_json", JSON, nullable=False),
    Column("tier", String(1)),
    Column("correspondence", String(20)),
    Column("id_evidence", String(10)),
    Column("ambiguity", String(20)),
    Column("flags_json", JSON, nullable=False),
    Column("carried_over", Boolean, nullable=False),
    Column("stale", Boolean, nullable=False),
    _at("window_end", nullable=False),
    _at("latest_block_end"),
    Column("metrics_json", JSON, nullable=False),
    Column("quality_note_json", JSON),
    Column("paste_row_json", JSON),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_milestones = Table(
    "ggwp_obs_milestones",
    metadata,
    _id(),
    Column("event", String(40), nullable=False),
    Column("identity", String(512)),  # null for an out-of-pool discovery: keyed by language and normalized title until it enters the pool (D37)
    _at("occurred_at", nullable=False),
    _at("recorded_at", nullable=False),
    Column("eval_rules_version", String(64), nullable=False),
    Column("source_ref", String(512)),
    Column("details_json", JSON, nullable=False),
    sqlite_autoincrement=True,
)
obs_alerts = Table(
    "ggwp_obs_alerts",
    metadata,
    _id(),
    Column("mode", String(10), nullable=False),
    Column("channel", String(10), nullable=False),
    Column("set_id", String(64), nullable=False),
    Column("state_row_id", Integer, nullable=False),
    Column("identity", String(512), nullable=False),
    Column("root_identity", String(512), nullable=False),
    Column("state", String(30), nullable=False),
    Column("scope", String(8), nullable=False),
    Column("dedupe_key", String(2048), nullable=False),
    _at("published_at", nullable=False),
    Column("eval_rules_version", String(64), nullable=False),
    Column("alias_version", Integer, nullable=False),
    Column("evidence_json", JSON, nullable=False),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_decisions = Table(
    "ggwp_obs_decisions",
    metadata,
    _id(),  # append-only; a set freezes the largest id it read as decisions_version (D24)
    Column("kind", String(40), nullable=False),
    Column("request_id", String(128), nullable=False),
    Column("owner_id", String(128), nullable=False),  # the authenticated operator
    Column("payload_json", JSON, nullable=False),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
obs_links = Table(
    "ggwp_obs_links",
    metadata,
    _id(),
    Column("link_rules_version", String(64), nullable=False),
    Column("trends_set_id", String(64), nullable=False),
    Column("gsc_set_id", String(64), nullable=False),
    Column("mode", String(10), nullable=False),
    Column("identity", String(512), nullable=False),
    Column("country", String(8)),
    Column("trends_geo", String(8)),
    Column("label", String(40), nullable=False),
    Column("trends_row_id", Integer),
    Column("gsc_row_id", Integer),
    _at("trends_anchor", nullable=False),
    _at("gsc_anchor", nullable=False),
    Column("pair_gap_minutes", Integer, nullable=False),
    Column("timely", Boolean, nullable=False),
    Column("published_gap_minutes", Integer, nullable=False),
    Column("stale", Boolean, nullable=False),
    _at("created_at", nullable=False),
    sqlite_autoincrement=True,
)
gsc_vchecks = Table(
    "ggwp_gsc_vchecks",
    metadata,
    _id(),
    Column("round_id", String(64), nullable=False),
    Column("identity", String(512), nullable=False),
    Column("check_kind", String(4), nullable=False),
    Column("window_label", String(20), nullable=False),
    _at("window_start", nullable=False),
    _at("window_end", nullable=False),
    Column("country", String(8), nullable=False),
    Column("data_state", String(20), nullable=False),
    Column("impressions", Integer),  # null: the response had no row (never 0 in its place)
    Column("clicks", Integer),
    Column("row_count", Integer, nullable=False),
    Column("request_status", String(20), nullable=False),
    Column("chunk_count", Integer, nullable=False),
    Column("slice_versions_json", JSON, nullable=False),
    _at("fetched_at", nullable=False),
    Column("reused", Boolean, nullable=False),
    Column("reused_from", Integer),
    sqlite_autoincrement=True,
)
gsc_totals = Table(
    "ggwp_gsc_totals",
    metadata,
    _id(),
    Column("round_id", String(64), nullable=False),
    Column("kind", String(10), nullable=False),
    Column("data_state", String(20), nullable=False),
    _at("hour"),
    _at("pt_date"),
    Column("impressions", Integer),
    Column("clicks", Integer),
    Column("request_status", String(20), nullable=False),
    Column("watermark", String(40)),
    _at("fetched_at", nullable=False),
    sqlite_autoincrement=True,
)

# Content plans are user-confirmed private drafts, independent of host scheduled Agent jobs.
content_plans = Table(
    "ggwp_content_plans",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("title", String(200), nullable=False),
    Column("timezone", String(100), nullable=False),
    Column("created_at", String(40), nullable=False),
    Column("updated_at", String(40), nullable=False),
    CheckConstraint("version >= 1", name="ggwp_content_plan_version"),
    Index("ggwp_content_plans_owner_updated", "owner_id", "updated_at", "id"),
)
content_plan_rows = Table(
    "ggwp_content_plan_rows",
    metadata,
    Column("plan_id", String(64), primary_key=True),
    Column("row_id", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("position", Integer),
    Column("source_json", JSON, nullable=False),
    Column("editable_json", JSON, nullable=False),
    Column("scheduled_at", String(40)),
    CheckConstraint("position IS NULL OR position >= 0", name="ggwp_content_plan_row_position"),
)
content_plan_commands = Table(
    "ggwp_content_plan_commands",
    metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("request_id", String(128), primary_key=True),
    Column("payload_hash", String(64), nullable=False),
    Column("receipt_json", JSON, nullable=False),
    Column("created_at", String(40), nullable=False),
)
