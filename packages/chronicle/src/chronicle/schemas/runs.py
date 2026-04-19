"""Pydantic schemas for run query endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, computed_field

from chronicle.schemas.common import PagedResponse


class RunSummary(BaseModel):
    """One row in the run list (GET /api/v1/runs)."""

    model_config = {"from_attributes": True}

    id: UUID
    tenant_slug: str
    started_at: datetime
    finished_at: datetime
    duration_ms: float
    environment: str | None
    transport: str
    branch: str | None
    git_sha: str | None
    project_name: str | None
    total_tests: int
    total_passed: int
    total_failed: int
    total_errored: int
    total_skipped: int
    total_slow: int
    anomaly_count: int = 0

    # PRD-010 additions — denormalised stats from ingest
    topic_count: int = 0
    p50_ms: float | None = None
    p95_ms: float | None = None
    p99_ms: float | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float:
        """Fraction of tests that passed (0.0-1.0). Used for sparklines."""
        if self.total_tests == 0:
            return 0.0
        return self.total_passed / self.total_tests


class ScenarioSummary(BaseModel):
    """Scenario within a run detail response."""

    model_config = {"from_attributes": True}

    id: UUID
    test_nodeid: str
    name: str
    correlation_id: str | None
    outcome: str
    duration_ms: float
    completed_normally: bool
    handle_count: int = 0


class RunDetail(RunSummary):
    """Full run detail (GET /api/v1/runs/{run_id})."""

    scenarios: list[ScenarioSummary]


class RunListResponse(PagedResponse[RunSummary]):
    """Paginated list of runs."""

    pass


class TenantSummary(BaseModel):
    """One row in the tenant list (GET /api/v1/tenants)."""

    model_config = {"from_attributes": True}

    id: UUID
    slug: str
    name: str
    created_at: datetime
    run_count: int = 0


class TenantListResponse(PagedResponse[TenantSummary]):
    """Paginated list of tenants."""

    pass
