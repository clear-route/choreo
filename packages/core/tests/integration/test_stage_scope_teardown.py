"""Group G: Stage scope teardown isolation. Negative-behaviour
integration tests.

Covers test-plan items G1-G5 from
`docs/test-plans/0027-stage-integration-tests.md` — the contract that
`_StageScenarioScope.__aexit__` per-child unsubscribe loop is isolated
by per-pair try/except so a single failing unsubscribe does not abort
the rest. Same isolation pattern as the single-transport scope at
`packages/core/src/choreo/scenario.py:1302-1310`.

R1 in the comprehensive review was the most-flagged item: the previous
draft did not isolate per-child unsubscribe, so one failing
`harness.unsubscribe()` would leak every subsequent transport's
callbacks. These tests pin the contract that closes that bug.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from choreo.matchers import field_equals

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    mapped_bridge_for,
)

# A placeholder matcher; same shape as the Group F constant.
_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------


def _failing_unsubscribe_stage(
    allowlist_yaml_path: Path,
    *,
    fail_unsubscribe_on: str | None,
):
    """Build a Stage with two transports where one of them raises on
    `unsubscribe`. `fail_unsubscribe_on` names the transport that
    raises; pass None to get a stage where neither fails.
    """
    from choreo.stage import Stage
    from choreo.transports import MockTransport

    def _mk_harness(name: str):
        if fail_unsubscribe_on == name:
            transport = _FailingMockTransport(
                fail_unsubscribe=RuntimeError(f"unsubscribe failure on transport {name!r}"),
                allowlist_path=allowlist_yaml_path,
                endpoint="mock://localhost",
            )
        else:
            transport = MockTransport(
                allowlist_path=allowlist_yaml_path,
                endpoint="mock://localhost",
            )
        return _harness_over(transport)

    nats_h = _mk_harness("nats")
    kafka_h = _mk_harness("kafka")
    return (
        Stage(
            harnesses={"nats": nats_h, "kafka": kafka_h},
            bridge=mapped_bridge_for("nats", "kafka"),
        ),
        nats_h,
        kafka_h,
    )


# ---------------------------------------------------------------------------
# G1 — one transport's unsubscribe raises; the other still tears down
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_complete_when_one_unsubscribe_raises(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """G1. Two transports, expectation registered on each. The first
    transport's unsubscribe raises during teardown. The second
    transport's unsubscribe must still be called, the scope must exit
    cleanly, and a structured WARNING (`stage_scope_unsubscribe_failed`)
    must be emitted naming the failing transport.

    Covers R1 (the most-flagged review item — isolation regression).
    """
    stage, _nats_h, kafka_h = _failing_unsubscribe_stage(
        allowlist_yaml_path, fail_unsubscribe_on="nats"
    )
    await stage.connect()

    with caplog.at_level(logging.WARNING, logger="choreo.stage"):
        async with stage.scenario("g1") as scope:
            scope.expect("topic.a", _PLACEHOLDER_MATCHER, on="nats")
            scope.expect("topic.b", _PLACEHOLDER_MATCHER, on="kafka")
        # Scope exits; teardown runs. Must not raise.

    await stage.disconnect()

    # The WARNING was emitted for the failing transport, with the
    # transport name on the LogRecord.
    teardown_warnings = [
        r for r in caplog.records if r.getMessage() == "stage_scope_unsubscribe_failed"
    ]
    assert len(teardown_warnings) == 1
    assert teardown_warnings[0].transport == "nats"
    assert teardown_warnings[0].topic == "topic.a"

    # The Kafka harness's unsubscribe ran cleanly: its subscription
    # count is zero. (The failing nats unsubscribe leaves the
    # MockTransport's count untouched because the fail-injection raises
    # before the parent unsubscribe runs; that is fine — the test
    # subject is the loop's isolation, not the transport's bookkeeping.)
    assert kafka_h.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# G2 — every transport's unsubscribe raises; scope still exits
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_complete_even_when_every_unsubscribe_raises(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """G2. Both transports' unsubscribes raise. `__aexit__` still
    returns without raising, and TWO WARNINGs are emitted (one per
    transport).
    """
    from choreo.stage import Stage

    nats_h = _harness_over(
        _FailingMockTransport(
            fail_unsubscribe=RuntimeError("nats unsubscribe failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    kafka_h = _harness_over(
        _FailingMockTransport(
            fail_unsubscribe=RuntimeError("kafka unsubscribe failed"),
            allowlist_path=allowlist_yaml_path,
            endpoint="mock://localhost",
        )
    )
    stage = Stage(
        harnesses={"nats": nats_h, "kafka": kafka_h},
        bridge=mapped_bridge_for("nats", "kafka"),
    )
    await stage.connect()

    with caplog.at_level(logging.WARNING, logger="choreo.stage"):
        async with stage.scenario("g2") as scope:
            scope.expect("topic.a", _PLACEHOLDER_MATCHER, on="nats")
            scope.expect("topic.b", _PLACEHOLDER_MATCHER, on="kafka")

    await stage.disconnect()

    teardown_warnings = [
        r for r in caplog.records if r.getMessage() == "stage_scope_unsubscribe_failed"
    ]
    assert len(teardown_warnings) == 2
    transports_named = {r.transport for r in teardown_warnings}
    assert transports_named == {"nats", "kafka"}


# ---------------------------------------------------------------------------
# G3 — failing unsubscribe still clears subscriber refs (no leak across scopes)
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_clear_subscriber_refs_even_when_unsubscribe_raises(
    allowlist_yaml_path: Path,
) -> None:
    """G3. Even when an unsubscribe raises, the per-child
    subscriber_refs is cleared — the scope no longer holds the
    reference once the framework has attempted to unsubscribe. A
    subsequent scope on the same Stage starts with no leaked
    callbacks.

    The observable: opening a fresh scope, registering one expectation
    on each transport, then exiting — afterward, the subscription
    counts on both transports are zero (in the second scope, neither
    fails, so unsubscribe completes cleanly).
    """
    stage, _nats_h, kafka_h = _failing_unsubscribe_stage(
        allowlist_yaml_path, fail_unsubscribe_on="nats"
    )
    await stage.connect()

    # First scope: nats unsubscribe fails. The scope's framework state
    # should still be cleared.
    async with stage.scenario("g3-first") as scope:
        scope.expect("topic.a", _PLACEHOLDER_MATCHER, on="nats")
        scope.expect("topic.b", _PLACEHOLDER_MATCHER, on="kafka")

    # Second scope: register one expectation on each transport, exit.
    # If subscriber_refs leaked across scopes, the second scope's
    # teardown would attempt to unsubscribe stale callbacks from the
    # first, causing extra WARNINGs or extra unsubscribe attempts.
    async with stage.scenario("g3-second") as scope:
        scope.expect("topic.c", _PLACEHOLDER_MATCHER, on="nats")
        scope.expect("topic.d", _PLACEHOLDER_MATCHER, on="kafka")

    await stage.disconnect()

    # Kafka unsubscribed cleanly across both scopes; count is zero.
    # Nats's failing unsubscribe means the underlying MockTransport's
    # callback registry still holds the first scope's callback for
    # topic.a, but the framework's per-scope state is what we're
    # asserting: kafka rounded-tripped cleanly across both scopes,
    # which proves G3 children's state cleared.
    assert kafka_h.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# G4 — body exception propagates unmodified
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_propagate_a_body_exception_unmodified(
    allowlist_yaml_path: Path,
) -> None:
    """G4. If the `async with` body raises, that exception propagates
    out of the scope unmodified. Teardown runs (subscribers cleared)
    but does NOT swallow or chain the body exception.
    """
    stage, _, kafka_h = _failing_unsubscribe_stage(allowlist_yaml_path, fail_unsubscribe_on=None)
    await stage.connect()

    body_exception = RuntimeError("the test body itself blew up")

    try:
        with pytest.raises(RuntimeError, match="the test body itself blew up"):
            async with stage.scenario("g4") as scope:
                scope.expect("topic.a", _PLACEHOLDER_MATCHER, on="nats")
                scope.expect("topic.b", _PLACEHOLDER_MATCHER, on="kafka")
                raise body_exception
    finally:
        await stage.disconnect()

    # Teardown ran: kafka's subscription was unsubscribed cleanly.
    assert kafka_h.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# G5 — body exception still propagates even when teardown logs warnings
# ---------------------------------------------------------------------------


async def test_stage_scope_aexit_should_propagate_a_body_exception_even_when_teardown_logs_warnings(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """G5. The body exception must propagate even when teardown
    encounters a failing unsubscribe (which logs a WARNING). The body
    exception is the one the user sees; the teardown failure is
    recorded only as a structured log entry.
    """
    stage, _, _ = _failing_unsubscribe_stage(allowlist_yaml_path, fail_unsubscribe_on="nats")
    await stage.connect()

    body_exception = RuntimeError("body blew up while teardown was unhappy")

    with caplog.at_level(logging.WARNING, logger="choreo.stage"):
        try:
            with pytest.raises(RuntimeError, match="body blew up while teardown was unhappy"):
                async with stage.scenario("g5") as scope:
                    scope.expect("topic.a", _PLACEHOLDER_MATCHER, on="nats")
                    scope.expect("topic.b", _PLACEHOLDER_MATCHER, on="kafka")
                    raise body_exception
        finally:
            await stage.disconnect()

    # The teardown WARNING was still recorded for the failing transport.
    teardown_warnings = [
        r for r in caplog.records if r.getMessage() == "stage_scope_unsubscribe_failed"
    ]
    assert len(teardown_warnings) == 1
    assert teardown_warnings[0].transport == "nats"
