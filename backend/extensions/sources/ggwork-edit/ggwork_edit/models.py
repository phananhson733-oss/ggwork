"""Private editing metadata: never added to host metadata."""

from sqlalchemy import JSON, Column, MetaData, String, Table

metadata = MetaData()
records = Table(
    "ggwe_records",
    metadata,
    Column("owner_id", String(128), primary_key=True),
    Column("kind", String(16), primary_key=True),
    Column("id", String(128), primary_key=True),
    Column("data", JSON, nullable=False),
)
credentials = Table(
    "ggwe_credentials",
    metadata,
    Column("digest", String(64), primary_key=True),
    Column("owner_id", String(128), nullable=False),
    Column("device_id", String(128), nullable=False),
)
