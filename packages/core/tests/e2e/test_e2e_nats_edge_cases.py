"""End-to-end tests that exercise behaviours MockTransport cannot simulate.

MockTransport delivers synchronously inside `publish()`. That removes:
  - async boundaries between publish and callback dispatch
  - real subscribe-before-publish TCP ordering
  - real unsubscribe reaching the server
  - connection errors, reconnect cycles, cross-connection isolation
  - user-callback exceptions propagating through async reader tasks

Each test below is designed to fail if the NatsTransport bridge regresses
on one of those properties, without depending on MockTransport semantics.

Runs only under `pytest -m e2e`.
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
# Concurrency: correlation routing, ordering, parallel scenarios
# ---------------------------------------------------------------------------


async def test_two_concurrent_scenarios_on_the_same_topic_should_only_fulfil_the_handle_whose_correlation_matches(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Both scenarios subscribe to the same subject. Broker fan-outs the
    single publish to both subscriber callbacks. The scope's correlation
    filter must drop the one whose correlation doesn't match — without that,
    a single test message would fulfil every concurrent scope on the topic."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic = _unique_topic("concurrent")
    # Per-scope correlation filter is what this test exercises — the
    # harness default is NoCorrelationPolicy (broadcast) since ADR-0019,
    # so opt in explicitly.
    harness = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:

        async def run(key: str) -> tuple[Any, Any]:
            async with harness.scenario(f"scope-{key}") as s:
                handle = s.expect(topic, field_equals("k", key))
                # Only the "a" scenario actually publishes; "b" must time out.
                if key == "a":
                    s = s.publish(topic, {"k": key})
                else:
                    # Publish something with a foreign correlation on the same
                    # topic so B's subscriber actually sees a wire message but
                    # filters it out. Bytes path bypasses the scope's outbound
                    # TEST- prefix guard — the point of this test is to simulate
                    # non-test traffic arriving on the subscribed topic.
                    s = s.publish(
                        topic,
                        json.dumps({"correlation_id": "foreign", "k": "b"}).encode(),
                    )
                return handle, await s.await_all(timeout_ms=1500)

        (handle_a, result_a), (handle_b, result_b) = await asyncio.gather(run("a"), run("b"))

        # A matched its own publish on the wire.
        result_a.assert_passed()
        assert handle_a.outcome is Outcome.PASS

        # B's subscriber saw A's message AND its own foreign-corr message;
        # both were filtered. The handle therefore times out rather than
        # passes or fails (zero attempts reached the matcher).
        assert result_b.passed is False
        assert handle_b.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_rapid_consecutive_publishes_should_arrive_at_the_subscriber_in_order(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """If the async bridge reorders publish coroutines, bursts will show up
    out-of-order at the subscriber. Mock cannot surface this — it dispatches
    synchronously in the publish() call frame."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("burst")
    count = 50
    received: list[int] = []
    done = asyncio.Event()

    def on_msg(t: str, payload: bytes) -> None:
        received.append(int(payload))
        if len(received) == count:
            done.set()

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        harness.subscribe(topic, on_msg)
        for i in range(count):
            harness.publish(topic, str(i).encode())

        await asyncio.wait_for(done.wait(), timeout=5.0)
        assert received == list(range(count)), (
            f"messages arrived out of order: first divergence at index "
            f"{next((i for i, v in enumerate(received) if v != i), None)}"
        )
    finally:
        await harness.disconnect()


async def test_a_scenario_should_drop_messages_carrying_a_foreign_correlation_id(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Over the wire, any subscriber on a topic receives every publish.
    The scope's correlation filter is the isolation boundary. A foreign
    publish on the scope's topic must not attempt the matcher."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic = _unique_topic("foreign-corr")
    harness = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:
        async with harness.scenario("foreign") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            # Bytes path bypasses the scope's outbound TEST- prefix guard —
            # the point of this test is to simulate non-test traffic arriving
            # on the subscribed topic.
            s = s.publish(
                topic,
                json.dumps({"correlation_id": "not-us", "k": "v"}).encode(),
            )
            result = await s.await_all(timeout_ms=300)

        assert result.passed is False
        # Zero attempts means the filter ran before the matcher — not FAIL.
        assert handle.outcome is Outcome.TIMEOUT
        assert handle.attempts == 0
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription lifecycle: unsubscribe must reach the server
# ---------------------------------------------------------------------------


async def test_unsubscribing_should_stop_further_deliveries_over_the_wire(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """On MockTransport, unsubscribe just pops from a dict. On a real broker,
    the client must send an UNSUB line — a regression where unsubscribe is
    scheduled-but-never-awaited would still show the callback firing for
    later publishes."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("unsub")
    first_arrived = asyncio.Event()
    received: list[bytes] = []

    def cb(t: str, p: bytes) -> None:
        received.append(p)
        first_arrived.set()

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        harness.subscribe(topic, cb)
        harness.publish(topic, b"before")
        await asyncio.wait_for(first_arrived.wait(), timeout=2.0)
        assert received == [b"before"]

        harness.unsubscribe(topic, cb)
        # Give the UNSUB command time to reach the server.
        await asyncio.sleep(0.2)

        harness.publish(topic, b"after")
        await asyncio.sleep(0.3)
        assert received == [b"before"], (
            f"unsubscribe did not reach the wire — still received: {received}"
        )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Reader-task resilience: user callback exceptions
# ---------------------------------------------------------------------------


async def test_a_callback_that_raises_should_not_prevent_later_messages_from_being_delivered(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """If a user callback raises, the NATS reader task must survive — any
    other subscriber on the topic, and any subsequent publish to this one,
    must still be delivered. Without the try/except in `nats_handler`, an
    uncaught exception on the reader task would silently wedge the
    connection."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("bad-cb")
    good_got: list[bytes] = []
    good_event = asyncio.Event()

    def bad(t: str, p: bytes) -> None:
        raise RuntimeError("deliberate failure in user callback")

    def good(t: str, p: bytes) -> None:
        good_got.append(p)
        if len(good_got) >= 2:
            good_event.set()

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        harness.subscribe(topic, bad)
        harness.subscribe(topic, good)

        harness.publish(topic, b"one")
        harness.publish(topic, b"two")

        await asyncio.wait_for(good_event.wait(), timeout=2.0)
        assert good_got == [b"one", b"two"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Cross-harness isolation: two clients on the same broker
# ---------------------------------------------------------------------------


async def test_two_independent_harnesses_on_the_same_broker_should_not_see_each_others_unsubscribed_traffic(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Impossible to model with MockTransport — there is no broker. Real
    brokers route by subject, so harness X must not observe messages on
    topics it did not subscribe to, even when harness Y publishes them."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic_x = _unique_topic("iso-x")
    topic_y = _unique_topic("iso-y")

    h_x = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    h_y = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await h_x.connect()
    await h_y.connect()
    try:
        x_got: list[bytes] = []
        y_got: list[bytes] = []
        x_event = asyncio.Event()
        y_event = asyncio.Event()

        h_x.subscribe(topic_x, lambda t, p: (x_got.append(p), x_event.set()))
        h_y.subscribe(topic_y, lambda t, p: (y_got.append(p), y_event.set()))

        # Each harness publishes on its own topic.
        h_x.publish(topic_x, b"x-message")
        h_y.publish(topic_y, b"y-message")

        await asyncio.wait_for(asyncio.gather(x_event.wait(), y_event.wait()), timeout=2.0)
        # Let any crossover have a chance to land.
        await asyncio.sleep(0.2)

        assert x_got == [b"x-message"]
        assert y_got == [b"y-message"]
    finally:
        await h_x.disconnect()
        await h_y.disconnect()


# ---------------------------------------------------------------------------
# Reconnect: disconnect then reconnect on the same transport instance
# ---------------------------------------------------------------------------


async def test_reconnecting_after_disconnect_should_restore_subscribe_and_publish(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """After disconnect, internal state (nc reference, pending tasks,
    subscription map) must be cleared so a second connect on the same
    instance can open a fresh session. A leaked task or stale nc would
    make the second round of subscribe/publish misbehave."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("reconnect")
    transport = NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path)
    harness = Harness(transport)

    # Round 1.
    await harness.connect()
    got1: list[bytes] = []
    ev1 = asyncio.Event()
    harness.subscribe(topic, lambda t, p: (got1.append(p), ev1.set()))
    harness.publish(topic, b"round-1")
    await asyncio.wait_for(ev1.wait(), timeout=2.0)
    assert got1 == [b"round-1"]
    await harness.disconnect()

    # Round 2 on the same instance.
    await harness.connect()
    try:
        got2: list[bytes] = []
        ev2 = asyncio.Event()
        harness.subscribe(topic, lambda t, p: (got2.append(p), ev2.set()))
        harness.publish(topic, b"round-2")
        await asyncio.wait_for(ev2.wait(), timeout=2.0)
        assert got2 == [b"round-2"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Connection errors
# ---------------------------------------------------------------------------


async def test_connecting_to_an_unreachable_nats_port_should_raise_a_transport_error(
    _nats_available: bool,
) -> None:
    """MockTransport never fails to connect. A real transport must surface
    a clear error when the broker is not reachable — tests need the
    distinction between 'wrong config' and 'something weird happened'."""
    from choreo.transports import NatsTransport, TransportError

    # Port 1 is reserved and will refuse. No allowlist — we are testing the
    # connection failure path, not the guard.
    transport = NatsTransport(
        servers=["nats://127.0.0.1:1"],
        connect_timeout_s=1.0,
    )
    with pytest.raises(TransportError) as exc:
        await transport.connect()
    assert "127.0.0.1:1" in str(exc.value) or "could not connect" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Payload integrity
# ---------------------------------------------------------------------------


async def test_a_large_json_payload_should_round_trip_unchanged(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Mock has no wire, no serialisation, no size limit. Real NATS does
    (default 1 MiB). This test sits well below that cap but is large enough
    to force multiple TCP segments, catching any truncation or framing bug
    in the bridge."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport

    topic = _unique_topic("large")
    big_string = "x" * 65_536  # 64 KiB of payload body
    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("large") as s:
            handle = s.expect(topic, field_equals("tag", "BIG"))
            s = s.publish(topic, {"tag": "BIG", "body": big_string})
            result = await s.await_all(timeout_ms=3000)

        result.assert_passed()
        assert handle.message["body"] == big_string
        assert len(handle.message["body"]) == 65_536
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Scope invariants under real-wire latency
# ---------------------------------------------------------------------------


async def test_a_timed_out_handle_should_remain_timed_out_even_after_a_late_message_arrives(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """A scenario with a tight deadline; the expected publish arrives after
    the deadline has elapsed. Over a real wire there is actual latency
    between 'await_all returned' and 'late message parsed by reader'. The
    handle's outcome must not flip from TIMEOUT back to PASS — that would
    mean await_all lied to the test.

    Opts into `test_namespace()` because the late publish echoes
    `s.correlation_id` onto its payload, which is only meaningful under a
    routing-capable policy (ADR-0019)."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic = _unique_topic("late")
    harness = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path),
        correlation=test_namespace(),
    )
    await harness.connect()
    try:
        async with harness.scenario("late") as s:
            handle = s.expect(topic, field_equals("k", "v"))
            corr = s.correlation_id

            async def publish_late() -> None:
                await asyncio.sleep(0.25)  # comfortably past the 100ms deadline
                harness.publish(topic, {"correlation_id": corr, "k": "v"})

            late_task = asyncio.create_task(publish_late())
            # Trigger the scope with a dummy publish on a different topic so
            # the state machine advances to 'triggered' without the real
            # message going out early.
            dummy_topic = _unique_topic("late-dummy")
            s = s.publish(dummy_topic, b"ignored")
            result = await s.await_all(timeout_ms=100)

        # Let the delayed publish actually land so we can assert the handle
        # stays TIMEOUT post hoc.
        await late_task
        await asyncio.sleep(0.1)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Multi-harness topology — three concurrent clients on one broker
# ---------------------------------------------------------------------------


async def test_three_harnesses_should_each_receive_only_messages_on_topics_they_subscribed_to(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Three independent client connections to the same broker. Each
    subscribes to its own distinct topic and publishes on it. Subject-based
    routing must isolate — harness A must not see harness B's or C's
    traffic. Impossible to exercise with MockTransport (no broker)."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topics = [_unique_topic(f"iso-{k}") for k in ("a", "b", "c")]
    harnesses = [
        Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
        for _ in range(3)
    ]
    for h in harnesses:
        await h.connect()
    try:
        buckets: list[list[bytes]] = [[], [], []]
        events: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
        for idx, h in enumerate(harnesses):

            def make_cb(i: int):
                def cb(t: str, p: bytes) -> None:
                    buckets[i].append(p)
                    events[i].set()

                return cb

            h.subscribe(topics[idx], make_cb(idx))

        # Each harness publishes only on its own topic.
        for idx, h in enumerate(harnesses):
            h.publish(topics[idx], f"payload-{idx}".encode())

        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in events)), timeout=3.0)
        # Leave time for any crossover to land before we assert on absence.
        await asyncio.sleep(0.25)

        assert buckets[0] == [b"payload-0"]
        assert buckets[1] == [b"payload-1"]
        assert buckets[2] == [b"payload-2"]
    finally:
        for h in harnesses:
            await h.disconnect()


async def test_three_harnesses_subscribed_to_a_shared_topic_should_all_receive_a_single_publish(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """NATS fan-out across distinct client connections. One publish from a
    fourth client must land on every subscribed connection. MockTransport
    cannot model separate-connection delivery at all."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("shared")
    subs = [
        Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
        for _ in range(3)
    ]
    pub = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    for h in (*subs, pub):
        await h.connect()
    try:
        buckets: list[list[bytes]] = [[], [], []]
        events: list[asyncio.Event] = [asyncio.Event() for _ in range(3)]
        for idx, h in enumerate(subs):

            def make_cb(i: int):
                def cb(t: str, p: bytes) -> None:
                    buckets[i].append(p)
                    events[i].set()

                return cb

            h.subscribe(topic, make_cb(idx))

        # Let every SUB command land on the server before publishing.
        await asyncio.sleep(0.1)

        pub.publish(topic, b"broadcast")

        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in events)), timeout=3.0)
        assert all(bucket == [b"broadcast"] for bucket in buckets), buckets
    finally:
        for h in (*subs, pub):
            await h.disconnect()


async def test_three_concurrent_scenarios_across_three_harnesses_should_only_fulfil_the_handle_matching_its_own_correlation(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Three parallel scenarios, each on its own harness, all running
    expectations against the **same topic**. One harness publishes a
    payload carrying its own correlation ID. Every harness's subscriber
    sees the wire message (fan-out), but the correlation filter must
    ensure only the matching scope fulfils. The other two scopes must
    time out."""
    from choreo import Harness, test_namespace
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic = _unique_topic("three-scopes")
    harnesses = [
        Harness(
            NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path),
            correlation=test_namespace(),
        )
        for _ in range(3)
    ]
    for h in harnesses:
        await h.connect()
    try:

        async def run(idx: int, h: Harness) -> tuple[Any, Any]:
            async with h.scenario(f"scope-{idx}") as s:
                handle = s.expect(topic, field_equals("k", "match"))
                # Only harness 1 actually publishes a matching payload.
                # The other two publish a benign dummy on a throwaway topic
                # just to satisfy the state machine.
                if idx == 1:
                    s = s.publish(topic, {"k": "match"})
                else:
                    s = s.publish(_unique_topic("dummy"), b"noop")
                return handle, await s.await_all(timeout_ms=1000)

        results = await asyncio.gather(*(run(i, h) for i, h in enumerate(harnesses)))

        h0, r0 = results[0]
        h1, r1 = results[1]
        h2, r2 = results[2]

        # Only scope 1 matches. The other two observed the same wire message
        # but the correlation filter rejected it → TIMEOUT, not FAIL.
        assert h1.outcome is Outcome.PASS
        r1.assert_passed()

        assert h0.outcome is Outcome.TIMEOUT, h0.outcome
        assert h2.outcome is Outcome.TIMEOUT, h2.outcome
        assert r0.passed is False
        assert r2.passed is False
    finally:
        for h in harnesses:
            await h.disconnect()


async def test_disconnecting_one_harness_should_not_disrupt_pub_sub_on_the_other_two(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Three connected harnesses share a topic. One disconnects. The
    remaining two must continue subscribing + publishing normally — a
    shared resource leak in one transport's cleanup could wedge the
    whole loop."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("drop-one")
    harnesses = [
        Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
        for _ in range(3)
    ]
    for h in harnesses:
        await h.connect()

    try:
        # Disconnect the middle one straight away to stress cleanup while
        # the other two are still live on the same broker.
        await harnesses[1].disconnect()

        received_a: list[bytes] = []
        received_c: list[bytes] = []
        ev_a = asyncio.Event()
        ev_c = asyncio.Event()

        harnesses[0].subscribe(topic, lambda t, p: (received_a.append(p), ev_a.set()))
        harnesses[2].subscribe(topic, lambda t, p: (received_c.append(p), ev_c.set()))

        await asyncio.sleep(0.1)  # let the SUBs settle
        harnesses[0].publish(topic, b"from-a")

        await asyncio.wait_for(asyncio.gather(ev_a.wait(), ev_c.wait()), timeout=2.0)
        assert received_a == [b"from-a"]
        assert received_c == [b"from-a"]
    finally:
        # harnesses[1] already disconnected; double-disconnect is idempotent.
        for h in harnesses:
            await h.disconnect()


# ---------------------------------------------------------------------------
# Multi-expectation scopes
# ---------------------------------------------------------------------------


async def test_a_scope_with_three_expectations_and_two_matching_publishes_should_report_one_failing_handle(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Real tests routinely register several expectations per scope — publish
    an order, observe a position update AND a fill confirmation AND a margin
    notice. If one of the expected downstreams is missing, `ScenarioResult`
    must report exactly which one, without affecting the others.

    Only real wire can detect a bug where one subscription's delivery blocks
    another's — MockTransport dispatches sequentially inside the publish
    frame so interleaving is invisible."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic_a = _unique_topic("multi-a")
    topic_b = _unique_topic("multi-b")
    topic_c = _unique_topic("multi-c")

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("multi") as s:
            ha = s.expect(topic_a, field_equals("k", "a"))
            hb = s.expect(topic_b, field_equals("k", "b"))
            hc = s.expect(topic_c, field_equals("k", "c"))

            s = s.publish(topic_a, {"k": "a"})
            s = s.publish(topic_b, {"k": "b"})
            # intentionally skip topic_c
            result = await s.await_all(timeout_ms=1500)

        assert result.passed is False
        assert ha.outcome is Outcome.PASS
        assert hb.outcome is Outcome.PASS
        assert hc.outcome is Outcome.TIMEOUT
        assert result.failing_handles == (hc,)
    finally:
        await harness.disconnect()


async def test_a_scope_with_three_matching_publishes_should_pass_every_handle(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Happy path for the same multi-expectation shape. Confirms that a
    scope can track three async completions in parallel without any one
    stepping on another's future."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic_a = _unique_topic("multi-pass-a")
    topic_b = _unique_topic("multi-pass-b")
    topic_c = _unique_topic("multi-pass-c")

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        async with harness.scenario("multi-pass") as s:
            ha = s.expect(topic_a, field_equals("k", "a"))
            hb = s.expect(topic_b, field_equals("k", "b"))
            hc = s.expect(topic_c, field_equals("k", "c"))

            s = s.publish(topic_a, {"k": "a"})
            s = s.publish(topic_b, {"k": "b"})
            s = s.publish(topic_c, {"k": "c"})
            result = await s.await_all(timeout_ms=1500)

        result.assert_passed()
        assert ha.outcome is Outcome.PASS
        assert hb.outcome is Outcome.PASS
        assert hc.outcome is Outcome.PASS
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscription hygiene under churn
# ---------------------------------------------------------------------------


async def test_running_many_sequential_scopes_should_not_accumulate_subscriptions(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Each scope registers and tears down its own subscribers. Over many
    iterations on a single harness, `active_subscription_count()` must
    return to zero — a leak that adds one sub per scope would silently eat
    memory and server-side subscription slots over a long test run."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport

    topic = _unique_topic("churn")
    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        assert harness.active_subscription_count() == 0

        for i in range(30):
            async with harness.scenario(f"churn-{i}") as s:
                s.expect(topic, field_equals("i", i))
                s = s.publish(topic, {"i": i})
                result = await s.await_all(timeout_ms=1000)
            result.assert_passed()
            # Give the UNSUB commands a beat to reach the server before
            # the next scope runs.
            await asyncio.sleep(0.01)
            assert harness.active_subscription_count() == 0, (
                f"subscription leak after iteration {i}: "
                f"{harness.active_subscription_count()} still registered"
            )
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Subscribe / unsubscribe symmetry with duplicate callbacks
# ---------------------------------------------------------------------------


async def test_subscribing_the_same_callback_twice_should_deliver_twice_per_publish(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """MockTransport stores callbacks in a list and fan-outs per occurrence.
    The real-wire transport must match: two subscribes of the same callback
    on the same topic means the callback observes each message twice.

    This pins the contract — changing to set-semantics (dedup) would be a
    silent behaviour change for any consumer that relies on N-way fan-out
    from repeated subscribes."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("double-sub")
    hits: list[bytes] = []

    def cb(t: str, p: bytes) -> None:
        hits.append(p)

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        harness.subscribe(topic, cb)
        harness.subscribe(topic, cb)
        await asyncio.sleep(0.15)  # let both SUBs reach the server

        harness.publish(topic, b"boom")
        await asyncio.sleep(0.3)

        assert hits == [b"boom", b"boom"], f"expected fan-out across both subscriptions, got {hits}"
        assert harness.active_subscription_count() == 2
    finally:
        await harness.disconnect()


async def test_unsubscribing_each_duplicate_in_turn_should_reduce_delivery_count_to_zero(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Pair with the fan-out test above. Two subscribes + two unsubscribes
    of the same callback must leave no server-side subscriptions — a
    regression where the second unsubscribe is a no-op would leak an
    orphan NATS subscription that keeps delivering forever."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("double-unsub")
    hits: list[bytes] = []

    def cb(t: str, p: bytes) -> None:
        hits.append(p)

    harness = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await harness.connect()
    try:
        harness.subscribe(topic, cb)
        harness.subscribe(topic, cb)
        await asyncio.sleep(0.1)

        harness.publish(topic, b"one")
        await asyncio.sleep(0.2)
        assert hits == [b"one", b"one"]

        harness.unsubscribe(topic, cb)
        await asyncio.sleep(0.15)
        harness.publish(topic, b"two")
        await asyncio.sleep(0.2)
        assert hits == [b"one", b"one", b"two"]  # one sub remains
        assert harness.active_subscription_count() == 1

        harness.unsubscribe(topic, cb)
        await asyncio.sleep(0.15)
        harness.publish(topic, b"three")
        await asyncio.sleep(0.2)
        assert hits == [b"one", b"one", b"two"], f"orphan subscription still delivering: {hits}"
        assert harness.active_subscription_count() == 0
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Session-scoped harness reuse (consumer-fixture pattern)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def shared_nats_harness(allowlist_yaml_path: Path, nats_url: str, _nats_available: bool):
    """A module-scoped NATS-backed harness, mirroring the session fixture
    that downstream consumers are expected to write (see CLAUDE.md). Any
    state that leaks between scenarios on the same harness instance will
    surface here."""
    from choreo import Harness
    from choreo.transports import NatsTransport

    h = Harness(NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path))
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_a_shared_nats_harness_should_be_connected_when_received_by_a_test(
    shared_nats_harness,
) -> None:
    assert shared_nats_harness.is_connected()


async def test_a_shared_nats_harness_should_run_a_scenario_to_completion(
    shared_nats_harness,
) -> None:
    from choreo.matchers import field_equals

    topic = _unique_topic("shared-1")
    async with shared_nats_harness.scenario("shared-1") as s:
        s.expect(topic, field_equals("k", "v"))
        s = s.publish(topic, {"k": "v"})
        result = await s.await_all(timeout_ms=1500)

    result.assert_passed()


async def test_a_shared_nats_harness_should_carry_no_state_between_scenarios(
    shared_nats_harness,
) -> None:
    """Second scenario on the same shared harness must not inherit
    subscriptions, correlation, or pending handles from the first. The
    previous test already ran a scope — if scope cleanup was partial,
    `active_subscription_count()` would be > 0 here."""
    from choreo.matchers import field_equals

    assert shared_nats_harness.active_subscription_count() == 0

    topic = _unique_topic("shared-2")
    async with shared_nats_harness.scenario("shared-2") as s:
        s.expect(topic, field_equals("k", "v"))
        s = s.publish(topic, {"k": "v"})
        result = await s.await_all(timeout_ms=1500)

    result.assert_passed()
    # Post-scope clean-up invariant.
    assert shared_nats_harness.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_topic(prefix: str) -> str:
    return f"e2e.edge.{prefix}.{uuid.uuid4().hex[:8]}"
