"""The observation radar's storage (plan TR-11; design 3.4, 3.5; D12, D13, D14, D15, D16, D23, D26, D27, D34).

Both dialects get 21 tables, three nullable columns on ggwp_candidate_sets and the two ggwp_obs_runtime rows. PostgreSQL
also gets the pick_obs schema: eight views with the columns of ggwork_pick/observe/contract_views.py (never imported here:
a revision stays as it was written), readable by the board reader alone, and the observer's table grants (TR-12's list).
Only tables and nullable columns are added, so the code before 0007 runs on a 0007 database unchanged.

Every step checks for what is already there: SQLite commits each DDL statement on its own, and rolling the code back means
setting the version table back by hand (realshort-sync.md). On PostgreSQL everything runs in the gateway's one migration
transaction, so a failure anywhere leaves nothing of 0001-0007 behind. The downgrade leaves the pick_obs schema, like
0006 leaves pick_mirror (U26), and the grants 0007 gave on objects older than it: TR-12's bootstrap gives the same ones.
"""

import logging
import os
import re

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# Alembic loads revision files under generated module names ("0007_observe_py"); log under a stable one.
logger = logging.getLogger("ggwork_pick.migrations")

READER_ROLE_ENV = "PICK_MIRROR_READER_ROLE"
DEFAULT_READER_ROLE = "pick_board_reader"
OBSERVER_ROLE_ENV = "PICK_OBS_OBSERVER_ROLE"
DEFAULT_OBSERVER_ROLE = "pick_observer"
_ROLE_NAME = re.compile(r"[a-z_][a-z0-9_]{0,62}")
# Supabase's API roles: pick_obs is closed to them and to PUBLIC wherever they exist.
API_ROLES = ("anon", "authenticated")
CHANNELS = ("trends", "gsc")
RUNTIME_ROWS = ", ".join(f"('{channel}')" for channel in CHANNELS)  # constants, never input

# sa.JSON is json on PostgreSQL like every other ggwp JSON column, never jsonb: json keeps an escaped NUL (plan 3.7).
COLUMNS = (
    ("ggwp_candidate_sets", "trends_set_id", sa.String(64)),
    ("ggwp_candidate_sets", "gsc_set_id", sa.String(64)),
    ("ggwp_candidate_sets", "obs_as_of_json", sa.JSON()),
)
CANDIDATE_INDEXES = (
    ("ggwp_candidate_sets_trends_set", "ggwp_candidate_sets (trends_set_id, created_at)"),
    ("ggwp_candidate_sets_gsc_set", "ggwp_candidate_sets (gsc_set_id, created_at)"),
)
# Raw DDL on both dialects: SQLAlchemy skips SQLite's expression indexes when it reflects, so IF NOT EXISTS does the check.
INDEXES = (
    # Design 3.2: one Trends batch per target date; GSC rounds are never unique by date.
    ("UNIQUE INDEX", "ggwp_obs_batches_trends_day", "ggwp_obs_batches (target_date) WHERE channel = 'trends'"),
    ("INDEX", "ggwp_obs_batches_started", "ggwp_obs_batches (channel, started_at)"),
    ("INDEX", "ggwp_obs_requests_sent", "ggwp_obs_requests (sent_at)"),
    ("INDEX", "ggwp_obs_requests_batch", "ggwp_obs_requests (batch_id)"),
    ("INDEX", "ggwp_obs_raw_batch", "ggwp_obs_raw (batch_id)"),
    ("INDEX", "ggwp_obs_discoveries_set", "ggwp_obs_discoveries (set_id)"),
    # Design 5.3: one active version per dataset and PT date.
    ("UNIQUE INDEX", "ggwp_gsc_slices_active", "ggwp_gsc_slices (dataset, pt_date) WHERE active"),
    ("INDEX", "ggwp_gsc_slices_round", "ggwp_gsc_slices (round_id)"),
    ("INDEX", "ggwp_obs_sets_published", "ggwp_obs_sets (channel, mode, published_at)"),
    ("INDEX", "ggwp_obs_states_identity", "ggwp_obs_states (identity, created_at)"),
    ("INDEX", "ggwp_obs_milestones_identity", "ggwp_obs_milestones (identity, event)"),
    ("INDEX", "ggwp_obs_milestones_recorded", "ggwp_obs_milestones (recorded_at)"),
    ("INDEX", "ggwp_obs_alerts_dedupe", "ggwp_obs_alerts (dedupe_key, published_at)"),
    ("INDEX", "ggwp_obs_alerts_published", "ggwp_obs_alerts (published_at)"),
    # D13: a link fact is keyed by its link-rules version; a newer version adds facts beside the old ones. The country is
    # null for an identity-level fact (different_markets), which a plain unique key would let repeat.
    ("UNIQUE INDEX", "ggwp_obs_links_fact", "ggwp_obs_links (link_rules_version, trends_set_id, gsc_set_id, identity, coalesce(country, ''))"),
    ("INDEX", "ggwp_obs_links_trends", "ggwp_obs_links (trends_set_id)"),
    ("INDEX", "ggwp_obs_links_gsc", "ggwp_obs_links (gsc_set_id)"),
    ("INDEX", "ggwp_gsc_vchecks_round", "ggwp_gsc_vchecks (round_id)"),
    ("INDEX", "ggwp_gsc_vchecks_reuse", "ggwp_gsc_vchecks (identity, check_kind, window_label)"),
    ("INDEX", "ggwp_gsc_totals_round", "ggwp_gsc_totals (round_id)"),
)

# ---- tables -------------------------------------------------------------------------------------------------------
# Time columns are String(40) in repository.stamp()'s form, dates YYYY-MM-DD in the same width. Autoincrement ids never
# repeat (AUTOINCREMENT on SQLite): a set, a decision version and an evidence source_ref name rows by them.


def _id() -> sa.Column:
    return sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)


def _at(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.String(40), nullable=nullable)


def _text(name: str, length: int, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.String(length), nullable=nullable)


def _int(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=nullable)


def _json(name: str, *, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.JSON(), nullable=nullable)


def _bool(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False)


def _count(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default=sa.text("0"))


def _flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


def _one_of(table: str, column: str, values: tuple[str, ...]) -> sa.CheckConstraint:
    listed = ", ".join(f"'{value}'" for value in values)  # constants, never input
    return sa.CheckConstraint(f"{column} IN ({listed})", name=f"{table}_{column}")


def _channel(table: str) -> sa.CheckConstraint:
    return _one_of(table, "channel", CHANNELS)


def _mode(table: str) -> sa.CheckConstraint:
    return _one_of(table, "mode", ("live", "shadow"))


def _runtime() -> tuple:
    return (
        sa.Column("channel", sa.String(10), primary_key=True),
        _text("lease_owner", 128, nullable=True),
        _at("lease_until"),
        _count("lease_generation"),
        _count("breaker_level"),
        _at("paused_until"),
        _at("disabled_at"),
        _text("reset_by", 128, nullable=True),
        _at("reset_at"),
        _text("user_agent", 500, nullable=True),
        sa.Column("cookie_jar", sa.Text()),  # ciphertext only; never in a view
        _at("cookie_warmed_at"),
        _json("state_json"),
        _at("updated_at"),
        _channel("ggwp_obs_runtime"),
    )


def _budget() -> tuple:
    return (
        sa.Column("channel", sa.String(10), primary_key=True),
        sa.Column("budget_day", sa.String(40), primary_key=True),  # Trends: the target_date; GSC: the UTC day (D23)
        _text("collect_mode", 20, nullable=True),
        _int("cap"),
        _count("requests"),
        _int("requests_before_first_limit"),
        _at("first_limited_at"),
        _count("breaker_trips"),
        _count("http_429"),
        _count("probe_failures"),
        _count("quota_errors"),
        _at("extinguished_at"),
        _text("extinguish_reason", 40, nullable=True),
        _at("updated_at"),
        _channel("ggwp_obs_budget"),
    )


def _batches() -> tuple:
    return (
        sa.Column("id", sa.String(64), primary_key=True),
        _text("channel", 10),
        _text("mode", 10),
        _text("collect_mode", 20, nullable=True),
        _at("target_date"),
        _text("round_id", 64, nullable=True),
        _at("window_end"),
        _text("collector_version", 64),
        _int("lease_generation"),
        _at("started_at", nullable=False),
        _at("finished_at"),
        _text("outcome", 20),
        _count("requests"),
        _int("planned_units"),
        _int("fetched_units"),
        sa.Column("coverage", sa.Float()),
        _count("breaker_events"),
        _json("plan_json"),
        _json("summary_json"),
        _json("status_codes_json", nullable=False),
        _text("published_set_id", 64, nullable=True),
        _channel("ggwp_obs_batches"),
        _mode("ggwp_obs_batches"),
    )


def _requests() -> tuple:
    return (
        _id(),
        _text("channel", 10),
        _text("batch_id", 64, nullable=True),
        _int("lease_generation"),
        _at("budget_day"),
        _text("budget_item", 20),
        _text("identity", 512, nullable=True),
        _text("geo", 8, nullable=True),
        _text("endpoint", 40),
        _at("sent_at", nullable=False),
        _int("status_code"),
        _int("latency_ms"),
        _text("redirect_host", 255, nullable=True),
        _text("egress_ip", 64, nullable=True),
        _at("egress_measured_at"),
        _text("user_type", 40, nullable=True),
        _text("fetch_status", 20, nullable=True),
        _text("error", 200, nullable=True),
        _channel("ggwp_obs_requests"),
    )


def _raw() -> tuple:
    return (
        _id(),
        _text("batch_id", 64),
        _int("request_id"),
        _text("identity", 512, nullable=True),
        _text("geo", 8),
        _text("property", 10, nullable=True),
        _text("line_role", 20),
        _int("line_index"),
        _text("time_range", 20),
        _text("fetch_status", 20),
        _text("user_type", 40, nullable=True),
        _json("params_json", nullable=False),
        _json("data_json"),  # time/value/isPartial as returned; null when the fetch failed (never zeros)
        _at("fetched_at", nullable=False),
    )


def _watch() -> tuple:
    return (
        _id(),
        _text("identity", 512),
        _text("geo", 8),
        _text("query_shape", 20),
        _text("title", 500),
        _text("language", 40),
        _text("theater", 100),
        _text("tier", 1),
        _text("origin", 40),
        _text("rules_version", 64),
        _text("ambiguity", 20),
        _at("ambiguity_checked_at"),
        _text("id_evidence", 10),
        _flag("shared_title"),
        _flag("paused"),
        _at("first_seen_at", nullable=False),
        _at("promoted_at"),
        _at("expires_at"),
        _text("source_catalog_batch_id", 64),
        _at("updated_at", nullable=False),
        sa.UniqueConstraint("identity", "geo", "query_shape", name="ggwp_obs_watch_unit"),
    )


def _discoveries() -> tuple:
    return (
        _id(),
        _text("set_id", 64),
        _text("mode", 10),
        _text("geo", 8),
        _text("seed", 200),
        _text("property", 10),
        sa.Column("term", sa.Text(), nullable=False),
        sa.Column("normalized_term", sa.Text(), nullable=False),
        _text("language", 40, nullable=True),
        _text("match_status", 20),
        _text("route", 20),
        _text("matched_identity", 512, nullable=True),
        _bool("breakout"),
        _at("first_seen_at", nullable=False),
        _int("raw_id"),
        _text("review_status", 20, nullable=True),
        _at("reviewed_at"),
        _at("created_at", nullable=False),
        _mode("ggwp_obs_discoveries"),
    )


def _identity_alias() -> tuple:
    return (
        _id(),
        _int("alias_version", nullable=False),
        _text("old_identity", 512),
        _text("new_identity", 512),
        _text("status", 20),
        _text("platform", 100),
        _text("language", 40),
        _text("old_title", 500),
        _text("new_title", 500),
        _json("evidence_json", nullable=False),
        _text("source_catalog_batch_id", 64),
        _int("decisions_version"),
        _at("created_at", nullable=False),
        sa.UniqueConstraint("alias_version", "old_identity", "new_identity", name="ggwp_obs_alias_pair"),
    )


def _legacy() -> tuple:
    return (
        sa.Column("snapshot_id", sa.String(128), primary_key=True),
        sa.Column("raw_url", sa.String(2048), primary_key=True),  # GSC's URL as returned, never decoded or trimmed
        _text("target_kind", 20),
        _text("reason", 100, nullable=True),
        _text("locale", 20, nullable=True),
        _text("book_id", 64, nullable=True),
        _text("first_hop_book_id", 64, nullable=True),
        _text("via", 100, nullable=True),
        sa.Column("landing_path", sa.Text()),
        sa.Column("blog_path", sa.Text()),
        _json("hops_json"),
        _text("realshort_commit", 64),
        _text("resolver_version", 128),
        _at("as_of", nullable=False),
        _at("imported_at", nullable=False),
    )


def _slices() -> tuple:
    return (
        _id(),  # the slice version id frozen sets name
        _text("dataset", 4),
        _at("pt_date", nullable=False),
        _text("shape", 20),
        _text("data_state", 20),
        _text("status", 20),
        _flag("active"),
        _flag("stale"),
        _text("round_id", 64),
        _int("row_count"),
        _text("first_incomplete_hour", 40, nullable=True),  # GSC's metadata strings, verbatim
        _text("first_incomplete_date", 40, nullable=True),
        _at("usable_until"),
        _flag("watermark_absent"),
        _json("details_json"),
        _at("fetched_at", nullable=False),
        _at("activated_at"),
    )


def _metrics() -> tuple[sa.Column, ...]:
    return (_int("impressions", nullable=False), _int("clicks", nullable=False), sa.Column("position", sa.Float()))


def _slice_key(*columns: sa.Column) -> tuple[sa.Column, ...]:
    return (sa.Column("slice_id", sa.Integer(), primary_key=True, autoincrement=False), *columns)


def _hourly() -> tuple:
    return (
        *_slice_key(
            sa.Column("hour", sa.String(40), primary_key=True),
            sa.Column("page", sa.String(2048), primary_key=True),
            sa.Column("country", sa.String(8), primary_key=True),
        ),
        *_metrics(),
    )


def _daily() -> tuple:
    return (
        *_slice_key(sa.Column("page", sa.String(2048), primary_key=True), sa.Column("country", sa.String(8), primary_key=True)),
        _at("pt_date", nullable=False),
        _text("data_state", 20),
        *_metrics(),
    )


def _query_daily() -> tuple:
    return (
        # The query is part of the key: the pan redaction deletes the row (D18).
        *_slice_key(sa.Column("page", sa.String(2048), primary_key=True), sa.Column("query", sa.String(500), primary_key=True)),
        _at("pt_date", nullable=False),
        *_metrics(),
    )


def _sets() -> tuple:
    return (
        sa.Column("id", sa.String(64), primary_key=True),
        _text("channel", 10),
        _text("mode", 10),
        _text("status", 20),
        _text("batch_id", 64),
        _at("published_at", nullable=False),
        _at("as_of", nullable=False),
        _text("source_catalog_batch_id", 64),
        _text("collector_version", 64),
        _text("rules_version", 64),
        _text("link_rules_version", 64),
        _int("alias_version", nullable=False),
        _int("decisions_version", nullable=False),
        _at("target_date"),
        _at("window_end"),
        _text("round_id", 64, nullable=True),
        _json("frozen_inputs_json", nullable=False),
        _json("summary_json", nullable=False),
        _at("pruned_at"),
        _channel("ggwp_obs_sets"),
        _mode("ggwp_obs_sets"),
        _one_of("ggwp_obs_sets", "status", ("published", "pruned")),
    )


def _states() -> tuple:
    return (
        _id(),
        *(_text(name, length) for name, length in (("set_id", 64), ("channel", 10), ("mode", 10), ("identity", 512), ("title", 500))),
        _text("normalized_title", 500, nullable=True),  # Trends rows only: the correspondence key's title (D24); not in the view
        *(_text(name, length) for name, length in (("language", 40), ("theater", 100), ("scope", 8), ("window_kind", 8), ("state", 30))),
        _text("confirmation", 20, nullable=True),
        _text("admission", 20, nullable=True),
        _json("labels_json", nullable=False),
        _text("tier", 1, nullable=True),
        _text("correspondence", 20, nullable=True),
        _text("id_evidence", 10, nullable=True),
        _text("ambiguity", 20, nullable=True),
        _json("flags_json", nullable=False),
        _bool("carried_over"),
        _bool("stale"),
        _at("window_end", nullable=False),
        _at("latest_block_end"),
        _json("metrics_json", nullable=False),
        _json("quality_note_json"),
        _json("paste_row_json"),
        _at("created_at", nullable=False),
        sa.UniqueConstraint("set_id", "identity", "scope", "window_kind", name="ggwp_obs_states_row"),
        _channel("ggwp_obs_states"),
        _mode("ggwp_obs_states"),
        sa.CheckConstraint("channel <> 'trends' OR normalized_title IS NOT NULL", name="ggwp_obs_states_trends_title"),
    )


def _milestones() -> tuple:
    return (
        _id(),
        _text("event", 40),
        _text("identity", 512, nullable=True),  # null for an out-of-pool discovery: keyed by language and normalized title until it enters the pool (D37)
        _at("occurred_at", nullable=False),
        _at("recorded_at", nullable=False),
        _text("eval_rules_version", 64),
        _text("source_ref", 512, nullable=True),
        _json("details_json", nullable=False),
    )


def _alerts() -> tuple:
    return (
        _id(),
        _text("mode", 10),
        _text("channel", 10),
        _text("set_id", 64),
        _int("state_row_id", nullable=False),
        _text("identity", 512),
        _text("root_identity", 512),
        _text("state", 30),
        _text("scope", 8),
        _text("dedupe_key", 2048),
        _at("published_at", nullable=False),
        _text("eval_rules_version", 64),
        _int("alias_version", nullable=False),
        _json("evidence_json", nullable=False),
        _at("created_at", nullable=False),
        _channel("ggwp_obs_alerts"),
        _mode("ggwp_obs_alerts"),
    )


def _decisions() -> tuple:
    return (
        _id(),  # append-only; a set freezes the largest id it read as decisions_version (D24)
        _text("kind", 40),
        _text("request_id", 128),
        _text("owner_id", 128),
        _json("payload_json", nullable=False),
        _at("created_at", nullable=False),
        sa.UniqueConstraint("owner_id", "request_id", name="ggwp_obs_decision_request"),
    )


def _links() -> tuple:
    return (
        _id(),
        *(_text(name, length) for name, length in (("link_rules_version", 64), ("trends_set_id", 64), ("gsc_set_id", 64), ("mode", 10))),
        _text("identity", 512),
        _text("country", 8, nullable=True),
        _text("trends_geo", 8, nullable=True),
        _text("label", 40),
        _int("trends_row_id"),
        _int("gsc_row_id"),
        _at("trends_anchor", nullable=False),
        _at("gsc_anchor", nullable=False),
        _int("pair_gap_minutes", nullable=False),
        _bool("timely"),
        _int("published_gap_minutes", nullable=False),
        _bool("stale"),
        _at("created_at", nullable=False),
        _mode("ggwp_obs_links"),
    )


def _vchecks() -> tuple:
    return (
        _id(),
        *(_text(name, length) for name, length in (("round_id", 64), ("identity", 512), ("check_kind", 4), ("window_label", 20))),
        _at("window_start", nullable=False),
        _at("window_end", nullable=False),
        _text("country", 8),
        _text("data_state", 20),
        _int("impressions"),  # null: the response had no row (never 0 in its place)
        _int("clicks"),
        _int("row_count", nullable=False),
        _text("request_status", 20),
        _int("chunk_count", nullable=False),
        _json("slice_versions_json", nullable=False),
        _at("fetched_at", nullable=False),
        _bool("reused"),
        _int("reused_from"),
    )


def _totals() -> tuple:
    return (
        _id(),
        _text("round_id", 64),
        _text("kind", 10),
        _text("data_state", 20),
        _at("hour"),
        _at("pt_date"),
        _int("impressions"),
        _int("clicks"),
        _text("request_status", 20),
        _text("watermark", 40, nullable=True),
        _at("fetched_at", nullable=False),
    )


# Design 3.5's seventeen, then D12's decisions, D13's links, D26's V checks and D27's totals; dropped in reverse.
TABLES = (
    ("ggwp_obs_runtime", _runtime),
    ("ggwp_obs_budget", _budget),
    ("ggwp_obs_batches", _batches),
    ("ggwp_obs_requests", _requests),
    ("ggwp_obs_raw", _raw),
    ("ggwp_obs_watch", _watch),
    ("ggwp_obs_discoveries", _discoveries),
    ("ggwp_obs_identity_alias", _identity_alias),
    ("ggwp_obs_legacy", _legacy),
    ("ggwp_gsc_slices", _slices),
    ("ggwp_gsc_hourly", _hourly),
    ("ggwp_gsc_daily", _daily),
    ("ggwp_gsc_query_daily", _query_daily),
    ("ggwp_obs_sets", _sets),
    ("ggwp_obs_states", _states),
    ("ggwp_obs_milestones", _milestones),
    ("ggwp_obs_alerts", _alerts),
    ("ggwp_obs_decisions", _decisions),
    ("ggwp_obs_links", _links),
    ("ggwp_gsc_vchecks", _vchecks),
    ("ggwp_gsc_totals", _totals),
)

# ---- pick_obs (PostgreSQL) ----------------------------------------------------------------------------------------
# {s} is the quoted schema of the ggwp tables (current_schema() while migrating). The views run with their owner's rights
# (no security_invoker), so the reader needs nothing of the tables. The runtime tables (cookie jar, request log, lease)
# are never in a view.
VIEWS = (
    (
        "sets",
        """SELECT id AS set_id, channel, mode, status, published_at, as_of, source_catalog_batch_id, collector_version, rules_version,
        link_rules_version, alias_version, decisions_version, target_date, window_end, round_id, frozen_inputs_json AS frozen_inputs,
        summary_json AS summary
        FROM {s}.ggwp_obs_sets""",
    ),
    (
        "states",
        """SELECT id AS row_id, set_id, channel, mode, identity, title, language, theater, scope, window_kind, state, confirmation,
        admission, labels_json AS labels, tier, correspondence, id_evidence, ambiguity, flags_json AS flags, carried_over, stale,
        window_end, latest_block_end, metrics_json AS metrics, quality_note_json AS quality_note, paste_row_json AS paste_row, created_at
        FROM {s}.ggwp_obs_states""",
    ),
    (
        "links",
        """SELECT id, link_rules_version, trends_set_id, gsc_set_id, mode, identity, country, trends_geo, label, trends_row_id, gsc_row_id,
        trends_anchor, gsc_anchor, pair_gap_minutes, timely, published_gap_minutes, stale, created_at
        FROM {s}.ggwp_obs_links""",
    ),
    (
        "discoveries",
        """SELECT id AS discovery_id, set_id, mode, geo, seed, property, term, normalized_term, language, match_status, route,
        matched_identity, breakout, first_seen_at
        FROM {s}.ggwp_obs_discoveries""",
    ),
    # Design 7.2: the pairs whose latest alias version still suggests them; a later version of a pair replaces the earlier.
    (
        "alias_queue",
        """SELECT a.id AS alias_id, a.alias_version, a.old_identity, a.new_identity, a.status, a.platform, a.language, a.old_title,
        a.new_title, a.evidence_json AS evidence, a.created_at
        FROM {s}.ggwp_obs_identity_alias a
        WHERE a.status = 'suggested' AND NOT EXISTS (
            SELECT 1 FROM {s}.ggwp_obs_identity_alias later
            WHERE later.old_identity = a.old_identity AND later.new_identity = a.new_identity AND later.alias_version > a.alias_version
        )""",
    ),
    # Design 4.7: rising or emerging Trends rows of the latest published Trends set of each mode whose correspondence nobody
    # has confirmed yet; since is the first such row of the same (identity, platform, normalized title) in that mode.
    (
        "confirm_queue",
        """WITH latest AS (
            SELECT s.id FROM {s}.ggwp_obs_sets s
            WHERE s.channel = 'trends' AND s.status = 'published' AND NOT EXISTS (
                SELECT 1 FROM {s}.ggwp_obs_sets n
                WHERE n.channel = 'trends' AND n.status = 'published' AND n.mode = s.mode AND (n.published_at, n.id) > (s.published_at, s.id)
            )
        ), queued AS (
            SELECT * FROM {s}.ggwp_obs_states
            WHERE channel = 'trends' AND state IN ('rising', 'emerging') AND correspondence = 'unconfirmed'
        )
        SELECT q.identity, q.title, q.theater AS platform, q.normalized_title, q.language, q.scope AS geo, q.state, q.confirmation,
        q.id_evidence, q.set_id, q.mode, (
            SELECT min(first.created_at) FROM queued first
            WHERE first.mode = q.mode AND first.identity = q.identity AND first.theater = q.theater
            AND first.normalized_title = q.normalized_title
        ) AS since
        FROM queued q JOIN latest ON latest.id = q.set_id""",
    ),
    (
        "alerts",
        """SELECT id, mode, channel, set_id, state_row_id, identity, root_identity, state, scope, dedupe_key, published_at,
        eval_rules_version, alias_version, evidence_json AS evidence, created_at
        FROM {s}.ggwp_obs_alerts""",
    ),
    (
        "run_status",
        """SELECT channel, id AS batch_id, mode, target_date, round_id, started_at, finished_at, outcome, requests, published_set_id,
        status_codes_json AS status_codes
        FROM {s}.ggwp_obs_batches""",
    ),
)
VIEW_NAMES = tuple(f"pick_obs.{name}" for name, _ in VIEWS)


def _has_serial_id(items: tuple) -> bool:
    return any(isinstance(item, sa.Column) and item.name == "id" and isinstance(item.type, sa.Integer) for item in items)


# Tables whose rows carry an autoincrement id: AUTOINCREMENT on SQLite, a sequence on PostgreSQL.
SERIAL_TABLES = tuple(name for name, columns in TABLES if _has_serial_id(columns()))

# The observer's table grants (TR-12; D14, D15, D16): it reads these, writes every other ggwp_obs_* and ggwp_gsc_* table
# (and their sequences), reads four columns of the candidate sets and the mirror's versions and series. Never pick_obs.
OBSERVER_READS = ("ggwp_import_batches", "ggwp_drama_versions", "ggwp_alembic_version", "ggwp_obs_decisions")
OBSERVER_WRITES = tuple(name for name, _ in TABLES if name != "ggwp_obs_decisions")
OBSERVER_CANDIDATE_COLUMNS = ("id", "trends_set_id", "gsc_set_id", "created_at")
OBSERVER_MIRROR = ("pick_mirror.versions", "pick_mirror.series")


def upgrade():
    bind = op.get_bind()
    postgres = bind.dialect.name == "postgresql"
    # Checked before any DDL: a bad name must stop the start, not surface later as a role that can do nothing. SQLite has no
    # roles, so neither variable is read there.
    roles = (_reader_role(), _observer_role()) if postgres else None
    _create_tables()
    for table, name, type_ in COLUMNS:
        if name not in _column_names(table):
            op.add_column(table, sa.Column(name, type_, nullable=True))
    for name, target in CANDIDATE_INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {target}")
    # D34: the two runtime rows exist from the start; a rerun never resets one a collector has written.
    op.execute(f"INSERT INTO ggwp_obs_runtime (channel) VALUES {RUNTIME_ROWS} ON CONFLICT DO NOTHING")
    if postgres:
        _pick_obs(bind, *roles)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for view in reversed(VIEW_NAMES):
            op.execute(f"DROP VIEW IF EXISTS {view}")
    # The indexes name 0007's columns; SQLite's batch copy of the table would carry them over a dropped column.
    for name, _ in CANDIDATE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
    _drop_columns("ggwp_candidate_sets", [name for _, name, _ in COLUMNS])
    for name, _ in reversed(TABLES):
        if sa.inspect(bind).has_table(name):
            op.drop_table(name)


def _create_tables() -> None:
    for name, columns in TABLES:
        if not sa.inspect(op.get_bind()).has_table(name):
            op.create_table(name, *columns(), sqlite_autoincrement=name in SERIAL_TABLES)
    for kind, name, target in INDEXES:
        op.execute(f"CREATE {kind} IF NOT EXISTS {name} ON {target}")


def _drop_columns(table: str, names: list[str]) -> None:
    present = _column_names(table)
    doomed = [name for name in names if name in present]
    if not doomed:
        return
    # SQLite drops a column by copying the table; batch mode does the copy, PostgreSQL gets a plain ALTER TABLE.
    with op.batch_alter_table(table) as batch:
        for name in doomed:
            batch.drop_column(name)


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


# ---- roles and grants (PostgreSQL) --------------------------------------------------------------------------------


def _role_from(env: str, default: str) -> str:
    # An empty value reads as unset, like the other deployment variables; the message names the variable, never the value.
    role = os.environ.get(env) or default
    if not _ROLE_NAME.fullmatch(role):
        raise ValueError(f"{env} 必须是小写字母、数字、下划线组成的角色名：以字母或下划线开头，不超过 63 个字符")
    return role


def _reader_role() -> str:
    return _role_from(READER_ROLE_ENV, DEFAULT_READER_ROLE)


def _observer_role() -> str:
    return _role_from(OBSERVER_ROLE_ENV, DEFAULT_OBSERVER_ROLE)


def _role_exists(bind, role: str) -> bool:
    return bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first() is not None


def _pick_obs(bind, reader: str, observer: str) -> None:
    quote = bind.dialect.identifier_preparer.quote
    schema = quote(bind.execute(sa.text("SELECT current_schema()")).scalar())
    closed = ", ".join(["PUBLIC", *(role for role in API_ROLES if _role_exists(bind, role))])
    op.execute("CREATE SCHEMA IF NOT EXISTS pick_obs")
    op.execute(f"REVOKE ALL ON SCHEMA pick_obs FROM {closed}")
    for name, body in VIEWS:
        op.execute(f"CREATE OR REPLACE VIEW pick_obs.{name} AS {body.format(s=schema)}")
    # Whatever default privileges the schema came with, the views are closed to everyone but the reader.
    op.execute(f"REVOKE ALL ON {', '.join(VIEW_NAMES)} FROM {closed}")
    _grant_reader(bind, reader)
    _grant_observer(bind, observer, schema)


def _grant_reader(bind, role: str) -> None:
    # GRANT to a missing role fails the statement and so the whole migration transaction: skip it instead.
    if not _role_exists(bind, role):
        logger.warning("[pick-obs] the role %s names does not exist; pick_obs read grants skipped", READER_ROLE_ENV)
        return
    quoted = bind.dialect.identifier_preparer.quote(role)
    op.execute(f"GRANT USAGE ON SCHEMA pick_obs TO {quoted}")
    op.execute(f"GRANT SELECT ON {', '.join(VIEW_NAMES)} TO {quoted}")


def _grant_observer(bind, role: str, schema: str) -> None:
    if not _role_exists(bind, role):
        logger.warning("[pick-obs] the role %s names does not exist; observer table grants skipped", OBSERVER_ROLE_ENV)
        return
    quoted = bind.dialect.identifier_preparer.quote(role)

    def tables(names) -> str:
        return ", ".join(f"{schema}.{name}" for name in names)

    op.execute(f"GRANT USAGE ON SCHEMA {schema} TO {quoted}")
    op.execute(f"GRANT SELECT ON {tables(OBSERVER_READS)} TO {quoted}")
    op.execute(f"GRANT SELECT ({', '.join(OBSERVER_CANDIDATE_COLUMNS)}) ON {schema}.ggwp_candidate_sets TO {quoted}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables(OBSERVER_WRITES)} TO {quoted}")
    for name in (name for name in OBSERVER_WRITES if name in SERIAL_TABLES):
        # The catalog's own spelling of the sequence behind the id, quoted where it needs to be.
        sequence = bind.execute(sa.text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": f"{schema}.{name}"}).scalar()
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {sequence} TO {quoted}")
    op.execute(f"GRANT USAGE ON SCHEMA pick_mirror TO {quoted}")
    op.execute(f"GRANT SELECT ON {', '.join(OBSERVER_MIRROR)} TO {quoted}")
