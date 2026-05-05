"""Group A: Stage construction. Negative-behaviour integration tests.

Covers test-plan items A1-A9 from
`docs/test-plans/0027-stage-integration-tests.md` — every failure path the
Stage's `__init__` is responsible for catching before the user can call
`connect()`.

Each test maps to one ADR-0027 §Validation success metric (or one of the
25 review items, R1-R25). The mapping is named in the test docstring.

These tests assume `from choreo.stage import Stage, ...` resolves. Until
the production code lands, every test in this module is expected to fail
with `ImportError` — that is the TDD red state.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import (
    _CollidingBridge,
    _RaisingBridge,
    _TypeBrokenBridge,
    make_mapped_bridge,
)

# ---------------------------------------------------------------------------
# A1 — empty harness mapping
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_an_empty_harness_mapping() -> None:
    """A1. Stage requires at least one harness; empty mapping is a config bug.

    Covers ADR-0027 §Implementation Stage.__init__ early guard.
    """
    from choreo.stage import IdentityBridge, Stage

    with pytest.raises(ValueError, match="at least one harness"):
        Stage(harnesses={}, bridge=IdentityBridge())


# ---------------------------------------------------------------------------
# A2 — colliding bridge fails the startup smoke test
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_a_bridge_that_collides_on_the_smoke_test(
    two_harnesses: dict[str, Any],
) -> None:
    """A2. A bridge returning the same wire id for every transport is the
    failure mode `BridgeAmbiguityError` exists to catch — at startup, before
    any test code can rely on (broken) per-transport routing.

    The `.transports` tuple is sorted so consumer code can assert
    `excinfo.value.transports == ("kafka", "nats")` without flake risk
    from registration order.

    Covers R6 (smoke-test claim weakened but still fires);
    ADR-0027 §Validation "BridgeAmbiguityError at startup smoke test".
    """
    from choreo.stage import BridgeAmbiguityError, Stage

    with pytest.raises(BridgeAmbiguityError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=_CollidingBridge())

    # Deterministic tuple ordering (sorted) — consumer code can assert
    # exactly without depending on registration order.
    assert excinfo.value.transports == ("kafka", "nats")
    assert "same-wire-id-for-every-transport" not in str(excinfo.value)


def test_stage_construction_should_accept_a_mapped_bridge_advertising_transports_as_a_list(
    two_harnesses: dict[str, Any],
) -> None:
    """A custom bridge whose `configured_transports` returns a list (not
    a frozenset) must NOT trip BridgeTransportMismatchError when the
    names match. The Stage coerces the advertised set before comparing.
    """
    from choreo.stage import Stage

    class _ListAdvertisingBridge:
        @property
        def configured_transports(self) -> list[str]:
            return ["nats", "kafka"]

        async def fresh(self) -> str:
            return "logical"

        def to_wire(self, logical: Any, transport: str) -> str:
            return f"{transport}-{logical}"

        def from_wire(self, wire: str, transport: str) -> Any | None:
            return None

    # Must not raise.
    stage = Stage(harnesses=two_harnesses, bridge=_ListAdvertisingBridge())
    assert stage is not None


# ---------------------------------------------------------------------------
# A3 — MappedBridge missing a registered transport
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_a_mapped_bridge_missing_a_registered_transport(
    two_harnesses: dict[str, Any],
) -> None:
    """A3. The transport-set mismatch surfaces as a typed
    BridgeTransportMismatchError, not a generic BridgeTranslationError
    wrapping a KeyError. Diagnostic surface matters here — the consumer
    needs to see the two sets to fix their config.

    Covers ADR-0027 §Validation "BridgeTransportMismatchError for MappedBridge".
    """
    from choreo.stage import BridgeTransportMismatchError, Stage

    bridge = make_mapped_bridge(forwards={"nats": lambda logical: f"n-{logical}"})

    with pytest.raises(BridgeTransportMismatchError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert excinfo.value.bridge_transports == ("nats",)
    assert excinfo.value.registered_transports == ("kafka", "nats")


# ---------------------------------------------------------------------------
# A4 — MappedBridge with an extra transport (symmetry with A3)
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_a_mapped_bridge_with_an_extra_transport(
    two_harnesses: dict[str, Any],
) -> None:
    """A4. Symmetric to A3: bridge knows about a transport the Stage does
    not have. Same typed error; both sets present on the attributes.
    """
    from choreo.stage import BridgeTransportMismatchError, Stage

    bridge = make_mapped_bridge(
        forwards={
            "nats": lambda logical: f"n-{logical}",
            "kafka": lambda logical: f"k-{logical}",
            "extra": lambda logical: f"x-{logical}",
        }
    )

    with pytest.raises(BridgeTransportMismatchError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert "extra" in excinfo.value.bridge_transports
    assert "extra" not in excinfo.value.registered_transports


# ---------------------------------------------------------------------------
# A5 — to_wire raising during the smoke test
# ---------------------------------------------------------------------------


def test_stage_construction_should_wrap_a_bridge_to_wire_exception_during_smoke_test(
    two_harnesses: dict[str, Any],
) -> None:
    """A5. Consumer bridge code is a trust boundary. An uncaught exception
    surfaces as a typed BridgeTranslationError, with the original on a named
    attribute (mirroring ADR-0019's CorrelationPolicyError shape) so
    consumers do not have to walk __cause__.

    Covers R19 (.original attribute populated);
    ADR-0027 §Validation "BridgeTranslationError carries .original".
    """
    from choreo.stage import BridgeTranslationError, Stage

    injected = RuntimeError("bridge said no")
    bridge = _RaisingBridge(raise_on="to_wire", exc=injected)

    with pytest.raises(BridgeTranslationError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert excinfo.value.method == "to_wire"
    assert excinfo.value.bridge_class == "_RaisingBridge"
    assert excinfo.value.original is injected
    # Failure happens on the FIRST registered transport (nats), not later.
    assert excinfo.value.transport == "nats"


# ---------------------------------------------------------------------------
# A6 — to_wire returning a non-string type
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_return",
    [
        pytest.param(None, id="None"),
        pytest.param(42, id="int"),
        pytest.param(b"bytes", id="bytes"),
        pytest.param(3.14, id="float"),
        pytest.param([], id="empty-list"),
    ],
)
def test_stage_construction_should_reject_a_bridge_to_wire_returning_a_non_string(
    two_harnesses: dict[str, Any], bad_return: Any
) -> None:
    """A6. The Protocol declares `-> str` but Python does not enforce.
    The Stage validates because the inbound matching path uses string
    equality; an int or bytes return silently breaks correlation.

    Covers R9 (return-type validation);
    ADR-0027 §Validation "to_wire return-type validation".
    """
    from choreo.stage import BridgeTranslationError, Stage

    bridge = _TypeBrokenBridge(returns=bad_return)

    with pytest.raises(BridgeTranslationError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert excinfo.value.method == "to_wire"
    assert isinstance(excinfo.value.original, TypeError)


# ---------------------------------------------------------------------------
# A7 — to_wire returning an empty string (separate from non-string per plan)
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_a_bridge_to_wire_returning_an_empty_string(
    two_harnesses: dict[str, Any],
) -> None:
    """A7. Empty string is a `str` instance but useless as a wire id; an
    empty value silently collides on the inbound match path. Rejected with
    the same typed error as the non-string returns.
    """
    from choreo.stage import BridgeTranslationError, Stage

    bridge = _TypeBrokenBridge(returns="")

    with pytest.raises(BridgeTranslationError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert excinfo.value.method == "to_wire"
    assert isinstance(excinfo.value.original, TypeError)


# ---------------------------------------------------------------------------
# A8 — to_wire returning an oversized string
# ---------------------------------------------------------------------------


def test_stage_construction_should_reject_a_bridge_to_wire_returning_an_oversized_string(
    two_harnesses: dict[str, Any],
) -> None:
    """A8. The Stage caps wire id length to defend the dispatcher key
    space, log lines, and Handle.correlation_id from a bridge with no
    bounds. The original exception is a ValueError naming both lengths.

    Covers R16 (bounds enforcement).
    """
    from choreo.stage import _MAX_WIRE_ID_LEN, BridgeTranslationError, Stage

    oversized = "x" * (_MAX_WIRE_ID_LEN + 1)
    bridge = _TypeBrokenBridge(returns=oversized)

    with pytest.raises(BridgeTranslationError) as excinfo:
        Stage(harnesses=two_harnesses, bridge=bridge)

    assert excinfo.value.method == "to_wire"
    assert isinstance(excinfo.value.original, ValueError)
    # Both the actual length and the limit appear in the diagnostic.
    msg = str(excinfo.value.original)
    assert str(_MAX_WIRE_ID_LEN + 1) in msg
    assert str(_MAX_WIRE_ID_LEN) in msg


# ---------------------------------------------------------------------------
# A9 — boundary: exactly at the length limit is accepted
# ---------------------------------------------------------------------------


def test_redact_should_quote_short_strings_unredacted_and_truncate_long_ones() -> None:
    """The `_redact` helper redacts long strings (head + tail + length
    annotation) for safe inclusion in error messages, and quotes short
    ones unmodified. Boundary: strings of length head+tail+3 or less are
    treated as short. The +3 is the literal `...` separator's length.
    """
    from choreo.stage import _redact

    # Short string: returned via repr() (quoted, unredacted).
    assert _redact("short") == "'short'"
    # At-the-boundary length (head=8 + tail=4 + 3 = 15): still short.
    assert _redact("x" * 15) == repr("x" * 15)
    # One char over the boundary: redacted with head/tail markers.
    over = "x" * 16
    redacted = _redact(over)
    assert "..." in redacted
    assert "len=16" in redacted
    # Different head/tail values respect the boundary identically.
    assert _redact("ab", head=1, tail=1) == "'ab'"  # under boundary 5
    assert _redact("abcdefgh", head=1, tail=1) == "'a'...'h' (len=8)"


def test_stage_construction_should_emit_an_audit_log_naming_the_bridge_class(
    two_harnesses: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ADR-0027 §Security Considerations promises a structured startup
    log naming the bridge class so audit can identify what bridge code
    was in effect for a given run.
    """
    import logging

    from choreo.stage import Stage

    bridge = make_mapped_bridge()  # MappedBridge over the canonical pair

    with caplog.at_level(logging.INFO, logger="choreo.stage"):
        Stage(harnesses=two_harnesses, bridge=bridge)

    init_records = [r for r in caplog.records if r.getMessage() == "stage_initialised"]
    assert len(init_records) == 1
    record = init_records[0]
    assert record.bridge_class == "MappedBridge"
    assert set(record.transports) == {"nats", "kafka"}


def test_stage_construction_should_accept_a_bridge_to_wire_at_exactly_the_length_limit(
    two_harnesses: dict[str, Any],
) -> None:
    """A9. Boundary partner of A8: the limit is inclusive. A bridge whose
    return is exactly _MAX_WIRE_ID_LEN chars long must construct cleanly,
    so consumers who tune to the limit are not silently broken on
    next-version off-by-one regressions.

    Covers R16 boundary.

    Note: this scenario uses two transports but the bridge collides
    (returns the same value for both). To isolate the length check from
    the distinctness check, we use a per-transport-suffixed bridge so the
    smoke-test distinctness passes and only the length check is in play.
    """
    from choreo.stage import _MAX_WIRE_ID_LEN, Stage

    base = "x" * (_MAX_WIRE_ID_LEN - 5)  # leave room for "-nats" / "-kafka"

    class _PerTransportLengthLimitBridge:
        async def fresh(self) -> str:
            return "logical"

        def to_wire(self, logical: Any, transport: str) -> str:
            # Suffix differs per transport so distinctness passes; total
            # length stays at exactly _MAX_WIRE_ID_LEN for both.
            suffix = "-nats" if transport == "nats" else "-kafk"
            value = base + suffix
            assert len(value) == _MAX_WIRE_ID_LEN
            return value

        def from_wire(self, wire: str, transport: str) -> Any | None:
            return None

    # Construction must not raise.
    stage = Stage(harnesses=two_harnesses, bridge=_PerTransportLengthLimitBridge())
    assert stage is not None
