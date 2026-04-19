"""Repository for topic-level latency queries.

Uses raw SQL via ``text()`` for all queries (ADR-0025 Decision 1).
TimescaleDB-specific functions (``time_bucket``, ``percentile_cont``)
and continuous aggregate views are not ORM-mappable, so raw SQL is
the pragmatic choice.  All user-supplied values are bound parameters.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ── Raw bucket interval (ADR-0025 Decision 2) ──

# SECURITY: constant only — inlined into SQL f-string. Never from user input.
_RAW_BUCKET_INTERVAL = "15 minutes"

# ── Aggregate view safelist (ADR-0025 §Security) ──

_AGGREGATE_VIEWS: dict[str, str] = {
    "hourly": "topic_latency_hourly",
    "daily": "topic_latency_daily",
}

# ── Row types ──


class LatencyBucketRow(NamedTuple):
    """One time bucket in a topic latency time-series."""

    bucket: datetime
    sample_count: int
    avg_ms: float | None
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    min_ms: float | None
    max_ms: float | None
    slow_count: int
    timeout_count: int
    fail_count: int
    budget_violation_count: int


class TopicSummaryRow(NamedTuple):
    """One row in the topic list."""

    topic: str
    latest_run_at: datetime
    sample_count: int = 0
    latest_p50_ms: float | None = None
    latest_p95_ms: float | None = None
    latest_p99_ms: float | None = None
    slow_count: int = 0
    timeout_count: int = 0


class TopicRunSummaryRow(NamedTuple):
    """One row in the topic-runs list."""

    run_id: UUID
    started_at: datetime
    environment: str | None
    branch: str | None
    handle_count: int
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    slow_count: int
    timeout_count: int


# ── SQL templates ──

_RAW_QUERY = text(f"""
    SELECT
        time_bucket('{_RAW_BUCKET_INTERVAL}', time) AS bucket,
        count(*)                     AS sample_count,
        avg(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_ms,
        percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
        percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
        percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
            FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
        min(latency_ms)              AS min_ms,
        max(latency_ms)              AS max_ms,
        count(*) FILTER (WHERE outcome = 'slow')    AS slow_count,
        count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count,
        count(*) FILTER (WHERE outcome = 'fail')    AS fail_count,
        count(*) FILTER (WHERE over_budget)          AS budget_violation_count
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND topic = :topic
      AND time >= :start AND time < :end
      AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
    GROUP BY bucket
    ORDER BY bucket
""")

_TOPIC_LIST_QUERY = text("""
    WITH stats AS (
        SELECT topic,
               count(*) AS sample_count,
               percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
                   FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
               percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                   FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
               percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
                   FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
               count(*) FILTER (WHERE outcome = 'slow') AS slow_count,
               count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count
        FROM handle_measurements
        WHERE tenant_id = :tenant_id
          AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
        GROUP BY topic
    )
    SELECT t.name AS topic,
           t.last_seen_at AS latest_run_at,
           COALESCE(s.sample_count, 0) AS sample_count,
           s.p50_ms AS latest_p50_ms,
           s.p95_ms AS latest_p95_ms,
           s.p99_ms AS latest_p99_ms,
           COALESCE(s.slow_count, 0) AS slow_count,
           COALESCE(s.timeout_count, 0) AS timeout_count
    FROM topics t
    LEFT JOIN stats s ON t.name = s.topic
    WHERE t.tenant_id = :tenant_id
    ORDER BY t.last_seen_at DESC
    LIMIT :limit OFFSET :offset
""")

_TOPIC_COUNT_QUERY = text("""
    SELECT count(*)
    FROM topics
    WHERE tenant_id = :tenant_id
""")

_TOPIC_RUNS_QUERY = text("""
    WITH run_stats AS (
        SELECT
            run_id,
            count(*) AS handle_count,
            percentile_cont(0.50) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p50_ms,
            percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p95_ms,
            percentile_cont(0.99) WITHIN GROUP (ORDER BY latency_ms)
                FILTER (WHERE latency_ms IS NOT NULL) AS p99_ms,
            count(*) FILTER (WHERE outcome = 'slow') AS slow_count,
            count(*) FILTER (WHERE outcome = 'timeout') AS timeout_count
        FROM handle_measurements
        WHERE tenant_id = :tenant_id
          AND topic = :topic
          AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
        GROUP BY run_id
    )
    SELECT
        rs.run_id, r.started_at, r.environment, r.branch,
        rs.handle_count, rs.p50_ms, rs.p95_ms, rs.p99_ms,
        rs.slow_count, rs.timeout_count
    FROM run_stats rs
    JOIN runs r ON r.id = rs.run_id
    ORDER BY r.started_at DESC
    LIMIT :limit OFFSET :offset
""")

_TOPIC_RUNS_COUNT_QUERY = text("""
    SELECT count(DISTINCT run_id)
    FROM handle_measurements
    WHERE tenant_id = :tenant_id
      AND topic = :topic
      AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
""")


class TopicRepository:
    """Database access for topic-level queries.

    All queries use raw SQL via ``text()`` with named bind parameters
    (ADR-0025 Decision 1).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_topics(
        self,
        tenant_id: UUID,
        *,
        environment: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TopicSummaryRow], int]:
        """Return distinct topics for a tenant with latest stats.

        Uses a CTE joining ``topic_latency_hourly`` (for percentiles)
        with ``handle_measurements`` (for run counts).
        """
        params = {
            "tenant_id": tenant_id,
            "environment": environment,
            "limit": limit,
            "offset": offset,
        }

        count_result = await self.session.execute(_TOPIC_COUNT_QUERY, params)
        total = count_result.scalar_one()

        result = await self.session.execute(_TOPIC_LIST_QUERY, params)
        rows = [TopicSummaryRow(*row) for row in result.fetchall()]

        return rows, total

    async def query_raw(
        self,
        topic: str,
        tenant_id: UUID,
        environment: str | None,
        start: datetime,
        end: datetime,
    ) -> list[LatencyBucketRow]:
        """Query raw ``handle_measurements`` aggregated into 15-minute buckets.

        Used for the last 24 hours or for partial-bucket fill.
        """
        result = await self.session.execute(
            _RAW_QUERY,
            {
                "tenant_id": tenant_id,
                "topic": topic,
                "start": start,
                "end": end,
                "environment": environment,
            },
        )
        return [LatencyBucketRow(*row) for row in result.fetchall()]

    async def query_aggregate(
        self,
        source: str,
        topic: str,
        tenant_id: UUID,
        environment: str | None,
        start: datetime,
        end: datetime,
    ) -> list[LatencyBucketRow]:
        """Query a continuous aggregate for pre-computed latency buckets.

        ``source`` must be ``"hourly"`` or ``"daily"``.  The view name
        is resolved from a safelist — never from user input.
        """
        view = _AGGREGATE_VIEWS.get(source)
        if view is None:
            raise ValueError(f"Unknown aggregate source: {source!r}")

        stmt = text(f"""
            SELECT bucket, sample_count, avg_ms,
                   p50_ms, p95_ms, p99_ms, min_ms, max_ms,
                   slow_count, timeout_count, fail_count,
                   budget_violation_count
            FROM {view}
            WHERE tenant_id = :tenant_id
              AND topic = :topic
              AND bucket >= :start AND bucket < :end
              AND (CAST(:environment AS TEXT) IS NULL OR environment = :environment)
            ORDER BY bucket
        """)
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "topic": topic,
                "start": start,
                "end": end,
                "environment": environment,
            },
        )
        return [LatencyBucketRow(*row) for row in result.fetchall()]

    async def list_topic_runs(
        self,
        topic: str,
        tenant_id: UUID,
        *,
        environment: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[TopicRunSummaryRow], int]:
        """Return recent runs containing handles on the given topic,
        with per-run latency summary.
        """
        params = {
            "tenant_id": tenant_id,
            "topic": topic,
            "environment": environment,
            "limit": limit,
            "offset": offset,
        }
        count_result = await self.session.execute(
            _TOPIC_RUNS_COUNT_QUERY,
            params,
        )
        total = count_result.scalar_one()

        result = await self.session.execute(_TOPIC_RUNS_QUERY, params)
        return [TopicRunSummaryRow(*row) for row in result.fetchall()], total
