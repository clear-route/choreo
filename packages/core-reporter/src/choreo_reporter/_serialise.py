"""Serialisation — PRD-007 §3, §5; PRD-012 §1.7, §2.2, §2.3, §2.4.

Converts `ScenarioResult` / `StageScenarioResult` / `Handle` /
`TimelineEntry` (defined in `core`) into JSON-shaped dicts that
conform to docs/schemas/test-report-v1.1.json.

Applies size caps in a single tree walk: string fields >2 KB are replaced
with a truncation marker, and a JSON-encoded per-payload cap of 8 KB
guards against large dicts.

Outcome values are normalised according to the table in PRD-007 §3.

Stage scenarios (PRD-012):

* `serialise_scenario` dispatches on `result.kind` — Stage results
  emit the additional `stage` block plus `handle.transport` and
  redacted `handle.correlation_id`.
* `_serialise_reply_state` maps `StageReplyState.FIRED` → `"replied"`
  and `StageReplyState.FIRED_BUILDER_ERROR` → `"reply_failed"` (no
  enum extension — the wire format keeps its v1.0 four values).
* Wire ids land at the report boundary via
  `choreo.redaction.redact_correlation_id` (single source of truth — the
  reporter does not re-implement).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from choreo._redact import redact_matcher_description
from choreo.matchers import MatchFailure
from choreo.redaction import redact_correlation_id
from choreo.scenario import (
    Handle,
    Outcome,
    ReplyReport,
    ReplyReportState,
    ScenarioResult,
    TimelineEntry,
)
from choreo.stage import (
    StageReplyReport,
    StageReplyState,
    StageScenarioResult,
)

from ._redact import RedactionStats, redact_structured

# ---------------------------------------------------------------------------
# Caps (PRD-007 §5)
# ---------------------------------------------------------------------------


PAYLOAD_BYTES_CAP = 8 * 1024
FIELD_STRING_CAP = 2 * 1024
STREAM_BYTES_CAP = 64 * 1024
TIMELINE_CAP = 256
HANDLES_CAP = 100
SCENARIOS_CAP = 100
TESTS_CAP = 1000


def _truncation_marker(original_bytes: int, head: Any) -> dict[str, Any]:
    return {"_truncated": True, "_original_bytes": original_bytes, "_head": head}


def cap_string(value: str, limit: int = FIELD_STRING_CAP) -> str | dict[str, Any]:
    """Return the string unchanged, or a truncation marker if it exceeds limit.

    Returns a dict marker for strings over the cap; callers who receive
    a dict where a string was expected should treat it as an opaque
    truncated placeholder (this is what the JSON Schema expects for the
    `actual` / `expected` fields which accept any type).
    """
    if not isinstance(value, str):
        return value
    raw = value.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return value
    return _truncation_marker(
        original_bytes=len(raw),
        head=raw[:limit].decode("utf-8", errors="replace"),
    )


def cap_stream(text: str, limit: int = STREAM_BYTES_CAP) -> str:
    """Tail-keep at most `limit` bytes of captured stdout/stderr/log.

    Head-dropped with a leading `[N bytes truncated]` marker so readers
    know content was elided. Returns the original string when it fits.
    """
    if not text:
        return text
    raw = text.encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return text
    dropped = len(raw) - limit
    tail = raw[-limit:].decode("utf-8", errors="replace")
    return f"[{dropped} bytes truncated]\n{tail}"


def cap_payload(value: Any, stats: RedactionStats) -> tuple[Any, bool]:
    """Redact + cap a payload for handle `actual` / `expected`.

    Returns `(value_or_marker, truncated_flag)`. The caller uses the
    flag to set the handle's `truncated` field.

    Strategy: redact first (so we never ship secrets even in the head
    slice), then try a single tree walk with per-field string capping,
    then check the JSON-encoded size against the per-payload cap. If
    over, replace with a truncation marker carrying the head of the
    encoded form.
    """
    if value is None:
        return None, False
    try:
        walked = _walk_and_cap_strings(redact_structured(value, stats))
        encoded = json.dumps(walked, default=str)
    except (TypeError, ValueError):
        # Fall back to repr for non-JSON values (custom classes, etc.).
        # A failure here should not break the report.
        try:
            encoded = repr(value)
        except Exception:  # pragma: no cover — defensive
            encoded = "<unserialisable>"
        walked = {"_unserialisable": True, "_repr": cap_string(encoded)}
        return walked, False

    if len(encoded) <= PAYLOAD_BYTES_CAP:
        return walked, False
    return (
        _truncation_marker(
            original_bytes=len(encoded),
            head=encoded[:PAYLOAD_BYTES_CAP],
        ),
        True,
    )


def _walk_and_cap_strings(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _walk_and_cap_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_walk_and_cap_strings(v) for v in value]
    if isinstance(value, tuple):
        return [_walk_and_cap_strings(v) for v in value]
    if isinstance(value, str):
        return cap_string(value)
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    # Unknown types — coerce to string via repr so json.dumps won't raise.
    return repr(value)


# ---------------------------------------------------------------------------
# Outcome normalisation (PRD-007 §3)
# ---------------------------------------------------------------------------


_HANDLE_OUTCOME_MAP = {
    Outcome.PASS: "pass",
    Outcome.FAIL: "fail",
    Outcome.TIMEOUT: "timeout",
    Outcome.SLOW: "slow",
    Outcome.PENDING: "pending",
}

_SCENARIO_OUTCOME_PRIORITY = {
    "timeout": 4,
    "fail": 3,
    "slow": 2,
    "pending": 1,
    "pass": 0,
}


def serialise_handle_outcome(outcome: Outcome) -> str:
    return _HANDLE_OUTCOME_MAP[outcome]


def derive_scenario_outcome(handles: tuple[Handle, ...]) -> str:
    """Worst-handle wins; defaults to pass for empty handle tuples."""
    if not handles:
        return "pass"
    worst = max(
        (serialise_handle_outcome(h.outcome) for h in handles),
        key=lambda o: _SCENARIO_OUTCOME_PRIORITY.get(o, 0),
    )
    # A scenario's outcome vocabulary excludes `pending`; treat any
    # unresolved handle as `fail` for serialisation purposes. In
    # practice `await_all` resolves PENDING into TIMEOUT or FAIL, so
    # this only fires for the partial-emit path.
    if worst == "pending":
        return "fail"
    return worst


def derive_test_outcome(pytest_outcome: str, scenario_outcomes: list[str]) -> str:
    """Map a pytest outcome + per-scenario outcomes into the test-level string.

    `pytest_outcome` is the value from pytest's `TestReport.outcome`
    (one of 'passed', 'failed', 'skipped'), plus our own 'errored' when
    the failing phase is setup/teardown.

    Test passes pytest-wise but carries a SLOW scenario → 'slow'.
    """
    if pytest_outcome in ("failed", "errored", "skipped"):
        return pytest_outcome
    if pytest_outcome == "passed":
        if any(o == "slow" for o in scenario_outcomes):
            return "slow"
        return "passed"
    # Unknown — surface rather than silently map.
    return pytest_outcome


# ---------------------------------------------------------------------------
# Handle / scenario / timeline serialisers
# ---------------------------------------------------------------------------


def serialise_timeline_entry(entry: TimelineEntry) -> dict[str, Any]:
    return {
        "offset_ms": entry.offset_ms,
        "wall_clock": entry.wall_clock,
        "topic": entry.topic,
        "action": entry.action.value,
        "detail": entry.detail,
    }


def serialise_match_failure(failure: MatchFailure, stats: RedactionStats) -> dict[str, Any]:
    """Render a MatchFailure into the JSON shape in `docs/schemas/test-report-v1.json`.

    `expected` and `actual` are capped and redacted via the same pipeline as
    handle payloads — matchers can carry payload-derived values in `actual`
    (e.g. `payload_contains`'s `{payload_hex: ...}`), and a custom matcher
    could stash arbitrary sub-payloads there.
    """
    expected, _ = cap_payload(failure.expected, stats)
    actual, _ = cap_payload(failure.actual, stats)
    out: dict[str, Any] = {
        "kind": failure.kind,
        "path": failure.path,
        "expected": expected,
        "actual": actual,
    }
    if failure.children:
        out["children"] = [serialise_match_failure(c, stats) for c in failure.children]
    return out


def _derive_diagnosis(handle: Handle) -> dict[str, Any]:
    """Typed classification replacing `handle._reason` prose for the report UI.

    The UI composes any human text; the framework never emits English here.
    Branches mirror the Outcome enum exactly so adding a new outcome forces
    a typed update here rather than letting a prose string leak through.
    """
    if handle.outcome is Outcome.PASS:
        return {"kind": "matched"}
    if handle.outcome is Outcome.SLOW:
        return {
            "kind": "over_budget",
            "latency_ms": handle._latency_ms or 0.0,
            "budget_ms": handle._budget_ms or 0.0,
        }
    if handle.outcome is Outcome.FAIL:
        return {
            "kind": "near_miss",
            "attempts": handle.attempts,
            "deadline_ms": handle._latency_ms or 0.0,
        }
    if handle.outcome is Outcome.TIMEOUT:
        return {
            "kind": "silent_timeout",
            "topic": handle.topic,
            "deadline_ms": handle._latency_ms or 0.0,
        }
    return {"kind": "pending"}


def serialise_handle(handle: Handle, stats: RedactionStats) -> dict[str, Any]:
    outcome = serialise_handle_outcome(handle.outcome)

    # Actual is the matched payload for PASS/SLOW, last-mismatched for FAIL,
    # null for TIMEOUT / PENDING.
    if handle.outcome in (Outcome.PASS, Outcome.SLOW):
        actual_source = handle._message
    elif handle.outcome is Outcome.FAIL:
        actual_source = handle.last_mismatch_payload
    else:
        actual_source = None

    actual, actual_truncated = cap_payload(actual_source, stats)
    expected, expected_truncated = cap_payload(handle.matcher_expected, stats)

    if handle.outcome is Outcome.FAIL:
        reason = handle.last_mismatch_reason or ""
    else:
        reason = handle.reason

    # Structured replacement for `reason` — populated for FAIL with the most
    # recent near-miss; null for other outcomes. Step 5 deletes `reason`.
    failures = [serialise_match_failure(f, stats) for f in handle.failures]
    failure_summary: dict[str, Any] | None = (
        failures[-1] if handle.outcome is Outcome.FAIL and failures else None
    )

    out: dict[str, Any] = {
        "topic": handle.topic,
        "outcome": outcome,
        "latency_ms": handle._latency_ms,
        "budget_ms": handle._budget_ms,
        "matcher_description": handle.matcher_description,
        "expected": expected,
        "actual": actual,
        "attempts": handle.attempts,
        "reason": reason,
        "truncated": actual_truncated or expected_truncated,
        "failure": failure_summary,
        "failures": failures,
        "failures_dropped": handle.failures_dropped,
        "diagnosis": _derive_diagnosis(handle),
    }
    # PRD-012 §2.3, §1.5.1: Stage handles carry `transport` and a
    # hash-redacted `correlation_id`. Single-Harness handles omit
    # both keys (the scenario-level correlation_id stays as-is in the
    # parent scenario serialiser).
    if handle.transport is not None:
        out["transport"] = handle.transport
        if handle.correlation_id is not None:
            out["correlation_id"] = redact_correlation_id(handle.correlation_id)
    return out


REPLIES_CAP = 100


# StageReplyState → schema-string mapping per PRD-012 §1.7.
# The `state` enum in test-report-v1.1.json keeps the v1.0 four values.
# Framework-side StageReplyState.FIRED collapses to "replied"; the
# distinction (which framework path produced it) is preserved
# in-process via the enum, not on the wire.
_STAGE_REPLY_STATE_MAP: dict[StageReplyState, str] = {
    StageReplyState.FIRED: "replied",
    StageReplyState.FIRED_BUILDER_ERROR: "reply_failed",
    StageReplyState.ARMED_NO_MATCH: "armed_no_match",
    StageReplyState.ARMED_MATCHER_MISMATCHED: "armed_matcher_mismatched",
}

_REPLY_REPORT_STATE_MAP: dict[ReplyReportState, str] = {
    ReplyReportState.REPLIED: "replied",
    ReplyReportState.REPLY_FAILED: "reply_failed",
    ReplyReportState.ARMED_NO_MATCH: "armed_no_match",
    ReplyReportState.ARMED_MATCHER_MISMATCHED: "armed_matcher_mismatched",
}


def _serialise_reply_state(state: Any) -> str:
    """Map any reply-state enum value to the schema string.

    PRD-012 §1.7 distinguishes two failure modes:

    * `StageReplyState.ARMED` is a runtime-only state; encountering it
      at serialisation means the freeze-path in the framework's
      `_StageScenarioScope.__aexit__` did not flip it before result
      construction — a programmer error in the framework. Raises
      `AssertionError` so the bug surfaces immediately.
    * `None`, unknown strings, or any other unrecognised value is a
      malformed-input case. Raises `ValueError`.
    """
    if isinstance(state, StageReplyState):
        if state is StageReplyState.ARMED:
            raise AssertionError(
                "StageReplyState.ARMED reached serialisation; "
                "freeze-path bug in choreo.stage._StageScenarioScope"
            )
        return _STAGE_REPLY_STATE_MAP[state]
    if isinstance(state, ReplyReportState):
        return _REPLY_REPORT_STATE_MAP[state]
    raise ValueError(f"unrecognised reply state {state!r}")


def serialise_reply_report(report: ReplyReport | StageReplyReport) -> dict[str, Any]:
    """Render a reply report for the JSON output.

    Matcher descriptions carry literal values (ADR-0017 §Security); the
    serialiser redacts them before emission — the report itself is a log-
    like artefact that leaves the scope and must not carry payload-derived
    matcher literals. The unredacted description remains on the in-memory
    report for post-scope test assertions.

    Stage replies (`StageReplyReport`) carry `trigger_transport` and
    `response_transport`; the framework's `response_topic` is mapped
    onto the existing `reply_topic` JSON key (PRD-012 §1.2.1).
    Single-`Harness` replies omit the transport fields.
    """
    is_stage = isinstance(report, StageReplyReport)
    reply_topic = report.response_topic if is_stage else report.reply_topic  # type: ignore[union-attr]
    out: dict[str, Any] = {
        "trigger_topic": report.trigger_topic,
        "reply_topic": reply_topic,
        "state": _serialise_reply_state(report.state),
        "matcher_description": redact_matcher_description(report.matcher_description),
        "candidate_count": report.candidate_count,
        "match_count": report.match_count,
        "reply_published": report.reply_published,
        "builder_error": report.builder_error,
        # `correlation_overridden` lives on the single-Harness ReplyReport
        # only; StageReplyReport does not carry the field (cross-transport
        # bridge stamping is the equivalent concern). Default to False
        # for Stage replies so the schema's required field is populated.
        "correlation_overridden": getattr(report, "correlation_overridden", False),
    }
    if is_stage:
        out["trigger_transport"] = report.trigger_transport  # type: ignore[union-attr]
        out["response_transport"] = report.response_transport  # type: ignore[union-attr]
    return out


_LOG = logging.getLogger("choreo_reporter.serialise")


def serialise_scenario(
    result: ScenarioResult | StageScenarioResult,
    *,
    duration_ms: float,
    completed_normally: bool,
    stats: RedactionStats,
) -> dict[str, Any]:
    """Dispatch on `result.kind` (PRD-012 §2.1) to single-Harness vs
    Stage emission.

    Single-Harness scenarios produce the v1.0 shape (byte-identical
    aside from `schema_version`). Stage scenarios add the optional
    `stage` block plus per-handle `transport` and redacted
    `correlation_id` (handled by `serialise_handle`).

    Defence in depth (PRD-012 §2.1 §2nd-to-last paragraph): the
    Stage-emission branch is wrapped in `try/except`. On unexpected
    failure (e.g. a hostile result whose `by_transport` is a property
    that raises), the reporter falls through to single-Harness
    emission and logs a structured WARNING `stage_emission_failed` so
    the failure surfaces in CI logs without truncating the report.
    """
    if result.kind == "stage":
        try:
            return _serialise_stage_scenario(
                result,
                duration_ms=duration_ms,
                completed_normally=completed_normally,
                stats=stats,
            )
        except Exception as exc:
            _LOG.warning(
                "stage_emission_failed",
                extra={
                    "event": "stage_emission_failed",
                    "exc_type": type(exc).__name__,
                },
                exc_info=True,
            )
            # Fall through to single-Harness emission. The result type
            # is StageScenarioResult; it shares `handles`, `passed`,
            # `replies` and (post-PRD-012) `correlation_id` with the
            # single-Harness shape, so the fallback emits the partial
            # data without a stage block.
            return _serialise_stage_result_as_fallback(
                result,
                duration_ms=duration_ms,
                completed_normally=completed_normally,
                stats=stats,
            )
    return _serialise_single_harness_scenario(
        result,
        duration_ms=duration_ms,
        completed_normally=completed_normally,
        stats=stats,
    )


def _serialise_stage_result_as_fallback(
    result: StageScenarioResult,
    *,
    duration_ms: float,
    completed_normally: bool,
    stats: RedactionStats,
) -> dict[str, Any]:
    """Defensive fallback when `_serialise_stage_scenario` raises.

    Emits the bare minimum schema-conformant shape (no stage block, no
    per-handle transport, no redacted correlation_id) so the rest of
    the run's report still lands. The structured WARNING already
    surfaced the actual failure; this keeps the artefact writable.
    """
    handles = tuple(result.handles[:HANDLES_CAP])
    replies = tuple(result.replies[:REPLIES_CAP])
    return {
        "name": result.name or "stage",
        "correlation_id": result.correlation_id,
        "outcome": derive_scenario_outcome(handles),
        "duration_ms": duration_ms,
        "completed_normally": completed_normally,
        "handles": [serialise_handle(h, stats) for h in handles],
        "timeline": [],
        "timeline_dropped": 0,
        "replies": [serialise_reply_report(r) for r in replies],
        "summary_text": "",
    }


def _serialise_single_harness_scenario(
    result: ScenarioResult,
    *,
    duration_ms: float,
    completed_normally: bool,
    stats: RedactionStats,
) -> dict[str, Any]:
    handles = tuple(result.handles[:HANDLES_CAP])
    timeline = tuple(result.timeline[:TIMELINE_CAP])
    replies = tuple(result.replies[:REPLIES_CAP])
    return {
        "name": result.name,
        "correlation_id": result.correlation_id,
        "outcome": derive_scenario_outcome(handles),
        "duration_ms": duration_ms,
        "completed_normally": completed_normally,
        "handles": [serialise_handle(h, stats) for h in handles],
        "timeline": [serialise_timeline_entry(e) for e in timeline],
        "timeline_dropped": result.timeline_dropped,
        "replies": [serialise_reply_report(r) for r in replies],
        "summary_text": cap_stream(result.failure_summary()),
    }


def _serialise_stage_scenario(
    result: StageScenarioResult,
    *,
    duration_ms: float,
    completed_normally: bool,
    stats: RedactionStats,
) -> dict[str, Any]:
    """Serialise a Stage scenario.

    Adds the `stage` block (bridge_class, registered transports,
    redacted per-transport correlation ids). The handle list emits
    `transport` + redacted `correlation_id` per handle. PRD-012 §1.4,
    §1.5.1.

    Naming: at the report boundary the per-transport wire ids are
    serialised as `correlation_ids` (consistent with
    `Handle.correlation_id`); the framework-internal "wire id"
    terminology is preserved for the bridge protocol's `to_wire`/
    `from_wire`.
    """
    handles = tuple(result.handles[:HANDLES_CAP])
    replies = tuple(result.replies[:REPLIES_CAP])
    by_transport = result.by_transport

    # PRD-012 §1.4 + the user-feedback fix: emit the FULL set of
    # transports registered on the Stage (sorted alphabetically), not
    # just the subset that produced handles. The QA reading the report
    # should see the configured shape, not the executed subset.
    registered = result.registered_transports
    if registered:
        transports_sorted = sorted(registered)
    else:
        # Defensive fallback for older Stage results that pre-date the
        # `registered_transports` field; current emission always sets it.
        transports_sorted = sorted(by_transport.keys())

    # Per-transport correlation ids — one entry per registered transport,
    # hash-redacted via choreo.redaction.redact_correlation_id. Sourced
    # from the first Stage handle observed on that transport (every
    # Stage handle on a given transport carries the same per-transport
    # id in `correlation_id`).
    correlation_ids: dict[str, str] = {}
    for transport in transports_sorted:
        handles_for_transport = by_transport.get(transport)
        if not handles_for_transport:
            continue
        first_handle = handles_for_transport[0]
        if first_handle.correlation_id is not None:
            correlation_ids[transport] = redact_correlation_id(
                first_handle.correlation_id
            )

    # PRD-012 §1.4: `bridge_class` is populated by the Stage scope at
    # result construction (advisory-tier audit field, regex-validated
    # in the schema). The fallback to "Unknown" handles older Stage
    # results that pre-date the field; new emission always carries
    # the actual class name.
    bridge_class = result.bridge_class or "Unknown"

    stage_block = {
        "bridge_class": bridge_class,
        "transports": transports_sorted,
        "correlation_ids": correlation_ids,
    }

    out = {
        # Use the scope name passed to `stage.scenario("name")` rather
        # than the v2 placeholder "stage". Empty string fallback for
        # older results that pre-date the `name` field.
        "name": result.name or "stage",
        "correlation_id": result.correlation_id,
        "outcome": derive_scenario_outcome(handles),
        "duration_ms": duration_ms,
        "completed_normally": completed_normally,
        "handles": [serialise_handle(h, stats) for h in handles],
        "timeline": [],
        "timeline_dropped": 0,
        "replies": [serialise_reply_report(r) for r in replies],
        "summary_text": "",
        "stage": stage_block,
    }
    return out
