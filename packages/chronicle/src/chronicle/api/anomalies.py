"""Route handlers for anomaly endpoints.

GET /api/v1/anomalies                    -- anomaly feed for a tenant.
GET /api/v1/anomalies/topics/{topic}     -- anomaly history for a topic.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from chronicle.dependencies import get_anomaly_repo, get_tenant_or_404
from chronicle.models.tables import Tenant
from chronicle.repositories.anomaly_repo import AnomalyRepository
from chronicle.schemas.anomalies import AnomalyCard, AnomalyListResponse

router = APIRouter(tags=["anomalies"])


@router.get("/anomalies", response_model=AnomalyListResponse)
async def list_anomalies(
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    repo: Annotated[AnomalyRepository, Depends(get_anomaly_repo)],
    topic: Annotated[str | None, Query(max_length=256)] = None,
    severity: Annotated[str | None, Query(pattern=r"^(warning|critical)$")] = None,
    resolved: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> AnomalyListResponse:
    """List anomalies for a tenant, newest first."""
    anomalies, total = await repo.list_anomalies(
        tenant_row.id,
        topic=topic,
        severity=severity,
        resolved=resolved,
        limit=limit,
        offset=offset,
    )
    return AnomalyListResponse(
        items=[AnomalyCard.model_validate(a) for a in anomalies],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/anomalies/topics/{topic}", response_model=AnomalyListResponse)
async def list_anomalies_for_topic(
    topic: Annotated[str, Path(max_length=256)],
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    repo: Annotated[AnomalyRepository, Depends(get_anomaly_repo)],
    severity: Annotated[str | None, Query(pattern=r"^(warning|critical)$")] = None,
    resolved: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> AnomalyListResponse:
    """List anomalies for a specific topic."""
    anomalies, total = await repo.list_anomalies(
        tenant_row.id,
        topic=topic,
        severity=severity,
        resolved=resolved,
        limit=limit,
        offset=offset,
    )
    return AnomalyListResponse(
        items=[AnomalyCard.model_validate(a) for a in anomalies],
        total=total,
        limit=limit,
        offset=offset,
    )
