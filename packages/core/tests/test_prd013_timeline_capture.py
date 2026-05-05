"""PRD-013 PR 1.1 - additive `transport` field on TimelineEntry / _Timeline.record.

Red tests for the smallest framework change in PRD-013 §2.1: an optional,
kwarg-only `transport: str | None = None` on `TimelineEntry` and
`_Timeline.record(...)`. The field is the per-transport attribution that
Stage scopes will populate when the hook-point work in subsequent PRs lands.

Single-Harness scopes never pass the kwarg, so existing call sites continue
to produce entries with `transport=None`. The frozen dataclass contract on
`TimelineEntry` is preserved (post-construction mutation must raise).
"""

from __future__ import annotations

import dataclasses

import pytest

from choreo.scenario import TimelineAction, TimelineEntry, _Timeline


# ---------------------------------------------------------------------------
# TimelineEntry: additive `transport` field
# ---------------------------------------------------------------------------


def test_a_timeline_entry_constructed_without_transport_should_default_to_none():
    """The field is additive; existing constructors keep their shape."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
    )
    assert entry.transport is None


def test_a_timeline_entry_should_accept_a_transport_name_via_kwarg():
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
    )
    assert entry.transport == "kafka"


def test_a_timeline_entry_should_remain_frozen_after_adding_transport():
    """Frozen dataclass contract from PRD-012 is preserved; consumer-side
    post-hoc mutation of `transport` raises (consistent with Handle.transport
    read-only posture per ADR-0027 §Security Considerations)."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.transport = "nats"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _Timeline.record: additive kwarg-only `transport`
# ---------------------------------------------------------------------------


def test_recording_without_transport_should_produce_an_entry_with_transport_none():
    """Existing single-Harness call sites in scenario.py do not pass the
    kwarg; the resulting entry must carry `transport=None`. This is the
    byte-identity contract PRD-012's snapshot test pins for the JSON output."""
    timeline = _Timeline()
    timeline.record(
        now=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
    )
    (entry,) = tuple(timeline.entries)
    assert entry.transport is None


def test_recording_with_transport_should_round_trip_the_value_to_the_entry():
    """Stage hook points (PRD-013 PRs 1.2+) will call record(transport=...)
    with the per-transport child name; the kwarg must reach the entry."""
    timeline = _Timeline()
    timeline.record(
        now=0.0,
        topic="orders.processed",
        action=TimelineAction.PUBLISHED,
        transport="nats",
    )
    (entry,) = tuple(timeline.entries)
    assert entry.transport == "nats"


def test_recording_should_accept_transport_only_as_a_keyword_argument():
    """The kwarg-only constraint matches every other parameter on
    `_Timeline.record` (see scenario.py:166-173) and prevents accidental
    positional misuse from call sites."""
    timeline = _Timeline()
    with pytest.raises(TypeError):
        timeline.record(  # type: ignore[misc]
            0.0,
            "orders.new",
            TimelineAction.PUBLISHED,
            "",
            "kafka",
        )


# ---------------------------------------------------------------------------
# TimelineEntry: scope-level events (DEADLINE) carry topic=None
# ---------------------------------------------------------------------------


def test_a_timeline_entry_should_accept_topic_none_for_scope_level_events():
    """PRD-013 §D-3: scope-level events such as DEADLINE OMIT the
    `topic` field (Python `None`, JSON key omitted at the reporter
    boundary). Symmetric with the `transport` field's omission for
    scope-level events."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic=None,
        action=TimelineAction.DEADLINE,
    )
    assert entry.topic is None


def test_recording_with_topic_none_should_round_trip_to_the_entry():
    """`_Timeline.record(topic=None, ...)` is the recording path for
    scope-level events that ship with no per-topic attribution."""
    timeline = _Timeline()
    timeline.record(
        now=0.0,
        topic=None,
        action=TimelineAction.DEADLINE,
        detail="timeout_ms=200",
    )
    (entry,) = tuple(timeline.entries)
    assert entry.topic is None


# ---------------------------------------------------------------------------
# TimelineEntry: logical_topic field for cross-transport bridge translation
# ---------------------------------------------------------------------------


def test_a_timeline_entry_should_default_logical_topic_to_none():
    """The `logical_topic` field is additive groundwork for translating
    bridges (PRD-013 follow-up). Today's `MappedBridge` does not
    translate topic names, so all Phase 1 entries leave it at `None`
    and the reporter omits the JSON key."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="nats-orders",
        action=TimelineAction.PUBLISHED,
        transport="nats",
    )
    assert entry.logical_topic is None


def test_a_timeline_entry_should_accept_a_logical_topic_via_kwarg():
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="nats-orders",
        action=TimelineAction.PUBLISHED,
        transport="nats",
        logical_topic="orders",
    )
    assert entry.logical_topic == "orders"


def test_recording_with_logical_topic_should_round_trip_to_the_entry():
    timeline = _Timeline()
    timeline.record(
        now=0.0,
        topic="nats-orders",
        action=TimelineAction.PUBLISHED,
        transport="nats",
        logical_topic="orders",
    )
    (entry,) = tuple(timeline.entries)
    assert entry.logical_topic == "orders"


# ---------------------------------------------------------------------------
# TimelineEntry: `source` attribution (PRD-013 v1.3, schema v1.3)
# ---------------------------------------------------------------------------


def test_a_timeline_entry_should_default_source_to_none():
    """The field is additive on the dataclass; existing call sites
    that construct `TimelineEntry` without the kwarg still pass."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
    )
    assert entry.source is None


def test_a_timeline_entry_should_accept_a_source_via_kwarg():
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="orders.new",
        action=TimelineAction.PUBLISHED,
        source="publish",
    )
    assert entry.source == "publish"


def test_recording_with_source_should_round_trip_to_the_entry():
    """The kwarg-only `source` on `_Timeline.record` reaches the
    recorded entry. Stage hook sites pass one of {"publish", "expect",
    "reply", "scope"} per PRD-013 §1.6."""
    timeline = _Timeline()
    timeline.record(
        now=0.0,
        topic="orders.processed",
        action=TimelineAction.REPLIED,
        transport="nats",
        source="reply",
    )
    (entry,) = tuple(timeline.entries)
    assert entry.source == "reply"


# ---------------------------------------------------------------------------
# Resilience: timeline.record() must not break the AUT
# ---------------------------------------------------------------------------


def test_a_timeline_entry_should_truncate_an_oversized_detail_at_construction():
    """Defence-in-depth: detail truncation lives in `_Timeline.record`,
    but `__post_init__` enforces the cap at construction so a future
    direct-construction call site cannot bypass it."""
    from choreo.scenario import _TIMELINE_DETAIL_MAX_CHARS

    oversized = "a" * (_TIMELINE_DETAIL_MAX_CHARS * 2)
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="t",
        action=TimelineAction.PUBLISHED,
        detail=oversized,
    )
    assert len(entry.detail) == _TIMELINE_DETAIL_MAX_CHARS
    assert entry.detail.endswith("...")


def test_recording_a_failing_event_should_not_propagate_the_exception():
    """An observability seam must never break the AUT (PRD-013 §2.3
    notes mirror ADR-0017's reply-diagnostic posture). If recording
    raises internally, `record_errors` increments and the exception
    is swallowed."""
    timeline = _Timeline()
    # Force a failure: pass a `now` that breaks the offset arithmetic.
    # `(now - self.t0) * 1000` raises if `now` is a non-numeric type.
    timeline.record(
        now="not-a-float",  # type: ignore[arg-type]
        topic="t",
        action=TimelineAction.PUBLISHED,
    )
    assert timeline.record_errors == 1
    # The deque stays empty; the AUT continues unimpeded.
    assert tuple(timeline.entries) == ()


def test_recording_after_sealing_should_be_a_silent_no_op():
    """After `Stage.await_all` snapshots the timeline, the seal is set
    so late inbound callbacks (subscriptions still active until
    `__aexit__`) cannot mutate the snapshot's counters or entries."""
    timeline = _Timeline()
    timeline.record(
        now=0.0, topic="t", action=TimelineAction.PUBLISHED
    )
    assert len(timeline.entries) == 1

    timeline.sealed = True
    timeline.record(
        now=1.0, topic="t", action=TimelineAction.PUBLISHED
    )
    # No new entry, no counter mutation.
    assert len(timeline.entries) == 1
    assert timeline.dropped == 0
    assert timeline.record_errors == 0
