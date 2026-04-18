"""E2E authenticated transport contract tests (ADR-0020).

These tests exercise the auth descriptor path end-to-end against a real
authenticated NATS broker.  They validate that:

- a correct auth descriptor completes a round-trip,
- wrong credentials are refused at connect time,
- the descriptor is cleared after a successful connect.

Runs only under ``pytest -m e2e``.  Requires the auth compose profile:

    docker compose -f docker/compose.e2e.yaml --profile nats up -d
    pytest -m e2e
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unique_topic(prefix: str) -> str:
    return f"e2e.auth.{prefix}.{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Round-trip with correct credentials
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_should_complete_a_round_trip_with_the_supplied_auth_descriptor(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    nats_auth_user: str,
    nats_auth_password: str,
    _nats_auth_available: bool,
) -> None:
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport
    from choreo.transports.nats_auth import NatsAuth

    topic = _unique_topic("authed.roundtrip")
    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=NatsAuth.user_password(nats_auth_user, nats_auth_password),
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-auth-happy") as s:
            s.expect(topic, field_equals("status", "OK"))
            s = s.publish(topic, {"status": "OK"})
            result = await s.await_all(timeout_ms=2000)
        result.assert_passed()
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Wrong credentials
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_should_refuse_to_connect_with_the_wrong_credentials(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    _nats_auth_available: bool,
) -> None:
    from choreo.transports import NatsTransport, TransportError
    from choreo.transports.nats_auth import NatsAuth

    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=NatsAuth.user_password("wrong-user", "wrong-password"),
    )
    with pytest.raises(TransportError, match="could not connect"):
        await transport.connect()


# ---------------------------------------------------------------------------
# Descriptor cleared after connect
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_should_clear_the_descriptor_after_a_successful_connect(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    nats_auth_user: str,
    nats_auth_password: str,
    _nats_auth_available: bool,
) -> None:
    from choreo.transports import NatsTransport
    from choreo.transports.nats_auth import NatsAuth

    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=NatsAuth.user_password(nats_auth_user, nats_auth_password),
    )
    await transport.connect()
    try:
        assert transport._auth is None
    finally:
        await transport.disconnect()


# ---------------------------------------------------------------------------
# Resolver form
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_with_a_resolver_should_complete_a_round_trip(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    nats_auth_user: str,
    nats_auth_password: str,
    _nats_auth_available: bool,
) -> None:
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport
    from choreo.transports.nats_auth import NatsAuth

    topic = _unique_topic("authed.resolver")

    def auth_resolver():
        return NatsAuth.user_password(nats_auth_user, nats_auth_password)

    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=auth_resolver,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-auth-resolver") as s:
            s.expect(topic, field_equals("status", "RESOLVED"))
            s = s.publish(topic, {"status": "RESOLVED"})
            result = await s.await_all(timeout_ms=2000)
        result.assert_passed()
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Async resolver form
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_with_an_async_resolver_should_complete_a_round_trip(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    nats_auth_user: str,
    nats_auth_password: str,
    _nats_auth_available: bool,
) -> None:
    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import NatsTransport
    from choreo.transports.nats_auth import NatsAuth

    topic = _unique_topic("authed.async_resolver")

    async def auth_resolver():
        return NatsAuth.user_password(nats_auth_user, nats_auth_password)

    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=auth_resolver,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-auth-async-resolver") as s:
            s.expect(topic, field_equals("status", "ASYNC"))
            s = s.publish(topic, {"status": "ASYNC"})
            result = await s.await_all(timeout_ms=2000)
        result.assert_passed()
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# No credential egress in error messages
# ---------------------------------------------------------------------------


async def test_an_authenticated_nats_connect_failure_should_not_leak_credentials_in_the_error(
    allowlist_yaml_path: Path,
    nats_auth_url: str,
    _nats_auth_available: bool,
) -> None:
    from choreo.transports import NatsTransport, TransportError
    from choreo.transports.nats_auth import NatsAuth

    bad_password = "choreo-e2e-bad-password-sentinel"
    transport = NatsTransport(
        servers=[nats_auth_url],
        allowlist_path=allowlist_yaml_path,
        auth=NatsAuth.user_password("wrong", bad_password),
    )
    with pytest.raises(TransportError) as exc_info:
        await transport.connect()
    assert bad_password not in str(exc_info.value)
    assert "wrong" not in str(exc_info.value) or "wrong" in "could not connect"
