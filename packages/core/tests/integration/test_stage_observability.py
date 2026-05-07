"""Group M: Stage bridge call observability integration tests.

Covers test-plan items M1-M2.

M1 verifies the structured startup audit log emitted by
`Stage.__init__` (`stage_initialised`) carries the bridge class name
and the registered transport names — the audit trail ADR-0027
§Security Considerations promises.

M2 verifies the `from_wire` diagnostic path: when an inbound
correlation id does not match any active scope and a bridge whose
`from_wire` raises is in effect, a structured WARNING
(`stage_from_wire_failed`) is emitted; the inbound message is
silently treated as unmatched. The dispatcher loop is not poisoned.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from admiral import Harness
from admiral.correlation import DictFieldPolicy
from admiral.matchers import field_equals
from admiral.stage import Stage
from admiral.transports import MockTransport

from .conftest import mapped_bridge_for, two_harnesses  # noqa: F401

_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# M1 — stage_initialised audit log carries bridge class + transports
# ---------------------------------------------------------------------------


async def test_stage_construction_should_emit_a_stage_initialised_audit_log(
    two_harnesses: dict[str, Harness],  # noqa: F811 — fixture re-import
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M1. The startup audit ADR-0027 §Security Considerations
    promises: `Stage.__init__` emits a structured INFO event named
    `stage_initialised` with `bridge_class` (str) and `transports`
    (tuple[str, ...]) on the LogRecord. Audit can identify which
    bridge was in effect for a given run.

    Also covered by Group A's
    `test_stage_construction_should_emit_an_audit_log_naming_the_bridge_class`;
    M1 is the "observability" framing of the same contract, with a
    sharper assertion on the structured fields.
    """
    bridge = mapped_bridge_for("nats", "kafka")

    with caplog.at_level(logging.INFO, logger="admiral.stage"):
        Stage(harnesses=two_harnesses, bridge=bridge)

    init_records = [r for r in caplog.records if r.getMessage() == "stage_initialised"]
    assert len(init_records) == 1
    record = init_records[0]
    assert record.bridge_class == "MappedBridge"
    assert tuple(record.transports) == ("nats", "kafka")


# ---------------------------------------------------------------------------
# M2 — from_wire raise during diagnostic path is logged + scope continues
# ---------------------------------------------------------------------------


async def test_stage_should_log_warning_when_from_wire_raises_during_diagnostics(
    allowlist_yaml_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """M2. When an inbound message's correlation id is present but
    does not match any active scope, the framework optionally calls
    `bridge.from_wire(msg_corr, transport)` for diagnostic
    translation. If `from_wire` raises, a structured WARNING
    (`stage_from_wire_failed`) is emitted with the bridge class name
    and the transport. The inbound message is silently treated as
    unmatched (the dispatcher does not propagate the diagnostic
    failure).

    Setup: a bridge whose `from_wire` raises. Stage is constructed
    and connected. A scope opens with one expectation. We publish a
    DIFFERENT correlation id (one that does NOT match the scope's
    wire id) — the filter rejects it AND attempts the diagnostic,
    which raises. The WARNING fires; the scope's expectation stays
    unresolved (it would TIMEOUT but we don't await long enough to
    care).
    """

    class _FromWireRaisingBridge:
        """MappedBridge-like contract for to_wire (so distinctness
        passes), but `from_wire` raises. Diagnostic-only path."""

        async def fresh(self) -> str:
            return "logical-m2"

        def to_wire(self, logical: Any, transport: str) -> str:
            return f"{transport}-{logical}"

        def from_wire(self, wire: str, transport: str) -> Any:
            raise RuntimeError(f"from_wire test-injected failure: {wire!r}")

    nats_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    stage = Stage(harnesses={"nats": nats_h}, bridge=_FromWireRaisingBridge())
    await stage.connect()

    try:
        with caplog.at_level(logging.WARNING, logger="admiral.stage"):
            async with stage.scenario("m2") as scope:
                # Register an expectation; the scope has wire_id
                # `nats-logical-m2` for this transport.
                scope.expect("topic", _PLACEHOLDER_MATCHER, on="nats")

                # Publish DIRECTLY to the underlying transport (bypassing
                # Stage.publish) with a DIFFERENT correlation_id so the
                # filter rejects it AND attempts from_wire diagnostics.
                # The payload is a JSON dict with `correlation_id` set
                # to a string that does NOT match the scope's wire_id.
                import json

                wrong_payload = {
                    "status": "ok",
                    "correlation_id": "nats-some-other-scope",
                }
                nats_h._transport.publish(  # type: ignore[attr-defined]
                    "topic", json.dumps(wrong_payload).encode("utf-8")
                )

                # Don't await_all — the test's subject is the WARNING,
                # not the timeout. Scope exits; teardown runs.
    finally:
        await stage.disconnect()

    # The diagnostic failure was logged.
    diag_warnings = [r for r in caplog.records if r.getMessage() == "stage_from_wire_failed"]
    assert len(diag_warnings) == 1
    record = diag_warnings[0]
    assert record.bridge_class == "_FromWireRaisingBridge"
    assert record.transport == "nats"
    assert record.error_class == "RuntimeError"
