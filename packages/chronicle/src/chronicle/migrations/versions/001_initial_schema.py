"""Initial Chronicle schema with TimescaleDB hypertable and continuous aggregates.

Revision ID: 001
Revises: None
Create Date: 2026-04-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Enable TimescaleDB extension ──
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")

    # ── Tenants ──
    op.create_table(
        "tenants",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("slug", sa.Text(), unique=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.CheckConstraint(
            r"slug ~ '^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$'",
            name="chk_slug_format",
        ),
    )

    # ── Runs ──
    op.create_table(
        "runs",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("idempotency_key", sa.Text(), unique=True),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("environment", sa.Text()),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text()),
        sa.Column("git_sha", sa.Text()),
        sa.Column("hostname", sa.Text()),
        sa.Column("harness_version", sa.Text()),
        sa.Column("reporter_version", sa.Text()),
        sa.Column("python_version", sa.Text()),
        sa.Column("project_name", sa.Text()),
        sa.Column("total_tests", sa.Integer(), nullable=False),
        sa.Column("total_passed", sa.Integer(), nullable=False),
        sa.Column("total_failed", sa.Integer(), nullable=False),
        sa.Column("total_errored", sa.Integer(), nullable=False),
        sa.Column("total_skipped", sa.Integer(), nullable=False),
        sa.Column("total_slow", sa.Integer(), nullable=False),
        sa.Column("raw_report", JSONB(), nullable=False),
    )
    op.create_index("idx_runs_tenant_time", "runs", ["tenant_id", sa.text("started_at DESC")])
    op.create_index(
        "idx_runs_environment", "runs", ["tenant_id", "environment", sa.text("started_at DESC")]
    )
    op.create_index("idx_runs_branch", "runs", ["tenant_id", "branch", sa.text("started_at DESC")])

    # ── Scenarios ──
    op.create_table(
        "scenarios",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("test_nodeid", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.Text()),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("completed_normally", sa.Boolean(), nullable=False),
    )
    op.create_index("idx_scenarios_run", "scenarios", ["run_id"])
    op.create_index("idx_scenarios_name", "scenarios", ["name", "run_id"])

    # ── Handle measurements (hypertable) ──
    op.create_table(
        "handle_measurements",
        sa.Column("time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), nullable=False),
        sa.Column("scenario_id", UUID(as_uuid=True), nullable=False),
        sa.Column("environment", sa.Text()),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text()),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Float()),
        sa.Column("budget_ms", sa.Float()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("matcher_description", sa.Text(), nullable=False),
        sa.Column("diagnosis_kind", sa.Text()),
        sa.Column("over_budget", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    # Convert to hypertable — time-only partitioning
    op.execute("SELECT create_hypertable('handle_measurements', 'time')")

    op.create_index(
        "idx_handles_topic", "handle_measurements", ["tenant_id", "topic", sa.text("time DESC")]
    )
    op.create_index(
        "idx_handles_outcome", "handle_measurements", ["tenant_id", "outcome", sa.text("time DESC")]
    )
    op.create_index(
        "idx_handles_baseline",
        "handle_measurements",
        ["tenant_id", "environment", "topic", sa.text("time DESC")],
    )

    # Compression policy
    op.execute("""
        ALTER TABLE handle_measurements SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'tenant_id, topic',
            timescaledb.compress_orderby = 'time DESC'
        )
    """)
    op.execute("SELECT add_compression_policy('handle_measurements', INTERVAL '7 days')")

    # ── Continuous aggregates ──
    # TimescaleDB continuous aggregates use CREATE MATERIALIZED VIEW which
    # cannot run inside a transaction block. We must commit the current
    # transaction first, then execute these statements outside a transaction.
    op.execute(sa.text("COMMIT"))
    op.execute("""
        CREATE MATERIALIZED VIEW topic_latency_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', time)     AS bucket,
            tenant_id,
            environment,
            transport,
            topic,
            count(*)                        AS sample_count,
            avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
            min(latency_ms)                 AS min_ms,
            max(latency_ms)                 AS max_ms,
            count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
            count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
            count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
            count(*) FILTER (WHERE over_budget)         AS budget_violation_count
        FROM handle_measurements
        GROUP BY bucket, tenant_id, environment, transport, topic
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('topic_latency_hourly',
            start_offset    => INTERVAL '3 hours',
            end_offset      => INTERVAL '30 minutes',
            schedule_interval => INTERVAL '30 minutes'
        )
    """)

    op.execute("""
        CREATE MATERIALIZED VIEW topic_latency_daily
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 day', time)      AS bucket,
            tenant_id,
            environment,
            transport,
            topic,
            count(*)                        AS sample_count,
            avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
            min(latency_ms)                 AS min_ms,
            max(latency_ms)                 AS max_ms,
            count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
            count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
            count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
            count(*) FILTER (WHERE over_budget)         AS budget_violation_count
        FROM handle_measurements
        GROUP BY bucket, tenant_id, environment, transport, topic
    """)
    op.execute("""
        SELECT add_continuous_aggregate_policy('topic_latency_daily',
            start_offset    => INTERVAL '3 days',
            end_offset      => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour'
        )
    """)

    # ── Anomalies ──
    op.create_table(
        "anomalies",
        sa.Column(
            "id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True
        ),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("detection_method", sa.Text(), nullable=False),
        sa.Column("metric", sa.Text(), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("baseline_value", sa.Float(), nullable=False),
        sa.Column("baseline_stddev", sa.Float(), nullable=False),
        sa.Column("change_pct", sa.Float(), nullable=False),
        sa.Column("severity", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "idx_anomalies_tenant_time", "anomalies", ["tenant_id", sa.text("detected_at DESC")]
    )
    op.create_index(
        "idx_anomalies_topic", "anomalies", ["tenant_id", "topic", sa.text("detected_at DESC")]
    )


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS topic_latency_daily CASCADE")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS topic_latency_hourly CASCADE")
    op.drop_table("anomalies")
    op.drop_table("handle_measurements")
    op.drop_table("scenarios")
    op.drop_table("runs")
    op.drop_table("tenants")
