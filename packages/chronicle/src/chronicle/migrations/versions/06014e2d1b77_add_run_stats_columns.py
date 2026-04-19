"""add run stats columns

Revision ID: 06014e2d1b77
Revises: 001
Create Date: 2026-04-19 16:40:57.509587
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "06014e2d1b77"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("runs", sa.Column("topic_count", sa.Integer(), nullable=True))
    op.add_column("runs", sa.Column("p50_ms", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("p95_ms", sa.Float(), nullable=True))
    op.add_column("runs", sa.Column("p99_ms", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("runs", "p99_ms")
    op.drop_column("runs", "p95_ms")
    op.drop_column("runs", "p50_ms")
    op.drop_column("runs", "topic_count")
