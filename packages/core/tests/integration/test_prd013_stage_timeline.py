""" PRs 1.2-1.6 - Stage scope timeline + per-event hook points.

Red tests for the end-to-end slices of  §2.2-§2.3:

PR 1.2 covers the public `timeline` field on `StageScenarioResult`, the
`_Timeline` instance on `_StageScenarioScope`, and the `PUBLISHED` hook
via the `on_sent` callback (matches single-`Harness` post-wire semantics).

PR 1.3 adds `RECEIVED` (accept-path of `_decode_and_correlation_check`) and
`CORRELATION_SKIPPED` (correlation-mismatch path), with the wire-id mismatch
hash-redacted via `redact_correlation_id`.

PR 1.4 adds `MATCHED` (matcher-accept branch of `_resolve_handle_on_match`)
and `MISMATCHED` (matcher-reject branch). The MISMATCHED `detail` carries
the matcher's mismatch reason un-redacted ( §Security: payload
values stay visible in this test tool).

PR 1.5 adds `DEADLINE` (await_all timeout path). Scope-level event - the
`transport` field is OMITTED per  §D-3 (no sentinel, no `null`,
just absent).

PR 1.6 adds `REPLIED` (response_harness.publish post-wire via `on_sent`)
and `REPLY_FAILED` (build / publish exception path). REPLY_FAILED detail
carries only `type(exc).__name__` per  §Security Considerations -
exception messages are NOT included.

Phase 1 closes with PR 1.6; Phase 2 covers reporter + renderer.
"""

from __future__ import annotations

import math
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import pytest
from admiral import Harness
from admiral import test_namespace as _test_namespace
from admiral.correlation import DictFieldPolicy
from admiral.matchers import field_equals
from admiral.scenario import TimelineAction, TimelineEntry
from admiral.stage import InvalidTransportNameError, Stage, StageScenarioResult
from admiral.transports import MockTransport

from .conftest import mapped_bridge_for

# ---------------------------------------------------------------------------
# StageScenarioResult.timeline field shape
# ---------------------------------------------------------------------------


def test_a_stage_scenario_result_should_default_to_an_empty_timeline_tuple():
    """The field is additive on the dataclass; existing call sites that
    construct `StageScenarioResult` without the kwarg still pass."""
    result = StageScenarioResult(handles=(), passed=True, replies=(), correlation_id=None)
    assert result.timeline == ()


def test_a_stage_scenario_result_should_default_timeline_dropped_to_zero():
    result = StageScenarioResult(handles=(), passed=True, replies=(), correlation_id=None)
    assert result.timeline_dropped == 0


def test_a_stage_scenario_result_timeline_should_round_trip_a_constructed_entry():
    """Frozen dataclass: the field accepts a tuple at construction and
    exposes the same tuple. Tuple-ness is implied by `==` against a
    tuple literal — no need for a separate `isinstance` check."""
    entry = TimelineEntry(
        offset_ms=0.0,
        _wall_clock_epoch=0.0,
        topic="t",
        action=TimelineAction.PUBLISHED,
        transport="kafka",
    )
    result = StageScenarioResult(
        handles=(),
        passed=True,
        replies=(),
        correlation_id=None,
        timeline=(entry,),
    )
    assert result.timeline == (entry,)


# ---------------------------------------------------------------------------
# End-to-end: scope.publish() records a PUBLISHED entry with the right transport
# ---------------------------------------------------------------------------


async def test_a_stage_scope_with_no_activity_should_produce_an_empty_timeline(
    connected_stage: Stage,
) -> None:
    """The scope owns a `_Timeline` from `__aenter__` but no events fire
    if the scope body does not publish or expect."""
    async with connected_stage.scenario("empty") as scope:
        result = await scope.await_all(timeout_ms=10)
    assert result.timeline == ()


async def test_publishing_on_a_named_transport_should_record_a_published_entry(
    connected_stage: Stage,
) -> None:
    """A single `scope.publish(topic, payload, on=...)` records exactly one
    PUBLISHED entry whose `transport` is the publish target. This is the
    end-to-end hook-point test for  §2.3 row 1 (PUBLISHED)."""
    async with connected_stage.scenario("publish-only") as scope:
        scope.publish("orders.new", b"payload", on="kafka")
        result = await scope.await_all(timeout_ms=10)
    actions = [(e.action, e.transport, e.topic) for e in result.timeline]
    assert (TimelineAction.PUBLISHED, "kafka", "orders.new") in actions


async def test_publishing_on_two_transports_should_record_two_published_entries_with_distinct_transports(
    connected_stage: Stage,
) -> None:
    """The `transport` attribution is per-publish; two publishes on
    different transports produce two PUBLISHED entries with distinct
    `transport` values, in publish order."""
    async with connected_stage.scenario("two-publishes") as scope:
        scope.publish("orders.new", b"k", on="kafka")
        scope.publish("results", b"n", on="nats")
        result = await scope.await_all(timeout_ms=10)
    published = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.PUBLISHED
    ]
    assert published == [("kafka", "orders.new"), ("nats", "results")]


async def test_publishing_on_one_transport_should_not_record_a_published_entry_for_another_transport(
    connected_stage: Stage,
) -> None:
    """Negative guard: a publish on `kafka` must NOT produce a PUBLISHED
    entry attributed to `nats`. The `transport` field is per-publish,
    not per-Stage-registration."""
    async with connected_stage.scenario("only-kafka") as scope:
        scope.publish("orders.new", b"payload", on="kafka")
        result = await scope.await_all(timeout_ms=10)
    nats_published = [
        e for e in result.timeline if e.action == TimelineAction.PUBLISHED and e.transport == "nats"
    ]
    assert nats_published == []


async def test_a_scope_with_no_publish_should_record_no_published_entries(
    connected_stage: Stage,
) -> None:
    """Negative guard: a scope that only registers `expect` (no `publish`)
    records no PUBLISHED entries."""
    async with connected_stage.scenario("expect-only") as scope:
        scope.expect("orders.new", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=10)
    published = [e for e in result.timeline if e.action == TimelineAction.PUBLISHED]
    assert published == []


async def test_publishing_on_one_transport_should_not_record_a_received_entry_on_another_transport(
    connected_stage: Stage,
) -> None:
    """Negative guard: a same-scope publish/expect pair on transport A
    does NOT cause a RECEIVED entry attributed to transport B. The
    receiving-child's transport name is what populates `transport`,
    not the publisher's. Mirrors the cross-scope leak defence by
    pinning the per-transport routing rule directly."""
    async with connected_stage.scenario("transport-isolation") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    nats_received = [
        e for e in result.timeline if e.action == TimelineAction.RECEIVED and e.transport == "nats"
    ]
    assert nats_received == []


async def test_a_published_entry_should_carry_a_non_negative_offset_ms(
    connected_stage: Stage,
) -> None:
    """`_Timeline.record` anchors at first-event time; the resulting
    `offset_ms` is >= 0 and finite."""

    async with connected_stage.scenario("offset-shape") as scope:
        scope.publish("orders.new", b"payload", on="kafka")
        result = await scope.await_all(timeout_ms=10)
    published = [e for e in result.timeline if e.action == TimelineAction.PUBLISHED]
    assert len(published) == 1
    assert published[0].offset_ms >= 0.0
    assert math.isfinite(published[0].offset_ms)


async def test_a_single_harness_scenario_timeline_should_remain_unchanged(
    allowlist_yaml_path: Path,
) -> None:
    """Single-`Harness` scopes do not run through the Stage path; their
    timeline byte-identity contract from  must hold. Verifies that
    PR 1.2's Stage instrumentation does not regress single-`Harness`."""

    harness = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"),
        correlation=_test_namespace(),
    )

    await harness.connect()
    try:
        async with harness.scenario("single-harness") as s:
            s.expect("results", field_equals("kind", "never-arrives"))
            s = s.publish("topic", b"payload")
            result = await s.await_all(timeout_ms=10)
        published = [e for e in result.timeline if e.action == TimelineAction.PUBLISHED]
        assert len(published) == 1
        # Single-Harness entries do NOT carry a transport attribution; the
        # field defaults to None and the reporter omits the JSON key.
        assert published[0].transport is None
    finally:
        await harness.disconnect()


# ---------------------------------------------------------------------------
# PR 1.3: RECEIVED hook (end-to-end through `expect`)
# ---------------------------------------------------------------------------


async def test_a_message_arriving_on_an_expected_topic_should_record_a_received_entry(
    connected_stage: Stage,
) -> None:
    """A Stage `expect()` registered on transport X observing a `publish()`
    on transport X with the matching wire id records exactly one RECEIVED
    entry on the receiving transport. The `transport` field carries the
    receiving child's name."""

    async with connected_stage.scenario("received") as scope:
        scope.expect("orders.new", field_equals("kind", "never-matches"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    received = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.RECEIVED
    ]
    assert received == [("kafka", "orders.new")]


async def test_a_loopback_publish_should_record_both_published_and_received(
    connected_stage: Stage,
) -> None:
    """A same-transport publish/expect pair records exactly one PUBLISHED
    and one RECEIVED entry, both attributed to the same transport. The
    presence-and-attribution contract is pinned here; the relative
    order is pinned by
    `test_a_loopback_publish_should_record_published_before_received`."""

    async with connected_stage.scenario("loopback") as scope:
        scope.expect("orders.new", field_equals("kind", "never-matches"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    actions_with_transport = [(e.action, e.transport) for e in result.timeline]
    assert (TimelineAction.PUBLISHED, "kafka") in actions_with_transport
    assert (TimelineAction.RECEIVED, "kafka") in actions_with_transport


async def test_a_loopback_publish_should_record_published_before_received(
    connected_stage: Stage,
) -> None:
    """ §2.3.1: `on_sent` fires synchronously at the publish call
    site BEFORE subscriber dispatch (MockTransport) and BEFORE the
    broker round-trip (real transports). For a same-transport
    publish/expect pair, PUBLISHED's `offset_ms` is therefore <= the
    matching RECEIVED's. Closes the gap flagged by Phase 2 review
    cycle 2026-05-05 — previously the loopback test deliberately did
    not pin order, masking a real ordering inversion that a human
    caught from the rendered HTML report."""

    async with connected_stage.scenario("ordering") as scope:
        scope.expect("orders.new", field_equals("kind", "never-matches"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    pub = next(e for e in result.timeline if e.action == TimelineAction.PUBLISHED)
    rcv = next(e for e in result.timeline if e.action == TimelineAction.RECEIVED)
    assert pub.offset_ms <= rcv.offset_ms


async def test_a_received_message_should_record_received_before_matched(
    connected_stage: Stage,
) -> None:
    """ §2.3 row 2 + 4: RECEIVED is recorded inside
    `_decode_and_correlation_check` BEFORE the matcher runs; MATCHED
    is recorded inside `_resolve_handle_on_match`'s accept branch.
    `RECEIVED.offset_ms <= MATCHED.offset_ms` for any matched
    inbound."""

    async with connected_stage.scenario("rcv-then-match") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    rcv = next(e for e in result.timeline if e.action == TimelineAction.RECEIVED)
    matched = next(e for e in result.timeline if e.action == TimelineAction.MATCHED)
    assert rcv.offset_ms <= matched.offset_ms


async def test_a_rejected_message_should_record_received_before_mismatched(
    connected_stage: Stage,
) -> None:
    """Symmetric to the matched case: RECEIVED precedes MISMATCHED
    by construction (the matcher runs inside `_resolve_handle_on_match`
    after `_decode_and_correlation_check` has already recorded
    RECEIVED)."""

    async with connected_stage.scenario("rcv-then-mismatch") as scope:
        scope.expect("orders.new", field_equals("kind", "expected"), on="kafka")
        scope.publish("orders.new", {"kind": "actual"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    rcv = next(e for e in result.timeline if e.action == TimelineAction.RECEIVED)
    mis = next(e for e in result.timeline if e.action == TimelineAction.MISMATCHED)
    assert rcv.offset_ms <= mis.offset_ms


async def test_a_reply_chain_should_record_published_trigger_before_replied_response(
    connected_stage: Stage,
) -> None:
    """ §2.3 rows 1 + 7: the trigger publish records PUBLISHED
    at call-time; the reply chain runs inside the subsequent subscriber
    dispatch and publishes the response, recording REPLIED. PUBLISHED's
    `offset_ms` is therefore <= REPLIED's. This is the test that
    would have caught the recently-fixed inversion bug."""

    async with connected_stage.scenario("reply-order") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    pub = next(e for e in result.timeline if e.action == TimelineAction.PUBLISHED)
    rep = next(e for e in result.timeline if e.action == TimelineAction.REPLIED)
    assert pub.offset_ms <= rep.offset_ms


async def test_a_reply_chain_should_record_received_on_the_trigger_transport(
    connected_stage: Stage,
) -> None:
    """The reply chain's `_on_trigger` callback runs through
    `_decode_and_correlation_check`, which records RECEIVED on the
    trigger transport. Closes the gap flagged by Phase 2 review cycle
    2026-05-05 — previously the timeline kwarg was missing on the
    reply path, silently dropping every reply trigger's RECEIVED."""

    async with connected_stage.scenario("reply-received") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    received_on_kafka = [
        e for e in result.timeline if e.action == TimelineAction.RECEIVED and e.transport == "kafka"
    ]
    assert len(received_on_kafka) >= 1


async def test_two_consecutive_publishes_should_record_strictly_increasing_offsets(
    connected_stage: Stage,
) -> None:
    """Test-side control flow monotonicity: two `scope.publish()`
    calls in sequence record two PUBLISHED entries whose `offset_ms`
    values are strictly increasing (or equal under same-tick collisions
    the renderer handles via insertion order)."""

    async with connected_stage.scenario("two-pubs") as scope:
        scope.publish("orders.new", b"a", on="kafka")
        scope.publish("orders.new", b"b", on="kafka")
        result = await scope.await_all(timeout_ms=20)
    published = [e for e in result.timeline if e.action == TimelineAction.PUBLISHED]
    assert len(published) == 2
    assert published[0].offset_ms <= published[1].offset_ms


# ---------------------------------------------------------------------------
#  v1.3 / schema v1.3: TimelineEntry.source attribution
# ---------------------------------------------------------------------------


async def test_a_test_side_publish_should_carry_source_publish(
    connected_stage: Stage,
) -> None:
    """`scope.publish(...)` records a PUBLISHED entry whose `source`
    is `"publish"` — distinguishes a test-initiated publish from a
    reply-chain's automatic response.  §1.6, schema v1.3."""

    async with connected_stage.scenario("source-publish") as scope:
        scope.publish("orders.new", b"payload", on="kafka")
        result = await scope.await_all(timeout_ms=10)
    pub = next(e for e in result.timeline if e.action == TimelineAction.PUBLISHED)
    assert pub.source == "publish"


async def test_an_expect_subscriber_callback_should_carry_source_expect(
    connected_stage: Stage,
) -> None:
    """RECEIVED / MATCHED / MISMATCHED events recorded inside an
    `scope.expect(...)` subscriber's `_on_message` callback carry
    `source="expect"`."""

    async with connected_stage.scenario("source-expect") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    rcv = next(e for e in result.timeline if e.action == TimelineAction.RECEIVED)
    matched = next(e for e in result.timeline if e.action == TimelineAction.MATCHED)
    assert rcv.source == "expect"
    assert matched.source == "expect"


async def test_a_reply_chain_published_response_should_carry_source_reply(
    connected_stage: Stage,
) -> None:
    """A reply-chain's REPLIED event carries `source="reply"` —
    distinguishes the chain's automatic response from a test-side
    publish on the same topic."""

    async with connected_stage.scenario("source-reply") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    rep = next(e for e in result.timeline if e.action == TimelineAction.REPLIED)
    assert rep.source == "reply"


async def test_a_reply_trigger_received_should_carry_source_reply(
    connected_stage: Stage,
) -> None:
    """The RECEIVED event recorded inside the reply chain's
    `_on_trigger` callback carries `source="reply"` — distinguishes
    a chain-side trigger arrival from an `expect`-side observation
    on the same topic."""

    async with connected_stage.scenario("source-reply-trigger") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    received_on_kafka = [
        e for e in result.timeline if e.action == TimelineAction.RECEIVED and e.transport == "kafka"
    ]
    assert len(received_on_kafka) == 1
    assert received_on_kafka[0].source == "reply"


async def test_a_deadline_event_should_carry_source_scope(
    connected_stage: Stage,
) -> None:
    """Scope-level events (currently only DEADLINE) carry
    `source="scope"`. Symmetric with `transport=None` and
    `topic=None` """

    async with connected_stage.scenario("source-scope") as scope:
        scope.expect("never.arrives", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=20)
    (deadline,) = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadline.source == "scope"


async def test_canonical_round_trip_disambiguates_test_publish_from_reply_publish(
    connected_stage: Stage,
) -> None:
    """The headline use-case: a Stage scope where the test publishes
    on a topic AND a reply chain publishes on a different topic. The
    `source` field disambiguates the two without reading the test
    code or chasing the chain registration."""

    async with connected_stage.scenario("disambiguate") as scope:
        scope.on("orders.new", on="kafka").publish(
            "orders.processed",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    test_publishes = [
        e for e in result.timeline if e.action == TimelineAction.PUBLISHED and e.source == "publish"
    ]
    reply_publishes = [
        e for e in result.timeline if e.action == TimelineAction.REPLIED and e.source == "reply"
    ]
    assert len(test_publishes) == 1
    assert test_publishes[0].topic == "orders.new"
    assert len(reply_publishes) == 1
    assert reply_publishes[0].topic == "orders.processed"


# ---------------------------------------------------------------------------
# PR 1.3: CORRELATION_SKIPPED hook (cross-scope leak defence)
# ---------------------------------------------------------------------------


async def test_a_message_for_another_scopes_wire_id_should_record_a_correlation_skipped_entry(
    allowlist_yaml_path: Path,
) -> None:
    """Two concurrent Stage scopes share infrastructure under
    `DictFieldPolicy`. The foreign scope publishes on `kafka`; its
    correlation policy stamps the foreign scope's wire id into the
    payload. The victim scope's `kafka` subscriber sees the same
    inbound (MockTransport fan-out is shared per-Harness), and
    `_decode_and_correlation_check` rejects the foreign wire id AND
    records a CORRELATION_SKIPPED on the receiving transport
   . The detail is hash-redacted via
    `redact_correlation_id` """

    nats_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    stage = Stage(
        harnesses={"nats": nats_h, "kafka": kafka_h},
        bridge=mapped_bridge_for("nats", "kafka"),
    )
    await stage.connect()
    try:
        async with AsyncExitStack() as stack:
            victim = await stack.enter_async_context(stage.scenario("victim"))
            foreign = await stack.enter_async_context(stage.scenario("foreign"))
            victim.expect(
                "orders.new",
                field_equals("kind", "never-matches"),
                on="kafka",
            )
            foreign.publish("orders.new", {"kind": "leak"}, on="kafka")
            # Drain the foreign scope first so its publish has fanned out
            # to every Harness subscriber, including the victim's.
            await foreign.await_all(timeout_ms=10)
            result = await victim.await_all(timeout_ms=10)
        skipped = [e for e in result.timeline if e.action == TimelineAction.CORRELATION_SKIPPED]
        assert len(skipped) == 1
        assert skipped[0].transport == "kafka"
        assert skipped[0].topic == "orders.new"
        assert skipped[0].detail.startswith("sha256:")
    finally:
        await stage.disconnect()


# ---------------------------------------------------------------------------
# PR 1.4: MATCHED hook (matcher-accept branch of _resolve_handle_on_match)
# ---------------------------------------------------------------------------


async def test_a_matcher_accepting_a_message_should_record_a_matched_entry(
    connected_stage: Stage,
) -> None:
    """A Stage `expect()` whose matcher accepts the inbound payload
    records exactly one MATCHED entry attributed to the receiving
    transport."""

    async with connected_stage.scenario("matched") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    matched = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.MATCHED
    ]
    assert matched == [("kafka", "orders.new")]


async def test_a_scope_with_a_matched_handle_should_pass(
    connected_stage: Stage,
) -> None:
    """The MATCHED hook records the matcher event, but the scope's
    pass/fail is a separate observable on `result.passed`. Pinned
    here so the matched-recording test asserts on one behaviour."""

    async with connected_stage.scenario("match-passes") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    assert result.passed


async def test_a_matched_entry_should_carry_the_expectation_transport(
    connected_stage: Stage,
) -> None:
    """When the same scope expects on one transport and publishes on
    another (cross-transport bridge case is not exercised here, just the
    attribution rule), the MATCHED `transport` is the EXPECTATION's
    transport, not the publisher's. For a same-transport loopback the
    two are identical."""

    async with connected_stage.scenario("attribution") as scope:
        scope.expect("results", field_equals("kind", "result"), on="nats")
        scope.publish("results", {"kind": "result"}, on="nats")
        result = await scope.await_all(timeout_ms=20)
    matched = [e for e in result.timeline if e.action == TimelineAction.MATCHED]
    assert len(matched) == 1
    assert matched[0].transport == "nats"


# ---------------------------------------------------------------------------
# PR 1.4: MISMATCHED hook (matcher-reject branch)
# ---------------------------------------------------------------------------


async def test_a_matcher_rejecting_a_message_should_record_a_mismatched_entry(
    connected_stage: Stage,
) -> None:
    """A Stage `expect()` whose matcher rejects the inbound payload (the
    payload arrived for the right scope but the field/value did not
    satisfy the matcher) records a MISMATCHED entry. The `transport`
    field carries the receiving child's name."""

    async with connected_stage.scenario("mismatched") as scope:
        scope.expect("orders.new", field_equals("kind", "expected"), on="kafka")
        scope.publish("orders.new", {"kind": "actual"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    mismatched = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.MISMATCHED
    ]
    assert mismatched == [("kafka", "orders.new")]


async def test_a_scope_whose_matcher_rejects_should_not_pass(
    connected_stage: Stage,
) -> None:
    """The MISMATCHED hook records the matcher event; the scope's
    pass/fail is separate. A scope whose only handle never resolves
    PASS reports `passed=False`."""

    async with connected_stage.scenario("mismatch-fails") as scope:
        scope.expect("orders.new", field_equals("kind", "expected"), on="kafka")
        scope.publish("orders.new", {"kind": "actual"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    assert not result.passed


async def test_a_mismatched_entry_should_carry_the_un_redacted_matcher_reason(
    connected_stage: Stage,
) -> None:
    """ §Security: payload-derived values in the MISMATCHED `detail`
    stay un-redacted because the test report's diagnostic value IS the
    actual rejected payload. The matcher's reason string (containing the
    expected and actual field values) reaches the timeline verbatim,
    bounded only by `_TIMELINE_DETAIL_MAX_CHARS` truncation."""

    async with connected_stage.scenario("mismatch-reason") as scope:
        scope.expect("orders.new", field_equals("kind", "expected"), on="kafka")
        scope.publish("orders.new", {"kind": "actual"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    mismatched = [e for e in result.timeline if e.action == TimelineAction.MISMATCHED]
    assert len(mismatched) == 1
    detail = mismatched[0].detail
    # The actual rejected value must be visible (un-redacted).
    assert "actual" in detail
    # The expected value should also be in the reason.
    assert "expected" in detail


async def test_a_matched_payload_should_not_record_a_mismatched_entry(
    connected_stage: Stage,
) -> None:
    """Regression guard: when the matcher accepts on first try, no
    MISMATCHED entry is recorded. PR 1.4's two events are mutually
    exclusive for a given attempt."""

    async with connected_stage.scenario("clean-match") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    mismatched = [e for e in result.timeline if e.action == TimelineAction.MISMATCHED]
    assert mismatched == []


# ---------------------------------------------------------------------------
# PR 1.5: DEADLINE hook (await_all timeout path; scope-level event)
# ---------------------------------------------------------------------------


async def test_a_scope_that_times_out_should_record_a_deadline_entry(
    connected_stage: Stage,
) -> None:
    """When `await_all`'s `timeout_ms` fires before every expectation
    resolves, the scope records exactly one DEADLINE entry (
    §2.3 row 6)."""

    async with connected_stage.scenario("times-out") as scope:
        scope.expect("never.arrives", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=20)
    deadlines = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert len(deadlines) == 1


async def test_a_deadline_entry_should_omit_the_transport_field(
    connected_stage: Stage,
) -> None:
    """ §D-3: DEADLINE is a scope-level event - no per-transport
    attribution. The `transport` field is OMITTED (Python: `None` so
    the reporter omits the JSON key per §1.1). No sentinel, no null
    semantics overloaded."""

    async with connected_stage.scenario("deadline-attr") as scope:
        scope.expect("never.arrives", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=20)
    (deadline,) = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadline.transport is None


async def test_a_deadline_entry_should_omit_the_topic_field(
    connected_stage: Stage,
) -> None:
    """ §D-3 symmetry: DEADLINE is a scope-level event, not
    topic-scoped. `topic` is `None` (reporter omits the JSON key) -
    parallels the `transport` omission. Avoids the in-band-signalling
    anti-pattern of using `topic=""` to flag scope-level events."""

    async with connected_stage.scenario("deadline-topic") as scope:
        scope.expect("never.arrives", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=20)
    (deadline,) = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadline.topic is None


async def test_a_deadline_detail_should_describe_the_timeout_budget(
    connected_stage: Stage,
) -> None:
    """The DEADLINE `detail` carries an operator-readable descriptor of
    the deadline that just fired in the form `timeout_ms=<budget>`
   ."""

    async with connected_stage.scenario("deadline-detail") as scope:
        scope.expect("never.arrives", field_equals("kind", "x"), on="kafka")
        result = await scope.await_all(timeout_ms=42)
    (deadline,) = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadline.detail == "timeout_ms=42"


async def test_a_scope_that_completes_within_budget_should_not_record_a_deadline_entry(
    connected_stage: Stage,
) -> None:
    """A scope whose every expectation resolves before `timeout_ms`
    fires records NO DEADLINE entry. The deadline event is not a
    scope-close marker; it is a deadline-fired marker."""

    async with connected_stage.scenario("no-deadline") as scope:
        scope.expect("orders.new", field_equals("kind", "ping"), on="kafka")
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=200)
    deadlines = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadlines == []


async def test_a_scope_with_no_expectations_should_not_record_a_deadline_entry(
    connected_stage: Stage,
) -> None:
    """A no-expectation scope's `await_all` returns immediately without
    waiting on a future, so no deadline fires. Mirrors single-`Harness`
    behaviour for the empty-handle case."""
    async with connected_stage.scenario("no-expectations") as scope:
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    deadlines = [e for e in result.timeline if e.action == TimelineAction.DEADLINE]
    assert deadlines == []


# ---------------------------------------------------------------------------
# PR 1.6: REPLIED hook (reply-chain success path)
# ---------------------------------------------------------------------------


async def test_a_reply_that_fires_successfully_should_record_a_replied_entry(
    connected_stage: Stage,
) -> None:
    """When `_StageReply.fire` builds a response and `response_harness.publish`
    succeeds, the scope records exactly one REPLIED entry. The `transport`
    field carries the response transport name."""
    async with connected_stage.scenario("reply-success") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    replied = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.REPLIED
    ]
    assert replied == [("nats", "results")]


async def test_a_replied_detail_should_name_the_trigger_topic(
    connected_stage: Stage,
) -> None:
    """The REPLIED `detail` records the trigger topic that fired the reply,
    so a reader can correlate trigger -> response across transports
    without cross-referencing the reply registration list."""
    async with connected_stage.scenario("reply-detail") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    (replied,) = [e for e in result.timeline if e.action == TimelineAction.REPLIED]
    assert "orders.new" in replied.detail


# ---------------------------------------------------------------------------
# PR 1.6: REPLY_FAILED hook (build / publish exception path)
# ---------------------------------------------------------------------------


async def test_a_reply_whose_build_raises_should_record_a_reply_failed_entry(
    connected_stage: Stage,
) -> None:
    """When the reply's `build` callback raises, `_StageReply.fire`
    transitions to `FIRED_BUILDER_ERROR` AND records a REPLY_FAILED
    entry attributed to the response transport."""

    def boom(trigger):
        raise ValueError("test-injected build failure")

    async with connected_stage.scenario("reply-build-fails") as scope:
        scope.on("orders.new", on="kafka").publish("results", on="nats", build=boom)
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    failed = [
        (e.transport, e.topic) for e in result.timeline if e.action == TimelineAction.REPLY_FAILED
    ]
    assert failed == [("nats", "results")]


async def test_a_reply_failed_detail_should_carry_only_the_exception_class_name(
    connected_stage: Stage,
) -> None:
    """ §Security Considerations: exception messages are NOT
    included in `detail` (they tend to interpolate context that is more
    variable and less diagnostic than the class name). The detail names
    the response topic and the exception CLASS only - never `str(exc)`."""

    def boom(trigger):
        raise ValueError("this message MUST NOT appear in the timeline")

    async with connected_stage.scenario("reply-detail-redaction") as scope:
        scope.on("orders.new", on="kafka").publish("results", on="nats", build=boom)
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    (failed,) = [e for e in result.timeline if e.action == TimelineAction.REPLY_FAILED]
    # Class name MUST appear; exception message MUST NOT.
    assert "ValueError" in failed.detail
    assert "MUST NOT appear" not in failed.detail


async def test_a_transport_name_with_html_should_be_rejected_at_stage_construction(
    two_harnesses: dict[str, Any],
) -> None:
    """ §1.4-§1.5 /  §1.1 schema regex
    `^[a-zA-Z0-9_-]{1,64}$` constrains transport names. Stage validates
    at __init__ so consumer-supplied names cannot propagate into the
    timeline / results.json / Phase 2 renderer where they would become
    an injection surface. Fail-closed at the framework boundary."""

    bad = {"<script>alert(1)</script>": next(iter(two_harnesses.values()))}
    with pytest.raises(InvalidTransportNameError):
        Stage(harnesses=bad, bridge=mapped_bridge_for("nats"))


async def test_a_transport_name_longer_than_sixty_four_chars_should_be_rejected(
    two_harnesses: dict[str, Any],
) -> None:
    """The 64-char ceiling matches the schema cap. Names at the limit
    pass; names over the limit fail."""

    bad = {"x" * 65: next(iter(two_harnesses.values()))}
    with pytest.raises(InvalidTransportNameError):
        Stage(harnesses=bad, bridge=mapped_bridge_for("nats"))


async def test_a_successful_reply_should_not_record_a_reply_failed_entry(
    connected_stage: Stage,
) -> None:
    """Regression guard: a clean reply path records REPLIED but no
    REPLY_FAILED. The two events are mutually exclusive for a given
    reply registration."""
    async with connected_stage.scenario("clean-reply") as scope:
        scope.on("orders.new", on="kafka").publish(
            "results",
            on="nats",
            build=lambda trigger: {"echo": trigger},
        )
        scope.publish("orders.new", {"kind": "ping"}, on="kafka")
        result = await scope.await_all(timeout_ms=20)
    failed = [e for e in result.timeline if e.action == TimelineAction.REPLY_FAILED]
    assert failed == []
