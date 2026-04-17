"""Internal framework plumbing. Not part of the public API.

Nothing in `core.*` outside this package should import from modules other than
this package's `__init__`.

`Dispatcher` and `LoopPoster` are **reserved scaffolding** (ADR-0003, ADR-0004)
for a future threaded transport (LbmTransport). No shipping transport uses
them — the async backends (Mock, NATS, Kafka, Rabbit, Redis) schedule work
via `loop.create_task(...)` directly because they run on the asyncio loop
thread. The abstractions are retained here because the ADRs treat them as
foundational design; deleting them would drop the threaded-transport contract."""
from __future__ import annotations

from .correlation import generate_correlation_id
from .dispatcher import Dispatcher, SurpriseEntry
from .loop_poster import LoopPoster


__all__ = [
    "Dispatcher",
    "LoopPoster",
    "SurpriseEntry",
    "generate_correlation_id",
]
