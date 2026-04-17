"""Behavioural tests for Dispatcher — correlation-based inbound Mediator (ADR-0004).

The Dispatcher is the single dispatch point for every inbound message. It owns a
`correlation_id → scope` map, a per-topic extractor registry, and a redacted
surprise log for unmatched inbound.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest


@dataclass
class _FakeScope:
    correlation_id: str
    future: asyncio.Future[bytes]


def _make_scope(loop: asyncio.AbstractEventLoop, correlation_id: str) -> _FakeScope:
    return _FakeScope(correlation_id=correlation_id, future=loop.create_future())


async def test_the_dispatcher_should_deliver_inbound_to_the_scope_that_owns_the_correlation() -> None:
    from core._internal import Dispatcher, LoopPoster

    loop = asyncio.get_running_loop()
    dispatcher = Dispatcher(poster=LoopPoster(loop))

    scope = _make_scope(loop, correlation_id="TEST-abc")
    dispatcher.register_scope(scope, correlation_id=scope.correlation_id)

    dispatcher.register_extractor(
        topic="orders.booked",
        extractor=lambda p: p.removeprefix(b"corr=").decode()
        if p.startswith(b"corr=")
        else None,
    )

    dispatcher.dispatch(
        topic="orders.booked",
        payload=b"corr=TEST-abc",
        source="lbm",
        resolver=lambda sc, msg: sc.future.set_result(msg),
    )

    msg = await asyncio.wait_for(scope.future, timeout=1.0)
    assert msg == b"corr=TEST-abc"
    dispatcher.deregister_scope(scope)


async def test_an_unrecognised_correlation_should_go_to_the_surprise_log_without_the_payload() -> None:
    from core._internal import Dispatcher, LoopPoster

    loop = asyncio.get_running_loop()
    dispatcher = Dispatcher(poster=LoopPoster(loop))
    dispatcher.register_extractor(
        topic="orders.booked",
        extractor=lambda p: p.removeprefix(b"corr=").decode()
        if p.startswith(b"corr=")
        else None,
    )

    dispatcher.dispatch(
        topic="orders.booked",
        payload=b"corr=TEST-UNKNOWN",
        source="lbm",
        resolver=lambda sc, msg: None,
    )

    log = dispatcher.surprise_log()
    assert len(log) == 1
    entry = log[0]
    assert entry.topic == "orders.booked"
    assert entry.correlation_id == "TEST-UNKNOWN"
    assert entry.classification == "unknown_scope"
    assert entry.size == len(b"corr=TEST-UNKNOWN")
    # Redaction: the payload must NOT appear on the entry.
    assert not hasattr(entry, "payload")


async def test_a_message_arriving_after_its_scope_deregisters_should_be_classified_as_timeout_race() -> None:
    from core._internal import Dispatcher, LoopPoster

    loop = asyncio.get_running_loop()
    dispatcher = Dispatcher(poster=LoopPoster(loop))
    dispatcher.register_extractor(
        topic="t",
        extractor=lambda p: p.decode() if p else None,
    )

    scope = _make_scope(loop, correlation_id="TEST-lived-briefly")
    dispatcher.register_scope(scope, correlation_id=scope.correlation_id)
    dispatcher.deregister_scope(scope)

    dispatcher.dispatch(
        topic="t",
        payload=b"TEST-lived-briefly",
        source="lbm",
        resolver=lambda sc, msg: sc.future.set_result(msg),
    )

    log = dispatcher.surprise_log()
    assert len(log) == 1
    assert log[0].classification == "timeout_race"


async def test_registering_an_extractor_that_deserialises_untrusted_data_should_be_rejected() -> None:
    """ADR-0004 §Security: extractors are pure parsing functions."""
    import pickle

    from core._internal import Dispatcher, LoopPoster

    dispatcher = Dispatcher(poster=LoopPoster(asyncio.get_running_loop()))
    with pytest.raises(ValueError):
        dispatcher.register_extractor(topic="t", extractor=pickle.loads)  # type: ignore[arg-type]


async def test_attempts_to_override_the_dispatchers_dispatch_method_should_fail_at_class_creation() -> None:
    """Subclassing and overriding `dispatch` breaks the single-dispatch-point invariant.
    The framework must refuse at class creation, not at runtime."""
    from core._internal import Dispatcher

    with pytest.raises(TypeError):

        class _Evil(Dispatcher):  # type: ignore[misc]
            def dispatch(self, *args: object, **kwargs: object) -> None:  # type: ignore[override]
                pass


async def test_generated_correlation_ids_should_be_unique() -> None:
    from core._internal import generate_correlation_id

    ids = {generate_correlation_id() for _ in range(100)}
    assert len(ids) == 100


async def test_generated_correlation_ids_should_be_unguessable() -> None:
    from core._internal import generate_correlation_id

    for _ in range(10):
        cid = generate_correlation_id()
        assert cid.startswith("TEST-")
        suffix = cid.removeprefix("TEST-")
        # Long enough that a brute-force echo of a different scope's ID is infeasible.
        assert len(suffix) >= 16
