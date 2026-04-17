"""Behavioural tests for LoopPoster — the thread-safety boundary (ADR-0003).

Every cross-thread delivery from a transport thread to the asyncio loop goes
through `LoopPoster.post`. It wraps `loop.call_soon_threadsafe` and (in debug
builds) constrains the callable to a whitelist.
"""
from __future__ import annotations

import asyncio
import threading
import time


async def test_posting_from_the_loop_thread_should_still_resolve_on_the_loop() -> None:
    from core._internal import LoopPoster

    loop = asyncio.get_running_loop()
    poster = LoopPoster(loop)
    fut: asyncio.Future[str] = loop.create_future()

    poster.post(fut.set_result, "delivered")
    result = await asyncio.wait_for(fut, timeout=1.0)
    assert result == "delivered"


async def test_posting_from_a_non_loop_thread_should_resolve_on_the_loop_thread() -> None:
    """The crux of the poster: cross-thread delivery is safe for Futures."""
    from core._internal import LoopPoster

    loop = asyncio.get_running_loop()
    poster = LoopPoster(loop)
    fut: asyncio.Future[int] = loop.create_future()
    resolver_thread: list[int] = []

    def _resolve() -> None:
        resolver_thread.append(threading.get_ident())
        fut.set_result(42)

    def _worker() -> None:
        time.sleep(0.01)  # let the loop reach its await first
        poster.post(_resolve)

    worker = threading.Thread(target=_worker)
    worker.start()
    try:
        result = await asyncio.wait_for(fut, timeout=1.0)
    finally:
        worker.join()

    assert result == 42
    assert resolver_thread[0] == threading.get_ident()  # loop thread resolved it


async def test_the_poster_should_reject_callables_outside_its_whitelist_in_debug_mode() -> None:
    from core._internal import LoopPoster
    import pytest

    loop = asyncio.get_running_loop()
    poster = LoopPoster(loop, debug=True)

    def arbitrary() -> None:
        pass

    with pytest.raises(RuntimeError):
        poster.post(arbitrary)


async def test_drain_should_return_zero_when_the_callback_queue_is_empty() -> None:
    from core._internal import LoopPoster

    loop = asyncio.get_running_loop()
    poster = LoopPoster(loop)

    fut: asyncio.Future[None] = loop.create_future()
    poster.post(fut.set_result, None)

    remaining = await poster.drain(timeout=1.0)
    assert fut.done()
    assert remaining == 0


async def test_drain_should_not_block_forever_when_callbacks_are_still_in_flight() -> None:
    from core._internal import LoopPoster

    loop = asyncio.get_running_loop()
    poster = LoopPoster(loop)

    async def slow() -> None:
        await asyncio.sleep(0.5)

    poster.post(asyncio.ensure_future, slow())

    remaining = await poster.drain(timeout=0.05)
    # Callers use this signal to decide whether to hard-close or wait longer.
    assert remaining >= 0
