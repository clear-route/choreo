"""End-to-end reply cascade over real NATS.

Exercises PRD-008 replys (`s.on(...).publish(...)`) across a real wire.
A single initial publish kicks off a six-hop request/reply cascade,
each hop a reply watching the previous hop's reply. Six expects assert
that every step of the cascade carried its fields forward and that
every reply fired exactly once.

This test validates, against a real broker:

  - Multi-hop reply cascades (output of one reply triggers the next).
  - `message_received` access inside builders (each hop threads fields from
    the trigger into its reply).
  - Auto-injected `correlation_id` propagates across the whole chain, so
    the expect filter routes each downstream message back to the scope.
  - Fire-once semantics hold over the wire (ADR-0016 §Fire-once).
  - Consumer-owned bundle pattern: each hop is a plain function taking the
    Scenario, composable in-line by the test.

Runs only under ``pytest -m e2e`` with the compose stack:

    docker compose -f docker/compose.e2e.yaml up -d
    pytest -m e2e
"""
from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest


pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Topic set — one per hop, unique per test run so concurrent CI jobs cannot
# collide on the same broker.
# ---------------------------------------------------------------------------


def _make_topics() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    return {
        "events":     f"e2e.cascade.events.created.{suffix}",
        "check_req":  f"e2e.cascade.check.request.{suffix}",
        "check_resp": f"e2e.cascade.check.response.{suffix}",
        "svc_req":    f"e2e.cascade.service.request.{suffix}",
        "svc_reply":  f"e2e.cascade.service.reply.{suffix}",
        "state":      f"e2e.cascade.state.changed.{suffix}",
        "audit":      f"e2e.cascade.audit.record.{suffix}",
    }


# ---------------------------------------------------------------------------
# Bundles — each hop of the cascade as a plain function.
# ---------------------------------------------------------------------------


def _validation_asker(scenario, topics: dict[str, str]) -> None:
    """Hop 1: on created event, ask the validator to check."""
    scenario.on(topics["events"]).publish(
        topics["check_req"],
        lambda message_received: {
            "request_id": message_received["request_id"],
            "region":     message_received["region"],
            "count":      message_received["count"],
        },
    )


def _validation_approver(scenario, topics: dict[str, str]) -> None:
    """Hop 2: on validation request, approve with a quota remaining."""
    scenario.on(topics["check_req"]).publish(
        topics["check_resp"],
        lambda message_received: {
            "request_id":       message_received["request_id"],
            "approved":         True,
            "quota_remaining":  10_000,
        },
    )


def _request_router(scenario, topics: dict[str, str]) -> None:
    """Hop 3: on validation approval, route the request to the backend."""
    scenario.on(topics["check_resp"]).publish(
        topics["svc_req"],
        lambda message_received: {
            "request_id": message_received["request_id"],
            "backend":    "BACKEND-PRIMARY",
            "kind":       "CREATE",
        },
    )


def _reply_generator(scenario, topics: dict[str, str]) -> None:
    """Hop 4: on backend request, emit a completed reply."""
    scenario.on(topics["svc_req"]).publish(
        topics["svc_reply"],
        lambda message_received: {
            "request_id": message_received["request_id"],
            "reply_id":   f"REP-{message_received['request_id']}",
            "status":     "COMPLETED",
            "backend":    message_received["backend"],
        },
    )


def _state_updater(scenario, topics: dict[str, str]) -> None:
    """Hop 5: on reply, update state."""
    scenario.on(topics["svc_reply"]).publish(
        topics["state"],
        lambda message_received: {
            "request_id":   message_received["request_id"],
            "status":       "APPLIED",
            "count_applied": 1000,
        },
    )


def _audit_recorder(scenario, topics: dict[str, str]) -> None:
    """Hop 6: on state change, record an audit event."""
    scenario.on(topics["state"]).publish(
        topics["audit"],
        lambda message_received: {
            "request_id": message_received["request_id"],
            "event":      "APPLIED",
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_a_six_hop_reply_cascade_over_nats_should_complete_end_to_end(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Six replys chain a request/reply cascade over real NATS; six expects
    assert every hop's observable effect. One initial publish triggers the
    cascade."""
    from core import Harness
    from core.matchers import contains_fields, field_equals
    from core.scenario import ReplyReportState
    from core.transports import NatsTransport

    topics = _make_topics()
    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()
    try:
        async with harness.scenario("nats-reply-cascade") as s:
            # 6 expects — assert each observable effect of the cascade.
            h_check_req = s.expect(
                topics["check_req"],
                field_equals("request_id", "REQ-E2E-001"),
            )
            h_check_resp = s.expect(
                topics["check_resp"],
                field_equals("approved", True),
            )
            h_svc_req = s.expect(
                topics["svc_req"],
                contains_fields({"backend": "BACKEND-PRIMARY", "kind": "CREATE"}),
            )
            h_svc_reply = s.expect(
                topics["svc_reply"],
                field_equals("status", "COMPLETED"),
            )
            h_state = s.expect(
                topics["state"],
                field_equals("status", "APPLIED"),
            )
            h_audit = s.expect(
                topics["audit"],
                field_equals("event", "APPLIED"),
            )

            # 6 reply bundles — compose the cascade.
            _validation_asker(s, topics)
            _validation_approver(s, topics)
            _request_router(s, topics)
            _reply_generator(s, topics)
            _state_updater(s, topics)
            _audit_recorder(s, topics)

            # One publish kicks the whole cascade off.
            s = s.publish(
                topics["events"],
                {
                    "request_id": "REQ-E2E-001",
                    "region":     "eu-west",
                    "count":      1000,
                },
            )
            # 5s budget: six async hops over local NATS typically complete
            # in well under 100ms; the headroom is for slow CI containers.
            result = await s.await_all(timeout_ms=5000)

        result.assert_passed()

        # Every expect saw its expected payload.
        assert h_check_req.was_fulfilled()
        assert h_check_req.message["region"] == "eu-west"
        assert h_check_req.message["count"] == 1000

        assert h_check_resp.was_fulfilled()
        assert h_check_resp.message["quota_remaining"] == 10_000

        assert h_svc_req.was_fulfilled()
        assert h_svc_req.message["request_id"] == "REQ-E2E-001"

        assert h_svc_reply.was_fulfilled()
        assert h_svc_reply.message["reply_id"] == "REP-REQ-E2E-001"
        # Field threaded through four reply hops.
        assert h_svc_reply.message["backend"] == "BACKEND-PRIMARY"

        assert h_state.was_fulfilled()
        assert h_state.message["count_applied"] == 1000

        assert h_audit.was_fulfilled()
        assert h_audit.message["request_id"] == "REQ-E2E-001"

        # Every reply fired exactly once; none overrode correlation;
        # no builder raised. ADR-0017 §Validation — state-coverage test.
        assert len(result.replies) == 6
        registration_order = [
            topics["events"],
            topics["check_req"],
            topics["check_resp"],
            topics["svc_req"],
            topics["svc_reply"],
            topics["state"],
        ]
        assert [r.trigger_topic for r in result.replies] == registration_order

        for r in result.replies:
            assert r.state is ReplyReportState.REPLIED, (
                f"reply on {r.trigger_topic} ended in {r.state.value}"
            )
            assert r.match_count == 1
            assert r.candidate_count == 1
            assert r.reply_published is True
            assert r.correlation_overridden is False
            assert r.builder_error is None

        # Scope-bound cleanup (ADR-0016 §Cleanup): after the scope exits,
        # the transport carries no reply subscriptions attributable to us.
        assert harness.active_subscription_count() == 0
    finally:
        await harness.disconnect()


async def test_parallel_reply_scopes_over_nats_should_stay_isolated_by_correlation(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """Multiple concurrent scopes share the same NATS subjects. Each scope's
    reply must only fire for its own scope's trigger — the correlation
    filter (ADR-0018) is the load-bearing invariant here.

    Scaled to 5 concurrent scopes with a three-hop cascade each (15 replys
    total). Enough to prove cross-scope routing over a real wire without
    dominating CI wall-clock."""
    from core import Harness
    from core.matchers import field_equals
    from core.scenario import ReplyReportState
    from core.transports import NatsTransport

    N_SCOPES = 5
    # Topics shared across every scope — this is what makes the isolation
    # interesting. Only correlation-ID routing keeps the scopes from
    # cross-firing.
    trigger_topic = f"e2e.parallel.trigger.{uuid.uuid4().hex[:8]}"
    hop1_topic    = f"e2e.parallel.hop1.{uuid.uuid4().hex[:8]}"
    hop2_topic    = f"e2e.parallel.hop2.{uuid.uuid4().hex[:8]}"
    hop3_topic    = f"e2e.parallel.hop3.{uuid.uuid4().hex[:8]}"

    transport = NatsTransport(
        servers=[nats_url],
        allowlist_path=allowlist_yaml_path,
    )
    harness = Harness(transport)
    await harness.connect()

    async def run_scope(index: int) -> tuple[bool, list[ReplyReportState]]:
        async with harness.scenario(f"nats-parallel-{index}") as s:
            h1 = s.expect(hop1_topic, field_equals("index", index))
            h2 = s.expect(hop2_topic, field_equals("index", index))
            h3 = s.expect(hop3_topic, field_equals("index", index))

            # Three-hop cascade, each reply threads the scope's index
            # through — if isolation fails, the foreign-scope message
            # would carry a different index and the expects would FAIL
            # (not TIMEOUT) on the near-miss.
            s.on(trigger_topic).publish(
                hop1_topic,
                lambda message_received: {"index": message_received["index"]},
            )
            s.on(hop1_topic).publish(
                hop2_topic,
                lambda message_received: {"index": message_received["index"]},
            )
            s.on(hop2_topic).publish(
                hop3_topic,
                lambda message_received: {"index": message_received["index"]},
            )

            s = s.publish(trigger_topic, {"index": index})
            result = await s.await_all(timeout_ms=5000)

        all_fulfilled = (
            h1.was_fulfilled() and h2.was_fulfilled() and h3.was_fulfilled()
        )
        return all_fulfilled, [r.state for r in result.replies]

    try:
        outcomes = await asyncio.gather(
            *(run_scope(i) for i in range(N_SCOPES))
        )

        for i, (fulfilled, reply_states) in enumerate(outcomes):
            assert fulfilled, f"scope {i}: not every expect fulfilled"
            assert all(
                state is ReplyReportState.REPLIED for state in reply_states
            ), f"scope {i}: reply states {reply_states}"

        # After every scope has exited, there should be no residual
        # subscriptions attributable to replys or expects.
        assert harness.active_subscription_count() == 0
    finally:
        await harness.disconnect()
