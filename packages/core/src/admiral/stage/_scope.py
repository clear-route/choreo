"""Per-scenario async-with scope (`_StageScenarioScope`) and the
reactive-reply builder (`StageReplyChain`).

The scope owns:
  * the logical scope id (minted via `bridge.fresh()` at __aenter__),
  * one `_StageChild` per registered transport (minted from the logical
    id via `bridge.to_wire(...)`),
  * the per-scope `_Timeline` and its seal-on-exit
    semantics (§2.4),
  * the dispatcher subscriptions registered via `expect()` and `on()`,
    cleaned up via per-pair try/except in `_teardown`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from admiral.correlation import Envelope
from admiral.scenario import Handle, TimelineAction, _Timeline

from ._helpers import (
    _check_distinctness,
    _decode_and_correlation_check,
    _freeze_reply_report,
    _record_event,
    _register_stage_reply,
    _resolve_handle_on_match,
    _resolve_pending_handle,
    _resolve_pending_reply,
)
from ._state import (
    StageScenarioResult,
    _StageChild,
    _StageExpectation,
    _StageReply,
)
from .errors import (
    MissingTransportError,
    StageStateError,
    UnknownTransportError,
)

if TYPE_CHECKING:
    from ._stage import Stage

log = logging.getLogger("admiral.stage")


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
      protocol; eager mint + teardown are mostly sync but the protocol
      contract requires async.

    Future thread-safety: the dispatcher callback registered by
    `expect()` calls `fulfilled.set_result(None)` on a future created
    on the test event loop. The shipped transports either run callbacks
    synchronously on that same loop (MockTransport) or schedule them
    via `loop.call_soon_threadsafe` from the transport's own dispatcher
    thread. A custom transport that delivers from a different thread
    without `call_soon_threadsafe` would race the future resolution.
    """

    def __init__(self, *, name: str, stage: Stage) -> None:
        self._name = name
        self._stage = stage
        self._logical_id: Any | None = None
        self._children: dict[str, _StageChild] = {}
        self._entered = False
        #  §2.2: per-scope event timeline. Anchored on first
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
            # raises, so we must clean up partial state ourselves
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
        unsubscribe is wrapped in its own try/except so a single failure
        (e.g. transport already disconnected, broker dropped) does not
        abort the rest of the loop. Failures surface as a structured
        WARNING (`stage_scope_unsubscribe_failed`) carrying the
        transport and topic on the LogRecord.

        After the loop, the child's `subscriber_refs` is cleared
        regardless — once the unsubscribe attempt has been made
        (whether or not it succeeded), the framework no longer holds
        the reference.

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
        distinctness against the actual logical id — the second pass of
         §Security Considerations' two-pass distinctness check,
        catching bridges that pass startup smoke-test but collide on
        real input.
        """
        for name, wire_id in _check_distinctness(
            transports=self._stage._harnesses,
            call_to_wire=lambda name: self._stage._call_to_wire(self._logical_id, name),
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
                f"transport {on!r} not registered on Stage; known: {sorted(self._stage._harnesses)}"
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
        dataclass-constructor level (no post-hoc mutation). The callback
        registered with the harness decodes inbound bytes via the
        harness's codec, applies the per-scope correlation filter, runs
        the matcher, and resolves the handle on match (PASS) or
        increments `attempts` on near-miss. The scope's `await_all`
        aggregates these handles under a single deadline.

        The codec, correlation, and resolution steps delegate to module
        helpers (`_decode_and_correlation_check` and
        `_resolve_handle_on_match`) so the same prelude is reused by
        `_register_stage_reply` and any future dispatcher callsite.
        Subscribing to the harness happens BEFORE the expectation is
        appended to the child's list, so a subscribe failure does not
        leave an orphan expectation that `await_all` would later block
        on.
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
        ) is a no-op write — payload passes through unchanged
        — so single-transport-per-Stage tests are unaffected. Consumers
        wanting parallel-scope isolation across multiple Stages on
        shared infrastructure configure each harness with a
        `DictFieldPolicy` (or similar) so the wire id round-trips
        through the message and the inbound filter in
        `_decode_and_correlation_check` accepts only own-scope traffic.

        Any harness/transport exception (including
        `RuntimeError("not connected")` from a harness whose transport
        was dropped) propagates to the caller — that is a feature, not
        a bug: the caller wants to know the publish did not happen.

        Sync because every shipped transport's `publish()` is sync (see
        `transports/base.py`). Returns `self` so chains like
        `s.publish(...).publish(...)` read naturally.
        """
        transport = self._require_transport_in_scope(on)
        child = self._children[transport]
        envelope = child.harness.correlation.write(
            Envelope(topic=topic, payload=payload),
            child.wire_id,
        )
        #  §2.3 row 1 (PUBLISHED) + §2.3.1: record at the
        # post-wire `on_sent` boundary so semantics match
        # single-Harness scenario.py:716. If the harness/transport
        # raises before invoking `on_sent`, no PUBLISHED is recorded
        # and the exception propagates — consistent with the "the
        # bytes have left the wire" reading  documents.
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

        child.harness.publish(envelope.topic, envelope.payload, on_sent=_record_published)
        return self

    async def await_all(self, timeout_ms: int) -> StageScenarioResult:
        """Aggregate every per-transport child's expectations into a
        single deadline-bounded wait.

        Behaviour at the deadline mirrors single-transport `_await_all`
        (scenario.py:1181): handles still in PENDING flip to TIMEOUT
        (no message arrived) or FAIL (messages arrived but the matcher
        rejected them, observable via `handle.attempts`).
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
                # Some expectations did not fire within the budget.
                #  §2.3 row 6: record one scope-level DEADLINE
                # event. Both `transport` and `topic` are OMITTED
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
            _resolve_pending_handle(expectation=exp, now_t=now_t, timeout_ms=timeout_ms)

        # Replies live on the TRIGGER child only (per
        # single-writer invariant); response children have empty reply
        # lists by construction.
        all_replies: list[_StageReply] = []
        for child in self._children.values():
            for reply in child.replies:
                _resolve_pending_reply(reply)
                all_replies.append(reply)

        handles = tuple(exp.handle for exp in all_expectations)
        passed = all(h.was_fulfilled() for h in handles) if handles else True
        replies = tuple(_freeze_reply_report(r) for r in all_replies)
        # `_logical_id` is the scope's logical id minted by
        # `bridge.fresh()` at __aenter__. It may be any type the bridge
        # returns; coerce to str for the schema's `["string", "null"]`
        # contract.
        correlation_id: str | None = str(self._logical_id) if self._logical_id is not None else None
        #  §1.4: `bridge_class` is an advisory-tier audit field
        # carrying the consumer-supplied bridge's class name. Read here
        # so the reporter does not have to reach across the private
        # boundary.
        bridge_class = type(self._stage._bridge).__name__
        # The full registered set (in registration order) — not the
        # subset that produced handles. Tests with a path that only
        # fires on one transport still register every harness; the
        # report should show the configured shape, not the executed
        # subset.
        registered_transports = tuple(self._stage._harnesses.keys())
        #  §2.2: freeze the per-scope timeline into the result.
        # Seal BEFORE snapshotting so any in-flight inbound callback
        # (subscriptions stay live until `__aexit__` runs the
        # unsubscribe loop) sees `sealed=True` and becomes a silent
        # no-op. Without this ordering, a late callback's `record()`
        # could see `sealed=False`, append to the deque, and increment
        # `dropped` AFTER the snapshot was taken — producing a
        # consumer-visible inconsistency between `len(result.timeline)`
        # and `result.timeline_dropped`.
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
        # Notify the reporter. Mirrors the
        # single-Harness `Scenario._do_await_all` emission at
        # scenario.py:734-736 so Stage scenarios appear in
        # `results.json`. Wrapped in try/except: a reporter failure
        # must never break a passing test.
        try:
            from .._reporting import _emit

            _emit(result, completed_normally=True)
        except Exception:  # pragma: no cover - defensive
            log.warning(
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

        The `on=` keyword and this method `on` share a name; this is
        acceptable shadowing because the keyword is the public API
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
    on NATS).
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
        followed by further DSL calls.
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
