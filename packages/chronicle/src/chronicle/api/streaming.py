"""SSE streaming endpoint.

GET /api/v1/stream — server-sent events for live dashboard updates.

Tenant validation uses ``get_tenant_or_404`` which briefly acquires a
session to resolve the slug.  The session is released before the SSE
generator starts, so long-lived connections do not hold pool slots.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from chronicle.broadcast import BroadcastChannel, HeartbeatComment, TooManyConnections
from chronicle.dependencies import get_broadcast, get_tenant_or_404
from chronicle.models.tables import Tenant

router = APIRouter(tags=["streaming"])


@router.get("/stream")
async def stream(
    request: Request,
    tenant_row: Annotated[Tenant, Depends(get_tenant_or_404)],
    broadcast: Annotated[BroadcastChannel, Depends(get_broadcast)],
) -> EventSourceResponse:
    """Subscribe to live events for a tenant.

    Pushes ``run.completed`` and ``anomaly.detected`` events as they
    occur.  Sends a heartbeat comment every 30 seconds to keep the
    connection alive through proxies.

    Supports ``Last-Event-ID`` for reconnection replay from the
    in-memory ring buffer (last 1,000 events).
    """
    last_event_id_raw = request.headers.get("Last-Event-ID")
    last_event_id: int | None = None
    if last_event_id_raw:
        try:
            last_event_id = int(last_event_id_raw)
        except ValueError:
            pass  # ignore non-numeric Last-Event-ID; start from latest

    async def event_generator():
        try:
            async for event in broadcast.subscribe(
                tenant_id=tenant_row.id,
                last_event_id=last_event_id,
            ):
                if event is None:
                    break
                if isinstance(event, HeartbeatComment):
                    yield {"comment": "heartbeat"}
                else:
                    yield {
                        "id": str(event.id),
                        "event": event.event,
                        "data": json.dumps(event.data),
                    }
        except TooManyConnections:
            yield {
                "event": "error",
                "data": json.dumps({"detail": "Too many SSE connections."}),
            }

    return EventSourceResponse(event_generator())
