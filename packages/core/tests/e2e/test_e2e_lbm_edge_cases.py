"""End-to-end tests for LbmTransport-specific behaviours.

The shared contract suite under ``tests/e2e/contract/`` already exercises the
Transport Protocol surface against every backend. This file covers behaviours
that only a real LBM messaging system can surface — multicast fanout semantics,
fire-and-forget publish (messages lost when no subscriber exists), message
ordering per topic, and LBM's callback threading model.

Runs only under ``pytest -m e2e``. Requires:

    export LBM_CONFIG_FILE=/path/to/lbm_config.xml
    export LBM_LICENSE_FILENAME=/path/to/lbm_license.txt
    pytest -m e2e -k lbm

LBM is proprietary software from Informatica that requires a commercial license.
Unlike other transports, there is no Docker Compose setup — tests skip gracefully
when LBM is not available.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_an_lbm_transport_should_connect_and_disconnect_cleanly(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LbmTransport should establish connection to LBM messaging system and
    clean up all resources on disconnect."""
    from choreo import Harness
    from choreo.transports.lbm import LbmTransport

    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)

    await harness.connect()
    assert harness.is_connected()

    await harness.disconnect()
    assert not harness.is_connected()


# ---------------------------------------------------------------------------
# Pub/Sub Semantics
# ---------------------------------------------------------------------------


async def test_an_lbm_transport_should_lose_messages_published_before_subscriber_exists(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LBM is fire-and-forget: messages published before any subscriber
    exists are lost. This matches NATS/Redis pub/sub semantics."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports.lbm import LbmTransport

    topic = _topic("lost")
    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        # Publish BEFORE subscribing
        harness.publish(topic, b'{"status": "EARLY"}')
        await asyncio.sleep(0.1)  # Let message propagate

        # Now subscribe and expect the message
        async with harness.scenario("late-sub") as s:
            handle = s.expect(topic, field_equals("status", "EARLY"))
            result = await s.await_all(timeout_ms=500)

        # Message was lost — handle should timeout
        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_an_lbm_transport_should_fan_out_to_multiple_subscribers(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LBM multicast broadcasts to every subscriber on a topic. Multiple
    subscribers should all receive the same message."""
    from choreo import Harness
    from choreo.transports.lbm import LbmTransport

    topic = _topic("fanout")
    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        received_a: list[bytes] = []
        received_b: list[bytes] = []
        received_c: list[bytes] = []
        done = asyncio.Event()

        def cb_a(t: str, payload: bytes) -> None:
            received_a.append(payload)
            if len(received_a) + len(received_b) + len(received_c) == 3:
                done.set()

        def cb_b(t: str, payload: bytes) -> None:
            received_b.append(payload)
            if len(received_a) + len(received_b) + len(received_c) == 3:
                done.set()

        def cb_c(t: str, payload: bytes) -> None:
            received_c.append(payload)
            if len(received_a) + len(received_b) + len(received_c) == 3:
                done.set()

        # Subscribe three callbacks to same topic
        harness.subscribe(topic, cb_a)
        harness.subscribe(topic, cb_b)
        harness.subscribe(topic, cb_c)

        # Give LBM time to establish subscriptions
        await asyncio.sleep(0.2)

        # Publish one message
        harness.publish(topic, b"broadcast")

        await asyncio.wait_for(done.wait(), timeout=2.0)

        # All three should have received it
        assert received_a == [b"broadcast"]
        assert received_b == [b"broadcast"]
        assert received_c == [b"broadcast"]
    finally:
        await harness.disconnect()


async def test_messages_on_an_lbm_topic_should_arrive_in_order(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LBM preserves message order per topic. A burst of messages should
    arrive at the subscriber in the order they were published."""
    from choreo import Harness
    from choreo.transports.lbm import LbmTransport

    topic = _topic("ordered")
    count = 30
    received: list[int] = []
    done = asyncio.Event()

    def on_msg(_t: str, payload: bytes) -> None:
        received.append(int(payload))
        if len(received) == count:
            done.set()

    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        harness.subscribe(topic, on_msg)
        await asyncio.sleep(0.2)  # Let subscription establish

        # Rapid-fire publish
        for i in range(count):
            harness.publish(topic, str(i).encode())

        await asyncio.wait_for(done.wait(), timeout=3.0)

        # Messages should arrive in order 0, 1, 2, ..., count-1
        assert received == list(range(count))
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Scenario DSL Integration
# ---------------------------------------------------------------------------


async def test_a_matching_message_published_over_lbm_should_fulfil_the_scenario(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """End-to-end through the Scenario DSL: expect, publish, await_all
    should work identically to other transports."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports.lbm import LbmTransport

    topic = _topic("scenario.pass")
    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("lbm-happy") as s:
            s.expect(topic, field_equals("status", "APPROVED"))
            s = s.publish(topic, {"status": "APPROVED", "order_id": "LBM001"})
            result = await s.await_all(timeout_ms=2000)

        result.assert_passed()
    finally:
        await harness.disconnect()


async def test_a_timeout_on_lbm_should_surface_as_timeout_outcome(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """When no message arrives, the handle should resolve as TIMEOUT, not
    FAIL — preserving the routing-vs-expectation distinction."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports.lbm import LbmTransport

    topic = _topic("timeout")
    other_topic = _topic("other")
    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("lbm-timeout") as s:
            handle = s.expect(topic, field_equals("status", "NEVER"))
            # Publish on different topic so harness sees zero attempts
            s = s.publish(other_topic, {"status": "NEVER"})
            result = await s.await_all(timeout_ms=500)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Wildcard Patterns
# ---------------------------------------------------------------------------


async def test_an_lbm_transport_should_support_wildcard_subscriptions(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LBM supports pattern receivers. A subscription to 'orders.*' should
    match 'orders.new', 'orders.cancel', etc."""
    from choreo import Harness
    from choreo.transports.lbm import LbmTransport

    pattern = _topic("orders.*")
    topic_new = _topic("orders.new")
    topic_cancel = _topic("orders.cancel")

    received: list[tuple[str, bytes]] = []
    done = asyncio.Event()

    def on_msg(topic: str, payload: bytes) -> None:
        received.append((topic, payload))
        if len(received) == 2:
            done.set()

    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        # Subscribe with wildcard pattern
        harness.subscribe(pattern, on_msg)
        await asyncio.sleep(0.2)  # Let subscription establish

        # Publish to two different topics matching the pattern
        harness.publish(topic_new, b"NEW_ORDER")
        harness.publish(topic_cancel, b"CANCEL_ORDER")

        await asyncio.wait_for(done.wait(), timeout=2.0)

        # Both messages should have arrived
        topics_seen = {topic for topic, _ in received}
        assert topic_new in topics_seen
        assert topic_cancel in topics_seen
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Concurrency / Correlation
# ---------------------------------------------------------------------------


async def test_two_concurrent_scenarios_on_lbm_should_only_fulfil_the_handle_whose_correlation_matches(
    allowlist_yaml_path: Path,
    lbm_config_file: str | None,
    lbm_license_file: str | None,
    _lbm_available: bool,
) -> None:
    """LBM broadcasts to all subscribers. The scope's correlation filter is
    the only isolation boundary between concurrent test scenarios.

    Opts into `test_namespace()` — ADR-0019 makes correlation routing opt-in."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports.lbm import LbmTransport

    topic = _topic("concurrent")
    transport = LbmTransport(
        lbm_config_file=lbm_config_file,
        license_file=lbm_license_file,
    )
    harness = Harness(transport, correlation=test_namespace())
    await harness.connect()
    try:

        async def run(key: str) -> tuple[Any, Any]:
            async with harness.scenario(f"scope-{key}") as s:
                handle = s.expect(topic, field_equals("k", key))
                if key == "a":
                    s = s.publish(topic, {"k": key})
                else:
                    # Publish with foreign correlation_id
                    s = s.publish(
                        topic,
                        json.dumps({"correlation_id": "foreign", "k": "b"}).encode(),
                    )
                return handle, await s.await_all(timeout_ms=1500)

        (handle_a, result_a), (handle_b, result_b) = await asyncio.gather(run("a"), run("b"))

        result_a.assert_passed()
        assert handle_a.outcome is Outcome.PASS

        assert result_b.passed is False
        assert handle_b.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topic(suffix: str) -> str:
    """Generate unique topic name to avoid collisions between concurrent runs.

    LBM topics are strings; dots are common separators like NATS subjects."""
    return f"e2e.lbm.{suffix}.{uuid.uuid4().hex[:8]}"
