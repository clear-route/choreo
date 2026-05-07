"""Group I: Stage cross-transport reply lifecycle. Negative-behaviour
integration tests.

Covers test-plan items I1-I6 and I8 from
`docs/test-plans/0027-stage-integration-tests.md`. I7 (correlation-
translation observation) requires `DictFieldPolicy` configuration on
the underlying harnesses to make the wire id appear in the published
payload; that setup belongs with Group J's parallel-isolation work
where the policy configuration is already a prerequisite.

The contract under test:

* Cross-transport replies fire on the trigger transport and emit on
  the response transport (which may differ).
* Fire-once is preserved: a second trigger does NOT cause a second
  emit, but the trigger arrival still increments `candidate_count`.
* Build/publish exceptions transition state to FIRED_BUILDER_ERROR
  (terminal); the reply does not retry.
* Reply records live on the trigger context only — single-writer per
  ADR-0016. The framework surface (`result.replies`) reflects this.
* Same-transport replies via Stage match the observable behaviour of
  single-transport `Scenario.on(...).publish(...)`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from admiral import Harness
from admiral.matchers import field_equals
from admiral.scenario import Outcome
from admiral.stage import Stage, StageReplyState
from admiral.transports import MockTransport

from .conftest import (
    _FailingMockTransport,
    _harness_over,
    mapped_bridge_for,
    single_transport_bridge,
)

_PLACEHOLDER_MATCHER = field_equals("status", "ok")


# ---------------------------------------------------------------------------
# Fixtures local to Group I
# ---------------------------------------------------------------------------


class _BridgePair(NamedTuple):
    """A two-transport Stage with one droppable harness, used by the
    cross-transport reply tests."""

    stage: Stage
    nats: Harness
    kafka: Harness


def _bridge_pair(
    allowlist_yaml_path: Path,
    *,
    droppable: str | None = None,
) -> _BridgePair:
    """Build a Stage with `nats` + `kafka` transports. If `droppable`
    is set, that harness uses a `_FailingMockTransport` whose `drop()`
    simulates a broker drop. The other uses a vanilla MockTransport.
    """

    def _mk(name: str) -> Harness:
        if droppable == name:
            return _harness_over(
                _FailingMockTransport(
                    allowlist_path=allowlist_yaml_path,
                    endpoint="mock://localhost",
                )
            )
        return Harness(
            MockTransport(
                allowlist_path=allowlist_yaml_path,
                endpoint="mock://localhost",
            )
        )

    nats_h = _mk("nats")
    kafka_h = _mk("kafka")
    return _BridgePair(
        stage=Stage(
            harnesses={"nats": nats_h, "kafka": kafka_h},
            bridge=mapped_bridge_for("nats", "kafka"),
        ),
        nats=nats_h,
        kafka=kafka_h,
    )


def _drop(harness: Harness) -> None:
    harness._transport.drop()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# I1 — cross-transport reply fires on the trigger and emits on the response
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_emit_response_on_other_transport(
    allowlist_yaml_path: Path,
) -> None:
    """I1. Trigger arrives on Kafka; build runs; response published on
    NATS. An expectation registered on the response topic on NATS
    sees the reply payload and resolves PASS.

    The build callback receives the decoded triggering payload and
    returns the response payload. Both legs are dict payloads
    encoded/decoded via JSON (the harnesses' default codec).
    """
    pair = _bridge_pair(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i1") as scope:
            # Reply: trigger on Kafka, build a response, emit on NATS.
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok", "from": trig["id"]},
            )
            # Expect the response to land on NATS.
            response_handle = scope.expect("orders.processed", _PLACEHOLDER_MATCHER, on="nats")
            # Publish the trigger on Kafka — this is the AUT-stand-in
            # in the test (a real bridge AUT would be sending this).
            scope.publish("orders.new", {"id": 42}, on="kafka")
            result = await scope.await_all(timeout_ms=50)
    finally:
        await pair.stage.disconnect()

    # Expectation on NATS resolved with the build's output.
    assert response_handle.outcome is Outcome.PASS
    assert response_handle.message == {"status": "ok", "from": 42}

    # Reply report says the reply fired.
    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.FIRED
    assert report.trigger_transport == "kafka"
    assert report.response_transport == "nats"
    assert report.candidate_count == 1
    assert report.match_count == 1
    assert report.reply_published is True


# ---------------------------------------------------------------------------
# I2 — fire-once: second trigger does not re-emit
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_fire_only_once_when_trigger_arrives_twice(
    allowlist_yaml_path: Path,
) -> None:
    """I2. Two triggers on Kafka. The reply emits on NATS exactly
    once; the second trigger increments `candidate_count` but does
    NOT cause a second response. State stays FIRED.

    Verified via two parallel observations:
    * NATS-side expectation registered with a count-on-arrival
      callback would fire only once (we don't have such a primitive
      yet in this group, so we use the direct assertion below).
    * `report.match_count == 1` proves only one trigger satisfied
      the matcher AND was acted on.
    * `report.candidate_count == 2` proves both triggers reached the
      callback (observability of post-FIRED arrivals).
    """
    pair = _bridge_pair(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i2") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok"},
            )
            scope.publish("orders.new", {"id": 1}, on="kafka")
            scope.publish("orders.new", {"id": 2}, on="kafka")
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.FIRED
    assert report.candidate_count == 2  # both triggers reached callback
    assert report.match_count == 1  # but only one fired the build
    assert report.reply_published is True


# ---------------------------------------------------------------------------
# I3 — response transport dropped: reply records FIRED_BUILDER_ERROR
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_record_failed_when_response_transport_dropped(
    allowlist_yaml_path: Path,
) -> None:
    """I3. Trigger arrives on Kafka, build succeeds, but the publish
    on NATS raises (NATS dropped). State transitions to
    FIRED_BUILDER_ERROR (terminal); `builder_error` is the
    `RuntimeError` class name.
    """
    pair = _bridge_pair(allowlist_yaml_path, droppable="nats")
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i3") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok"},
            )
            _drop(pair.nats)
            scope.publish("orders.new", {"id": 1}, on="kafka")
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.FIRED_BUILDER_ERROR
    assert report.response_transport == "nats"
    assert report.builder_error == "RuntimeError"
    assert report.reply_published is False


# ---------------------------------------------------------------------------
# I4 — trigger transport dropped: reply records ARMED_NO_MATCH
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_record_armed_no_match_when_trigger_transport_dropped(
    allowlist_yaml_path: Path,
) -> None:
    """I4. Kafka (trigger transport) drops mid-scenario; nothing
    publishes on Kafka after that. The trigger callback never fires;
    the reply state is derived as ARMED_NO_MATCH at scope exit.
    """
    pair = _bridge_pair(allowlist_yaml_path, droppable="kafka")
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i4") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok"},
            )
            _drop(pair.kafka)
            # No publish on Kafka after the drop.
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.ARMED_NO_MATCH
    assert report.candidate_count == 0
    assert report.match_count == 0
    assert report.reply_published is False


# ---------------------------------------------------------------------------
# I5 — build callback raises: reply records FIRED_BUILDER_ERROR
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_record_failed_when_builder_raises(
    allowlist_yaml_path: Path,
) -> None:
    """I5. The build callback itself raises (a programming error in
    the test author's response builder). State transitions to
    FIRED_BUILDER_ERROR; `builder_error` is the exception class name.
    The error class name only — never `str(exc)` — protects against
    payload-derived data leaking through diagnostics.
    """
    pair = _bridge_pair(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i5") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: 1 / 0,  # ZeroDivisionError
            )
            scope.publish("orders.new", {"id": 1}, on="kafka")
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.FIRED_BUILDER_ERROR
    assert report.builder_error == "ZeroDivisionError"
    assert report.match_count == 1  # the matcher accepted before build raised
    assert report.reply_published is False


# ---------------------------------------------------------------------------
# I6 — same-transport reply via Stage matches single-transport behaviour
# ---------------------------------------------------------------------------


async def test_stage_same_transport_reply_should_emit_on_the_same_transport(
    allowlist_yaml_path: Path,
) -> None:
    """I6. A reply where trigger and response use the SAME transport
    is the degenerate case of cross-transport. The reply still fires
    once, the response lands on the response topic of the same
    harness, and the report's `trigger_transport` and
    `response_transport` are equal. The cross-transport machinery
    handles this case without a special path.
    """
    only_h = Harness(MockTransport(allowlist_path=allowlist_yaml_path, endpoint="mock://localhost"))
    stage = Stage(
        harnesses={"only": only_h},
        bridge=single_transport_bridge("only"),
    )
    await stage.connect()
    try:
        async with stage.scenario("i6") as scope:
            scope.on("orders.new", on="only").publish(
                "orders.processed",
                on="only",
                build=lambda trig: {"status": "ok", "echo": trig["id"]},
            )
            response_handle = scope.expect("orders.processed", _PLACEHOLDER_MATCHER, on="only")
            scope.publish("orders.new", {"id": 7}, on="only")
            result = await scope.await_all(timeout_ms=30)
    finally:
        await stage.disconnect()

    assert response_handle.outcome is Outcome.PASS
    assert response_handle.message == {"status": "ok", "echo": 7}

    assert len(result.replies) == 1
    report = result.replies[0]
    assert report.state is StageReplyState.FIRED
    assert report.trigger_transport == "only"
    assert report.response_transport == "only"


# ---------------------------------------------------------------------------
# I7 — cross-transport reply uses the response context's correlation id
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_use_response_context_correlation_id_for_emit(
    allowlist_yaml_path: Path,
) -> None:
    """I7. The bridge translation property: the published response
    carries the RESPONSE transport's wire id (per the bridge's
    `to_wire(logical, response_transport)`), NOT the trigger
    transport's. Without this, a downstream consumer subscribed on
    the response transport with its own correlation policy would
    fail to route the reply to the originating scope.

    Setup: harnesses with `DictFieldPolicy` so the wire id appears in
    the published response payload. Bridge with distinct prefix per
    transport. The reply registers a build callback that returns a
    plain dict; the framework stamps the correlation id via the
    response harness's policy. The test inspects the published bytes
    on the NATS-side MockTransport and asserts the embedded
    correlation_id starts with the `nats-` prefix (response wire id),
    NOT the `kafka-` prefix (trigger wire id).

    Test was originally Group I but deferred until Group J's setup
    introduced the `DictFieldPolicy` configuration this assertion
    requires.
    """
    from admiral.correlation import DictFieldPolicy

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
        async with stage.scenario("i7") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok", "echo": trig.get("id")},
            )
            scope.publish("orders.new", {"id": 99}, on="kafka")
            await scope.await_all(timeout_ms=20)

            # Inspect the bytes the response landed on the NATS
            # MockTransport's `_sent` ledger. There is exactly one
            # send: the framework's reply emit. Decode and assert
            # the embedded correlation id is the NATS-side wire id
            # (the bridge's `nats-` prefix), not the Kafka-side.
            nats_sent = nats_h._transport._sent  # type: ignore[attr-defined]
    finally:
        await stage.disconnect()

    # Exactly one publish on NATS — the reply emit.
    response_topic, response_bytes = nats_sent[0]
    assert response_topic == "orders.processed"

    response_payload = json.loads(response_bytes)
    assert response_payload["status"] == "ok"
    assert response_payload["echo"] == 99

    # The correlation translation property:
    correlation_id = response_payload["correlation_id"]
    assert correlation_id.startswith("nats-"), (
        f"response correlation id is {correlation_id!r}; "
        f"expected the NATS-side wire id, not the Kafka-side one. "
        f"Stage's reply emit must stamp via the RESPONSE harness's "
        f"correlation policy with the RESPONSE child's wire id."
    )
    assert not correlation_id.startswith("kafka-"), (
        f"correlation id {correlation_id!r} carries the kafka- prefix; "
        f"the bridge translation from kafka-side to nats-side did not apply"
    )


# ---------------------------------------------------------------------------
# I8 — reply records live on the trigger context only
# ---------------------------------------------------------------------------


async def test_stage_cross_transport_reply_should_produce_exactly_one_reply_report(
    allowlist_yaml_path: Path,
) -> None:
    """I8. ADR-0016 single-writer invariant: the `_StageReply` record
    is held only on the trigger context. The user-visible
    consequence: `result.replies` contains exactly one
    `StageReplyReport` per `on().publish()` registration —
    NOT two (one per transport). This rules out a duplicate-record
    bug where both the trigger and response children would carry
    state, risking double-fire.
    """
    pair = _bridge_pair(allowlist_yaml_path)
    await pair.stage.connect()
    try:
        async with pair.stage.scenario("i8") as scope:
            scope.on("orders.new", on="kafka").publish(
                "orders.processed",
                on="nats",
                build=lambda trig: {"status": "ok"},
            )
            scope.publish("orders.new", {"id": 1}, on="kafka")
            result = await scope.await_all(timeout_ms=20)
    finally:
        await pair.stage.disconnect()

    # Exactly one report — the response side has no record.
    assert len(result.replies) == 1
    # And the report names the trigger transport as the registration site.
    assert result.replies[0].trigger_transport == "kafka"
