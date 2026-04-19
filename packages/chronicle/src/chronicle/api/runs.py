"""Route handlers for run endpoints.

POST /api/v1/runs          -- ingest a test-report-v1 JSON report.
GET  /api/v1/runs          -- list runs for a tenant.
GET  /api/v1/runs/{run_id} -- run detail with scenarios.
GET  /api/v1/runs/{run_id}/raw -- raw JSON as originally ingested.
"""

from __future__ import annotations

import re
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from starlette.responses import JSONResponse

from chronicle.dependencies import get_ingest_service, get_run_repo, get_tenant_or_404
from chronicle.models.tables import Tenant
from chronicle.repositories.run_repo import RunRepository
from chronicle.schemas.ingest import IngestRequest, IngestResponse
from chronicle.schemas.runs import RunDetail, RunListResponse, RunSummary, ScenarioSummary
from chronicle.services.ingest_service import IngestService

router = APIRouter(tags=["runs"])

_TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    environment: Annotated[str | None, Query(max_length=64)] = None,
    branch: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=10000)] = 0,
) -> RunListResponse:
    """List runs for a tenant, newest first."""
    runs, total = await run_repo.list_runs(
        tenant_row.id,
        environment=environment,
        branch=branch,
        limit=limit,
        offset=offset,
    )

    return RunListResponse(
        items=[
            RunSummary(
                id=r.id,
                tenant_slug=tenant_row.slug,
                started_at=r.started_at,
                finished_at=r.finished_at,
                duration_ms=r.duration_ms,
                environment=r.environment,
                transport=r.transport,
                branch=r.branch,
                git_sha=r.git_sha,
                project_name=r.project_name,
                total_tests=r.total_tests,
                total_passed=r.total_passed,
                total_failed=r.total_failed,
                total_errored=r.total_errored,
                total_skipped=r.total_skipped,
                total_slow=r.total_slow,
            )
            for r in runs
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/runs", status_code=201, response_model=IngestResponse)
async def ingest_run(
    body: IngestRequest,
    x_chronicle_tenant: Annotated[str, Header()],
    service: Annotated[IngestService, Depends(get_ingest_service)],
    idempotency_key: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    """Ingest a test-report-v1 JSON report."""
    tenant_slug = x_chronicle_tenant.lower()
    if not _TENANT_SLUG_RE.match(tenant_slug):
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tenant slug: '{tenant_slug}'. "
            "Must match ^[a-z0-9][a-z0-9-]{{0,62}}[a-z0-9]$",
        )

    return await service.ingest(body, tenant_slug, idempotency_key)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: UUID,
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
) -> RunDetail:
    """Return run detail including scenarios."""
    run = await run_repo.get_run_with_scenarios(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    # Count handles per scenario from raw report
    raw = run.raw_report
    handle_counts: dict[str, int] = {}
    for test in raw.get("tests", []):
        for s in test.get("scenarios", []):
            handle_counts[s["name"]] = len(s.get("handles", []))

    return RunDetail(
        id=run.id,
        tenant_slug=run.tenant.slug if run.tenant else "",
        started_at=run.started_at,
        finished_at=run.finished_at,
        duration_ms=run.duration_ms,
        environment=run.environment,
        transport=run.transport,
        branch=run.branch,
        git_sha=run.git_sha,
        project_name=run.project_name,
        total_tests=run.total_tests,
        total_passed=run.total_passed,
        total_failed=run.total_failed,
        total_errored=run.total_errored,
        total_skipped=run.total_skipped,
        total_slow=run.total_slow,
        scenarios=[
            ScenarioSummary(
                id=s.id,
                test_nodeid=s.test_nodeid,
                name=s.name,
                correlation_id=s.correlation_id,
                outcome=s.outcome,
                duration_ms=s.duration_ms,
                completed_normally=s.completed_normally,
                handle_count=handle_counts.get(s.name, 0),
            )
            for s in run.scenarios
        ],
    )


@router.get("/runs/{run_id}/raw", response_model=None)
async def get_run_raw(
    run_id: UUID,
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
) -> JSONResponse:
    """Return the original test-report-v1 JSON exactly as ingested."""
    raw = await run_repo.get_run_raw_report(run_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="Run not found.")

    return JSONResponse(content=raw)
