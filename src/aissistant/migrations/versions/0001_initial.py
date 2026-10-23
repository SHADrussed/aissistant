"""Create v1, or validate and adopt the original unversioned MVP without changing data."""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def schema():
    # Immutable migration definition: never import mutable application metadata here.
    meta = sa.MetaData()
    sa.Table(
        "cursors",
        meta,
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("value", sa.Text),
    )
    sa.Table("seen", meta, sa.Column("key", sa.String(64), primary_key=True))
    sa.Table(
        "outbox",
        meta,
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("payload", sa.Text),
        sa.Column("created", sa.Float),
        sa.Column("delivered", sa.Float, nullable=True),
    )
    return meta


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    expected = schema()
    present = set(inspector.get_table_names())
    app_tables = set(expected.tables)
    if present.intersection(app_tables):
        if not app_tables.issubset(present):
            raise RuntimeError("Incomplete legacy schema; restore or migrate manually")
        for table in expected.sorted_tables:
            actual = {c["name"]: c for c in inspector.get_columns(table.name)}
            if set(actual) != set(table.columns.keys()):
                raise RuntimeError("Unrecognized legacy columns")
            for column in table.columns:
                observed = actual[column.name]
                family = sa.Text if isinstance(column.type, sa.Text) else type(column.type)
                if not isinstance(observed["type"], family):
                    raise RuntimeError("Unrecognized legacy column type")
                if isinstance(column.type, sa.String) and not isinstance(column.type, sa.Text):
                    if observed["type"].length != column.type.length:
                        raise RuntimeError("Unrecognized legacy string size")
                if observed["nullable"] != column.nullable:
                    raise RuntimeError("Unrecognized legacy nullability")
            if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["key"]:
                raise RuntimeError("Unrecognized legacy primary key")
        return  # Adoption records only the revision, never rewrites existing data.
    expected.create_all(bind)


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup")
