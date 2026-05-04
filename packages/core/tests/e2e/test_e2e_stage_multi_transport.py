"""End-to-end: Stage over real NATS + Kafka.

Exercises a single `Stage` coordinating two real transports running
in Docker Compose: a request published on Kafka triggers a reply
emitted on NATS, the reply round-trips through an AUT-stand-in
subscriber on the actual NATS broker, and the scope's expectation
on NATS resolves PASS. Validates that the cross-transport defences
shipped in the integration suite (correlation filter, fire-once
reply, per-transport handle attribution) all hold when the
in-memory shortcut of `MockTransport` is removed and real wire
delivery, real broker buffering, and real codec round-trips are in
play.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

import pytest
from choreo import Harness
from choreo.correlation import DictFieldPolicy
from choreo.matchers import field_equals
from choreo.scenario import Outcome
from choreo.stage import MappedBridge, Stage, StageReplyState

pytestmark = pytest.mark.e2e


def _unique_subject(prefix: str) -> str:
    """Every test gets a unique topic suffix so concurrent runs
    against the same broker cannot interfere. NATS treats dots as
    separators; the same suffix is also a valid Kafka topic."""
    return f"e2e.stage.{prefix}.{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Stage construction over real transports
# ---------------------------------------------------------------------------


async def test_a_stage_over_nats_and_kafka_should_round_trip_a_cross_transport_reply(
    allowlist_yaml_path: Path,
    nats_url: str,
    kafka_bootstrap: str,
    _nats_available: bool,
    _kafka_available: bool,
) -> None:
    """The full bridge round-trip with real brokers:

    1. Test publishes a request on Kafka with the per-scope wire id
       stamped via DictFieldPolicy.
    2. An AUT-stand-in subscriber on Kafka decodes the request and
       publishes a response on NATS, translating the bridge's
       Kafka-side wire id to the NATS-side wire id (the bridge is
       deterministic per-transport via prefix).
    3. The scope's `expect()` on NATS resolves on the AUT's response.

    Verifies that:

    * The correlation filter routes correctly across real brokers.
    * The cross-transport reply chain via `s.on(...).publish(...)`
      fires once on the trigger transport (Kafka) and emits on the
      response transport (NATS) with the response-side wire id.
    * `result.by_transport` reports per-transport handle attribution
      reflecting the real-wire round-trip.
    """
    from choreo.transports import KafkaTransport, NatsTransport

    request_topic = _unique_subject("request")
    response_topic = _unique_subject("response")
    final_topic = _unique_subject("final")

    nats_h = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    kafka_h = Harness(
        KafkaTransport(
            bootstrap_servers=[kafka_bootstrap],
            allowlist_path=allowlist_yaml_path,
        ),
        correlation=DictFieldPolicy(field="correlation_id"),
    )
    bridge = MappedBridge(
        forwards={
            "nats": lambda logical: f"nats-{logical}",
            "kafka": lambda logical: f"kafka-{logical}",
        }
    )
    stage = Stage(harnesses={"nats": nats_h, "kafka": kafka_h}, bridge=bridge)

    # AUT-stand-in: subscribed on the response_topic on NATS — when the
    # in-scope reply chain emits there, this callback re-publishes on
    # `final_topic` with the same correlation_id so the scope's
    # final `expect` (also on NATS, on `final_topic`) resolves. The
    # transition models a downstream service that consumes the reply.
    aut_received: list[dict] = []

    def aut_nats_to_nats(topic: str, raw: bytes) -> None:
        msg = json.loads(raw)
        aut_received.append(msg)
        echo = {
            "result": "echoed",
            "from": msg.get("forwarded"),
            "correlation_id": msg["correlation_id"],
        }
        nats_h._transport.publish(  # type: ignore[attr-defined]
            final_topic, json.dumps(echo).encode("utf-8")
        )

    await stage.connect()
    nats_h.subscribe(response_topic, aut_nats_to_nats)

    try:
        async with stage.scenario("nats-kafka-bridge") as scope:
            # Reply chain: trigger on Kafka, emit on NATS.
            scope.on(request_topic, on="kafka").publish(
                response_topic,
                on="nats",
                build=lambda trigger: {"forwarded": trigger["payload"]},
            )
            # Final assertion: the AUT's echo lands on NATS final_topic.
            final_handle = scope.expect(
                final_topic,
                field_equals("result", "echoed"),
                on="nats",
            )
            # Kick off the round-trip.
            scope.publish(request_topic, {"payload": 42}, on="kafka")
            # Real brokers need a generous deadline — Kafka in particular
            # buffers and round-trips can take 100ms+ on cold start.
            result = await scope.await_all(timeout_ms=5000)
    finally:
        await stage.disconnect()

    # The expectation resolved with the AUT's echo.
    assert final_handle.outcome is Outcome.PASS, (
        f"final handle did not pass: outcome={final_handle.outcome}, "
        f"reason={final_handle.reason!r}"
    )
    assert final_handle.message["from"] == 42
    assert final_handle.transport == "nats"

    # Reply lifecycle: the cross-transport reply fired once.
    assert len(result.replies) == 1
    reply = result.replies[0]
    assert reply.state is StageReplyState.FIRED
    assert reply.trigger_transport == "kafka"
    assert reply.response_transport == "nats"
    assert reply.match_count == 1

    # Per-transport handle attribution — the by_transport view is
    # populated for the multi-transport scenario; only NATS has a
    # handle in this round-trip (the Kafka leg is the trigger).
    assert "nats" in result.by_transport
    assert len(result.by_transport["nats"]) == 1
    assert result.by_transport["nats"][0] is final_handle

    # The AUT actually saw the in-flight reply.
    assert len(aut_received) == 1
    assert aut_received[0]["forwarded"] == 42


# ---------------------------------------------------------------------------
# Connect rollback against real transports
# ---------------------------------------------------------------------------


async def test_a_stage_should_roll_back_real_transports_when_one_fails_to_connect(
    allowlist_yaml_path: Path,
    nats_url: str,
    _nats_available: bool,
) -> None:
    """If one transport in a multi-transport Stage fails to connect
    (here: a Kafka address outside the allowlist), the rollback path
    must disconnect the already-connected real NATS transport — no
    leaked socket. Validates that R4 (rollback disconnects the
    failing transport AND the already-connected siblings) holds
    across real wire connections.

    Skipped if NATS is unreachable; does not require Kafka because
    the Kafka transport's allowlist failure happens before any
    socket is opened.
    """
    from choreo.environment import HostNotInAllowlist
    from choreo.stage import StageConnectError
    from choreo.transports import KafkaTransport, NatsTransport

    nats_h = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path)
    )
    bad_kafka_h = Harness(
        # An address NOT in the allowlist — connect() raises
        # HostNotInAllowlist before any socket opens.
        KafkaTransport(
            bootstrap_servers=["forbidden.host:9092"],
            allowlist_path=allowlist_yaml_path,
        )
    )
    bridge = MappedBridge(
        forwards={
            "nats": lambda logical: f"nats-{logical}",
            "kafka": lambda logical: f"kafka-{logical}",
        }
    )
    stage = Stage(
        harnesses={"nats": nats_h, "kafka": bad_kafka_h}, bridge=bridge
    )

    with pytest.raises(StageConnectError) as excinfo:
        await stage.connect()

    # The failing transport is named.
    assert excinfo.value.failing_transport == "kafka"
    # The original cause is the allowlist rejection (real-wire
    # validation, not a mock).
    assert isinstance(excinfo.value.__cause__, HostNotInAllowlist)
    # The NATS harness was rolled back to disconnected.
    assert not nats_h.is_connected()


# ---------------------------------------------------------------------------
# Mid-scenario broker drop — real NATS disconnect + Stage timeout path
# ---------------------------------------------------------------------------


async def test_a_stage_should_resolve_handles_as_timeout_when_a_real_transport_disconnects(
    allowlist_yaml_path: Path,
    nats_url: str,
    kafka_bootstrap: str,
    _nats_available: bool,
    _kafka_available: bool,
) -> None:
    """Disconnect the NATS harness mid-scenario (simulating a broker
    drop) and verify the scope's NATS-side expectation resolves as
    TIMEOUT at the deadline rather than hanging or surfacing a
    transport-level error. Mirrors Group H's mock-based tests but
    against real brokers.
    """
    from choreo.transports import KafkaTransport, NatsTransport

    nats_h = Harness(
        NatsTransport(servers=[nats_url], allowlist_path=allowlist_yaml_path)
    )
    kafka_h = Harness(
        KafkaTransport(
            bootstrap_servers=[kafka_bootstrap],
            allowlist_path=allowlist_yaml_path,
        )
    )
    bridge = MappedBridge(
        forwards={
            "nats": lambda logical: f"nats-{logical}",
            "kafka": lambda logical: f"kafka-{logical}",
        }
    )
    stage = Stage(harnesses={"nats": nats_h, "kafka": kafka_h}, bridge=bridge)
    await stage.connect()
    try:
        async with stage.scenario("real-drop") as scope:
            handle = scope.expect(
                _unique_subject("never-arrives"),
                field_equals("status", "ok"),
                on="nats",
            )
            # Simulate broker drop by disconnecting the NATS harness
            # directly. Subsequent inbound delivery on NATS stops; the
            # scope's expectation cannot resolve.
            await nats_h.disconnect()
            # Short deadline so the test stays fast; the contract under
            # test is "the scope resolves its handles to TIMEOUT", not
            # "the scope waits patiently".
            result = await scope.await_all(timeout_ms=200)
    finally:
        # nats_h is already disconnected; stage.disconnect handles that.
        await stage.disconnect()

    assert handle.outcome is Outcome.TIMEOUT
    assert handle.transport == "nats"
    assert not result.passed


# Suppress unused-async-import — `asyncio` is needed for some skip paths.
_ = asyncio
