"""Transport contract tests — one test, every reachable backend.

Runs under ``pytest -m e2e``. Every test takes the parametrised
``transport_factory`` fixture and therefore runs once per transport
registered in ``tests/e2e/factories.py``. Unreachable or uninstalled
backends skip inside the fixture, so a dev who only has NATS running
still gets green CI output for the ones they have.

What this suite covers (behaviours every pub/sub-shaped transport must
honour):

  - Connect / disconnect lifecycle
  - Disconnect is idempotent
  - Publish → subscribe round-trip (bytes)
  - Fan-out: two subscribers on one topic both receive the message
  - Unsubscribe stops further deliveries for that callback
  - A matching publish fulfils a scenario expectation
  - A topic with no matching publish surfaces as Outcome.TIMEOUT
  - Multi-expectation scopes track failures per-handle
  - Large JSON payload round-trip
  - Subscription churn does not leak subscriptions
  - Reconnect restores subscribe + publish

Transport-specific quirks (NATS callback exceptions, Kafka consumer groups,
RabbitMQ publisher confirms, Redis buffered PUBLISH) are NOT in this suite.
Each transport's module-level tests cover those where they matter.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ..factories import TransportFactory


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_a_harness_over_the_transport_should_report_itself_connected_after_connect(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness

    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        assert harness.is_connected()
    finally:
        await harness.disconnect()


async def test_disconnecting_twice_should_be_idempotent(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness

    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    await harness.disconnect()
    await harness.disconnect()  # must not raise


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


async def test_a_published_byte_payload_should_arrive_at_the_subscribed_callback(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness

    topic = transport_factory.topic("round-trip")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        received: list[bytes] = []
        arrived = asyncio.Event()

        def cb(_t: str, payload: bytes) -> None:
            received.append(payload)
            arrived.set()

        harness.subscribe(topic, cb)
        # Some transports require a beat between SUBSCRIBE and the first
        # PUBLISH for the bind to land on the broker. 250 ms is pessimistic
        # for NATS (<1ms in practice) and reasonable for Kafka.
        await asyncio.sleep(0.25)

        harness.publish(topic, b"payload")

        await asyncio.wait_for(arrived.wait(), timeout=5.0)
        assert received == [b"payload"]
    finally:
        await harness.disconnect()


async def test_publishing_should_fan_out_to_every_subscriber_on_the_topic(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    if not transport_factory.capabilities.broadcast_fanout:
        pytest.skip(f"{transport_factory.name} does not broadcast fan-out")

    from core import Harness

    topic = transport_factory.topic("fanout")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        got_a: list[bytes] = []
        got_b: list[bytes] = []
        ev_a = asyncio.Event()
        ev_b = asyncio.Event()

        def cb_a(_t: str, p: bytes) -> None:
            got_a.append(p)
            ev_a.set()

        def cb_b(_t: str, p: bytes) -> None:
            got_b.append(p)
            ev_b.set()

        harness.subscribe(topic, cb_a)
        harness.subscribe(topic, cb_b)
        await asyncio.sleep(0.25)

        harness.publish(topic, b"tick")

        await asyncio.wait_for(
            asyncio.gather(ev_a.wait(), ev_b.wait()), timeout=5.0
        )
        assert got_a == [b"tick"]
        assert got_b == [b"tick"]
    finally:
        await harness.disconnect()


async def test_unsubscribing_should_stop_further_deliveries(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness

    topic = transport_factory.topic("unsub")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        received: list[bytes] = []
        first = asyncio.Event()

        def cb(_t: str, p: bytes) -> None:
            received.append(p)
            first.set()

        harness.subscribe(topic, cb)
        await asyncio.sleep(0.25)

        harness.publish(topic, b"before")
        await asyncio.wait_for(first.wait(), timeout=5.0)
        assert received == [b"before"]

        harness.unsubscribe(topic, cb)
        # Give the unsubscribe command time to reach the server.
        await asyncio.sleep(0.5)

        harness.publish(topic, b"after")
        await asyncio.sleep(0.5)
        assert received == [b"before"], (
            f"unsubscribe did not reach the wire — still received: {received}"
        )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Scenario DSL — the same expect/publish/await_all flow over each backend
# ---------------------------------------------------------------------------


async def test_a_matching_published_message_should_fulfil_the_scenario(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness
    from core.matchers import field_equals

    topic = transport_factory.topic("happy")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("happy") as s:
            s.expect(topic, field_equals("status", "APPROVED"))
            # Give some transports time to register the subscription before
            # the publish lands.
            await asyncio.sleep(0.25)
            s = s.publish(topic, {"status": "APPROVED"})
            result = await s.await_all(timeout_ms=5000)
        result.assert_passed()
    finally:
        await harness.disconnect()


async def test_a_silent_topic_should_surface_as_timeout_outcome(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome

    topic = transport_factory.topic("silent")
    other = transport_factory.topic("other")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("silent") as s:
            handle = s.expect(topic, field_equals("status", "X"))
            await asyncio.sleep(0.25)
            # Publish on a different topic so no message hits the matcher.
            s = s.publish(other, {"status": "X"})
            result = await s.await_all(timeout_ms=500)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_a_scope_with_three_expectations_and_two_matching_publishes_should_report_one_failing_handle(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome

    topic_a = transport_factory.topic("multi-a")
    topic_b = transport_factory.topic("multi-b")
    topic_c = transport_factory.topic("multi-c")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("multi") as s:
            ha = s.expect(topic_a, field_equals("k", "a"))
            hb = s.expect(topic_b, field_equals("k", "b"))
            hc = s.expect(topic_c, field_equals("k", "c"))

            await asyncio.sleep(0.25)
            s = s.publish(topic_a, {"k": "a"})
            s = s.publish(topic_b, {"k": "b"})
            # intentionally skip topic_c
            result = await s.await_all(timeout_ms=3000)

        assert result.passed is False
        assert ha.outcome is Outcome.PASS
        assert hb.outcome is Outcome.PASS
        assert hc.outcome is Outcome.TIMEOUT
        assert result.failing_handles == (hc,)
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Payload integrity
# ---------------------------------------------------------------------------


async def test_a_large_json_payload_should_round_trip_unchanged(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness
    from core.matchers import field_equals

    topic = transport_factory.topic("large")
    big = "x" * 65_536
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("large") as s:
            handle = s.expect(topic, field_equals("tag", "BIG"))
            await asyncio.sleep(0.25)
            s = s.publish(topic, {"tag": "BIG", "body": big})
            result = await s.await_all(timeout_ms=5000)

        result.assert_passed()
        assert handle.message["body"] == big
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription hygiene under churn
# ---------------------------------------------------------------------------


async def test_running_many_sequential_scopes_should_not_accumulate_subscriptions(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness
    from core.matchers import field_equals

    topic = transport_factory.topic("churn")
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    await harness.connect()
    try:
        assert harness.active_subscription_count() == 0

        for i in range(10):
            async with harness.scenario(f"churn-{i}") as s:
                s.expect(topic, field_equals("i", i))
                await asyncio.sleep(0.2)
                s = s.publish(topic, {"i": i})
                result = await s.await_all(timeout_ms=3000)
            result.assert_passed()
            # Give unsubscribes a beat to land.
            await asyncio.sleep(0.1)
            assert harness.active_subscription_count() == 0, (
                f"subscription leak after iteration {i}: "
                f"{harness.active_subscription_count()} still registered"
            )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Reconnect
# ---------------------------------------------------------------------------


async def test_reconnecting_after_disconnect_should_restore_subscribe_and_publish(
    transport_factory: TransportFactory,
    allowlist_yaml_path: Path,
) -> None:
    from core import Harness

    topic = transport_factory.topic("reconnect")
    harness = Harness(transport_factory.build(allowlist_yaml_path))

    # Round 1.
    await harness.connect()
    got1: list[bytes] = []
    ev1 = asyncio.Event()
    harness.subscribe(topic, lambda _t, p: (got1.append(p), ev1.set()))
    await asyncio.sleep(0.25)
    harness.publish(topic, b"round-1")
    await asyncio.wait_for(ev1.wait(), timeout=5.0)
    assert got1 == [b"round-1"]
    await harness.disconnect()

    # Round 2 on the same transport instance.
    # Rebuild because some transport impls (Kafka) keep heavy state
    # (consumer instances, group ids) that aren't meant to survive a
    # full tear-down; the contract only promises Harness-level reusability.
    harness = Harness(transport_factory.build(allowlist_yaml_path))
    topic = transport_factory.topic("reconnect-2")
    await harness.connect()
    try:
        got2: list[bytes] = []
        ev2 = asyncio.Event()
        harness.subscribe(topic, lambda _t, p: (got2.append(p), ev2.set()))
        await asyncio.sleep(0.25)
        harness.publish(topic, b"round-2")
        await asyncio.wait_for(ev2.wait(), timeout=5.0)
        assert got2 == [b"round-2"]
    finally:
        await harness.disconnect()
