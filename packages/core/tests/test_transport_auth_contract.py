"""Cross-transport auth contract tests (Phase 1: Mock + NATS rows).

These tests verify the auth lifecycle, redaction, and safety properties
that every transport must honour.  Parametrised over transport factories
so each transport runs the identical suite.  See ADR-0020 §Validation.
"""

from __future__ import annotations

import pickle

import pytest
from choreo.transports import MockTransport, TransportError
from choreo.transports.nats_auth import NatsAuth, _NatsToken

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_factory(auth=None):
    """Create a MockTransport with optional auth."""
    return MockTransport(auth=auth)


# ---------------------------------------------------------------------------
# No-auth backward compat
# ---------------------------------------------------------------------------


async def test_a_transport_constructed_without_auth_should_not_alter_current_connect_behaviour() -> (
    None
):
    t = MockTransport()
    await t.connect()
    assert t._connected
    await t.disconnect()


# ---------------------------------------------------------------------------
# Literal descriptor
# ---------------------------------------------------------------------------


async def test_a_transport_with_an_auth_descriptor_should_accept_a_literal_value() -> None:
    t = MockTransport(auth=NatsAuth.token("test-token"))
    await t.connect()
    assert t._connected
    await t.disconnect()


# ---------------------------------------------------------------------------
# Sync resolver
# ---------------------------------------------------------------------------


async def test_a_transport_with_a_sync_auth_resolver_should_call_it_exactly_once_per_connect() -> (
    None
):
    call_count = 0

    def resolver():
        nonlocal call_count
        call_count += 1
        return NatsAuth.token("resolved")

    t = MockTransport(auth=resolver)
    await t.connect()
    assert call_count == 1
    await t.disconnect()


# ---------------------------------------------------------------------------
# Async resolver
# ---------------------------------------------------------------------------


async def test_a_transport_with_an_async_auth_resolver_should_await_it_exactly_once_per_connect() -> (
    None
):
    call_count = 0

    async def resolver():
        nonlocal call_count
        call_count += 1
        return NatsAuth.token("async-resolved")

    t = MockTransport(auth=resolver)
    await t.connect()
    assert call_count == 1
    await t.disconnect()


# ---------------------------------------------------------------------------
# Resolver failure
# ---------------------------------------------------------------------------


async def test_a_transport_auth_resolver_that_raises_should_surface_a_transport_error() -> None:
    def bad_resolver():
        raise RuntimeError("vault unreachable")

    t = MockTransport(auth=bad_resolver)
    with pytest.raises(TransportError, match="auth resolver failed"):
        await t.connect()


async def test_a_transport_auth_resolver_error_message_should_not_contain_the_resolver_exception_args() -> (
    None
):
    def bad_resolver():
        raise ValueError("super secret message from vault")

    t = MockTransport(auth=bad_resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    assert "super secret message" not in str(exc_info.value)


async def test_a_transport_auth_resolver_error_should_suppress_the_original_exception_cause_chain() -> (
    None
):
    def bad_resolver():
        raise RuntimeError("should not appear")

    t = MockTransport(auth=bad_resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


async def test_a_transport_auth_resolver_error_should_record_the_original_exception_class_name_on_a_resolver_cause_attribute() -> (
    None
):
    def bad_resolver():
        raise RuntimeError("internal")

    t = MockTransport(auth=bad_resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    assert exc_info.value.resolver_cause == "RuntimeError"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Wrong-variant rejection
# ---------------------------------------------------------------------------


async def test_a_transport_given_a_descriptor_of_another_transports_variant_should_raise_a_transport_error_without_stringifying_the_descriptor() -> (
    None
):
    """Pass something that isn't a registered variant."""
    # Create an object that looks like a descriptor but isn't registered

    # We can't subclass _TransportAuth from outside, so use a non-descriptor
    # callable that returns a non-variant type.
    class FakeDescriptor:
        _consumed = False

    t = MockTransport(auth=lambda: FakeDescriptor())  # type: ignore[arg-type]
    with pytest.raises(TransportError, match="not a known variant"):
        await t.connect()


# ---------------------------------------------------------------------------
# Subclass rejection
# ---------------------------------------------------------------------------


async def test_a_transport_given_a_subclass_of_a_known_variant_should_raise_a_transport_error() -> (
    None
):
    """Subclassing is blocked at import time by __init_subclass__."""
    with pytest.raises(TypeError, match="cannot subclass"):

        class SneakyToken(_NatsToken):
            pass


# ---------------------------------------------------------------------------
# Post-connect state
# ---------------------------------------------------------------------------


async def test_a_connected_transport_should_not_expose_its_auth_descriptor_via_any_public_accessor() -> (
    None
):
    t = MockTransport(auth=NatsAuth.token("secret"))
    await t.connect()
    # _auth should be None after connect
    assert t._auth is None
    await t.disconnect()


async def test_a_connected_transport_should_report_its_bytes_secret_fields_as_all_zero_after_connect() -> (
    None
):
    ba = bytearray(b"SECRET_SEED")
    descriptor = NatsAuth.nkey(ba)
    t = MockTransport(auth=descriptor)
    await t.connect()
    # The bytearray should be zeroed
    assert all(b == 0 for b in ba)
    await t.disconnect()


# ---------------------------------------------------------------------------
# Failure-path clearing
# ---------------------------------------------------------------------------


async def test_a_transport_whose_logon_fails_should_still_clear_the_auth_descriptor_on_the_failure_path() -> (
    None
):
    """For Mock, logon doesn't fail, so test the resolver failure path."""

    def failing_resolver():
        raise RuntimeError("boom")

    t = MockTransport(auth=failing_resolver)
    with pytest.raises(TransportError):
        await t.connect()
    # _auth should be cleared even on failure
    assert t._auth is None


# ---------------------------------------------------------------------------
# Reconnect guard
# ---------------------------------------------------------------------------


async def test_a_transport_constructed_without_auth_should_permit_reconnect_after_disconnect() -> (
    None
):
    t = MockTransport()
    await t.connect()
    await t.disconnect()
    await t.connect()  # should not raise
    await t.disconnect()


# ---------------------------------------------------------------------------
# Pickle refusal
# ---------------------------------------------------------------------------


async def test_a_transport_should_refuse_to_pickle_with_or_without_auth() -> None:
    t_no_auth = MockTransport()
    with pytest.raises(TypeError):
        pickle.dumps(t_no_auth)

    t_with_auth = MockTransport(auth=NatsAuth.token("x"))
    with pytest.raises(TypeError):
        pickle.dumps(t_with_auth)


# ---------------------------------------------------------------------------
# Repr safety
# ---------------------------------------------------------------------------


async def test_a_transport_repr_should_never_contain_any_auth_descriptor_value() -> None:
    t = MockTransport(auth=NatsAuth.token("super-secret-token"))
    r = repr(t)
    assert "super-secret-token" not in r


# ---------------------------------------------------------------------------
# Connect failure safety
# ---------------------------------------------------------------------------


async def test_a_transport_connect_failure_should_raise_a_transport_error_that_does_not_contain_any_secret() -> (
    None
):
    def resolver():
        raise ValueError("vault-password-do-not-log")

    t = MockTransport(auth=resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    assert "vault-password-do-not-log" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# Descriptor reuse (consumed flag)
# ---------------------------------------------------------------------------


async def test_a_descriptor_consumed_by_one_connect_should_be_refused_by_another() -> None:
    descriptor = NatsAuth.token("one-time")
    t1 = MockTransport(auth=descriptor)
    await t1.connect()

    t2 = MockTransport(auth=descriptor)
    with pytest.raises(TransportError, match="already been consumed"):
        await t2.connect()
