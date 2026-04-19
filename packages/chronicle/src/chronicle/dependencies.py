"""FastAPI dependency providers for request-scoped resources."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from chronicle.broadcast import BroadcastChannel
from chronicle.config import Settings
from chronicle.models.tables import Tenant
from chronicle.repositories.anomaly_repo import AnomalyRepository
from chronicle.repositories.run_repo import RunRepository
from chronicle.repositories.topic_repo import TopicRepository
from chronicle.services.detection_service import DetectionService
from chronicle.services.ingest_service import IngestService
from chronicle.services.resolution_service import ResolutionService


def create_engine_and_sessionmaker(
    settings: Settings,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Build the SQLAlchemy async engine and session factory.

    Called once during application lifespan startup.
    """
    engine = create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_max - settings.db_pool_size,
        pool_timeout=settings.db_pool_timeout,
        echo=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


async def get_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a request-scoped database session.

    The session is checked out from the pool on entry and returned on
    exit.  Route handlers should **not** call ``session.commit()``
    directly -- services own transaction boundaries via
    ``session.begin()``.

    **SSE route handlers must NOT depend on this provider.**  A long-lived
    SSE connection would hold the session (and its pool slot) open
    indefinitely.  See ADR-0021 SSE Broadcast Channel.
    """
    async with request.app.state.sessionmaker() as session:
        yield session


async def get_run_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunRepository:
    """Request-scoped run repository."""
    return RunRepository(session)


def get_detection_service(request: Request) -> DetectionService:
    """Detection service configured from application settings."""
    from chronicle.services.detection_service import DetectionConfig

    settings: Settings = request.app.state.settings
    return DetectionService(
        DetectionConfig(
            baseline_window=settings.baseline_window,
            baseline_min_samples=settings.baseline_min_samples,
            baseline_sigma=settings.baseline_sigma,
            budget_violation_pct=settings.budget_violation_pct,
            outcome_shift_pct=settings.outcome_shift_pct,
        )
    )


def get_broadcast(request: Request) -> BroadcastChannel:
    """Application-scoped broadcast channel from app.state."""
    return request.app.state.broadcast


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """Application-scoped sessionmaker for creating detection sessions."""
    return request.app.state.sessionmaker


async def get_ingest_service(
    run_repo: Annotated[RunRepository, Depends(get_run_repo)],
    detection: Annotated[DetectionService, Depends(get_detection_service)],
    broadcast: Annotated[BroadcastChannel, Depends(get_broadcast)],
    sessionmaker: Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)],
) -> IngestService:
    """Request-scoped ingest service with all dependencies wired."""
    return IngestService(
        run_repo=run_repo,
        detection=detection,
        broadcast=broadcast,
        sessionmaker=sessionmaker,
    )


async def get_tenant_or_404(
    tenant: Annotated[str, Query(max_length=64, pattern=r"^[a-z0-9][a-z0-9-]{0,62}[a-z0-9]$")],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Tenant:
    """Resolve a tenant slug to a ``Tenant`` row, or raise 404.

    Reusable across any endpoint that needs tenant scoping without
    coupling to ``RunRepository``.
    """
    from sqlalchemy import select

    result = await session.execute(select(Tenant).where(Tenant.slug == tenant.lower()))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Tenant not found.")
    return row


async def get_anomaly_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AnomalyRepository:
    """Request-scoped anomaly repository."""
    return AnomalyRepository(session)


async def get_topic_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TopicRepository:
    """Request-scoped topic repository."""
    return TopicRepository(session)


async def get_resolution_service(
    topic_repo: Annotated[TopicRepository, Depends(get_topic_repo)],
) -> ResolutionService:
    """Request-scoped resolution service."""
    return ResolutionService(topic_repo)
