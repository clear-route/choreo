"""Health check endpoint.

Shallow check (default): verifies database connectivity with ``SELECT 1``.
Deep check (``?deep=true``): additionally verifies TimescaleDB extension is
loaded and the ``handle_measurements`` hypertable exists.

Note: this module imports ``sqlalchemy.text`` directly because the health
check executes raw diagnostic SQL that does not belong in a repository.
This is a documented exception to the "no sqlalchemy in API layer" rule.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from starlette.responses import JSONResponse, Response

from chronicle.dependencies import get_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["operations"])


@router.get("/health", response_model=None)
async def health(
    session: Annotated[object, Depends(get_session)],
    deep: Annotated[bool, Query()] = False,
) -> Response:
    """Return service health status."""
    from sqlalchemy import text

    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.error("health check: database unreachable", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unreachable"},
        )

    result: dict = {"status": "ok", "database": "connected"}

    if deep:
        try:
            row = await session.execute(
                text(
                    "SELECT count(*) FROM timescaledb_information.hypertables "
                    "WHERE hypertable_name = 'handle_measurements'"
                )
            )
            hypertable_count = row.scalar_one()
            result["hypertable"] = "present" if hypertable_count > 0 else "missing"
            if hypertable_count == 0:
                result["status"] = "degraded"
                return JSONResponse(status_code=503, content=result)
        except Exception:
            logger.error("deep health check failed", exc_info=True)
            result["status"] = "degraded"
            result["hypertable"] = "check_failed"
            return JSONResponse(status_code=503, content=result)

    return JSONResponse(status_code=200, content=result)
