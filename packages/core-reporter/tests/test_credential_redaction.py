"""Credential-shape redaction — PRD-007 §12."""

from __future__ import annotations

import pytest
from choreo_reporter._redact import (
    REDACTED,
    RedactionStats,
    _clear_consumer_redactors_for_test,
    redact_stream,
    redact_structured,
    register_redactor,
)


@pytest.fixture(autouse=True)
def _cleanup_registry():
    yield
    _clear_consumer_redactors_for_test()


# ---------------------------------------------------------------------------
# redact_structured
# ---------------------------------------------------------------------------


def test_a_password_field_should_be_replaced_with_redacted_marker() -> None:
    stats = RedactionStats()
    out = redact_structured({"user": "alice", "password": "hunter2"}, stats)
    assert out["password"] == REDACTED
    assert out["user"] == "alice"
    assert stats.fields == 1


def test_an_api_key_variant_should_be_caught_case_insensitively() -> None:
    stats = RedactionStats()
    out = redact_structured({"Api_Key": "x", "API-KEY": "y"}, stats)
    assert out["Api_Key"] == REDACTED
    assert out["API-KEY"] == REDACTED
    assert stats.fields == 2


def test_a_bearer_token_field_should_be_redacted() -> None:
    stats = RedactionStats()
    out = redact_structured({"bearer": "abc.def.ghi"}, stats)
    assert out["bearer"] == REDACTED


def test_nested_credential_fields_should_be_redacted() -> None:
    stats = RedactionStats()
    payload = {
        "order": {"id": 1, "credentials": {"token": "t", "password": "p"}},
        "list": [{"secret": "s"}, {"ok": "fine"}],
    }
    out = redact_structured(payload, stats)
    assert out["order"]["credentials"]["token"] == REDACTED
    assert out["order"]["credentials"]["password"] == REDACTED
    assert out["list"][0]["secret"] == REDACTED
    assert out["list"][1]["ok"] == "fine"
    assert stats.fields == 3


def test_a_non_credential_key_should_be_left_alone() -> None:
    stats = RedactionStats()
    out = redact_structured({"username": "alice", "qty": 1000}, stats)
    assert out == {"username": "alice", "qty": 1000}
    assert stats.fields == 0


# ---------------------------------------------------------------------------
# redact_stream
# ---------------------------------------------------------------------------


def test_a_bearer_token_in_free_text_should_be_replaced() -> None:
    stats = RedactionStats()
    text = "Authorization: Bearer abc.def-ghi"
    out = redact_stream(text, stats)
    assert "abc.def-ghi" not in out
    assert REDACTED in out
    assert stats.stream_matches >= 1


def test_an_x_api_key_header_in_free_text_should_be_replaced() -> None:
    stats = RedactionStats()
    text = "POST /api\nX-API-Key: 1234567890abcdef"
    out = redact_stream(text, stats)
    assert "1234567890abcdef" not in out
    assert REDACTED in out


def test_a_password_in_an_inline_assignment_should_be_replaced() -> None:
    stats = RedactionStats()
    out = redact_stream("connecting with password=secret123", stats)
    assert "secret123" not in out


def test_an_empty_string_should_round_trip_without_counting() -> None:
    stats = RedactionStats()
    assert redact_stream("", stats) == ""
    assert stats.stream_matches == 0


def test_a_url_with_user_and_password_in_free_text_should_be_scrubbed() -> None:
    stats = RedactionStats()
    text = "could not connect to amqp://admin:hunter2@broker.internal:5672/"
    out = redact_stream(text, stats)
    assert "admin" not in out
    assert "hunter2" not in out
    assert "broker.internal:5672" in out  # host + port survive
    assert REDACTED in out


def test_a_url_with_only_password_in_free_text_should_be_scrubbed() -> None:
    stats = RedactionStats()
    out = redact_stream("DATABASE_URL=postgres://:topsecret@db:5432/app", stats)
    assert "topsecret" not in out
    assert "db:5432" in out


# ---------------------------------------------------------------------------
# Value-level scrubbing inside structured payloads
# ---------------------------------------------------------------------------


def test_a_url_credential_inside_a_payload_value_should_be_scrubbed() -> None:
    """A field named `config` (not a credential-key) that holds a connection
    string with inline credentials must not survive into the report."""
    stats = RedactionStats()
    payload = {"config": "amqp://admin:hunter2@broker.internal:5672/"}
    out = redact_structured(payload, stats)
    assert "hunter2" not in out["config"]
    assert "admin" not in out["config"]
    assert REDACTED in out["config"]


def test_a_bearer_token_inside_a_payload_value_should_be_scrubbed() -> None:
    stats = RedactionStats()
    payload = {"http_header": "Authorization: Bearer abc.def.ghi"}
    out = redact_structured(payload, stats)
    assert "abc.def.ghi" not in out["http_header"]


def test_a_nested_list_value_string_should_be_scrubbed() -> None:
    stats = RedactionStats()
    payload = {
        "routes": [
            {"name": "primary", "uri": "postgres://user:pw@db:5432/app"},
        ]
    }
    out = redact_structured(payload, stats)
    assert "pw" not in out["routes"][0]["uri"]
    assert "db:5432" in out["routes"][0]["uri"]


# ---------------------------------------------------------------------------
# Consumer-registered redactors
# ---------------------------------------------------------------------------


def test_a_registered_consumer_redactor_should_run_after_the_builtin() -> None:
    stats = RedactionStats()

    def strip_username(payload):
        if isinstance(payload, dict) and "username" in payload:
            payload["username"] = "<pii>"
        return payload

    register_redactor(strip_username)
    out = redact_structured({"username": "alice", "password": "hunter2"}, stats)
    assert out["username"] == "<pii>"
    assert out["password"] == REDACTED


def test_a_consumer_redactor_that_raises_should_not_break_redaction() -> None:
    stats = RedactionStats()

    def boom(payload):
        raise RuntimeError("consumer redactor blew up")

    register_redactor(boom)
    out = redact_structured({"password": "x"}, stats)
    assert out["password"] == REDACTED
