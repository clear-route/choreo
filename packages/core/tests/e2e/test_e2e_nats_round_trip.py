"""End-to-end: harness over real NATS.

These tests exercise the full path — Harness → NatsTransport → nats-py →
TCP → NATS broker → TCP → nats-py → Harness callback — for a single
request/response pattern. They validate that the Transport Protocol
contract holds when the in-memory shortcut of MockTransport is removed.

Runs only under `pytest -m e2e`. Requires the compose stack:

    docker compose -f docker/compose.e2e.yaml up -d
    pytest -m e2e
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Connect / disconnect lifecycle
# ---------------------------------------------------------------------------


async def test_a_harness_over_nats_should_report_itself_connected_after_connect(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    from choreo import Harness
    from choreo.transports import NatsTransport

    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        assert harness.is_connected()
    finally:
        await harness.disconnect()


async def test_a_nats_transport_should_refuse_to_connect_to_a_server_outside_the_allowlist(
    allowlist_yaml_path: Path,
    _nats_available: bool,
) -> None:
    from choreo.environment import HostNotInAllowlist
    from choreo.transports import NatsTransport

    transport = NatsTransport(
        servers=["nats://prod.internal:4222"],
        allowlist_path=allowlist_yaml_path,
    )
    with pytest.raises(HostNotInAllowlist) as exc:
        await transport.connect()
    assert "prod.internal" in str(exc.value)


# ---------------------------------------------------------------------------
# Round-trip through the scenario DSL
# ---------------------------------------------------------------------------


async def test_a_matching_message_published_over_nats_should_fulfil_the_scenario(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport

    topic = _unique_topic("orders.approved")
    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-happy") as s:
            s.expect(topic, field_equals("status", "APPROVED"))
            s = s.publish(topic, {"status": "APPROVED"})
            result = await s.await_all(timeout_ms=2000)

        result.assert_passed()
    finally:
        await harness.disconnect()


async def test_a_timeout_on_nats_should_surface_as_timeout_outcome(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """If nothing is ever published on the topic, the handle resolves as
    TIMEOUT (not FAIL) — the same routing-vs-expectation distinction that
    MockTransport guarantees must hold over a real wire."""
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome
    from choreo.transports import NatsTransport

    topic = _unique_topic("silent")
    other_topic = _unique_topic("other")
    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-timeout") as s:
            handle = s.expect(topic, field_equals("status", "X"))
            # Publish on a DIFFERENT topic so the harness sees zero attempts.
            s = s.publish(other_topic, {"status": "X"})
            result = await s.await_all(timeout_ms=200)

        assert result.passed is False
        assert handle.outcome is Outcome.TIMEOUT
    finally:
        await harness.disconnect()


async def test_a_nats_transport_should_fan_out_to_multiple_subscribers_on_the_same_topic(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    from choreo import Harness
    from choreo.transports import NatsTransport

    topic = _unique_topic("fanout")
    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        seen_a: list[bytes] = []
        seen_b: list[bytes] = []
        done_a: asyncio.Event = asyncio.Event()
        done_b: asyncio.Event = asyncio.Event()

        def cb_a(t: str, p: bytes) -> None:
            seen_a.append(p)
            done_a.set()

        def cb_b(t: str, p: bytes) -> None:
            seen_b.append(p)
            done_b.set()

        harness.subscribe(topic, cb_a)
        harness.subscribe(topic, cb_b)
        harness.publish(topic, b"tick")

        await asyncio.wait_for(
            asyncio.gather(done_a.wait(), done_b.wait()),
            timeout=2.0,
        )
        assert seen_a == [b"tick"]
        assert seen_b == [b"tick"]
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_topic(prefix: str) -> str:
    """Every test gets its own subject so concurrent runs against the same
    broker cannot interfere. NATS dots act as token separators; using a
    suffix keeps the subject valid."""
    return f"e2e.{prefix}.{uuid.uuid4().hex[:8]}"
