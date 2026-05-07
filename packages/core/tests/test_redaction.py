"""Tests for `admiral.redaction` — report-boundary redaction (PRD-012 §1.5.1).

The framework's in-process `_redact()` (head=8, tail=4, length annotation)
is preserved for short-lived error messages. The on-disk `results.json`
boundary uses a stricter hash-based redaction here: SHA-256 truncated
to 16 hex chars, prefixed `sha256:`. Single source of truth — the
admiral-reporter imports from this module rather than re-implementing.

Properties asserted:

* Output shape is exactly `sha256:<16 hex chars>` for every input.
* Deterministic: same input → same output.
* Distinct inputs → distinct outputs (within the test fixture set).
* Non-reversible: the input string never appears verbatim in the
  output (Hypothesis property).
* `REDACTION_VERSION` is `"v1"`; the reporter emits this in
  `run.redactions.redaction_version` so consumers can detect
  algorithm changes.
"""

from __future__ import annotations

import re

import pytest
from admiral.redaction import REDACTION_VERSION, redact_correlation_id
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_REDACTED_PATTERN = re.compile(r"^sha256:[0-9a-f]{16}$")


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "wire_id",
    [
        "",
        "x",
        "ab",
        "abc",
        "0123456789abcdef",  # 16 chars
        "0123456789abcdef0123456789abcdef",  # 32 chars
        "x" * 1000,  # 1000 chars
        "kafka-orders-3f2a91b8c4d50e1f",
        "nats.subject.orders.bridge.3f2a91b8c4d50e1f",
    ],
)
def test_redact_correlation_id_should_emit_a_sha256_prefixed_16_hex_string(
    wire_id: str,
) -> None:
    """Output shape is invariant across input length: `sha256:<16 hex>`."""
    redacted = redact_correlation_id(wire_id)
    assert _REDACTED_PATTERN.fullmatch(redacted), (
        f"unexpected shape: {redacted!r} for input len={len(wire_id)}"
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_redact_correlation_id_should_be_deterministic_for_the_same_input():
    """Two calls with the same input produce the same output."""
    a = redact_correlation_id("kafka-orders-3f2a91b8")
    b = redact_correlation_id("kafka-orders-3f2a91b8")
    assert a == b


def test_redact_correlation_id_should_distinguish_distinct_inputs():
    """Different inputs produce different outputs (no collision in the
    test fixture set; the 16-hex-char prefix gives ~64 bits of space)."""
    outputs = {
        redact_correlation_id("kafka-orders-3f2a91b8"),
        redact_correlation_id("nats-orders-3f2a91b8"),
        redact_correlation_id("kafka-orders-7e8b50c1"),
        redact_correlation_id("kafka-payments-3f2a91b8"),
    }
    assert len(outputs) == 4


# ---------------------------------------------------------------------------
# Non-reversibility (Hypothesis property)
# ---------------------------------------------------------------------------


@given(st.text(min_size=4, max_size=200))
@settings(suppress_health_check=[HealthCheck.differing_executors], deadline=None)
def test_a_redacted_wire_id_should_not_contain_the_original_string(
    wire_id: str,
) -> None:
    """The original wire id must not appear verbatim inside the
    redacted output. Hash-based redaction with a 16-hex-char truncation
    has effectively no collision risk for short strings."""
    redacted = redact_correlation_id(wire_id)
    if len(wire_id) >= 4:
        # Only assert non-substring for inputs of meaningful length.
        # A 1-char input could plausibly appear in a hex-string output
        # by chance (the hex alphabet is small).
        assert wire_id not in redacted


# ---------------------------------------------------------------------------
# Module-level constant
# ---------------------------------------------------------------------------


def test_redaction_version_should_be_v1():
    """The version constant is what the reporter emits in
    `run.redactions.redaction_version`. Bumping it signals an algorithm
    change to consumers."""
    assert REDACTION_VERSION == "v1"


def test_redaction_version_should_be_a_string_matching_the_schema_pattern():
    """The schema (PRD-012 Appendix A.1) declares
    `redaction_version` with pattern `^v[0-9]+$`. The constant must
    satisfy this pattern."""
    assert re.fullmatch(r"^v[0-9]+$", REDACTION_VERSION)
