from alembic import context

# The caller supplies a connection; no DSN/config file/logging setup is needed here.
connection = context.config.attributes["connection"]
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()
