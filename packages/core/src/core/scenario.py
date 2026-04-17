"""Scenario DSL — Handles, runtime state checks, scope lifecycle, timeouts.

Implements ADR-0014 (Handle) and ADR-0015 (deadline via `asyncio.timeout_at`).
ADR-0012 specified four state classes; the implementation was reduced to a
single `Scenario` with a `_state` flag that raises `AttributeError` on illegal
calls — the same guarantee at runtime without sacrificing the `handle = s.expect(...)`
pattern from PRD-002. ADR-0012's "Notes" records the correction.
"""
from __future__ import annotations

import asyncio
import logging
import time
import warnings
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, TYPE_CHECKING, TypeAlias

from ._redact import redact_matcher_description
from .matchers import Matcher, MatchFailure


_LOG = logging.getLogger("core.scenario")


# Cap near-miss collection per handle. A misbehaving SUT that floods the
# correlation must not pin unbounded memory in the report payload. Anything
# beyond this is elided with a counter on `_failures_dropped`.
_FAILURES_MAX = 20


# Safety boundary: every outbound dict payload must carry a TEST-prefixed
# correlation_id so production systems can filter test traffic at their edge
# (ADR-0006). Raised when a caller supplies a dict with a correlation_id
# that fails this prefix check — by accident (echoing a trigger payload's
# upstream id) or on purpose. Kept separate from ValueError so consumer
# code can catch it specifically.
class CorrelationIdNotInTestNamespaceError(ValueError):
    """An outbound correlation_id did not start with `TEST-` (ADR-0006)."""


def _assert_outbound_correlation_id(
    payload: dict[str, Any], harness: "Harness", *, where: str
) -> None:
    """Validate that a payload's `correlation_id` is TEST-prefixed.

    The prefix is the production-boundary filter: anything without it is
    indistinguishable from real traffic by the downstream ACL. A scope that
    publishes a non-prefixed id has either echoed a triggering payload from
    an upstream mock or deliberately tried to impersonate prod — both are
    bugs this guard surfaces at publish time rather than on the wire.
    """
    cid = payload.get("correlation_id")
    if cid is None:
        return
    prefix = harness.correlation_prefix()
    if not isinstance(cid, str) or not cid.startswith(prefix):
        raise CorrelationIdNotInTestNamespaceError(
            f"{where}: correlation_id {cid!r} is not in the test namespace "
            f"(expected to start with {prefix!r}). ADR-0006 requires every "
            "test-originated publish to carry the TEST- prefix so downstream "
            "systems can filter it at the boundary."
        )


if TYPE_CHECKING:
    from .harness import Harness


# ---------------------------------------------------------------------------
# Outcome + Handle — ADR-0014, PRD-006
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"
    TIMEOUT = "timeout"
    SLOW = "slow"


# ---------------------------------------------------------------------------
# Timeline — PRD-006
# ---------------------------------------------------------------------------


_TIMELINE_MAX_ENTRIES = 256
_TIMELINE_DETAIL_MAX_CHARS = 120


class TimelineAction(StrEnum):
    PUBLISHED = "published"
    # `RECEIVED` records the moment a subscriber callback saw a message
    # after the correlation filter passed, *before* the matcher ran. The
    # bar from the emitter's PUBLISHED/REPLIED to this RECEIVED is
    # transport propagation (the honest "how long did the wire take");
    # the bar from this RECEIVED to the subscriber's MATCHED/REPLIED is
    # handler work (matcher + builder + publish enqueue).
    RECEIVED = "received"
    MATCHED = "matched"
    # `MISMATCHED` means a message arrived on the expected topic but the
    # matcher's predicate rejected it. It is a test-side mismatch between
    # the received shape and the expected shape, not a reject from the SUT.
    MISMATCHED = "mismatched"
    DEADLINE = "deadline"
    # Reply lifecycle events (PRD-008). `REPLIED` records the post-wire
    # moment for the reply's outbound publish: the builder ran, the
    # transport sent the reply, and the `on_sent` hook fired. `REPLY_FAILED`
    # records the path where the builder or the publish itself raised —
    # the `detail` field carries the exception class name only
    # (ADR-0017 §Security).
    REPLIED = "replied"
    REPLY_FAILED = "reply_failed"


@dataclass(frozen=True)
class TimelineEntry:
    """One observed event in a scenario scope's timeline.

    `offset_ms` is monotonic from the scope's first anchor — the same
    anchor as `Handle.latency_ms`. `wall_clock` is best-effort ISO8601
    for correlation with external logs; never used for budget arithmetic.

    Under the hood the entry stores a unix epoch float
    (`_wall_clock_epoch`) captured via `time.time()` — that's a ~50 ns
    syscall on the hot path. ISO formatting is deferred to the
    `wall_clock` property, so the formatting cost (~3-5 μs per call)
    is only paid when the reporter serialises the entry, not once per
    event on every test run.
    """
    offset_ms: float
    _wall_clock_epoch: float
    topic: str
    action: TimelineAction
    detail: str = ""

    @property
    def wall_clock(self) -> str:
        return datetime.fromtimestamp(
            self._wall_clock_epoch, timezone.utc
        ).isoformat()


@dataclass
class _Timeline:
    """Bounded ring buffer of scope-observed events.

    Capped at `_TIMELINE_MAX_ENTRIES` so a runaway scope cannot pin unbounded
    memory. Overflow drops the oldest entries and increments `dropped`. The
    deque's `maxlen` gives O(1) append + drop, so a flooded scope pays the
    same cost per event whether it's the 10th or the 10,000th.
    """
    t0: float | None = None
    entries: deque[TimelineEntry] = field(
        default_factory=lambda: deque(maxlen=_TIMELINE_MAX_ENTRIES)
    )
    dropped: int = 0

    def anchor(self, now: float) -> None:
        if self.t0 is None:
            self.t0 = now

    def record(
        self,
        *,
        now: float,
        topic: str,
        action: TimelineAction,
        detail: str = "",
    ) -> None:
        if self.t0 is None:
            self.t0 = now
        if len(detail) > _TIMELINE_DETAIL_MAX_CHARS:
            detail = detail[: _TIMELINE_DETAIL_MAX_CHARS - 3] + "..."
        if len(self.entries) == _TIMELINE_MAX_ENTRIES:
            self.dropped += 1
        self.entries.append(
            TimelineEntry(
                offset_ms=(now - self.t0) * 1000,
                _wall_clock_epoch=time.time(),
                topic=topic,
                action=action,
                detail=detail,
            )
        )


@dataclass
class Handle:
    topic: str
    matcher_description: str
    correlation_id: str
    outcome: Outcome = Outcome.PENDING
    _message: Any = None
    _latency_ms: float | None = None
    _reason: str = ""
    _attempts: int = 0
    _last_rejection_reason: str | None = None
    _last_rejection_payload: Any = None
    _failures: list[MatchFailure] = field(default_factory=list)
    _failures_dropped: int = 0
    _budget_ms: float | None = None
    _matcher_expected: Any = None

    def within_ms(self, budget_ms: float) -> Handle:
        """Declare a latency budget for this expectation (PRD-006).

        If the matcher accepts a message but the elapsed time since the
        expectation was registered exceeds `budget_ms`, the outcome is
        `Outcome.SLOW` and the scenario fails. Call this after `expect()`
        and before `publish()`; calling after the handle resolves raises
        `RuntimeError`. Re-calling replaces the prior budget and emits
        a `UserWarning`.
        """
        if not isinstance(budget_ms, (int, float)) or isinstance(budget_ms, bool):
            raise TypeError(
                f"budget_ms must be a number, got {type(budget_ms).__name__}"
            )
        fb = float(budget_ms)
        if fb <= 0 or fb != fb or fb == float("inf"):
            raise ValueError(
                f"budget_ms must be positive and finite, got {budget_ms!r}"
            )
        if self.outcome is not Outcome.PENDING:
            raise RuntimeError(
                "within_ms() called after the handle resolved "
                f"(outcome={self.outcome.value}); declare budgets before publish()"
            )
        if self._budget_ms is not None:
            warnings.warn(
                f"within_ms({fb}) overrides previously-set budget "
                f"{self._budget_ms} on handle for topic {self.topic!r}",
                UserWarning,
                stacklevel=2,
            )
        self._budget_ms = fb
        return self

    def was_fulfilled(self) -> bool:
        return self.outcome is Outcome.PASS

    @property
    def message(self) -> Any:
        if self.outcome is Outcome.PENDING:
            raise RuntimeError(
                "handle accessed before await_all() — outcome is still PENDING"
            )
        return self._message

    @property
    def latency_ms(self) -> float:
        if self._latency_ms is None:
            raise RuntimeError(
                "handle accessed before await_all() — latency not yet measured"
            )
        return self._latency_ms

    @property
    def reason(self) -> str:
        return self._reason

    @property
    def attempts(self) -> int:
        """Count of messages that matched the scope's correlation on this topic
        but failed the matcher. Zero means no message arrived at all;
        non-zero means routing worked but the matcher rejected what came in."""
        return self._attempts

    @property
    def last_rejection_reason(self) -> str | None:
        """Reason from the most recent matcher rejection, or None if no
        attempts occurred. Useful for diagnosing near-miss timeouts."""
        return self._last_rejection_reason

    @property
    def failures(self) -> tuple[MatchFailure, ...]:
        """Every near-miss observed on this handle, oldest-first. Bounded by
        `_FAILURES_MAX`; overflow is counted on `failures_dropped`. Structured
        alternative to `last_rejection_reason` — the report renders these into
        a typed expected-vs-actual diff without parsing any prose."""
        return tuple(self._failures)

    @property
    def failures_dropped(self) -> int:
        """Count of near-misses beyond the cap that were not retained."""
        return self._failures_dropped

    @property
    def last_rejection_payload(self) -> Any:
        """The decoded payload of the most recent matcher rejection, or None
        if no attempts occurred. Consumed by the test report (PRD-007) to
        render the actual-side of the expected-vs-actual diff when the
        handle resolves to FAIL (i.e. messages arrived but none matched)."""
        return self._last_rejection_payload

    @property
    def matcher_expected(self) -> Any:
        """Machine-readable expected shape captured from `matcher.expected_shape()`
        at expectation-registration time, or None if the matcher does not
        expose one. Consumed by the test report (PRD-007) to render the
        expected-side of the expected-vs-actual diff."""
        return self._matcher_expected

    def __repr__(self) -> str:
        return (
            f"<Handle topic={self.topic} "
            f"outcome={self.outcome.value} "
            f"matcher={self.matcher_description!r}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError("Handle is not pickleable — may carry payload data")


# ---------------------------------------------------------------------------
# Reply types — PRD-008, ADR-0016, ADR-0017
# ---------------------------------------------------------------------------


class _ReplyState(StrEnum):
    """Runtime state of a live reply (ADR-0016 §Fire-once enforcement).

    Distinct from the terminal `ReplyReportState` derived at scope exit:
    this tracks what a reply is doing *while it is armed*, the report
    captures how it ended up.
    """
    ARMED = "armed"
    REPLIED = "replied"
    FAILED = "failed"


class ReplyReportState(StrEnum):
    """Terminal state on `ReplyReport`, derived at scope exit (ADR-0017).

    The four states distinguish failure modes that look identical from
    outside the reply: wrong topic (no candidates arrived) vs wrong shape
    (candidates arrived but matcher rejected all).
    """
    ARMED_NO_MATCH = "armed_no_match"
    ARMED_MATCHER_REJECTED = "armed_matcher_rejected"
    REPLIED = "replied"
    REPLY_FAILED = "reply_failed"


class ReplyAlreadyBoundError(RuntimeError):
    """Raised when `ReplyChain.publish()` is called more than once.

    Chains are single-use: one `on()` binds one reply. Multi-hop chains are
    a follow-up (see PRD-008 §Future Considerations).
    """


@dataclass(frozen=True)
class ReplyReport:
    """Per-reply observability record on `ScenarioResult.replies`.

    Carries state, counts, topics and a redacted-for-logging matcher
    description. It does NOT carry the triggering payload or the published
    reply payload — `__repr__` and `summary()` must not leak payload content
    (ADR-0017 §Security Considerations). `builder_error` is the exception
    class name alone (never `str(e)`) so a builder raising with a
    payload-derived message does not leak through the report.
    """
    trigger_topic: str
    matcher_description: str
    reply_topic: str
    state: ReplyReportState
    candidate_count: int
    match_count: int
    reply_published: bool
    builder_error: str | None = None
    correlation_overridden: bool = False

    def __repr__(self) -> str:
        return (
            f"<ReplyReport trigger={self.trigger_topic} "
            f"reply={self.reply_topic} state={self.state.value}>"
        )

    def __reduce__(self) -> Any:
        raise TypeError(
            "ReplyReport is not pickleable: redaction enforced structurally"
        )


@dataclass
class _Reply:
    """Internal record for a live reply registration.

    Mutated by the trigger-topic subscriber as messages arrive. Frozen into
    a `ReplyReport` at scope exit. One instance per `on().publish()` call.
    """
    trigger_topic: str
    matcher: Matcher | None
    reply_topic: str
    reply_spec: Any
    matcher_description: str
    state: _ReplyState = _ReplyState.ARMED
    candidate_count: int = 0
    match_count: int = 0
    reply_published: bool = False
    builder_error: str | None = None
    correlation_overridden: bool = False


# ---------------------------------------------------------------------------
# Internal expectation + result
# ---------------------------------------------------------------------------


@dataclass
class _Expectation:
    handle: Handle
    matcher: Matcher
    registered_at: float
    fulfilled: asyncio.Future[None]


@dataclass
class ScenarioResult:
    name: str
    correlation_id: str
    handles: tuple[Handle, ...]
    passed: bool
    timeline: tuple[TimelineEntry, ...] = ()
    timeline_dropped: int = 0
    replies: tuple[ReplyReport, ...] = ()

    @property
    def failing_handles(self) -> tuple[Handle, ...]:
        return tuple(h for h in self.handles if not h.was_fulfilled())

    def __reduce__(self) -> Any:
        raise TypeError(
            "ScenarioResult is not pickleable — carries Handle and ReplyReport "
            "objects that may hold payload content (ADR-0017)"
        )

    def reply_at(self, trigger_topic: str) -> ReplyReport:
        """Return the reply report for `trigger_topic` (ADR-0017).

        Raises KeyError when no reply with that trigger was registered.
        """
        for r in self.replies:
            if r.trigger_topic == trigger_topic:
                return r
        raise KeyError(trigger_topic)

    def assert_passed(self) -> None:
        """Raise AssertionError with a breakdown of every non-passing expectation.

        Use this in place of `assert result.passed is True`; the error message
        names every failing topic, matcher, outcome, and reason rather than
        just showing `False != True`.
        """
        if self.passed:
            return
        raise AssertionError(self.failure_summary())

    def failure_summary(self) -> str:
        """Multi-line breakdown of failing expectations. Always shows every
        handle so the caller sees the full context.

        The diagnosis text distinguishes between a silent timeout (no message
        arrived on the topic matching the scope's correlation) and a near-miss
        timeout (N messages matched correlation but failed the matcher). The
        two are materially different bugs — routing vs expectation — and the
        error message must make the distinction obvious."""
        failing = self.failing_handles
        total = len(self.handles)
        header = (
            f"scenario {self.name!r} failed — "
            f"{len(failing)} of {total} expectations did not pass"
        )
        lines = [
            header,
            f"correlation: {self.correlation_id}",
            "",
        ]
        for h in self.handles:
            latency = (
                f"{h._latency_ms:.1f}ms" if h._latency_ms is not None else "-"
            )
            lines.append(f"  [{h.outcome.value.upper()}] {h.topic}")
            lines.append(f"      matcher : {h.matcher_description}")
            lines.append(f"      why     : {_diagnose(h)}")
            lines.append(f"      latency : {latency}")
            lines.append("")
        if self.timeline:
            shown = self.timeline[-20:]
            total = len(self.timeline) + self.timeline_dropped
            lines.append(f"Timeline (last {len(shown)} of {total}):")
            if self.timeline_dropped:
                lines.append(
                    f"  ... {self.timeline_dropped} earliest entries dropped "
                    f"(buffer cap)"
                )
            elif len(self.timeline) > len(shown):
                lines.append(
                    f"  ... {len(self.timeline) - len(shown)} earlier entries elided"
                )
            for e in shown:
                suffix = f"  ({e.detail})" if e.detail else ""
                lines.append(
                    f"  {e.offset_ms:7.1f}ms  {e.topic:<24} {e.action.value}{suffix}"
                )
            lines.append("")
        return "\n".join(lines).rstrip()

    def summary(self) -> str:
        """Short single-line-per-handle summary, intended for structured logs."""
        lines = [f"scenario={self.name} passed={self.passed}"]
        for h in self.handles:
            lines.append(
                f"  [{h.outcome.value}] topic={h.topic} "
                f"matcher={h.matcher_description}"
                + (
                    f" latency={h.latency_ms:.1f}ms"
                    if h._latency_ms is not None
                    else ""
                )
            )
        if self.replies:
            lines.append("Replies:")
            for r in self.replies:
                detail = (
                    f" builder={r.builder_error}" if r.builder_error else ""
                )
                override = (
                    " correlation_overridden" if r.correlation_overridden else ""
                )
                lines.append(
                    f"  {r.trigger_topic} -> {r.reply_topic}: {r.state.value} "
                    f"({r.match_count} match / {r.candidate_count} candidate)"
                    f"{detail}{override}"
                )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.failure_summary()


# ---------------------------------------------------------------------------
# Diagnosis text — shared by ScenarioResult.failure_summary()
# ---------------------------------------------------------------------------


def _diagnose(handle: Handle) -> str:
    """One-line human-readable explanation of a handle's outcome.

    Outcome labels carry the primary signal:
      - TIMEOUT → no message arrived on the topic + correlation (routing issue)
      - FAIL    → messages arrived but none satisfied the matcher (expectation issue)
      - PASS    → matched
    """
    if handle.outcome is Outcome.PASS:
        return f"matched: {handle._reason}"
    if handle.outcome is Outcome.TIMEOUT:
        latency = handle._latency_ms or 0
        return (
            f"no matching message arrived on topic "
            f"{handle.topic!r} within {latency:.0f}ms"
        )
    if handle.outcome is Outcome.FAIL:
        plural = "s" if handle._attempts != 1 else ""
        return (
            f"{handle._attempts} message{plural} matched the correlation "
            f"but failed the matcher; latest rejection: "
            f"{handle._last_rejection_reason}"
        )
    if handle.outcome is Outcome.SLOW:
        budget = handle._budget_ms or 0
        latency = handle._latency_ms or 0
        return (
            f"matched in {latency:.1f}ms, budget {budget:.1f}ms "
            f"(exceeded by {latency - budget:.1f}ms)"
        )
    return f"unresolved (outcome={handle.outcome.value})"


# ---------------------------------------------------------------------------
# Scenario — single class with runtime state flag (ADR-0012 Notes)
# ---------------------------------------------------------------------------


_STATE_BUILDER = "builder"
_STATE_EXPECTING = "expecting"
_STATE_TRIGGERED = "triggered"


@dataclass
class _ScenarioContext:
    name: str
    harness: Harness
    correlation_id: str
    expectations: list[_Expectation] = field(default_factory=list)
    replies: list[_Reply] = field(default_factory=list)
    subscriber_refs: list[tuple[str, Any]] = field(default_factory=list)
    timeline: _Timeline = field(default_factory=_Timeline)
    emitted: bool = False  # set True once `_do_await_all` fires the observer


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
    def correlation_id(self) -> str:
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

    def _do_publish(
        self, topic: str, payload: bytes | dict[str, Any]
    ) -> Scenario:
        """Publish a payload on `topic` and advance the scenario to TRIGGERED.

        Accepts either raw `bytes` (passed through verbatim, the caller owns
        encoding) or a `dict` (encoded via the harness codec). When a dict is
        given without a `correlation_id` key, the scope's correlation ID is
        injected automatically so the receive-side filter routes the response
        back to this scope. Pass an explicit `correlation_id` in the dict to
        override — useful for negative tests that need to publish under a
        different scope's identity; the override must still be TEST-prefixed
        (ADR-0006), else a `CorrelationIdNotInTestNamespaceError` is raised.

        Timing: the PUBLISHED event is recorded via the transport's `on_sent`
        hook at the post-wire moment (when bytes have actually left). For
        synchronous transports this fires before `publish()` returns; for
        NATS it fires when the underlying task completes `nc.publish()`.
        If the publish call itself raises (allowlist refusal, encoder error,
        correlation_id refusal), NO PUBLISHED entry is recorded and the
        scope does not advance to TRIGGERED — the caller sees the exception
        and the state is unchanged.
        """
        if isinstance(payload, dict):
            payload = dict(payload)
            payload.setdefault("correlation_id", self._context.correlation_id)
            _assert_outbound_correlation_id(
                payload, self._context.harness, where="Scenario.publish"
            )
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
        from ._reporting import _emit

        _emit(result, completed_normally=True)
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _register_expectation(
    context: _ScenarioContext, topic: str, matcher: Matcher
) -> Handle:
    from .matchers import _expected_shape

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
    codec = context.harness.codec
    timeline = context.timeline

    def on_message(msg_topic: str, raw_payload: bytes) -> None:
        if fulfilled.done():
            return
        # A codec exception here must not abort the transport's dispatch loop
        # — sibling subscribers on the same topic would otherwise be starved
        # of this message. Log at WARNING (class name only — see ADR-0017)
        # and drop this callback's handling of the message.
        try:
            payload = codec.decode(raw_payload)
        except Exception as e:
            _LOG.warning(
                "codec.decode raised %s on topic %r; expect subscriber "
                "ignoring this message",
                type(e).__name__,
                msg_topic,
            )
            return

        # Correlation filter: ignore messages meant for other scopes.
        if isinstance(payload, dict):
            msg_corr = payload.get("correlation_id")
            if msg_corr is not None and msg_corr != scope_corr:
                return

        # RECEIVED timestamp — captured at the top of the callback so the
        # bar from the emitter's PUBLISHED/REPLIED to this RECEIVED is
        # honest transport propagation (no matcher cost mixed in).
        recv_t = loop.time()
        timeline.record(
            now=recv_t,
            topic=msg_topic,
            action=TimelineAction.RECEIVED,
            detail="expect",
        )

        result = matcher.match(payload)
        # MATCHED/MISMATCHED timestamp — captured AFTER matcher returns so
        # the bar from RECEIVED to here is the matcher's evaluation time.
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
                    detail=(
                        f"{result.reason} "
                        f"[SLOW {latency_ms:.1f}ms>{budget:.1f}ms]"
                    ),
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
            # Near-miss: correlation matched but the matcher's predicate
            # did not accept the payload shape. The timeline records this
            # as `mismatched` (user-facing vocabulary in the test report);
            # internally we still track attempts and the last mismatch
            # reason under the `_last_rejection_*` field names for
            # backwards compatibility with any external consumer of
            # `Handle` attributes.
            handle._attempts += 1
            handle._last_rejection_reason = result.reason
            handle._last_rejection_payload = payload
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


# ---------------------------------------------------------------------------
# Replies — registration, dispatch, reporting (PRD-008, ADR-0016/17/18)
# ---------------------------------------------------------------------------


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


def _register_reply(
    context: _ScenarioContext,
    *,
    trigger_topic: str,
    matcher: Matcher | None,
    reply_topic: str,
    reply_spec: Any,
) -> None:
    """Register a reply subscription on the scope's trigger topic.

    Dispatch rules (ADR-0016 §Fire-once enforcement, ADR-0018):
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
    codec = context.harness.codec
    harness = context.harness
    timeline = context.timeline
    # Captured once at registration; the running loop is stable for the
    # lifetime of the scope (pytest-asyncio pins session-scope). Avoids
    # a `asyncio.get_running_loop()` lookup on every routed message.
    loop = asyncio.get_running_loop()

    def on_trigger(msg_topic: str, raw_payload: bytes) -> None:
        # Same codec-decode defence as `on_message`: a bad payload here must
        # not abort the transport's dispatch loop. Sibling subscribers (other
        # replies or expectations on the same topic) must still see the next
        # callback in the fan-out.
        try:
            payload = codec.decode(raw_payload)
        except Exception as e:
            _LOG.warning(
                "codec.decode raised %s on topic %r; reply subscriber "
                "ignoring this message",
                type(e).__name__,
                msg_topic,
            )
            return

        # Correlation filter — identical to `expect` (ADR-0018).
        if isinstance(payload, dict):
            msg_corr = payload.get("correlation_id")
            if msg_corr is not None and msg_corr != scope_corr:
                return

        # RECEIVED timestamp at callback entry, paired on the waterfall
        # with the MATCHED/REPLIED that follows within this callback.
        recv_t = loop.time()
        timeline.record(
            now=recv_t,
            topic=msg_topic,
            action=TimelineAction.RECEIVED,
            detail=f"reply:{reply_topic}",
        )

        # Every routed candidate counts, including post-REPLIED ones.
        reply.candidate_count += 1

        # Fire-once bypass — no matcher, no builder, no reply (ADR-0016).
        if reply.state is not _ReplyState.ARMED:
            return

        if matcher is not None:
            match_result = matcher.match(payload)
            if not match_result.matched:
                return

        # Re-check ARMED under dispatcher's single-threaded guarantee
        # (ADR-0016 §Security Considerations §Fire-once TOCTOU).
        if reply.state is not _ReplyState.ARMED:
            return

        reply.match_count += 1

        # Close the fire-once window BEFORE the builder runs. A callable
        # reply_spec that itself re-enters the dispatcher (synchronously, via
        # a nested `harness.publish`) would otherwise see `ARMED` and fire a
        # second time. Flipping to REPLIED first means re-entrance short-
        # circuits at the "Fire-once bypass" check above. If the builder or
        # publish subsequently raises, the state is downgraded to FAILED.
        reply.state = _ReplyState.REPLIED

        try:
            if callable(reply_spec):
                out = reply_spec(payload)
            elif isinstance(reply_spec, dict):
                out = dict(reply_spec)
            else:
                out = reply_spec  # bytes — pass through
        except Exception as e:
            reply.state = _ReplyState.FAILED
            reply.builder_error = type(e).__name__
            # `error`, not `exception` — the traceback renders `str(e)` which
            # may contain payload-derived content (e.g. KeyError carrying a
            # field value). ADR-0017 restricts report emissions to the class
            # name only; honour that at the log boundary too.
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

        # Correlation stamping + override detection (ADR-0018). When the
        # builder returned a dict, work on a copy so an override setdefault
        # does not mutate a caller-owned or module-level template dict.
        if isinstance(out, dict):
            # Always copy before mutating — callable builders may return a
            # caller-owned dict that must not be stamped in place.
            out = dict(out)
            out.setdefault("correlation_id", scope_corr)
            if out["correlation_id"] != scope_corr:
                reply.correlation_overridden = True
                # The overridden correlation_id is user-controlled and may
                # carry identifiers from an upstream mock; do NOT log the
                # value. The flag on the report is enough for diagnosis,
                # and the full value is available in-memory on the test.
                _LOG.warning(
                    "reply correlation_id overridden: trigger=%r reply=%r "
                    "(outgoing value not logged; see report flag)",
                    trigger_topic,
                    reply_topic,
                )
            # Safety check: the outbound correlation_id MUST still be in the
            # test namespace so downstream systems filter it (ADR-0006).
            try:
                _assert_outbound_correlation_id(
                    out, harness, where="reply"
                )
            except CorrelationIdNotInTestNamespaceError as e:
                reply.state = _ReplyState.FAILED
                reply.builder_error = type(e).__name__
                _LOG.error(
                    "reply refused: non-TEST correlation_id on trigger=%r reply=%r",
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

        # REPLIED is timestamped from the transport's `on_sent` hook — for
        # async transports (NATS) this fires when the bytes have actually
        # left, not when the publish call returned. That's what makes the
        # "propagation latency" bar honest over a real wire.
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
            # Publish raised synchronously (allowlist, encoder, …).
            # Downgrade the state: we never reached the wire.
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


def _derive_reply_state(reply: _Reply) -> ReplyReportState:
    """Map the runtime state + counts onto the terminal report state (ADR-0017)."""
    if reply.state is _ReplyState.FAILED:
        return ReplyReportState.REPLY_FAILED
    if reply.state is _ReplyState.REPLIED:
        return ReplyReportState.REPLIED
    # state is ARMED
    if reply.candidate_count == 0:
        return ReplyReportState.ARMED_NO_MATCH
    return ReplyReportState.ARMED_MATCHER_REJECTED


def _freeze_reply_reports(
    context: _ScenarioContext,
) -> tuple[ReplyReport, ...]:
    return tuple(
        ReplyReport(
            trigger_topic=r.trigger_topic,
            matcher_description=r.matcher_description,
            reply_topic=r.reply_topic,
            state=_derive_reply_state(r),
            candidate_count=r.candidate_count,
            match_count=r.match_count,
            reply_published=r.reply_published,
            builder_error=r.builder_error,
            correlation_overridden=r.correlation_overridden,
        )
        for r in context.replies
    )


async def _await_all(
    context: _ScenarioContext, *, timeout_ms: int
) -> ScenarioResult:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_ms / 1000
    futures = [e.fulfilled for e in context.expectations]

    if futures:
        try:
            async with asyncio.timeout_at(deadline):
                await asyncio.wait(futures, return_when=asyncio.ALL_COMPLETED)
        except TimeoutError:
            pass  # expected when some expectations did not fire

    now_t = loop.time()
    for exp in context.expectations:
        if exp.handle.outcome is not Outcome.PENDING:
            continue
        # Actual wake time, not the declared budget — the scheduler may
        # have returned slightly after `deadline`, and the DEADLINE bar
        # should show that reality rather than claiming we woke exactly
        # on budget.
        exp.handle._latency_ms = max(0.0, (now_t - exp.registered_at) * 1000)
        # Distinguish routing failure (nothing came) from expectation failure
        # (things came but matcher rejected). The label is the primary DX signal.
        if exp.handle._attempts > 0:
            exp.handle.outcome = Outcome.FAIL
            exp.handle._reason = (
                f"{exp.handle._attempts} message(s) matched the correlation "
                f"but failed the matcher within {timeout_ms}ms; "
                f"latest rejection: {exp.handle._last_rejection_reason}"
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
                f"no matching message arrived on topic {exp.handle.topic!r} "
                f"within {timeout_ms}ms"
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
    # Timeline retention: PRD-006 keeps it empty on pass to minimise report
    # noise. PRD-008 extends that — if the scope registered any reply, the
    # timeline carries reply events that are worth surfacing even when the
    # scenario passed (otherwise the HTML reply section has no context).
    keep_timeline = (not passed) or bool(context.replies)
    timeline_entries: tuple[TimelineEntry, ...] = (
        tuple(context.timeline.entries) if keep_timeline else ()
    )
    timeline_dropped = context.timeline.dropped if keep_timeline else 0
    return ScenarioResult(
        name=context.name,
        correlation_id=context.correlation_id,
        handles=handles,
        passed=passed,
        timeline=timeline_entries,
        timeline_dropped=timeline_dropped,
        replies=_freeze_reply_reports(context),
    )


# ---------------------------------------------------------------------------
# Scope — the entry point used by Harness.scenario(name)
# ---------------------------------------------------------------------------


class _ScenarioScope:
    def __init__(self, harness: Harness, name: str) -> None:
        from ._internal import generate_correlation_id

        self._harness = harness
        self._context = _ScenarioContext(
            name=name,
            harness=harness,
            correlation_id=generate_correlation_id(),
        )

    async def __aenter__(self) -> Scenario:
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
                    else ReplyReportState.ARMED_MATCHER_REJECTED
                )
                _LOG.warning(
                    "reply never sent: trigger=%r reply=%r matcher=%s "
                    "state=%s candidates=%d",
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
                    "scope teardown unsubscribe raised %s on topic %r; "
                    "subscriber may leak",
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
            from ._reporting import _emit

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
                replies=_freeze_reply_reports(self._context),
            )
            _emit(partial, completed_normally=False)
