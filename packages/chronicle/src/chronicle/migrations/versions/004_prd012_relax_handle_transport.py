"""PRD-012 follow-up: relax handle_measurements.transport NOT NULL.

Mixed-mode runs (single-Harness scenarios + Stage scenarios in the
same pytest session) emit `run.transport: null` plus a `run.transports`
array. Single-Harness handles in such a run carry no per-handle
transport and no run-level fallback either, so the NOT-NULL constraint
on `handle_measurements.transport` rejects the COPY.

Stage handles always have a transport. Single-Harness-only runs always
have a run-level transport. The only NULL case is a single-Harness
handle inside a mixed-mode run, where neither side has a value to
contribute. NULL is the honest representation; continuous aggregates
GROUP BY transport already tolerate NULL as its own bucket.

Revision ID: 004
Revises: 003
Create Date: 2026-05-04 17:15:00
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("handle_measurements", "transport", nullable=True)


def downgrade() -> None:
    # Re-tighten NOT NULL. Fails if any mixed-mode single-Harness
    # handles landed with transport=NULL after this migration. The
    # rollback path is to either delete those rows or backfill them
    # from the corresponding run's `transports[0]`.
    op.alter_column("handle_measurements", "transport", nullable=False)
