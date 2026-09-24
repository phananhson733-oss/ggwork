"""Extension-private SQLAlchemy tables. Never register with the host metadata."""

from sqlalchemy import JSON, Column, Integer, MetaData, String, Table, Text

metadata = MetaData()

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
