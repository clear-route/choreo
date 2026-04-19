"""Global exception handlers -- no stack traces in responses."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from chronicle.exceptions import ReportValidationError

logger = logging.getLogger(__name__)


def register_error_handlers(app: FastAPI) -> None:
    """Attach exception handlers that return ``ErrorResponse``-shaped JSON."""

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Strip 'input' values from error dicts to prevent reflecting
        # potentially sensitive submitted data back to the caller.
        safe_errors = [{k: v for k, v in e.items() if k != "input"} for e in exc.errors()]
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": str(safe_errors),
            },
        )

    @app.exception_handler(ReportValidationError)
    async def report_validation_error(request: Request, exc: ReportValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": "report_validation_error",
                "detail": f"Report failed schema validation: {'; '.join(exc.messages)}",
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "detail": str(exc.detail),
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "unhandled exception",
            extra={"request_id": request_id, "path": request.url.path},
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "detail": "An unexpected error occurred.",
            },
        )
