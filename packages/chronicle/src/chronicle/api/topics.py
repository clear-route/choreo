"""Route handlers for topic endpoints.

GET /api/v1/topics                    -- list topics for a tenant.
GET /api/v1/topics/{topic}/latency    -- time-series latency data.
GET /api/v1/topics/{topic}/runs       -- recent runs containing a topic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from chronicle.dependencies import get_resolution_service, get_tenant_or_404, get_topic_repo
from chronicle.models.tables import Tenant
from chronicle.repositories.topic_repo import TopicRepository
from chronicle.schemas.topics import (
    LatencyBucket,
    TopicLatencyResponse,
    TopicListResponse,
    TopicRunListResponse,
    TopicRunSummary,
    TopicSummary,
)
from chronicle.services.resolution_service import ResolutionService

router = APIRouter(tags=["topics"])


@router.get("/topics", response_model=TopicListResponse)
async def list_topics(
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> TopicListResponse:
    """List all topics seen for a tenant, with latest latency stats."""
    topics, total = await topic_repo.list_topics(
        tenant_row.id,
        environment=environment,
        limit=limit,
        offset=offset,
    )
    return TopicListResponse(
        items=[TopicSummary(**t._asdict()) for t in topics],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/topics/{topic}/latency", response_model=TopicLatencyResponse)
async def get_topic_latency(
    topic: Annotated[str, Path(max_length=256)],
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    resolution_svc: Annotated[ResolutionService, Depends(get_resolution_service)],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query()] = None,
    resolution: Annotated[str | None, Query(pattern=r"^(raw|hourly|daily)$")] = None,
) -> TopicLatencyResponse:
    """Return time-series latency data for a specific topic."""
    end = to or datetime.now(UTC)
    start = from_ or (end - timedelta(days=7))

    max_span = timedelta(days=365)
    if (end - start) > max_span:
        raise HTTPException(
            status_code=422,
            detail="Time range exceeds maximum of 365 days.",
        )

    buckets, resolution_used = await resolution_svc.get_topic_latency(
        topic,
        tenant_row.id,
        environment,
        start,
        end,
        resolution,
    )
    return TopicLatencyResponse(
        topic=topic,
        tenant=tenant_row.slug,
        environment=environment,
        resolution=resolution_used,
        buckets=[LatencyBucket(**b._asdict()) for b in buckets],
    )


@router.get("/topics/{topic}/runs", response_model=TopicRunListResponse)
async def list_topic_runs(
    topic: Annotated[str, Path(max_length=256)],
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> TopicRunListResponse:
    """List recent runs that included handles on the given topic."""
    runs, total = await topic_repo.list_topic_runs(
        topic,
        tenant_row.id,
        environment=environment,
        limit=limit,
        offset=offset,
    )
    return TopicRunListResponse(
        items=[TopicRunSummary(**r._asdict()) for r in runs],
        total=total,
        limit=limit,
        offset=offset,
    )
