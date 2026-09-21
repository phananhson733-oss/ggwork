"""Private ggwp_ migration chain: never target DeerFlow metadata."""

from alembic import context

connection = context.config.attributes["connection"]
context.configure(connection=connection, version_table="ggwp_alembic_version", render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
