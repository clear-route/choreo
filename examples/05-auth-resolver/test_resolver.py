"""05 — Auth resolvers.

Fetch credentials at connect() time via a callable — sync or async.
The secret exists in process memory only for the duration of connect().

Run it:

    NATS_TOKEN=my-dev-token pytest examples/05-auth-resolver/

    # Or with defaults:
    pytest examples/05-auth-resolver/
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from choreo import Harness
from choreo.matchers import contains_fields
from choreo.transports import MockTransport, TransportError
from choreo.transports.nats_auth import NatsAuth

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


# ---------------------------------------------------------------------------
# Sync resolver — env vars
# ---------------------------------------------------------------------------


def _env_resolver() -> NatsAuth:
    """A sync resolver that reads credentials from the environment.

    In a real consumer repo this might read from a .env file, a config
    object, or a sync Vault client.
    """
    token = os.environ.get("NATS_TOKEN", "default-dev-token")
    return NatsAuth.token(token)


async def test_a_sync_resolver_should_fetch_credentials_at_connect_time() -> None:
    """The resolver is called inside connect(), not at construction.
    The scenario DSL is identical to the literal-auth case."""

    transport = MockTransport(
        allowlist_path=ALLOWLIST,
        endpoint="mock://localhost",
        auth=_env_resolver,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("sync-resolver") as s:
            s.expect("signals.tick", contains_fields({"seq": 1}))
            s = s.publish("signals.tick", {"seq": 1})
            result = await s.await_all(timeout_ms=100)

        result.assert_passed()
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Async resolver — simulating a Vault / Secrets Manager fetch
# ---------------------------------------------------------------------------


async def _async_resolver() -> NatsAuth:
    """An async resolver simulating a Vault or AWS Secrets Manager call.

    In a real consumer this would be:

        async def vault_resolver():
            secret = await vault_client.read("secret/nats")
            return NatsAuth.user_password(
                secret["username"], secret["password"],
            )
    """
    # Simulate an async secret-store fetch.
    return NatsAuth.user_password("vault-user", "vault-password")


async def test_an_async_resolver_should_be_awaited_inside_connect() -> None:
    """Async resolvers are first-class — the library detects the coroutine
    and awaits it.  Useful for async-native SDKs (boto3/aioboto3, hvac,
    azure-keyvault-secrets)."""

    transport = MockTransport(
        allowlist_path=ALLOWLIST,
        endpoint="mock://localhost",
        auth=_async_resolver,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("async-resolver") as s:
            s.expect("signals.tick", contains_fields({"seq": 2}))
            s = s.publish("signals.tick", {"seq": 2})
            result = await s.await_all(timeout_ms=100)

        result.assert_passed()
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# Resolver failure — secrets are never leaked
# ---------------------------------------------------------------------------


async def test_a_failing_resolver_should_surface_a_safe_transport_error() -> None:
    """If the resolver raises, the error is wrapped in TransportError.
    The original exception's args (which might contain secrets) are stripped —
    only the exception class name is recorded."""

    def bad_resolver():
        # Simulates a Vault client that raises with a URL containing creds.
        raise ConnectionError("https://admin:s3cret@vault.internal:8200/v1/secret")

    transport = MockTransport(auth=bad_resolver)
    with pytest.raises(TransportError, match="auth resolver failed") as exc_info:
        await transport.connect()

    # The original exception's args never leak.
    error_msg = str(exc_info.value)
    assert "s3cret" not in error_msg
    assert "admin" not in error_msg
    assert "vault.internal" not in error_msg

    # The exception class name is available for diagnostics.
    assert exc_info.value.resolver_cause == "ConnectionError"

    # The cause chain is suppressed — log formatters won't walk it.
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
