""": relax runs.transport NOT NULL; add runs.transports.

 §1.5 / §2.5: Stage-only and mixed-mode test runs emit
`run.transport: null` and a `run.transports: [...]` array. Chronicle's
runs.transport NOT NULL constraint would reject these; the migration
relaxes the constraint and adds the new array column.

Per-handle `handle_measurements.transport` already exists and stays
NOT NULL — the repository writes per-handle transport for Stage
handles (preferred over the run-level value).

Revision ID: 003
Revises: 002
Create Date: 2026-05-04 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Relax NOT NULL on runs.transport — Stage runs emit null.
    op.alter_column("runs", "transport", nullable=True)
    # Add runs.transports — sorted union of transports for any run
    # containing a Stage scenario; null for single-Harness-only runs.
    op.add_column(
        "runs",
        sa.Column("transports", sa.ARRAY(sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("runs", "transports")
    # Re-tighten NOT NULL. This may fail if any v1.1 Stage runs landed
    # with transport=NULL; the rollback path is to first delete those
    # rows or backfill from runs.transports.
    op.alter_column("runs", "transport", nullable=False)
