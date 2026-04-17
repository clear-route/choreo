"""Serialisation — PRD-007 §3, §5.

Converts `ScenarioResult` / `Handle` / `TimelineEntry` (defined in `core`)
into JSON-shaped dicts that conform to docs/schemas/test-report-v1.json.

Applies size caps in a single tree walk: string fields >2 KB are replaced
with a truncation marker, and a JSON-encoded per-payload cap of 8 KB
guards against large dicts.

Outcome values are normalised according to the table in PRD-007 §3.
"""

from __future__ import annotations

import json
from typing import Any

from choreo._redact import redact_matcher_description
from choreo.matchers import MatchFailure
from choreo.scenario import (
    Handle,
    Outcome,
    ReplyReport,
    ScenarioResult,
    TimelineEntry,
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

    # Actual is the matched payload for PASS/SLOW, last-rejected for FAIL,
    # null for TIMEOUT / PENDING.
    if handle.outcome in (Outcome.PASS, Outcome.SLOW):
        actual_source = handle._message
    elif handle.outcome is Outcome.FAIL:
        actual_source = handle.last_rejection_payload
    else:
        actual_source = None

    actual, actual_truncated = cap_payload(actual_source, stats)
    expected, expected_truncated = cap_payload(handle.matcher_expected, stats)

    if handle.outcome is Outcome.FAIL:
        reason = handle.last_rejection_reason or ""
    else:
        reason = handle.reason

    # Structured replacement for `reason` — populated for FAIL with the most
    # recent near-miss; null for other outcomes. Step 5 deletes `reason`.
    failures = [serialise_match_failure(f, stats) for f in handle.failures]
    failure_summary: dict[str, Any] | None = (
        failures[-1] if handle.outcome is Outcome.FAIL and failures else None
    )

    return {
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


REPLIES_CAP = 100


def serialise_reply_report(report: ReplyReport) -> dict[str, Any]:
    """Render a reply report for the JSON output.

    Matcher descriptions carry literal values (ADR-0017 §Security); the
    serialiser redacts them before emission — the report itself is a log-
    like artefact that leaves the scope and must not carry payload-derived
    matcher literals. The unredacted description remains on the in-memory
    `ReplyReport` for post-scope test assertions.
    """
    return {
        "trigger_topic": report.trigger_topic,
        "reply_topic": report.reply_topic,
        "state": report.state.value,
        "matcher_description": redact_matcher_description(report.matcher_description),
        "candidate_count": report.candidate_count,
        "match_count": report.match_count,
        "reply_published": report.reply_published,
        "builder_error": report.builder_error,
        "correlation_overridden": report.correlation_overridden,
    }


def serialise_scenario(
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
