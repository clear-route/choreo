"""End-to-end tests for KafkaTransport-specific behaviours.

The shared contract suite under ``tests/e2e/contract/`` already exercises the
Transport Protocol surface against every backend. This file covers behaviours
that only a real Kafka cluster can surface — async consumer-group joins,
partition assignment, offset-reset semantics, and the quirks of aiokafka's
producer/consumer bridge.

Runs only under ``pytest -m e2e``. Requires:

    docker compose -f docker/compose.e2e.yaml --profile kafka up -d
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
# Concurrency
# ---------------------------------------------------------------------------


async def test_two_concurrent_scenarios_on_the_same_kafka_topic_should_only_fulfil_the_handle_whose_correlation_matches(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Both scenarios subscribe to the same topic; Kafka broadcasts to each
    consumer group. The scope's correlation filter must drop the one whose
    correlation doesn't match."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import KafkaTransport

    topic = _topic("concurrent")
    # DictFieldPolicy is what exercises the per-scope correlation filter;
    # the harness's default is NoCorrelationPolicy (broadcast). Without an
    # explicit policy here the "foreign correlation dropped" expectation
    # doesn't hold — every scope sees every message (ADR-0019).
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:

        async def run(key: str) -> tuple[Any, Any]:
            async with harness.scenario(f"scope-{key}") as s:
                handle = s.expect(topic, field_equals("k", key))
                if key == "a":
                    s = s.publish(topic, {"k": key})
                else:
                    # Non-matching correlation on the same topic — bytes
                    # path bypasses the TEST- prefix guard to simulate
                    # foreign traffic.
                    s = s.publish(
                        topic,
                        json.dumps({"correlation_id": "foreign", "k": "b"}).encode(),
                    )
                return handle, await s.await_all(timeout_ms=4000)

        (handle_a, result_a), (handle_b, result_b) = await asyncio.gather(run("a"), run("b"))

        result_a.assert_passed()
        assert handle_a.outcome is Outcome.PASS

        assert result_b.passed is False
        assert handle_b.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_rapid_consecutive_publishes_should_arrive_at_the_subscriber_in_order(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Kafka guarantees per-partition ordering. The auto-created topics in
    this suite have a single partition (cp-kafka's default), so every
    publish-to-the-same-topic sequence must arrive in order."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic = _topic("burst")
    count = 30
    received: list[int] = []
    done = asyncio.Event()

    def on_msg(_t: str, payload: bytes) -> None:
        received.append(int(payload))
        if len(received) == count:
            done.set()

    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, on_msg)
        for i in range(count):
            harness.publish(topic, str(i).encode())

        await asyncio.wait_for(done.wait(), timeout=10.0)
        assert received == list(range(count)), (
            f"messages arrived out of order: first divergence at index "
            f"{next((i for i, v in enumerate(received) if v != i), None)}"
        )
    finally:
        await harness.disconnect()


async def test_a_scenario_should_drop_messages_carrying_a_foreign_correlation_id(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Any subscriber on a topic receives every publish to it; the scope's
    correlation filter is the only isolation boundary."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import KafkaTransport

    topic = _topic("foreign_corr")
    # See note above on DictFieldPolicy — this test exists specifically to
    # verify correlation filtering, so the policy must actually filter.
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:
        async with harness.scenario("foreign") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            s = s.publish(
                topic,
                json.dumps({"correlation_id": "not-us", "k": "v"}).encode(),
            )
            result = await s.await_all(timeout_ms=1500)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
        assert handle.attempts == 0
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription lifecycle
# ---------------------------------------------------------------------------


async def test_unsubscribing_should_stop_further_deliveries_over_the_wire(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Unsubscribe must tear down the AIOKafkaConsumer, not just remove the
    callback from a dict. A regression where the consumer keeps polling
    would still invoke the callback for later publishes."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic = _topic("unsub")
    first_arrived = asyncio.Event()
    received: list[bytes] = []

    def cb(_t: str, p: bytes) -> None:
        received.append(p)
        first_arrived.set()

    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, cb)
        harness.publish(topic, b"before")
        await asyncio.wait_for(first_arrived.wait(), timeout=8.0)
        assert received == [b"before"]

        harness.unsubscribe(topic, cb)
        # Give the consumer-stop coroutine time to actually tear down.
        await asyncio.sleep(1.0)

        harness.publish(topic, b"after")
        await asyncio.sleep(1.0)
        assert received == [b"before"], (
            f"unsubscribe did not tear down the consumer — still received: {received}"
        )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Reader-task resilience
# ---------------------------------------------------------------------------


async def test_a_callback_that_raises_should_not_prevent_later_messages_from_being_delivered(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """An exception in one callback must not kill the reader task or block
    another subscriber's delivery."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic = _topic("bad_cb")
    good_got: list[bytes] = []
    good_event = asyncio.Event()

    def bad(_t: str, _p: bytes) -> None:
        raise RuntimeError("deliberate failure in user callback")

    def good(_t: str, p: bytes) -> None:
        good_got.append(p)
        if len(good_got) >= 2:
            good_event.set()

    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, bad)
        harness.subscribe(topic, good)

        harness.publish(topic, b"one")
        harness.publish(topic, b"two")

        await asyncio.wait_for(good_event.wait(), timeout=8.0)
        assert good_got == [b"one", b"two"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Cross-harness isolation + connection errors
# ---------------------------------------------------------------------------


async def test_two_independent_harnesses_on_the_same_broker_should_not_see_each_others_unsubscribed_traffic(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Kafka routes by topic, so an AIOKafkaConsumer subscribed to topic X
    must not receive any record on topic Y even when both harnesses share
    the broker."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic_x = _topic("iso_x")
    topic_y = _topic("iso_y")

    h_x = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    h_y = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await h_x.connect()
    await h_y.connect()
    try:
        x_got: list[bytes] = []
        y_got: list[bytes] = []
        x_event = asyncio.Event()
        y_event = asyncio.Event()

        h_x.subscribe(topic_x, lambda _t, p: (x_got.append(p), x_event.set()))
        h_y.subscribe(topic_y, lambda _t, p: (y_got.append(p), y_event.set()))

        h_x.publish(topic_x, b"x-message")
        h_y.publish(topic_y, b"y-message")

        await asyncio.wait_for(asyncio.gather(x_event.wait(), y_event.wait()), timeout=8.0)
        await asyncio.sleep(0.5)
        assert x_got == [b"x-message"]
        assert y_got == [b"y-message"]
    finally:
        await h_x.disconnect()
        await h_y.disconnect()


async def test_connecting_to_an_unreachable_kafka_port_should_raise_a_transport_error(
    _kafka_available: bool,
) -> None:
    """A real transport must surface a clear error when the broker is not
    reachable — tests need the distinction between 'wrong config' and
    'something weird happened'."""
    from choreo.transports import KafkaTransport, TransportError

    transport = KafkaTransport(
        bootstrap_servers=["127.0.0.1:1"],
        connect_timeout_s=2.0,
    )
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert "127.0.0.1:1" in str(exc.value) or "could not connect" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Payload integrity
# ---------------------------------------------------------------------------


async def test_a_large_json_payload_should_round_trip_unchanged(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Kafka's default max.message.bytes is 1 MiB; 64 KiB sits well below
    that but is large enough to force multiple TCP segments and exercise
    any truncation / framing bug in the aiokafka bridge."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import KafkaTransport

    topic = _topic("large")
    big = "x" * 65_536
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("large") as s:
            handle = s.expect(topic, field_equals("tag", "BIG"))
            s = s.publish(topic, {"tag": "BIG", "body": big})
            result = await s.await_all(timeout_ms=8000)

        result.assert_passed()
        assert handle.message["body"] == big
        assert len(handle.message["body"]) == 65_536
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Kafka-specific: offset semantics
# ---------------------------------------------------------------------------


async def test_a_subscriber_joining_after_a_publish_should_not_replay_the_earlier_message(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Consumers in KafkaTransport use ``auto_offset_reset=latest`` so the
    NATS-like 'late subscriber gets nothing' semantics hold. If a regression
    switched the consumer to ``earliest``, a subscribe following a publish
    would suddenly replay old records — breaking every test that assumes
    a silent topic means timeout."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic = _topic("late_sub")
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        # Publish before any subscriber exists.
        harness.publish(topic, b"pre-subscribe")
        # Let the PUB land on the broker.
        await asyncio.sleep(1.0)

        received: list[bytes] = []
        ev = asyncio.Event()

        def cb(_t: str, p: bytes) -> None:
            received.append(p)
            ev.set()

        harness.subscribe(topic, cb)
        # If the consumer replayed from earliest, we'd see "pre-subscribe".
        await asyncio.sleep(2.0)

        assert received == [], f"consumer replayed from earliest offset — got {received}"

        # Now a post-subscribe publish should arrive.
        harness.publish(topic, b"post-subscribe")
        await asyncio.wait_for(ev.wait(), timeout=8.0)
        assert received == [b"post-subscribe"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Kafka-specific: consumer-group isolation
# ---------------------------------------------------------------------------


async def test_two_subscribes_on_one_harness_should_each_receive_every_message(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """KafkaTransport gives each subscribe() call its own consumer group so
    broadcast fan-out matches NATS/Redis. Without that, two subscribes would
    share a single group and split partitions — only one callback would see
    each message."""
    from choreo import Harness
    from choreo.transports import KafkaTransport

    topic = _topic("fanout")
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        got_a: list[bytes] = []
        got_b: list[bytes] = []
        ev_a = asyncio.Event()
        ev_b = asyncio.Event()

        harness.subscribe(topic, lambda _t, p: (got_a.append(p), ev_a.set()))
        harness.subscribe(topic, lambda _t, p: (got_b.append(p), ev_b.set()))

        # Both consumer joins must land before publish.
        await asyncio.sleep(1.5)
        harness.publish(topic, b"tick")

        await asyncio.wait_for(asyncio.gather(ev_a.wait(), ev_b.wait()), timeout=8.0)
        assert got_a == [b"tick"]
        assert got_b == [b"tick"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Scope invariants under real-wire latency
# ---------------------------------------------------------------------------


async def test_a_timed_out_handle_should_remain_timed_out_even_after_a_late_message_arrives(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """If the expected publish arrives after the scope's deadline, the
    handle must stay TIMEOUT — it must not flip back to PASS.

    Opts into `test_namespace()` because the late publish echoes
    `s.correlation_id` onto its payload (ADR-0019)."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import KafkaTransport

    topic = _topic("late")
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:
        async with harness.scenario("late") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            corr = s.correlation_id

            async def publish_late() -> None:
                await asyncio.sleep(0.75)  # past the 200 ms scope deadline
                harness.publish(topic, {"correlation_id": corr, "k": "v"})

            late_task = asyncio.create_task(publish_late())
            # Trigger the scope with a dummy publish so the state machine
            # advances without the real message going out early.
            dummy_topic = _topic("late_dummy")
            s = s.publish(dummy_topic, b"ignored")
            result = await s.await_all(timeout_ms=200)

        await late_task
        await asyncio.sleep(0.5)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription hygiene under churn
# ---------------------------------------------------------------------------


async def test_running_many_sequential_scopes_should_not_accumulate_subscriptions(
    allowlist_yaml_path: Path,
    kafka_bootstrap: str,
    _kafka_available: bool,
) -> None:
    """Kafka consumers are heavy; a leak would pile up consumer groups
    server-side and AIOKafkaConsumer instances client-side. Fewer iterations
    than the NATS equivalent because each Kafka sub takes ~500ms to join."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import KafkaTransport

    topic = _topic("churn")
    harness = Harness(
        KafkaTransport(bootstrap_servers=[kafka_bootstrap], allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        assert harness.active_subscription_count() == 0

        for i in range(5):
            async with harness.scenario(f"churn-{i}") as s:
                s.expect(topic, field_equals("i", i))
                s = s.publish(topic, {"i": i})
                result = await s.await_all(timeout_ms=5000)
            result.assert_passed()
            await asyncio.sleep(0.2)
            assert harness.active_subscription_count() == 0, (
                f"subscription leak after iteration {i}: "
                f"{harness.active_subscription_count()} still registered"
            )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _topic(prefix: str) -> str:
    """Kafka permits dots in topic names but discourages them; underscores
    are the safer separator. Every test gets a unique topic so concurrent
    runs against the same broker cannot interfere."""
    return f"e2e_kafka_edge_{prefix}_{uuid.uuid4().hex[:8]}"
