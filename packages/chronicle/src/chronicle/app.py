"""FastAPI application factory for Chronicle."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from chronicle.api import anomalies, health, runs, streaming, tenants, topics
from chronicle.broadcast import BroadcastChannel
from chronicle.config import Settings
from chronicle.dependencies import create_engine_and_sessionmaker
from chronicle.middleware.error_handlers import register_error_handlers
from chronicle.middleware.request_id import RequestIDMiddleware
from chronicle.middleware.security_headers import SecurityHeadersMiddleware

# Pre-built React frontend, included in the wheel via force-include.
# Missing during backend-only development — the guard in create_app
# allows the API to start without the frontend.
_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application-scoped resources (engine, session factory, broadcast)."""
    settings: Settings = app.state.settings
    settings.validate_production_config()
    engine, sessionmaker = create_engine_and_sessionmaker(settings)
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker
    app.state.broadcast = BroadcastChannel(
        max_connections=settings.max_sse_connections,
    )
    yield
    app.state.broadcast.shutdown()
    await engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and return the Chronicle FastAPI application.

    Accepts an optional ``Settings`` instance for testing.  When omitted,
    settings are loaded from environment variables.
    """
    if settings is None:
        settings = Settings()

    app = FastAPI(
        title="Chronicle",
        description="Longitudinal reporting server for Choreo test performance analytics",
        lifespan=lifespan,
        docs_url="/api/v1/docs",
        openapi_url="/api/v1/openapi.json",
        debug=False,
    )

    # Store settings on app.state so the lifespan and dependencies can
    # access them without module-level singletons.
    app.state.settings = settings

    # --- Routers ---
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(runs.router, prefix="/api/v1")
    app.include_router(tenants.router, prefix="/api/v1")
    app.include_router(topics.router, prefix="/api/v1")
    app.include_router(anomalies.router, prefix="/api/v1")
    app.include_router(streaming.router, prefix="/api/v1")

    # --- Error handlers ---
    register_error_handlers(app)

    # --- Middleware ---
    # Registration order matters: Starlette applies middleware in reverse
    # order (last registered = outermost wrapper).
    # We want: SecurityHeaders(outermost) → RequestID → GZip(innermost)
    app.add_middleware(GZipMiddleware, minimum_size=1000)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, include_hsts=settings.is_production())

    # --- Static frontend (must be LAST so API routes take precedence) ---
    if _STATIC_DIR.exists():
        app.mount(
            "/",
            StaticFiles(directory=_STATIC_DIR, html=True),
            name="static",
        )

    return app
