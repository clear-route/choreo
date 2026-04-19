"""Behavioural tests for Scenario DSL (ADR-0012, ADR-0014, ADR-0015).

Usage pattern inside a scenario scope:
    async with harness.scenario("name") as s:
        h_a = s.expect(topic, matcher)         # returns Handle, advances state
        s.expect(topic, matcher)               # multi-expect; Handle discarded
        s = s.publish(topic, payload)          # returns self, advances to triggered
        result = await s.await_all(timeout_ms=500)

Calling `publish` before any `expect` or `await_all` before any `publish`
raises `AttributeError` (ADR-0012 runtime enforcement).
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
async def harness(allowlist_yaml_path: Path):
    # Scenario tests assert on correlation stamping and per-scope isolation
    # (ADR-0002). The library default under ADR-0019 is NoCorrelationPolicy;
    # pass `test_namespace()` to reproduce the pre-ADR-0019 posture.
    from choreo import Harness, test_namespace
    from choreo.transports import MockTransport

    transport = MockTransport(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )
    h = Harness(transport, correlation=test_namespace())
    await h.connect()
    try:
        yield h
    finally:
        await h.disconnect()


# ---------------------------------------------------------------------------
# Scope lifecycle
# ---------------------------------------------------------------------------


async def test_harness_scenario_should_enter_a_scenario(harness) -> None:
    from choreo.scenario import Scenario

    async with harness.scenario("test.topic") as s:
        assert isinstance(s, Scenario)


async def test_scenario_scope_should_release_subscriptions_on_clean_exit(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("test.topic") as s:
        s.expect("test.topic", field_equals("k", "v"))
        # No publish; just exit to verify teardown.

    assert harness.active_subscription_count() == 0


async def test_scenario_scope_should_release_subscriptions_when_the_test_raises(
    harness,
) -> None:
    from choreo.matchers import field_equals

    with pytest.raises(RuntimeError):
        async with harness.scenario("test.topic") as s:
            s.expect("test.topic", field_equals("k", "v"))
            raise RuntimeError("deliberate")

    assert harness.active_subscription_count() == 0


# ---------------------------------------------------------------------------
# Type-state — illegal transitions at runtime
# ---------------------------------------------------------------------------


async def test_calling_publish_on_a_fresh_scenario_should_raise_attribute_error(
    harness,
) -> None:
    async with harness.scenario("test.topic") as s:
        with pytest.raises(AttributeError):
            s.publish("test.topic", b"x")


async def test_calling_await_all_on_a_fresh_scenario_should_raise_attribute_error(
    harness,
) -> None:
    async with harness.scenario("test.topic") as s:
        with pytest.raises(AttributeError):
            await s.await_all(timeout_ms=100)


async def test_calling_await_all_after_expect_but_before_publish_should_raise(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("test.topic") as s:
        s.expect("test.topic", field_equals("k", "v"))
        with pytest.raises(AttributeError):
            await s.await_all(timeout_ms=100)


async def test_calling_expect_after_publish_should_raise_attribute_error(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("test.topic") as s:
        s.expect("test.topic", field_equals("k", "v"))
        s = s.publish("test.topic", {"k": "v"})
        with pytest.raises(AttributeError):
            s.expect("test.topic", field_equals("k", "v"))


# ---------------------------------------------------------------------------
# Happy path: expect → publish → await_all
# ---------------------------------------------------------------------------


async def test_a_matching_inbound_should_fulfil_the_handle(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("happy") as s:
        s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", {"status": "PASS"})
        result = await s.await_all(timeout_ms=500)

    result.assert_passed()


async def test_a_non_matching_inbound_on_matching_correlation_should_resolve_as_fail(
    harness,
) -> None:
    """When messages arrive for the scope's correlation but none pass the
    matcher, the handle resolves as FAIL (expectation issue), not TIMEOUT
    (routing issue). The outcome label is the DX signal — a reviewer should
    immediately know whether messages arrived or not."""
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("mismatch") as s:
        handle = s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", {"status": "FAIL"})
        result = await s.await_all(timeout_ms=50)

    assert result.passed is False
    assert handle.outcome is Outcome.FAIL


async def test_a_handle_should_retain_a_structured_failure_for_every_near_miss(
    harness,
) -> None:
    """The report renders from `handle.failures`, not a single `last_mismatch_*`.
    Every near-miss on the scope's correlation is kept (bounded) as a typed
    `MatchFailure` so the UI can show the whole expected-vs-actual trail."""
    from choreo.matchers import MatchFailure, field_equals

    async with harness.scenario("many-mismatches") as s:
        handle = s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", {"status": "FAIL"})
        s = s.publish("test.topic", {"status": "BOOKED"})
        s = s.publish("test.topic", {"status": "REJECTED"})
        await s.await_all(timeout_ms=50)

    assert handle.attempts == 3
    assert handle.failures == (
        MatchFailure(kind="mismatch", path="status", expected="PASS", actual="FAIL"),
        MatchFailure(kind="mismatch", path="status", expected="PASS", actual="BOOKED"),
        MatchFailure(kind="mismatch", path="status", expected="PASS", actual="REJECTED"),
    )
    assert handle.failures_dropped == 0


async def test_a_handle_should_cap_retained_failures_and_count_the_overflow(
    harness,
) -> None:
    """A misbehaving SUT that floods the correlation must not pin unbounded
    memory on the handle. Retention is capped; overflow is counted so the
    report can show '5 shown, 23 more elided'."""
    from choreo.matchers import field_equals
    from choreo.scenario import _FAILURES_MAX

    async with harness.scenario("flood") as s:
        handle = s.expect("test.topic", field_equals("status", "PASS"))
        for i in range(_FAILURES_MAX + 5):
            s = s.publish("test.topic", {"status": f"FAIL-{i}"})
        await s.await_all(timeout_ms=50)

    assert len(handle.failures) == _FAILURES_MAX
    assert handle.failures_dropped == 5


async def test_deadline_with_zero_attempts_should_resolve_as_timeout(
    harness,
) -> None:
    """No messages arrived on the handle's topic → TIMEOUT (routing issue)."""
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("silent-routing") as s:
        handle = s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("somewhere.else", b"x")
        await s.await_all(timeout_ms=20)

    assert handle.outcome is Outcome.TIMEOUT
    assert handle.attempts == 0


async def test_deadline_with_near_misses_should_resolve_as_fail_not_timeout(
    harness,
) -> None:
    """The semantic distinction TIMEOUT vs FAIL must match reality:
    TIMEOUT means 'nothing came', FAIL means 'things came but were wrong'."""
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("near-miss-fail") as s:
        handle = s.expect("orders.booked", field_equals("status", "BOOKED"))
        s = s.publish("orders.booked", {"status": "REJECTED"})
        s = s.publish("orders.booked", {"status": "PENDING"})
        result = await s.await_all(timeout_ms=30)

    assert handle.outcome is Outcome.FAIL
    assert handle.attempts == 2
    # Summary should show FAIL label, not TIMEOUT.
    summary = result.failure_summary()
    assert "[FAIL]" in summary
    assert "[TIMEOUT]" not in summary


# ---------------------------------------------------------------------------
# Timeout behaviour
# ---------------------------------------------------------------------------


async def test_await_all_should_return_after_the_deadline_even_if_nothing_arrives(
    harness,
) -> None:
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("silent") as s:
        handle = s.expect("test.topic", field_equals("k", "v"))
        s = s.publish("other-topic", b"unrelated")
        result = await s.await_all(timeout_ms=50)

    assert result.passed is False
    assert handle.outcome is Outcome.TIMEOUT
    # The reason mentions no-message-arrived, naming the topic and budget.
    assert "no matching message" in handle.reason.lower()
    assert "test.topic" in handle.reason


async def test_a_timeout_should_not_leak_asyncio_timeout_error(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("caught") as s:
        s.expect("test.topic", field_equals("k", "v"))
        s = s.publish("other", b"")
        result = await s.await_all(timeout_ms=50)
        assert result is not None


# ---------------------------------------------------------------------------
# Multiple expectations
# ---------------------------------------------------------------------------


async def test_multiple_expectations_should_all_fulfil_on_matching_inbound(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("fan-out") as s:
        s.expect("topic.a", field_equals("value", "A"))
        s.expect("topic.b", field_equals("value", "B"))
        s = s.publish("topic.a", {"value": "A"})
        s = s.publish("topic.b", {"value": "B"})
        result = await s.await_all(timeout_ms=500)

    result.assert_passed()


async def test_scenario_result_passed_should_be_false_when_any_handle_times_out(
    harness,
) -> None:
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("partial") as s:
        h_a = s.expect("topic.a", field_equals("value", "A"))
        h_b = s.expect("topic.b", field_equals("value", "B"))
        s = s.publish("topic.a", {"value": "A"})
        # Deliberately don't publish topic.b
        result = await s.await_all(timeout_ms=50)

    assert result.passed is False
    assert h_a.outcome is Outcome.PASS
    assert h_b.outcome is Outcome.TIMEOUT


# ---------------------------------------------------------------------------
# Correlation isolation between scopes
# ---------------------------------------------------------------------------


async def test_two_sequential_scenarios_should_have_different_correlation_ids(
    harness,
) -> None:
    async with harness.scenario("one") as s1:
        corr1 = s1.correlation_id

    async with harness.scenario("two") as s2:
        corr2 = s2.correlation_id

    assert corr1 != corr2
    assert corr1.startswith("TEST-")
    assert corr2.startswith("TEST-")


# ---------------------------------------------------------------------------
# Assertion ergonomics — result.assert_passed() with rich failure messages
# ---------------------------------------------------------------------------


async def test_assert_passed_should_not_raise_when_every_handle_fulfilled(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("all-pass") as s:
        s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", {"status": "PASS"})
        result = await s.await_all(timeout_ms=500)

    # Must not raise.
    result.assert_passed()


async def test_assert_passed_should_raise_assertion_error_when_any_handle_failed(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("has-timeout") as s:
        s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("other", b"x")
        result = await s.await_all(timeout_ms=30)

    with pytest.raises(AssertionError):
        result.assert_passed()


async def test_assert_passed_failure_message_should_name_the_scenario(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("my-flaky-scenario") as s:
        s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("other", b"x")
        result = await s.await_all(timeout_ms=30)

    with pytest.raises(AssertionError) as exc:
        result.assert_passed()

    assert "my-flaky-scenario" in str(exc.value)


async def test_assert_passed_failure_message_should_name_the_failing_topic_and_matcher(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("mixed") as s:
        s.expect("orders.booked", field_equals("status", "BOOKED"))
        s = s.publish("other", b"x")
        result = await s.await_all(timeout_ms=30)

    with pytest.raises(AssertionError) as exc:
        result.assert_passed()

    message = str(exc.value)
    assert "orders.booked" in message
    assert "status" in message
    assert "BOOKED" in message


async def test_assert_passed_failure_message_should_include_timeout_reason(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("silent") as s:
        s.expect("orders.booked", field_equals("status", "BOOKED"))
        s = s.publish("other", b"x")
        result = await s.await_all(timeout_ms=30)

    with pytest.raises(AssertionError) as exc:
        result.assert_passed()

    message = str(exc.value)
    assert "TIMEOUT" in message
    # Zero-attempt diagnosis calls out that no message arrived.
    assert "no matching message arrived" in message.lower()


async def test_assert_passed_failure_message_should_list_every_failing_expectation(
    harness,
) -> None:
    """Multiple failures should all appear in the same AssertionError, not just the first."""
    from choreo.matchers import field_equals

    async with harness.scenario("multi-fail") as s:
        s.expect("topic.alpha", field_equals("alpha", "A"))
        s.expect("topic.bravo", field_equals("bravo", "B"))
        s.expect("topic.charlie", field_equals("charlie", "C"))
        s = s.publish("unrelated", b"x")
        result = await s.await_all(timeout_ms=30)

    with pytest.raises(AssertionError) as exc:
        result.assert_passed()

    message = str(exc.value)
    assert "topic.alpha" in message
    assert "topic.bravo" in message
    assert "topic.charlie" in message


async def test_scenario_result_str_should_include_outcome_counts(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("mixed") as s:
        s.expect("topic.pass", field_equals("k", "v"))
        s.expect("topic.timeout", field_equals("k", "v"))
        s = s.publish("topic.pass", {"k": "v"})
        result = await s.await_all(timeout_ms=30)

    text = str(result)
    assert "mixed" in text
    # Shows both the passing and timing-out expectation.
    assert "topic.pass" in text
    assert "topic.timeout" in text


# ---------------------------------------------------------------------------
# Failure-message DX — correlation ID, near-miss tracking, clear diagnosis
# ---------------------------------------------------------------------------


async def test_failure_summary_should_include_the_scope_correlation_id(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("with-corr") as s:
        s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("other", b"x")
        result = await s.await_all(timeout_ms=20)

    summary = result.failure_summary()
    assert result.correlation_id in summary


async def test_timeout_with_zero_attempts_should_report_no_message_arrived(
    harness,
) -> None:
    """When the topic received no messages matching the scope's correlation,
    the diagnosis says so clearly — distinguishable from a near-miss."""
    from choreo.matchers import field_equals

    async with harness.scenario("silent") as s:
        handle = s.expect("never.arrives", field_equals("k", "v"))
        s = s.publish("unrelated", b"x")
        result = await s.await_all(timeout_ms=20)

    assert handle.attempts == 0
    summary = result.failure_summary()
    assert "no matching message arrived" in summary.lower()


async def test_timeout_after_near_misses_should_report_attempt_count(harness) -> None:
    """When messages arrived with the right correlation but failed the matcher,
    the diagnosis names the attempt count so the author knows to look at matcher
    logic, not routing."""
    from choreo.matchers import field_equals

    async with harness.scenario("near-miss") as s:
        handle = s.expect("t", field_equals("status", "PASS"))
        # Two near-misses — correlation matches, matcher rejects.
        s = s.publish("t", {"status": "FAIL"})
        s = s.publish("t", {"status": "REJECTED"})
        result = await s.await_all(timeout_ms=30)

    assert handle.attempts == 2
    summary = result.failure_summary()
    assert "2" in summary


async def test_timeout_after_near_misses_should_report_latest_rejection_reason(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("latest-reject") as s:
        handle = s.expect("t", field_equals("status", "PASS"))
        s = s.publish("t", {"status": "REJECTED"})
        result = await s.await_all(timeout_ms=30)

    assert handle.last_mismatch_reason is not None
    assert "REJECTED" in handle.last_mismatch_reason
    summary = result.failure_summary()
    assert "REJECTED" in summary


async def test_handle_attempts_should_be_zero_by_default(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("fresh") as s:
        handle = s.expect("t", field_equals("k", "v"))
        assert handle.attempts == 0
        assert handle.last_mismatch_reason is None


async def test_handle_should_stop_accepting_attempts_after_pass(harness) -> None:
    """After a Handle fulfils, further inbound on its topic should not bump attempts."""
    from choreo.matchers import field_equals

    async with harness.scenario("idempotent") as s:
        handle = s.expect("t", field_equals("status", "PASS"))
        s = s.publish("t", {"status": "PASS"})
        # A second, non-matching inbound after the handle has fulfilled.
        s = s.publish("t", {"status": "OTHER"})
        result = await s.await_all(timeout_ms=20)

    result.assert_passed()
    assert handle.attempts == 0  # no rejection counted after fulfilment


# ---------------------------------------------------------------------------
# Latency budgets + SLOW outcome (PRD-006)
# ---------------------------------------------------------------------------


async def test_a_handle_matched_within_its_budget_should_resolve_as_pass(
    harness,
) -> None:
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("fast-enough") as s:
        handle = s.expect("test.topic", field_equals("k", "v")).within_ms(200)
        s = s.publish("test.topic", {"k": "v"})
        result = await s.await_all(timeout_ms=500)

    assert handle.outcome is Outcome.PASS
    result.assert_passed()


async def test_a_handle_matched_after_its_budget_should_resolve_as_slow(
    harness,
) -> None:
    import asyncio

    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("too-slow") as s:
        handle = s.expect("test.topic", field_equals("k", "v")).within_ms(5)
        await asyncio.sleep(0.02)  # 20ms elapses before publish, budget is 5ms
        s = s.publish("test.topic", {"k": "v"})
        result = await s.await_all(timeout_ms=500)

    assert handle.outcome is Outcome.SLOW
    assert handle.latency_ms > 5
    assert result.passed is False


async def test_a_slow_handle_should_make_assert_passed_raise(harness) -> None:
    import asyncio

    from choreo.matchers import field_equals

    async with harness.scenario("slow-fails-assert") as s:
        s.expect("test.topic", field_equals("k", "v")).within_ms(5)
        await asyncio.sleep(0.02)
        s = s.publish("test.topic", {"k": "v"})
        result = await s.await_all(timeout_ms=500)

    with pytest.raises(AssertionError) as exc:
        result.assert_passed()
    assert "SLOW" in str(exc.value)
    assert "budget" in str(exc.value).lower()


async def test_within_ms_should_return_the_handle_for_chaining(harness) -> None:
    from choreo.matchers import field_equals
    from choreo.scenario import Handle

    async with harness.scenario("chain") as s:
        chained = s.expect("test.topic", field_equals("k", "v")).within_ms(50)
        assert isinstance(chained, Handle)


async def test_within_ms_with_zero_should_raise_value_error(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("zero-budget") as s:
        handle = s.expect("test.topic", field_equals("k", "v"))
        with pytest.raises(ValueError):
            handle.within_ms(0)


async def test_within_ms_with_a_negative_budget_should_raise_value_error(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("neg-budget") as s:
        handle = s.expect("test.topic", field_equals("k", "v"))
        with pytest.raises(ValueError):
            handle.within_ms(-10)


async def test_calling_within_ms_twice_should_emit_a_user_warning(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("double-budget") as s:
        handle = s.expect("test.topic", field_equals("k", "v")).within_ms(50)
        with pytest.warns(UserWarning, match="overrides"):
            handle.within_ms(100)


# ---------------------------------------------------------------------------
# Failure timeline (PRD-006)
# ---------------------------------------------------------------------------


async def test_a_passing_scenario_should_have_an_empty_timeline(harness) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("pass-no-timeline") as s:
        s.expect("test.topic", field_equals("k", "v"))
        s = s.publish("test.topic", {"k": "v"})
        result = await s.await_all(timeout_ms=200)

    assert result.passed is True
    assert result.timeline == ()


async def test_a_failing_scenarios_timeline_should_include_publishes_rejections_and_deadlines(
    harness,
) -> None:
    """Diagnosing a timeout means seeing what the scope *did* observe before
    giving up. The timeline captures publishes, near-miss rejections, and
    the final deadline marker, in arrival order.
    """
    from choreo.matchers import field_equals
    from choreo.scenario import TimelineAction

    async with harness.scenario("fail-with-timeline") as s:
        s.expect("test.topic", field_equals("status", "BOOKED"))
        s = s.publish("test.topic", {"status": "REJECTED"})
        s = s.publish("test.topic", {"status": "PENDING"})
        result = await s.await_all(timeout_ms=30)

    assert result.passed is False
    actions = [e.action for e in result.timeline]
    assert actions.count(TimelineAction.PUBLISHED) == 2
    assert actions.count(TimelineAction.MISMATCHED) == 2
    assert actions.count(TimelineAction.DEADLINE) == 1
    offsets = [e.offset_ms for e in result.timeline]
    assert offsets == sorted(offsets), "timeline should be in arrival order"


async def test_failure_summary_should_include_the_timeline_when_the_scenario_fails(
    harness,
) -> None:
    from choreo.matchers import field_equals

    async with harness.scenario("fail-with-summary") as s:
        s.expect("test.topic", field_equals("status", "BOOKED"))
        s = s.publish("test.topic", {"status": "REJECTED"})
        result = await s.await_all(timeout_ms=30)

    summary = result.failure_summary()
    assert "Timeline" in summary
    assert "published" in summary
    assert "mismatched" in summary
    assert "deadline" in summary


# ---------------------------------------------------------------------------
# Dict payloads — codec encode + correlation_id auto-injection
# ---------------------------------------------------------------------------


async def test_publishing_a_dict_should_route_back_to_the_scope_without_a_manual_correlation_id(
    harness,
) -> None:
    """Auto-injection is observable: if the receive-side filter routes the
    response, the matcher fires. The test never mentions correlation_id."""
    from choreo.matchers import field_equals

    async with harness.scenario("dict-happy") as s:
        s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", {"status": "PASS"})
        result = await s.await_all(timeout_ms=500)

    result.assert_passed()


async def test_publishing_a_dict_with_an_explicit_correlation_id_should_not_be_overwritten(
    harness,
) -> None:
    """Explicit correlation overrides auto-injection; this is the negative-test
    door — publishing under a foreign correlation must not be routed back to
    this scope, so the handle should TIMEOUT."""
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    async with harness.scenario("dict-explicit") as s:
        handle = s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish(
            "test.topic",
            {"correlation_id": "TEST-not-mine", "status": "PASS"},
        )
        result = await s.await_all(timeout_ms=50)

    assert result.passed is False
    assert handle.outcome is Outcome.TIMEOUT


async def test_publishing_a_dict_with_a_non_prefixed_correlation_should_raise(
    harness,
) -> None:
    """Under the `test_namespace()` policy (harness fixture), every outbound
    publish must carry a `TEST-` correlation so downstream systems filter it
    at the boundary. A caller who overrides with a production-looking id
    (`PROD-...`) is refused at publish time, not allowed onto the wire
    (ADR-0006 + ADR-0019)."""
    from choreo import CorrelationIdNotInNamespaceError
    from choreo.matchers import field_equals

    async with harness.scenario("prefix-enforcement") as s:
        s.expect("test.topic", field_equals("k", "v"))
        with pytest.raises(CorrelationIdNotInNamespaceError):
            s.publish(
                "test.topic",
                {"correlation_id": "PROD-live-account", "k": "v"},
            )


async def test_publishing_bytes_should_pass_through_without_codec_intervention(
    harness,
) -> None:
    """Bytes are an explicit opt-out from auto-injection — the caller owns
    every byte. Verifies the dispatch boundary: a bytes payload still
    requires a manual correlation_id to be routed."""
    from choreo.matchers import field_equals

    async with harness.scenario("bytes-passthrough") as s:
        s.expect("test.topic", field_equals("status", "PASS"))
        corr = s.correlation_id.encode()
        s = s.publish(
            "test.topic",
            b'{"correlation_id":"' + corr + b'","status":"PASS"}',
        )
        result = await s.await_all(timeout_ms=500)

    result.assert_passed()


async def test_publishing_a_dict_should_not_mutate_the_caller_s_object(
    harness,
) -> None:
    """Auto-injection must not surprise the caller by adding keys to the dict
    they handed in — common bug when fixtures share dict templates."""
    from choreo.matchers import field_equals

    payload = {"status": "PASS"}
    async with harness.scenario("no-mutation") as s:
        s.expect("test.topic", field_equals("status", "PASS"))
        s = s.publish("test.topic", payload)
        await s.await_all(timeout_ms=500)

    assert "correlation_id" not in payload


# ---------------------------------------------------------------------------
# ScenarioResult pickle safety (complements Handle / ReplyReport guards)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Codec-decode resilience — a bad payload on one subscriber must not starve
# sibling subscribers on the same topic
# ---------------------------------------------------------------------------


async def test_a_codec_decode_exception_should_not_abort_sibling_subscribers(
    allowlist_yaml_path, caplog
) -> None:
    """If one subscriber's codec raises on a particular payload, the
    transport's fan-out loop would previously propagate the exception and
    skip every subscriber after the failing one. The scope's own on_message
    now swallows and logs at WARNING so MockTransport's iteration continues
    to every other subscriber on the topic."""
    import asyncio
    import logging

    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import MockTransport

    class BoomCodec:
        def decode(self, raw: bytes):
            raise ValueError("bad wire")

        def encode(self, obj):
            import json

            return json.dumps(obj).encode()

    transport = MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost")
    local_harness = Harness(transport, codec=BoomCodec())
    await local_harness.connect()
    try:
        with caplog.at_level(logging.WARNING, logger="choreo.scenario"):
            async with local_harness.scenario("decode-resilience") as s:
                h = s.expect("decode.topic", field_equals("ok", True))
                other_received: list[bytes] = []
                local_harness.subscribe(
                    "decode.topic",
                    lambda t, p: other_received.append(p),
                )
                s = s.publish("decode.topic", b"not-json")
                await asyncio.sleep(0)
                await s.await_all(timeout_ms=30)
    finally:
        await local_harness.disconnect()

    # The expect handle did not fulfil (decode bailed), but the sibling
    # subscriber DID see the raw bytes — fan-out was not aborted.
    assert h.was_fulfilled() is False
    assert other_received == [b"not-json"]
    warn_text = " ".join(r.message for r in caplog.records)
    assert "ValueError" in warn_text
    assert "bad wire" not in warn_text


# ---------------------------------------------------------------------------
# Scope lifecycle signals — reporter sees every scope, not just the
# ones that called await_all on the happy path.
# ---------------------------------------------------------------------------


async def test_scope_exited_without_await_all_should_emit_a_partial_result(
    harness,
) -> None:
    """A scope body that forgot to call `await_all()` must not silently
    disappear from the report. The reporter needs to surface it as a
    never-awaited scope, not act as if the scope never ran."""
    from choreo._reporting import register_observer, unregister_observer
    from choreo.matchers import field_equals

    emitted: list[tuple[str, bool]] = []

    def collect(result, nodeid, completed_normally):
        emitted.append((result.name, completed_normally))

    register_observer(collect)
    try:
        async with harness.scenario("forgot-await-all") as s:
            s.expect("t", field_equals("k", "v"))
            s.publish("t", {"k": "v"})
            # No await_all — simulate the developer error.
    finally:
        unregister_observer(collect)

    assert ("forgot-await-all", False) in emitted


async def test_scope_exited_without_await_all_should_log_a_warning(harness, caplog) -> None:
    import logging

    from choreo.matchers import field_equals

    with caplog.at_level(logging.WARNING, logger="choreo.scenario"):
        async with harness.scenario("forgot-await-all-log") as s:
            s.expect("t", field_equals("k", "v"))
            s.publish("t", {"k": "v"})

    warn_text = " ".join(r.message for r in caplog.records)
    assert "without calling await_all" in warn_text
    assert "forgot-await-all-log" in warn_text


async def test_scope_body_raising_after_await_all_should_log_a_teardown_warning(
    harness, caplog
) -> None:
    """If the body completes `await_all()` successfully but then raises during
    teardown, the primary result is already reported but the teardown failure
    must still be visible to the developer."""
    import logging

    from choreo.matchers import field_equals

    with caplog.at_level(logging.WARNING, logger="choreo.scenario"):
        with pytest.raises(RuntimeError, match="teardown boom"):
            async with harness.scenario("post-await-raise") as s:
                s.expect("t", field_equals("k", "v"))
                s = s.publish("t", {"k": "v"})
                await s.await_all(timeout_ms=50)
                raise RuntimeError("teardown boom")

    warn_text = " ".join(r.message for r in caplog.records)
    assert "AFTER await_all" in warn_text
    assert "RuntimeError" in warn_text


async def test_partial_emit_on_raise_should_not_leave_handles_in_pending_state(
    harness,
) -> None:
    """Previously, a scope that raised before `await_all()` emitted a
    ScenarioResult whose handles were still Outcome.PENDING — contradicting
    Handle's docstring which says PENDING is only valid before await_all.
    The partial-emit path must promote every PENDING handle to a terminal
    outcome so downstream consumers never see PENDING in a returned result."""
    from choreo._reporting import register_observer, unregister_observer
    from choreo.matchers import field_equals
    from choreo.scenario import Outcome

    captured: list = []

    def collect(result, nodeid, completed_normally):
        captured.append(result)

    register_observer(collect)
    try:
        with pytest.raises(ValueError, match="body boom"):
            async with harness.scenario("pending-resolution") as s:
                s.expect("t", field_equals("k", "v"))
                raise ValueError("body boom")
    finally:
        unregister_observer(collect)

    assert len(captured) == 1
    result = captured[0]
    for h in result.handles:
        assert h.outcome is not Outcome.PENDING
        assert h.outcome is Outcome.TIMEOUT
        assert "ValueError" in h.reason


async def test_unsubscribe_failure_during_teardown_should_log_a_warning(
    allowlist_yaml_path, caplog
) -> None:
    """If a transport's `unsubscribe` raises during scope teardown, the
    scope previously swallowed the exception silently — leaking subscribers
    across scenarios with no signal. Now it logs at WARNING so the developer
    sees the leak."""
    import logging

    from choreo import Harness
    from choreo.matchers import field_equals
    from choreo.transports import MockTransport

    class FailingUnsubscribe(MockTransport):
        def unsubscribe(self, topic, callback):
            raise RuntimeError("unsubscribe boom")

    transport = FailingUnsubscribe(
        allowlist_path=allowlist_yaml_path,
        endpoint="mock://localhost",
    )
    local_harness = Harness(transport)
    await local_harness.connect()

    try:
        with caplog.at_level(logging.WARNING, logger="choreo.scenario"):
            async with local_harness.scenario("unsub-fails") as s:
                s.expect("t", field_equals("k", "v"))
                s = s.publish("t", {"k": "v"})
                await s.await_all(timeout_ms=30)
    finally:
        await local_harness.disconnect()

    warn_text = " ".join(r.message for r in caplog.records)
    assert "unsubscribe raised RuntimeError" in warn_text


async def test_scenario_result_should_refuse_pickling_with_a_clear_message(
    harness,
) -> None:
    """A ScenarioResult carries Handles and ReplyReports that reject pickling
    individually. Without its own __reduce__, pickling would fail via a nested
    class's error — confusing to debug. The ScenarioResult-level guard gives
    a message that names the right object (ADR-0017)."""
    import pickle

    from choreo.matchers import field_equals

    async with harness.scenario("pickle-safe") as s:
        s.expect("t", field_equals("k", "v"))
        s = s.publish("t", {"k": "v"})
        result = await s.await_all(timeout_ms=50)

    with pytest.raises(TypeError) as exc:
        pickle.dumps(result)
    assert "ScenarioResult" in str(exc.value)
