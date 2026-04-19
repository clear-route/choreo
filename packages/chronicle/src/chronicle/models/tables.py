"""SQLAlchemy 2.0 mapped classes for Chronicle's relational schema.

These models serve two purposes:

1. **Alembic auto-generation** — Alembic compares these models against the
   live database to generate migration stubs.
2. **Read-path queries** — repositories use these for SELECT queries via
   SQLAlchemy's expression language.

Write-path for ``handle_measurements`` uses asyncpg's COPY protocol directly,
bypassing the ORM (see ``RunRepository.copy_handle_measurements``).

TimescaleDB-specific DDL (hypertables, continuous aggregates, compression
policies) is not represented here — it lives in Alembic migration scripts
as explicit ``op.execute()`` calls.
"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared base for all Chronicle ORM models."""

    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    slug: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            r"slug ~ '^[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]$'",
            name="chk_slug_format",
        ),
    )

    runs: Mapped[list["Run"]] = relationship(back_populates="tenant")
    topics: Mapped[list["Topic"]] = relationship(back_populates="tenant")


class Topic(Base):
    """Known topics per tenant, populated during ingest."""

    __tablename__ = "topics"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="topics")

    __table_args__ = (Index("idx_topics_tenant_name", "tenant_id", "name", unique=True),)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    idempotency_key: Mapped[str | None] = mapped_column(Text, unique=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    # From run metadata
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    environment: Mapped[str | None] = mapped_column(Text)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str | None] = mapped_column(Text)
    git_sha: Mapped[str | None] = mapped_column(Text)
    hostname: Mapped[str | None] = mapped_column(Text)
    harness_version: Mapped[str | None] = mapped_column(Text)
    reporter_version: Mapped[str | None] = mapped_column(Text)
    python_version: Mapped[str | None] = mapped_column(Text)
    project_name: Mapped[str | None] = mapped_column(Text)

    # Totals (denormalised for list queries)
    total_tests: Mapped[int] = mapped_column(Integer, nullable=False)
    total_passed: Mapped[int] = mapped_column(Integer, nullable=False)
    total_failed: Mapped[int] = mapped_column(Integer, nullable=False)
    total_errored: Mapped[int] = mapped_column(Integer, nullable=False)
    total_skipped: Mapped[int] = mapped_column(Integer, nullable=False)
    total_slow: Mapped[int] = mapped_column(Integer, nullable=False)

    # Denormalised stats (populated during ingest — PRD-010)
    topic_count: Mapped[int | None] = mapped_column(Integer)
    p50_ms: Mapped[float | None] = mapped_column(Float)
    p95_ms: Mapped[float | None] = mapped_column(Float)
    p99_ms: Mapped[float | None] = mapped_column(Float)

    # Raw JSON — system of record
    raw_report: Mapped[dict] = mapped_column(JSONB, nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="runs")
    scenarios: Mapped[list["Scenario"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_runs_tenant_time", "tenant_id", started_at.desc()),
        Index("idx_runs_environment", "tenant_id", "environment", started_at.desc()),
        Index("idx_runs_branch", "tenant_id", "branch", started_at.desc()),
        # idempotency_key uniqueness is handled by unique=True on the column.
        # No separate UniqueConstraint needed — PostgreSQL treats NULLs as
        # distinct in unique constraints, which is the desired behaviour.
    )


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_nodeid: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    completed_normally: Mapped[bool] = mapped_column(Boolean, nullable=False)

    run: Mapped["Run"] = relationship(back_populates="scenarios")

    __table_args__ = (
        Index("idx_scenarios_run", "run_id"),
        Index("idx_scenarios_name", "name", "run_id"),
    )


class HandleMeasurement(Base):
    """ORM model for the ``handle_measurements`` hypertable.

    Used for:
    - Alembic migration auto-generation (table structure).
    - Read-path queries (SELECT via SQLAlchemy expression language).

    **Not used for writes.** The ingest path uses asyncpg's COPY protocol
    for bulk insertion (see ``RunRepository.copy_handle_measurements``).
    """

    __tablename__ = "handle_measurements"

    # Composite primary key is not enforced — TimescaleDB hypertables
    # do not support unique constraints across chunks. We use a surrogate
    # column-less approach; rows are identified by (time, run_id, scenario_id, topic).
    time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, nullable=False
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, nullable=False)
    run_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    scenario_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    environment: Mapped[str | None] = mapped_column(Text)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    branch: Mapped[str | None] = mapped_column(Text)

    topic: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    budget_ms: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    matcher_description: Mapped[str] = mapped_column(Text, nullable=False)
    diagnosis_kind: Mapped[str | None] = mapped_column(Text)
    over_budget: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (
        Index("idx_handles_topic", "tenant_id", "topic", time.desc()),
        Index("idx_handles_outcome", "tenant_id", "outcome", time.desc()),
        Index(
            "idx_handles_baseline",
            "tenant_id",
            "environment",
            "topic",
            time.desc(),
        ),
    )


class Anomaly(Base):
    __tablename__ = "anomalies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("runs.id"), nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    detection_method: Mapped[str] = mapped_column(Text, nullable=False)
    metric: Mapped[str] = mapped_column(Text, nullable=False)
    current_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_value: Mapped[float] = mapped_column(Float, nullable=False)
    baseline_stddev: Mapped[float] = mapped_column(Float, nullable=False)
    change_pct: Mapped[float] = mapped_column(Float, nullable=False)
    severity: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_anomalies_tenant_time", "tenant_id", detected_at.desc()),
        Index("idx_anomalies_topic", "tenant_id", "topic", detected_at.desc()),
    )
