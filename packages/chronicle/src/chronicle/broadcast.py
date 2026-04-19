"""In-memory SSE fan-out channel with per-tenant event filtering.

Safe only under a single asyncio event loop (single uvicorn worker).
Multi-worker deployments require a PostgreSQL LISTEN/NOTIFY bridge or
Redis Pub/Sub; this class would be replaced wholesale, not extended.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncGenerator
from uuid import UUID

from chronicle.exceptions import TooManyConnections  # noqa: F401 — re-export

logger = logging.getLogger(__name__)


class SSEEvent:
    """A single event to be sent to SSE clients."""

    __slots__ = ("data", "event", "id", "tenant_id")

    def __init__(self, id: int, event: str, data: dict, tenant_id: UUID) -> None:
        self.id = id
        self.event = event
        self.data = data
        self.tenant_id = tenant_id


class HeartbeatComment:
    """SSE comment line (``: heartbeat``) -- keeps connections alive."""

    pass


class BroadcastChannel:
    """In-memory SSE fan-out with per-tenant event filtering.

    Clients subscribe with a ``tenant_id`` and only receive events for
    that tenant.  Events are buffered in a ring buffer for
    ``Last-Event-ID`` replay on reconnection.
    """

    def __init__(
        self,
        max_connections: int = 100,
        event_buffer_size: int = 1000,
        client_queue_size: int = 100,
        heartbeat_interval_seconds: float = 30,
    ) -> None:
        self._max_connections = max_connections
        self._event_buffer_size = event_buffer_size
        self._client_queue_size = client_queue_size
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._clients: dict[
            int, tuple[UUID, asyncio.Queue[SSEEvent | HeartbeatComment | None]]
        ] = {}
        self._event_buffer: deque[SSEEvent] = deque(maxlen=event_buffer_size)
        self._next_id = 0
        self._sequence = 0

    async def subscribe(
        self,
        tenant_id: UUID,
        last_event_id: int | None = None,
    ) -> AsyncGenerator[SSEEvent | HeartbeatComment, None]:
        """Async generator that yields events for the given tenant.

        Sends a heartbeat comment every ``heartbeat_interval_seconds``
        seconds to keep the connection alive through proxies.  Yields
        ``None`` on graceful shutdown.
        """
        if len(self._clients) >= self._max_connections:
            raise TooManyConnections()

        client_id = self._next_id
        self._next_id += 1
        queue: asyncio.Queue[SSEEvent | HeartbeatComment | None] = asyncio.Queue(
            maxsize=self._client_queue_size,
        )
        self._clients[client_id] = (tenant_id, queue)

        # Replay missed events for this tenant only
        if last_event_id is not None:
            for event in self._event_buffer:
                if event.id > last_event_id and event.tenant_id == tenant_id:
                    await queue.put(event)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=self._heartbeat_interval_seconds
                    )
                    if event is None:
                        break  # shutdown sentinel
                    yield event
                except TimeoutError:
                    yield HeartbeatComment()
        finally:
            self._clients.pop(client_id, None)

    async def emit(
        self,
        tenant_id: UUID,
        event_type: str,
        data: dict,
    ) -> None:
        """Broadcast an event to all clients subscribed to the given tenant."""
        self._sequence += 1
        event = SSEEvent(
            id=self._sequence,
            event=event_type,
            data=data,
            tenant_id=tenant_id,
        )
        self._event_buffer.append(event)

        for client_id, (client_tenant, queue) in list(self._clients.items()):
            if client_tenant != tenant_id:
                continue
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("disconnecting slow SSE client %d", client_id)
                self._clients.pop(client_id, None)

    def shutdown(self) -> None:
        """Close all client queues during graceful shutdown."""
        for _, (_, queue) in self._clients.items():
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        self._clients.clear()
