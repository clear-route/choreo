"""Stage-internal constants and shared helpers.

The helpers here centralise behaviour that the scope's DSL surface,
the reply-trigger callback, and `await_all`'s deadline path all share:
codec/correlation prelude, handle resolution, timeline recording,
distinctness-check loop. Single source of truth keeps the
trust-boundary defences and the
 §2.3 timeline contract uniform across call sites.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from admiral.correlation import Envelope
from admiral.redaction import redact_correlation_id
from admiral.scenario import Handle, Outcome, TimelineAction, _Timeline

from ._state import (
    StageReplyReport,
    StageReplyState,
    _StageChild,
    _StageExpectation,
    _StageReply,
)
from .errors import BridgeAmbiguityError

log = logging.getLogger("admiral.stage")


_MAX_WIRE_ID_LEN = 1024
"""Upper bound on `to_wire` return string length.

Defends the dispatcher key space, log lines, and `Handle.correlation_id`
from a bridge with no internal bounds. See  §Security
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


def _redact(s: str, head: int = 8, tail: int = 4) -> str:
    """Truncate a string for safe inclusion in error messages and logs.

    Wire ids may carry consumer-supplied data; the full value goes only
    into structured fields a redaction policy can scrub, not into message
    strings. 
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
    every  hook-point recording site so the call sites stay
    one-liners and the behavioural contract stays in one place.

    `source` is the DSL-surface attribution.
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
    scope's expectations; returns `None` if the message must be ignored
    (decode failure, correlation read failure, or correlation id present
    but not for this scope).

    The two-step prelude (codec.decode → correlation_policy.read) is
    shared by every Stage callback that consumes inbound bytes:
    `_StageScenarioScope.expect()` and `_register_stage_reply`.
    Centralised so the correlation defence ( §Security
    Considerations) is enforced uniformly: a message whose
    `policy.read()` returns a wire id NOT equal to `expected_wire_id`
    is dropped, defending against cross-scope leak when multiple Stages
    share infrastructure.

    A policy returning `None` (e.g. NoCorrelationPolicy, or an unstamped
    message under DictFieldPolicy) falls through to the matcher — this
    is the broadcast fallback  §Implementation documents.

    Diagnostic from_wire path: when an inbound wire id is present but
    does NOT match the scope's, the helper optionally calls
    `bridge.from_wire(msg_corr, transport)` to surface a
    debugger-friendly translation back to a logical scope id. The
    result is logged at DEBUG; any exception from from_wire is caught
    and logged at WARNING (`stage_from_wire_failed`) so a faulty
    diagnostic path cannot poison the dispatch loop.
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
        msg_corr = correlation_policy.read(Envelope(topic=msg_topic, payload=payload))
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
        # Belongs to another scope's correlation; drop. Optionally call
        # bridge.from_wire for a diagnostic translation back to the
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
        #  §2.3 row 3: record CORRELATION_SKIPPED with the wire-id
        # mismatch hash-redacted. Wire ids carry no diagnostic value
        # once they are visibly distinct, so hash redaction costs
        # nothing while preventing archive-grep correlation across
        # tenants. Asymmetric vs MISMATCHED's un-redacted detail because
        # payload values DO have diagnostic value.
        # Do NOT add `wire_id`/`msg_corr` to log extras above —
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

    #  §2.3 row 2: RECEIVED records at the moment a subscriber
    # callback saw a message after the correlation filter passed,
    # BEFORE the matcher ran. The bar from a previous PUBLISHED/REPLIED
    # to this RECEIVED is wire propagation; the bar from this RECEIVED
    # to the subsequent MATCHED/REPLIED is handler work.
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
    correlation filter. On match: resolve the handle (PASS) and complete
    the future. On near-miss: bump `_attempts` and record the mismatch
    reason for diagnostics.

    Mutates `handle` in place. Mirrors the matcher branch in
    `scenario._register_expectation` minus latency-budget evaluation.

     §2.3 rows 4-5: when `timeline` is provided, records
    `MATCHED` on the accept branch and `MISMATCHED` on the reject
    branch, both attributed to `transport`. The MISMATCHED `detail` is
    the matcher's reason (un-redacted per  §Security: payload
    values stay visible in this test tool).
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
    harness's codec/policy + match against the trigger's wire id. On
    match the build callback is invoked with the decoded trigger
    payload; the response is encoded via `ctx_response.harness.codec`,
    correlation-stamped via `ctx_response.harness.correlation` against
    the response child's wire id, and published on
    `ctx_response.harness`.

    Lifecycle (mirrors `scenario._register_reply`):
      1. Every routed message increments `candidate_count`
         unconditionally — including post-FIRED arrivals (observability).
      2. Post-FIRED messages bypass matcher + builder (fire-once
         enforcement).
      3. `matcher=None` auto-passes (no-matcher reply fires on any
         routed candidate).
      4. The fire-once window closes (state ARMED → FIRED) BEFORE the
         build callback runs. Re-entrance through a callable build that
         itself triggers the same callback short-circuits at the
         post-FIRED bypass.
      5. Build/encode/stamp/publish exceptions transition state to
         FIRED_BUILDER_ERROR (terminal) and record `builder_error` as
         the exception class name. The reply does not retry.

    The `_StageReply` record is held only on `ctx_trigger.replies` —
    single-writer  The response context has no record.
    """
    matcher_description = matcher.description if matcher is not None else "(any)"
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
    # `expect()` callbacks use. Replies emit their own WARNINGs on
    # build/publish failure, which already covers the observability need.
    bridge: Any | None = None

    def _on_trigger(msg_topic: str, raw_payload: bytes) -> None:
        # Same decode + correlation prelude as expect()'s callback —
        # single source of truth for the trust-boundary defence and
        # for the codec-failure WARNING shape.
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

        # Fire-once bypass: post-FIRED messages do not re-trigger build
        # or publish. (Also short-circuits FIRED_BUILDER_ERROR.)
        if reply.state is not StageReplyState.ARMED:
            return

        if matcher is not None:
            match_result = matcher.match(payload)
            if not match_result.matched:
                return

        # Re-check ARMED: the dispatcher's single-writer guarantee
        # makes this a defensive read (a callable build cannot
        # synchronously transition the state without going through
        # this code path), but the symmetry with scenario.py:1010 keeps
        # the invariant stated explicitly.
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
            #  §2.3 row 7 + §2.3.1: REPLIED records at the
            # post-wire `on_sent` boundary so semantics match
            # PUBLISHED. The detail carries the trigger topic so a
            # reader can correlate trigger → response across transports.
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

                response_harness.publish(envelope.topic, envelope.payload, on_sent=_record_replied)
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
            #  §2.3 row 8: REPLY_FAILED detail carries the
            # response topic and the exception CLASS NAME ONLY (no
            # `str(exc)`) 
            # Consistent with single-Harness scenario.py:1058.
            _record_event(
                timeline,
                action=TimelineAction.REPLY_FAILED,
                topic=response_topic,
                transport=response_transport,
                detail=f"reply={response_topic} error={type(exc).__name__}",
                source="reply",
            )

    # Subscribe BEFORE recording the subscriber ref (so teardown cleans
    # up if subscribe raised). The reply was already appended to
    # `replies` above; that list holds the lifecycle record, separate
    # from the subscription ref list which holds (topic, callback) for
    # unsubscribe.
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

    Already-terminal states (FIRED, FIRED_BUILDER_ERROR) are untouched.
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
    `matcher` (potentially mutable) are NOT carried into the report by
    design — the report is a redaction-safe snapshot."""
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
    __aenter__). The two passes together implement  §Security
    Considerations' two-pass distinctness defence.
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
