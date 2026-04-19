"""Pydantic schemas for anomaly query endpoints."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from chronicle.schemas.common import PagedResponse


class AnomalyCard(BaseModel):
    """One anomaly in the feed (GET /api/v1/anomalies)."""

    model_config = {"from_attributes": True}

    id: UUID
    tenant_id: UUID
    run_id: UUID
    detected_at: datetime
    topic: str
    detection_method: str  # "rolling_baseline", "budget_violation", "outcome_shift"
    metric: str  # "p95_ms", "budget_violation_pct", "timeout_rate"
    current_value: float
    baseline_value: float
    baseline_stddev: float
    change_pct: float
    severity: str  # "warning", "critical"
    resolved: bool
    resolved_at: datetime | None


class AnomalyListResponse(PagedResponse[AnomalyCard]):
    """Paginated list of anomalies."""

    pass
