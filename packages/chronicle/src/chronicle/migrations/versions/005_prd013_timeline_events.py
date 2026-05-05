"""PRD-013 §5: timeline_events hypertable for Stage timeline ingest.

Phase 3 PR 3.1. One row per TimelineEntry from the test report's
`scenario.timeline[]` array. Schema mirrors the v1.3 JSON shape:
mandatory `time` / tenant / run / scenario / action / detail / offset_ms
plus optional `topic` / `transport` / `logical_topic` / `source` (the
v1.2 + v1.3 additions).

Hypertable shape mirrors `handle_measurements` for operational
consistency: time-only partitioning at the default 7-day chunk
interval, compression policy + 7-day retention threshold, no FK to
`runs.id` / `scenarios.id` (TimescaleDB hypertables cannot reference
regular tables; integrity is enforced application-side by
`IngestService`).

Three indexes per the PRD:
  - (tenant_id, run_id, scenario_id, time DESC) — per-scope lookup
  - (tenant_id, transport, time DESC)           — per-transport rollup
  - (tenant_id, action, time DESC)              — per-action rollup

Linear migration chain: extends 004. No multi-head.

Revision ID: 005
Revises: 004
Create Date: 2026-05-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "timeline_events",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        # No FK to runs.id / scenarios.id: TimescaleDB hypertables cannot
        # reference regular tables. Application-side integrity is enforced
        # by `IngestService` (run + scenarios are inserted in the same
        # transaction as the timeline rows).
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("offset_ms", sa.Float(), nullable=False),
        sa.Column("topic", sa.Text()),
        sa.Column("transport", sa.Text()),
        sa.Column("logical_topic", sa.Text()),
        sa.Column("source", sa.Text()),
    )

    # Default chunk interval (7 days). At cap-saturated workload
    # (50k events/run × ~100 runs/day = 5M rows/day), 7-day chunks
    # produce ~35M rows/chunk — comfortably within Timescale's
    # planner sweet spot (target <100M rows/chunk). Smaller intervals
    # generate more chunks for typical workloads (~100k rows/day),
    # adding planner overhead without real benefit.
    op.execute("SELECT create_hypertable('timeline_events', 'time')")

    op.create_index(
        "idx_timeline_events_scope",
        "timeline_events",
        ["tenant_id", "run_id", "scenario_id", sa.text("time DESC")],
    )
    op.create_index(
        "idx_timeline_events_transport",
        "timeline_events",
        ["tenant_id", "transport", sa.text("time DESC")],
    )
    op.create_index(
        "idx_timeline_events_action",
        "timeline_events",
        ["tenant_id", "action", sa.text("time DESC")],
    )

    # Compression: segment by scope (tenant/run/scenario) so chunk-skip
    # locality matches the dominant per-scope read pattern. Order by
    # `time DESC, offset_ms ASC` so within a chunk-segment the rows
    # land in observation order. Mirrors the `handle_measurements`
    # precedent (001:137-144).
    op.execute("""
        ALTER TABLE timeline_events SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tenant_id, run_id, scenario_id',
            timescaledb.compress_orderby = 'time DESC, offset_ms ASC'
        )
    """)
    op.execute(
        "SELECT add_compression_policy('timeline_events', INTERVAL '7 days')"
    )

    # Retention: drop chunks older than 90 days. Aligns with the
    # implicit run-retention horizon and avoids unbounded storage
    # growth from orphan timeline_events when runs are deleted at
    # the application layer (no FK cascade since hypertables cannot
    # reference regular tables).
    op.execute(
        "SELECT add_retention_policy('timeline_events', INTERVAL '90 days')"
    )


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('timeline_events')")
    op.execute("SELECT remove_compression_policy('timeline_events')")
    op.drop_index("idx_timeline_events_action", table_name="timeline_events")
    op.drop_index("idx_timeline_events_transport", table_name="timeline_events")
    op.drop_index("idx_timeline_events_scope", table_name="timeline_events")
    op.drop_table("timeline_events")
