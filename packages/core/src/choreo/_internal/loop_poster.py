"""Thread-safety poster — ADR-0003.

Cross-thread delivery from a transport callback thread into the asyncio event
loop goes through `LoopPoster.post`. One helper, one method, one behaviour: post
a callable + args, have it run on the loop thread via `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

_WHITELIST_NAMES: frozenset[str] = frozenset(
    {"set_result", "set_exception", "ensure_future", "set", "dispatch"}
)


class LoopPoster:
    """Post callables onto the asyncio loop from any thread.

    When `debug=True`, callables whose `__name__` is outside a small whitelist
    are rejected — a runtime belt-and-braces check on top of the CI AST guard
    described in [ADR-0003](../../docs/adr/0003-threadsafe-call-soon-bridge.md).
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, *, debug: bool = False) -> None:
        self._loop = loop
        self._debug = debug

    def post(self, fn: Callable[..., Any], *args: Any) -> None:
        """Schedule `fn(*args)` to run on the loop thread."""
        if self._debug and not self._is_whitelisted(fn):
            raise RuntimeError(
                f"callable {fn!r} is not in the LoopPoster whitelist; "
                f"only {sorted(_WHITELIST_NAMES)} are allowed in debug mode"
            )
        self._loop.call_soon_threadsafe(fn, *args)

    @staticmethod
    def _is_whitelisted(fn: Callable[..., Any]) -> bool:
        name = getattr(fn, "__name__", None)
        return name in _WHITELIST_NAMES

    async def drain(self, timeout: float = 5.0) -> int:
        """Wait for posted callbacks and their spawned tasks to finish.

        Returns the number of tasks still pending at the deadline. Zero means
        everything drained cleanly.
        """
        # Fence-post: ensure every already-scheduled call_soon callback has
        # had a chance to run before we start counting tasks.
        fence: asyncio.Future[None] = self._loop.create_future()
        self._loop.call_soon(fence.set_result, None)
        await fence

        deadline = self._loop.time() + timeout
        while True:
            pending = self._pending_tasks()
            if not pending:
                return 0
            if self._loop.time() >= deadline:
                return len(pending)
            await asyncio.sleep(0.005)

    def _pending_tasks(self) -> list[asyncio.Task[Any]]:
        current = asyncio.current_task()
        return [t for t in asyncio.all_tasks(self._loop) if t is not current and not t.done()]
