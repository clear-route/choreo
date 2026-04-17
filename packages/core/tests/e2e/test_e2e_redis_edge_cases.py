"""End-to-end tests for RedisTransport-specific behaviours.

The shared contract suite under ``tests/e2e/contract/`` already exercises the
Transport Protocol surface against every backend. This file covers behaviours
that only a real Redis broker can surface — PUBLISH-with-no-subscribers,
multi-channel dispatch from a single pubsub connection, and the quirks of
redis-py's async pubsub bridge.

Runs only under ``pytest -m e2e``. Requires:

    docker compose -f docker/compose.e2e.yaml --profile redis up -d
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


async def test_two_concurrent_scenarios_on_the_same_redis_channel_should_only_fulfil_the_handle_whose_correlation_matches(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Redis PUBLISH fans out to every subscriber on the channel. The
    scope's correlation filter is the only isolation boundary."""
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RedisTransport

    channel = _channel("concurrent")
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:

        async def run(key: str) -> tuple[Any, Any]:
            async with harness.scenario(f"scope-{key}") as s:
                handle = s.expect(channel, field_equals("k", key))
                if key == "a":
                    s = s.publish(channel, {"k": key})
                else:
                    s = s.publish(
                        channel,
                        json.dumps(
                            {"correlation_id": "foreign", "k": "b"}
                        ).encode(),
                    )
                return handle, await s.await_all(timeout_ms=1500)

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
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Redis guarantees per-connection FIFO. The reader task dispatches
    messages in arrival order, so a burst of 50 publishes on one channel
    must land on the subscriber in the order they went out."""
    from core import Harness
    from core.transports import RedisTransport

    channel = _channel("burst")
    count = 50
    received: list[int] = []
    done = asyncio.Event()

    def on_msg(_t: str, payload: bytes) -> None:
        received.append(int(payload))
        if len(received) == count:
            done.set()

    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(channel, on_msg)
        for i in range(count):
            harness.publish(channel, str(i).encode())

        await asyncio.wait_for(done.wait(), timeout=5.0)
        assert received == list(range(count))
    finally:
        await harness.disconnect()


async def test_a_scenario_should_drop_messages_carrying_a_foreign_correlation_id(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RedisTransport

    channel = _channel("foreign_corr")
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("foreign") as s:
            handle = s.expect(channel, field_equals("k", "v"))
            s = s.publish(
                channel,
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
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Unsubscribe must send the UNSUBSCRIBE command so the server stops
    delivering to this connection. A regression that just drops the
    callback dict entry would leak subscriptions server-side."""
    from core import Harness
    from core.transports import RedisTransport

    channel = _channel("unsub")
    first_arrived = asyncio.Event()
    received: list[bytes] = []

    def cb(_t: str, p: bytes) -> None:
        received.append(p)
        first_arrived.set()

    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(channel, cb)
        harness.publish(channel, b"before")
        await asyncio.wait_for(first_arrived.wait(), timeout=3.0)
        assert received == [b"before"]

        harness.unsubscribe(channel, cb)
        # Give the UNSUBSCRIBE command time to reach the server.
        await asyncio.sleep(0.3)

        harness.publish(channel, b"after")
        await asyncio.sleep(0.3)
        assert received == [b"before"], (
            f"unsubscribe did not reach the wire — still received: {received}"
        )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Reader-task resilience
# ---------------------------------------------------------------------------


async def test_a_callback_that_raises_should_not_prevent_later_messages_from_being_delivered(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    """One broken callback must not kill the pubsub reader loop — a silent
    wedge would mean every subsequent message is lost."""
    from core import Harness
    from core.transports import RedisTransport

    channel = _channel("bad_cb")
    good_got: list[bytes] = []
    good_event = asyncio.Event()

    def bad(_t: str, _p: bytes) -> None:
        raise RuntimeError("deliberate failure in user callback")

    def good(_t: str, p: bytes) -> None:
        good_got.append(p)
        if len(good_got) >= 2:
            good_event.set()

    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        harness.subscribe(channel, bad)
        harness.subscribe(channel, good)

        harness.publish(channel, b"one")
        harness.publish(channel, b"two")

        await asyncio.wait_for(good_event.wait(), timeout=3.0)
        assert good_got == [b"one", b"two"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Cross-harness isolation + connection errors
# ---------------------------------------------------------------------------


async def test_two_independent_harnesses_on_the_same_broker_should_not_see_each_others_unsubscribed_traffic(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    from core import Harness
    from core.transports import RedisTransport

    channel_x = _channel("iso_x")
    channel_y = _channel("iso_y")

    h_x = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    h_y = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await h_x.connect()
    await h_y.connect()
    try:
        x_got: list[bytes] = []
        y_got: list[bytes] = []
        x_event = asyncio.Event()
        y_event = asyncio.Event()

        h_x.subscribe(channel_x, lambda _t, p: (x_got.append(p), x_event.set()))
        h_y.subscribe(channel_y, lambda _t, p: (y_got.append(p), y_event.set()))

        h_x.publish(channel_x, b"x-message")
        h_y.publish(channel_y, b"y-message")

        await asyncio.wait_for(
            asyncio.gather(x_event.wait(), y_event.wait()), timeout=3.0
        )
        await asyncio.sleep(0.2)
        assert x_got == [b"x-message"]
        assert y_got == [b"y-message"]
    finally:
        await h_x.disconnect()
        await h_y.disconnect()


async def test_connecting_to_an_unreachable_redis_port_should_raise_a_transport_error(
    _redis_available: bool,
) -> None:
    from core.transports import RedisTransport, TransportError

    transport = RedisTransport(
        url="redis://127.0.0.1:1/0",
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
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Redis has no hard ceiling on message size (proto-max-bulk-len is
    512 MiB by default); 64 KiB forces multiple TCP segments and catches
    any framing bug in the bridge."""
    from core import Harness
    from core.matchers import field_equals
    from core.transports import RedisTransport

    channel = _channel("large")
    big = "x" * 65_536
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("large") as s:
            handle = s.expect(channel, field_equals("tag", "BIG"))
            s = s.publish(channel, {"tag": "BIG", "body": big})
            result = await s.await_all(timeout_ms=3000)

        result.assert_passed()
        assert handle.message["body"] == big
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Redis-specific: publish with no subscriber is fire-and-forget
# ---------------------------------------------------------------------------


async def test_publishing_to_a_channel_with_no_subscribers_should_not_raise(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Redis PUBLISH returns the number of clients that received the
    message (zero here). The transport must not treat zero-recipients as
    an error — this is an intentional property of fire-and-forget
    pub/sub."""
    from core import Harness
    from core.transports import RedisTransport

    channel = _channel("no_subscriber")
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        # Must not raise.
        harness.publish(channel, b"ghost")
        # Give the publish task a chance to complete.
        await asyncio.sleep(0.1)
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Redis-specific: multi-channel dispatch from one pubsub connection
# ---------------------------------------------------------------------------


async def test_multiple_channels_on_one_connection_should_each_receive_only_their_own_publishes(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    """RedisTransport runs one pubsub connection per harness and routes
    messages to the right callback based on the message's channel field.
    Three channels on the same pubsub must stay isolated — a cross-wire
    would mean the reader dispatches by order or by last-subscribed
    rather than by message.channel."""
    from core import Harness
    from core.transports import RedisTransport

    channels = [_channel(f"multi_{k}") for k in ("a", "b", "c")]
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        buckets: list[list[bytes]] = [[], [], []]
        events: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
        for idx, channel in enumerate(channels):
            def make_cb(i: int):
                def cb(_t: str, p: bytes) -> None:
                    buckets[i].append(p)
                    events[i].set()
                return cb
            harness.subscribe(channel, make_cb(idx))

        await asyncio.sleep(0.2)
        for idx, channel in enumerate(channels):
            harness.publish(channel, f"payload-{idx}".encode())

        await asyncio.wait_for(
            asyncio.gather(*(e.wait() for e in events)), timeout=3.0
        )
        assert buckets[0] == [b"payload-0"]
        assert buckets[1] == [b"payload-1"]
        assert buckets[2] == [b"payload-2"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Redis-specific: fan-out across three harnesses
# ---------------------------------------------------------------------------


async def test_three_harnesses_subscribed_to_a_shared_channel_should_all_receive_a_single_publish(
    allowlist_yaml_path: Path,
    redis_url: str,
    _redis_available: bool,
) -> None:
    """Redis PUBLISH broadcasts to every client subscribed to the
    channel. Three harnesses on the same broker, same channel, must each
    see the single publish."""
    from core import Harness
    from core.transports import RedisTransport

    channel = _channel("shared")
    subs = [
        Harness(RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path))
        for _ in range(3)
    ]
    pub = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
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
            h.subscribe(channel, make_cb(idx))

        await asyncio.sleep(0.3)
        pub.publish(channel, b"broadcast")

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
    redis_url: str,
    _redis_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import Outcome
    from core.transports import RedisTransport

    channel = _channel("late")
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        async with harness.scenario("late") as s:
            handle = s.expect(channel, field_equals("k", "v"))
            corr = s.correlation_id

            async def publish_late() -> None:
                await asyncio.sleep(0.25)
                harness.publish(channel, {"correlation_id": corr, "k": "v"})

            late_task = asyncio.create_task(publish_late())
            dummy_channel = _channel("late_dummy")
            s = s.publish(dummy_channel, b"ignored")
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
    redis_url: str,
    _redis_available: bool,
) -> None:
    from core import Harness
    from core.matchers import field_equals
    from core.transports import RedisTransport

    channel = _channel("churn")
    harness = Harness(
        RedisTransport(url=redis_url, allowlist_path=allowlist_yaml_path)
    )
    await harness.connect()
    try:
        assert harness.active_subscription_count() == 0

        for i in range(30):
            async with harness.scenario(f"churn-{i}") as s:
                s.expect(channel, field_equals("i", i))
                s = s.publish(channel, {"i": i})
                result = await s.await_all(timeout_ms=1000)
            result.assert_passed()
            await asyncio.sleep(0.02)
            assert harness.active_subscription_count() == 0, (
                f"subscription leak after iteration {i}: "
                f"{harness.active_subscription_count()} still registered"
            )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _channel(prefix: str) -> str:
    """Redis channels are arbitrary binary-safe strings. Colons are the
    conventional separator. Every test gets a unique channel so concurrent
    runs don't collide."""
    return f"e2e:redis:edge:{prefix}:{uuid.uuid4().hex[:8]}"
