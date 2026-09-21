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
