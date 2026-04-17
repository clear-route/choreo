"""Behavioural tests for Harness — pure coordinator over a Transport.

The Harness owns no queue-specific knowledge. It takes any `Transport`
instance + an optional `Codec`, and delegates. Every test here constructs
a `MockTransport` explicitly and passes it in, mirroring what a consumer
would do with a real NatsTransport / KafkaTransport / etc.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _mock_transport(allowlist_yaml_path: Path, **kwargs):
    """Local helper: builds a MockTransport with the shipped allowlist and
    any extra fields the specific test needs."""
    from choreo.transports import MockTransport

    defaults = {
        "allowlist_path": allowlist_yaml_path,
        "endpoint": "mock://localhost",
    }
    defaults.update(kwargs)
    return MockTransport(**defaults)


# ---------------------------------------------------------------------------
# Harness lifecycle
# ---------------------------------------------------------------------------


async def test_a_newly_constructed_harness_should_be_disconnected(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    assert not harness.is_connected()


async def test_a_connected_harness_should_report_itself_as_connected(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    try:
        assert harness.is_connected()
    finally:
        await harness.disconnect()


async def test_disconnecting_an_already_disconnected_harness_should_not_raise(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    await harness.disconnect()
    await harness.disconnect()


async def test_connecting_an_already_connected_harness_should_not_re_enter_transport(
    allowlist_yaml_path: Path,
) -> None:
    """Double-connect must be a no-op. A transport whose `connect()` is not
    idempotent (many real backends aren't) would misbehave if the Harness
    called it twice."""
    from choreo import Harness

    transport = _mock_transport(allowlist_yaml_path)
    connect_calls = 0

    async def counting_connect() -> None:
        nonlocal connect_calls
        connect_calls += 1

    transport.connect = counting_connect  # type: ignore[method-assign]

    harness = Harness(transport)
    await harness.connect()
    await harness.connect()
    await harness.connect()
    assert connect_calls == 1
    assert harness.is_connected() is True


async def test_disconnect_should_leave_harness_unusable_even_when_transport_raises(
    allowlist_yaml_path: Path,
) -> None:
    """If the transport's `disconnect()` raises, the harness must still flip
    to disconnected — otherwise `_connected` stays True and a subsequent
    `publish()` would sail past the guard while the transport is torn down.
    The exception propagates so the caller sees it."""
    from choreo import Harness

    transport = _mock_transport(allowlist_yaml_path)

    async def boom_disconnect() -> None:
        raise RuntimeError("transport disconnect failure")

    harness = Harness(transport)
    await harness.connect()
    transport.disconnect = boom_disconnect  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="transport disconnect failure"):
        await harness.disconnect()

    assert harness.is_connected() is False
    # Second disconnect is a clean no-op; _connected is False so we short-circuit.
    await harness.disconnect()
    # Publish after disconnect is rejected by the harness (not by the dead transport).
    with pytest.raises(RuntimeError, match="not connected"):
        harness.publish("topic", b"x")


# ---------------------------------------------------------------------------
# Harness facade — subscribe / publish
# ---------------------------------------------------------------------------


async def test_a_message_published_on_a_subscribed_topic_should_reach_the_subscriber(
    allowlist_yaml_path: Path,
) -> None:
    import asyncio

    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    received: list[bytes] = []

    harness.subscribe("test.topic", lambda topic, p: received.append(p))
    harness.publish("test.topic", b"x")
    await asyncio.sleep(0)
    assert received == [b"x"]
    await harness.disconnect()


async def test_an_unsubscribed_callback_should_not_receive_subsequent_messages(
    allowlist_yaml_path: Path,
) -> None:
    import asyncio

    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    received: list[bytes] = []

    def cb(topic: str, payload: bytes) -> None:
        received.append(payload)

    harness.subscribe("test.topic", cb)
    harness.unsubscribe("test.topic", cb)
    harness.publish("test.topic", b"x")
    await asyncio.sleep(0)
    assert received == []
    await harness.disconnect()


# ---------------------------------------------------------------------------
# Harness ownership invariants
# ---------------------------------------------------------------------------


async def test_disconnecting_a_harness_should_release_every_subscription(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    harness.subscribe("x", lambda topic, p: None)
    harness.subscribe("y", lambda topic, p: None)
    await harness.disconnect()
    assert harness.active_subscription_count() == 0


async def test_the_harness_should_not_be_pickleable(
    allowlist_yaml_path: Path,
) -> None:
    import pickle

    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    with pytest.raises((TypeError, pickle.PicklingError)):
        pickle.dumps(harness)


# ---------------------------------------------------------------------------
# Session-scoped fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def harness(allowlist_yaml_path: Path):
    from choreo import Harness

    h = Harness(_mock_transport(allowlist_yaml_path))
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


async def test_the_session_scoped_harness_fixture_should_provide_a_connected_harness(
    harness,
) -> None:
    assert harness.is_connected()


async def test_the_session_scoped_harness_should_be_the_same_instance_across_tests(
    harness, request: pytest.FixtureRequest
) -> None:
    key = "_first_harness_id"
    if getattr(request.session, key, None) is None:
        setattr(request.session, key, id(harness))
    assert id(harness) == getattr(request.session, key)


async def test_the_session_scoped_harness_should_expose_its_correlation_policy(
    harness,
) -> None:
    """A consumer can introspect the active policy. The library default is
    `NoCorrelationPolicy`; this harness is constructed without an explicit
    policy, so it is no-op (ADR-0019)."""
    from choreo import NoCorrelationPolicy

    assert isinstance(harness.correlation, NoCorrelationPolicy)
    assert await harness.correlation.new_id() is None


# ---------------------------------------------------------------------------
# Codec injection — non-default payload decoders
# ---------------------------------------------------------------------------


async def test_harness_should_default_to_json_codec(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness
    from choreo.codecs import JSONCodec

    harness = Harness(_mock_transport(allowlist_yaml_path))
    assert isinstance(harness.codec, JSONCodec)


async def test_harness_should_accept_a_custom_codec(
    allowlist_yaml_path: Path,
) -> None:
    from choreo import Harness
    from choreo.codecs import RawCodec

    raw = RawCodec()
    harness = Harness(_mock_transport(allowlist_yaml_path), codec=raw)
    assert harness.codec is raw


async def test_publishing_a_memoryview_should_pass_through_without_codec_encoding(
    allowlist_yaml_path: Path,
) -> None:
    """memoryview is a buffer-protocol type like bytes/bytearray — the caller
    handed us raw bytes, they should not be routed through the codec's encode.
    """
    import asyncio

    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    received: list[bytes] = []

    harness.subscribe("test.topic", lambda topic, p: received.append(p))
    harness.publish("test.topic", memoryview(b"raw-bytes"))
    await asyncio.sleep(0)
    assert received == [b"raw-bytes"]
    await harness.disconnect()


async def test_publishing_a_bytearray_should_pass_through_without_codec_encoding(
    allowlist_yaml_path: Path,
) -> None:
    import asyncio

    from choreo import Harness

    harness = Harness(_mock_transport(allowlist_yaml_path))
    await harness.connect()
    received: list[bytes] = []

    harness.subscribe("test.topic", lambda topic, p: received.append(p))
    harness.publish("test.topic", bytearray(b"mutable-bytes"))
    await asyncio.sleep(0)
    assert received == [b"mutable-bytes"]
    await harness.disconnect()
