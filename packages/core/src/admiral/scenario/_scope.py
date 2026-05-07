"""DSL surface — `Scenario`, `ReplyChain`, and `_ScenarioScope`.

ADR-0012 specified four state classes; the implementation was reduced to a
single `Scenario` with a `_state` flag that raises `AttributeError` on illegal
calls — the same guarantee at runtime without sacrificing the
`handle = s.expect(...)` pattern from PRD-002. ADR-0012's "Notes" records the
correction.

ADR-0014 (Handle) and ADR-0015 (deadline via `asyncio.timeout_at`) are also
implemented here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from admiral._redact import redact_matcher_description
from admiral.correlation import CorrelationPolicyError
from admiral.matchers import Matcher

from ._context import (
    _STATE_BUILDER,
    _STATE_EXPECTING,
    _STATE_TRIGGERED,
    _ScenarioContext,
)
from ._dispatch import _await_all, _policy_write, _register_expectation, _register_reply
from ._handle import Handle
from ._outcome import Outcome
from ._reply import (
    ReplyAlreadyBoundError,
    ReplyReportState,
    _freeze_reply_reports,
    _ReplyState,
)
from ._result import ScenarioResult
from ._timeline import TimelineAction

if TYPE_CHECKING:
    from admiral.harness import Harness

_LOG = logging.getLogger("admiral.scenario")


class Scenario:
    """The per-scenario object yielded by `async with harness.scenario(name)`.

    State transitions:
        builder → expecting (on first expect)
        expecting → triggered (on publish)
        triggered → (await_all returns ScenarioResult)

    `publish` and `await_all` raise `AttributeError` when called in the wrong
    state (ADR-0012). `expect` returns a Handle (ADR-0014); the scenario is
    mutated in place so subsequent `s.publish(...)` works without reassignment.
    """

    def __init__(self, context: _ScenarioContext) -> None:
        self._context = context
        self._state = _STATE_BUILDER

    @property
    def correlation_id(self) -> str | None:
        """The scope's correlation id, or `None` under `NoCorrelationPolicy`.

        Callers stamping correlation manually into a schema the policy does
        not understand should handle the `None` case explicitly: under a
        no-op policy there is no id to stamp.
        """
        return self._context.correlation_id

    # ---- expect ----------------------------------------------------------

    def expect(self, topic: str, matcher: Matcher) -> Handle:
        if self._state == _STATE_TRIGGERED:
            raise AttributeError(
                "'Scenario' in 'triggered' state has no attribute 'expect' — "
                "register expectations before publish() (ADR-0012)"
            )
        handle = _register_expectation(self._context, topic, matcher)
        if self._state == _STATE_BUILDER:
            self._state = _STATE_EXPECTING
        return handle

    # ---- on / reply registration (ADR-0016) ------------------------------

    def on(self, topic: str, matcher: Matcher | None = None) -> ReplyChain:
        """Register a reply: observe `topic` and publish a response.

        Returns a `ReplyChain` terminated by `.publish(reply_topic, payload)`.
        Callable from BUILDER or EXPECTING state; raises AttributeError from
        TRIGGERED state (replies must be armed before the trigger fires —
        ADR-0016). `matcher=None` matches every inbound on the topic.
        """
        if self._state == _STATE_TRIGGERED:
            raise AttributeError(
                "'Scenario' in 'triggered' state has no attribute 'on' — "
                "register replies before publish() (ADR-0016)"
            )
        if self._state == _STATE_BUILDER:
            self._state = _STATE_EXPECTING
        return ReplyChain(self, topic, matcher)

    # ---- publish ---------------------------------------------------------

    @property
    def publish(self):
        if self._state == _STATE_BUILDER:
            raise AttributeError(
                "'Scenario' in 'builder' state has no attribute 'publish' — "
                "register at least one expectation first (ADR-0012)"
            )
        return self._do_publish

    def _do_publish(self, topic: str, payload: bytes | dict[str, Any]) -> Scenario:
        """Publish a payload on `topic` and advance the scenario to TRIGGERED.

        Accepts either raw `bytes` (passed through verbatim, the caller owns
        encoding) or a `dict` (encoded via the harness codec). The active
        `CorrelationPolicy` (ADR-0019) decides what happens to dict payloads:
        under the library default `NoCorrelationPolicy` the payload reaches
        the wire unchanged; under `DictFieldPolicy` the policy stamps its
        configured field (unless the caller already set it). A policy with a
        configured `prefix` raises `CorrelationIdNotInNamespaceError` on an
        explicit override that does not match the prefix.

        Timing: the PUBLISHED event is recorded via the transport's `on_sent`
        hook at the post-wire moment (when bytes have actually left). For
        synchronous transports this fires before `publish()` returns; for
        NATS it fires when the underlying task completes `nc.publish()`.
        If the publish call itself raises (allowlist refusal, encoder error,
        correlation-id refusal), NO PUBLISHED entry is recorded and the
        scope does not advance to TRIGGERED — the caller sees the exception
        and the state is unchanged.
        """
        from admiral.correlation import Envelope

        harness = self._context.harness
        scope_corr = self._context.correlation_id
        if scope_corr is not None:
            envelope = _policy_write(
                harness.correlation, Envelope(topic=topic, payload=payload), scope_corr
            )
            payload = envelope.payload
        timeline = self._context.timeline
        loop = asyncio.get_running_loop()

        def _record_published() -> None:
            now_t = loop.time()
            timeline.anchor(now_t)
            timeline.record(
                now=now_t,
                topic=topic,
                action=TimelineAction.PUBLISHED,
            )

        self._context.harness.publish(topic, payload, on_sent=_record_published)
        self._state = _STATE_TRIGGERED
        return self

    # ---- await_all -------------------------------------------------------

    @property
    def await_all(self):
        if self._state != _STATE_TRIGGERED:
            raise AttributeError(
                f"'Scenario' in {self._state!r} state has no attribute 'await_all' — "
                "call publish() first (ADR-0012)"
            )
        return self._do_await_all

    async def _do_await_all(self, *, timeout_ms: int) -> ScenarioResult:
        result = await _await_all(self._context, timeout_ms=timeout_ms)
        self._context.emitted = True
        from admiral._reporting import _emit

        _emit(result, completed_normally=True)
        return result


class ReplyChain:
    """Transient object returned by `Scenario.on(...)`.

    Terminated by `.publish(reply_topic, payload)`, which registers the
    reply on the scope and returns the live `Scenario`. Single-use —
    calling `publish()` twice raises `ReplyAlreadyBoundError` (ADR-0016).
    """

    def __init__(
        self,
        scenario: Scenario,
        trigger_topic: str,
        matcher: Matcher | None,
    ) -> None:
        self._scenario = scenario
        self._trigger_topic = trigger_topic
        self._matcher = matcher
        self._bound = False

    def publish(
        self,
        reply_topic: str,
        payload: bytes | dict[str, Any] | Callable[[Any], bytes | dict[str, Any]],
    ) -> Scenario:
        if self._bound:
            raise ReplyAlreadyBoundError(
                "this reply chain already has a payload bound; chains are "
                "single-use (ADR-0016). Register a second reply via a "
                "fresh .on() call."
            )
        self._bound = True
        _register_reply(
            self._scenario._context,
            trigger_topic=self._trigger_topic,
            matcher=self._matcher,
            reply_topic=reply_topic,
            reply_spec=payload,
        )
        return self._scenario


class _ScenarioScope:
    def __init__(self, harness: Harness, name: str) -> None:
        self._harness = harness
        # Correlation id is generated in __aenter__ because policies may
        # resolve ids asynchronously (ADR-0019). Placeholder until then.
        self._context = _ScenarioContext(
            name=name,
            harness=harness,
            correlation_id=None,
        )

    async def __aenter__(self) -> Scenario:
        # Generate the scope's correlation id via the policy. Under
        # NoCorrelationPolicy this stays None and every inbound message
        # fans out (broadcast fallback).
        policy = self._harness.correlation
        try:
            self._context.correlation_id = await policy.new_id()
        except Exception as exc:
            raise CorrelationPolicyError(
                policy_class=type(policy).__name__, method="new_id", original=exc
            ) from exc

        # Anchor the timeline at scope entry so every event has a
        # deterministic origin regardless of whether the first thing
        # the test does is `expect`, `on`, or `publish`. Without this,
        # a reply-only scope anchors on the first received message, so
        # that message shows `offset_ms = 0` and the waterfall has no
        # visible propagation span on the first hop.
        loop = asyncio.get_running_loop()
        self._context.timeline.anchor(loop.time())
        return Scenario(self._context)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        # Reply never-fired WARNINGs before teardown, so the log line
        # includes the topic + redacted matcher for diagnosis (ADR-0017
        # §Warning-log behaviour). Replied entries are silent here —
        # any over-count is visible on the report. Builder errors have
        # already logged at ERROR from the dispatcher.
        for r in self._context.replies:
            if r.state is _ReplyState.ARMED:
                terminal = (
                    ReplyReportState.ARMED_NO_MATCH
                    if r.candidate_count == 0
                    else ReplyReportState.ARMED_MATCHER_MISMATCHED
                )
                _LOG.warning(
                    "reply never sent: trigger=%r reply=%r matcher=%s state=%s candidates=%d",
                    r.trigger_topic,
                    r.reply_topic,
                    redact_matcher_description(r.matcher_description),
                    terminal.value,
                    r.candidate_count,
                )

        # Unsubscribe every callback the scope registered. A transport that
        # raises here previously left the failure silent, leaking subscribers
        # into subsequent scopes. Log at WARNING (class name only, ADR-0017)
        # and continue — one failing unsubscribe must not abort the rest.
        for topic, cb in self._context.subscriber_refs:
            try:
                self._harness.unsubscribe(topic, cb)
            except Exception as e:
                _LOG.warning(
                    "scope teardown unsubscribe raised %s on topic %r; subscriber may leak",
                    type(e).__name__,
                    topic,
                )

        # ------ Emit decision matrix -----------------------------------
        # emitted=True   exc=None  → normal path, _do_await_all already
        #                             emitted. Nothing to do.
        # emitted=True   exc≠None  → scenario was reported, but the body
        #                             raised during teardown. Log a WARNING
        #                             so the developer sees the signal; do
        #                             not re-emit (the reporter already has
        #                             a final result for this scope).
        # emitted=False  exc≠None  → body raised before await_all. Emit a
        #                             partial with PENDING handles resolved
        #                             so consumers never see Outcome.PENDING
        #                             in a returned result.
        # emitted=False  exc=None  → body completed but forgot await_all.
        #                             Log a WARNING and emit a partial so
        #                             the reporter records the scope rather
        #                             than silently dropping it.
        # --------------------------------------------------------------
        if self._context.emitted and exc_type is not None:
            _LOG.warning(
                "scope %r raised %s AFTER await_all completed; the primary "
                "result is already reported — fix the teardown path",
                self._context.name,
                exc_type.__name__,
            )
            return

        if not self._context.emitted:
            from admiral._reporting import _emit

            if exc_type is None:
                _LOG.warning(
                    "scope %r exited cleanly without calling await_all(); "
                    "expectations and replies registered but never resolved",
                    self._context.name,
                )

            # Promote any still-PENDING handle to a terminal outcome so the
            # returned result does not carry PENDING into consumer code.
            # TIMEOUT is reused rather than introducing a new Outcome — the
            # reason string names the scope-exit cause, which is the DX signal.
            reason_suffix = (
                f"scope raised {exc_type.__name__} before await_all"
                if exc_type is not None
                else "scope exited without await_all"
            )
            for exp in self._context.expectations:
                if exp.handle.outcome is Outcome.PENDING:
                    exp.handle.outcome = Outcome.TIMEOUT
                    exp.handle._reason = f"aborted: {reason_suffix}"
                    if exp.handle._latency_ms is None:
                        exp.handle._latency_ms = 0.0
                    if not exp.fulfilled.done():
                        exp.fulfilled.cancel()

            handles = tuple(e.handle for e in self._context.expectations)
            partial = ScenarioResult(
                name=self._context.name,
                correlation_id=self._context.correlation_id,
                handles=handles,
                passed=False,
                timeline=tuple(self._context.timeline.entries),
                timeline_dropped=self._context.timeline.dropped,
                replies=_freeze_reply_reports(self._context.replies),
            )
            _emit(partial, completed_normally=False)
