"""Behavioural tests for the credential-scrubbing helper used in transport
error messages.

Real transports (Rabbit, Redis) format their connection URL into the
`TransportError` they raise on connect failure. URLs carry credentials inline
(`amqp://user:pw@host`, `redis://:pw@host`); those credentials must never
appear in exception tracebacks, which reach CI logs and captured test output.
"""

from __future__ import annotations

from choreo.transports.base import safe_url


def test_safe_url_should_redact_both_user_and_password() -> None:
    assert safe_url("amqp://guest:guest@localhost:5672/") == ("amqp://<redacted>@localhost:5672/")


def test_safe_url_should_redact_when_only_password_is_present() -> None:
    assert safe_url("redis://:pw@example.com:6379/0") == ("redis://<redacted>@example.com:6379/0")


def test_safe_url_should_redact_when_only_user_is_present() -> None:
    assert safe_url("redis://user@example.com:6379/0") == ("redis://<redacted>@example.com:6379/0")


def test_safe_url_should_preserve_a_url_without_credentials() -> None:
    assert safe_url("redis://localhost:6379/0") == "redis://localhost:6379/0"
    assert safe_url("nats://localhost:4222") == "nats://localhost:4222"


def test_safe_url_should_preserve_the_scheme_path_and_query() -> None:
    assert safe_url("amqp://u:p@host/vhost?heartbeat=30") == (
        "amqp://<redacted>@host/vhost?heartbeat=30"
    )


def test_safe_url_should_not_leak_credentials_in_any_output() -> None:
    """Defensive check: whatever transformation we apply, neither the username
    nor the password should survive in the returned string."""
    out = safe_url("amqp://sensitive_user:secretpassword@broker.internal:5672/")
    assert "sensitive_user" not in out
    assert "secretpassword" not in out
    assert "broker.internal" in out
    assert "5672" in out


def test_safe_url_should_return_a_malformed_url_unchanged() -> None:
    """We prefer not to eat pre-existing malformed input — returning the
    original string keeps the caller's error clearer than `<redacted>`."""
    assert safe_url("not-a-url") == "not-a-url"
