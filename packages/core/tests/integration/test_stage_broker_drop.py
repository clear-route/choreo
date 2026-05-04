"""Group H: Stage mid-scenario broker drop. Negative-behaviour
integration tests.

Covers test-plan items H1-H4 from
`docs/test-plans/0027-stage-integration-tests.md` — the contract that
when a transport's broker drops mid-scenario, the Stage scope's
behaviour stays defined: handles waiting on the dropped transport
resolve as `Outcome.TIMEOUT` at the global deadline, attempts to
publish on the dropped transport raise loudly, and `__aexit__`
tolerates the unsubscribe failure that follows from a dropped
transport.

The key observable: `Handle.transport` carries the breadcrumb that
names which side of a multi-transport scenario dropped, so the
diagnostic is unambiguous when one transport in a pair fails.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple

import pytest
from choreo import Harness
from choreo.matchers import field_equals
from choreo.scenario import Outcome
from choreo.stage import Stage
from choreo.transports import MockTransport

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    mapped_bridge_for,
)

# Placeholder matcher; payload `{"status": "ok"}` matches it.
_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


class _DroppablePair(NamedTuple):
    """Test fixture bundle: a Stage with two harnesses, one droppable.

    `droppable` is the harness whose underlying transport is a
    `_FailingMockTransport` whose `drop()` simulates a broker drop.
    `live` is the other harness, with a vanilla MockTransport.

    Named so test bodies can read `pair.droppable` instead of
    underscoring an unused name from a 3-tuple.
    """

    stage: Stage
    droppable: Harness
    live: Harness


def _stage_with_droppable(
    allowlist_yaml_path: Path,
    *,
    droppable_name: str = "nats",
    live_name: str = "kafka",
) -> _DroppablePair:
    """Build a Stage with two named transports where one is droppable."""

    droppable_h = _harness_over(
        _FailingMockTransport(
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    live_h = Harness(
        MockTransport(
            allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"
        )
    )
    return _DroppablePair(
        stage=Stage(
            harnesses={droppable_name: droppable_h, live_name: live_h},
            bridge=mapped_bridge_for(droppable_name, live_name),
        ),
        droppable=droppable_h,
        live=live_h,
    )


def _drop(harness: Harness) -> None:
    """Trigger the broker-drop simulation on a `_FailingMockTransport`-
    backed harness. Test-only path; `drop()` is not on the public
    Transport Protocol."""
    harness._transport.drop()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# H1 — handle on dropped transport resolves as TIMEOUT at the deadline
# ---------------------------------------------------------------------------


async def test_stage_scope_should_resolve_handles_as_timeout_when_subscribed_transport_drops(
    allowlist_yaml_path: Path,
) -> None:
    """H1. Register an expectation on the droppable transport, drop
    it, await the deadline. The handle resolves as `Outcome.TIMEOUT`
    and `Handle.transport` names which side dropped.
    """
    pair = _stage_with_droppable(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("h1") as scope:
            handle = scope.expect("topic.dropped", _PLACEHOLDER_MATCHER, on="nats")
            _drop(pair.droppable)
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    assert handle.outcome is Outcome.TIMEOUT
    assert handle.transport == "nats"
    assert "no matching message arrived" in handle.reason
    assert not result.passed


# ---------------------------------------------------------------------------
# H2 — publish on dropped transport raises
# ---------------------------------------------------------------------------


async def test_stage_publish_should_raise_when_target_transport_dropped(
    allowlist_yaml_path: Path,
) -> None:
    """H2. After the transport is dropped, attempting `s.publish` on
    that transport raises `RuntimeError` (the underlying transport's
    error). The error propagates out of the scope body — that is the
    correct failure mode: the caller wants to know the publish did
    not happen.
    """
    pair = _stage_with_droppable(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        with pytest.raises(RuntimeError, match="transport dropped"):
            async with pair.stage.scenario("h2") as scope:
                _drop(pair.droppable)
                scope.publish("topic.x", {"hello": "world"}, on="nats")
    finally:
        await pair.stage.disconnect()


# ---------------------------------------------------------------------------
# H3 — __aexit__ completes when unsubscribe fails because of dropped transport
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_complete_when_unsubscribe_raises_due_to_dropped_transport(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """H3. After a broker drop, the transport's `unsubscribe` raises
    (per the `drop()` simulation contract). The scope's `__aexit__`
    must complete cleanly via the per-child isolation pattern — the
    failing unsubscribe is recorded as a structured WARNING
    (`stage_scope_unsubscribe_failed`) naming the dropped transport,
    and the scope exits without raising.

    The intersection of Group G's per-child unsubscribe isolation
    with Group H's broker-drop simulation: same defence applies.
    """
    pair = _stage_with_droppable(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        with caplog.at_level(logging.WARNING, logger="choreo.stage"):
            async with pair.stage.scenario("h3") as scope:
                scope.expect("topic.x", _PLACEHOLDER_MATCHER, on="nats")
                _drop(pair.droppable)
            # Scope exits normally (no body exception); teardown ran.
    finally:
        await pair.stage.disconnect()

    # The dropped transport's unsubscribe failure was recorded.
    teardown_warnings = [
        r
        for r in caplog.records
        if r.getMessage() == "stage_scope_unsubscribe_failed"
    ]
    assert len(teardown_warnings) == 1
    assert teardown_warnings[0].transport == "nats"


# ---------------------------------------------------------------------------
# H4 — only the dropped transport's handles resolve as TIMEOUT
# ---------------------------------------------------------------------------


async def test_stage_scope_should_resolve_only_dropped_transports_handles_as_timeout(
    allowlist_yaml_path: Path,
) -> None:
    """H4. Two-transport scope. Register an expectation on each. Drop
    one transport. Publish a matching message on the LIVE transport.
    Await the deadline.

    Expected: the live transport's handle resolves as `Outcome.PASS`
    (matcher accepted the inbound payload); the dropped transport's
    handle resolves as `Outcome.TIMEOUT` (no message arrived).
    `result.passed` is False because TIMEOUT propagates to scenario
    failure even when other handles passed.
    """
    pair = _stage_with_droppable(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("h4") as scope:
            dropped_handle = scope.expect(
                "topic.dropped", _PLACEHOLDER_MATCHER, on="nats"
            )
            live_handle = scope.expect(
                "topic.live", _PLACEHOLDER_MATCHER, on="kafka"
            )
            _drop(pair.droppable)
            scope.publish("topic.live", {"status": "ok"}, on="kafka")
            result = await scope.await_all(timeout_ms=50)
    finally:
        await pair.stage.disconnect()

    # Live handle resolved by the matcher.
    assert live_handle.outcome is Outcome.PASS
    assert live_handle.transport == "kafka"
    assert live_handle.message == {"status": "ok"}

    # Dropped handle hit the deadline.
    assert dropped_handle.outcome is Outcome.TIMEOUT
    assert dropped_handle.transport == "nats"

    # Result: any TIMEOUT means scenario failed.
    assert not result.passed
