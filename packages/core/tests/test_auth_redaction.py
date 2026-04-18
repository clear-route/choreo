"""Auth redaction tests — verifies that credentials never leak through
repr, eq, deepcopy, pytest assertion rewriting, or resolver error messages.
See ADR-0020 §Validation.
"""

from __future__ import annotations

import copy
import logging

import pytest
from choreo.transports import MockTransport, TransportError
from choreo.transports.nats_auth import NatsAuth

# ---------------------------------------------------------------------------
# Resolver failure redaction
# ---------------------------------------------------------------------------


async def test_a_transport_error_wrapping_a_resolver_failure_should_not_carry_the_resolver_exceptions_original_args() -> (
    None
):
    def resolver():
        raise ValueError("password=s3cret host=db.internal")

    t = MockTransport(auth=resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    msg = str(exc_info.value)
    assert "s3cret" not in msg
    assert "db.internal" not in msg


async def test_a_transport_error_wrapping_a_resolver_failure_should_not_expose_the_original_exceptions_cause_chain() -> (
    None
):
    def resolver():
        try:
            raise ConnectionError("tcp://secret-host:5432")
        except ConnectionError:
            raise RuntimeError("wrapped") from None

    t = MockTransport(auth=resolver)
    with pytest.raises(TransportError) as exc_info:
        await t.connect()
    # No cause chain
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True
    # The original exception messages should not appear
    assert "secret-host" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# pytest assertion rewrite safety (eq=False)
# ---------------------------------------------------------------------------


def test_a_descriptor_subjected_to_pytest_assertion_rewriting_should_not_leak_any_field_value() -> (
    None
):
    a = NatsAuth.user_password("admin", "super-secret-password")
    b = NatsAuth.user_password("admin", "super-secret-password")
    # eq=False means they compare by identity, so a != b
    with pytest.raises(AssertionError) as exc_info:
        assert a == b
    msg = str(exc_info.value)
    assert "super-secret-password" not in msg


# ---------------------------------------------------------------------------
# Deepcopy refusal
# ---------------------------------------------------------------------------


def test_a_descriptor_deepcopied_should_raise_type_error() -> None:
    d = NatsAuth.token("do-not-copy")
    with pytest.raises(TypeError, match="deepcopy"):
        copy.deepcopy(d)


# ---------------------------------------------------------------------------
# Mock warning content
# ---------------------------------------------------------------------------


async def test_a_transport_instance_reference_in_a_captured_log_event_should_render_the_variant_tag_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        t = MockTransport(auth=NatsAuth.token("secret-in-log"))
        await t.connect()

    # The warning should mention the variant tag
    assert any("mock_transport_ignored_auth" in r.message for r in caplog.records)
    # But never the secret
    for record in caplog.records:
        assert "secret-in-log" not in record.getMessage()
        extra = getattr(record, "auth_variant", "")
        assert "secret-in-log" not in str(extra)
    await t.disconnect()
