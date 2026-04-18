"""04 — Transport authentication.

Wire a typed auth descriptor into a transport.  MockTransport validates
the descriptor shape and discards it — your test runs without a broker,
but the same auth plumbing works when you swap for NatsTransport later.

Run it:

    pytest examples/04-transport-auth/
"""

from pathlib import Path

from choreo import Harness
from choreo.matchers import contains_fields
from choreo.transports import MockTransport
from choreo.transports.nats_auth import NatsAuth

ALLOWLIST = Path(__file__).parent / "allowlist.yaml"


async def test_a_scenario_with_auth_should_round_trip_the_same_as_without() -> None:
    """Auth is transparent to the scenario DSL.  The transport handles
    credentials; the test code is identical whether auth is present or not."""

    # In a real consumer fixture you'd swap MockTransport for:
    #
    #   NatsTransport(
    #       servers=["nats://broker:4222"],
    #       allowlist_path=ALLOWLIST,
    #       auth=NatsAuth.user_password("admin", "s3cret"),
    #   )
    #
    # The scenario code below stays the same.

    transport = MockTransport(
        allowlist_path=ALLOWLIST,
        endpoint="mock://localhost",
        auth=NatsAuth.token("my-dev-token"),
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("auth-round-trip") as s:
            s.expect(
                "orders.approved",
                contains_fields({"status": "APPROVED", "order_id": "ORD-1"}),
            )
            s = s.publish(
                "orders.approved",
                {"status": "APPROVED", "order_id": "ORD-1", "amount": 42.0},
            )
            result = await s.await_all(timeout_ms=100)

        result.assert_passed()
    finally:
        await harness.disconnect()


async def test_the_auth_descriptor_should_be_cleared_after_connect() -> None:
    """After connect(), the transport drops its reference to the descriptor.
    This is the bounded-lifetime guarantee — credentials exist in memory
    only for the duration of connect()."""

    transport = MockTransport(
        auth=NatsAuth.user_password("admin", "s3cret"),
    )
    await transport.connect()

    # The transport no longer holds the descriptor.
    assert transport._auth is None

    await transport.disconnect()


async def test_the_auth_descriptor_repr_should_never_reveal_secrets() -> None:
    """Descriptors print their variant tag only — no field values,
    redacted or otherwise.  Safe for logs, error messages, and CI output."""

    descriptor = NatsAuth.user_password("admin", "super-secret-password")
    r = repr(descriptor)

    assert "super-secret-password" not in r
    assert "admin" not in r
    assert "<redacted>" in r
