"""Behavioural tests for Scenario replies.

Covers PRD-008 functional requirements and the three reply ADRs:
  - ADR-0016 — lifecycle: scope-bound, fire-once, pre-publish registration
  - ADR-0017 — fire-and-forget results via `ReplyReport`
  - ADR-0018 — correlation scoping reuses the expect-filter

Usage pattern inside a scenario scope:

    async with harness.scenario("name") as s:
        s.on("svc.request").publish(
            "svc.reply",
            lambda message_received: {"correlation_id": message_received["correlation_id"], "status": "COMPLETED"},
        )
        h = s.expect("state.changed", contains_fields({"qty": 1000}))
        s = s.publish("requests.submitted", fixture)
        result = await s.await_all(timeout_ms=500)
"""
from __future__ import annotations

import asyncio
import logging
import pickle
from pathlib import Path

import pytest


@pytest.fixture(scope="session")
async def harness(allowlist_yaml_path: Path):
    from core import Harness
    from core.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        lbm_resolver="lbmrd:15380",
    )
    h = Harness(transport)
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


# ---------------------------------------------------------------------------
# Chain shape — ADR-0016 type-state
# ---------------------------------------------------------------------------


async def test_on_should_return_a_reply_chain_from_builder_state(harness) -> None:
    from core.scenario import ReplyChain

    async with harness.scenario("chain-from-builder") as s:
        chain = s.on("svc.request")
        assert isinstance(chain, ReplyChain)


async def test_on_should_return_a_reply_chain_from_expecting_state(harness) -> None:
    from core.matchers import field_equals
    from core.scenario import ReplyChain

    async with harness.scenario("chain-from-expecting") as s:
        s.expect("t", field_equals("k", "v"))
        chain = s.on("svc.request")
        assert isinstance(chain, ReplyChain)


async def test_on_should_raise_attribute_error_from_triggered_state(harness) -> None:
    from core.matchers import field_equals

    async with harness.scenario("on-after-publish") as s:
        s.expect("t", field_equals("k", "v"))
        s = s.publish("t", {"k": "v"})
        with pytest.raises(AttributeError):
            s.on("svc.request")


async def test_chain_publish_should_return_the_scenario(harness) -> None:
    from core.scenario import Scenario

    async with harness.scenario("publish-returns-scenario") as s:
        returned = s.on("svc.request").publish("svc.reply", {"ok": True})
        assert returned is s
        assert isinstance(returned, Scenario)


async def test_chain_publish_called_twice_should_raise(harness) -> None:
    from core.scenario import ReplyAlreadyBoundError

    async with harness.scenario("double-publish-on-chain") as s:
        chain = s.on("svc.request")
        chain.publish("svc.reply", {"ok": True})
        with pytest.raises(ReplyAlreadyBoundError):
            chain.publish("svc.reply", {"ok": True})


async def test_on_from_builder_state_should_leave_scenario_able_to_publish(harness) -> None:
    """Registering a reply on a fresh builder transitions to EXPECTING, so
    the usual publish → await_all path works without a prior expect()."""
    async with harness.scenario("no-expect-but-on") as s:
        s.on("t").publish("t.reply", {"ok": True})
        # `publish` must be callable — the on() registration advanced state.
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=50)

    assert result.passed is True


# ---------------------------------------------------------------------------
# Fire semantics — happy path
# ---------------------------------------------------------------------------


async def test_a_matching_trigger_should_publish_the_reply(harness) -> None:
    from core.matchers import field_equals

    async with harness.scenario("instant-reply") as s:
        h_reply = s.expect("svc.reply", field_equals("status", "COMPLETED"))
        s.on("svc.request").publish(
            "svc.reply",
            lambda message_received: {
                "request_id": message_received["request_id"],
                "status": "COMPLETED",
                "qty": message_received["qty"],
            },
        )
        s = s.publish(
            "svc.request", {"request_id": "CL-1", "qty": 100}
        )
        result = await s.await_all(timeout_ms=200)

    result.assert_passed()
    assert h_reply.was_fulfilled()
    assert h_reply.message["request_id"] == "CL-1"
    assert h_reply.message["qty"] == 100


async def test_a_static_dict_reply_should_publish_the_dict(harness) -> None:
    from core.matchers import field_equals

    async with harness.scenario("static-reply") as s:
        h_reply = s.expect("heartbeat.response", field_equals("ok", True))
        s.on("heartbeat.request").publish("heartbeat.response", {"ok": True})
        s = s.publish("heartbeat.request", {})
        result = await s.await_all(timeout_ms=200)

    result.assert_passed()
    assert h_reply.was_fulfilled()


async def test_a_bytes_reply_should_publish_bytes_verbatim(harness) -> None:
    """Bytes bypass the codec — the caller owns every byte. The expect-side
    correlation filter only applies to dicts, so a raw-bytes reply routes
    to every subscriber."""
    from core.matchers import payload_contains

    async with harness.scenario("bytes-reply") as s:
        h = s.expect("raw.reply", payload_contains(b"RAW-BYTES"))
        s.on("raw.request").publish("raw.reply", b"\x00RAW-BYTES\x01")
        s = s.publish("raw.request", {})
        result = await s.await_all(timeout_ms=100)

    result.assert_passed()
    assert h.was_fulfilled()


async def test_a_reply_with_no_matcher_should_match_every_inbound_on_topic(harness) -> None:
    async with harness.scenario("no-matcher") as s:
        s.on("t").publish("t.reply", lambda message_received: {"ok": True})
        s = s.publish("t", {"anything": "goes"})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.match_count == 1


async def test_a_reply_with_a_matcher_should_only_fire_on_matching_trigger(harness) -> None:
    from core.matchers import field_equals

    async with harness.scenario("filtered") as s:
        h = s.expect("svc.reply", field_equals("kind", "ITEM-A"))
        s.on("svc.request", field_equals("kind", "ITEM-A")).publish(
            "svc.reply",
            lambda message_received: {"kind": message_received["kind"], "status": "COMPLETED"},
        )
        # First request does not match the reply's trigger filter.
        s = s.publish("svc.request", {"kind": "ITEM-B"})
        # Second matches and fires the reply.
        s = s.publish("svc.request", {"kind": "ITEM-A"})
        result = await s.await_all(timeout_ms=200)

    result.assert_passed()
    assert h.message["kind"] == "ITEM-A"


# ---------------------------------------------------------------------------
# Fire-once semantics — ADR-0016 §Fire-once enforcement
# ---------------------------------------------------------------------------


async def test_a_reply_should_fire_at_most_once_per_scope(harness) -> None:
    from core.scenario import ReplyReportState

    call_count = 0

    def builder(message_received):
        nonlocal call_count
        call_count += 1
        return {
            "request_id": message_received["request_id"],
            "status": "COMPLETED",
        }

    async with harness.scenario("fire-once") as s:
        s.on("svc.request").publish("svc.reply", builder)
        s = s.publish("svc.request", {"request_id": "A"})
        s = s.publish("svc.request", {"request_id": "B"})
        result = await s.await_all(timeout_ms=100)

    assert call_count == 1
    rep = result.reply_at("svc.request")
    assert rep.state is ReplyReportState.REPLIED
    assert rep.candidate_count == 2
    assert rep.match_count == 1
    assert rep.reply_published is True


async def test_a_reply_should_count_candidates_arriving_after_it_fires(harness) -> None:
    """Post-FIRED subscription stays alive for candidate counting (ADR-0016
    §Fire-once enforcement step 2)."""
    async with harness.scenario("post-fire-count") as s:
        s.on("t").publish("t.reply", lambda message_received: {"ok": True})
        s = s.publish("t", {"n": 1})
        s = s.publish("t", {"n": 2})
        s = s.publish("t", {"n": 3})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.candidate_count == 3
    assert rep.match_count == 1


# ---------------------------------------------------------------------------
# Terminal report states — ADR-0017
# ---------------------------------------------------------------------------


async def test_a_reply_that_never_saw_a_candidate_should_report_armed_no_match(
    harness,
) -> None:
    from core.scenario import ReplyReportState

    async with harness.scenario("no-candidates") as s:
        s.on("never.arrives").publish("never.reply", {"ok": True})
        s = s.publish("other.topic", {})
        result = await s.await_all(timeout_ms=30)

    rep = result.reply_at("never.arrives")
    assert rep.state is ReplyReportState.ARMED_NO_MATCH
    assert rep.candidate_count == 0
    assert rep.match_count == 0
    assert rep.reply_published is False


async def test_a_reply_whose_matcher_rejected_every_candidate_should_report_armed_matcher_rejected(
    harness,
) -> None:
    from core.matchers import field_equals
    from core.scenario import ReplyReportState

    async with harness.scenario("matcher-rejected") as s:
        s.on("t", field_equals("kind", "ITEM-A")).publish(
            "t.reply", {"ok": True}
        )
        s = s.publish("t", {"kind": "ITEM-B"})
        s = s.publish("t", {"kind": "ITEM-C"})
        result = await s.await_all(timeout_ms=30)

    rep = result.reply_at("t")
    assert rep.state is ReplyReportState.ARMED_MATCHER_REJECTED
    assert rep.candidate_count == 2
    assert rep.match_count == 0
    assert rep.reply_published is False


async def test_a_reply_whose_builder_raises_should_report_reply_failed(
    harness,
) -> None:
    from core.scenario import ReplyReportState

    def bad_builder(message_received):
        raise ValueError("oops")

    async with harness.scenario("builder-boom") as s:
        s.on("t").publish("t.reply", bad_builder)
        s = s.publish("t", {"k": "v"})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.state is ReplyReportState.REPLY_FAILED
    assert rep.builder_error == "ValueError"
    assert rep.reply_published is False
    # The match happened; only the publish failed.
    assert rep.match_count == 1


async def test_builder_error_should_not_stop_the_scenario(harness) -> None:
    """Scenario continues after a builder exception; other expectations/replies
    still resolve normally."""
    from core.matchers import field_equals

    def bad(message_received):
        raise RuntimeError("boom")

    async with harness.scenario("builder-nonfatal") as s:
        h = s.expect("other.topic", field_equals("ok", True))
        s.on("trigger.topic").publish("t.reply", bad)
        s = s.publish("trigger.topic", {})
        s = s.publish("other.topic", {"ok": True})
        result = await s.await_all(timeout_ms=100)

    # The unrelated expect still passes — builder error is isolated to its reply.
    assert h.was_fulfilled()


# ---------------------------------------------------------------------------
# Correlation scoping — ADR-0018
# ---------------------------------------------------------------------------


async def test_a_reply_should_auto_inject_scope_correlation_id(harness) -> None:
    """ADR-0018: reply stamping mirrors `Scenario.publish`. The scope's
    correlation appears on the reply so a downstream in-scope expect routes."""
    from core.matchers import field_equals

    async with harness.scenario("auto-inject") as s:
        h = s.expect("reply.topic", field_equals("k", "v"))
        s.on("t").publish("reply.topic", lambda message_received: {"k": "v"})
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=100)

    result.assert_passed()
    # The downstream expect only fires if the filter routed the reply back to
    # this scope, which only happens if the injection worked.
    assert h.message["correlation_id"] == result.correlation_id


async def test_a_reply_should_ignore_messages_from_other_scopes(harness) -> None:
    """ADR-0018 parallel isolation — foreign-correlation messages are filtered
    out before they become candidates."""
    import json

    from core.matchers import field_equals

    async with harness.scenario("cross-scope-filter") as s:
        s.expect("svc.reply", field_equals("ok", True))
        s.on("svc.request").publish("svc.reply", lambda message_received: {"ok": True})

        # Manually inject a message on the bus under a foreign correlation.
        # It must not trip the reply.
        foreign = {"correlation_id": "TEST-foreign", "anything": "goes"}
        harness.publish("svc.request", json.dumps(foreign).encode())

        # Now a legitimate in-scope trigger.
        s = s.publish("svc.request", {"ok": True})
        result = await s.await_all(timeout_ms=100)

    rep = result.reply_at("svc.request")
    # Only the in-scope message becomes a candidate.
    assert rep.candidate_count == 1
    assert rep.match_count == 1


async def test_builder_overriding_correlation_id_should_flag_the_report(
    harness,
) -> None:
    """ADR-0018 §Correlation-override detection."""
    async with harness.scenario("override") as s:
        s.on("t").publish(
            "t.reply",
            lambda message_received: {"correlation_id": "TEST-other", "k": "v"},
        )
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.correlation_overridden is True


async def test_override_warning_should_not_log_the_outgoing_correlation_value(
    harness, caplog
) -> None:
    """ADR-0017 §Security — the override warning proves an override happened
    via the report flag; the raw value must not leak to logs because it is
    caller-controlled and may carry upstream identifiers."""
    import logging

    caplog.set_level(logging.WARNING, logger="core.scenario")

    async with harness.scenario("override-log-redaction") as s:
        s.on("t").publish(
            "t.reply",
            lambda message_received: {
                "correlation_id": "TEST-SECRET-UPSTREAM-ID",
                "k": "v",
            },
        )
        s = s.publish("t", {})
        await s.await_all(timeout_ms=50)

    warn_text = " ".join(r.message for r in caplog.records)
    assert "TEST-SECRET-UPSTREAM-ID" not in warn_text
    # But the event itself is visible.
    assert "correlation_id overridden" in warn_text


async def test_a_reply_without_correlation_override_should_not_flag_report(harness) -> None:
    async with harness.scenario("no-override") as s:
        s.on("t").publish("t.reply", lambda message_received: {"k": "v"})
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.correlation_overridden is False


async def test_replies_across_parallel_scopes_should_fire_only_for_their_own_scope(
    harness,
) -> None:
    """ADR-0018 / PRD-008 §Success Metrics — scaled to 20 concurrent scopes
    for CI speed; proves the parallel-isolation invariant.
    """
    from core.matchers import field_equals
    from core.scenario import ReplyReportState

    N = 20

    async def one_scope(index: int) -> tuple[bool, int, int]:
        async with harness.scenario(f"isolated-{index}") as s:
            h = s.expect("iso.reply", field_equals("index", index))
            s.on("iso.request").publish(
                "iso.reply",
                lambda message_received: {"index": message_received["index"]},
            )
            s = s.publish("iso.request", {"index": index})
            result = await s.await_all(timeout_ms=500)

        rep = result.reply_at("iso.request")
        return (
            h.was_fulfilled()
            and rep.state is ReplyReportState.REPLIED,
            rep.candidate_count,
            rep.match_count,
        )

    outcomes = await asyncio.gather(*(one_scope(i) for i in range(N)))
    assert all(passed for passed, _, _ in outcomes)
    # Each reply saw exactly one candidate — its own.
    for _, candidates, matches in outcomes:
        assert candidates == 1
        assert matches == 1


# ---------------------------------------------------------------------------
# ScenarioResult.replies shape + summary
# ---------------------------------------------------------------------------


async def test_scenario_result_should_carry_reply_reports(harness) -> None:
    async with harness.scenario("reports") as s:
        s.on("svc.request").publish("svc.reply", {"ok": True})
        s = s.publish("svc.request", {})
        result = await s.await_all(timeout_ms=50)

    assert len(result.replies) == 1
    rep = result.replies[0]
    assert rep.trigger_topic == "svc.request"
    assert rep.reply_topic == "svc.reply"


async def test_replies_in_result_should_be_in_registration_order(harness) -> None:
    async with harness.scenario("order") as s:
        s.on("a.topic").publish("a.reply", {"k": "v"})
        s.on("b.topic").publish("b.reply", {"k": "v"})
        s.on("c.topic").publish("c.reply", {"k": "v"})
        s = s.publish("a.topic", {})
        result = await s.await_all(timeout_ms=50)

    topics = [r.trigger_topic for r in result.replies]
    assert topics == ["a.topic", "b.topic", "c.topic"]


async def test_reply_at_should_raise_key_error_for_unknown_topic(harness) -> None:
    async with harness.scenario("lookup") as s:
        s.on("t").publish("t.reply", {"ok": True})
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=50)

    with pytest.raises(KeyError):
        result.reply_at("not.a.topic")


async def test_summary_should_include_a_replies_section(harness) -> None:
    async with harness.scenario("summarise") as s:
        s.on("svc.request").publish("svc.reply", {"ok": True})
        s = s.publish("svc.request", {})
        result = await s.await_all(timeout_ms=50)

    text = result.summary()
    assert "svc.request" in text
    assert "svc.reply" in text
    assert "replied" in text.lower()


async def test_summary_without_replies_should_not_include_a_replies_section(
    harness,
) -> None:
    from core.matchers import field_equals

    async with harness.scenario("plain-expect-only") as s:
        s.expect("t", field_equals("k", "v"))
        s = s.publish("t", {"k": "v"})
        result = await s.await_all(timeout_ms=50)

    text = result.summary()
    assert "Replys:" not in text


# ---------------------------------------------------------------------------
# Cleanup — ADR-0016 scope-bound teardown
# ---------------------------------------------------------------------------


async def test_reply_subscriptions_should_be_cleaned_up_on_clean_scope_exit(
    harness,
) -> None:
    async with harness.scenario("clean") as s:
        s.on("t").publish("t.reply", {"ok": True})
        s = s.publish("t", {})
        await s.await_all(timeout_ms=50)

    assert harness.active_subscription_count() == 0


async def test_reply_subscriptions_should_be_cleaned_up_on_exception(
    harness,
) -> None:
    with pytest.raises(RuntimeError):
        async with harness.scenario("clean-on-raise") as s:
            s.on("t").publish("t.reply", {"ok": True})
            raise RuntimeError("deliberate")

    assert harness.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# Security — ADR-0017 §Security Considerations
# ---------------------------------------------------------------------------


def test_reply_report_should_refuse_pickling() -> None:
    from core.scenario import ReplyReport, ReplyReportState

    rep = ReplyReport(
        trigger_topic="t",
        matcher_description="(any)",
        reply_topic="t.reply",
        state=ReplyReportState.REPLIED,
        candidate_count=1,
        match_count=1,
        reply_published=True,
    )
    with pytest.raises(TypeError):
        pickle.dumps(rep)


async def test_reply_report_repr_should_not_contain_payload(harness) -> None:
    async with harness.scenario("no-leak") as s:
        s.on("t").publish("t.reply", lambda message_received: {"secret": "PII-OUT"})
        s = s.publish("t", {"input": "PII-IN"})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert "PII-IN" not in repr(rep)
    assert "PII-OUT" not in repr(rep)


async def test_builder_error_report_should_contain_only_the_exception_class_name(
    harness,
) -> None:
    """ADR-0017 §Security — builder_error is constructed as
    `f"{type(e).__name__}"` alone. The exception's str(e) may contain
    payload-derived values and must not interpolate into the report."""

    def bad_builder(message_received):
        raise ValueError(
            f"bad request_id {message_received['request_id']}"
        )

    async with harness.scenario("redaction") as s:
        s.on("t").publish("t.reply", bad_builder)
        s = s.publish("t", {"request_id": "SECRET-XYZ"})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.builder_error == "ValueError"
    assert "SECRET-XYZ" not in rep.builder_error
    assert "SECRET-XYZ" not in repr(rep)
    assert "SECRET-XYZ" not in result.summary()


async def test_never_fired_reply_warning_should_redact_matcher_literal_values(
    harness, caplog
) -> None:
    """ADR-0017 §Security — WARNING-line matcher description is redacted;
    the unredacted text stays on the in-memory report for assertions."""
    from core.matchers import field_equals

    caplog.set_level(logging.WARNING, logger="core.scenario")

    async with harness.scenario("redacted-warn") as s:
        s.on("trigger.topic", field_equals("account", "ACME-SECRET-123")).publish(
            "reply.topic", {"ok": True}
        )
        s = s.publish("unrelated", {})
        await s.await_all(timeout_ms=30)

    warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    warn_text = " ".join(r.message for r in warn_records)
    assert "ACME-SECRET-123" not in warn_text
    assert "trigger.topic" in warn_text  # topic is not sensitive


# ---------------------------------------------------------------------------
# Logging — ADR-0017 §Warning-log behaviour
# ---------------------------------------------------------------------------


async def test_never_fired_reply_should_log_at_warning_on_scope_exit(
    harness, caplog
) -> None:
    caplog.set_level(logging.WARNING, logger="core.scenario")

    async with harness.scenario("never-fired-log") as s:
        s.on("never.arrives").publish("reply", {"k": "v"})
        s = s.publish("other", {})
        await s.await_all(timeout_ms=30)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("never.arrives" in r.message for r in warnings)


async def test_builder_error_should_log_at_error_level(harness, caplog) -> None:
    caplog.set_level(logging.ERROR, logger="core.scenario")

    def bad(message_received):
        raise ValueError("bad")

    async with harness.scenario("builder-error-log") as s:
        s.on("t").publish("t.reply", bad)
        s = s.publish("t", {})
        await s.await_all(timeout_ms=50)

    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert any("ValueError" in r.message for r in error_records)


async def test_fired_reply_should_not_log_at_warning(harness, caplog) -> None:
    """ADR-0017 §Warning-log behaviour: FIRED does not WARN, even when
    candidate_count > 1 (the over-count is visible on the report)."""
    caplog.set_level(logging.WARNING, logger="core.scenario")

    async with harness.scenario("fired-quiet") as s:
        s.on("t").publish("t.reply", lambda message_received: {"k": "v"})
        s = s.publish("t", {"i": 1})
        s = s.publish("t", {"i": 2})
        await s.await_all(timeout_ms=50)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert not any("t.reply" in r.message for r in warnings)


# ---------------------------------------------------------------------------
# Bundle pattern
# ---------------------------------------------------------------------------


def test_bundle_fn_type_alias_should_be_importable_from_core() -> None:
    """Consumers opt into bundle-compatibility type-checking by annotating
    against `BundleFn`."""
    from core import BundleFn  # noqa: F401

    assert BundleFn is not None


async def test_bundle_style_helpers_should_compose_on_a_shared_scope(
    harness,
) -> None:
    """Bundles are plain functions taking a Scenario."""
    from core.matchers import field_equals

    def instant_reply(scenario):
        scenario.on("svc.request").publish(
            "svc.reply",
            lambda message_received: {
                "request_id": message_received["request_id"],
                "status": "COMPLETED",
            },
        )

    def audit_capture(scenario):
        scenario.on("audit.request").publish("audit.ack", {"ok": True})

    async with harness.scenario("compose") as s:
        instant_reply(s)
        audit_capture(s)
        h_reply = s.expect("svc.reply", field_equals("status", "COMPLETED"))
        h_audit = s.expect("audit.ack", field_equals("ok", True))
        s = s.publish("svc.request", {"request_id": "X"})
        s = s.publish("audit.request", {})
        result = await s.await_all(timeout_ms=200)

    result.assert_passed()
    assert h_reply.was_fulfilled()
    assert h_audit.was_fulfilled()
    assert len(result.replies) == 2


# ---------------------------------------------------------------------------
# Timeline integration — reply lifecycle events appear on the scope timeline
# ---------------------------------------------------------------------------


async def test_reply_fire_should_record_a_timeline_entry(harness) -> None:
    """Reply activity must be visible on the scope timeline so the HTML
    report can render reply events inline with publishes / matches."""
    from core.scenario import TimelineAction

    async with harness.scenario("reply-fires-timeline") as s:
        s.on("trigger.t").publish(
            "reply.t",
            lambda message_received: {"echo": message_received.get("x")},
        )
        s = s.publish("trigger.t", {"x": 1})
        result = await s.await_all(timeout_ms=50)

    actions = [e.action for e in result.timeline]
    assert TimelineAction.REPLIED in actions


async def test_reply_fire_timeline_entry_should_record_the_trigger_topic(
    harness,
) -> None:
    """The entry's `topic` is the trigger topic (where the message was
    observed) so the by-topic view groups it with the trigger's publish.
    The reply topic lands in `detail` for the tooltip."""
    async with harness.scenario("reply-timeline-topic") as s:
        s.on("trigger.t").publish("reply.t", {"ok": True})
        s = s.publish("trigger.t", {})
        result = await s.await_all(timeout_ms=50)

    fired = [e for e in result.timeline if e.action.value == "replied"]
    assert len(fired) == 1
    assert fired[0].topic == "trigger.t"
    assert "reply.t" in fired[0].detail


async def test_reply_builder_error_should_record_a_timeline_entry(harness) -> None:
    from core.scenario import TimelineAction

    def bad(message_received):
        raise ValueError("boom")

    async with harness.scenario("builder-err-timeline") as s:
        s.on("trigger.t").publish("reply.t", bad)
        s = s.publish("trigger.t", {})
        result = await s.await_all(timeout_ms=50)

    actions = [e.action for e in result.timeline]
    assert TimelineAction.REPLY_FAILED in actions
    builder_err_entry = next(
        e for e in result.timeline if e.action.value == "reply_failed"
    )
    # Detail names the exception class (not str(e) — ADR-0017 §Security).
    assert "ValueError" in builder_err_entry.detail


async def test_passing_scenario_with_replies_should_retain_timeline(harness) -> None:
    """Passing scenarios normally return an empty timeline (PRD-006). Scenarios
    with replies override that so reply activity is still visible on pass."""
    async with harness.scenario("pass-with-replies") as s:
        s.on("trigger.t").publish("reply.t", {"ok": True})
        s = s.publish("trigger.t", {})
        result = await s.await_all(timeout_ms=50)

    assert result.passed is True
    assert len(result.timeline) > 0


async def test_passing_scenario_without_replies_should_still_have_empty_timeline(
    harness,
) -> None:
    """Guard: the reply-exception to the 'pass = empty timeline' rule must
    not bleed into reply-less passing scenarios. PRD-006 still applies."""
    from core.matchers import field_equals

    async with harness.scenario("pass-no-reply") as s:
        s.expect("t", field_equals("k", "v"))
        s = s.publish("t", {"k": "v"})
        result = await s.await_all(timeout_ms=50)

    assert result.passed is True
    assert result.timeline == ()


# ---------------------------------------------------------------------------
# Safety — ADR-0006 correlation boundary + fire-once re-entrance
# ---------------------------------------------------------------------------


async def test_a_reply_with_a_non_test_prefixed_correlation_override_should_fail(
    harness,
) -> None:
    """ADR-0006 — every outbound publish must carry a TEST- correlation so
    production systems can filter test traffic at their boundary. A reply
    builder that returns a foreign-namespace correlation_id must not make it
    to the wire; the reply is marked FAILED and the scenario continues."""
    from core.scenario import ReplyReportState

    async with harness.scenario("prod-id-refused") as s:
        s.on("t").publish(
            "t.reply",
            lambda message_received: {
                "correlation_id": "PROD-not-a-test-id",
                "k": "v",
            },
        )
        s = s.publish("t", {})
        result = await s.await_all(timeout_ms=50)

    rep = result.reply_at("t")
    assert rep.state is ReplyReportState.REPLY_FAILED
    assert rep.reply_published is False
    assert rep.builder_error == "CorrelationIdNotInTestNamespaceError"


async def test_a_builder_that_re_publishes_synchronously_should_not_double_fire(
    harness,
) -> None:
    """ADR-0016 §Fire-once enforcement — a callable reply_spec that itself
    calls `harness.publish` on the trigger topic (or anything that re-enters
    the dispatcher) must not cause the reply to fire twice. Closing the
    fire-once window BEFORE the builder runs is what makes this safe."""
    import json

    call_count = 0

    def reentrant_builder(message_received):
        nonlocal call_count
        call_count += 1
        # Re-publish on the trigger topic synchronously — MockTransport fans
        # callbacks synchronously, so this re-enters on_trigger before the
        # outer call's state transition in the old (buggy) implementation.
        if call_count == 1:
            foreign = {
                "correlation_id": message_received["correlation_id"],
                "nested": True,
            }
            harness.publish("reentrant.trigger", json.dumps(foreign).encode())
        return {"k": "v", "call": call_count}

    async with harness.scenario("fire-once-reentrance") as s:
        s.on("reentrant.trigger").publish("reentrant.reply", reentrant_builder)
        s = s.publish("reentrant.trigger", {})
        result = await s.await_all(timeout_ms=50)

    assert call_count == 1
    rep = result.reply_at("reentrant.trigger")
    # candidate_count sees both the original trigger AND the re-entrant
    # publish (two in-scope messages on the trigger topic), but match_count
    # is one — the reply fired exactly once.
    assert rep.candidate_count == 2
    assert rep.match_count == 1
    assert rep.reply_published is True


async def test_builder_exception_log_should_not_carry_the_exception_string(
    harness, caplog
) -> None:
    """ADR-0017 §Security — the ERROR log when a builder raises must not
    include str(e) because the exception message often echoes payload
    content. Only the class name is permitted across the log boundary."""
    import logging

    caplog.set_level(logging.ERROR, logger="core.scenario")

    def leaky_builder(message_received):
        raise ValueError(
            f"bad account {message_received['account']}"
        )

    async with harness.scenario("leaky-log") as s:
        s.on("t").publish("t.reply", leaky_builder)
        s = s.publish("t", {"account": "ACME-LEAKED-SECRET-42"})
        await s.await_all(timeout_ms=50)

    error_text = " ".join(r.message for r in caplog.records)
    # Class name appears (it IS the class name, not exception detail).
    assert "ValueError" in error_text
    # But the payload-derived content MUST NOT leak.
    assert "ACME-LEAKED-SECRET-42" not in error_text


async def test_a_callable_builder_should_not_mutate_its_returned_dict(
    harness,
) -> None:
    """A builder that returns a module-level template dict must not be
    mutated in place with the stamped correlation_id — otherwise the
    template accrues state across scopes and tests bleed into one another."""

    SHARED_TEMPLATE = {"status": "COMPLETED", "qty": 100}

    def template_builder(message_received):
        return SHARED_TEMPLATE

    async with harness.scenario("no-template-mutation") as s:
        s.on("t").publish("t.reply", template_builder)
        s = s.publish("t", {})
        await s.await_all(timeout_ms=50)

    assert "correlation_id" not in SHARED_TEMPLATE
    assert SHARED_TEMPLATE == {"status": "COMPLETED", "qty": 100}


def test_core_source_should_contain_no_domain_identifiers() -> None:
    """Choreo is a domain-agnostic event-driven testing library. Protocol-
    specific and domain-specific names belong in consumer repos, not in
    `packages/core/src/core/`."""
    core_src = Path(__file__).resolve().parents[1] / "src" / "core"
    forbidden = (
        "clOrdId",
        "execId",
        "execution_report",
        "ExecutionReport",
        "FakeFxAdapter",
        "fake_fx_adapter",
        "JUNO",
        "BOOKIE",
        "BOUNCER",
        "LEDGER",
        "Trading Technologies",
    )
    offenders: list[str] = []
    for py_file in core_src.rglob("*.py"):
        text = py_file.read_text()
        for term in forbidden:
            if term in text:
                offenders.append(f"{py_file}: {term}")
    assert offenders == [], (
        "Domain-specific identifiers must stay in consumer repos:\n"
        + "\n".join(offenders)
    )
