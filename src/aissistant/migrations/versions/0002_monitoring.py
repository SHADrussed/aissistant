"""Add priorities, peer metadata and durable conservative API accounting."""

import sqlalchemy as sa
from alembic import op

revision = "0002_monitoring"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "outbox", sa.Column("priority", sa.String(16), nullable=False, server_default="important")
    )
    op.create_table(
        "telegram_peers",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("access_hash", sa.String(32), nullable=False),
        sa.Column("chat_type", sa.String(16), nullable=False),
        sa.Column("baseline", sa.String(32), nullable=False),
    )
    op.create_table(
        "api_usage",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("requests", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.BigInteger, nullable=False),
        sa.Column("output_tokens", sa.BigInteger, nullable=False),
        sa.Column("reserved_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("actual_micro_usd", sa.BigInteger, nullable=False),
    )


def downgrade():
    raise RuntimeError("Destructive downgrade is disabled; restore a verified backup")
