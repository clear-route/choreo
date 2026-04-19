"""Pydantic schemas for topic query endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from chronicle.schemas.common import PagedResponse


class TopicSummary(BaseModel):
    """One row in the topic list (GET /api/v1/topics)."""

    topic: str
    latest_run_at: datetime
    sample_count: int = 0
    latest_p50_ms: float | None = None
    latest_p95_ms: float | None = None
    latest_p99_ms: float | None = None
    slow_count: int = 0
    timeout_count: int = 0


class TopicListResponse(PagedResponse[TopicSummary]):
    """Paginated list of topics."""

    pass


class LatencyBucket(BaseModel):
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


class TopicLatencyResponse(BaseModel):
    """Time-series latency data (GET /api/v1/topics/{topic}/latency)."""

    topic: str
    tenant: str
    environment: str | None
    resolution: str  # "raw", "hourly", "daily"
    buckets: list[LatencyBucket]


class TopicRunSummary(BaseModel):
    """One row in the topic-runs list (GET /api/v1/topics/{topic}/runs)."""

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


class TopicRunListResponse(PagedResponse[TopicRunSummary]):
    """Paginated list of runs for a specific topic."""

    pass
