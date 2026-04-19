"""Route handlers for tenant endpoints.

GET /api/v1/tenants -- list all tenants with run counts.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from chronicle.dependencies import get_run_repo
from chronicle.repositories.run_repo import RunRepository
from chronicle.schemas.runs import TenantListResponse, TenantSummary

router = APIRouter(tags=["tenants"])


@router.get("/tenants", response_model=TenantListResponse)
async def list_tenants(
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TenantListResponse:
    """List all tenants with run counts, newest first."""
    tenants, total = await run_repo.list_tenants(limit=limit, offset=offset)

    return TenantListResponse(
        items=[
            TenantSummary(
                id=t.id,
                slug=t.slug,
                name=t.name,
                created_at=t.created_at,
                run_count=t.run_count,
            )
            for t in tenants
        ],
        total=total,
        limit=limit,
        offset=offset,
    )
