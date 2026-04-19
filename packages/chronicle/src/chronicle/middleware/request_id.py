"""Request ID middleware.

Generates a UUID request ID for each request (or echoes the client's
``X-Request-ID`` header if it matches a safe pattern).  The ID is
stored on ``request.state.request_id`` for use in structured logging,
and returned in the ``X-Request-ID`` response header.

Client-provided IDs are validated against ``^[a-zA-Z0-9._-]{1,128}$``
to prevent log injection via newlines or ANSI escape sequences.
"""

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        client_id = request.headers.get("X-Request-ID")
        if client_id and _SAFE_ID_RE.match(client_id):
            request_id = client_id
        else:
            request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
