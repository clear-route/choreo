"""Subscriber registration, policy wrapping, and await_all (/008/013).

The big function bodies — `_register_expectation`, `_register_reply`,
`_await_all` — live here so the DSL surface in `_scope` stays readable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from admiral.correlation import (
    CorrelationIdNotInNamespaceError,
    CorrelationPolicy,
    CorrelationPolicyError,
    Envelope,
)
from admiral.matchers import Matcher

from ._context import _Expectation, _ScenarioContext
from ._handle import _FAILURES_MAX, Handle
from ._outcome import Outcome
from ._reply import _freeze_reply_reports, _Reply, _ReplyState
from ._result import ScenarioResult
from ._timeline import TimelineAction

_LOG = logging.getLogger("admiral.scenario")


def _policy_write(policy: CorrelationPolicy, envelope: Envelope, correlation_id: str) -> Envelope:
    """Call `policy.write`, wrapping any unexpected exception.

    `CorrelationIdNotInNamespaceError` is a deliberate signal from the
    policy (the caller supplied a mismatching override) and propagates
    unwrapped so existing error-handling can catch it.
    """
    try:
        return policy.write(envelope, correlation_id)
    except CorrelationIdNotInNamespaceError:
        raise
    except Exception as exc:
        raise CorrelationPolicyError(
            policy_class=type(policy).__name__, method="write", original=exc
        ) from exc


def _policy_read(policy: CorrelationPolicy, envelope: Envelope) -> str | None:
    try:
        return policy.read(envelope)
    except Exception as exc:
        raise CorrelationPolicyError(
            policy_class=type(policy).__name__, method="read", original=exc
        ) from exc


def _register_expectation(context: _ScenarioContext, topic: str, matcher: Matcher) -> Handle:
    from admiral.matchers import _expected_shape

    loop = asyncio.get_running_loop()
    now = loop.time()
    context.timeline.anchor(now)
    handle = Handle(
        topic=topic,
        matcher_description=matcher.description,
        correlation_id=context.correlation_id,
        _matcher_expected=_expected_shape(matcher),
    )
    fulfilled: asyncio.Future[None] = loop.create_future()
    exp = _Expectation(
        handle=handle,
        matcher=matcher,
        registered_at=now,
        fulfilled=fulfilled,
    )
    context.expectations.append(exp)

    scope_corr = context.correlation_id
    harness = context.harness
    policy = harness.correlation
    codec = harness.codec
    timeline = context.timeline

    def on_message(msg_topic: str, raw_payload: bytes) -> None:
        if fulfilled.done():
            return




        try:
            payload = codec.decode(raw_payload)
        except Exception as e:
            _LOG.warning(
                "codec.decode raised %s on topic %r; expect subscriber ignoring this message",
                type(e).__name__,
                msg_topic,
            )
            return

        if scope_corr is not None:
            try:
                msg_corr = policy.read(Envelope(topic=msg_topic, payload=payload))
            except Exception as e:
                _LOG.warning(
                    "%s.read raised %s on topic %r; expect subscriber ignoring this message",
                    type(policy).__name__,
                    type(e).__name__,
                    msg_topic,
                )
                return
            if msg_corr is not None and msg_corr != scope_corr:
                return

        recv_t = loop.time()
        timeline.record(
            now=recv_t,
            topic=msg_topic,
            action=TimelineAction.RECEIVED,
            detail="expect",
        )

        result = matcher.match(payload)


        done_t = loop.time()

        if result.matched:
            latency_ms = (done_t - exp.registered_at) * 1000
            handle._message = payload
            handle._latency_ms = latency_ms
            budget = handle._budget_ms
            if budget is not None and latency_ms > budget:
                handle.outcome = Outcome.SLOW
                handle._reason = (
                    f"matched in {latency_ms:.1f}ms, budget {budget:.1f}ms "
                    f"(exceeded by {latency_ms - budget:.1f}ms); "
                    f"matcher: {result.reason}"
                )
                timeline.record(
                    now=done_t,
                    topic=msg_topic,
                    action=TimelineAction.MATCHED,
                    detail=(f"{result.reason} [SLOW {latency_ms:.1f}ms>{budget:.1f}ms]"),
                )
            else:
                handle.outcome = Outcome.PASS
                handle._reason = result.reason
                timeline.record(
                    now=done_t,
                    topic=msg_topic,
                    action=TimelineAction.MATCHED,
                    detail=result.reason,
                )
            fulfilled.set_result(None)
        else:







            handle._attempts += 1
            handle._last_mismatch_reason = result.reason
            handle._last_mismatch_payload = payload
            if result.failure is not None:
                if len(handle._failures) < _FAILURES_MAX:
                    handle._failures.append(result.failure)
                else:
                    handle._failures_dropped += 1
            timeline.record(
                now=done_t,
                topic=msg_topic,
                action=TimelineAction.MISMATCHED,
                detail=result.reason,
            )

    context.harness.subscribe(topic, on_message)
    context.subscriber_refs.append((topic, on_message))
    return handle


def _register_reply(
    context: _ScenarioContext,
    *,
    trigger_topic: str,
    matcher: Matcher | None,
    reply_topic: str,
    reply_spec: Any,
) -> None:
    """Register a reply subscription on the scope's trigger topic.

    Dispatch rules:
      1. Correlation filter identical to `expect`; foreign messages never
         become candidates.
      2. `candidate_count` increments unconditionally for every routed
         message, including post-REPLIED ones (observability).
      3. Post-REPLIED messages bypass matcher + builder (fire-once).
      4. Matcher `None` auto-passes.
      5. Dict replies have `correlation_id` stamped via `setdefault` and the
         outgoing field is compared to the scope's; mismatch → `correlation_overridden`.
      6. Builder exceptions → `FAILED`; reply not published; scenario
         continues.
    """
    matcher_description = matcher.description if matcher is not None else "(any)"
    reply = _Reply(
        trigger_topic=trigger_topic,
        matcher=matcher,
        reply_topic=reply_topic,
        reply_spec=reply_spec,
        matcher_description=matcher_description,
    )
    context.replies.append(reply)

    scope_corr = context.correlation_id
    harness = context.harness
    policy = harness.correlation
    codec = harness.codec
    timeline = context.timeline



    loop = asyncio.get_running_loop()

    def on_trigger(msg_topic: str, raw_payload: bytes) -> None:




        try:
            payload = codec.decode(raw_payload)
        except Exception as e:
            _LOG.warning(
                "codec.decode raised %s on topic %r; reply subscriber ignoring this message",
                type(e).__name__,
                msg_topic,
            )
            return


        if scope_corr is not None:
            try:
                msg_corr = policy.read(Envelope(topic=msg_topic, payload=payload))
            except Exception as e:
                _LOG.warning(
                    "%s.read raised %s on topic %r; reply subscriber ignoring this message",
                    type(policy).__name__,
                    type(e).__name__,
                    msg_topic,
                )
                return
            if msg_corr is not None and msg_corr != scope_corr:
                return



        recv_t = loop.time()
        timeline.record(
            now=recv_t,
            topic=msg_topic,
            action=TimelineAction.RECEIVED,
            detail=f"reply:{reply_topic}",
        )


        reply.candidate_count += 1


        if reply.state is not _ReplyState.ARMED:
            return

        if matcher is not None:
            match_result = matcher.match(payload)
            if not match_result.matched:
                return



        if reply.state is not _ReplyState.ARMED:
            return

        reply.match_count += 1







        reply.state = _ReplyState.REPLIED

        try:
            if callable(reply_spec):
                out = reply_spec(payload)
            elif isinstance(reply_spec, dict):
                out = dict(reply_spec)
            else:
                out = reply_spec
        except Exception as e:
            reply.state = _ReplyState.FAILED
            reply.builder_error = type(e).__name__




            _LOG.error(
                "reply builder raised %s for trigger=%r reply=%r",
                type(e).__name__,
                trigger_topic,
                reply_topic,
            )
            timeline.record(
                now=loop.time(),
                topic=trigger_topic,
                action=TimelineAction.REPLY_FAILED,
                detail=f"reply={reply_topic} error={type(e).__name__}",
            )
            return








        if scope_corr is not None:
            try:
                stamped = _policy_write(
                    policy, Envelope(topic=reply_topic, payload=out), scope_corr
                )
            except (CorrelationIdNotInNamespaceError, CorrelationPolicyError) as e:
                reply.state = _ReplyState.FAILED
                reply.builder_error = type(e).__name__
                _LOG.error(
                    "reply refused: %s on trigger=%r reply=%r",
                    type(e).__name__,
                    trigger_topic,
                    reply_topic,
                )
                timeline.record(
                    now=loop.time(),
                    topic=trigger_topic,
                    action=TimelineAction.REPLY_FAILED,
                    detail=f"reply={reply_topic} error={type(e).__name__}",
                )
                return


            try:
                outgoing = _policy_read(policy, stamped)
            except CorrelationPolicyError as e:
                reply.state = _ReplyState.FAILED
                reply.builder_error = type(e).__name__
                _LOG.error(
                    "reply refused: %s on trigger=%r reply=%r",
                    type(e).__name__,
                    trigger_topic,
                    reply_topic,
                )
                timeline.record(
                    now=loop.time(),
                    topic=trigger_topic,
                    action=TimelineAction.REPLY_FAILED,
                    detail=f"reply={reply_topic} error={type(e).__name__}",
                )
                return
            if outgoing is not None and outgoing != scope_corr:
                reply.correlation_overridden = True



                _LOG.warning(
                    "reply correlation_id overridden: trigger=%r reply=%r "
                    "(outgoing value not logged; see report flag)",
                    trigger_topic,
                    reply_topic,
                )
            out = stamped.payload





        def _on_reply_sent() -> None:
            reply.reply_published = True
            timeline.record(
                now=loop.time(),
                topic=trigger_topic,
                action=TimelineAction.REPLIED,
                detail=f"reply={reply_topic}",
            )

        try:
            harness.publish(reply_topic, out, on_sent=_on_reply_sent)
        except Exception as e:


            reply.state = _ReplyState.FAILED
            reply.builder_error = type(e).__name__
            _LOG.error(
                "reply publish raised %s for trigger=%r reply=%r",
                type(e).__name__,
                trigger_topic,
                reply_topic,
            )
            timeline.record(
                now=loop.time(),
                topic=trigger_topic,
                action=TimelineAction.REPLY_FAILED,
                detail=f"reply={reply_topic} error={type(e).__name__}",
            )
            return

    harness.subscribe(trigger_topic, on_trigger)
    context.subscriber_refs.append((trigger_topic, on_trigger))


async def _await_all(context: _ScenarioContext, *, timeout_ms: int) -> ScenarioResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    futures = [e.fulfilled for e in context.expectations]

    if futures:
        try:
            async with asyncio.timeout_at(deadline):
                await asyncio.wait(futures, return_when=asyncio.ALL_COMPLETED)
        except TimeoutError:
            pass

    now_t = loop.time()
    for exp in context.expectations:
        if exp.handle.outcome is not Outcome.PENDING:
            continue




        exp.handle._latency_ms = max(0.0, (now_t - exp.registered_at) * 1000)


        if exp.handle._attempts > 0:
            exp.handle.outcome = Outcome.FAIL
            exp.handle._reason = (
                f"{exp.handle._attempts} message(s) matched the correlation "
                f"but failed the matcher within {timeout_ms}ms; "
                f"latest mismatch: {exp.handle._last_mismatch_reason}"
            )
            context.timeline.record(
                now=now_t,
                topic=exp.handle.topic,
                action=TimelineAction.DEADLINE,
                detail=f"{exp.handle._attempts} near-miss(es)",
            )
        else:
            exp.handle.outcome = Outcome.TIMEOUT
            exp.handle._reason = (
                f"no matching message arrived on topic {exp.handle.topic!r} within {timeout_ms}ms"
            )
            context.timeline.record(
                now=now_t,
                topic=exp.handle.topic,
                action=TimelineAction.DEADLINE,
                detail="no message",
            )
        if not exp.fulfilled.done():
            exp.fulfilled.cancel()

    handles = tuple(e.handle for e in context.expectations)
    passed = all(h.was_fulfilled() for h in handles) if handles else True








    context.timeline.sealed = True




    keep_timeline = (not passed) or bool(context.replies)
    timeline_entries = tuple(context.timeline.entries) if keep_timeline else ()
    timeline_dropped = context.timeline.dropped if keep_timeline else 0
    return ScenarioResult(
        name=context.name,
        correlation_id=context.correlation_id,
        handles=handles,
        passed=passed,
        timeline=timeline_entries,
        timeline_dropped=timeline_dropped,
        replies=_freeze_reply_reports(context.replies),
    )
