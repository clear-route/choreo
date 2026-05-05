"""Per-scope timeline buffer (PRD-006, PRD-013)."""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

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
    # matcher's predicate did not accept it. It is a test-side mismatch
    # between the received shape and the expected shape, not a signal from
    # the SUT.
    MISMATCHED = "mismatched"
    # `CORRELATION_SKIPPED` records the moment an inbound message was
    # rejected by the per-scope correlation filter (its wire id belongs
    # to another scope). Single-Harness scopes do not record this event
    # by today's path; PRD-013 wires it on the Stage path with
    # transport attribution and a hash-redacted wire-id mismatch in
    # `detail` (PRD-013 §2.3 row 3, §Security).
    CORRELATION_SKIPPED = "correlation_skipped"
    DEADLINE = "deadline"
    # Reply lifecycle events (PRD-008). `REPLIED` records the post-wire
    # moment for the reply's outbound publish: the builder ran, the
    # transport sent the reply, and the `on_sent` hook fired. `REPLY_FAILED`
    # records the path where the builder or the publish itself raised —
    # the `detail` field carries the exception class name only
    # (ADR-0017 §Security).
    REPLIED = "replied"
    REPLY_FAILED = "reply_failed"


@dataclass(frozen=True, slots=True)
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
    topic: str | None
    """Wire topic the event observed. `None` for scope-level events
    (currently only `DEADLINE`) per PRD-013 §D-3; the reporter omits
    the JSON key in that case. Single-`Harness` and per-transport
    Stage events both carry the wire topic the subscriber/publisher
    saw (which, for translating bridges, is post-translation on the
    publisher side and pre-translation on the receiver side; see
    `logical_topic` for the resolved scope-level topic name)."""
    action: TimelineAction
    detail: str = ""
    transport: str | None = None
    """Per-transport attribution for Stage scenarios (PRD-013 §2.1).
    Single-Harness scopes leave this at the default `None`; the reporter
    omits the JSON key entirely in that case (PRD-013 §1.1, preserving
    PRD-012's byte-identity contract). Stage per-transport children
    populate it with the child's transport name (e.g. `"kafka"`, `"nats"`).
    Scope-level Stage events (currently only `DEADLINE`) leave it at
    `None` per PRD-013 §D-3, so the JSON key is also omitted there."""
    logical_topic: str | None = None
    """The scope-level (pre-bridge-translation) topic name where it
    differs from the wire topic. For Stage entries with a translating
    `MappedBridge`, the wire `topic` lives on a transport-specific
    namespace (e.g. `"nats-orders"`) while `logical_topic` carries the
    cross-transport identity (e.g. `"orders"`). Phase 2's reply-arrow
    pairing uses this to correlate `PUBLISHED` on transport A with
    `RECEIVED` on transport B. `None` when the bridge does not
    translate or for non-Stage entries; the reporter omits the JSON
    key in that case."""
    source: str | None = None
    """Which DSL surface produced this event (PRD-013 §1.6, schema
    v1.3). One of `"publish"` (test-side `scope.publish` /
    `harness.publish`), `"expect"` (subscriber registered by
    `scope.expect` / `s.expect`), `"reply"` (reply-chain registered
    by `scope.on(...).publish(...)`), or `"scope"` (scope-level
    framework event such as `DEADLINE`). Distinguishes a test's
    explicit publish from a reply-chain's response on the same topic
    when both appear in the timeline. `None` for entries that
    pre-date the v1.3 instrumentation; the reporter omits the JSON
    key in that case."""

    def __post_init__(self) -> None:
        # Defence-in-depth: every shipping write path goes through
        # `_Timeline.record` which truncates `detail`, but a future
        # direct-construction call site must not bypass the cap.
        # `frozen=True` requires `object.__setattr__` to mutate.
        if len(self.detail) > _TIMELINE_DETAIL_MAX_CHARS:
            object.__setattr__(
                self,
                "detail",
                self.detail[: _TIMELINE_DETAIL_MAX_CHARS - 3] + "...",
            )

    @property
    def wall_clock(self) -> str:
        return datetime.fromtimestamp(self._wall_clock_epoch, UTC).isoformat()


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
    record_errors: int = 0
    """Count of `record()` calls that raised internally and were
    swallowed. An observability failure must never break the AUT, so
    `record()` catches its own exceptions; this counter exposes them
    to consumers (notably the reporter) without impacting test outcomes."""
    sealed: bool = False
    """When True, `record()` becomes a no-op. Set by the scope's
    `await_all` immediately after snapshotting `entries` so that
    late-arriving inbound callbacks (subscriptions still active until
    `__aexit__` unsubscribes) cannot mutate the snapshot's
    `dropped` counter or the entry tail. Without this flag a late
    inbound on a still-subscribed callback would produce a
    consumer-visible inconsistency between `len(result.timeline)`
    and `result.timeline_dropped`."""

    def anchor(self, now: float) -> None:
        if self.t0 is None:
            self.t0 = now

    def record(
        self,
        *,
        now: float,
        topic: str | None,
        action: TimelineAction,
        detail: str = "",
        transport: str | None = None,
        logical_topic: str | None = None,
        source: str | None = None,
    ) -> None:
        """Record one observed event.

        `topic` is the wire topic. `None` is valid for scope-level
        events such as `DEADLINE` (PRD-013 §D-3); the reporter omits
        the JSON key for those entries.

        `transport` is the per-transport attribution for Stage scenarios
        (PRD-013 §2.1). Single-`Harness` callers leave it at `None`;
        Stage callers pass the per-transport child name (e.g. `"kafka"`)
        for transport-scoped events and leave it at `None` for
        scope-level events. The reporter omits the JSON key when `None`.

        `logical_topic` is the scope-level pre-bridge-translation topic
        name where it differs from the wire `topic`. Phase 2's reply-arrow
        pairing uses this to correlate cross-transport events.

        `detail` is bounded by `_TIMELINE_DETAIL_MAX_CHARS`; longer
        values are truncated with a trailing ellipsis.

        Resilience: any exception inside this method is swallowed and
        logged at WARNING (`timeline_record_failed`). An observability
        seam must never break the AUT — same principle ADR-0017 applies
        to reply diagnostics. The `record_errors` counter exposes the
        error count for tests / consumers that want to assert on it.

        After `sealed` is set (by Stage's `await_all` once the snapshot
        is taken), `record()` is a silent no-op. Late inbound callbacks
        on still-subscribed transports are dropped without mutating the
        snapshot's counters or entries.
        """
        if self.sealed:
            return
        try:
            if self.t0 is None:
                self.t0 = now
            # Defensive monotonicity clamp: an event whose `now` is
            # before the timeline's anchor would compute a negative
            # `offset_ms`, breaking the rendered waterfall's
            # left-to-right reading. Real brokers can deliver out of
            # order, and a future transport hop across threads could
            # report a `loop.time()` slightly before another thread's.
            # Clamp at the anchor so offsets stay non-negative; the
            # cost is one comparison per record.
            if now < self.t0:
                now = self.t0
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
                    transport=transport,
                    logical_topic=logical_topic,
                    source=source,
                )
            )
        except Exception:
            self.record_errors += 1
            logging.getLogger("choreo.scenario").warning(
                "timeline_record_failed",
                extra={"action": str(action), "topic": topic, "transport": transport},
                exc_info=True,
            )
