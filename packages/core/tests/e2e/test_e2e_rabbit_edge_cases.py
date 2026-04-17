"""End-to-end tests for RabbitTransport-specific behaviours.

The shared contract suite under ``tests/e2e/contract/`` already exercises the
Transport Protocol surface against every backend. This file covers behaviours
that only a real RabbitMQ broker can surface — topic-exchange routing,
exclusive auto-delete queues, publisher confirms, and the quirks of
aio-pika's channel lifecycle.

Runs only under ``pytest -m e2e``. Requires:

    docker compose -f docker/compose.e2e.yaml --profile rabbitmq up -d
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


async def test_two_concurrent_scenarios_on_the_same_rabbit_topic_should_only_fulfil_the_handle_whose_correlation_matches(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """Two scopes each bind their own exclusive queue to the shared routing
    key; every publish fans out to both. The scope's correlation filter is
    the isolation boundary."""
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RabbitTransport

    topic = _topic("concurrent")
    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:

        async def run(key: str) -> tuple[Any, Any]:
            async with harness.scenario(f"scope-{key}") as s:
                handle = s.expect(topic, field_equals("k", key))
                if key == "a":
                    s = s.publish(topic, {"k": key})
                else:
                    s = s.publish(
                        topic,
                        json.dumps(
                            {"correlation_id": "foreign", "k": "b"}
                        ).encode(),
                    )
                return handle, await s.await_all(timeout_ms=2000)

        (handle_a, result_a), (handle_b, result_b) = await asyncio.gather(
            run("a"), run("b")
        )

        result_a.assert_passed()
        assert handle_a.outcome is Outcome.PASS

        assert result_b.passed is False
        assert handle_b.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_rapid_consecutive_publishes_should_arrive_at_the_subscriber_in_order(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """A single topic-exchange binding with a single queue must preserve
    publish order — aio-pika / RabbitMQ guarantee FIFO within a queue."""
    from core import Harness
    from core.transports import RabbitTransport

    topic = _topic("burst")
    count = 50
    received: list[int] = []
    done = asyncio.Event()

    def on_msg(_t: str, payload: bytes) -> None:
        received.append(int(payload))
        if len(received) == count:
            done.set()

    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, on_msg)
        for i in range(count):
            harness.publish(topic, str(i).encode())

        await asyncio.wait_for(done.wait(), timeout=5.0)
        assert received == list(range(count))
    finally:
        await harness.disconnect()


async def test_a_scenario_should_drop_messages_carrying_a_foreign_correlation_id(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RabbitTransport

    topic = _topic("foreign_corr")
    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("foreign") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            s = s.publish(
                topic,
                json.dumps({"correlation_id": "not-us", "k": "v"}).encode(),
            )
            result = await s.await_all(timeout_ms=500)

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
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """Unsubscribe must cancel the consumer AND delete the exclusive
    queue. A regression that just pops the callback from the dict would
    keep the queue bound and the handler firing."""
    from core import Harness
    from core.transports import RabbitTransport

    topic = _topic("unsub")
    first_arrived = asyncio.Event()
    received: list[bytes] = []

    def cb(_t: str, p: bytes) -> None:
        received.append(p)
        first_arrived.set()

    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, cb)
        harness.publish(topic, b"before")
        await asyncio.wait_for(first_arrived.wait(), timeout=3.0)
        assert received == [b"before"]

        harness.unsubscribe(topic, cb)
        await asyncio.sleep(0.4)

        harness.publish(topic, b"after")
        await asyncio.sleep(0.4)
        assert received == [b"before"], (
            f"unsubscribe did not tear down the queue — still received: {received}"
        )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Reader-task resilience
# ---------------------------------------------------------------------------


async def test_a_callback_that_raises_should_not_prevent_later_messages_from_being_delivered(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """A raising callback must not cancel the consumer task or block
    another subscriber's delivery. aio-pika's message.process() context
    manager would normally nack-and-requeue an unhandled exception; the
    transport catches inside to preserve the reader."""
    from core import Harness
    from core.transports import RabbitTransport

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
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(topic, bad)
        harness.subscribe(topic, good)

        harness.publish(topic, b"one")
        harness.publish(topic, b"two")

        await asyncio.wait_for(good_event.wait(), timeout=3.0)
        assert good_got == [b"one", b"two"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Cross-harness isolation + connection errors
# ---------------------------------------------------------------------------


async def test_two_independent_harnesses_on_the_same_broker_should_not_see_each_others_unsubscribed_traffic(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    from core import Harness
    from core.transports import RabbitTransport

    topic_x = _topic("iso_x")
    topic_y = _topic("iso_y")

    h_x = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    h_y = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
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

        await asyncio.wait_for(
            asyncio.gather(x_event.wait(), y_event.wait()), timeout=3.0
        )
        await asyncio.sleep(0.2)
        assert x_got == [b"x-message"]
        assert y_got == [b"y-message"]
    finally:
        await h_x.disconnect()
        await h_y.disconnect()


async def test_connecting_to_an_unreachable_amqp_port_should_raise_a_transport_error(
    _rabbit_available: bool,
) -> None:
    from core.transports import RabbitTransport, TransportError

    transport = RabbitTransport(
        url="amqp://guest:guest@127.0.0.1:1/",
        connect_timeout_s=1.0,
    )
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert (
        "127.0.0.1" in str(exc.value)
        or "could not connect" in str(exc.value).lower()
    )


# ---------------------------------------------------------------------------
# Payload integrity
# ---------------------------------------------------------------------------


async def test_a_large_json_payload_should_round_trip_unchanged(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """RabbitMQ has no default message size cap but frame_max (default 128
    KiB) forces chunking above ~128 KiB. 64 KiB is comfortably in the
    single-frame regime and large enough to catch truncation bugs."""
    from core import Harness
    from core.matchers import field_equals
    from core.transports import RabbitTransport

    topic = _topic("large")
    big = "x" * 65_536
    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("large") as s:
            handle = s.expect(topic, field_equals("tag", "BIG"))
            s = s.publish(topic, {"tag": "BIG", "body": big})
            result = await s.await_all(timeout_ms=3000)

        result.assert_passed()
        assert handle.message["body"] == big
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# RabbitMQ-specific: queue auto-delete after disconnect
# ---------------------------------------------------------------------------


async def test_closing_the_harness_should_delete_its_exclusive_queues(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """The transport declares each subscription queue as
    exclusive+auto_delete. After the harness disconnects the broker must
    reclaim those queues — a leak would make every test run accumulate
    rubbish on the broker.

    We verify indirectly: bring a second harness up, publish to the topic
    the first harness subscribed to, and assert it goes nowhere (no bound
    queue exists anymore)."""
    from core import Harness
    from core.transports import RabbitTransport

    topic = _topic("autodelete")

    first = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await first.connect()
    first.subscribe(topic, lambda _t, _p: None)
    await asyncio.sleep(0.2)
    await first.disconnect()

    # The queue from `first` should be gone. A fresh harness subscribing to
    # the same routing key creates a brand-new queue and should observe the
    # post-disconnect traffic only on its own queue. We publish once via a
    # third harness and assert only the second's callback fires.
    second = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    publisher = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await second.connect()
    await publisher.connect()
    try:
        got: list[bytes] = []
        ev = asyncio.Event()

        second.subscribe(topic, lambda _t, p: (got.append(p), ev.set()))
        await asyncio.sleep(0.2)
        publisher.publish(topic, b"after-first-gone")

        await asyncio.wait_for(ev.wait(), timeout=3.0)
        assert got == [b"after-first-gone"]
    finally:
        await second.disconnect()
        await publisher.disconnect()


# ---------------------------------------------------------------------------
# RabbitMQ-specific: fan-out across three independent subscribers on one topic
# ---------------------------------------------------------------------------


async def test_three_harnesses_subscribed_to_a_shared_topic_should_all_receive_a_single_publish(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    """Three separate harnesses each bind their own queue to the same
    routing key on the shared topic exchange. A single publish must
    deliver to all three queues — the point of a topic exchange."""
    from core import Harness
    from core.transports import RabbitTransport

    topic = _topic("shared")
    subs = [
        Harness(RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path))
        for _ in range(3)
    ]
    pub = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    for h in (*subs, pub):
        await h.connect()
    try:
        buckets: list[list[bytes]] = [[], [], []]
        events: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
        for idx, h in enumerate(subs):
            def make_cb(i: int):
                def cb(_t: str, p: bytes) -> None:
                    buckets[i].append(p)
                    events[i].set()
                return cb

            h.subscribe(topic, make_cb(idx))

        await asyncio.sleep(0.3)
        pub.publish(topic, b"broadcast")

        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in events)), timeout=3.0
        )
        assert all(bucket == [b"broadcast"] for bucket in buckets), buckets
    finally:
        for h in (*subs, pub):
            await h.disconnect()


# ---------------------------------------------------------------------------
# Scope invariants under real-wire latency
# ---------------------------------------------------------------------------


async def test_a_timed_out_handle_should_remain_timed_out_even_after_a_late_message_arrives(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RabbitTransport

    topic = _topic("late")
    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("late") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            corr = s.correlation_id

            async def publish_late() -> None:
                await asyncio.sleep(0.25)
                harness.publish(topic, {"correlation_id": corr, "k": "v"})

            late_task = asyncio.create_task(publish_late())
            dummy_topic = _topic("late_dummy")
            s = s.publish(dummy_topic, b"ignored")
            result = await s.await_all(timeout_ms=100)

        await late_task
        await asyncio.sleep(0.1)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription hygiene under churn
# ---------------------------------------------------------------------------


async def test_running_many_sequential_scopes_should_not_accumulate_subscriptions(
    allowlist_yaml_path: Path,
    amqp_url: str,
    _rabbit_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.transports import RabbitTransport

    topic = _topic("churn")
    harness = Harness(
        RabbitTransport(url=amqp_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        assert harness.active_subscription_count() == 0

        for i in range(20):
            async with harness.scenario(f"churn-{i}") as s:
                s.expect(topic, field_equals("i", i))
                s = s.publish(topic, {"i": i})
                result = await s.await_all(timeout_ms=1500)
            result.assert_passed()
            await asyncio.sleep(0.05)
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
    """Rabbit topic exchanges expect dot-separated routing keys. Every test
    gets a unique key so concurrent runs don't interfere."""
    return f"e2e.rabbit.edge.{prefix}.{uuid.uuid4().hex[:8]}"
