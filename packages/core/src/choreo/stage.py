"""Multi-transport scenario coordinator.

`Stage` wraps a named registry of `Harness` instances and a
`CorrelationBridge` so a single scenario can publish, expect, and reply
across multiple message transports. See ADR-0027 for the design.

This module currently implements only the construction-time surface:

* Bridge protocol and reference implementations (`IdentityBridge`,
  `MappedBridge`).
* Error types (`StageError` mixin plus typed subclasses).
* `Stage.__init__` validation: empty-harness rejection, MappedBridge
  transport-set mismatch detection, startup smoke-test distinctness,
  and `to_wire` return-type/length validation.

Lifecycle (`connect`, `disconnect`), scope, and scenario surfaces land in
follow-up commits as the test plan walks through groups B onwards.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets

_LOG = logging.getLogger("choreo.stage")
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum, StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

from choreo.correlation import Envelope
from choreo.harness import Harness
from choreo.redaction import redact_correlation_id
from choreo.scenario import (
    Handle,
    Outcome,
    TimelineAction,
    TimelineEntry,
    _Timeline,
)

log = logging.getLogger("choreo.stage")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


_MAX_WIRE_ID_LEN = 1024
"""Upper bound on `to_wire` return string length.

Defends the dispatcher key space, log lines, and `Handle.correlation_id`
from a bridge with no internal bounds. See ADR-0027 §Security
Considerations and §Notes for the calibration discussion.
"""


_SMOKE_INPUT = "STAGE-VALIDATION-1234567890abcdef"
"""Synthetic input the Stage uses to exercise the bridge at construction.

Length-stable, type-string. The startup distinctness smoke test
(`Stage._validate_bridge_distinctness`) calls `bridge.to_wire(_SMOKE_INPUT, name)`
for every registered transport and asserts the results are pairwise
distinct.

Test doubles that need to recognise the smoke-test path (so they can pass
startup validation while failing at scope entry) import this constant.
The synthetic is public-by-inspection; the per-scope re-validation pass
in `_StageScenarioScope._mint_all_children` defends against bridges that
detect the synthetic and behave differently for it.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _redact(s: str, head: int = 8, tail: int = 4) -> str:
    """Truncate a string for safe inclusion in error messages and logs.

    Wire ids may carry consumer-supplied data; the full value goes only
    into structured fields a redaction policy can scrub, not into message
    strings. See ADR-0027 §Security Considerations.
    """
    if len(s) <= head + tail + 3:
        return repr(s)
    return f"{s[:head]!r}...{s[-tail:]!r} (len={len(s)})"


def _record_event(
    timeline: _Timeline | None,
    *,
    action: TimelineAction,
    topic: str | None,
    transport: str | None = None,
    detail: str = "",
    logical_topic: str | None = None,
    source: str | None = None,
    now: float | None = None,
) -> None:
    """Record a single timeline event, no-op when `timeline` is None.

    Centralises the `if timeline is not None:` guard, the `loop.time()`
    lookup for the event timestamp, and the kwargs threading. Used by
    every PRD-013 hook-point recording site so the call sites stay
    one-liners and the behavioural contract stays in one place.

    `source` is the DSL-surface attribution (PRD-013 §1.6, schema v1.3).
    Per-site values:
      - `"publish"` for `_StageScenarioScope.publish` (test-side publish)
      - `"reply"` for `_register_stage_reply._on_trigger` events
        (reply-chain trigger arrival + chain's response publish)
      - `"expect"` for `_StageScenarioScope.expect`'s `_on_message`
        events (subscriber-side observations)
      - `"scope"` for `_StageScenarioScope.await_all`'s `DEADLINE`
    """
    if timeline is None:
        return
    when = now if now is not None else asyncio.get_running_loop().time()
    timeline.record(
        now=when,
        topic=topic,
        action=action,
        detail=detail,
        transport=transport,
        logical_topic=logical_topic,
        source=source,
    )


def _decode_and_correlation_check(
    *,
    raw_payload: bytes,
    msg_topic: str,
    transport: str,
    codec: Any,
    correlation_policy: Any,
    expected_wire_id: str,
    bridge: Any | None = None,
    timeline: _Timeline | None = None,
    source: str | None = None,
) -> Any | None:
    """Decode an inbound payload and apply the per-scope correlation filter.

    Returns the decoded payload if it should be matched against this
    scope's expectations; returns `None` if the message must be
    ignored (decode failure, correlation read failure, or correlation
    id present but not for this scope).

    The two-step prelude (codec.decode → correlation_policy.read) is
    shared by every Stage callback that consumes inbound bytes:
    `_StageScenarioScope.expect()` and `_register_stage_reply`.
    Centralised so the correlation defence (ADR-0027 §Security
    Considerations) is enforced uniformly: a message whose
    `policy.read()` returns a wire id NOT equal to `expected_wire_id`
    is dropped, defending against cross-scope leak when multiple
    Stages share infrastructure.

    A policy returning `None` (e.g. NoCorrelationPolicy, or an
    unstamped message under DictFieldPolicy) falls through to the
    matcher — this is the broadcast fallback ADR-0019 §Implementation
    documents.

    Diagnostic from_wire path: when an inbound wire id is present
    but does NOT match the scope's, the helper optionally calls
    `bridge.from_wire(msg_corr, transport)` to surface a
    debugger-friendly translation of the wire id back to a logical
    scope id. The result is logged at DEBUG; any exception from
    from_wire is caught and logged at WARNING
    (`stage_from_wire_failed`) so a faulty diagnostic path cannot
    poison the dispatch loop. The `bridge` argument is optional;
    when omitted, the diagnostic is skipped.
    """
    try:
        payload = codec.decode(raw_payload)
    except Exception as exc:
        log.warning(
            "stage_codec_decode_failed",
            extra={
                "transport": transport,
                "topic": msg_topic,
                "error_class": type(exc).__name__,
            },
        )
        return None

    try:
        msg_corr = correlation_policy.read(
            Envelope(topic=msg_topic, payload=payload)
        )
    except Exception as exc:
        log.warning(
            "stage_correlation_read_failed",
            extra={
                "transport": transport,
                "topic": msg_topic,
                "error_class": type(exc).__name__,
            },
        )
        return None

    if msg_corr is not None and msg_corr != expected_wire_id:
        # Message belongs to another scope's correlation; drop. Optionally
        # call bridge.from_wire for a diagnostic translation back to the
        # logical id; never let the diagnostic itself raise out.
        if bridge is not None:
            try:
                bridge.from_wire(msg_corr, transport)
            except Exception as exc:
                log.warning(
                    "stage_from_wire_failed",
                    extra={
                        "transport": transport,
                        "bridge_class": type(bridge).__name__,
                        "error_class": type(exc).__name__,
                    },
                )
        # PRD-013 §2.3 row 3: record CORRELATION_SKIPPED with the wire-id
        # mismatch hash-redacted (PRD-013 §Security; preserves PRD-012
        # §1.5.1's report-boundary redaction posture). Wire ids carry no
        # diagnostic value once they are visibly distinct, so hash redaction
        # costs nothing while preventing archive-grep correlation across
        # tenants. Asymmetric vs MISMATCHED's un-redacted detail because
        # payload values DO have diagnostic value.
        # NOTE: do NOT add `wire_id`/`msg_corr` to log extras above —
        # the un-redacted id stays inside `bridge.from_wire`'s scope.
        try:
            redacted = redact_correlation_id(msg_corr)
        except Exception as exc:
            # A non-conforming `CorrelationPolicy.read()` could return
            # a non-`str` (the Protocol declares `str | None` but Python
            # does not enforce it at runtime). `.encode("utf-8")` on
            # bytes/int raises AttributeError. Swallow + log so the
            # dispatcher does not crash; the un-redacted id never leaks
            # into the timeline.
            log.warning(
                "stage_correlation_redact_failed",
                extra={
                    "transport": transport,
                    "topic": msg_topic,
                    "error_class": type(exc).__name__,
                },
            )
            redacted = "sha256:<redact-failed>"
        _record_event(
            timeline,
            action=TimelineAction.CORRELATION_SKIPPED,
            topic=msg_topic,
            transport=transport,
            detail=redacted,
            source=source,
        )
        return None

    # PRD-013 §2.3 row 2: RECEIVED is recorded at the moment a subscriber
    # callback saw a message after the correlation filter passed, BEFORE
    # the matcher ran. The bar from a previous PUBLISHED/REPLIED to this
    # RECEIVED is wire propagation; the bar from this RECEIVED to the
    # subsequent MATCHED/REPLIED is handler work.
    _record_event(
        timeline,
        action=TimelineAction.RECEIVED,
        topic=msg_topic,
        transport=transport,
        source=source,
    )
    return payload


def _resolve_handle_on_match(
    *,
    payload: Any,
    matcher: Any,
    handle: Handle,
    fulfilled: asyncio.Future[None],
    loop: asyncio.AbstractEventLoop,
    registered_at: float,
    timeline: _Timeline | None = None,
    transport: str | None = None,
) -> None:
    """Apply the matcher to a payload that has already passed the
    correlation filter. On match: resolve the handle (PASS) and
    complete the future. On near-miss: bump `_attempts` and record the
    mismatch reason for diagnostics.

    Mutates `handle` in place. Mirrors the matcher branch in
    `scenario._register_expectation` minus latency-budget evaluation
    (Group K).

    PRD-013 §2.3 rows 4-5: when `timeline` is provided, the function
    records `MATCHED` on the accept branch and `MISMATCHED` on the
    reject branch, both attributed to `transport`. The MISMATCHED
    `detail` is the matcher's reason (un-redacted per PRD-013
    §Security: payload values stay visible in this test tool).
    """
    recv_t = loop.time()
    result = matcher.match(payload)
    if result.matched:
        handle._message = payload
        handle._latency_ms = (recv_t - registered_at) * 1000
        handle.outcome = Outcome.PASS
        handle._reason = result.reason
        fulfilled.set_result(None)
        _record_event(
            timeline,
            action=TimelineAction.MATCHED,
            topic=handle.topic,
            transport=transport,
            source="expect",
            now=recv_t,
        )
    else:
        handle._attempts += 1
        handle._last_mismatch_reason = result.reason
        handle._last_mismatch_payload = payload
        _record_event(
            timeline,
            action=TimelineAction.MISMATCHED,
            topic=handle.topic,
            transport=transport,
            detail=result.reason,
            source="expect",
            now=recv_t,
        )


def _register_stage_reply(
    *,
    ctx_trigger: _StageChild,
    ctx_response: _StageChild,
    trigger_topic: str,
    trigger_transport: str,
    response_topic: str,
    response_transport: str,
    matcher: Any | None,
    build: Callable[[Any], Any],
    timeline: _Timeline | None = None,
) -> None:
    """Register a reactive reply on the trigger transport, with the
    response routed to the (possibly different) response transport.

    Cross-transport coordination: the trigger callback runs on
    `ctx_trigger`'s harness — decode + correlation-filter via that
    harness's codec/policy + match against the trigger's wire id.
    On match the build callback is invoked with the decoded trigger
    payload; the response is encoded via `ctx_response.harness.codec`,
    correlation-stamped via `ctx_response.harness.correlation` against
    the response child's wire id, and published on
    `ctx_response.harness`.

    Lifecycle (mirrors `scenario._register_reply`):
      1. Every routed message increments `candidate_count`
         unconditionally — including post-FIRED arrivals (observability).
      2. Post-FIRED messages bypass matcher + builder (fire-once
         enforcement, ADR-0016 §Security Considerations).
      3. `matcher=None` auto-passes (no-matcher reply fires on any
         routed candidate).
      4. The fire-once window closes (state ARMED → FIRED) BEFORE the
         build callback runs. Re-entrance through a callable build
         that itself triggers the same callback short-circuits at
         the post-FIRED bypass.
      5. Build/encode/stamp/publish exceptions transition state to
         FIRED_BUILDER_ERROR (terminal) and record `builder_error` as
         the exception class name. The reply does not retry.

    The `_StageReply` record is held only on `ctx_trigger.replies` —
    single-writer per ADR-0016. The response context has no record.
    """
    matcher_description = (
        matcher.description if matcher is not None else "(any)"
    )
    reply = _StageReply(
        trigger_topic=trigger_topic,
        trigger_transport=trigger_transport,
        response_topic=response_topic,
        response_transport=response_transport,
        matcher=matcher,
        matcher_description=matcher_description,
        build=build,
    )
    ctx_trigger.replies.append(reply)

    trigger_codec = ctx_trigger.harness.codec
    trigger_policy = ctx_trigger.harness.correlation
    trigger_wire_id = ctx_trigger.wire_id
    response_harness = ctx_response.harness
    response_policy = ctx_response.harness.correlation
    response_wire_id = ctx_response.wire_id
    # Reply-trigger paths skip the `bridge.from_wire` diagnostic that
    # `expect()` callbacks use (Group M2). Replies emit their own WARNINGs
    # on build/publish failure, which already covers the observability need.
    bridge: Any | None = None

    def _on_trigger(msg_topic: str, raw_payload: bytes) -> None:
        # Use the same decode + correlation prelude as expect()'s
        # callback — single source of truth for the trust-boundary
        # defence and for the codec-failure WARNING shape.
        payload = _decode_and_correlation_check(
            raw_payload=raw_payload,
            msg_topic=msg_topic,
            transport=trigger_transport,
            codec=trigger_codec,
            correlation_policy=trigger_policy,
            expected_wire_id=trigger_wire_id,
            bridge=bridge,
            timeline=timeline,
            source="reply",
        )
        if payload is None:
            return  # decode failure, policy failure, or for another scope

        # Every routed candidate counts. Includes post-FIRED arrivals.
        reply.candidate_count += 1

        # Fire-once bypass: post-FIRED messages do not re-trigger
        # build or publish. (Also short-circuits FIRED_BUILDER_ERROR.)
        if reply.state is not StageReplyState.ARMED:
            return

        if matcher is not None:
            match_result = matcher.match(payload)
            if not match_result.matched:
                return

        # Re-check ARMED: dispatcher's single-writer guarantee makes
        # this a defensive read (a callable build cannot synchronously
        # transition the state without going through this code path),
        # but the symmetry with scenario.py:1010 keeps the invariant
        # stated explicitly.
        if reply.state is not StageReplyState.ARMED:
            return

        reply.match_count += 1

        # Close the fire-once window BEFORE invoking build. A build
        # that re-enters the dispatcher (e.g. via a nested publish)
        # then sees state != ARMED and short-circuits at the bypass
        # above. If anything from here on raises, state downgrades
        # to FIRED_BUILDER_ERROR (terminal) so the reply does not
        # silently double-fire.
        reply.state = StageReplyState.FIRED

        try:
            response_payload = build(payload)
            envelope = response_policy.write(
                Envelope(topic=response_topic, payload=response_payload),
                response_wire_id,
            )
            # PRD-013 §2.3 row 7 + §2.3.1: REPLIED records at the post-wire
            # `on_sent` boundary so semantics match PUBLISHED. The detail
            # carries the trigger topic so a reader can correlate
            # trigger -> response across transports.
            if timeline is not None:
                published_topic = envelope.topic

                def _record_replied() -> None:
                    _record_event(
                        timeline,
                        action=TimelineAction.REPLIED,
                        topic=published_topic,
                        transport=response_transport,
                        detail=f"trigger={trigger_topic}",
                        source="reply",
                    )

                response_harness.publish(
                    envelope.topic, envelope.payload, on_sent=_record_replied
                )
            else:
                response_harness.publish(envelope.topic, envelope.payload)
        except Exception as exc:
            reply.state = StageReplyState.FIRED_BUILDER_ERROR
            reply.builder_error = type(exc).__name__
            log.warning(
                "stage_reply_failed",
                extra={
                    "trigger_topic": trigger_topic,
                    "trigger_transport": trigger_transport,
                    "response_topic": response_topic,
                    "response_transport": response_transport,
                    "error_class": type(exc).__name__,
                },
            )
            # PRD-013 §2.3 row 8: REPLY_FAILED detail carries the response
            # topic and the exception CLASS NAME ONLY (no `str(exc)`) per
            # ADR-0017 §Security Considerations. Consistent with
            # single-Harness scenario.py:1058.
            _record_event(
                timeline,
                action=TimelineAction.REPLY_FAILED,
                topic=response_topic,
                transport=response_transport,
                detail=f"reply={response_topic} error={type(exc).__name__}",
                source="reply",
            )

    # Subscribe BEFORE recording the reply on the trigger child's
    # subscriber_refs (so teardown cleans up if subscribe raised).
    # The reply was already appended to `replies` above; that list
    # holds the lifecycle record, separate from the subscription
    # ref list which holds (topic, callback) for unsubscribe.
    ctx_trigger.harness.subscribe(trigger_topic, _on_trigger)
    ctx_trigger.subscriber_refs.append((trigger_topic, _on_trigger))


def _resolve_pending_reply(reply: _StageReply) -> None:
    """Derive the terminal state of a reply still ARMED at scope exit.

    The two derived states distinguish failure-mode causes:
      * `ARMED_NO_MATCH` — no message reached the trigger callback at
        all. The trigger topic was wrong, or the correlation filter
        rejected every candidate, or the broker dropped before any
        delivery.
      * `ARMED_MATCHER_MISMATCHED` — messages reached the callback
        (`candidate_count > 0`) but the matcher rejected every one
        (`match_count == 0`). The trigger wiring was correct; the
        matcher predicate is the diagnosis.

    Already-terminal states (FIRED, FIRED_BUILDER_ERROR) are
    untouched.
    """
    if reply.state is not StageReplyState.ARMED:
        return
    if reply.candidate_count == 0:
        reply.state = StageReplyState.ARMED_NO_MATCH
    else:
        reply.state = StageReplyState.ARMED_MATCHER_MISMATCHED


def _freeze_reply_report(reply: _StageReply) -> StageReplyReport:
    """Convert a mutable `_StageReply` into a frozen `StageReplyReport`
    suitable for `StageScenarioResult.replies`. Preserves all fields
    visible on the user-facing report; `build` (a Callable) and
    `matcher` (potentially mutable) are NOT carried into the report
    by design — the report is a redaction-safe snapshot."""
    return StageReplyReport(
        trigger_topic=reply.trigger_topic,
        trigger_transport=reply.trigger_transport,
        matcher_description=reply.matcher_description,
        response_topic=reply.response_topic,
        response_transport=reply.response_transport,
        state=reply.state,
        candidate_count=reply.candidate_count,
        match_count=reply.match_count,
        reply_published=reply.state is StageReplyState.FIRED,
        builder_error=reply.builder_error,
    )


def _resolve_pending_handle(
    *,
    expectation: _StageExpectation,
    now_t: float,
    timeout_ms: int,
) -> None:
    """Flip a still-PENDING handle to TIMEOUT or FAIL after the deadline.

    `TIMEOUT` if no message reached the matcher at all
    (`handle._attempts == 0`). `FAIL` if messages arrived but every
    matcher invocation rejected the payload — the latest mismatch
    reason is included in the diagnostic.

    Cancels the future if the loop hasn't already done so. Mutates
    `expectation.handle` in place.
    """
    handle = expectation.handle
    if handle.outcome is not Outcome.PENDING:
        return
    handle._latency_ms = max(0.0, (now_t - expectation.registered_at) * 1000)
    if handle._attempts > 0:
        handle.outcome = Outcome.FAIL
        handle._reason = (
            f"{handle._attempts} message(s) reached the matcher on "
            f"transport {handle.transport!r} but none accepted the "
            f"payload within {timeout_ms}ms; latest mismatch: "
            f"{handle._last_mismatch_reason}"
        )
    else:
        handle.outcome = Outcome.TIMEOUT
        handle._reason = (
            f"no matching message arrived on topic {handle.topic!r} "
            f"(transport {handle.transport!r}) within {timeout_ms}ms"
        )
    if not expectation.fulfilled.done():
        expectation.fulfilled.cancel()


def _check_distinctness(
    *,
    transports: Iterable[str],
    call_to_wire: Callable[[str], str],
    bridge_class_name: str,
    context_label: str,
) -> Iterator[tuple[str, str]]:
    """Iterate transports, call `to_wire` per transport, raise
    `BridgeAmbiguityError` on collision. Yields each `(transport_name,
    wire_id)` pair as the loop progresses.

    Single source of truth for the two distinctness call sites:
    `Stage._validate_bridge_distinctness` (synthetic input, at __init__)
    and `_StageScenarioScope._mint_all_children` (real logical id, at
    __aenter__). The two passes together implement ADR-0027 §Security
    Considerations' two-pass distinctness defence; this helper keeps
    them on one definition.
    """
    seen: dict[str, str] = {}
    for name in transports:
        wire = call_to_wire(name)
        if wire in seen:
            raise BridgeAmbiguityError(
                f"bridge {bridge_class_name} produced wire id "
                f"{_redact(wire)} for both transport {seen[wire]!r} and "
                f"transport {name!r} {context_label}",
                transports=(seen[wire], name),
            )
        seen[wire] = name
        yield (name, wire)


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------
#
# Subclasses inherit from a `StageError` mixin AND a standard taxon
# (LookupError / ValueError / RuntimeError / ExceptionGroup). Consumers can
# `except StageError` for catch-all, or `except LookupError` for typo-style
# transport-name mistakes. See ADR-0027 §Implementation Error types.


class StageError(Exception):
    """Mixin marker for every Stage-emitted exception."""


class StageStateError(StageError, RuntimeError):
    """Stage method called in the wrong lifecycle state.

    Examples: connect() called twice; connect() called after disconnect();
    scenario() called before connect(). The message names the current
    state so the caller can distinguish the cases.
    """


class MissingTransportError(StageError, ValueError):
    """A Stage scenario DSL method was called without an `on=` selector.

    Inherits from `ValueError` (caller-error taxon) so consumers can
    `except ValueError` for typo-and-omission-style mistakes uniformly.
    The framework concept is named so diagnostics do not surface as a
    Python `TypeError` for a missing required keyword.
    """


class UnknownTransportError(StageError, LookupError):
    """A Stage scenario DSL method's `on=` selector named a transport
    not registered on the parent Stage.

    Inherits from `LookupError` (the standard taxon for name-not-found)
    so consumers can `except LookupError` for transport-name typos. The
    error message names the registered transports so the user can spot
    the typo without reading source.
    """


# Transport name regex (PRD-012 §1.4-§1.5, PRD-013 §1.1). Stage validates
# at __init__ to fail closed before consumer-supplied names can flow into
# the timeline / results.json / Phase 2 renderer.
_TRANSPORT_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class InvalidTransportNameError(StageError, ValueError):
    """A transport name registered on `Stage(harnesses=...)` does not
    match the schema regex `^[a-zA-Z0-9_-]{1,64}$` (PRD-012 §1.4-§1.5).

    Stage fails closed at construction so consumer-supplied names
    cannot propagate into the timeline / results.json / Phase 2
    renderer where they would otherwise become an injection surface.
    """


class StageConnectError(StageError, RuntimeError):
    """Stage.connect() aborted on a transport failure.

    The failing transport AND every already-connected sibling were
    disconnected (best-effort) before this exception propagated. The
    Stage stays in NEW state; construct a new Stage to retry.

    Attributes:
        failing_transport: name of the transport whose connect() raised.
        bridge_class: name of the bridge class in effect (logged for audit).
    """

    def __init__(self, *, failing_transport: str, bridge_class: str) -> None:
        super().__init__(
            f"Stage.connect aborted: transport {failing_transport!r} failed "
            f"(bridge: {bridge_class}); the failing transport AND every "
            f"already-connected transport were disconnected"
        )
        self.failing_transport = failing_transport
        self.bridge_class = bridge_class


class StageDisconnectError(StageError, ExceptionGroup):
    """PEP 654 ExceptionGroup for one or more disconnect failures.

    Use with `except*`:

        try:
            await stage.disconnect()
        except* StageDisconnectError as eg:
            for exc in eg.exceptions:
                ...

    Single-failure disconnect produces a group of length 1 — the surface
    is uniform regardless of how many transports raised, so consumer
    error-handling code does not branch on count.
    """

    def __new__(
        cls, message: str, errors: list[BaseException]
    ) -> StageDisconnectError:
        return super().__new__(cls, message, errors)


class BridgeAmbiguityError(StageError, ValueError):
    """Raised at Stage.__init__ (synthetic input) or scope entry (real
    logical id) when the bridge maps two transports to identical wire ids.

    The colliding transport names are on the `.transports` attribute. The
    wire id is redacted in the message string; consumers needing the full
    value must implement a bridge whose __cause__ carries it explicitly.
    """

    def __init__(
        self, message: str, *, transports: tuple[str, ...] = ()
    ) -> None:
        super().__init__(message)
        # Sort so consumers can assert against `.transports` exactly
        # without depending on dict iteration order.
        self.transports = tuple(sorted(transports))


class BridgeTransportMismatchError(StageError, ValueError):
    """Raised at Stage.__init__ when a bridge advertising
    `configured_transports` does not match the registered harness set
    exactly. Surfaces the mismatch as a typed error rather than as a
    generic KeyError-wrapped BridgeTranslationError at first use.
    """

    def __init__(
        self,
        *,
        bridge_class: str,
        bridge_transports: tuple[str, ...],
        registered_transports: tuple[str, ...],
    ) -> None:
        super().__init__(
            f"{bridge_class} configured for transports {bridge_transports} "
            f"but Stage registered {registered_transports}; the sets must "
            f"match"
        )
        self.bridge_class = bridge_class
        self.bridge_transports = bridge_transports
        self.registered_transports = registered_transports


class BridgeTranslationError(StageError, RuntimeError):
    """Wraps any exception raised by bridge.fresh / to_wire / from_wire,
    or a type-validation failure on the bridge's return value.

    Mirrors ADR-0019's `CorrelationPolicyError` shape: bridge_class,
    method, transport, and the original exception are all on named
    attributes. Consumers can write
        except BridgeTranslationError as e: log(e.original)
    without walking the __cause__ chain.
    """

    def __init__(
        self,
        *,
        bridge_class: str,
        method: str,
        transport: str | None,
        original: BaseException,
    ) -> None:
        location = f" for transport {transport!r}" if transport else ""
        # Message names the bridge, method, transport, and the original
        # exception's CLASS only — never its `str()`. Bridge-side
        # exceptions can carry sensitive identifiers (customer keys,
        # tenant ids); consumers wanting the full original use
        # `.original` programmatically. Mirrors the redaction posture
        # `BridgeAmbiguityError` takes for wire ids.
        super().__init__(
            f"{bridge_class}.{method} raised "
            f"{type(original).__name__}{location}; "
            f"see .original for the wrapped exception"
        )
        self.bridge_class = bridge_class
        self.method = method
        self.transport = transport
        self.original = original


# ---------------------------------------------------------------------------
# CorrelationBridge protocol and reference implementations
# ---------------------------------------------------------------------------


@runtime_checkable
class CorrelationBridge(Protocol):
    """Maps a logical scope id to and from per-transport wire ids.

    A bridge is consumer code. The Stage calls `fresh()` once per scope
    and `to_wire(logical, transport)` once per registered transport per
    scope at scope entry. The bridge is NOT invoked on the inbound
    message hot path; wire-level comparison is used there. `from_wire()`
    is invoked only for diagnostics.

    Implementations must:
      * be deterministic on `to_wire` for the duration of a scope
      * make `to_wire` return distinct, non-empty `str` values for
        distinct registered transports given the same logical id
      * keep `to_wire` synchronous and fast (it runs inline on the test
        event loop; a slow implementation stalls every concurrent scope)
      * keep wire id length within `_MAX_WIRE_ID_LEN` (the Stage rejects
        longer values)
      * be stateless across scopes: the bridge instance is shared by
        every scope opened against the Stage
      * make `fresh()` collision-resistant per process

    The shipped `IdentityBridge` and `MappedBridge` honour all the above.
    See ADR-0027 §Security Considerations for the trust-boundary
    discussion.
    """

    async def fresh(self) -> Any: ...

    def to_wire(self, logical: Any, transport: str) -> str: ...

    def from_wire(self, wire: str, transport: str) -> Any | None:
        return None


class IdentityBridge:
    """Bridge for the homogeneous case: every transport sees the same
    wire id.

    NOTE: this bridge is rejected by `Stage.__init__` whenever more than
    one transport is registered, because `to_wire` returns the same
    value for every transport (which trips `BridgeAmbiguityError`). It
    is therefore only useful for framework-internal tests of
    single-transport Stages and parallel-isolation tests where multiple
    Stages each carry one transport. Production code wanting
    per-transport translation must use `MappedBridge` or a custom
    implementation.
    """

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        return str(logical)

    def from_wire(self, wire: str, transport: str) -> str:
        return wire


@dataclass(frozen=True)
class _MapEntry:
    forward: Callable[[Any], str]
    inverse: Callable[[str], Any] | None = None


class MappedBridge:
    """Bridge with explicit per-transport forward functions.

    forwards: mapping of transport name -> function that turns a logical
        id into the wire id for that transport. Functions must be
        deterministic, synchronous, and return a non-empty `str`.
    inverses: optional mapping of transport name -> function that turns
        a wire id back into the logical id (diagnostics only). Missing
        inverses are silently treated as "no diagnostic available".

    Example:
        bridge = MappedBridge(
            forwards={
                "nats": lambda logical: str(logical),
                "kafka": lambda logical: f"evt-orders-{logical}",
            },
        )

    Stage validation: if the registered harness set does not match the
    bridge's `configured_transports`, the Stage surfaces a
    `BridgeTransportMismatchError` at `__init__` rather than surfacing
    the underlying KeyError at first use.
    """

    def __init__(
        self,
        forwards: Mapping[str, Callable[[Any], str]],
        inverses: Mapping[str, Callable[[str], Any]] | None = None,
    ) -> None:
        inv = inverses or {}
        self._entries: dict[str, _MapEntry] = {
            name: _MapEntry(forward=forwards[name], inverse=inv.get(name))
            for name in forwards
        }

    @property
    def configured_transports(self) -> frozenset[str]:
        """Public view used by Stage to detect transport-set mismatches
        before any to_wire() call runs."""
        return frozenset(self._entries)

    async def fresh(self) -> str:
        return secrets.token_hex(16)

    def to_wire(self, logical: Any, transport: str) -> str:
        entry = self._entries[transport]
        return str(entry.forward(logical))

    def from_wire(self, wire: str, transport: str) -> Any | None:
        entry = self._entries.get(transport)
        if entry is None or entry.inverse is None:
            return None
        return entry.inverse(wire)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


class _StageState(Enum):
    NEW = "new"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


class StageReplyState(StrEnum):
    """Lifecycle state of a Stage scope's reactive reply.

    Mirrors `scenario.ReplyReportState` but adapted for the
    cross-transport case. Terminal values appear in
    `StageReplyReport.state` after `await_all`; ARMED is runtime-only
    and a final ARMED state is impossible (terminal-state derivation
    in `_resolve_pending_reply` flips ARMED to one of the three
    derived states at scope exit).
    """

    ARMED = "armed"
    """Runtime: registered, no trigger has fired."""

    FIRED = "fired"
    """Terminal: trigger arrived, build succeeded, response published."""

    FIRED_BUILDER_ERROR = "fired_builder_error"
    """Terminal: trigger arrived AND fire-once committed, but the
    build callback raised OR the response publish raised. Mutually
    exclusive with FIRED — once set the reply does not retry."""

    ARMED_NO_MATCH = "armed_no_match"
    """Terminal (derived at scope exit): no message ever reached the
    trigger callback. Distinct from ARMED_MATCHER_MISMATCHED below."""

    ARMED_MATCHER_MISMATCHED = "armed_matcher_mismatched"
    """Terminal (derived at scope exit): messages reached the trigger
    callback (`candidate_count > 0`) but the matcher rejected every
    one (`match_count == 0`). The trigger topic was wired correctly;
    the matcher is the reason no reply fired."""


@dataclass
class _StageReply:
    """Internal record for a live reactive reply registration.

    Mutated by the trigger-topic subscriber as messages arrive;
    frozen into a `StageReplyReport` at scope exit. Held on the
    TRIGGER child's `replies` list (single-writer per ADR-0016 §
    Fire-once enforcement); the response child has no record.
    """

    trigger_topic: str
    trigger_transport: str
    response_topic: str
    response_transport: str
    matcher: Any | None
    matcher_description: str
    build: Callable[[Any], Any]
    state: StageReplyState = StageReplyState.ARMED
    candidate_count: int = 0
    match_count: int = 0
    builder_error: str | None = None
    """Class name only — never `str(exc)`. Builder/publish exceptions
    can carry payload-derived data; the redacted name is enough for
    diagnosis (mirrors ADR-0017 §Security Considerations posture)."""


@dataclass(frozen=True)
class StageReplyReport:
    """Per-reply observability record on `StageScenarioResult.replies`.

    Carries lifecycle state, counts, topics, transports, and a
    redacted-for-logging matcher description. It does NOT carry the
    triggering payload or the response payload — `__repr__` and any
    summary path must not leak payload content.
    """

    trigger_topic: str
    trigger_transport: str
    matcher_description: str
    response_topic: str
    response_transport: str
    state: StageReplyState
    candidate_count: int
    match_count: int
    reply_published: bool
    builder_error: str | None = None

    def __repr__(self) -> str:
        return (
            f"<StageReplyReport trigger={self.trigger_topic} "
            f"@{self.trigger_transport} → {self.response_topic} "
            f"@{self.response_transport} state={self.state.value}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError(
            "StageReplyReport is not pickleable: redaction enforced structurally"
        )


@dataclass
class _StageExpectation:
    """A pending expectation registered via `_StageScenarioScope.expect()`.

    Holds the handle (whose outcome the dispatcher resolves), the matcher
    (applied to inbound payloads), the future (set when the matcher
    accepts a message; awaited by `await_all()`), and the registration
    timestamp (for latency measurement on resolve).

    Mirrors `scenario._Expectation`'s shape but lives in the Stage
    namespace because Stage's correlation handling and codec routing
    flow through the per-child harness, not through a single
    Scenario-level context.
    """

    handle: Handle
    matcher: Any
    fulfilled: asyncio.Future[None]
    registered_at: float


@dataclass
class _StageChild:
    """Per-transport state held by a `_StageScenarioScope`.

    Fields populated incrementally as the test plan walks each group:

    * `wire_id` (Group E) — the per-transport wire id minted from the
      scope's single logical id. This is the value the bridge's
      `to_wire(logical, transport)` returns; at the report boundary
      it is serialised as `correlation_id` (consistent with
      `Handle.correlation_id` naming). Wire-id terminology is
      preserved here because it matches the bridge protocol's
      `to_wire`/`from_wire` API.
    * `harness` (Group G) — reference to the per-transport harness so
      `expect`/`publish`/`teardown` route to the right transport without
      re-looking up via the parent stage.
    * `subscriber_refs` (Group G) — `(topic, callback)` pairs registered
      via `harness.subscribe()`; `_StageScenarioScope._teardown` iterates
      these to call `harness.unsubscribe()` per pair, isolated by a
      per-pair try/except so a single failure does not abort the rest.
    * `expectations` (Group H) — pending expectations whose handles
      `await_all` aggregates and whose futures it awaits. Each entry is
      a `_StageExpectation` carrying the handle, matcher, future, and
      registration timestamp.

    Group I will add `replies` for the reactive-reply lifecycle.
    """

    wire_id: str
    harness: Harness
    subscriber_refs: list[tuple[str, Callable[[str, bytes], None]]] = field(
        default_factory=list
    )
    expectations: list[_StageExpectation] = field(default_factory=list)
    replies: list[_StageReply] = field(default_factory=list)


@dataclass(frozen=True)
class StageScenarioResult:
    """Result of `_StageScenarioScope.await_all(...)`.

    Group H ships `handles` (every expectation's resolved handle, in
    registration order across all transports) and `passed` (True iff
    every handle resolved as PASS). Group I adds `replies` (every
    reactive reply's lifecycle report, in registration order).

    `passed` is handles-only by design — replies are observed via
    `result.replies` and do not dictate scenario pass/fail. Consumers
    asserting that a reply must have FIRED check the report
    explicitly.

    `assert_passed()` raises `AssertionError` with failing-handle
    diagnostics; reasons are NOT included (may carry payload content).

    Group K extends this with `by_transport` (per-transport view of
    handles).

    PRD-012 (§2.6, §2.8) adds:

    * `correlation_id`: the scope's logical id (`bridge.fresh()`
      output captured at scope entry). The choreo-reporter populates
      `scenario.correlation_id` from this without reaching into
      private scope attributes.
    * `kind`: explicit discriminator (`"stage"`) that the
      choreo-reporter uses for dispatch in place of duck-typed
      hasattr checks.

    The legacy underscored alias `_StageScenarioResult` continues to
    resolve to this class for backward compatibility (PRD-012 §2.8;
    aliased for two minor versions).
    """

    handles: tuple[Handle, ...]
    passed: bool
    replies: tuple[StageReplyReport, ...] = ()
    correlation_id: str | None = None
    name: str = ""
    """The scope name passed to `stage.scenario("name")`. Empty string
    when the scope was opened without a name (current API requires one,
    so this default is defensive). The choreo-reporter uses this as the
    scenario's display name (replacing the v2 placeholder `"stage"`)."""
    bridge_class: str | None = None
    """Class name of the `CorrelationBridge` in effect for the Stage
    scope this result came from. Populated by `_StageScenarioScope.await_all`
    from `type(stage._bridge).__name__` so the choreo-reporter can emit
    the value in `scenario.stage.bridge_class` (PRD-012 §1.4) without
    reaching into private Stage attributes."""
    registered_transports: tuple[str, ...] = ()
    """The transports registered on the Stage at scope open, in
    registration order. Distinct from `by_transport.keys()` which only
    lists transports that produced at least one handle. The reporter
    uses this for the breadcrumb's transports list so the test author
    sees every transport they configured, even if the test path didn't
    fire on one of them."""
    timeline: tuple[TimelineEntry, ...] = ()
    """Observed events recorded during the scope's lifetime, in
    observation order (PRD-013 §2.4). Populated by the framework's
    Stage hook points; each entry's `transport` field carries the
    per-transport attribution for transport-scoped events and is
    omitted (`None`) for scope-level events such as `DEADLINE`
    (PRD-013 §D-3). Empty for scopes that did not exercise any
    instrumented path."""
    timeline_dropped: int = 0
    """Count of events the per-scope `_Timeline` ring buffer had to
    drop because the per-scope cap (256) was hit. Distinct from the
    per-run aggregate cap policed at the reporter boundary
    (PRD-013 §D-4)."""
    kind: Literal["stage"] = field(default="stage", init=False)

    @property
    def by_transport(self) -> Mapping[str, tuple[Handle, ...]]:
        """Per-transport view of `handles`, keyed by `Handle.transport`.

        Returns an immutable mapping. Stage scenarios produce one
        entry per touched transport (e.g. `{"nats": (...,), "kafka":
        (...,)}`); single-Harness scenario results bypass this view
        entirely (their handles' `transport` is `None` so the mapping
        is empty). `result.by_transport.get("any-name")` is None for
        single-Harness results — no surprising empty-string key.
        """
        groups: dict[str, list[Handle]] = {}
        for handle in self.handles:
            transport = handle.transport
            if transport is None:
                continue
            groups.setdefault(transport, []).append(handle)
        return {name: tuple(group) for name, group in groups.items()}

    def assert_passed(self) -> None:
        """Raise AssertionError if any handle did not resolve as PASS.

        The message names each failing handle's topic, transport, and
        outcome — but NOT `handle.reason`. Reasons are constructed
        from matcher feedback that may include payload contents
        (mismatch reasons can interpolate the rejected value); those
        belong on a programmatic access path, not in an exception
        message that lands in CI logs and pytest verbose output.
        Mirrors the redaction posture `BridgeAmbiguityError` and
        `BridgeTranslationError` take. Consumers wanting full reasons
        access them via `handle.reason` directly.
        """
        if self.passed:
            return
        failed = [
            f"  - topic={h.topic!r} transport={h.transport!r} "
            f"outcome={h.outcome.value} (see handle.reason for details)"
            for h in self.handles
            if not h.was_fulfilled()
        ]
        raise AssertionError(
            "Stage scenario did not pass; failing handles:\n"
            + "\n".join(failed)
        )


# Backward-compat alias for the pre-PRD-012 underscored name. In-tree
# imports (`from choreo.stage import _StageScenarioResult`) continue to
# resolve to the public class. Aliased for two minor versions per
# PRD-012 §2.8.
_StageScenarioResult = StageScenarioResult


class Stage:
    """Coordinator for multi-transport scenarios.

    Holds an ordered named registry of Harness instances and a bridge
    that translates a logical scope id into per-transport wire ids.
    Existing Harness behaviour is unchanged; the Stage drives the
    harnesses through their public surface and adds nothing that
    single-transport callers can see.

    State machine:
        NEW -> CONNECTED via connect(); StageConnectError on failure
            (state stays NEW; rolled-back transports are torn down).
        CONNECTED -> DISCONNECTED via disconnect(); idempotent thereafter.
        Re-use is not supported: connect() on a DISCONNECTED stage
        raises StageStateError. Construct a new Stage to reconnect.

    The registered harness set is fixed at construction. The Stage does
    not expose a runtime register/deregister API; consumer code that
    needs a different transport set constructs a new Stage. Internal
    callers (the per-scope guard, the connect/disconnect loop) treat
    `_harnesses` as read-only.

    See ADR-0027 for the full design discussion.
    """

    def __init__(
        self,
        harnesses: Mapping[str, Harness],
        bridge: CorrelationBridge,
    ) -> None:
        if not harnesses:
            raise ValueError("Stage requires at least one harness")
        # Fail closed on transport names that do not match the schema
        # regex BEFORE any further setup. Consumer-supplied names flow
        # into the timeline, results.json, and (Phase 2) the HTML
        # renderer; rejecting at construction keeps the framework
        # boundary the only place this constraint is enforced.
        for name in harnesses:
            if not isinstance(name, str) or not _TRANSPORT_NAME_PATTERN.match(name):
                raise InvalidTransportNameError(
                    f"transport name {name!r} does not match "
                    f"{_TRANSPORT_NAME_PATTERN.pattern}"
                )
        # `dict(...)` preserves insertion order (Python 3.7+) so the
        # registration order downstream rollback / disconnect routines
        # rely on is deterministic.
        self._harnesses: dict[str, Harness] = dict(harnesses)
        self._bridge = bridge
        self._state: _StageState = _StageState.NEW
        self._connected: list[str] = []
        self._validate_bridge_transport_set()
        self._validate_bridge_distinctness()
        log.info(
            "stage_initialised",
            extra={
                "bridge_class": type(self._bridge).__name__,
                "transports": tuple(self._harnesses),
            },
        )

    # -- validation ------------------------------------------------------

    def _validate_bridge_transport_set(self) -> None:
        """If the bridge advertises a `configured_transports` set
        (e.g. MappedBridge), check it matches the registered harnesses
        exactly. Surfaces a typed error rather than a generic KeyError
        at first use.

        The advertised value is coerced to `frozenset` before compare so
        bridges returning a list, tuple, set, or any iterable of names
        work consistently — the contract is "the set of names must
        match", not "the exact container type must be frozenset".
        """
        configured = getattr(self._bridge, "configured_transports", None)
        if configured is None:
            return  # bridge does not advertise its set; skip
        configured_set = frozenset(configured)
        registered = frozenset(self._harnesses)
        if configured_set != registered:
            raise BridgeTransportMismatchError(
                bridge_class=type(self._bridge).__name__,
                bridge_transports=tuple(sorted(configured_set)),
                registered_transports=tuple(sorted(registered)),
            )

    def _validate_bridge_distinctness(self) -> None:
        """Smoke-test the bridge: reject configurations that produce
        the same wire id for distinct transports against a synthetic
        input.

        This is a startup smoke test, NOT a correctness proof. A bridge
        that returns distinct values for the synthetic input but
        collides on real logical ids will pass this check;
        `_StageScenarioScope._mint_all_children` re-runs the same check
        against the actual logical id at scope entry to catch in-flight
        collisions. See ADR-0027 §Security Considerations.
        """
        # Exhaust the generator — we do not retain the (name, wire) pairs
        # at construction; the per-scope mint is the one that uses them.
        for _ in _check_distinctness(
            transports=self._harnesses,
            call_to_wire=lambda name: self._call_to_wire(_SMOKE_INPUT, name),
            bridge_class_name=type(self._bridge).__name__,
            context_label=(
                f"during startup smoke-test (synthetic input length "
                f"{len(_SMOKE_INPUT)}). The bridge must return distinct "
                f"wire ids per transport."
            ),
        ):
            pass

    def _bridge_error(
        self,
        method: str,
        transport: str | None,
        original: BaseException,
    ) -> BridgeTranslationError:
        """Factory for BridgeTranslationError carrying this Stage's
        bridge class. Shared by every bridge call site (`to_wire`,
        `fresh`, and any future `from_wire` diagnostic path) so the
        error shape stays uniform across the trust boundary."""
        return BridgeTranslationError(
            bridge_class=type(self._bridge).__name__,
            method=method,
            transport=transport,
            original=original,
        )

    def _call_to_wire(self, logical: Any, transport: str) -> str:
        """Wrap to_wire: type-check the return, surface failures as
        BridgeTranslationError. Single chokepoint so every call site
        uses the same validation."""
        try:
            wire = self._bridge.to_wire(logical, transport)
        except Exception as exc:
            raise self._bridge_error("to_wire", transport, exc) from exc
        if not isinstance(wire, str) or not wire:
            raise self._bridge_error(
                "to_wire",
                transport,
                TypeError(
                    f"to_wire must return a non-empty str, got "
                    f"{type(wire).__name__}"
                ),
            )
        if len(wire) > _MAX_WIRE_ID_LEN:
            raise self._bridge_error(
                "to_wire",
                transport,
                ValueError(
                    f"to_wire returned a wire id of length {len(wire)}; "
                    f"limit is {_MAX_WIRE_ID_LEN}"
                ),
            )
        return wire

    # -- lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        """Connect every registered harness in registration order.

        State transitions: NEW -> CONNECTED on success. On any harness's
        connect() raising, the Stage rolls back: it disconnects the
        failing transport (its connect may have opened resources before
        raising, see R4) and every already-connected sibling in reverse
        order, swallowing rollback failures into structured WARNING logs.
        The Stage stays in NEW state; the caller must construct a fresh
        Stage to retry.
        """
        if self._state is not _StageState.NEW:
            raise StageStateError(
                f"Stage.connect() requires state {_StageState.NEW.value!r}, "
                f"got {self._state.value!r}; construct a new Stage to reconnect"
            )
        for name, harness in self._harnesses.items():
            try:
                await harness.connect()
            except Exception as exc:
                await self._rollback(
                    connected_so_far=list(self._connected),
                    failing=(name, harness),
                )
                raise StageConnectError(
                    failing_transport=name,
                    bridge_class=type(self._bridge).__name__,
                ) from exc
            self._connected.append(name)
        self._state = _StageState.CONNECTED

    async def _rollback(
        self,
        *,
        connected_so_far: list[str],
        failing: tuple[str, Harness],
    ) -> None:
        """Disconnect the failing harness (force-mode) then every
        already-connected sibling in reverse registration order. Every
        disconnect is wrapped: rollback failures are logged at WARNING
        and never raised. The rollback path itself is total — once
        entered, it always returns.

        State is reset to NEW after rollback so the StageConnectError
        the caller sees corresponds to a stage that is not in any
        in-between state. Re-attempt requires a fresh Stage.

        The failing harness uses `force_disconnect()` so the transport's
        own disconnect runs even though the harness never reached the
        connected state (its connect() raised). Siblings use the regular
        `disconnect()` because they did complete their connect handshake.
        """
        failing_name, failing_harness = failing
        try:
            await failing_harness.force_disconnect()
        except Exception:
            log.warning(
                "stage_rollback_failing_transport_disconnect_failed",
                extra={"transport": failing_name},
                exc_info=True,
            )
        for name in reversed(connected_so_far):
            try:
                await self._harnesses[name].disconnect()
            except Exception:
                log.warning(
                    "stage_rollback_sibling_disconnect_failed",
                    extra={"transport": name},
                    exc_info=True,
                )
        self._connected.clear()
        # State stays at NEW; the caller may not retry this Stage.

    async def disconnect(self) -> None:
        """Disconnect every connected harness in reverse registration order.

        Idempotent: safe to call from a `finally` block whether
        `connect()` succeeded, partially succeeded, or never ran. After
        this call the Stage is in DISCONNECTED state and cannot be
        reconnected.

        Best-effort across all transports: if any harness's disconnect()
        raises, the loop continues. Collected errors are raised as a
        `StageDisconnectError` (PEP 654 ExceptionGroup) so consumers can
        use `except*` to walk individual failures. State is set to
        DISCONNECTED before the raise so the Stage is in a defined
        terminal state regardless of how disconnect ended.
        """
        if self._state is _StageState.DISCONNECTED:
            return
        errors: list[BaseException] = []
        for name in reversed(self._connected):
            try:
                await self._harnesses[name].disconnect()
            except Exception as exc:  # noqa: BLE001 — collected into group
                errors.append(exc)
        self._connected.clear()
        self._state = _StageState.DISCONNECTED
        if errors:
            raise StageDisconnectError(
                "stage disconnect raised on one or more transports", errors
            )

    def scenario(self, name: str) -> _StageScenarioScope:
        """Open a multi-transport scenario scope.

        Stage state is intentionally NOT mutated by opening or closing a
        scope: a Stage stays CONNECTED while N scopes are live. Each
        scope owns its own per-transport children and lifecycle (eager
        mint at __aenter__, isolated teardown at __aexit__). This means
        parallel scenarios on one Stage are supported by construction;
        the Stage does not gate them via state.

        If a future requirement needs "disconnect cannot run while a
        scope is open", that gating belongs in Stage.disconnect (a
        live-scope counter, separate from the lifecycle state machine),
        NOT in a fourth state. Adding a SCOPED state would force serial
        scopes and tangle two orthogonal concerns.

        """
        if self._state is not _StageState.CONNECTED:
            raise StageStateError(
                f"Stage.scenario() requires state "
                f"{_StageState.CONNECTED.value!r}, got {self._state.value!r}"
            )
        return _StageScenarioScope(name=name, stage=self)


# ---------------------------------------------------------------------------
# Scenario scope
# ---------------------------------------------------------------------------


class _StageScenarioScope:
    """async-with scope for a multi-transport scenario.

    Per-transport children are minted EAGERLY at __aenter__: bridge
    translation errors fire at scope entry rather than racing the test
    body, and the bridge's per-transport distinctness is re-validated
    against the actual logical id (defending against bridges that pass
    the Stage's startup smoke test but collide on real input).

    DSL surface async/sync semantics:

    * `expect(topic, matcher, *, on=...) -> Handle` is sync. It
      registers a callback synchronously via `harness.subscribe()` and
      returns the Handle. The callback resolves the Handle later when
      a matching message arrives.
    * `publish(topic, payload, *, on=...) -> _StageScenarioScope` is
      sync. Every shipped transport's `publish()` is sync (see
      `transports/base.py`); the Stage mirrors that.
    * `on(trigger_topic, *, on=...) -> StageReplyChain` is sync.
      Returns a builder; the builder's `.publish()` terminator is also
      sync because the underlying registration mirrors `expect`.
    * `await_all(timeout_ms) -> StageScenarioResult` is async — it
      awaits the per-handle futures under a deadline.
    * `__aenter__` / `__aexit__` are async per the context-manager
      protocol; eager mint + teardown are mostly sync but the
      protocol contract requires async.

    Future thread-safety: the dispatcher callback registered by
    `expect()` calls `fulfilled.set_result(None)` on a future created
    on the test event loop. The shipped transports either run
    callbacks synchronously on that same loop (MockTransport) or
    schedule them via `loop.call_soon_threadsafe` from the transport's
    own dispatcher thread (real transports per Transport Protocol
    §dispatch). The Stage callback assumes the harness/transport
    contract: callbacks already arrive on the test loop. A custom
    transport that delivers from a different thread without
    `call_soon_threadsafe` is violating that contract and would race
    the future resolution.

    Group E ships only the entry / exit / mint / teardown skeleton.
    The DSL methods (`expect`, `publish`, `on`) and per-child
    subscription state arrive with Groups F and G; until then a scope
    body is empty (the user opens and immediately exits).
    """

    def __init__(self, *, name: str, stage: Stage) -> None:
        self._name = name
        self._stage = stage
        self._logical_id: Any | None = None
        # transport name -> per-child state (wire id today; subscriber
        # refs etc. when Group F lands the DSL methods).
        self._children: dict[str, _StageChild] = {}
        self._entered = False
        # PRD-013 §2.2: per-scope event timeline. Anchored on first
        # recorded event (via `_Timeline.record`'s lazy anchoring), so
        # an empty scope does not consume any t0 slot.
        self._timeline = _Timeline()

    async def __aenter__(self) -> _StageScenarioScope:
        if self._entered:
            raise StageStateError(
                "StageScenarioScope is not re-entrant; "
                "construct a fresh scope via stage.scenario(...)"
            )
        self._entered = True
        try:
            self._logical_id = await self._call_fresh()
            self._mint_all_children()
        except Exception:
            # __aexit__ is NOT called by `async with` when __aenter__
            # raises, so we must clean up any partial state ourselves
            # before re-raising. Scope intentionally narrowed from
            # BaseException to Exception now that _teardown can call
            # transport.unsubscribe(): an asyncio.CancelledError or
            # KeyboardInterrupt during cleanup that ran arbitrary
            # transport code could swap the cancel signal for an
            # unsubscribe error and confuse upstream cancellation.
            self._teardown()
            raise
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        self._teardown()

    def _teardown(self) -> None:
        """Discard per-scope state.

        For each per-transport child, iterate the recorded
        `(topic, callback)` pairs and call `harness.unsubscribe()`. Each
        unsubscribe is wrapped in its own try/except so a single
        failure (e.g. transport already disconnected, broker dropped)
        does not abort the rest of the loop. Failures are surfaced as a
        structured WARNING (`stage_scope_unsubscribe_failed`) carrying
        the transport and topic on the LogRecord.

        After the unsubscribe loop runs for a child, the child's
        `subscriber_refs` is cleared regardless — once the unsubscribe
        attempt has been made (whether or not it succeeded), the
        framework no longer holds the reference. Mirrors the
        single-transport scope's isolation pattern (scenario.py:1302).

        Idempotent: safe to call from both `__aenter__`'s except branch
        (when mint failed mid-way; subscriber_refs is empty in that
        case because expect() was never called) AND `__aexit__` (the
        normal case). Python's `async with` semantics ensure only one
        of these fires per scope under normal use.
        """
        for transport_name in list(self._children):
            child = self._children[transport_name]
            for topic, callback in child.subscriber_refs:
                try:
                    child.harness.unsubscribe(topic, callback)
                except Exception:
                    log.warning(
                        "stage_scope_unsubscribe_failed",
                        extra={
                            "transport": transport_name,
                            "topic": topic,
                        },
                        exc_info=True,
                    )
            child.subscriber_refs.clear()
        self._children.clear()

    async def _call_fresh(self) -> Any:
        """Wrap bridge.fresh() so any exception surfaces as a typed
        BridgeTranslationError naming the `fresh` method."""
        try:
            return await self._stage._bridge.fresh()
        except Exception as exc:
            raise self._stage._bridge_error("fresh", None, exc) from exc

    def _mint_all_children(self) -> None:
        """Mint a per-transport wire id for every registered transport
        from the scope's single logical id. Re-validates per-scope
        distinctness against the actual logical id — the second pass
        of ADR-0027 §Security Considerations' two-pass distinctness
        check, catching bridges that pass startup smoke-test but
        collide on real input.
        """
        for name, wire_id in _check_distinctness(
            transports=self._stage._harnesses,
            call_to_wire=lambda name: self._stage._call_to_wire(
                self._logical_id, name
            ),
            bridge_class_name=type(self._stage._bridge).__name__,
            context_label=(
                "for the active logical scope id; the bridge passed startup "
                "validation but collides on real input"
            ),
        ):
            self._children[name] = _StageChild(
                wire_id=wire_id,
                harness=self._stage._harnesses[name],
            )

    # -- DSL surface -----------------------------------------------------
    #
    # Each DSL entry-point routes through `_require_transport_in_scope`
    # so the on=None and on=unknown failure modes surface as typed
    # framework errors (MissingTransportError / UnknownTransportError)
    # rather than Python's generic TypeError or KeyError. The success
    # paths are minimal in Group F (sufficient for the DSL-error tests
    # to assert their negative behaviours and for `Handle.transport` to
    # be asserted at construction); full dispatcher wiring lands in
    # Group G.

    def _require_transport_in_scope(self, on: str | None) -> str:
        """Validate the `on=` selector on every DSL method and return it
        as a non-Optional `str` for downstream type-narrowing. Raises:

        * MissingTransportError if `on` is None (the user omitted the
          selector entirely);
        * UnknownTransportError if `on` names a transport not registered
          on the parent Stage.

        The two cases are distinct framework concepts and use distinct
        typed error classes so consumer error-handling can branch on
        intent rather than parsing message strings.
        """
        if on is None:
            raise MissingTransportError(
                "Stage scenario DSL methods require an `on=` selector "
                "naming the transport; got on=None"
            )
        if on not in self._stage._harnesses:
            raise UnknownTransportError(
                f"transport {on!r} not registered on Stage; "
                f"known: {sorted(self._stage._harnesses)}"
            )
        return on

    def expect(
        self,
        topic: str,
        matcher: Any,
        *,
        on: str | None = None,
    ) -> Handle:
        """Register an expectation on a named transport.

        Returns a `Handle` whose `transport` reflects `on=` at the
        dataclass-constructor level (no post-hoc mutation; F7). The
        callback registered with the harness decodes inbound bytes via
        the harness's codec, applies the per-scope correlation filter,
        runs the matcher, and resolves the handle on match (PASS) or
        increments `attempts` on near-miss. The scope's `await_all`
        aggregates these handles under a single deadline.

        The codec, correlation, and resolution steps delegate to
        module-level helpers (`_decode_and_correlation_check` and
        `_resolve_handle_on_match`) so the same prelude can be reused
        by `_register_reply` (Group I) and any future dispatcher
        callsite. Subscribing to the harness happens BEFORE the
        expectation is appended to the child's list, so a subscribe
        failure does not leave an orphan expectation that `await_all`
        would later block on.
        """
        transport = self._require_transport_in_scope(on)
        child = self._children[transport]
        loop = asyncio.get_running_loop()
        registered_at = loop.time()

        handle = Handle(
            topic=topic,
            matcher_description=matcher.description,
            correlation_id=child.wire_id,
            _transport=transport,
        )
        fulfilled: asyncio.Future[None] = loop.create_future()
        expectation = _StageExpectation(
            handle=handle,
            matcher=matcher,
            fulfilled=fulfilled,
            registered_at=registered_at,
        )

        codec = child.harness.codec
        correlation_policy = child.harness.correlation
        wire_id = child.wire_id
        bridge = self._stage._bridge
        timeline = self._timeline

        def _on_message(msg_topic: str, raw_payload: bytes) -> None:
            if fulfilled.done():
                return
            payload = _decode_and_correlation_check(
                raw_payload=raw_payload,
                msg_topic=msg_topic,
                transport=transport,
                codec=codec,
                correlation_policy=correlation_policy,
                expected_wire_id=wire_id,
                bridge=bridge,
                timeline=timeline,
                source="expect",
            )
            if payload is None:
                return  # decode failure, policy failure, or for another scope
            _resolve_handle_on_match(
                payload=payload,
                matcher=matcher,
                handle=handle,
                fulfilled=fulfilled,
                loop=loop,
                registered_at=registered_at,
                timeline=timeline,
                transport=transport,
            )

        # Subscribe BEFORE appending the expectation: if subscribe
        # raises (e.g. transport already disconnected), we have not
        # left a phantom registration that `await_all` would block on.
        child.harness.subscribe(topic, _on_message)
        child.subscriber_refs.append((topic, _on_message))
        child.expectations.append(expectation)

        return handle

    def publish(
        self,
        topic: str,
        payload: Any,
        *,
        on: str | None = None,
    ) -> _StageScenarioScope:
        """Publish a payload to a named transport.

        Stamps the per-child wire id into the outbound envelope via the
        harness's `CorrelationPolicy.write()` before delegating to
        `harness.publish()`. The default `NoCorrelationPolicy` (per
        ADR-0019) is a no-op write — payload passes through unchanged
        — so single-transport-per-Stage tests are unaffected.
        Consumers wanting parallel-scope isolation across multiple
        Stages on shared infrastructure configure each harness with a
        `DictFieldPolicy` (or similar) so the wire id round-trips
        through the message and the inbound filter in
        `_decode_and_correlation_check` accepts only own-scope traffic.

        Any harness/transport exception (including
        `RuntimeError("not connected")` from a harness whose transport
        was dropped) propagates to the caller — that is a feature, not
        a bug: the caller wants to know the publish did not happen.

        Sync because every shipped transport's `publish()` is sync
        (see `transports/base.py`). Returns `self` so chains like
        `s.publish(...).publish(...)` read naturally; consumer tests
        today do not depend on the return value.
        """
        transport = self._require_transport_in_scope(on)
        child = self._children[transport]
        envelope = child.harness.correlation.write(
            Envelope(topic=topic, payload=payload),
            child.wire_id,
        )
        # PRD-013 §2.3 row 1 (PUBLISHED) + §2.3.1: record at the post-wire
        # `on_sent` boundary so semantics match single-Harness scenario.py:716.
        # If the harness/transport raises before invoking `on_sent`, no
        # PUBLISHED is recorded and the exception propagates — consistent
        # with the "the bytes have left the wire" reading PRD-013 documents.
        timeline = self._timeline
        published_topic = envelope.topic

        def _record_published() -> None:
            _record_event(
                timeline,
                action=TimelineAction.PUBLISHED,
                topic=published_topic,
                transport=transport,
                source="publish",
            )

        child.harness.publish(
            envelope.topic, envelope.payload, on_sent=_record_published
        )
        return self

    async def await_all(self, timeout_ms: int) -> StageScenarioResult:
        """Aggregate every per-transport child's expectations into a
        single deadline-bounded wait.

        Behaviour at the deadline mirrors single-transport
        `_await_all` (scenario.py:1181): handles still in PENDING flip
        to TIMEOUT (no message arrived) or FAIL (messages arrived but
        the matcher rejected them, observable via `handle.attempts`).
        Group H exercises only the TIMEOUT path; Group K extends
        coverage to FAIL/SLOW/PASS shapes.

        Returns a `StageScenarioResult` whose `handles` is every
        expectation's handle in registration order across all
        transports, and whose `passed` is True iff every handle
        resolved as PASS. The result also carries `correlation_id`
        (the scope's logical id) so the choreo-reporter can populate
        `scenario.correlation_id` without reaching into private scope
        attributes (PRD-012 §2.6).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_ms / 1000

        all_expectations: list[_StageExpectation] = []
        for child in self._children.values():
            all_expectations.extend(child.expectations)

        futures = [exp.fulfilled for exp in all_expectations]
        if futures:
            try:
                async with asyncio.timeout_at(deadline):
                    await asyncio.wait(futures, return_when=asyncio.ALL_COMPLETED)
            except TimeoutError:
                # Expected when some expectations did not fire within the
                # budget. PRD-013 §2.3 row 6: record one scope-level
                # DEADLINE event. Both `transport` and `topic` are OMITTED
                # (None) for scope-level events per §D-3 — symmetric
                # treatment, no in-band signalling on either field.
                _record_event(
                    self._timeline,
                    action=TimelineAction.DEADLINE,
                    topic=None,
                    detail=f"timeout_ms={timeout_ms}",
                    source="scope",
                    now=loop.time(),
                )

        now_t = loop.time()
        for exp in all_expectations:
            _resolve_pending_handle(
                expectation=exp, now_t=now_t, timeout_ms=timeout_ms
            )

        # Replies: derive terminal state for any still-ARMED, then
        # freeze into reports. Replies live on the TRIGGER child only
        # (per ADR-0016 single-writer invariant); response children
        # have empty reply lists by construction.
        all_replies: list[_StageReply] = []
        for child in self._children.values():
            for reply in child.replies:
                _resolve_pending_reply(reply)
                all_replies.append(reply)

        handles = tuple(exp.handle for exp in all_expectations)
        passed = all(h.was_fulfilled() for h in handles) if handles else True
        replies = tuple(_freeze_reply_report(r) for r in all_replies)
        # `_logical_id` is the scope's logical id minted by `bridge.fresh()`
        # at __aenter__. It may be any type the bridge returns; coerce to
        # str for the schema's `["string", "null"]` contract (PRD-012 §1.4.1).
        correlation_id: str | None = (
            str(self._logical_id) if self._logical_id is not None else None
        )
        # PRD-012 §1.4: `bridge_class` is an advisory-tier audit field
        # carrying the consumer-supplied bridge's class name. Read from
        # the Stage's `_bridge` here so the reporter does not have to
        # reach across the private boundary.
        bridge_class = type(self._stage._bridge).__name__
        # The full registered set (in registration order) — not the
        # subset that produced handles. Tests with a path that only
        # fires on one transport still register every harness; the
        # report should show the configured shape, not the executed
        # subset.
        registered_transports = tuple(self._stage._harnesses.keys())
        # PRD-013 §2.2: freeze the per-scope timeline into the result.
        # The deque is iterated in observation order (§2.4); the result
        # carries an immutable tuple. `timeline_dropped` exposes the
        # ring-buffer overflow count (§D-4 per-scope cap).
        # Seal BEFORE snapshotting so any in-flight inbound callback
        # (subscriptions stay live until `__aexit__` runs the unsubscribe
        # loop) sees `sealed=True` and becomes a silent no-op. Without
        # this ordering, a late callback's `record()` could see
        # `sealed=False`, append to the deque, and increment `dropped`
        # AFTER the snapshot was taken — producing a consumer-visible
        # inconsistency between `len(result.timeline)` and
        # `result.timeline_dropped`. The closures captured the same
        # `_Timeline` instance, so flipping the seal propagates to every
        # recording site immediately.
        self._timeline.sealed = True
        timeline = tuple(self._timeline.entries)
        timeline_dropped = self._timeline.dropped
        result = StageScenarioResult(
            handles=handles,
            passed=passed,
            replies=replies,
            correlation_id=correlation_id,
            name=self._name,
            bridge_class=bridge_class,
            registered_transports=registered_transports,
            timeline=timeline,
            timeline_dropped=timeline_dropped,
        )
        # Notify the reporter (PRD-007 §2 observer seam). Mirrors the
        # single-Harness `Scenario._do_await_all` emission at
        # scenario.py:734-736 so Stage scenarios appear in `results.json`.
        # Wrapped in try/except: a reporter failure must never break a
        # passing test.
        try:
            from ._reporting import _emit

            _emit(result, completed_normally=True)
        except Exception:  # pragma: no cover - defensive
            _LOG.warning(
                "stage_scope_reporter_emit_failed",
                exc_info=True,
            )
        return result

    def on(
        self,
        trigger_topic: str,
        matcher: Any | None = None,
        *,
        on: str | None = None,
    ) -> StageReplyChain:
        """Begin a reactive reply chain rooted at a trigger arriving on
        a named transport. Returns a `StageReplyChain` to be terminated
        by `.publish(response_topic, on=..., build=...)`.

        Note: the `on=` keyword and this method `on` share a name; this
        is acceptable shadowing because the keyword is the public API
        (consumers write `s.on("topic", on="kafka")`). Internally the
        validated transport name is bound to `trigger_transport` to
        avoid further confusion.
        """
        trigger_transport = self._require_transport_in_scope(on)
        return StageReplyChain(
            scope=self,
            trigger_transport=trigger_transport,
            trigger_topic=trigger_topic,
            matcher=matcher,
        )


class StageReplyChain:
    """Builder returned by `_StageScenarioScope.on()`. Terminate with
    `.publish()` to register a reply.

    The `on=` on the trigger may differ from the `on=` on the response —
    that is the cross-transport bridge case (trigger on Kafka, response
    on NATS). See ADR-0027 §Implementation StageReplyChain.

    Group F scaffold: methods exist with NotImplementedError bodies so
    that the negative-case tests (response transport typo, etc.) red on
    their own assertion. The success path lands later in Group F.
    """

    def __init__(
        self,
        *,
        scope: _StageScenarioScope,
        trigger_transport: str,
        trigger_topic: str,
        matcher: Any | None,
    ) -> None:
        self._scope = scope
        self._trigger_transport = trigger_transport
        self._trigger_topic = trigger_topic
        self._matcher = matcher

    def publish(
        self,
        response_topic: str,
        *,
        on: str | None = None,
        build: Callable[[Any], Any],
    ) -> _StageScenarioScope:
        """Terminate the chain by publishing a reply on the response
        transport. The response transport's `on=` may differ from the
        trigger transport's `on=` (the cross-transport bridge case).

        `build` is mandatory: a reply chain that does not produce a
        response payload is a misconfiguration, not a deferred decision.
        Omitting it raises Python's `TypeError` for a missing required
        keyword — the framework does not wrap this because there is no
        ambiguity (one method, one purpose for the parameter).

        Returns the parent scope so a reply registration can be
        followed by further DSL calls (`expect`, `publish`, more
        `on(...).publish(...)` chains).
        """
        response_transport = self._scope._require_transport_in_scope(on)
        ctx_trigger = self._scope._children[self._trigger_transport]
        ctx_response = self._scope._children[response_transport]
        _register_stage_reply(
            ctx_trigger=ctx_trigger,
            ctx_response=ctx_response,
            trigger_topic=self._trigger_topic,
            trigger_transport=self._trigger_transport,
            response_topic=response_topic,
            response_transport=response_transport,
            matcher=self._matcher,
            build=build,
            timeline=self._scope._timeline,
        )
        return self._scope
