"""Behavioural tests for MockTransport.

MockTransport is the in-memory transport used when unit-testing the framework
itself. It implements the `Transport` Protocol and honours the same
subscribe / unsubscribe / publish contract as any real transport.
"""

from __future__ import annotations

import asyncio

import pytest


async def test_a_mock_transport_should_tolerate_repeated_connect_calls() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    await t.connect()  # second call is a no-op, not an error
    await t.disconnect()


async def test_a_mock_transport_should_tolerate_repeated_disconnect_calls() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    await t.disconnect()
    await t.disconnect()  # idempotent


async def test_publishing_should_deliver_the_payload_to_a_subscribed_callback() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    received: list[bytes] = []

    def on_message(topic: str, payload: bytes) -> None:
        received.append(payload)

    t.subscribe("requests.submitted", on_message)
    t.publish("requests.submitted", b"hello")

    await asyncio.sleep(0)
    assert received == [b"hello"]
    await t.disconnect()


async def test_publishing_should_fan_out_to_every_callback_on_the_topic() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    seen_a: list[bytes] = []
    seen_b: list[bytes] = []

    t.subscribe("signals.tick", lambda topic, p: seen_a.append(p))
    t.subscribe("signals.tick", lambda topic, p: seen_b.append(p))
    t.publish("signals.tick", b"tick")
    await asyncio.sleep(0)

    assert seen_a == [b"tick"]
    assert seen_b == [b"tick"]
    await t.disconnect()


async def test_publishing_to_a_topic_with_no_subscribers_should_not_raise() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    t.publish("nobody.listening", b"void")
    await asyncio.sleep(0)
    await t.disconnect()


async def test_an_unsubscribed_callback_should_not_receive_subsequent_messages() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    cancelled: list[bytes] = []
    other: list[bytes] = []

    def cb_cancelled(topic: str, payload: bytes) -> None:
        cancelled.append(payload)

    def cb_other(topic: str, payload: bytes) -> None:
        other.append(payload)

    t.subscribe("entries", cb_cancelled)
    t.subscribe("entries", cb_other)
    t.unsubscribe("entries", cb_cancelled)

    t.publish("entries", b"one")
    await asyncio.sleep(0)
    assert cancelled == []
    assert other == [b"one"]
    await t.disconnect()


async def test_a_disconnected_transport_should_reject_further_publishes() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    t.subscribe("x", lambda topic, p: None)
    await t.disconnect()

    with pytest.raises(RuntimeError):
        t.publish("x", b"late")


async def test_a_mock_transport_should_record_every_publish_for_later_assertion() -> None:
    from choreo.transports import MockTransport

    t = MockTransport()
    await t.connect()
    t.publish("a", b"1")
    t.publish("b", b"2")
    t.publish("a", b"3")

    sent = t.sent()
    assert ("a", b"1") in sent
    assert ("b", b"2") in sent
    assert ("a", b"3") in sent

    await t.disconnect()
